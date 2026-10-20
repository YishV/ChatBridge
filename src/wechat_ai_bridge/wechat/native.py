"""Pure normalization of explicitly supplied native WeChat message metadata.

This module does not read databases or infer mentions from message text. The
caller supplies resolved account, group and sender IDs alongside native fields.
"""

import hashlib
import json
import re
from xml.etree import ElementTree

from wechat_ai_bridge.wechat.events import GatewayMessage


MAX_SOURCE_BYTES = 64 * 1024
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,255}\Z")


def _source_text(source: str | bytes | None) -> str | None:
    if not isinstance(source, (str, bytes)) or not source or len(source) > MAX_SOURCE_BYTES:
        return None
    try:
        if isinstance(source, str):
            return source if len(source.encode("utf-8")) <= MAX_SOURCE_BYTES else None
        if source.startswith(_ZSTD_MAGIC):
            try:
                import zstandard
            except ImportError:
                return None
            try:
                frame = zstandard.get_frame_parameters(source)
                if (frame.window_size > MAX_SOURCE_BYTES
                        or frame.content_size not in (zstandard.CONTENTSIZE_UNKNOWN,)
                        and frame.content_size > MAX_SOURCE_BYTES):
                    return None
                # Known frame sizes are checked above: max_output_size alone
                # only bounds frames whose decompressed size is unspecified.
                source = zstandard.ZstdDecompressor().decompress(
                    source, max_output_size=MAX_SOURCE_BYTES, allow_extra_data=False,
                )
            except zstandard.ZstdError:
                return None
        if len(source) > MAX_SOURCE_BYTES:
            return None
        return source.decode("utf-8", errors="strict")
    except UnicodeError:
        return None


def parse_native_mentions(source: str | bytes | None) -> tuple[str, ...] | None:
    """Return native account IDs, () for known no-mention, or None if unknown.

Only the unnamespaced msgsource root and its direct, flat atuserlist are trusted.
Unrelated native metadata is ignored; nested, duplicate or namespaced mention
lists are rejected. Both compressed and decoded data are limited to 64 KiB.
"""
    text = _source_text(source)
    # UTF-16 ASCII bytes can pass UTF-8 decoding as ASCII plus NUL. Reject NUL
    # before XML parsing can autodetect UTF-16 and bypass the declaration guard.
    if text is None or "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        return None
    encoding = re.search(r"<\?xml\b[^?]*\bencoding\s*=\s*(['\"])(.*?)\1", text, re.IGNORECASE)
    if encoding and encoding.group(2).lower() not in ("utf-8", "utf8"):
        return None
    try:
        root = ElementTree.fromstring(text)
    except (ElementTree.ParseError, ValueError):
        return None
    if root.tag != "msgsource" or root.attrib or (root.text or "").strip():
        return None
    direct_lists = root.findall("atuserlist")
    all_lists = [element for element in root.iter()
                 if element.tag.rsplit("}", 1)[-1] == "atuserlist"]
    if len(direct_lists) > 1 or all_lists != direct_lists:
        return None
    if any((child.tail or "").strip() for child in root):
        return None
    if not direct_lists:
        return ()
    mentions = direct_lists[0]
    if mentions.attrib or len(mentions):
        return None
    value = (mentions.text or "").strip()
    if not value:
        return ()
    identifiers = tuple(part.strip() for part in value.split(","))
    if any(not _IDENTIFIER.fullmatch(identifier) for identifier in identifiers):
        return None
    return tuple(dict.fromkeys(identifiers))


def native_group_message(
    *, account_id: str, group_id: str, sender_id: str, server_id: int | str,
    local_type: int, message_content: str, source: str | bytes | None,
) -> GatewayMessage | None:
    """Map native columns and resolved identities to one confirmed group text.

server_id, local_type, message_content and source are native row fields. Sender
IDs must already be resolved from the same shard's Name2Id table. Unknown mention
metadata remains None, so downstream normalization cannot trigger from text.
"""
    if (any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value)
            for value in (account_id, group_id, sender_id))
            or not group_id.endswith("@chatroom")
            or type(local_type) is not int or local_type != 1
            or not isinstance(message_content, str) or not message_content.strip()
            or type(server_id) not in (int, str)):
        return None
    server = str(server_id)
    if not re.fullmatch(r"[0-9]{1,32}", server) or int(server) <= 0:
        return None
    identity = json.dumps([account_id, group_id, str(int(server))], separators=(",", ":"))
    return GatewayMessage(
        message_id="wechat-native:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        conversation_id=group_id,
        sender_id=sender_id,
        text=message_content,
        message_type="text",
        is_group=True,
        is_from_bot=sender_id == account_id,
        mentioned_account_ids=parse_native_mentions(source),
    )
