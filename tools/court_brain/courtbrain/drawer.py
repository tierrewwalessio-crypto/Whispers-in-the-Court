"""The side panel: where the player actually talks to their court.

A borderless window that docks to the right edge of the EU5 window and
slides in only when something in the game asks for it (Speak With, the
council, an envoy, a progress, "answer in person" on an event, the Corte
button). It is drawn to look like one of EU5's own windows: the navy
lacquer with its soft gradient, a double gilded frame, the game's own
fleur-de-lis corners, carved ornament and diamond dividers (decoded from the
game's files by gfx.py and painted gold), plate buttons, the khaki section
bars of the outliner, and the game's typefaces (Cormorant Garamond for
headings, Noto Serif for text, loaded from the game folder for this process
only).

Tkinter ships with Python, so the panel adds no dependency.

Threading: Tk lives on the main thread. The rest of Court Brain talks to the
panel only through `Drawer.post(...)`, which queues a call that the Tk loop
runs; the panel talks back through the callbacks it was given.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# --------------------------------------------------------------------------
# EU5 palette, sampled from the game's own windows
# --------------------------------------------------------------------------
NAVY_HI = "#223750"     # top of a window's gradient
NAVY = "#152131"        # panel lacquer
NAVY_LO = "#0f1824"
INSET = "#0b1018"       # text boxes, like the event text frame
INSET_EDGE = "#3b3322"
GOLD = "#c9a45c"        # frames and rules
GOLD_HI = "#ecd49a"     # names, headings
GOLD_DIM = "#7d6639"
GOLD_DEEP = "#4a3c22"
TEXT = "#ece2c9"        # parchment text
TEXT_DIM = "#a39b88"
BLUE = "#8db7d9"        # the game's italic subtitles; the ruler's own words
GOOD = "#93c572"
BAD = "#dd7b62"
CARD = "#111a27"        # suggested replies
CARD_HI = "#1c2b40"

# plate buttons: (top, bottom, edge, text)
PLATES = {
    "blue": ("#2d4768", "#172a43", "#6f7f93", TEXT),
    "gold": ("#6f5a2c", "#3a2e17", GOLD, GOLD_HI),
    "red": ("#7a2a1e", "#47150e", GOLD, GOLD_HI),
}
BARS = {                # section bars, like "Markets" and "Government" in the outliner
    "gold": ("#806a39", "#4b3d21"),
    "red": ("#74281c", "#43140d"),
}

WIDTH = 520
TOP_MARGIN = 150        # below EU5's top bar and outliner header
BOTTOM_MARGIN = 120     # above the bottom bar

IS_WINDOWS = hasattr(ctypes, "windll")


def mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x + (y - x) * t):02x}" for x, y in zip(a, b))


def gradient(canvas: tk.Canvas, x0: int, y0: int, x1: int, y1: int, top: str, bottom: str,
             tag: str = "bg") -> None:
    h = max(1, y1 - y0)
    step = 1 if h < 200 else 2
    for y in range(0, h, step):
        canvas.create_line(x0, y0 + y, x1, y0 + y, fill=mix(top, bottom, y / h), width=step, tags=tag)


# --------------------------------------------------------------------------
# Game fonts and artwork
# --------------------------------------------------------------------------

def _load_game_fonts(game_dir: str) -> None:
    if not IS_WINDOWS or not game_dir:
        return
    root = Path(game_dir) / "game" / "loading_screen" / "fonts"
    wanted = [
        "CormorantGaramond/CormorantGaramond-SemiBold.ttf",
        "CormorantGaramond/CormorantGaramond-Bold.ttf",
        "CormorantGaramond/CormorantGaramond-Medium.ttf",
        "MapNamesFonts/NotoSerif-Regular.ttf",
        "MapNamesFonts/NotoSerif_SemiCondensed-Regular.ttf",
    ]
    for rel in wanted:
        path = root / rel
        if path.is_file():
            # FR_PRIVATE: visible to this process only, gone when it exits.
            ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)


def _pick(families: set[str], *candidates: str) -> str:
    lower = {f.lower(): f for f in families}
    for name in candidates:
        if name.lower() in lower:
            return lower[name.lower()]
    for name in candidates:
        for fam_low, fam in lower.items():
            if name.lower() in fam_low:
                return fam
    return candidates[-1]


class Art:
    """Fonts and gilded ornaments shared by the panel and the Court Brain window."""

    def __init__(self, root: tk.Misc, game_dir: str, cache_dir: Path | None) -> None:
        fams = set(tkfont.families(root))
        head = _pick(fams, "Cormorant Garamond SemiBold", "Cormorant Garamond", "Georgia")
        body = _pick(fams, "Noto Serif", "Noto Serif SemiCondensed", "Georgia")
        self.kind = tkfont.Font(root, family=head, size=11, weight="bold")
        self.title = tkfont.Font(root, family=head, size=21, weight="bold")
        self.sub = tkfont.Font(root, family=body, size=10, slant="italic")
        self.speaker = tkfont.Font(root, family=head, size=15, weight="bold")
        self.text = tkfont.Font(root, family=body, size=11)
        self.italic = tkfont.Font(root, family=body, size=11, slant="italic")
        self.gesture = tkfont.Font(root, family=body, size=10, slant="italic")
        self.small = tkfont.Font(root, family=body, size=9, slant="italic")
        self.note = tkfont.Font(root, family=body, size=10)
        self.hint = tkfont.Font(root, family=body, size=10)
        self.btn = tkfont.Font(root, family=head, size=13, weight="bold")
        self.bar = tkfont.Font(root, family=head, size=12, weight="bold")
        self.images: dict[str, tk.PhotoImage] = {}
        if cache_dir is None or not game_dir:
            return
        try:
            from . import gfx
            for name, path in gfx.prepare(game_dir, cache_dir, GOLD).items():
                self.images[name] = tk.PhotoImage(master=root, file=str(path))
        except Exception:           # artwork is a luxury: the panel draws without it
            self.images = {}

    def img(self, name: str) -> tk.PhotoImage | None:
        return self.images.get(name)


# --------------------------------------------------------------------------
# Finding the game window
# --------------------------------------------------------------------------

def game_client_rect(title_hint: str = "europa universalis") -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of EU5's client area on screen, or None."""
    if not IS_WINDOWS:
        return None
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title_hint in buf.value.lower():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        return None
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        return None
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, pt.x + rect.right, pt.y + rect.bottom


