"""A local random secret keeps group-scoped pseudonyms stable across restarts."""

import os
import secrets
from pathlib import Path


def load_identity_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(secrets.token_bytes(32))
                handle.flush()
                os.fsync(handle.fileno())
    with path.open('rb') as handle:
        result = handle.read(33)
    if len(result) != 32:
        raise ValueError('member_identity_key_invalid')
    return result
