"""Windows GUI for typing a UTF-8 file into the focused editor."""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog


if getattr(sys, "frozen", False):
    # PyInstaller onefile unpacks the script into a temporary folder; keep the
    # log (and the optional default file) next to the exe the user double-clicks.
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

LOG_PATH = BASE_DIR / "模拟输入.log"


def log(message: str) -> None:
    """Append one diagnostic line next to the script; never raise."""
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
VK_RETURN = 0x0D
VK_F8 = 0x77
MODE_SCANCODE = "scancode"
MODE_UNICODE = "unicode"
SCAN_LEFT_SHIFT = 0x2A
ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
GA_ROOT = 2


def build_us_keymap() -> dict[str, tuple[int, bool]]:
    """Map text to Set 1 scan codes for an English (US) guest keyboard."""
    keys = {" ": (0x39, False), "\t": (0x0F, False), "\n": (0x1C, False)}
    for plain, shifted, first_scan in (
        ("1234567890-=", "!@#$%^&*()_+", 0x02),
        ("qwertyuiop[]", "QWERTYUIOP{}", 0x10),
        ("asdfghjkl;'", 'ASDFGHJKL:"', 0x1E),
        ("`", "~", 0x29),
        ("\\", "|", 0x2B),
        ("zxcvbnm,./", "ZXCVBNM<>?", 0x2C),
    ):
        for offset, (lower, upper) in enumerate(zip(plain, shifted)):
            keys[lower] = (first_scan + offset, False)
            keys[upper] = (first_scan + offset, True)
    return keys


US_KEYMAP = build_us_keymap()


def validate_input_mode(content: str, mode: str) -> None:
    if mode == MODE_UNICODE:
        return
    if mode != MODE_SCANCODE:
        raise ValueError("未知的输入模式。")
    for index, character in enumerate(content, 1):
        if character not in US_KEYMAP:
            raise ValueError(
                f"VM 模式不支持第 {index} 个字符 {character!r} "
                f"(U+{ord(character):04X})。仅支持英文、数字、半角符号、Tab 和换行。"
            )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", INPUT_UNION)]


def keyboard_event(vk: int, scan: int, flags: int) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.data.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return event


def send_events(user32, events: list[INPUT]) -> None:
    batch = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(batch), batch, ctypes.sizeof(INPUT))
    if sent == len(batch):
        return
    error = ctypes.get_last_error()
    # Release only keys left down by the successfully inserted prefix.
    # In particular, a partial Shift+key sequence must not leave Shift held.
    pressed = {}
    for event in events[:sent]:
        key = event.data.ki
        identity = (key.wVk, key.wScan, key.dwFlags & ~KEYEVENTF_KEYUP)
        if key.dwFlags & KEYEVENTF_KEYUP:
            pressed.pop(identity, None)
        else:
            pressed[identity] = None
    if pressed:
        releases = [
            keyboard_event(vk, scan, flags | KEYEVENTF_KEYUP)
            for vk, scan, flags in reversed(pressed)
        ]
        cleanup = (INPUT * len(releases))(*releases)
        user32.SendInput(len(cleanup), cleanup, ctypes.sizeof(INPUT))
    log(f"SendInput partial sent={sent}/{len(batch)} error={error}")
    raise OSError(error, f"SendInput 仅发送了 {sent}/{len(batch)} 个按键事件。")


def send_key(user32, vk: int, scan: int, flags: int) -> None:
    send_events(user32, [
        keyboard_event(vk, scan, flags),
        keyboard_event(vk, scan, flags | KEYEVENTF_KEYUP),
    ])


def type_scancode_character(user32, character: str) -> None:
    scan, shifted = US_KEYMAP[character]
    events = []
    if shifted:
        events.append(keyboard_event(0, SCAN_LEFT_SHIFT, KEYEVENTF_SCANCODE))
    events.extend([
        keyboard_event(0, scan, KEYEVENTF_SCANCODE),
        keyboard_event(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
    ])
    if shifted:
        events.append(keyboard_event(0, SCAN_LEFT_SHIFT, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))
    send_events(user32, events)


def type_character(user32, character: str, mode: str = MODE_SCANCODE) -> None:
    validate_input_mode(character, mode)
    if mode == MODE_SCANCODE:
        type_scancode_character(user32, character)
        return
    if character == "\n":
        send_key(user32, VK_RETURN, 0, 0)
        return
    encoded = character.encode("utf-16-le")
    for offset in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[offset : offset + 2], "little")
        send_key(user32, 0, unit, KEYEVENTF_UNICODE)


