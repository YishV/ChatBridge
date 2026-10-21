"""Local controls, manual chat, and an explicitly configured Windows gateway."""

import argparse
import json
import time
from pathlib import Path

from wechat_ai_bridge.console_log import ConsoleLog, NullConsole
from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.models.client import ChatCompletionsClient, ModelError
from wechat_ai_bridge.media.cover import add_cover_parser, run_cover
from wechat_ai_bridge.media.kits import CoverError
from wechat_ai_bridge.media.memes import MemeCatalog, MemeError
from wechat_ai_bridge.qa.service import ChatService, DEFAULT_SYSTEM_PROMPT
from wechat_ai_bridge.qa.identity import load_identity_key
from wechat_ai_bridge.settings import Settings
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.voice.errors import VoiceError
from wechat_ai_bridge.wechat.delivery import DeliveryService
from wechat_ai_bridge.wechat.gateway import GatewayConfig, GatewayError, GatewayRunner, NativeClient, NativeSender
from wechat_ai_bridge.wechat.instance import SingleInstance
from wechat_ai_bridge.wechat.images import outgoing_directory
from wechat_ai_bridge.wechat.reader import EncryptedMessageReader


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def create_model(settings):
    return ChatCompletionsClient(base_url=settings.model_base_url,
        allowed_hosts=settings.allowed_model_hosts, api_key=settings.model_api_key,
        model=settings.model_name, thinking=settings.model_thinking,
        timeout_seconds=settings.model_timeout_seconds, max_tokens=settings.model_max_tokens)


_UNSET = object()


def create_meme_catalog(settings, *, outgoing=None):
    if not settings.memes_enabled:
        return None
    return MemeCatalog(settings.meme_cache_dir, outgoing or Path('state/stickers/outgoing'))


def create_processor(settings, store, *, history_groups=None, meme_catalog=_UNSET):
    identity_path = settings.state_db.with_suffix('.members.key')
    if store.has_chat_history() and not identity_path.is_file():
        raise GatewayError('member_identity_key_missing_for_history')
    groups = store.allowed_groups() if history_groups is None else history_groups
    if meme_catalog is _UNSET:
        meme_catalog = create_meme_catalog(settings)
    chat = ChatService(create_model(settings), settings.system_prompt or DEFAULT_SYSTEM_PROMPT,
        prompt_file=None if settings.system_prompt else settings.prompt_file,
        alias_key=load_identity_key(identity_path), history_store=store,
        history_groups=groups, history_limit=settings.history_context_messages,
        meme_catalog=meme_catalog,
        meme_cooldown_seconds=settings.meme_cooldown_seconds, meme_recent_count=settings.meme_recent_count)
    return MessageProcessor(store, chat, cooldown_seconds=settings.cooldown_seconds,
        hourly_quota=settings.hourly_quota, participation_mode=settings.participation_mode,
        spontaneous_cooldown_seconds=settings.spontaneous_cooldown_seconds)


def run_voice(args, settings, emit):
    from wechat_ai_bridge.voice import create_voice_sender
    from wechat_ai_bridge.voice.errors import VoiceError
    from wechat_ai_bridge.wechat.reader import EncryptedMessageReader
    config = GatewayConfig.load(settings.gateway_config)
    account, passphrase = config.reader_credentials()
    reader = EncryptedMessageReader(account, passphrase, config.account_id, config.groups)
    native = NativeClient(config, account.name,
        can_send=lambda group: True)
    health = native.health()
    sender = create_voice_sender(settings, reader, health.get('pid'))
    if args.action == 'check':
        import importlib.util
        output = False
        capture = False
        window = False
        try:
            sender.audio._output_device()
            output = True
        except Exception:
            pass
        try:
            sender.audio._capture_id()
            capture = True
        except Exception:
            pass
        try:
            sender.ui._find()
            window = True
        except Exception:
            pass
        resolvable = sum(1 for group in config.groups if reader.conversation_display_name(group))
        emit({'voiceReady': output and capture and window and importlib.util.find_spec('edge_tts') is not None,
              'edgeTts': importlib.util.find_spec('edge_tts') is not None,
              'cableOutput': output, 'cableInput': capture, 'windowFound': window,
              'configuredGroups': len(config.groups), 'resolvableChatNames': resolvable,
              'voice': settings.voice_name, 'sent': False})
        return
    if not args.group or not args.text:
        raise VoiceError('voice_arguments_missing')
    result = sender.send_group_voice(args.group, args.text)
    emit({'voiceSent': result.confirmed, 'serverMessageId': result.server_message_id})
    if not result.confirmed:
        raise SystemExit(1)


