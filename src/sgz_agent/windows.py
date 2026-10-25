"""Win32 window metadata, input and hotkey adapters. No game process memory access."""
import ctypes as C
from ctypes import wintypes as W
import os
import threading
from pathlib import Path

from .guard import GuardError
from .models import WindowSnapshot


ULONG_PTR = C.c_size_t


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class INPUTUNION(C.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _fields_ = [("type", W.DWORD), ("data", INPUTUNION)]


def libraries():
    if os.name != "nt":
        raise RuntimeError("Windows is required")
    user = C.WinDLL("user32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "IsWindow": ([W.HWND], W.BOOL), "IsWindowVisible": ([W.HWND], W.BOOL),
        "IsIconic": ([W.HWND], W.BOOL), "GetForegroundWindow": ([], W.HWND),
        "GetAncestor": ([W.HWND, W.UINT], W.HWND),
        "GetClientRect": ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
        "ClientToScreen": ([W.HWND, C.POINTER(W.POINT)], W.BOOL),
        "GetWindowThreadProcessId": ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
        "GetWindowTextLengthW": ([W.HWND], C.c_int),
        "GetWindowTextW": ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
        "WindowFromPoint": ([W.POINT], W.HWND),
        "SendInput": ([W.UINT, C.POINTER(INPUT), C.c_int], W.UINT),
        "GetSystemMetrics": ([C.c_int], C.c_int),
        "SetForegroundWindow": ([W.HWND], W.BOOL),
        "RegisterHotKey": ([W.HWND, C.c_int, W.UINT, W.UINT], W.BOOL),
        "UnregisterHotKey": ([W.HWND, C.c_int], W.BOOL),
        "GetMessageW": ([C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], W.BOOL),
        "PostThreadMessageW": ([W.DWORD, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(user, name)
        fn.argtypes, fn.restype = args, result
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = W.BOOL
    kernel.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
    kernel.CreateMutexW.restype = W.HANDLE
    kernel.GetCurrentThreadId.restype = W.DWORD
    try:
        user.SetProcessDpiAwarenessContext.argtypes = [C.c_void_p]
        user.SetProcessDpiAwarenessContext(C.c_void_p(-4))
    except AttributeError:
        pass
    return user, kernel


class WindowsDesktop:
    def __init__(self):
        self.user, self.kernel = libraries()
        self._mutex = None

    def process_path(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise C.WinError(C.get_last_error())
        try:
            buf = C.create_unicode_buffer(32768)
            size = W.DWORD(len(buf))
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, buf, C.byref(size)):
                raise C.WinError(C.get_last_error())
            return buf.value
        finally:
            self.kernel.CloseHandle(handle)

    def activate(self, hwnd):
        if not self.user.SetForegroundWindow(hwnd):
            raise GuardError("activation_denied")

    def snapshot(self, hwnd):
        if not self.user.IsWindow(hwnd):
            raise GuardError("window_closed")
        pid = W.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        rect, origin = W.RECT(), W.POINT(0, 0)
        if not self.user.GetClientRect(hwnd, C.byref(rect)) or not self.user.ClientToScreen(hwnd, C.byref(origin)):
            raise C.WinError(C.get_last_error())
        title = C.create_unicode_buffer(self.user.GetWindowTextLengthW(hwnd) + 1)
        self.user.GetWindowTextW(hwnd, title, len(title))
        foreground = self.user.GetAncestor(self.user.GetForegroundWindow(), 2)
        return WindowSnapshot(int(hwnd), pid.value, self.process_path(pid.value),
                              (origin.x, origin.y, rect.right, rect.bottom),
                              foreground == hwnd, bool(self.user.IsIconic(hwnd)), title.value)

    def list_windows(self, executable):
        result = []
        wanted = os.path.normcase(os.path.abspath(str(executable)))
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        def visit(hwnd, _):
            if self.user.IsWindowVisible(hwnd):
                try:
                    state = self.snapshot(hwnd)
                    if os.path.normcase(os.path.abspath(state.exe)) == wanted:
                        result.append(state)
                except (OSError, GuardError):
                    pass
            return True
        callback = callback_type(visit)
        self.user.EnumWindows.argtypes = [callback_type, W.LPARAM]
        self.user.EnumWindows(callback, 0)
        return result

    def ensure_hit(self, hwnd, point):
        hit = self.user.WindowFromPoint(W.POINT(*point))
        if self.user.GetAncestor(hit, 2) != hwnd:
            raise GuardError("window_occluded")

    def capture(self, window):
        from mss import MSS
        from PIL import Image
        left, top, width, height = window.rect
        for dx, dy in ((1, 1), (width-2, 1), (1, height-2), (width-2, height-2), (width//2, height//2)):
            self.ensure_hit(window.hwnd, (left + dx, top + dy))
        with MSS() as capture:
            shot = capture.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def acquire(self, hwnd):
        if self._mutex:
            return
        state = self.snapshot(hwnd)
        handle = self.kernel.CreateMutexW(None, False, f"Local\\SGZAgent-{state.pid}-{hwnd}")
        error = C.get_last_error()
        if not handle:
            raise C.WinError(error)
        if error == 183:
            self.kernel.CloseHandle(handle)
            raise GuardError("window_busy")
        self._mutex = handle

    def release(self):
        if self._mutex:
            self.kernel.CloseHandle(self._mutex)
            self._mutex = None

    def _send(self, item):
        if self.user.SendInput(1, C.byref(item), C.sizeof(INPUT)) != 1:
            raise OSError(C.get_last_error(), "SendInput failed")

    def move(self, point):
        left, top = self.user.GetSystemMetrics(76), self.user.GetSystemMetrics(77)
        width, height = self.user.GetSystemMetrics(78), self.user.GetSystemMetrics(79)
        x = round((point[0] - left) * 65535 / max(1, width - 1))
        y = round((point[1] - top) * 65535 / max(1, height - 1))
        self._send(INPUT(0, INPUTUNION(mi=MOUSEINPUT(x, y, 0, 0xC001, 0, 0))))

    def button(self, down):
        self._send(INPUT(0, INPUTUNION(mi=MOUSEINPUT(0, 0, 0, 0x0002 if down else 0x0004, 0, 0))))


class StopListener:
    def __init__(self, callback):
        self.callback = callback
        self.ready = threading.Event()
        self.thread_id = None
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="sgz-stop-hotkey")

    def start(self):
        self.thread.start()
        if not self.ready.wait(3) or self.error:
            raise RuntimeError(f"stop_hotkey_unavailable: {self.error or 'timeout'}")
        return self

    def _run(self):
        self.user, kernel = libraries()
        self.thread_id = kernel.GetCurrentThreadId()
        if not self.user.RegisterHotKey(None, 0x5347, 0x4003, 0x7B):
            self.error = C.get_last_error()
            self.ready.set()
            return
        self.ready.set()
        msg = W.MSG()
        try:
            while self.user.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:
                    self.callback()
        finally:
            self.user.UnregisterHotKey(None, 0x5347)

    def close(self):
        if self.thread_id and self.thread.is_alive():
            self.user.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(2)
