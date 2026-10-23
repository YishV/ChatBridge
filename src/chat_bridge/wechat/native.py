"""Pure normalization of explicitly supplied native WeChat message metadata.

This module does not read databases or infer mentions from message text. The
caller supplies resolved account, group and sender IDs alongside native fields.
"""

import hashlib
import json
import re
from dataclasses import replace
from xml.etree import ElementTree

from chat_bridge.wechat.events import GatewayMessage


MAX_SOURCE_BYTES = 64 * 1024
PAT_TYPE = (62 << 32) | 49
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,255}\Z")


def native_group_pat(
    *, account_id: str, group_id: str, sender_id: str | None, server_id: int | str,
    local_type: int, message_content: str | bytes, source: str | bytes | None = None,
) -> GatewayMessage | None:
    """Map a confirmed native group pat directed at the current bot account."""
    if type(local_type) is not int or local_type != PAT_TYPE:
        return None
    root = _native_xml(message_content)
    if root is None or root.tag != "msg" or root.attrib:
        return None
    apps = _direct_elements(root, "appmsg")
    if apps is None or len(apps) != 1:
        return None
    app = apps[0]
    types = _direct_elements(app, "type")
    pats = _direct_elements(app, "patinfo")
    if (types is None or len(types) != 1 or _flat_text(types[0]) != "62"
            or pats is None or len(pats) != 1 or pats[0].attrib):
        return None
    pat = pats[0]
    if any((element.text or "").strip() or any((child.tail or "").strip() for child in element)
           for element in (root, app, pat)):
        return None
    fields = {}
    for name in ("fromusername", "chatusername", "pattedusername"):
        elements = _direct_elements(pat, name)
        if elements is None or len(elements) != 1:
            return None
        value = _flat_text(elements[0])
        if value is None or not _IDENTIFIER.fullmatch(value):
            return None
        fields[name] = value
    actor = fields["fromusername"]
    root_senders = root.findall("fromusername")
    # The observed XML also contains an optional empty root-level sender. Only
    # that field and patinfo's actor may use this tag anywhere in the message.
    expected_senders = [pat.find("fromusername"), *root_senders]
    all_senders = [element for element in root.iter()
                   if element.tag.rsplit("}", 1)[-1] == "fromusername"]
    if (len(root_senders) > 1 or len(all_senders) != len(expected_senders)
            or any(element not in expected_senders for element in all_senders)):
        return None
    if root_senders and _flat_text(root_senders[0]) not in ("", actor):
        return None
    # Reject nested/namespaced copies outside patinfo as well as within it.
    for name in ("patinfo", "chatusername", "pattedusername"):
        expected = pat if name == "patinfo" else pat.find(name)
        if [element for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == name] != [expected]:
            return None
    if (actor == account_id or fields["pattedusername"] != account_id
            or fields["chatusername"] != group_id
            or sender_id is not None and (not isinstance(sender_id, str)
                                         or sender_id not in ("", actor))):
        return None
    message = native_group_message(
        account_id=account_id, group_id=group_id, sender_id=actor, server_id=server_id,
        local_type=1, message_content="[拍一拍] 群友拍了拍你。", source=None,
    )
    return replace(message, message_type="pat", target_account_id=account_id) if message else None


def _flat_text(element: ElementTree.Element) -> str | None:
    if element.attrib or len(element):
        return None
    return (element.text or "").strip()


def _direct_elements(parent: ElementTree.Element, name: str) -> list[ElementTree.Element] | None:
    """Find direct unnamespaced fields, refusing hidden or namespaced copies."""
    direct = parent.findall(name)
    all_fields = [element for element in parent.iter()
                  if element.tag.rsplit("}", 1)[-1] == name]
    return direct if all_fields == direct else None


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
                compressed = source
                source = zstandard.ZstdDecompressor().decompress(
                    source, max_output_size=MAX_SOURCE_BYTES, allow_extra_data=False,
                )
                # Unknown-size frames can ignore allow_extra_data in the
                # native binding. After the bounded pass, verify that exactly
                # one complete frame consumed all input. Its output is already
                # known to fit the same limit.
                verifier = zstandard.ZstdDecompressor().decompressobj()
                verifier.decompress(compressed)
                if not verifier.eof or verifier.unused_data:
                    return None
            except zstandard.ZstdError:
                return None
        if len(source) > MAX_SOURCE_BYTES:
            return None
        return source.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        return None


def _native_xml(source: str | bytes | None) -> ElementTree.Element | None:
    """Decode bounded native XML while rejecting alternate encodings and DTDs."""
    text = _source_text(source)
    # UTF-16 ASCII bytes can pass UTF-8 decoding as ASCII plus NUL. Reject NUL
    # before XML parsing can autodetect UTF-16 and bypass the declaration guard.
    if text is None or "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        return None
    encoding = re.search(r"<\?xml\b[^?]*\bencoding\s*=\s*(['\"])(.*?)\1", text, re.IGNORECASE)
    if encoding and encoding.group(2).lower() not in ("utf-8", "utf8"):
        return None
    try:
        return ElementTree.fromstring(text)
    except (ElementTree.ParseError, ValueError):
        return None


def parse_native_mentions(source: str | bytes | None) -> tuple[str, ...] | None:
    """Return native account IDs, () for known no-mention, or None if unknown.

Only the unnamespaced msgsource root and its direct, flat atuserlist are trusted.
Unrelated native metadata is ignored; nested, duplicate or namespaced mention
lists are rejected. Both compressed and decoded data are limited to 64 KiB.
"""
    root = _native_xml(source)
    if root is None:
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


def native_private_message(
    *, account_id: str, conversation_id: str, sender_id: str, server_id: int | str,
    local_type: int, message_content: str,
) -> GatewayMessage | None:
    """Map a confirmed one-to-one native text row without group assumptions."""
    if (any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value)
            for value in (account_id, conversation_id, sender_id))
            or conversation_id.endswith("@chatroom")
            or type(local_type) is not int or local_type != 1
            or not isinstance(message_content, str) or not message_content.strip()
            or type(server_id) not in (int, str)):
        return None
    server = str(server_id)
    if not re.fullmatch(r"[0-9]{1,32}", server) or int(server) <= 0:
        return None
    identity = json.dumps([account_id, conversation_id, str(int(server))], separators=(",", ":"))
    return GatewayMessage(
        message_id="wechat-private:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        conversation_id=conversation_id,
        sender_id=sender_id,
        text=message_content,
        message_type="text",
        is_group=False,
        is_from_bot=sender_id == account_id,
        mentioned_account_ids=None,
    )


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