INTEGRITY_UNTRUSTED = 0x0000
INTEGRITY_LOW = 0x1000
INTEGRITY_MEDIUM = 0x2000
INTEGRITY_HIGH = 0x3000
INTEGRITY_SYSTEM = 0x4000
INTEGRITY_NAMES = {
    INTEGRITY_UNTRUSTED: "Untrusted",
    INTEGRITY_LOW: "Low",
    INTEGRITY_MEDIUM: "Medium",
    INTEGRITY_HIGH: "High",
    INTEGRITY_SYSTEM: "System",
}
LOW_INTEGRITY_HINT = (
    "低完整性环境：Windows 会丢弃发往普通程序的按键，请把整个文件夹复制到桌面再运行。"
)


def process_integrity_level() -> int | None:
    """Return this process' integrity RID (0x1000 = Low, 0x2000 = Medium)."""
    if os.name != "nt":
        return None
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        return None

    token_query = 0x0008
    token_integrity_level = 25
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        return None
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token, token_integrity_level, None, 0, ctypes.byref(size)
        )
        if size.value == 0:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, token_integrity_level, buffer, size, ctypes.byref(size)
        ):
            return None
        sid = ctypes.c_void_p.from_buffer(buffer).value
        if not sid:
            return None
        count = advapi32.GetSidSubAuthorityCount(sid)[0]
        if not count:
            return None
        return int(advapi32.GetSidSubAuthority(sid, count - 1)[0])
    except (OSError, ValueError, AttributeError):
        return None
    finally:
        kernel32.CloseHandle(token)


def format_integrity(level: int | None) -> str:
    if level is None:
        return "unknown"
    return f"{INTEGRITY_NAMES.get(level, 'Other')}(0x{level:04X})"


class TypingStopped(Exception):
    pass


def prepare_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    return user32


VK_F9 = 0x78
APP_VERSION = "v1.3.2"


def drain_hotkeys(user32) -> None:
    """Clear stale "pressed since last poll" bits before a run starts."""
    for vk in (VK_F8, VK_F9):
        user32.GetAsyncKeyState(vk)


class KeyPoller:
    """Edge detection for one hotkey (low bit = pressed since the last poll)."""

    def __init__(self, vk: int) -> None:
        self.vk = vk
        self.last_down = False

    def pressed(self, user32) -> bool:
        state = user32.GetAsyncKeyState(self.vk)
        down = bool(state & 0x8000)
        edge = bool(state & 0x0001) or (down and not self.last_down)
        self.last_down = down
        if edge:
            log(f"hotkey 0x{self.vk:02X} raw=0x{state & 0xFFFF:04X}")
        return edge


class Hotkeys:
    """F8 toggles pause, F9 stops the whole run."""

    def __init__(self) -> None:
        self.pause = KeyPoller(VK_F8)
        self.stop = KeyPoller(VK_F9)


def window_pid(user32, hwnd) -> int:
    if not hwnd:
        return 0
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return process_id.value


def set_pause(pause_event: threading.Event, events: queue.Queue, paused: bool) -> None:
    if paused:
        pause_event.set()
    else:
        pause_event.clear()
    events.put(("paused", paused))


def check_hotkeys(user32, hotkeys, pause_event, events) -> None:
    """Raise on F9, toggle pause on F8."""
    if hotkeys.stop.pressed(user32):
        events.put(("status", "已按 F9 停止。", "normal"))
        raise TypingStopped("已按 F9 停止。")
    if not hotkeys.pause.pressed(user32):
        return
    paused = not pause_event.is_set()
    set_pause(pause_event, events, paused)
    events.put(
        (
            "status",
            "已暂停，按 F8 或点“继续”接着输入。" if paused else "继续输入。",
            "paused" if paused else "normal",
        )
    )


def target_in_front(user32, target_pid: int) -> bool:
    window = user32.GetForegroundWindow()
    return bool(window) and window_pid(user32, window) == target_pid