def run_gateway(args, settings, store):
    config = GatewayConfig.load(settings.gateway_config)
    account, passphrase = config.reader_credentials()
    reader = EncryptedMessageReader(account, passphrase, config.account_id, config.groups)
    native = NativeClient(config, account.name,
        can_send=lambda group: store.is_allowed(group) and not store.is_paused())
    health = native.health()
    catalog = None
    if settings.memes_enabled:
        try:
            catalog = create_meme_catalog(settings, outgoing=outgoing_directory(config.expected_executable))
        except (GatewayError, OSError, TypeError, IndexError):
            # A path or cache problem only disables images; text chat continues.
            catalog = None
    # Without native sticker support, do not offer the model an image it cannot send.
    active_catalog = catalog if health.get('nativeStickerSend') is True else None
    voice_sender = None
    if settings.voice_enabled:
        try:
            from wechat_ai_bridge.voice import create_voice_sender
            voice_sender = create_voice_sender(settings, reader, health.get('pid'))
        except Exception:
            voice_sender = None
    processor = create_processor(settings, store, history_groups=config.groups, meme_catalog=active_catalog)
    processor.chat._load_system_prompt()
    for group in config.groups:
        reader.snapshot(group)
    status = {"gatewayReady": health["ready"], "clientVersion": health["version"],
        "modelConfigured": bool(settings.model_api_key), "participationMode": settings.participation_mode,
        "allowedGroupCount": sum(store.is_allowed(group) for group in config.groups), "paused": store.is_paused(),
        "promptReady": True, "naturalBatchSeconds": settings.spontaneous_cooldown_seconds,
        'historyRestored': processor.chat.restored_messages, 'historyContextMessages': settings.history_context_messages,
        'memesEnabled': settings.memes_enabled, 'memeAssets': len(catalog.assets) if catalog else 0,
        'memeNativeReady': health.get('nativeStickerSend') is True,
        'voiceEnabled': settings.voice_enabled, 'voiceReady': voice_sender is not None}
    if args.check:
        emit(status)
        return
    if not settings.model_api_key:
        raise GatewayError("model_key_not_configured")
    if not status["allowedGroupCount"]:
        raise GatewayError("no_allowed_groups")
    lock_file = settings.state_db.with_suffix(".gateway.lock")
    with SingleInstance(lock_file):
        console = NullConsole() if getattr(args, 'quiet_console', False) else ConsoleLog()
        service = DeliveryService(processor, store,
            NativeSender(reader, native, store, config.account_id, config.groups), console=console,
            meme_catalog=active_catalog,
            meme_cooldown_seconds=settings.meme_cooldown_seconds, meme_recent_count=settings.meme_recent_count,
            voice_sender=voice_sender)
        if settings.memes_enabled and not status['memeAssets']:
            console.notice('未加载表情素材，继续文字聊天；可在另一终端执行 memes sync 后重启网关')
        elif settings.memes_enabled and not status['memeNativeReady']:
            console.notice('本机组件暂不支持表情发送，继续文字聊天')
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
    memes = commands.add_parser('memes', help='同步和查看场景表情素材，不发送微信消息')
    memes.add_argument('action', choices=('sync', 'list', 'check', 'prepare'))
    memes.add_argument('--id', help='prepare 时指定已注册素材编号，如 083')
    ask = commands.add_parser("ask", help="本机模拟群文本，不向微信发送")
    ask.add_argument("--group", required=True)
    ask.add_argument("--sender", required=True)
    ask.add_argument("--message-id", required=True)
    ask.add_argument("--text", required=True)
    ask.add_argument("--mentioned", action="store_true", help="模拟原生 @机器人")
    voice = commands.add_parser('voice', help='本机语音条检查或显式发送，不进入聊天流程')
    voice.add_argument('action', choices=('check', 'say'))
    voice.add_argument('--group', help='say 时的群 ID；必须是已配置群')
    voice.add_argument('--text', help='say 时要朗读的文字')
    commands.add_parser("model-check", help="私有模型连通检查，不发送微信消息")
    run = commands.add_parser("run", help="持续处理已配置白名单群的新消息")
    run.add_argument('--quiet-console', action='store_true', help='不打印群消息正文和回复，仅输出状态计数')
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
        if args.command == 'voice':
            run_voice(args, settings, emit)
            return
        if args.command == 'memes':
            catalog = create_meme_catalog(settings)
            if catalog is None:
                raise MemeError('memes_disabled')
            if args.action == 'sync':
                emit({'status': 'memes_synced', **catalog.sync()})
            elif args.action == 'list':
                emit({'assets': [{'id': asset.id, 'description': asset.description} for asset in catalog.assets]})
            elif args.action == 'prepare':
                if not args.id:
                    parser.error('memes prepare 需要 --id 素材编号')
                path = catalog.prepare(args.id)
                emit({'status': 'meme_prepared', 'id': args.id, 'path': str(path)})
            else:
                emit({'enabled': settings.memes_enabled, 'assets': len(catalog.assets),
                      'cooldownSeconds': settings.meme_cooldown_seconds, 'recentCount': settings.meme_recent_count,
                      'nativeChecked': False, 'sent': False})
            return
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
            result = create_processor(settings, store, history_groups=(args.group,)).process(IncomingMessage(
                args.message_id, args.group, args.sender, args.text, args.mentioned))
            emit({"outcome": result.outcome, "reply": result.reply})
    except KeyboardInterrupt:
        emit({"status": "stopped"})
    except CoverError as error:
        emit({"status": "cover_unavailable", "error": error.code})
        raise SystemExit(1) from None
    except MemeError as error:
        emit({'status': 'memes_unavailable', 'error': error.code})
        raise SystemExit(1) from None
    except VoiceError as error:
        emit({'status': 'voice_unavailable', 'error': error.code})
        raise SystemExit(1) from None
    except GatewayError as error:
        emit({"status": "stopped", "error": str(error)})
        raise SystemExit(1) from None
    except ModelError as error:
        status = getattr(error.__cause__, "code", None)
        emit({"status": "model_unavailable", "httpStatus": status, 'error': error.code,
              'retryable': error.retryable})
        raise SystemExit(1) from None
    except (OSError, ValueError, KeyError, TypeError):
        emit({"status": "stopped", "error": "configuration_or_runtime_invalid"})
        raise SystemExit(1) from None
    finally:
        if store:
            store.close()
