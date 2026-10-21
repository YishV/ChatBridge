"""Bounded local polling and one-attempt native text delivery."""

import json
import re
import time
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

from wechat_ai_bridge.message import Outcome
from wechat_ai_bridge.wechat.delivery import DeliveryOutcome, SendReceipt
from wechat_ai_bridge.wechat.errors import GatewayError, GatewayFatalError
from wechat_ai_bridge.wechat.events import normalize_gateway_message
from wechat_ai_bridge.wechat.native import PAT_TYPE, native_group_message, native_group_pat
from wechat_ai_bridge.wechat.images import matches_image_receipt, outgoing_directory, static_gif
from wechat_ai_bridge.wechat.windows import unprotect_credential, verify_native_process


def read_private_json(path: Path) -> dict:
    if path.stat().st_size > 1024 * 1024:
        raise GatewayError("configuration_too_large")
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise GatewayError("configuration_invalid")
    return result


@dataclass(frozen=True)
class GatewayConfig:
    expected_executable: Path
    sandbox: str
    sandbox_root: Path
    credential_file: Path
    token_file: Path
    account_id: str
    groups: frozenset[str]
    hook_sha256: str

    @classmethod
    def load(cls, path: Path):
        data = read_private_json(path)
        config = cls(Path(data["expectedExecutable"]).resolve(), data["sandbox"], Path(data["sandboxRoot"]).resolve(),
                     Path(data["credentialFile"]).resolve(), Path(data["tokenFile"]).resolve(),
                     data["accountId"], frozenset(data["allowedGroupIds"]), data["hookSha256"])
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", config.sandbox)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", config.hook_sha256)
                or not config.account_id or not config.groups
                or any(not isinstance(group, str) or not group.endswith("@chatroom") for group in config.groups)):
            raise GatewayError("configuration_invalid")
        return config

    def reader_credentials(self):
        data = read_private_json(self.credential_file)
        account = Path(data["dbPath"]).resolve()
        if not data.get("ok") or not account.is_relative_to(self.sandbox_root):
            raise GatewayError("reader_account_outside_sandbox")
        # Folder names carry a four-character installation suffix; require the
        # remainder to equal the explicit resolved account rather than a prefix.
        if account.name != self.account_id and re.sub(r"_[a-zA-Z0-9]{4}$", "", account.name) != self.account_id:
            raise GatewayError("reader_account_mismatch")
        return account, unprotect_credential(data["protectedKey"])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class NativeClient:
    def __init__(self, config: GatewayConfig, account_directory: str, verifier=verify_native_process, *, can_send=None):
        self.config = config
        self.account_directory = account_directory.lower()
        self.verifier = verifier
        self.can_send = can_send
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _request(self, route: str, payload: dict | None = None):
        token = self.config.token_file.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", token):
            raise GatewayError("native_token_invalid")
        headers = {"Authorization": "Bearer " + token, "X-WeChatAgent-Account": self.account_directory,
                   "Content-Type": "application/json"}
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request("http://127.0.0.1:30001" + route, data=body, headers=headers)
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError()
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except Exception:
            raise GatewayError("native_request_failed") from None

    def health(self):
        # Check listener ownership before disclosing the bearer token.
        try:
            pid = self.verifier(self.config)
            data = self._request("/WeChatAgent/health")
        except (GatewayError, OSError, ValueError, TypeError):
            raise GatewayFatalError("native_identity_or_health_check_failed") from None
        if (data.get("pid") != pid or data.get("version") != "4.1.10.27"
                or data.get("startupState") != "ready" or data.get("integration") != "WeChatAgent-1"
                or data.get("databaseAccounts") != [self.account_directory]):
            raise GatewayFatalError("native_account_or_version_mismatch")
        return {"ready": True, "version": "4.1.10.27", 'nativeStickerSend': data.get('nativeStickerSend') is True,
                'pid': pid}

    def send_text(self, group_id: str, text: str):
        if group_id not in self.config.groups or not text.strip() or len(text.encode()) > 16000:
            raise GatewayError("invalid_native_send")
        self.health()
        if self.can_send is None or not self.can_send(group_id):
            raise GatewayError("conversation_not_allowed_or_paused")
        return self._request("/SendTextMsg", {"wxidorgid": group_id, "msg": text})

    def send_image(self, group_id: str, path: Path):
        if group_id not in self.config.groups:
            raise GatewayError('invalid_native_send')
        path = path.resolve()
        metadata = static_gif(path, directory=outgoing_directory(self.config.expected_executable))
        health = self.health()
        if not health.get('nativeStickerSend'):
            raise GatewayError('native_media_not_supported')
        if self.can_send is None or not self.can_send(group_id):
            raise GatewayError('conversation_not_allowed_or_paused')
        return self._request('/SendStickerMsg', {'wxidorgid': group_id, 'path': str(path), 'sticker': metadata})