def wait_until_ready(user32, stop_event, pause_event, target_pid, events, hotkeys) -> None:
    """Block until typing may continue: not paused and the target app in front."""
    away_announced = False
    while True:
        if stop_event.is_set():
            raise TypingStopped("已停止。")
        check_hotkeys(user32, hotkeys, pause_event, events)
        if pause_event.is_set():
            away_announced = False
            time.sleep(0.05)
            continue
        if not target_in_front(user32, target_pid):
            if not away_announced:
                events.put(
                    ("status", "已切离目标窗口，自动暂停；切回后自动继续。", "paused")
                )
                away_announced = True
            time.sleep(0.05)
            continue
        if away_announced:
            events.put(("status", "继续输入。", "normal"))
        return


def wait_for_target_window(user32, stop_event, pause_event, events, hotkeys) -> tuple:
    """Wait until another process owns the foreground window, then lock onto it."""
    announced = False
    while True:
        if stop_event.is_set():
            raise TypingStopped("已停止。")
        check_hotkeys(user32, hotkeys, pause_event, events)
        if pause_event.is_set():
            time.sleep(0.05)
            continue
        window = user32.GetForegroundWindow()
        pid = window_pid(user32, window)
        if pid and pid != os.getpid():
            return window, pid
        if not announced:
            events.put(("status", "请切换到目标窗口，切过去后自动开始输入。", "normal"))
            announced = True
        time.sleep(0.05)


def type_content(
    user32,
    content: str,
    delay: float,
    stop_event: threading.Event,
    target_window,
    events: queue.Queue,
    mode: str = MODE_SCANCODE,
    pause_event: threading.Event | None = None,
    hotkeys: "Hotkeys | None" = None,
) -> None:
    # Reject unsupported text before sending even its first character.
    validate_input_mode(content, mode)
    if pause_event is None:
        pause_event = threading.Event()
    if hotkeys is None:
        hotkeys = Hotkeys()
    target_pid = window_pid(user32, target_window)
    log(
        f"type target_pid={target_pid} fg_pid={window_pid(user32, user32.GetForegroundWindow())}"
    )
    after_newline = False
    total = len(content)
    for index, character in enumerate(content, 1):
        wait_until_ready(user32, stop_event, pause_event, target_pid, events, hotkeys)
        if stop_event.is_set():
            raise TypingStopped(f"已停止，输入了 {index - 1}/{total} 个字符。")
        type_character(user32, character, mode)
        after_newline = character == "\n"
        if index % 100 == 0 or index == total:
            events.put(("progress", index, total))
        # Give the editor time to process Enter before the next character.
        wait_time = max(delay, 0.08) if after_newline else delay
        if wait_time and stop_event.wait(wait_time):
            raise TypingStopped(f"已停止，输入了 {index}/{total} 个字符。")


