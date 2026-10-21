"""Optional, local voice-message support for the Windows WeChat UI."""


class VoiceError(Exception):
    """A voice operation failed with a safe, non-secret reason."""

    def __init__(self, code: str, *, sent: bool = False):
        super().__init__(code)
        self.code = code
        self.sent = sent