class NativeSender:
    def __init__(self, reader, native, store, account_id: str, groups: frozenset[str], *,
                 clock=time.time, sleep=time.sleep, receipt_timeout=18):
        self.reader, self.native, self.store = reader, native, store
        self.account_id, self.groups = account_id, groups
        self.clock, self.sleep, self.receipt_timeout = clock, sleep, receipt_timeout

    def send_group_text(self, group_id: str, text: str) -> SendReceipt:
        if group_id not in self.groups or not self.store.is_allowed(group_id) or self.store.is_paused():
            raise GatewayError("conversation_not_allowed_or_paused")
        started = self.clock()
        baseline = {(row.shard, row.local_id) for row in self.reader.recent(group_id, int(started) - 30)}
        # Recheck policy after database access and immediately before submission.
        if not self.store.is_allowed(group_id) or self.store.is_paused():
            raise GatewayError("conversation_not_allowed_or_paused")
        try:
            accepted = self.native.send_text(group_id, text)
        except GatewayFatalError:
            raise
        except Exception:
            return SendReceipt(False)
        if accepted.get("ret") != 0:
            return SendReceipt(False)
        deadline = time.monotonic() + self.receipt_timeout
        while time.monotonic() < deadline:
            for row in self.reader.recent(group_id, int(started) - 30):
                if ((row.shard, row.local_id) not in baseline and row.local_type == 1
                        and row.sender_id == self.account_id and row.text == text
                        and row.create_time >= int(started) - 30 and row.local_id > 0
                        and row.server_id > 0):
                    return SendReceipt(True, str(row.server_id))
            self.sleep(1)
        return SendReceipt(False)

    def send_group_image(self, group_id: str, path: Path) -> SendReceipt:
        if group_id not in self.groups or not self.store.is_allowed(group_id) or self.store.is_paused():
            raise GatewayError('conversation_not_allowed_or_paused')
        metadata = static_gif(path)
        started = self.clock()
        baseline = {(row.shard, row.local_id) for row in self.reader.recent(group_id, int(started) - 30)}
        if not self.store.is_allowed(group_id) or self.store.is_paused():
            raise GatewayError('conversation_not_allowed_or_paused')
        try:
            accepted = self.native.send_image(group_id, path)
        except GatewayFatalError:
            raise
        except Exception:
            return SendReceipt(False)
        if accepted.get('ret') != 0:
            return SendReceipt(False)
        deadline = time.monotonic() + self.receipt_timeout
        while time.monotonic() < deadline:
            for row in self.reader.recent(group_id, int(started) - 30):
                if ((row.shard, row.local_id) not in baseline and row.sender_id == self.account_id
                        and row.create_time >= int(started) - 30 and row.local_id > 0 and row.server_id > 0
                        and matches_image_receipt(row, metadata['md5'])):
                    return SendReceipt(True, str(row.server_id))
            self.sleep(1)
        return SendReceipt(False)


