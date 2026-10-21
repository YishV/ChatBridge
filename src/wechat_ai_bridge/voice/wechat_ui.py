"""Minimal Win32 automation for WeChat's visible voice-message button."""

import ctypes
import time
from ctypes import wintypes

from wechat_ai_bridge.voice.errors import VoiceError


class WeChatUi:
    TITLE = '微信'
    SEARCH = (0.16, 0.09)
    INPUT = (0.47, 0.88)
    VOICE_ICON = (0.856, 0.930)

    def __init__(self, pid: int):
        if type(pid) is not int or pid <= 0:
            raise VoiceError('voice_window_missing')
        self.pid = pid
        self.hwnd = None
        self.previous = 0

    def _find(self):
        user32 = ctypes.windll.user32
        found = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != self.pid or not user32.IsWindowVisible(hwnd):
                return True
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            if title.value != self.TITLE:
                return True
            rect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                found.append((max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top), hwnd))
            return True

        user32.EnumWindows(callback_type(callback), 0)
        if not found:
            raise VoiceError('voice_window_missing')
        return max(found, key=lambda item: item[0])[1]

    def begin(self):
        user32 = ctypes.windll.user32
        self.hwnd = self._find()
        self.previous = user32.GetForegroundWindow()
        user32.ShowWindow(self.hwnd, 9)
        user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0003)
        for _ in range(3):
            user32.SetForegroundWindow(self.hwnd)
            if user32.GetForegroundWindow() == self.hwnd:
                break
            foreground = user32.GetForegroundWindow()
            if foreground:
                pid = wintypes.DWORD()
                target_thread = user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
                current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                user32.AttachThreadInput(current_thread, target_thread, True)
                try:
                    user32.BringWindowToTop(self.hwnd)
                    user32.SetForegroundWindow(self.hwnd)
                finally:
                    user32.AttachThreadInput(current_thread, target_thread, False)
            time.sleep(0.3)
        if user32.GetForegroundWindow() != self.hwnd:
            self.end()
            raise VoiceError('voice_window_foreground_failed')
        time.sleep(0.2)

    def end(self):
        user32 = ctypes.windll.user32
        if self.hwnd:
            user32.SetWindowPos(self.hwnd, -2, 0, 0, 0, 0, 0x0003)
        if self.previous:
            user32.SetForegroundWindow(self.previous)
        time.sleep(0.2)

    def _rect(self):
        if not self.hwnd:
            raise VoiceError('voice_window_missing')
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            raise VoiceError('voice_window_missing')
        return rect

    def _overlay_rect(self):
        user32 = ctypes.windll.user32
        rect = self._rect()
        found = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            klass = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, klass, 256)
            if 'Chrome_WidgetWin_0' not in klass.value:
                return True
            child = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(child)):
                return True
            width = child.right - child.left
            if (250 <= width <= 600 and child.left > rect.left + (rect.right - rect.left) * 0.55):
                found.append(child)
            return True

        user32.EnumChildWindows(self.hwnd, callback_type(callback), 0)
        return found[0] if found else None

    def _chat_rect(self):
        """Exclude an open WeChat search side window from chat coordinates."""
        rect = self._rect()
        overlay = self._overlay_rect()
        if overlay is not None:
            rect.right = overlay.left - 2
        return rect

    def _reset_search(self):
        """A search overlay closes when WeChat briefly loses foreground."""
        if self._overlay_rect() is None:
            return
        user32 = ctypes.windll.user32
        if self.previous:
            user32.SetForegroundWindow(self.previous)
            time.sleep(0.4)
        user32.SetForegroundWindow(self.hwnd)
        time.sleep(0.4)

    def _click_point(self, x, y):
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.2)

    def _click(self, relative, *, full=False):
        rect = self._rect() if full else self._chat_rect()
        self._click_point(rect.left + int((rect.right - rect.left) * relative[0]),
                          rect.top + int((rect.bottom - rect.top) * relative[1]))

    @staticmethod
    def _key(vk, flags=0):
        ctypes.windll.user32.keybd_event(vk, 0, flags, 0)

    def _clear_at(self, relative, *, full=False):
        self._click(relative, full=full)
        self._key(0x11, 0)
        self._key(0x41, 0)
        self._key(0x41, 2)
        self._key(0x11, 2)
        self._key(0x2E, 0)
        self._key(0x2E, 2)

    def _clear(self):
        self._clear_at(self.INPUT)

    def open_chat(self, name: str, *, prefer_group: bool = False):
        if (not isinstance(name, str) or not 1 <= len(name) <= 64
                or any(ord(char) < 32 for char in name)):
            raise VoiceError('voice_chat_name_invalid')
        try:
            import pyperclip
        except ImportError:
            raise VoiceError('voice_clipboard_missing') from None
        self._reset_search()
        self._click(self.SEARCH, full=True)
        self._clear_at(self.SEARCH, full=True)
        try:
            pyperclip.copy(name)
        except Exception as exc:
            raise VoiceError('voice_clipboard_failed') from exc
        time.sleep(0.2)
        self._key(0x11, 0)
        self._key(0x56, 0)
        self._key(0x56, 2)
        self._key(0x11, 2)
        time.sleep(1.0)
        if prefer_group:
            rect = self._rect()
            overlay = self._overlay_rect()
            if overlay is None and rect.right - rect.left <= 1000:
                raise VoiceError('voice_search_not_open')
            # Enter in the search field opens the first web card. Move the
            # selection into the result list, jump to the last section (群聊),
            # then open it.
            self._key(0x28, 0)
            self._key(0x28, 2)
            time.sleep(0.2)
            self._key(0x23, 0)
            self._key(0x23, 2)
            time.sleep(0.2)
            self._key(0x0D, 0)
            self._key(0x0D, 2)
            time.sleep(1.2)
            return
        self._key(0x0D, 0)
        self._key(0x0D, 2)
        time.sleep(1.2)
        # Close the search side panel; the chat remains open. A second Esc is
        # harmless in the chat and handles focus that remains in search results.
        self._key(0x1B, 0)
        self._key(0x1B, 2)
        time.sleep(0.3)
        self._key(0x1B, 0)
        self._key(0x1B, 2)
        time.sleep(0.4)

    def focus_input(self):
        self._clear()

    def start_recording(self):
        self._click(self.VOICE_ICON)

    def stop_recording(self):
        self._click(self.VOICE_ICON)
