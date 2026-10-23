# -*- coding: utf-8 -*-
import ctypes
import queue
import threading
import time
import tkinter as tk
import winreg
from ctypes import wintypes
from datetime import datetime
from tkinter import messagebox, ttk

try:
    import pystray
    from PIL import Image, ImageDraw

    TRAY_AVAILABLE = True
except Exception:
    pystray = None
    Image = None
    ImageDraw = None
    TRAY_AVAILABLE = False


HIDE_CHAR = "\u200b"
WINDOW_ALPHA = 0.15
WINDOW_GEOMETRY = "360x84+24+700"
DEBUG_LOG = False
REGISTRY_PATH = r"Software\PrivateInput"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004

ACTION_TOGGLE_TEXT = "toggle_text"
ACTION_TOGGLE_WINDOW = "toggle_window"
ACTION_TOGGLE_THEME = "toggle_theme"
ACTION_ALPHA_UP = "alpha_up"
ACTION_ALPHA_DOWN = "alpha_down"

ACTION_ORDER = (
    ACTION_TOGGLE_TEXT,
    ACTION_TOGGLE_WINDOW,
    ACTION_TOGGLE_THEME,
    ACTION_ALPHA_UP,
    ACTION_ALPHA_DOWN,
)

CONTINUOUS_ACTIONS = {ACTION_ALPHA_UP, ACTION_ALPHA_DOWN}
ALPHA_STEP = 0.02
ALPHA_REPEAT_INTERVAL_SEC = 0.06

ACTION_LABEL = {
    ACTION_TOGGLE_TEXT: "显/隐文字",
    ACTION_TOGGLE_WINDOW: "显/隐窗口",
    ACTION_TOGGLE_THEME: "底色换色",
    ACTION_ALPHA_UP: "透明度增加",
    ACTION_ALPHA_DOWN: "透明度减少",
}

DEFAULT_HOTKEYS = {
    ACTION_TOGGLE_TEXT: "Ctrl+Alt+2",
    ACTION_TOGGLE_WINDOW: "Ctrl+Alt+8",
    ACTION_TOGGLE_THEME: "Ctrl+Alt+0",
    ACTION_ALPHA_UP: "Ctrl+Alt+9",
    ACTION_ALPHA_DOWN: "Ctrl+Alt+7",
}

DEFAULT_PREFS = {
    "startup_hidden": False,
    "window_alpha": WINDOW_ALPHA,
    "auto_hide_ms": 0,
    "use_long_press": False,
    "long_press_ms": 400,
}

SPECIAL_KEY_VK = {
    "SPACE": 0x20,
    "TAB": 0x09,
    "ESC": 0x1B,
    "ENTER": 0x0D,
    "PGUP": 0x21,
    "PGDN": 0x22,
    "HOME": 0x24,
    "END": 0x23,
    "DEL": 0x2E,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "-": 0xBD,
    "=": 0xBB,
    "VOLUME_UP": 0xAF,
    "VOLUME_DOWN": 0xAE,
    "VOLUME_MUTE": 0xAD,
}

KEYSYM_TO_TOKEN = {
    "Escape": "ESC",
    "Return": "ENTER",
    "Tab": "TAB",
    "space": "SPACE",
    "Prior": "PGUP",
    "Next": "PGDN",
    "Home": "HOME",
    "End": "END",
    "Delete": "DEL",
    "Page_Up": "PGUP",
    "Page_Down": "PGDN",
    "Up": "UP",
    "Down": "DOWN",
    "Left": "LEFT",
    "Right": "RIGHT",
    "minus": "-",
    "equal": "=",
    "AudioRaiseVolume": "VOLUME_UP",
    "AudioLowerVolume": "VOLUME_DOWN",
    "AudioMute": "VOLUME_MUTE",
    "XF86AudioRaiseVolume": "VOLUME_UP",
    "XF86AudioLowerVolume": "VOLUME_DOWN",
    "XF86AudioMute": "VOLUME_MUTE",
}

KEYCODE_TO_TOKEN = {
    0xAF: "VOLUME_UP",
    0xAE: "VOLUME_DOWN",
    0xAD: "VOLUME_MUTE",
}

IGNORED_KEYSYM = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"}

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

user32 = ctypes.WinDLL("user32", use_last_error=True)


def debug_log(message: str):
    if DEBUG_LOG:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [debug] {message}", flush=True)


