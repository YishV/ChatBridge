"""Errors from a local speech service; codes are safe to log."""


class SpeechError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
