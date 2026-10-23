"""Read only selected conversations from encrypted local WeChat databases."""

import hashlib
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from chat_bridge.wechat.errors import GatewayError
from chat_bridge.wechat.native import _source_text


@dataclass(frozen=True)
class NativeRow:
    shard: str
    local_id: int
    server_id: int
    create_time: int
    sender_id: str = field(repr=False)
    local_type: int
    text: str = field(repr=False)
    source: str | bytes | None = field(repr=False)


class EncryptedMessageReader:
    def __init__(self, account_path: Path, passphrase: str, account_id: str, groups: frozenset[str]):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", passphrase):
            raise GatewayError("invalid_reader_credential")
        self.account_path = account_path.resolve()
        self.account_id = account_id
        self.groups = groups
        self._passphrase = bytes.fromhex(passphrase)
        self._keys: dict[bytes, bytes] = {}
        self._sender_names: OrderedDict[tuple[str, str], tuple[float, str | None]] = OrderedDict()

    def sender_display_name(self, group_id: str, sender_id: str) -> str | None:
        """Resolve the WeChat nickname of a sender from a validated group message.

        Callers must first validate a message in this group and obtain its sender
        from that message. This lookup does not establish group membership, and
        contact.nick_name is a WeChat nickname rather than a group-specific name.
        """
        if (group_id not in self.groups
                or not isinstance(sender_id, str) or not sender_id.strip()
                or sender_id == self.account_id or sender_id.endswith("@chatroom")):
            return None
        key = (group_id, sender_id)
        now = time.monotonic()
        cached = self._sender_names.get(key)
        if cached is not None and now < cached[0]:
            self._sender_names.move_to_end(key)
            return cached[1]
        self._sender_names.pop(key, None)
        name = None
        try:
            directory = self.account_path / "db_storage" / "contact"
            resolved_directory = directory.resolve()
            path = (directory / "contact.db").resolve()
            if (resolved_directory.is_relative_to(directory)
                    and path.is_relative_to(resolved_directory) and path.is_file()):
                connection = self._connect(path)
                try:
                    row = connection.execute(
                        "SELECT nick_name FROM contact WHERE username=? LIMIT 1", (sender_id,)).fetchone()
                    if row and isinstance(row[0], str):
                        cleaned = "".join(
                            " " if char.isspace() else char
                            for char in row[0]
                            if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf"})
                        name = " ".join(cleaned.split())[:64].strip() or None
                finally:
                    connection.close()
        except Exception:
            # Nicknames are optional; contact failures must not stop message reads.
            name = None
        self._sender_names[key] = (now + 60, name)
        if len(self._sender_names) > 500:
            self._sender_names.popitem(last=False)
        return name

    def conversation_display_name(self, group_id: str) -> str | None:
        """Return a unique group display name suitable for WeChat search.

        Voice sending drives the visible WeChat UI, so an ambiguous display
        name is rejected rather than risking a message in the wrong chat.
        """
        if group_id not in self.groups:
            return None
        directory = self.account_path / 'db_storage/contact'
        resolved_directory = directory.resolve()
        path = (directory / 'contact.db').resolve()
        if not resolved_directory.is_relative_to(self.account_path) or not path.is_relative_to(resolved_directory):
            return None
        if not path.is_file():
            return None
        try:
            connection = self._connect(path)
            try:
                row = connection.execute(
                    'SELECT remark, nick_name FROM contact WHERE username=? LIMIT 1', (group_id,)).fetchone()
                if not row:
                    return None
                raw = (row[0] or '').strip() or (row[1] or '').strip()
                cleaned = ''.join(
                    ' ' if char.isspace() else char
                    for char in raw
                    if char.isspace() or unicodedata.category(char) not in {'Cc', 'Cf'})
                name = ' '.join(cleaned.split())[:64].strip()
                if not name:
                    return None
                duplicate = (connection.execute(
                    'SELECT COUNT(*) FROM contact WHERE username LIKE ? AND (remark=? OR nick_name=?)',
                    ('%@chatroom', name, name)).fetchone()[0]
                    if group_id.endswith('@chatroom') else 1)
                if duplicate != 1:
                    return None
                return name
            finally:
                connection.close()
        except Exception:
            return None

    def list_contacts(self, query: str | None = None, *, limit: int = 100) -> list[dict]:
        """List local one-to-one contact wxids for explicit private allowlisting."""
        if query is not None and (not isinstance(query, str) or len(query) > 64):
            raise GatewayError('contact_query_invalid')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise GatewayError('contact_limit_invalid')
        directory = self.account_path / 'db_storage/contact'
        resolved_directory = directory.resolve()
        path = (directory / 'contact.db').resolve()
        if not resolved_directory.is_relative_to(self.account_path) or not path.is_relative_to(resolved_directory):
            raise GatewayError('contact_database_missing')
        if not path.is_file():
            raise GatewayError('contact_database_missing')
        pattern = '%' + (query.strip() if query else '') + '%'
        try:
            connection = self._connect(path)
            try:
                rows = connection.execute(
                    'SELECT username, remark, nick_name FROM contact '
                    'WHERE username NOT LIKE ? AND username NOT LIKE ? AND username NOT LIKE ? '
                    'AND (username LIKE ? OR remark LIKE ? OR nick_name LIKE ?) '
                    "ORDER BY COALESCE(NULLIF(remark, ''), NULLIF(nick_name, ''), username) LIMIT ?",
                    ('%@chatroom', 'gh_%', '%@openim', pattern, pattern, pattern, limit)).fetchall()
            finally:
                connection.close()
        except GatewayError:
            raise
        except Exception:
            raise GatewayError('contact_database_unreadable') from None
        contacts = []
        for username, remark, nickname in rows:
            if not isinstance(username, str) or not username:
                continue
            raw = (remark or '').strip() or (nickname or '').strip()
            cleaned = ''.join(
                ' ' if char.isspace() else char
                for char in raw
                if char.isspace() or unicodedata.category(char) not in {'Cc', 'Cf'})
            name = ' '.join(cleaned.split())[:64].strip()
            contacts.append({'wxid': username, 'name': name or username})
        return contacts

    def _shards(self):
        directory = self.account_path / "db_storage/message"
        if not directory.is_dir():
            raise GatewayError("message_database_missing")
        paths = sorted(p for p in directory.glob("message_*.db")
                       if re.fullmatch(r"message_\d+\.db", p.name))
        if not paths or len(paths) > 256:
            raise GatewayError("message_shard_scope_invalid")
        if any(not p.resolve().is_relative_to(self.account_path) for p in paths):
            raise GatewayError("message_database_outside_scope")
        return paths

    def _connect(self, path: Path):
        try:
            from sqlcipher3 import dbapi2 as sqlcipher
        except ImportError:
            raise GatewayError("install_native_dependencies") from None
        with path.open("rb") as handle:
            salt = handle.read(16)
        if len(salt) != 16:
            raise GatewayError("invalid_database_header")
        if salt not in self._keys:
            self._keys[salt] = hashlib.pbkdf2_hmac("sha512", self._passphrase, salt, 256000, 32)
        connection = sqlcipher.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            raw = (self._keys[salt] + salt).hex()
            connection.execute(f'''PRAGMA key = "x'{raw}'"''')
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA busy_timeout=3000")
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            return connection
        except BaseException:
            connection.close()
            raise GatewayError("database_unreadable") from None

    def _table(self, group_id: str):
        if group_id not in self.groups:
            raise GatewayError("conversation_not_allowed")
        return "Msg_" + hashlib.md5(group_id.encode()).hexdigest()

    def snapshot(self, group_id: str) -> dict[str, int]:
        table = self._table(group_id)
        result = {}
        for path in self._shards():
            connection = self._connect(path)
            try:
                exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
                result[path.name] = int(connection.execute(f'SELECT COALESCE(MAX(local_id), 0) FROM "{table}"').fetchone()[0]) if exists else 0
            finally:
                connection.close()
        return result

    def _read(self, group_id: str, cursors: dict[str, int] | None, since: int | None):
        table = self._table(group_id)
        result = []
        for path in self._shards():
            connection = self._connect(path)
            try:
                if not connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    continue
                clause, parameters = ("m.local_id > ?", (cursors.get(path.name, 0),)) if cursors is not None else ("m.create_time >= ?", (since,))
                order = "ASC" if cursors is not None else "DESC"
                rows = connection.execute(
                    f'SELECT m.local_id, m.server_id, m.create_time, n.user_name, m.local_type, '
                    f'm.message_content, m.source FROM "{table}" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id '
                    f'WHERE {clause} ORDER BY m.local_id {order} LIMIT 500', parameters).fetchall()
                for local, server, created, sender, kind, content, source in rows:
                    text = _source_text(content) or ""
                    # Some native rows prefix the body with the resolved sender.
                    if sender:
                        text = re.sub(r"^" + re.escape(sender) + r":\s*\n", "", text, count=1)
                    result.append(NativeRow(path.name, int(local), int(server or 0), int(created or 0),
                                            sender or "", int(kind or 0), text, source))
            except GatewayError:
                raise
            except Exception:
                raise GatewayError("message_schema_or_read_failed") from None
            finally:
                connection.close()
        return sorted(result, key=lambda row: (row.shard, row.local_id))

    def read_after(self, group_id: str, cursors: dict[str, int]) -> list[NativeRow]:
        return self._read(group_id, cursors, None)

    def recent(self, group_id: str, since: int) -> list[NativeRow]:
        return self._read(group_id, None, since)