class App:
    BG = "#0C1523"
    PANEL = "#17253A"
    SEGMENT = "#152337"
    SEGMENT_ACTIVE = "#1F3A5C"
    SEGMENT_SELECTED = "#23456B"
    SEGMENT_HOVER = "#1B2C44"
    FIELD = "#0A121E"
    FIELD_FOCUS = "#12243B"
    LINE = "#1F2E45"
    TEXT = "#E9F2FF"
    MUTED = "#8CA3C2"
    DIM = "#61789A"
    ACCENT = "#4F9DFF"
    ACCENT_HOVER = "#6FB2FF"
    ACCENT_DEEP = "#1B3E66"
    PAUSE = "#F2B45C"
    PAUSE_DEEP = "#5A3F12"
    GREEN = "#5AD1AC"
    RED = "#FF8C99"
    FONT = "Microsoft YaHei UI"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("模拟输入小助手")
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.resizable(False, False)
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.running = False
        self.paused = False
        self.drop_enabled = False
        self.drop_hwnd = 0
        self.pending_drop: list[str] | None = None
        self.progress_ratio = 0.0
        self.integrity = process_integrity_level()
        self.low_integrity = self.integrity is not None and self.integrity < INTEGRITY_MEDIUM

        default_file = BASE_DIR / "test.txt"
        self.path_var = tk.StringVar(value=str(default_file) if default_file.is_file() else "")
        self.delay_var = tk.StringVar(value="0.01")
        self.countdown_var = tk.StringVar(value="2")
        self.mode_var = tk.StringVar(value=MODE_UNICODE)
        self.mode_hint_var = tk.StringVar()
        self.file_info_var = tk.StringVar(value="尚未选择文件")
        self.status_var = tk.StringVar(value="准备就绪。")
        self.progress_var = tk.StringVar(value="0%")

        shell = tk.Frame(root, bg=self.BG, padx=24, pady=18)
        shell.pack(fill="both", expand=True)

        head = tk.Frame(shell, bg=self.BG)
        head.pack(fill="x")
        self.draw_badge(head)
        titles = tk.Frame(head, bg=self.BG)
        titles.pack(side="left", padx=(10, 0))
        tk.Label(
            titles, text="模拟输入小助手", font=(self.FONT, 12, "bold"),
            fg=self.TEXT, bg=self.BG,
        ).pack(anchor="w")
        tk.Label(
            titles, text="拖拽文件到窗口 · 切到目标窗口 · 自动敲字", font=(self.FONT, 8),
            fg=self.DIM, bg=self.BG,
        ).pack(anchor="w")
        tk.Label(
            head, text=APP_VERSION, font=("Segoe UI", 8), fg=self.DIM, bg=self.BG,
        ).pack(side="right", anchor="n", pady=(5, 0))

        self.separator(shell).pack(fill="x", pady=(13, 12))

        file_row = tk.Frame(shell, bg=self.BG)
        file_row.pack(fill="x")
        self.file_box = tk.Frame(file_row, bg=self.FIELD)
        self.file_box.pack(side="left", fill="x", expand=True)
        self.path_entry = tk.Entry(
            self.file_box, textvariable=self.path_var, font=(self.FONT, 9),
            fg=self.TEXT, bg=self.FIELD, insertbackground=self.TEXT, relief="flat",
            bd=0, highlightthickness=0, disabledbackground=self.FIELD,
            disabledforeground=self.MUTED, selectbackground="#24507F",
            selectforeground=self.TEXT,
        )
        self.path_entry.pack(fill="x", padx=12, ipady=8)
        self.path_entry.bind("<FocusOut>", lambda _event: self.load_file_info())
        self.path_entry.bind("<Return>", lambda _event: self.load_file_info())
        self.path_entry.bind("<FocusIn>", lambda _event: self.focus_file(True))
        self.path_entry.bind("<FocusOut>", lambda _event: self.focus_file(False), add="+")
        self.browse_button = self.ghost_button(file_row, "浏览", self.browse)
        self.browse_button.pack(side="left", padx=(8, 0))
        self.file_info_label = tk.Label(
            shell, textvariable=self.file_info_var, font=(self.FONT, 8),
            fg=self.DIM, bg=self.BG, anchor="w",
        )
        self.file_info_label.pack(fill="x", pady=(6, 0))

        controls = tk.Frame(shell, bg=self.BG)
        controls.pack(fill="x", pady=(13, 0))
        self.delay_entry = self.inline_field(controls, "间隔", "秒", self.delay_var)
        self.countdown_entry = self.inline_field(controls, "倒计时", "秒", self.countdown_var)
        segment = tk.Frame(controls, bg=self.SEGMENT)
        segment.pack(side="right")
        self.mode_buttons = []
        self.mode_choices = []
        for title, mode in (("VM 控制台", MODE_SCANCODE), ("本机文本", MODE_UNICODE)):
            button = tk.Button(
                segment, text=title, command=lambda value=mode: self.choose_mode(value),
                font=(self.FONT, 9), bd=0, relief="flat", padx=13, pady=6, cursor="hand2",
                highlightthickness=0, bg=self.SEGMENT, fg=self.MUTED,
                activebackground=self.SEGMENT_ACTIVE, activeforeground=self.TEXT,
                disabledforeground=self.DIM,
            )
            button.bind("<Enter>", lambda _event, b=button, m=mode: self.hover_mode(b, m, True))
            button.bind("<Leave>", lambda _event, b=button, m=mode: self.hover_mode(b, m, False))
            button.pack(side="left")
            self.mode_buttons.append(button)
            self.mode_choices.append(mode)
        self.mode_hint_label = tk.Label(
            shell, textvariable=self.mode_hint_var, font=(self.FONT, 8),
            fg=self.DIM, bg=self.BG, anchor="w", justify="left", wraplength=430,
        )
        self.mode_hint_label.pack(fill="x", pady=(9, 0))
        self.update_mode_hint()

        self.separator(shell).pack(fill="x", pady=(13, 12))

        action_row = tk.Frame(shell, bg=self.BG)
        action_row.pack(fill="x")
        self.start_button = self.primary_button(action_row, "开始输入", self.on_primary)
        self.start_button.pack(side="left")
        self.stop_button = self.ghost_button(action_row, "停止", self.stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.stop_button.configure(state="disabled")
        tk.Label(
            action_row, text="F8 暂停/继续 · F9 停止 · 切窗自动暂停", font=(self.FONT, 8),
            fg=self.DIM, bg=self.BG,
        ).pack(side="right")

        progress_row = tk.Frame(shell, bg=self.BG)
        progress_row.pack(fill="x", pady=(14, 0))
        self.progress_canvas = tk.Canvas(
            progress_row, height=4, bg=self.LINE, highlightthickness=0, bd=0,
        )
        self.progress_canvas.pack(side="left", fill="x", expand=True, pady=(6, 0))
        self.progress_canvas.bind("<Configure>", lambda _event: self.draw_progress())
        tk.Label(
            progress_row, textvariable=self.progress_var, font=("Segoe UI", 8, "bold"),
            fg=self.MUTED, bg=self.BG, width=5, anchor="e",
        ).pack(side="right", padx=(10, 0))

        self.status_label = tk.Label(
            shell, textvariable=self.status_var, font=(self.FONT, 8),
            fg=self.DIM, bg=self.BG, anchor="w", justify="left", wraplength=460,
        )
        self.status_label.pack(fill="x", pady=(9, 0))

        self.root.after(100, self.poll_events)
        self.root.after(200, self.enable_file_drop)
        if default_file.is_file():
            self.load_file_info()
        if self.low_integrity:
            self.apply_low_integrity_state()

    @staticmethod
    def rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, fill, outline="", width=1):
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return canvas.create_polygon(
            points, smooth=True, splinesteps=16, fill=fill, outline=outline, width=width
        )

    @staticmethod
    def bind_hover(button: tk.Button, idle: str, hover: str) -> None:
        def on_enter(_event):
            if str(button.cget("state")) != "disabled":
                button.configure(bg=hover)

        def on_leave(_event):
            if str(button.cget("state")) != "disabled":
                button.configure(bg=idle)

        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", on_leave)

    def draw_badge(self, parent: tk.Widget) -> None:
        canvas = tk.Canvas(parent, width=32, height=32, bg=self.BG, highlightthickness=0, bd=0)
        canvas.pack(side="left")
        self.rounded_rect(canvas, 1, 1, 31, 31, 9, self.ACCENT_DEEP)
        for x in (8, 15, 22):
            canvas.create_rectangle(x, 10, x + 4, 14, fill="#9ECBFF", outline="")
        for x in (11.5, 18.5):
            canvas.create_rectangle(x, 17, x + 4, 21, fill="#9ECBFF", outline="")
        canvas.create_line(10, 25, 22, 25, fill="#9ECBFF", width=2)

    def focus_file(self, active: bool) -> None:
        color = self.FIELD_FOCUS if active else self.FIELD
        self.file_box.configure(bg=color)
        self.path_entry.configure(bg=color)

    def separator(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=self.LINE, height=1)

    def inline_field(self, parent: tk.Widget, title: str, unit: str, variable: tk.StringVar) -> tk.Entry:
        cell = tk.Frame(parent, bg=self.BG)
        cell.pack(side="left", padx=(0, 14))
        tk.Label(
            cell, text=title, font=(self.FONT, 8), fg=self.MUTED, bg=self.BG,
        ).pack(side="left", padx=(0, 7))
        pill = tk.Frame(cell, bg=self.FIELD)
        pill.pack(side="left")
        entry = tk.Entry(
            pill, width=4, textvariable=variable, font=("Segoe UI", 10), fg=self.TEXT,
            bg=self.FIELD, relief="flat", bd=0, highlightthickness=0, justify="center",
            insertbackground=self.TEXT, disabledbackground=self.FIELD,
            disabledforeground=self.MUTED, selectbackground="#24507F",
            selectforeground=self.TEXT,
        )
        entry.pack(side="left", padx=(9, 3), ipady=5)
        tk.Label(
            pill, text=unit, font=(self.FONT, 8), fg=self.DIM, bg=self.FIELD,
        ).pack(side="left", padx=(0, 9))
        return entry

    def primary_button(self, parent: tk.Widget, text: str, command) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command, font=(self.FONT, 10, "bold"),
            bg=self.ACCENT, fg="#06121F", activebackground=self.ACCENT_HOVER,
            activeforeground="#06121F", relief="flat", bd=0, padx=24, pady=8,
            cursor="hand2", highlightthickness=0, disabledforeground="#7C93AF",
        )
        self.bind_hover(button, self.ACCENT, self.ACCENT_HOVER)
        return button

    def ghost_button(self, parent: tk.Widget, text: str, command) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command, font=(self.FONT, 9), bg=self.PANEL,
            fg=self.TEXT, activebackground=self.SEGMENT_ACTIVE, activeforeground=self.TEXT,
            relief="flat", bd=0, padx=15, pady=7, cursor="hand2",
            highlightthickness=0, disabledforeground=self.DIM,
        )
        self.bind_hover(button, self.PANEL, self.SEGMENT_ACTIVE)
        return button

    def choose_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        self.update_mode_hint()

    def hover_mode(self, button: tk.Button, mode: str, inside: bool) -> None:
        if str(button.cget("state")) == "disabled":
            return
        if mode == self.mode_var.get():
            button.configure(bg=self.SEGMENT_SELECTED)
            return
        button.configure(bg=self.SEGMENT_HOVER if inside else self.SEGMENT)

    def refresh_mode_buttons(self) -> None:
        current = self.mode_var.get()
        for mode, button in zip(self.mode_choices, self.mode_buttons):
            active = mode == current
            button.configure(
                bg=self.SEGMENT_SELECTED if active else self.SEGMENT,
                fg="#DCEBFF" if active else self.MUTED,
            )

    def set_status(self, message: str, tone: str = "normal") -> None:
        self.status_var.set(message)
        colors = {
            "normal": self.MUTED,
            "success": self.GREEN,
            "error": self.RED,
            "paused": self.PAUSE,
        }
        self.status_label.configure(fg=colors.get(tone, self.MUTED))

    def update_mode_hint(self) -> None:
        if self.mode_var.get() == MODE_SCANCODE:
            hint = "仅支持英文、数字、半角符号、Tab、换行；VM 内请用美式键盘并关闭 Caps Lock。"
        else:
            hint = "支持中文；往 VM 控制台输入时请切到 VM 模式。"
        self.mode_hint_var.set(hint)
        self.refresh_mode_buttons()

    def draw_progress(self) -> None:
        self.progress_canvas.delete("fill")
        width = self.progress_canvas.winfo_width()
        if width > 1 and self.progress_ratio > 0:
            self.progress_canvas.create_rectangle(
                0, 0, round(width * self.progress_ratio), 4,
                fill=self.ACCENT, outline="", tags="fill",
            )

    def browse(self) -> None:
        current_path = self.path_var.get().strip().strip('"')
        selected = filedialog.askopenfilename(
            title="选择要输入的文件",
            initialdir=str(Path(current_path).parent if current_path else Path.home()),
            filetypes=[("代码文件", "*.py *.js *.ts *.json *.md *.txt"), ("所有文件", "*.*")],
        )
        if selected:
            self.path_var.set(selected)
            self.load_file_info()

    def read_content(self) -> str:
        raw_path = self.path_var.get().strip().strip('"')
        if not raw_path:
            raise ValueError("请先选择输入文件。")
        path = Path(raw_path)
        if not path.is_file():
            raise ValueError(f"文件不存在：{path}")
        content = path.read_text(encoding="utf-8-sig")
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if not content:
            raise ValueError("文件是空的。")
        if "\x00" in content:
            raise ValueError("文件包含 NUL 字符，不能作为文本输入。")
        return content

    def load_file_info(self) -> None:
        if self.running:
            return
        try:
            content = self.read_content()
        except (OSError, UnicodeError, ValueError) as exc:
            self.file_info_var.set("无法读取文件，请重新选择")
            self.file_info_label.configure(fg=self.RED)
            self.set_status(str(exc), "error")
            return
        name = Path(self.path_var.get().strip().strip('"')).name
        self.file_info_var.set(f"已加载 · {name} · {len(content):,} 个字符")
        self.file_info_label.configure(fg=self.MUTED)
        self.set_status("准备就绪。")

    def set_running(self, running: bool) -> None:
        self.running = running
        state = "disabled" if running else "normal"
        for widget in (
            self.path_entry, self.browse_button, self.delay_entry,
            self.countdown_entry, *self.mode_buttons,
        ):
            widget.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")
        if not running:
            self.pause_event.clear()
            self.paused = False
            self.refresh_mode_buttons()
        self.refresh_primary()

    def refresh_primary(self) -> None:
        if self.low_integrity:
            self.start_button.configure(
                text="无法输入",
                state="disabled",
                bg=self.SEGMENT,
                fg=self.RED,
                disabledforeground=self.RED,
            )
            return
        if not self.running:
            text, background = "开始输入", self.ACCENT
        elif self.paused:
            text, background = "继续", self.PAUSE_DEEP
        else:
            text, background = "暂停", self.SEGMENT_ACTIVE
        self.start_button.configure(text=text, bg=background, fg=self.TEXT)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.refresh_primary()

    def on_primary(self) -> None:
        if self.running:
            self.toggle_pause()
        else:
            self.start()

    def stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.pause_event.clear()
        self.paused = False
        self.refresh_primary()
        self.set_status("正在停止…", "normal")

    def toggle_pause(self) -> None:
        if not self.running:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.set_paused(False)
            self.set_status("继续输入；请切回目标窗口。")
            return
        self.pause_event.set()
        self.set_paused(True)
        self.set_status("已暂停。按 F8 或点“继续”接着输入。", "paused")

    def start(self) -> None:
        if self.running:
            return
        try:
            content = self.read_content()
            mode = self.mode_var.get()
            validate_input_mode(content, mode)
            delay = float(self.delay_var.get())
            countdown = float(self.countdown_var.get())
            if not (0 <= delay <= 10 and 0 <= countdown <= 120):
                raise ValueError("字符间隔需在 0–10 秒，倒计时需在 0–120 秒。")
        except (OSError, UnicodeError, ValueError) as exc:
            self.set_status(str(exc), "error")
            return
        if os.name != "nt":
            self.set_status("此工具只支持 Windows。", "error")
            return
        if self.low_integrity:
            self.apply_low_integrity_state()
            return

        self.stop_event.clear()
        self.pause_event.clear()
        drain_hotkeys(ctypes.windll.user32)
        log(
            f"start file={self.path_var.get()!r} mode={self.mode_var.get()} "
            f"delay={self.delay_var.get()} countdown={self.countdown_var.get()} "
            f"chars={len(content)}"
        )
        self.set_running(True)
        self.set_paused(False)
        self.progress_ratio = 0.0
        self.progress_var.set("0%")
        self.draw_progress()
        self.set_status("即将开始，请切换到目标编辑器。")
        worker = threading.Thread(
            target=self.run_worker,
            args=(content, delay, countdown, mode),
            daemon=True,
        )
        worker.start()

    def apply_low_integrity_state(self) -> None:
        """Show the Low-integrity error inline and refuse to type; never a dialog."""
        log(f"low integrity {format_integrity(self.integrity)}: typing disabled")
        self.set_status(LOW_INTEGRITY_HINT, "error")
        self.start_button.configure(
            text="无法输入",
            state="disabled",
            bg=self.SEGMENT,
            fg=self.RED,
            disabledforeground=self.RED,
        )

    def close(self) -> None:
        self.stop_event.set()
        self.pause_event.clear()
        self.disable_file_drop()
        self.root.destroy()

    def enable_file_drop(self) -> None:
        """Accept files dragged from Explorer via WM_DROPFILES."""
        if os.name != "nt" or self.drop_enabled:
            return
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            hwnd = user32.GetAncestor(self.root.winfo_id(), GA_ROOT)
            if not hwnd:
                return
            prototype = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )

            def wndproc(h_wnd, message, w_param, l_param):
                if message == WM_DROPFILES:
                    self.handle_drop(shell32, w_param)
                    return 0
                return user32.CallWindowProcW(
                    self.prev_wndproc, h_wnd, message, w_param, l_param
                )

            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.CallWindowProcW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_void_p,
            ]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            shell32.DragQueryFileW.argtypes = [
                wintypes.HANDLE,
                wintypes.UINT,
                wintypes.LPWSTR,
                wintypes.UINT,
            ]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragFinish.argtypes = [wintypes.HANDLE]
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]

            self.wndproc_ref = prototype(wndproc)
            self.wndproc_user32 = user32
            self.drop_hwnd = hwnd
            self.prev_wndproc = user32.SetWindowLongPtrW(
                hwnd, GWLP_WNDPROC, self.wndproc_ref
            )
            shell32.DragAcceptFiles(hwnd, True)
            self.drop_enabled = bool(self.prev_wndproc)
        except (OSError, AttributeError, ctypes.ArgumentError):
            self.drop_enabled = False

    def disable_file_drop(self) -> None:
        if not self.drop_enabled:
            return
        try:
            self.wndproc_user32.SetWindowLongPtrW(
                self.drop_hwnd, GWLP_WNDPROC, self.prev_wndproc
            )
        except (OSError, AttributeError):
            pass
        self.drop_enabled = False

    def handle_drop(self, shell32, hdrop) -> None:
        # Runs inside the window procedure, so only ctypes calls are allowed here;
        # Tk work is deferred to poll_events.
        paths: list[str] = []
        try:
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            for index in range(count):
                length = shell32.DragQueryFileW(hdrop, index, None, 0)
                buffer = ctypes.create_unicode_buffer(length + 1)
                shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
                paths.append(buffer.value)
        finally:
            shell32.DragFinish(hdrop)
        self.pending_drop = paths

    def apply_dropped_files(self, paths: list[str]) -> None:
        if self.running:
            self.set_status("正在输入，先暂停或等本次结束再换文件。", "error")
            return
        files = [item for item in paths if Path(item).is_file()]
        if not files:
            self.set_status("拖进来的是文件夹，请拖入具体文件。", "error")
            return
        self.path_var.set(files[0])
        self.load_file_info()
        if len(files) > 1:
            self.set_status(
                f"拖入 {len(files)} 个文件，已载入第一个：{Path(files[0]).name}", "success"
            )

    def run_worker(self, content: str, delay: float, countdown: float, mode: str) -> None:
        try:
            user32 = prepare_user32()
            deadline = time.monotonic() + countdown
            hotkeys = Hotkeys()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                check_hotkeys(user32, hotkeys, self.pause_event, self.events)
                if self.stop_event.is_set():
                    raise TypingStopped("已停止。")
                if self.pause_event.is_set():
                    frozen = time.monotonic()
                    while self.pause_event.is_set():
                        if self.stop_event.is_set():
                            raise TypingStopped("已停止。")
                        check_hotkeys(user32, hotkeys, self.pause_event, self.events)
                        time.sleep(0.05)
                    deadline += time.monotonic() - frozen
                    continue
                self.events.put(
                    ("status", f"请切换到目标编辑器，约 {remaining:.1f} 秒后开始。", "normal")
                )
                time.sleep(min(0.1, remaining))

            target_window, target_pid = wait_for_target_window(
                user32, self.stop_event, self.pause_event, self.events, hotkeys
            )
            log(f"target hwnd={target_window} pid={target_pid} self={os.getpid()}")

            self.events.put(
                ("status", "正在输入；F8 暂停/继续，F9 停止，切走窗口会自动暂停。", "normal")
            )
            type_content(
                user32,
                content,
                delay,
                self.stop_event,
                target_window,
                self.events,
                mode,
                pause_event=self.pause_event,
                hotkeys=hotkeys,
            )
            self.events.put(
                ("status", f"发送完成：{len(content)} 个字符，请核对目标窗口。", "success")
            )
            log(f"typed {len(content)} chars")
        except TypingStopped as exc:
            log(f"stopped: {exc}")
            self.events.put(("status", str(exc), "normal"))
        except (OSError, UnicodeError, ValueError) as exc:
            log(f"error: {exc!r}")
            self.events.put(
                (
                    "status",
                    f"输入失败：{exc} 若目标窗口以管理员身份运行，请同样以管理员身份运行本工具。",
                    "error",
                )
            )
        finally:
            self.events.put(("done",))

    def poll_events(self) -> None:
        if self.pending_drop is not None:
            paths, self.pending_drop = self.pending_drop, None
            self.apply_dropped_files(paths)
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "status":
                    self.set_status(event[1], event[2])
                elif event[0] == "progress":
                    self.progress_ratio = event[1] / event[2]
                    self.progress_var.set(f"{self.progress_ratio:.0%}")
                    self.draw_progress()
                elif event[0] == "paused":
                    self.set_paused(event[1])
                elif event[0] == "done":
                    self.set_running(False)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)


if __name__ == "__main__":
    log(f"app start {APP_VERSION} integrity={format_integrity(process_integrity_level())}")
    root = tk.Tk()
    app = App(root)
    try:
        root.mainloop()
    finally:
        app.disable_file_drop()