class GatewayRunner:
    def __init__(self, reader, native, service, store, account_id: str, groups: frozenset[str], *, clock=time.time):
        self.reader, self.native, self.service, self.store = reader, native, service, store
        self.account_id, self.groups = account_id, groups
        self.cursors: dict[str, dict[str, int]] = {}
        self.clock = clock
        self.started_at = 0.0

    def initialize(self):
        self.native.health()
        self.started_at = self.clock()
        self.cursors = {group: self.reader.snapshot(group) for group in self.groups if self.store.is_allowed(group)}

    def tick(self):
        self.native.health()
        active = {group for group in self.groups if self.store.is_allowed(group)}
        for removed in set(self.cursors) - active:
            del self.cursors[removed]
        counts = {"received": 0, "confirmed": 0, "unconfirmed": 0, "modelErrors": 0, "rateLimited": 0,
                  "deferred": 0, "naturalEvaluated": 0, "silent": 0, "modelErrorCodes": [], "patReceived": 0,
                  'modelRetries': 0, 'mediaConfirmed': 0, 'mediaUnconfirmed': 0, 'mediaErrorCodes': []}

        def record_result(result, *, natural=False):
            if result.media_delivery == DeliveryOutcome.CONFIRMED:
                counts['mediaConfirmed'] += 1
            elif result.media_delivery == DeliveryOutcome.UNCONFIRMED:
                counts['mediaUnconfirmed'] += 1
            if isinstance(result.media_reason, str) and result.media_reason not in counts['mediaErrorCodes']:
                counts['mediaErrorCodes'].append(result.media_reason)
            attempts = result.processed.model_attempts
            if type(result.processed.model_retries) is int:
                counts['modelRetries'] += result.processed.model_retries
            elif type(attempts) is int:
                counts['modelRetries'] += max(0, attempts - 1)
            if result.processed.outcome == Outcome.ERROR:
                counts["modelErrors"] += 1
                reason = result.processed.reason
                if isinstance(reason, str) and reason not in counts["modelErrorCodes"]:
                    counts["modelErrorCodes"].append(reason)
            elif result.processed.outcome == Outcome.RATE_LIMITED:
                counts["rateLimited"] += 1
            elif result.processed.outcome == Outcome.DEFERRED:
                counts["deferred"] += 1
            elif natural and result.processed.outcome == Outcome.IGNORED and result.processed.reason == "model_silent":
                counts["silent"] += 1
            if result.delivery == DeliveryOutcome.CONFIRMED:
                counts["confirmed"] += 1
            elif result.delivery == DeliveryOutcome.UNCONFIRMED:
                counts["unconfirmed"] += 1
        for group in sorted(active):
            if group not in self.cursors:
                self.cursors[group] = self.reader.snapshot(group)
                continue
            cursors = self.cursors[group]
            blocked = set()
            for row in self.reader.read_after(group, cursors):
                if row.shard in blocked:
                    continue
                if (row.local_type in (1, PAT_TYPE) and row.sender_id != self.account_id and row.server_id <= 0
                        and row.create_time >= self.started_at - 30):
                    blocked.add(row.shard)
                    continue
                # Advance before handling: delivery state provides persistent
                # at-most-once processing if a model or receipt operation fails.
                cursors[row.shard] = max(cursors.get(row.shard, 0), row.local_id)
                if row.create_time < self.started_at - 30:
                    continue
                mapper = native_group_pat if row.local_type == PAT_TYPE else native_group_message
                raw = mapper(account_id=self.account_id, group_id=group, sender_id=row.sender_id,
                    server_id=row.server_id, local_type=row.local_type, message_content=row.text, source=row.source)
                incoming = normalize_gateway_message(raw, self.account_id) if raw else None
                if incoming is None:
                    continue
                resolver = getattr(self.reader, 'sender_display_name', None)
                name = resolver(group, incoming.sender_id) if callable(resolver) else None
                if isinstance(name, str) and name:
                    incoming = replace(incoming, sender_name=name)
                result = self.service.handle(incoming)
                counts["received"] += 1
                if incoming.interaction == "pat":
                    counts["patReceived"] += 1
                record_result(result)
        for result in self.service.flush_pending():
            counts["naturalEvaluated"] += 1
            record_result(result, natural=True)
        return counts