def key_token_to_vk(token: str):
    if len(token) == 1 and token.isdigit():
        return ord(token)
    if len(token) == 1 and token.isalpha():
        return ord(token.upper())
    if token.startswith("F") and token[1:].isdigit():
        n = int(token[1:])
        if 1 <= n <= 24:
            return 0x70 + (n - 1)
    return SPECIAL_KEY_VK.get(token)


def build_hotkey_text(mods: int, key_token: str):
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    parts.append(key_token)
    return "+".join(parts)


def parse_hotkey_text(text: str):
    raw_parts = [p.strip() for p in text.split("+") if p.strip()]
    if not raw_parts:
        raise ValueError("空热键")

    mods = 0
    key_token = None
    for part in raw_parts:
        lower = part.lower()
        if lower in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif lower == "alt":
            mods |= MOD_ALT
        elif lower == "shift":
            mods |= MOD_SHIFT
        else:
            if key_token is not None:
                raise ValueError("只能设置一个主键")
            key_token = part.upper()

    if key_token is None:
        raise ValueError("缺少主键")

    vk = key_token_to_vk(key_token)
    if vk is None:
        raise ValueError("不支持的按键")

    canonical = build_hotkey_text(mods, key_token)
    return mods, vk, canonical


def key_event_to_token(event: tk.Event):
    keysym = event.keysym
    if keysym in IGNORED_KEYSYM:
        return None
    if keysym in KEYSYM_TO_TOKEN:
        return KEYSYM_TO_TOKEN[keysym]
    keycode = getattr(event, "keycode", None)
    if keycode in KEYCODE_TO_TOKEN:
        return KEYCODE_TO_TOKEN[keycode]
    if len(keysym) == 1:
        ch = keysym.upper()
        if ch.isalpha() or ch.isdigit():
            return ch
    if keysym.startswith("F") and keysym[1:].isdigit():
        return keysym.upper()
    return None


def mods_from_event_state(state: int):
    mods = 0
    if state & 0x0004:
        mods |= MOD_CONTROL
    if state & 0x0008:
        mods |= MOD_ALT
    if state & 0x0001:
        mods |= MOD_SHIFT
    return mods


def mods_from_keyboard_state():
    mods = 0
    if is_vk_down(VK_CONTROL) or is_vk_down(VK_LCONTROL) or is_vk_down(VK_RCONTROL):
        mods |= MOD_CONTROL
    if is_vk_down(VK_MENU) or is_vk_down(VK_LMENU) or is_vk_down(VK_RMENU):
        mods |= MOD_ALT
    if is_vk_down(VK_SHIFT) or is_vk_down(VK_LSHIFT) or is_vk_down(VK_RSHIFT):
        mods |= MOD_SHIFT
    return mods


def is_vk_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def is_mods_down(mods: int) -> bool:
    if mods & MOD_CONTROL and not (is_vk_down(VK_CONTROL) or is_vk_down(VK_LCONTROL) or is_vk_down(VK_RCONTROL)):
        return False
    if mods & MOD_ALT and not (is_vk_down(VK_MENU) or is_vk_down(VK_LMENU) or is_vk_down(VK_RMENU)):
        return False
    if mods & MOD_SHIFT and not (is_vk_down(VK_SHIFT) or is_vk_down(VK_LSHIFT) or is_vk_down(VK_RSHIFT)):
        return False
    return True


class HotkeyWorker:
    def __init__(self, event_queue):
        self.event_queue = event_queue
        self.stop_event = threading.Event()
        self.thread = None

    def start(self, parsed_hotkeys, use_long_press: bool, long_press_ms: int):
        self.thread = threading.Thread(target=self._run, args=(parsed_hotkeys, use_long_press, long_press_ms), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.0)

    def _run(self, parsed_hotkeys, use_long_press: bool, long_press_ms: int):
        states = {action: {"down_since": None, "fired": False, "next_repeat_at": 0.0} for action in parsed_hotkeys}
        self.event_queue.put(("worker_info", f"热键监听已启用（长按={'开' if use_long_press else '关'}）"))

        while not self.stop_event.is_set():
            now = time.monotonic()
            for action, (mods, vk, _text) in parsed_hotkeys.items():
                combo_down = is_mods_down(mods) and is_vk_down(vk)
                st = states[action]

                if combo_down:
                    if st["down_since"] is None:
                        st["down_since"] = now
                        st["fired"] = False

                    if not st["fired"]:
                        if use_long_press:
                            if int((now - st["down_since"]) * 1000) >= long_press_ms:
                                self.event_queue.put(("hotkey_action", action))
                                st["fired"] = True
                                st["next_repeat_at"] = now + ALPHA_REPEAT_INTERVAL_SEC
                        else:
                            self.event_queue.put(("hotkey_action", action))
                            st["fired"] = True
                            st["next_repeat_at"] = now + ALPHA_REPEAT_INTERVAL_SEC
                    elif action in CONTINUOUS_ACTIONS and now >= st["next_repeat_at"]:
                        self.event_queue.put(("hotkey_action", action))
                        st["next_repeat_at"] = now + ALPHA_REPEAT_INTERVAL_SEC
                else:
                    st["down_since"] = None
                    st["fired"] = False
                    st["next_repeat_at"] = 0.0

            time.sleep(0.02)


