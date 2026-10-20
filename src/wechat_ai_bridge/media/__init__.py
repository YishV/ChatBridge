"""Local audio and cloud media helpers with safe user-facing failures."""

import re


class CoverError(Exception):
    """Expose only a fixed, non-sensitive error code to callers."""

    def __init__(self, code: str):
        if not isinstance(code, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', code):
            code = 'cover_error'
        self.code = code
        super().__init__(code)