def foreground_is(hwnd_titles: tuple[str, ...]) -> bool:
    if not IS_WINDOWS:
        return True
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    title = buf.value.lower()
    return any(t in title for t in hwnd_titles)


# --------------------------------------------------------------------------
# Widgets in the game's manner
# --------------------------------------------------------------------------

class Plate(tk.Canvas):
    """A button like the game's: a lacquered plate with a gilded edge."""

    PADX, PADY = 16, 7

    def __init__(self, parent: tk.Misc, text: str, command: Callable[[], None], *, font: tkfont.Font,
                 style: str = "blue", wide: bool = False, bg: str = NAVY) -> None:
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=34, cursor="hand2")
        self.text, self.command, self.font, self.style, self.wide = text, command, font, style, wide
        self.hover = False
        if not wide:
            self.configure(width=font.measure(text) + 2 * self.PADX)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", lambda _e: self.command())

    def set_text(self, text: str) -> None:
        self.text = text
        if not self.wide:
            self.configure(width=self.font.measure(text) + 2 * self.PADX)
        self._draw()

    def set_style(self, style: str) -> None:
        if style != self.style:
            self.style = style
            self._draw()

    def _set_hover(self, on: bool) -> None:
        self.hover = on
        self._draw()

    def _draw(self) -> None:
        w = self.winfo_width()
        if w < 8:
            return
        self.delete("all")
        top, bottom, edge, fg = PLATES.get(self.style, PLATES["blue"])
        if self.hover:
            top, bottom, fg = mix(top, "#ffffff", 0.12), mix(bottom, "#ffffff", 0.08), GOLD_HI
        if self.wide:
            probe = self.create_text(0, 0, text=self.text, font=self.font, anchor="nw",
                                     width=w - 2 * self.PADX - 14)
            x0, y0, x1, y1 = self.bbox(probe)
            self.delete(probe)
            h = max(34, (y1 - y0) + 2 * self.PADY)
        else:
            h = 34
        if int(float(self.cget("height"))) != h:
            self.configure(height=h)
            return
        gradient(self, 1, 1, w - 1, h - 1, top, bottom)
        self.create_rectangle(0, 0, w - 1, h - 1, outline=edge)
        self.create_line(2, 2, w - 2, 2, fill=mix(top, "#ffffff", 0.18))
        self.create_line(2, h - 3, w - 2, h - 3, fill=mix(bottom, "#000000", 0.35))
        if self.wide:
            self.create_text(self.PADX + 7, h // 2 + 1, text=self.text, font=self.font, fill="#000000",
                             anchor="w", width=w - 2 * self.PADX - 14)
            self.create_text(self.PADX + 6, h // 2, text=self.text, font=self.font, fill=fg,
                             anchor="w", width=w - 2 * self.PADX - 14)
        else:
            self.create_text(w // 2 + 1, h // 2 + 1, text=self.text, font=self.font, fill="#000000")
            self.create_text(w // 2, h // 2, text=self.text, font=self.font, fill=fg)


class Bar(tk.Canvas):
    """A section bar like "Markets" in the outliner: gilt gradient, cream letters."""

    def __init__(self, parent: tk.Misc, text: str, *, font: tkfont.Font, style: str = "gold",
                 right: str = "", command: Callable[[], None] | None = None, bg: str = NAVY) -> None:
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=24,
                         cursor="hand2" if command else "")
        self.text, self.font, self.style, self.right = text, font, style, right
        self.bind("<Configure>", lambda _e: self._draw())
        if command:
            self.bind("<ButtonRelease-1>", lambda _e: command())

    def _draw(self) -> None:
        w, h = self.winfo_width(), 24
        if w < 8:
            return
        self.delete("all")
        top, bottom = BARS.get(self.style, BARS["gold"])
        gradient(self, 0, 0, w, h, top, bottom)
        self.create_rectangle(0, 0, w - 1, h - 1, outline=mix(top, "#000000", 0.4))
        self.create_line(1, 1, w - 1, 1, fill=mix(top, "#ffffff", 0.25))
        self.create_polygon(10, h // 2, 14, h // 2 - 4, 18, h // 2, 14, h // 2 + 4, fill=GOLD_HI, outline="")
        self.create_text(27, h // 2 + 1, text=self.text, font=self.font, fill="#000000", anchor="w")
        self.create_text(26, h // 2, text=self.text, font=self.font, fill=TEXT, anchor="w")
        if self.right:
            self.create_text(w - 10, h // 2, text=self.right, font=self.font, fill=TEXT, anchor="e")


class ThinScroll(tk.Canvas):
    """A slim gilt scrollbar: the white Windows one would be the only thing not of the game."""

    def __init__(self, parent: tk.Misc, target: tk.Text, *, bg: str = INSET) -> None:
        super().__init__(parent, bg=bg, width=7, highlightthickness=0, bd=0, cursor="arrow")
        self.target = target
        self.lo, self.hi = 0.0, 1.0
        self._grab: float | None = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_grab", None))

    def set(self, lo: str, hi: str) -> None:
        self.lo, self.hi = float(lo), float(hi)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        if self.hi - self.lo >= 0.999:
            return
        h = self.winfo_height()
        y0, y1 = int(self.lo * h), max(int(self.hi * h), int(self.lo * h) + 18)
        self.create_line(3, 0, 3, h, fill=GOLD_DEEP)
        self.create_rectangle(1, y0, 5, y1, fill=GOLD_DIM, outline=GOLD)

    def _press(self, e: tk.Event) -> None:
        h = max(1, self.winfo_height())
        pos = e.y / h
        if self.lo <= pos <= self.hi:
            self._grab = pos - self.lo
        else:
            self.target.yview_moveto(max(0.0, pos - (self.hi - self.lo) / 2))

    def _drag(self, e: tk.Event) -> None:
        if self._grab is None:
            return
        self.target.yview_moveto(max(0.0, e.y / max(1, self.winfo_height()) - self._grab))


def dark_title_bar(win: tk.Toplevel) -> None:
    """Windows 10/11 draw the title bar dark when asked; the white one would clash."""
    if not IS_WINDOWS:
        return
    try:
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        value = ctypes.c_int(1)
        for attr in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE (and its older number)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
        # DWMWA_CAPTION_COLOR / DWMWA_TEXT_COLOR (Windows 11): navy and parchment, as COLORREF
        for attr, color in ((35, NAVY_LO), (36, TEXT), (34, GOLD_DIM)):
            ref = ctypes.c_int(int(color[5:7], 16) << 16 | int(color[3:5], 16) << 8 | int(color[1:3], 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ref), ctypes.sizeof(ref))
    except (AttributeError, OSError, tk.TclError):
        pass


def inset(parent: tk.Misc) -> tuple[tk.Frame, tk.Frame]:
    """A sunken box with a thin gilt edge, like the event text frame. (outer, inner)"""
    outer = tk.Frame(parent, bg=INSET_EDGE, padx=1, pady=1)
    inner = tk.Frame(outer, bg=INSET)
    inner.pack(fill="both", expand=True)
    return outer, inner


# --------------------------------------------------------------------------
# The panel
# --------------------------------------------------------------------------

@dataclass
class Callbacks:
    on_send: Callable[[str], None]
    on_offer: Callable[[int], None]
    on_close: Callable[[], None]
    on_hub: Callable[[str], None]
    on_suggest: Callable[[], None] = lambda: None
    listen_start: Callable[[], bool] = lambda: False
    listen_stop: Callable[[], str] = lambda: ""


@dataclass
class Header:
    kind: str = ""        # small caps line: AUDIENCE, COUNCIL, ENVOY, ...
    title: str = ""       # the person / court / place
    subtitle: str = ""    # role, court, faith


PLACEHOLDER = "Say anything - propose, bargain, refuse…   (Enter to speak, Shift+Enter for a new line)"


class Drawer:
    WINDOW_TITLE = "Whispers in the Court"

    def __init__(self, callbacks: Callbacks, *, game_dir: str = "", cache_dir: Path | None = None) -> None:
        self.cb = callbacks
        self._calls: "queue.Queue[tuple[str, tuple, dict]]" = queue.Queue()
        _load_game_fonts(game_dir)

        self.root = tk.Tk()
        self.root.withdraw()
        # An error inside any Tk callback (a button, a timer) goes to the log,
        # never to an invisible console - and never stops the panel.
        self.on_error = lambda text: print(text, flush=True)
        self.root.report_callback_exception = self._tk_error
        self.art = Art(self.root, game_dir, cache_dir)
        f = self.art
        self.f_btn, self.f_text = f.btn, f.text

        self.win = tk.Toplevel(self.root)
        self.win.title(self.WINDOW_TITLE)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=GOLD)
        self.win.withdraw()

        self._shown = False
        self._busy = False
        self._busy_text = ""
        self._busy_tick = 0
        self._listening = False
        self._has_lines = False
        self._placeholder = False
        self._header = Header()
        self._build()
        self.root.after(60, self._drain)
        self.root.after(500, self._follow_game)
        self.root.after(400, self._animate)

    # ------------------------------------------------------------------ API
    def post(self, method: str, *args, **kwargs) -> None:
        """Thread-safe: run self.<method>(*args) on the Tk thread."""
        self._calls.put((method, args, kwargs))

    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        self.post("_quit")

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        f = self.art
        # A double frame, gold outside and a dark hairline inside, as on EU5's windows.
        outer = tk.Frame(self.win, bg="#06090e")
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        self.body = tk.Frame(outer, bg=NAVY)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)

        self.head = tk.Canvas(self.body, bg=NAVY_HI, height=128, highlightthickness=0, bd=0)
        self.head.pack(fill="x")
        self.head.bind("<Configure>", lambda _e: self._draw_head())
        self.head.tag_bind("close", "<ButtonRelease-1>", lambda _e: self.cb.on_close())
        self.head.tag_bind("close", "<Enter>", lambda _e: self._close_hover(True))
        self.head.tag_bind("close", "<Leave>", lambda _e: self._close_hover(False))

        # Bottom sections are packed first, from the bottom up, so the
        # transcript can take whatever height is left without ever pushing
        # the input box off the panel.
        foot = tk.Canvas(self.body, bg=NAVY_LO, height=10, highlightthickness=0, bd=0)
        foot.pack(side="bottom", fill="x")
        foot.bind("<Configure>", lambda e: (foot.delete("all"),
                                            foot.create_line(12, 3, e.width - 12, 3, fill=GOLD_DEEP)))
        bottom = tk.Frame(self.body, bg=NAVY_LO, padx=14, pady=4)
        bottom.pack(side="bottom", fill="x")
        box, box_in = inset(bottom)
        box.pack(fill="x", pady=(6, 0))
        self.entry = tk.Text(box_in, height=2, bg=INSET, fg=TEXT, insertbackground=GOLD_HI,
                             relief="flat", wrap="word", font=f.text, padx=10, pady=8,
                             highlightthickness=0, selectbackground=GOLD_DEEP)
        self.entry.pack(fill="x")
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<FocusIn>", lambda _e: self._hide_placeholder())
        self.entry.bind("<FocusOut>", lambda _e: self._show_placeholder())
        self.l_hint = tk.Label(bottom, text="", fg=TEXT_DIM, bg=NAVY_LO, font=f.small, anchor="w",
                               justify="left", wraplength=WIDTH - 60)
        row = tk.Frame(bottom, bg=NAVY_LO, pady=8)
        row.pack(fill="x")
        self._button_row = row
        self.b_speak = Plate(row, "Speak", self._send, font=f.btn, style="gold", bg=NAVY_LO)
        self.b_speak.pack(side="left")
        self.b_mic = Plate(row, "\U0001F399  Dictate", self._toggle_mic, font=f.btn, bg=NAVY_LO)
        self.b_mic.pack(side="left", padx=8)
        self.b_leave = Plate(row, "Dismiss", lambda: self.cb.on_close(), font=f.btn, bg=NAVY_LO)
        self.b_leave.pack(side="right")
        self._show_placeholder()

        rule = tk.Canvas(self.body, bg=NAVY_LO, height=12, highlightthickness=0, bd=0)
        rule.pack(side="bottom", fill="x")
        rule.bind("<Configure>", lambda e: self._draw_rule(rule, e.width, NAVY_LO))
        self.l_status = tk.Label(self.body, text="", fg=TEXT_DIM, bg=NAVY, font=f.small, anchor="w")
        self.l_status.pack(side="bottom", fill="x", padx=20, pady=(2, 0))
        # Suggested replies: small, quiet cards that can be folded away, so
        # they never take the room the conversation needs.
        self.f_sugg = tk.Frame(self.body, bg=NAVY)
        self.f_sugg.pack(side="bottom", fill="x", padx=14, pady=(2, 2))
        self._sugg_items: list[str] = []
        self._sugg_open = True
        self._sugg_offer = False          # show "Suggest replies" (they are written only when asked)
        self._sugg_loading = False
        self.f_offers = tk.Frame(self.body, bg=NAVY)
        self.f_offers.pack(side="bottom", fill="x", padx=14)

        # Transcript, in a sunken gilt-edged box, with a slim gilt scrollbar.
        mid = tk.Frame(self.body, bg=NAVY)
        mid.pack(side="top", fill="both", expand=True, padx=14, pady=(10, 6))
        frame, inner = inset(mid)
        frame.pack(fill="both", expand=True)
        self.text = tk.Text(inner, bg=INSET, fg=TEXT, relief="flat", wrap="word",
                            highlightthickness=0, borderwidth=0, padx=14, pady=10,
                            font=f.text, cursor="arrow", spacing1=2, spacing2=3, spacing3=4,
                            selectbackground=GOLD_DEEP)
        self.scroll = ThinScroll(inner, self.text)
        self.scroll.pack(side="right", fill="y", padx=(0, 3), pady=6)
        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(yscrollcommand=self.scroll.set)
        self.text.bind("<MouseWheel>", lambda e: self.text.yview_scroll(int(-e.delta / 60), "units"))
        self.text.tag_configure("speaker", foreground=GOLD_HI, font=f.speaker, spacing1=6, spacing3=1)
        self.text.tag_configure("role", foreground=BLUE, font=f.gesture)
        self.text.tag_configure("speech", foreground=TEXT, font=f.text, lmargin1=2, lmargin2=2)
        self.text.tag_configure("gesture", foreground=TEXT_DIM, font=f.gesture, lmargin1=2, lmargin2=2,
                                spacing3=3)
        self.text.tag_configure("you", foreground=BLUE, font=f.kind, spacing1=10, lmargin1=44,
                                lmargin2=44)
        self.text.tag_configure("player", foreground=BLUE, font=f.italic, lmargin1=44, lmargin2=44,
                                spacing3=6)
        self.text.tag_configure("note", foreground=TEXT_DIM, font=f.small, spacing1=4, lmargin1=10,
                                lmargin2=24)
        self.text.tag_configure("good", foreground=GOOD, font=f.note, spacing1=3, lmargin1=10, lmargin2=24)
        self.text.tag_configure("bad", foreground=BAD, font=f.note, spacing1=3, lmargin1=10, lmargin2=24)
        self.text.tag_configure("sep", justify="center", spacing1=8, spacing3=6)
        self.text.configure(state="disabled")

    def _draw_rule(self, canvas: tk.Canvas, w: int, bg: str) -> None:
        canvas.delete("all")
        img = self.art.img("divider_small")
        if img is not None:
            canvas.create_image(w // 2, 6, image=img)
        else:
            canvas.create_line(20, 6, w - 20, 6, fill=GOLD_DEEP)

    def _draw_head(self) -> None:
        c, f = self.head, self.art
        w = c.winfo_width()
        if w < 50:
            return
        c.delete("all")
        hd = self._header
        # Measure the title first: the band grows with a long name.
        probe = c.create_text(w // 2, 0, text=hd.title, font=f.title, width=w - 110, justify="center", anchor="n")
        th = c.bbox(probe)[3] - c.bbox(probe)[1]
        c.delete(probe)
        sub_h = f.sub.metrics("linespace") + 4 if hd.subtitle else 0
        h = 40 + th + sub_h + 30
        if int(float(c.cget("height"))) != h:
            c.configure(height=h)
            return
        gradient(c, 0, 0, w, h, NAVY_HI, NAVY)
        faint = f.img("ornament_faint")
        if faint is not None:
            c.create_image(w // 2, h // 2 + 6, image=faint)
        for name, x, anchor in (("corner_tl", 2, "nw"), ("corner_tr", w - 2, "ne")):
            img = f.img(name)
            if img is not None:
                c.create_image(x, 2, image=img, anchor=anchor)
        kind = " ".join(hd.kind.upper()) if hd.kind else ""
        if kind:
            c.create_text(w // 2, 24, text=kind, font=f.kind, fill=GOLD)
            half = f.kind.measure(kind) // 2 + 14
            for sx in (-1, 1):
                x0 = w // 2 + sx * half
                c.create_line(x0, 24, x0 + sx * 40, 24, fill=GOLD_DIM)
                c.create_polygon(x0 + sx * 44, 24, x0 + sx * 40, 21, x0 + sx * 36, 24, x0 + sx * 40, 27,
                                 fill=GOLD, outline="")
        c.create_text(w // 2 + 1, 41, text=hd.title, font=f.title, fill="#000000", width=w - 110,
                      justify="center", anchor="n")
        c.create_text(w // 2, 40, text=hd.title, font=f.title, fill=TEXT, width=w - 110,
                      justify="center", anchor="n")
        if hd.subtitle:
            c.create_text(w // 2, 42 + th, text=hd.subtitle, font=f.sub, fill=BLUE, width=w - 80,
                          justify="center", anchor="n")
        div = f.img("divider")
        if div is not None:
            c.create_image(w // 2, h - 11, image=div)
        c.create_line(0, h - 2, w, h - 2, fill=GOLD_DIM)
        c.create_line(0, h - 1, w, h - 1, fill="#06090e")
        # the close button: a gilt ring with a cross
        cx, cy = w - 24, 56
        c.create_oval(cx - 11, cy - 11, cx + 11, cy + 11, outline=GOLD_DIM, width=1, fill=NAVY_LO,
                      tags=("close", "close_ring"))
        c.create_text(cx, cy, text="✕", font=f.kind, fill=TEXT_DIM, tags=("close", "close_x"))

    def _close_hover(self, on: bool) -> None:
        self.head.itemconfigure("close_ring", outline=GOLD if on else GOLD_DIM)
        self.head.itemconfigure("close_x", fill=GOLD_HI if on else TEXT_DIM)
        self.head.configure(cursor="hand2" if on else "")

    # ------------------------------------------------------------ queue pump
    def _tk_error(self, exc, value, tb) -> None:
        import traceback
        try:
            self.on_error("Panel error:\n" + "".join(traceback.format_exception(exc, value, tb))[-1500:])
        except Exception:  # noqa: BLE001 - reporting must never fail
            pass

    def _drain(self) -> None:
        """Run what the other threads asked of the panel. Each call on its own:
        one that fails is reported and skipped, and the pump always goes on (a
        pump that stopped left the panel frozen until Court Brain restarted)."""
        import traceback
        try:
            for _ in range(200):
                try:
                    method, args, kwargs = self._calls.get_nowait()
                except queue.Empty:
                    break
                try:
                    getattr(self, method)(*args, **kwargs)
                except Exception:  # noqa: BLE001
                    try:
                        self.on_error(f"Panel error in {method}:\n" + traceback.format_exc()[-1500:])
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            self.root.after(60, self._drain)

    def _quit(self) -> None:
        self.root.quit()

    # -------------------------------------------------------------- docking
    def _geometry(self) -> tuple[int, int, int, int]:
        rect = game_client_rect()
        if rect is None:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            rect = (0, 0, sw, sh)
        left, top, right, bottom = rect
        height = max(460, (bottom - top) - TOP_MARGIN - BOTTOM_MARGIN)
        x = right - WIDTH - 10
        y = top + TOP_MARGIN
        return x, y, right, height

    def _follow_game(self) -> None:
        """Keep docked to EU5, and step aside when the player leaves the game."""
        try:
            self._dock()
        except Exception:  # noqa: BLE001 - a window query that fails once must not stop the docking
            pass
        finally:
            self.root.after(500, self._follow_game)

    def _dock(self) -> None:
        if self._shown:
            titles = ("europa universalis", self.WINDOW_TITLE.lower(), "court brain")
            if not foreground_is(titles) and game_client_rect() is None:
                self.win.withdraw()
            else:
                x, y, _right, h = self._geometry()
                if abs(self.win.winfo_x() - x) > 2 or abs(self.win.winfo_height() - h) > 2:
                    self.win.geometry(f"{WIDTH}x{h}+{x}+{y}")
                if not self.win.winfo_viewable():
                    self.win.deiconify()

    def _slide(self, start: int, end: int, y: int, h: int, step: int = 0, on_done=None) -> None:
        steps = 10
        t = step / steps
        ease = 1 - (1 - t) ** 3
        x = int(start + (end - start) * ease)
        try:
            self.win.geometry(f"{WIDTH}x{h}+{x}+{y}")
            self.win.attributes("-alpha", 0.35 + 0.65 * (ease if end < start else 1 - ease))
        except tk.TclError:
            step = steps                          # the window went away: finish at once
        if step < steps:
            self.root.after(16, lambda: self._slide(start, end, y, h, step + 1, on_done))
        elif on_done:
            on_done()

    # ---------------------------------------------------------- open/close
    def set_close_label(self, text: str) -> None:
        self.b_leave.set_text(text)

    def open(self, header: Header) -> None:
        self.clear()
        self.set_hint("")
        self.offer_suggestions(False)
        self.set_close_label("Dismiss")
        self._header = header
        self._draw_head()
        x, y, right, h = self._geometry()
        if not self._shown:
            self._shown = True
            self.win.geometry(f"{WIDTH}x{h}+{right}+{y}")
            self.win.deiconify()
            self.win.lift()
            self._slide(right, x, y, h)
        self.root.after(250, self._focus_input)

    def close(self) -> None:
        if not self._shown:
            return
        x, y, right, h = self._geometry()

        def done() -> None:
            self.win.withdraw()
            self.win.attributes("-alpha", 1.0)

        self._shown = False
        self._slide(x, right, y, h, on_done=done)

    def _focus_input(self) -> None:
        try:
            self.win.focus_force()
            self.entry.focus_set()
        except tk.TclError:
            pass

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._has_lines = False
        self.set_suggestions([])
        self.set_offers([])
        self.set_status("")

    # -------------------------------------------------------- transcript
    def _separator(self) -> None:
        if not self._has_lines:
            self._has_lines = True
            return
        img = self.art.img("divider_small")
        if img is not None:
            self.text.insert("end", "\n", "sep")
            self.text.image_create("end - 2 chars", image=img)
        else:
            self.text.insert("end", "◆\n", ("sep", "note"))

    def add_line(self, speaker: str, text: str, gesture: str = "", role: str = "") -> None:
        self.text.configure(state="normal")
        self._separator()
        if speaker:
            self.text.insert("end", speaker, "speaker")
            if role:
                self.text.insert("end", "  ·  " + role, "role")
            self.text.insert("end", "\n", "speaker")
        if gesture:
            g = gesture.strip().rstrip(".")
            self.text.insert("end", g[:1].upper() + g[1:] + ".\n", "gesture")
        self.text.insert("end", text.strip() + "\n", "speech")
        self.text.configure(state="disabled")
        self.text.see("end")

    def add_player(self, text: str) -> None:
        self.text.configure(state="normal")
        self._separator()
        self.text.insert("end", "You\n", "you")
        self.text.insert("end", text.strip() + "\n", "player")
        self.text.configure(state="disabled")
        self.text.see("end")

    def add_note(self, text: str, tone: str = "note") -> None:
        self.text.configure(state="normal")
        text = text.strip()
        if tone in ("good", "bad") and text.startswith("• "):
            text = "◆ " + text[2:]
        self.text.insert("end", text + "\n", tone if tone in ("good", "bad") else "note")
        self.text.configure(state="disabled")
        self.text.see("end")

    def set_status(self, text: str) -> None:
        self.l_status.configure(text=text)

    def set_busy(self, text: str) -> None:
        self._busy = bool(text)
        self._busy_text = "" if text in ("", "…") else text.rstrip("…. ")
        self._busy_tick = 0
        self.set_status(self._busy_line() if self._busy else "")

    def _busy_line(self) -> str:
        dots = "·" * (self._busy_tick % 3 + 1)
        words = self._busy_text or "The court ponders"
        return f"✒  {words} {dots}"

    def _animate(self) -> None:
        try:
            if self._busy:
                self._busy_tick += 1
                self.set_status(self._busy_line())
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.root.after(420, self._animate)

    # ------------------------------------------------------- choices
    @staticmethod
    def _empty(frame: tk.Frame) -> None:
        """Remove a section's contents and give its room back. (Tk keeps a
        frame at its last size when its last child goes, leaving a hole.)"""
        for child in frame.winfo_children():
            child.destroy()
        frame.configure(height=1)

    def set_hint(self, text: str) -> None:
        """A small, quiet line above the buttons (e.g. when what is said takes effect)."""
        self.l_hint.configure(text=text)
        if text:
            self.l_hint.pack(fill="x", pady=(4, 0), before=self._button_row)
        else:
            self.l_hint.pack_forget()

    def set_suggestions(self, items: list[str], title: str = "Suggested replies - or write your own") -> None:
        self._sugg_items = list(items[:3])
        self._sugg_title = title
        self._sugg_loading = False
        self._draw_suggestions()

    def offer_suggestions(self, on: bool) -> None:
        """Suggested replies are written only when the ruler asks for them: a quiet line
        to ask, instead of three replies written (and paid for) after every answer."""
        self._sugg_offer = on
        self._sugg_items = []
        self._sugg_loading = False
        self._draw_suggestions()

    def _ask_suggestions(self) -> None:
        if self._sugg_loading:
            return
        self._sugg_loading = True
        self._draw_suggestions()
        self.cb.on_suggest()

    def _toggle_suggestions(self) -> None:
        self._sugg_open = not self._sugg_open
        self._draw_suggestions()

    def _draw_suggestions(self) -> None:
        self._empty(self.f_sugg)
        if not self._sugg_items:
            if self._sugg_offer:
                ask = tk.Label(self.f_sugg, text=("…  thinking of a few replies" if self._sugg_loading
                                                  else "\U0001F4A1  Suggest a few replies"),
                               fg=TEXT_DIM, bg=NAVY, font=self.art.hint, anchor="w",
                               cursor="" if self._sugg_loading else "hand2", padx=6, pady=3)
                ask.pack(fill="x", pady=(2, 2))
                if not self._sugg_loading:
                    ask.bind("<Button-1>", lambda _e: self._ask_suggestions())
                    ask.bind("<Enter>", lambda _e, w=ask: w.configure(fg=GOLD_HI))
                    ask.bind("<Leave>", lambda _e, w=ask: w.configure(fg=TEXT_DIM))
            return
        Bar(self.f_sugg, getattr(self, "_sugg_title", "Suggested replies"), font=self.art.bar, right="▾" if self._sugg_open else "▸",
            command=self._toggle_suggestions).pack(fill="x", pady=(2, 3))
        if not self._sugg_open:
            return
        for item in self._sugg_items:
            lab = tk.Label(self.f_sugg, text="›  " + item, fg=TEXT_DIM, bg=CARD, font=self.art.hint,
                           anchor="w", justify="left", wraplength=WIDTH - 70, cursor="hand2",
                           padx=10, pady=4, highlightthickness=1, highlightbackground=NAVY_LO)
            lab.pack(fill="x", pady=1)
            lab.bind("<Button-1>", lambda _e, t=item: self._send_text(t))
            lab.bind("<Enter>", lambda _e, w=lab: w.configure(fg=GOLD_HI, bg=CARD_HI,
                                                              highlightbackground=GOLD_DEEP))
            lab.bind("<Leave>", lambda _e, w=lab: w.configure(fg=TEXT_DIM, bg=CARD,
                                                              highlightbackground=NAVY_LO))

    def set_offers(self, offers: list[str]) -> None:
        self._empty(self.f_offers)
        if not offers:
            return
        Bar(self.f_offers, "Decisions on the table", font=self.art.bar, style="red").pack(fill="x", pady=(8, 3))
        for idx, label in enumerate(offers):
            Plate(self.f_offers, "⚔  " + label, lambda i=idx, l=label: self._confirm_offer(i, l),
                  font=self.art.btn, style="gold", wide=True).pack(fill="x", pady=2)

    def _confirm_offer(self, idx: int, label: str) -> None:
        self._empty(self.f_offers)
        Bar(self.f_offers, "Confirm", font=self.art.bar, style="red").pack(fill="x", pady=(8, 3))
        tk.Label(self.f_offers, text=f"{label}\nThere is no going back. Do you confirm?",
                 fg=TEXT, bg=NAVY, font=self.art.text, justify="left", anchor="w",
                 wraplength=WIDTH - 60).pack(fill="x", pady=(2, 6))
        row = tk.Frame(self.f_offers, bg=NAVY)
        row.pack(fill="x")
        Plate(row, "Yes, go ahead", lambda: (self.set_offers([]), self.cb.on_offer(idx)),
              font=self.art.btn, style="gold").pack(side="left")
        Plate(row, "No", lambda: self.cb.on_offer(-1), font=self.art.btn).pack(side="left", padx=8)

    def set_hub(self, items: list[tuple[str, str]]) -> None:
        """The Corte button: a list of things to do, each (key, label)."""
        self._empty(self.f_offers)
        if items:
            tk.Frame(self.f_offers, bg=NAVY, height=6).pack(fill="x")
        for key, label in items:
            Plate(self.f_offers, label, lambda k=key: self.cb.on_hub(k), font=self.art.btn,
                  wide=True).pack(fill="x", pady=2)

    # ---------------------------------------------------------- input
    def _show_placeholder(self) -> None:
        if self.entry.get("1.0", "end").strip() or self._placeholder:
            return
        try:
            if self.win.focus_get() is self.entry:
                return
        except (KeyError, tk.TclError):
            pass
        self._placeholder = True
        self.entry.configure(fg=TEXT_DIM, font=self.art.small)
        self.entry.insert("1.0", PLACEHOLDER)

    def _hide_placeholder(self) -> None:
        if self._placeholder:
            self._placeholder = False
            self.entry.delete("1.0", "end")
            self.entry.configure(fg=TEXT, font=self.art.text)

    def _on_return(self, event) -> str:
        if event.state & 0x0001:     # Shift+Enter: new line
            return ""
        self._send()
        return "break"

    def _send(self) -> None:
        if self._placeholder:
            return
        text = self.entry.get("1.0", "end").strip()
        if not text or self._busy:
            return
        self.entry.delete("1.0", "end")
        self._send_text(text)

    def _send_text(self, text: str) -> None:
        if self._busy:
            return
        self.set_suggestions([])
        self.add_player(text)
        self.cb.on_send(text)

    def _toggle_mic(self) -> None:
        if not self._listening:
            if self.cb.listen_start():
                self._listening = True
                self.b_mic.set_text("■  Stop")
                self.set_status("Listening… (the microphone is the Player2 app's)")
            else:
                self.set_status("Player2 did not start the microphone.")
            return
        self._listening = False
        self.b_mic.set_text("\U0001F399  Dictate")
        self.set_status("Transcribing…")
        heard = self.cb.listen_stop()
        self.set_status("")
        if heard:
            self._hide_placeholder()
            self.entry.insert("end", heard)