class PrivateStealthInputV2:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.visible_text = False
        self.real_text = ""
        self.hidden_select_all = False
        self.hide_job = None
        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self.is_dark = False

        self.hotkeys, self.prefs = self._load_config()
        self.startup_hidden = bool(self.prefs["startup_hidden"])
        self.window_alpha = float(self.prefs["window_alpha"])
        self.auto_hide_ms = int(self.prefs["auto_hide_ms"])
        self.use_long_press = bool(self.prefs["use_long_press"])
        self.long_press_ms = int(self.prefs["long_press_ms"])

        self.hotkey_events = queue.Queue()
        self.hotkey_worker = None

        self.settings_window = None
        self.settings_vars = {}
        self.settings_edit_hotkeys = {}
        self.settings_pref_vars = {}
        self.capture_action = None
        self.settings_status_var = None

        self.tray_icon = None
        self.tray_thread = None
        self.status_var = None
        self.alpha_save_job = None

        self._setup_ui()
        self._apply_theme(dark=False)
        self._setup_tray()
        self._register_hotkeys()
        self._poll_hotkey_messages()
        if self.startup_hidden:
            self.root.after(10, self.quick_hide)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _setup_ui(self):
        self.root.title("Private Input")
        self.root.geometry(WINDOW_GEOMETRY)
        self.root.resizable(False, False)

        self.root.attributes("-topmost", False)
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", self.window_alpha)
        self.root.wm_attributes("-toolwindow", True)

        self.frame = tk.Frame(self.root, padx=3, pady=3, bd=0, highlightthickness=0)
        self.frame.pack(fill="both", expand=True)

        self.top_bar = tk.Frame(self.frame, height=16, bd=0, highlightthickness=0)
        self.top_bar.pack(fill="x", padx=1, pady=(0, 2))
        self.drag_area = tk.Frame(self.top_bar, bd=0, highlightthickness=0)
        self.drag_area.pack(side="left", fill="x", expand=True)

        self.btn_settings = tk.Button(self.top_bar, text="设", width=2, command=self.open_settings_window)
        self.btn_min = tk.Button(self.top_bar, text="-", width=2, command=self.minimize_window)
        self.btn_close = tk.Button(self.top_bar, text="x", width=2, command=self.on_close)
        self.btn_close.pack(side="right", padx=(0, 2))
        self.btn_min.pack(side="right", padx=(0, 2))
        self.btn_settings.pack(side="right", padx=(0, 2))

        self._bind_drag(self.top_bar, left_button=True)
        self._bind_drag(self.drag_area, left_button=True)

        self.entry = tk.Entry(self.frame, width=60, relief="flat", bd=0, highlightthickness=0)
        self.entry.pack(fill="x", expand=True, padx=1, pady=(0, 2))

        self.ctrl = tk.Frame(self.frame, bd=0, highlightthickness=0)
        self.ctrl.pack(fill="x", padx=1)
        self.btn_theme = tk.Button(self.ctrl, text="白/黑", command=self.toggle_theme)
        self.btn_toggle = tk.Button(self.ctrl, text="显/隐", command=self.toggle_text_visibility)

        for btn in (self.btn_theme, self.btn_toggle):
            btn.configure(font=("Segoe UI", 8), relief="flat", bd=0, padx=6, pady=1, highlightthickness=0)
            btn.pack(side="left", padx=(0, 3))

        for btn in (self.btn_settings, self.btn_min, self.btn_close):
            btn.configure(font=("Segoe UI", 8), relief="flat", bd=0, padx=3, pady=0, highlightthickness=0, takefocus=0)

        self._bind_drag(self.ctrl, left_button=True)
        self._bind_drag(self.entry, left_button=False)

        self.entry.configure(show=HIDE_CHAR, insertwidth=0)
        self.entry.bind("<Control-a>", lambda e: self.select_all())
        self.entry.bind("<KeyPress>", self._on_entry_keypress, add="+")
        self.entry.bind("<KeyRelease>", self._on_entry_keyrelease, add="+")
        self.entry.bind("<<Paste>>", self._on_entry_paste, add="+")
        self.entry.bind("<<Copy>>", self._on_entry_copy, add="+")
        self.entry.bind("<<Cut>>", self._on_entry_cut, add="+")
        self.entry.bind("<Button-3>", self._on_right_click)

        self.copy_menu = tk.Menu(self.root, tearoff=0)
        self.copy_menu.add_command(label="复制", command=self._copy_current_selection)
        self.copy_menu.add_command(label="粘贴", command=self._paste_from_clipboard)
        self.copy_menu.add_command(label="剪切", command=self._cut_current_selection)
        self.copy_menu.add_separator()
        self.copy_menu.add_command(label="全选", command=self.select_all)

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")

    def _set_clipboard_text(self, text: str):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
        except Exception:
            pass

    def _get_clipboard_text(self):
        try:
            return self.root.clipboard_get()
        except Exception:
            return ""

    def _sync_real_text_from_entry(self):
        if self.visible_text:
            self.real_text = self._normalize_text(self.entry.get())

    def _on_entry_keyrelease(self, _event: tk.Event):
        if self.visible_text:
            self._sync_real_text_from_entry()

    def _copy_current_selection(self, _event=None):
        if self.visible_text:
            try:
                selected = self.entry.selection_get()
            except Exception:
                selected = ""
            if selected:
                self._set_clipboard_text(selected)
            return "break"
        if self.hidden_select_all and self.real_text:
            self._set_clipboard_text(self.real_text)
        return "break"

    def _paste_from_clipboard(self, _event=None):
        pasted = self._normalize_text(self._get_clipboard_text())
        if not pasted:
            return "break"
        if self.visible_text:
            try:
                self.entry.delete("sel.first", "sel.last")
            except Exception:
                pass
            self.entry.insert("insert", pasted)
            self._sync_real_text_from_entry()
            return "break"
        if self.hidden_select_all:
            self.real_text = ""
            self.hidden_select_all = False
        self.real_text += pasted
        return "break"

    def _cut_current_selection(self, _event=None):
        if self.visible_text:
            try:
                selected = self.entry.selection_get()
            except Exception:
                selected = ""
            if selected:
                self._set_clipboard_text(selected)
                self.entry.delete("sel.first", "sel.last")
                self._sync_real_text_from_entry()
            return "break"
        if self.hidden_select_all and self.real_text:
            self._set_clipboard_text(self.real_text)
            self.real_text = ""
        self.hidden_select_all = False
        return "break"

    def _on_entry_paste(self, _event=None):
        return self._paste_from_clipboard()

    def _on_entry_copy(self, _event=None):
        return self._copy_current_selection()

    def _on_entry_cut(self, _event=None):
        return self._cut_current_selection()

    def _on_entry_keypress(self, event: tk.Event):
        if self.visible_text:
            self.hidden_select_all = False
            return

        ctrl = bool(event.state & 0x0004)
        alt = bool(event.state & 0x0008)
        if ctrl and not alt:
            key = event.keysym.lower()
            if key == "a":
                return self.select_all()
            if key == "c":
                return self._copy_current_selection()
            if key == "x":
                return self._cut_current_selection()
            if key == "v":
                return self._paste_from_clipboard()

        if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"):
            return "break"

        if event.keysym in ("BackSpace", "Delete"):
            if self.hidden_select_all:
                self.real_text = ""
            elif self.real_text:
                self.real_text = self.real_text[:-1]
            self.hidden_select_all = False
            return "break"

        if event.char:
            ch = event.char
            if ch >= " ":
                if self.hidden_select_all:
                    self.real_text = ""
                self.real_text += ch
                self.hidden_select_all = False
            return "break"

        return "break"

    def _apply_theme(self, dark: bool):
        if dark:
            frame_bg, panel_bg, drag_bg = "#111111", "#141414", "#2b2b2b"
            entry_bg, entry_fg = "#101010", "#e8e8e8"
            btn_bg, btn_fg = "#161616", "#c8c8c8"
            btn_active_bg, btn_active_fg = "#212121", "#ffffff"
        else:
            frame_bg, panel_bg, drag_bg = "#efefef", "#f4f4f4", "#a6a6a6"
            entry_bg, entry_fg = "#ffffff", "#222222"
            btn_bg, btn_fg = "#f0f0f0", "#666666"
            btn_active_bg, btn_active_fg = "#e5e5e5", "#444444"

        self.frame.configure(bg=frame_bg)
        self.top_bar.configure(bg=frame_bg)
        self.drag_area.configure(bg=drag_bg)
        self.ctrl.configure(bg=panel_bg)
        self.entry.configure(bg=entry_bg, fg=entry_fg, insertbackground=entry_fg, disabledbackground=entry_bg, disabledforeground=entry_fg)

        for btn in (self.btn_theme, self.btn_toggle):
            btn.configure(bg=btn_bg, fg=btn_fg, activebackground=btn_active_bg, activeforeground=btn_active_fg)
        for btn in (self.btn_settings, self.btn_min):
            btn.configure(bg=frame_bg, fg=btn_fg, activebackground=btn_active_bg, activeforeground=btn_active_fg)
        self.btn_close.configure(bg=frame_bg, fg=btn_fg, activebackground="#bb3b3b" if dark else "#e07b7b", activeforeground="#ffffff")
        self.is_dark = dark

    def _create_tray_image(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((8, 8, 56, 56), fill=(38, 127, 255, 230))
        draw.ellipse((20, 20, 44, 44), fill=(255, 255, 255, 230))
        draw.rectangle((30, 8, 34, 56), fill=(38, 127, 255, 230))
        return img

    def _setup_tray(self):
        if not TRAY_AVAILABLE:
            return
        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", lambda icon, item: self._run_on_ui(self.quick_show), default=True),
            pystray.MenuItem("显/隐文字", lambda icon, item: self._run_on_ui(self.toggle_text_visibility)),
            pystray.MenuItem("显/隐窗口", lambda icon, item: self._run_on_ui(self.toggle_window_visibility)),
            pystray.MenuItem("底色换色", lambda icon, item: self._run_on_ui(self.toggle_theme)),
            pystray.MenuItem("透明度增加", lambda icon, item: self._run_on_ui(self.increase_alpha)),
            pystray.MenuItem("透明度减少", lambda icon, item: self._run_on_ui(self.decrease_alpha)),
            pystray.MenuItem("设置", lambda icon, item: self._run_on_ui(self.open_settings_window)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda icon, item: self._run_on_ui(self.on_close)),
        )
        self.tray_icon = pystray.Icon("private_input_v2", self._create_tray_image(), "Private Input V2", menu)
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def _run_on_ui(self, callback):
        self.root.after(0, callback)

    def _shutdown_tray(self):
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None

    def _bind_drag(self, widget: tk.Widget, left_button: bool):
        if left_button:
            widget.bind("<ButtonPress-1>", self._start_drag, add="+")
            widget.bind("<B1-Motion>", self._on_drag, add="+")
        else:
            widget.bind("<ButtonPress-3>", self._start_drag, add="+")
            widget.bind("<B3-Motion>", self._on_drag, add="+")

    def _start_drag(self, event):
        self.drag_offset_x = event.x_root - self.root.winfo_x()
        self.drag_offset_y = event.y_root - self.root.winfo_y()

    def _on_drag(self, event):
        self.root.geometry(f"+{event.x_root - self.drag_offset_x}+{event.y_root - self.drag_offset_y}")

    def _load_config(self):
        hotkeys = dict(DEFAULT_HOTKEYS)
        prefs = dict(DEFAULT_PREFS)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_READ) as key:
                for action in DEFAULT_HOTKEYS:
                    try:
                        val, _ = winreg.QueryValueEx(key, f"hotkey_{action}")
                        if isinstance(val, str) and val.strip():
                            _m, _vk, canonical = parse_hotkey_text(val.strip())
                            hotkeys[action] = canonical
                    except FileNotFoundError:
                        pass

                for name, default in DEFAULT_PREFS.items():
                    try:
                        val, _ = winreg.QueryValueEx(key, name)
                        if isinstance(default, bool):
                            prefs[name] = bool(int(val))
                        elif isinstance(default, float):
                            prefs[name] = float(val)
                        else:
                            prefs[name] = int(val)
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass
        prefs["window_alpha"] = max(0.05, min(1.0, float(prefs["window_alpha"])))
        prefs["auto_hide_ms"] = max(0, min(600000, int(prefs["auto_hide_ms"])))
        prefs["long_press_ms"] = max(100, min(3000, int(prefs["long_press_ms"])))
        return hotkeys, prefs

    def _save_config(self):
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
                for action, val in self.hotkeys.items():
                    winreg.SetValueEx(key, f"hotkey_{action}", 0, winreg.REG_SZ, str(val))
                winreg.SetValueEx(key, "startup_hidden", 0, winreg.REG_DWORD, int(bool(self.startup_hidden)))
                winreg.SetValueEx(key, "window_alpha", 0, winreg.REG_SZ, f"{self.window_alpha:.2f}")
                winreg.SetValueEx(key, "auto_hide_ms", 0, winreg.REG_DWORD, int(self.auto_hide_ms))
                winreg.SetValueEx(key, "use_long_press", 0, winreg.REG_DWORD, int(bool(self.use_long_press)))
                winreg.SetValueEx(key, "long_press_ms", 0, winreg.REG_DWORD, int(self.long_press_ms))
        except Exception as e:
            debug_log(f"保存配置失败: {e}")

    def _register_hotkeys(self):
        self._unregister_hotkeys()
        parsed = {}
        used = {}
        for action in ACTION_ORDER:
            try:
                mods, vk, canonical = parse_hotkey_text(self.hotkeys[action])
                self.hotkeys[action] = canonical
                if canonical in used:
                    raise ValueError(f"热键冲突: {canonical}")
                used[canonical] = action
                parsed[action] = (mods, vk, canonical)
            except ValueError as e:
                debug_log(f"{ACTION_LABEL[action]} 热键无效: {e}")

        if parsed:
            self.hotkey_worker = HotkeyWorker(self.hotkey_events)
            self.hotkey_worker.start(parsed, self.use_long_press, self.long_press_ms)

    def _unregister_hotkeys(self):
        if self.hotkey_worker:
            self.hotkey_worker.stop()
            self.hotkey_worker = None

    def _poll_hotkey_messages(self):
        while True:
            try:
                event = self.hotkey_events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "hotkey_action":
                self._trigger_action(event[1])
        self.root.after(30, self._poll_hotkey_messages)

    def _trigger_action(self, action: str):
        if action == ACTION_TOGGLE_TEXT:
            self.toggle_text_visibility()
        elif action == ACTION_TOGGLE_WINDOW:
            self.toggle_window_visibility()
        elif action == ACTION_TOGGLE_THEME:
            self.toggle_theme()
        elif action == ACTION_ALPHA_UP:
            self.increase_alpha()
        elif action == ACTION_ALPHA_DOWN:
            self.decrease_alpha()

    def _set_window_alpha(self, value: float, save_later: bool = True):
        value = max(0.05, min(1.0, float(value)))
        if abs(value - self.window_alpha) < 1e-6:
            return
        self.window_alpha = value
        self.root.attributes("-alpha", self.window_alpha)
        if self.settings_pref_vars and "window_alpha" in self.settings_pref_vars:
            try:
                self.settings_pref_vars["window_alpha"].set(self.window_alpha)
            except Exception:
                pass
        if save_later:
            self._schedule_save_config()

    def _schedule_save_config(self):
        if self.alpha_save_job:
            self.root.after_cancel(self.alpha_save_job)
        self.alpha_save_job = self.root.after(500, self._flush_pending_save)

    def _flush_pending_save(self):
        self.alpha_save_job = None
        self._save_config()

    def increase_alpha(self):
        self._set_window_alpha(self.window_alpha + ALPHA_STEP)

    def decrease_alpha(self):
        self._set_window_alpha(self.window_alpha - ALPHA_STEP)

    def toggle_theme(self):
        self._apply_theme(not self.is_dark)

    def toggle_text_visibility(self):
        if self.visible_text:
            self.hide_text()
        else:
            self.show_text()

    def show_text(self):
        self.entry.delete(0, "end")
        if self.real_text:
            self.entry.insert(0, self.real_text)
        self.entry.configure(show="", insertwidth=1)
        self.visible_text = True
        self.hidden_select_all = False
        self.entry.icursor("end")
        self.entry.xview_moveto(1.0)
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
        self.hide_job = None
        if self.auto_hide_ms > 0:
            self.hide_job = self.root.after(self.auto_hide_ms, self.hide_text)

    def hide_text(self):
        self._sync_real_text_from_entry()
        self.entry.delete(0, "end")
        self.entry.configure(show=HIDE_CHAR, insertwidth=0)
        self.visible_text = False
        self.hidden_select_all = False
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None

    def quick_show(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.focus_force()
        self.entry.focus_set()
        self.root.attributes("-topmost", False)

    def quick_hide(self):
        self.root.withdraw()

    def toggle_window_visibility(self):
        if self.root.state() in ("withdrawn", "iconic"):
            self.quick_show()
        else:
            self.quick_hide()

    def minimize_window(self):
        self.root.iconify()

    def open_settings_window(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            self.settings_window.focus_force()
            return

        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("设置")
        self.settings_window.geometry("460x380+80+80")
        self.settings_window.resizable(False, False)
        self.settings_window.attributes("-alpha", 1.0)
        self.settings_window.overrideredirect(False)
        self.settings_window.transient(self.root)
        self.settings_window.grab_set()

        self.settings_edit_hotkeys = dict(self.hotkeys)
        self.settings_pref_vars = {
            "startup_hidden": tk.BooleanVar(value=self.startup_hidden),
            "window_alpha": tk.DoubleVar(value=self.window_alpha),
            "auto_hide_ms": tk.IntVar(value=self.auto_hide_ms),
            "use_long_press": tk.BooleanVar(value=self.use_long_press),
            "long_press_ms": tk.IntVar(value=self.long_press_ms),
        }
        self.capture_action = None
        self.settings_vars = {}
        self.settings_status_var = tk.StringVar(value="已载入上次保存的设置")

        container = ttk.Frame(self.settings_window)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0)
        vbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = ttk.Frame(canvas)
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfigure(body_window, width=event.width)

        def _on_mousewheel(event):
            if event.delta:
                canvas.yview_scroll(-1 * int(event.delta / 120), "units")

        body.bind("<Configure>", _on_body_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        self.settings_window.bind("<MouseWheel>", _on_mousewheel)

        content = ttk.Frame(body, padding=12)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="热键设置（类似游戏按键修改）", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(content, text="支持 Ctrl / Alt / Shift + 主键（字母、数字、F1-F24、音量键）").pack(anchor="w", pady=(2, 10))

        for action in ACTION_ORDER:
            row = ttk.Frame(content)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=ACTION_LABEL[action], width=10).pack(side="left")
            var = tk.StringVar(value=self.settings_edit_hotkeys[action])
            self.settings_vars[action] = var
            ttk.Label(row, textvariable=var, width=20).pack(side="left", padx=(0, 8))
            ttk.Button(row, text="设置", command=lambda a=action: self.start_hotkey_capture(a)).pack(side="left")

        ttk.Separator(content, orient="horizontal").pack(fill="x", pady=(10, 8))
        ttk.Label(content, text="其他设置", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Checkbutton(content, text="启动时隐藏窗口", variable=self.settings_pref_vars["startup_hidden"]).pack(anchor="w", pady=(6, 2))

        alpha_row = ttk.Frame(content)
        alpha_row.pack(fill="x", pady=4)
        ttk.Label(alpha_row, text="窗口透明度").pack(side="left")
        tk.Scale(alpha_row, from_=0.05, to=1.0, resolution=0.01, orient="horizontal", length=220, variable=self.settings_pref_vars["window_alpha"], showvalue=True).pack(side="left", padx=(10, 0))

        auto_row = ttk.Frame(content)
        auto_row.pack(fill="x", pady=4)
        ttk.Label(auto_row, text="文字自动隐藏(ms)").pack(side="left")
        ttk.Spinbox(auto_row, from_=0, to=600000, increment=100, textvariable=self.settings_pref_vars["auto_hide_ms"], width=12).pack(side="left", padx=(10, 0))

        lp_row = ttk.Frame(content)
        lp_row.pack(fill="x", pady=(8, 2))
        ttk.Checkbutton(lp_row, text="启用长按触发", variable=self.settings_pref_vars["use_long_press"]).pack(side="left")
        ttk.Label(lp_row, text="长按时长(ms)").pack(side="left", padx=(12, 4))
        long_press_spin = ttk.Spinbox(
            lp_row,
            from_=100,
            to=3000,
            increment=50,
            textvariable=self.settings_pref_vars["long_press_ms"],
            width=8,
        )
        long_press_spin.pack(side="left")

        def _toggle_long_press_spin(*_args):
            long_press_spin.configure(state="normal" if self.settings_pref_vars["use_long_press"].get() else "disabled")

        self.settings_pref_vars["use_long_press"].trace_add("write", _toggle_long_press_spin)
        _toggle_long_press_spin()

        ttk.Label(content, textvariable=self.settings_status_var, foreground="#444444").pack(anchor="w", pady=(8, 10))
        bottom = ttk.Frame(content)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="保存", command=self.save_hotkey_settings).pack(side="right")
        ttk.Button(bottom, text="取消", command=self.close_settings_window).pack(side="right", padx=(0, 8))

        self.settings_window.bind("<KeyPress>", self.on_settings_keypress)
        self.settings_window.bind("<Escape>", lambda e: self.close_settings_window())
        self.settings_window.protocol("WM_DELETE_WINDOW", self.close_settings_window)

    def start_hotkey_capture(self, action: str):
        self.capture_action = action
        self.settings_status_var.set(f"请按下 {ACTION_LABEL[action]} 的新热键...")

    def on_settings_keypress(self, event: tk.Event):
        if not self.capture_action:
            return
        token = key_event_to_token(event)
        if token is None:
            self.settings_status_var.set("这个键不支持，请换一个")
            return "break"
        mods = mods_from_event_state(event.state) | mods_from_keyboard_state()
        hotkey_text = build_hotkey_text(mods, token)
        action = self.capture_action
        self.capture_action = None
        self.settings_edit_hotkeys[action] = hotkey_text
        self.settings_vars[action].set(hotkey_text)
        self.settings_status_var.set(f"已设置 {ACTION_LABEL[action]} = {hotkey_text}")
        return "break"

    def save_hotkey_settings(self):
        used = {}
        normalized = {}
        try:
            for action, text in self.settings_edit_hotkeys.items():
                _mods, _vk, canonical = parse_hotkey_text(text)
                if canonical in used:
                    raise ValueError(f"热键冲突: {canonical}")
                used[canonical] = action
                normalized[action] = canonical
        except ValueError as e:
            self.settings_status_var.set(f"保存失败：{e}")
            messagebox.showerror("保存失败", str(e), parent=self.settings_window)
            return

        try:
            new_startup_hidden = bool(self.settings_pref_vars["startup_hidden"].get())
            new_alpha = float(self.settings_pref_vars["window_alpha"].get())
            new_auto_hide = int(self.settings_pref_vars["auto_hide_ms"].get())
            new_use_long_press = bool(self.settings_pref_vars["use_long_press"].get())
            new_long_press_ms = int(self.settings_pref_vars["long_press_ms"].get())
        except Exception:
            self.settings_status_var.set("保存失败：设置格式不正确")
            messagebox.showerror("保存失败", "设置格式不正确", parent=self.settings_window)
            return

        if not (0.05 <= new_alpha <= 1.0):
            messagebox.showerror("保存失败", "透明度需在 0.05 到 1.0", parent=self.settings_window)
            return
        if not (0 <= new_auto_hide <= 600000):
            messagebox.showerror("保存失败", "自动隐藏范围 0 到 600000", parent=self.settings_window)
            return
        if not (100 <= new_long_press_ms <= 3000):
            messagebox.showerror("保存失败", "长按时长范围 100 到 3000", parent=self.settings_window)
            return

        self.hotkeys.update(normalized)
        self.startup_hidden = new_startup_hidden
        self._set_window_alpha(new_alpha, save_later=False)
        self.auto_hide_ms = new_auto_hide
        self.use_long_press = new_use_long_press
        self.long_press_ms = new_long_press_ms

        if self.alpha_save_job:
            self.root.after_cancel(self.alpha_save_job)
            self.alpha_save_job = None
        self._save_config()
        self._register_hotkeys()
        lp = f"长按{self.long_press_ms}ms" if self.use_long_press else "短按触发"
        self.settings_status_var.set(f"保存成功，设置已生效（{lp}）")

    def close_settings_window(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.grab_release()
            self.settings_window.destroy()
        self.settings_window = None
        self.capture_action = None
        self.settings_vars = {}
        self.settings_edit_hotkeys = {}
        self.settings_pref_vars = {}
        self.settings_status_var = None

    def select_all(self):
        if self.visible_text:
            self.entry.selection_range(0, "end")
            self.entry.icursor("end")
        else:
            self.hidden_select_all = bool(self.real_text)
        return "break"

    def _on_right_click(self, event):
        self.copy_menu.tk_popup(event.x_root, event.y_root)

    def on_close(self):
        if self.alpha_save_job:
            self.root.after_cancel(self.alpha_save_job)
            self.alpha_save_job = None
            self._save_config()
        self._unregister_hotkeys()
        self._shutdown_tray()
        self.close_settings_window()
        self.root.destroy()


def main():
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    _app = PrivateStealthInputV2(root)
    root.mainloop()


if __name__ == "__main__":
    main()
