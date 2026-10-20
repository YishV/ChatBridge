"""Local controls, manual chat, and an explicitly configured Windows gateway."""

import argparse
import json
import time

from wechat_ai_bridge.console_log import ConsoleLog
from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.models.client import ChatCompletionsClient, ModelError
from wechat_ai_bridge.media.cover import add_cover_parser, run_cover
from wechat_ai_bridge.media.kits import CoverError
from wechat_ai_bridge.qa.service import ChatService, DEFAULT_SYSTEM_PROMPT
from wechat_ai_bridge.settings import Settings
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.delivery import DeliveryService
from wechat_ai_bridge.wechat.gateway import GatewayConfig, GatewayError, GatewayRunner, NativeClient, NativeSender
from wechat_ai_bridge.wechat.instance import SingleInstance
from wechat_ai_bridge.wechat.reader import EncryptedMessageReader


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def create_model(settings):
    return ChatCompletionsClient(base_url=settings.model_base_url,
        allowed_hosts=settings.allowed_model_hosts, api_key=settings.model_api_key,
        model=settings.model_name, thinking=settings.model_thinking)


def create_processor(settings, store):
    chat = ChatService(create_model(settings), settings.system_prompt or DEFAULT_SYSTEM_PROMPT,
        prompt_file=None if settings.system_prompt else settings.prompt_file)
    return MessageProcessor(store, chat, cooldown_seconds=settings.cooldown_seconds,
        hourly_quota=settings.hourly_quota, participation_mode=settings.participation_mode,
        spontaneous_cooldown_seconds=settings.spontaneous_cooldown_seconds)


def run_gateway(args, settings, store):
    processor = create_processor(settings, store)
    processor.chat._load_system_prompt()
    config = GatewayConfig.load(settings.gateway_config)
    account, passphrase = config.reader_credentials()
    reader = EncryptedMessageReader(account, passphrase, config.account_id, config.groups)
    native = NativeClient(config, account.name,
        can_send=lambda group: store.is_allowed(group) and not store.is_paused())
    health = native.health()
    for group in config.groups:
        reader.snapshot(group)
    status = {"gatewayReady": health["ready"], "clientVersion": health["version"],
        "modelConfigured": bool(settings.model_api_key), "participationMode": settings.participation_mode,
        "allowedGroupCount": sum(store.is_allowed(group) for group in config.groups), "paused": store.is_paused(),
        "promptReady": True, "naturalBatchSeconds": settings.spontaneous_cooldown_seconds}
    if args.check:
        emit(status)
        return
    if not settings.model_api_key:
        raise GatewayError("model_key_not_configured")
    if not status["allowedGroupCount"]:
        raise GatewayError("no_allowed_groups")
    lock_file = settings.state_db.with_suffix(".gateway.lock")
    with SingleInstance(lock_file):
        console = ConsoleLog()
        service = DeliveryService(processor, store,
            NativeSender(reader, native, store, config.account_id, config.groups), console=console)
        runner = GatewayRunner(reader, native, service, store, config.account_id, config.groups)
        runner.initialize()
        console.notice("网关已启动：收到消息和发送结果会实时打印到终端，不写入日志文件")
        emit({"status": "running", **status, "historyReplied": False})
        while True:
            counts = runner.tick()
            if counts["received"] or counts["naturalEvaluated"] or args.once:
                emit(counts)
            if args.once:
                return
            time.sleep(settings.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="通用微信群 AI 聊天机器人")
    commands = parser.add_subparsers(dest="command", required=True)
    add_cover_parser(commands)
    ask = commands.add_parser("ask", help="本机模拟群文本，不向微信发送")
    ask.add_argument("--group", required=True)
    ask.add_argument("--sender", required=True)
    ask.add_argument("--message-id", required=True)
    ask.add_argument("--text", required=True)
    ask.add_argument("--mentioned", action="store_true", help="模拟原生 @机器人")
    commands.add_parser("model-check", help="私有模型连通检查，不发送微信消息")
    run = commands.add_parser("run", help="持续处理已配置白名单群的新消息")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只读检查本机接入，不调用模型或发送消息")
    mode.add_argument("--once", action="store_true", help="跳过已有消息后执行一次轮询")
    admin = commands.add_parser("admin", help="本机配置白名单和暂停开关")
    admin.add_argument("action", choices=("allow", "deny", "pause", "resume", "status"))
    admin.add_argument("group_id", nargs="?")
    args = parser.parse_args()
    store = None
    try:
        if args.command == 'cover':
            run_cover(args, emit)
            return
        settings = Settings.from_env()
        if args.command == "model-check":
            response = create_model(settings).complete([{"role": "user", "content": "请仅回复一个字：好"}])
            emit({"modelReady": bool(response)})
            return
        store = StateStore(settings.state_db)
        if args.command == "admin":
            if args.action in ("allow", "deny") and not args.group_id:
                parser.error("allow/deny 需要 group_id")
            if args.action == "allow":
                store.allow_group(args.group_id)
            elif args.action == "deny":
                store.deny_group(args.group_id)
            elif args.action == "pause":
                store.set_paused(True)
            elif args.action == "resume":
                store.set_paused(False)
            emit({"paused": store.is_paused(), "allowed_groups": store.allowed_groups()})
        elif args.command == "run":
            run_gateway(args, settings, store)
        else:
            result = create_processor(settings, store).process(IncomingMessage(
                args.message_id, args.group, args.sender, args.text, args.mentioned))
            emit({"outcome": result.outcome, "reply": result.reply})
    except KeyboardInterrupt:
        emit({"status": "stopped"})
    except CoverError as error:
        emit({"status": "cover_unavailable", "error": error.code})
        raise SystemExit(1) from None
    except GatewayError as error:
        emit({"status": "stopped", "error": str(error)})
        raise SystemExit(1) from None
    except ModelError as error:
        status = getattr(error.__cause__, "code", None)
        emit({"status": "model_unavailable", "httpStatus": status})
        raise SystemExit(1) from None
    except (OSError, ValueError, KeyError, TypeError):
        emit({"status": "stopped", "error": "configuration_or_runtime_invalid"})
        raise SystemExit(1) from None
    finally:
        if store:
            store.close()
