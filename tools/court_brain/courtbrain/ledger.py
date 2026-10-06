"""The Court Brain window: settings, status and the record of what the court does.

A window in the game's manner, beside the game rather than over it. The
first time it opens it walks the player through the choices that shape the
campaign (the language the AI writes in, how often events come, how hard the
court is); afterwards it shows whether the game, the bridge and Player2
answer, which campaign and date the court is at, and the running record of
what Court Brain does. From here the player can also rewrite the AI's
instructions (see InstructionsEditor) and start EU5.

Everything written to the record also goes to court_brain.log in Court
Brain's folder. Closing the window ends Court Brain.
"""

from __future__ import annotations

import os
import queue
import webbrowser
import re
import time
import tkinter as tk
from pathlib import Path
from typing import Any, Callable

from . import prompts
from .drawer import (BAD, BLUE, CARD, CARD_HI, GOLD, GOLD_DEEP, GOLD_DIM, GOLD_HI, GOOD, INSET, NAVY, NAVY_HI,
                     NAVY_LO, TEXT, TEXT_DIM, Art, Bar, Plate, ThinScroll, dark_title_bar, gradient, inset, mix)

LOG_LIMIT = 2 * 1024 * 1024
MAX_LINES = 1500
WRAP = 560

FREQ_CHOICES = (
    ("off", "None", "Nothing new comes on its own. You still get the consequences of what you do, and the "
                    "stories already begun go on."),
    ("yearly", "Once a year", "About one new event a year of game time."),
    ("rare", "Rare", "About one new event every 140-200 days of game time (5 to 7 months)."),
    ("calm", "Calm", "About one new event every 90-140 days (3 to 5 months)."),
    ("normal", "Normal", "About one new event every 50-90 days (2 to 3 months). Recommended."),
    ("very", "Frequent", "About one new event every 30-50 days: a busy court."),
)
DIFF_CHOICES = (
    ("easy", "Easy", "A friendly court. People agree and forgive more readily; what hurts you weighs less "
                     "(major losses become moderate, moderate ones minor); risings need a truly desperate "
                     "realm."),
    ("normal", "Normal", "The court as designed: people weigh what you do as they really would, and costs "
                         "and gains are what the situation deserves. Recommended."),
    ("hard", "Hard", "A demanding court. People bargain hard and remember slights; minor losses become "
                     "moderate and the greatest gains are tempered; plots are subtler and crises start "
                     "sooner."),
    ("very_hard", "Very hard", "An unforgiving court. Every loss weighs one step more and every gain one step "
                               "less; rivals strike when you stumble, and crises start early."),
)
FREQ_NAMES = {k: n for k, n, _ in FREQ_CHOICES}
# Who writes: Player2 by default, or a cloud model with the player's own (free) key.
PROVIDER_CHOICES = (
    ("player2", "Player2 (default)", "The Player2 app you already use: nothing to set up. It spends its credits "
                                     "as you play; its free models cost nothing."),
    ("gemini", "Google Gemini - with your own free key", "Gemini through a key of your own from Google AI "
               "Studio: free within Google's limits (shown in AI Studio). gemini-3.1-flash-lite allows the most "
               "play a day for free; bigger Flash models write better but allow far fewer requests. On the free "
               "plan Google may use what is sent to improve its products."),
    ("openrouter", "OpenRouter - one key, hundreds of models", "Any model on OpenRouter (Gemini, DeepSeek, "
                   "Claude, GPT, Qwen, Mistral...) with one key of your own, paid with OpenRouter credits - a few "
                   "cents an evening with the default google/gemini-3.1-flash-lite; models ending in \":free\" "
                   "cost nothing but are slower and limited. Write the model as OpenRouter names it "
                   "(maker/model)."),
)
PROVIDER_NAMES = {k: n for k, n, _ in PROVIDER_CHOICES}
PROVIDER_KEY_URL = {"gemini": "https://aistudio.google.com/apikey", "mistral": "https://console.mistral.ai/api-keys",
                    "openrouter": "https://openrouter.ai/keys"}
PROVIDER_MODEL = {"gemini": "gemini-3.1-flash-lite", "mistral": "mistral-large-latest",
                  "openrouter": "google/gemini-3.1-flash-lite"}
DIFF_NAMES = {k: n for k, n, _ in DIFF_CHOICES}

_BAD = re.compile(r"warning|error|does not answer|traceback|dropped|not possible|not generated|failed|could not",
                  re.I)
_GOLD = re.compile(r"^(new story|outcome sent|the story|story|chronicle|decree|new name|campaign|you now play|"
                   r"a follow-up|\[)", re.I)
_GOOD = re.compile(r"works|answers at|started|ready|installed|up to date", re.I)


def _set_icon(win: tk.Toplevel, art: Art) -> None:
    ico = Path(__file__).with_name("icon.ico")
    try:
        if ico.is_file():
            win.iconbitmap(default=str(ico))       # also the taskbar's
        elif art.img("corner_tl") is not None:
            win.iconphoto(True, art.img("corner_tl"))
    except tk.TclError:
        pass


def _header(canvas: tk.Canvas, art: Art, title: str, subtitle: str, h: int) -> None:
    w = canvas.winfo_width()
    if w < 50:
        return
    canvas.delete("all")
    gradient(canvas, 0, 0, w, h, NAVY_HI, NAVY)
    orn = art.img("ornament")
    if orn is not None:
        canvas.create_image(w // 2, 26, image=orn)
    for name, x, anchor in (("corner_tl", 2, "nw"), ("corner_tr", w - 2, "ne")):
        img = art.img(name)
        if img is not None:
            canvas.create_image(x, 2, image=img, anchor=anchor)
    canvas.create_text(w // 2 + 1, 65, text=title, font=art.title, fill="#000000")
    canvas.create_text(w // 2, 64, text=title, font=art.title, fill=TEXT)
    canvas.create_text(w // 2, 92, text=subtitle, font=art.sub, fill=BLUE)
    canvas.create_line(0, h - 2, w, h - 2, fill=GOLD_DIM)
    canvas.create_line(0, h - 1, w, h - 1, fill="#06090e")


class Choice(tk.Frame):
    """A card to pick: a title and what it means. Gold edge when chosen."""

    def __init__(self, parent: tk.Misc, art: Art, title: str, text: str, command: Callable[[], None]) -> None:
        super().__init__(parent, bg=CARD, highlightthickness=1, highlightbackground=NAVY_LO, cursor="hand2",
                         padx=12, pady=6)
        self.chosen = False
        self.t = tk.Label(self, text=title, fg=TEXT, bg=CARD, font=art.bar, anchor="w", cursor="hand2")
        self.t.pack(fill="x")
        self.d = tk.Label(self, text=text, fg=TEXT_DIM, bg=CARD, font=art.note, anchor="w", justify="left",
                          wraplength=WRAP - 40, cursor="hand2")
        self.d.pack(fill="x")
        for w in (self, self.t, self.d):
            w.bind("<Button-1>", lambda _e: command())
            w.bind("<Enter>", lambda _e: self._paint(True))
            w.bind("<Leave>", lambda _e: self._paint(False))

    def set(self, chosen: bool) -> None:
        self.chosen = chosen
        self._paint(False)

    def _paint(self, hover: bool) -> None:
        bg = CARD_HI if (hover or self.chosen) else CARD
        self.configure(bg=bg, highlightbackground=GOLD if self.chosen else (GOLD_DEEP if hover else NAVY_LO))
        self.t.configure(bg=bg, fg=GOLD_HI if self.chosen else TEXT)
        self.d.configure(bg=bg)


class Ledger:
    TITLE = "Whispers in the Court - Court Brain"

    def __init__(self, root: tk.Tk, art: Art, *, cfg: Any, log_file: Path | None, data_dir: Path | None,
                 instructions_file: Path | None = None,
                 status: Callable[[], dict] | None = None, on_quit: Callable[[], None] | None = None,
                 on_frequency: Callable[[str], None] | None = None,
                 on_launch: Callable[[], None] | None = None,
                 save_option: Callable[[str, Any], None] | None = None,
                 on_provider: Callable[[], None] | None = None) -> None:
        self.root, self.art, self.cfg = root, art, cfg
        self.status_fn = status
        self.on_quit = on_quit
        self.on_frequency = on_frequency
        self.on_launch = on_launch
        self.on_provider = on_provider
        self.save_option = save_option or (lambda _k, _v: None)
        self.log_file = log_file
        self.data_dir = data_dir
        self.instructions_file = instructions_file
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._freq = ""
        self._editor: InstructionsEditor | None = None
        self.win = tk.Toplevel(root)
        self.win.title(self.TITLE)
        self.win.configure(bg=GOLD)
        self.win.geometry("660x760")
        self.win.minsize(520, 520)
        self.win.protocol("WM_DELETE_WINDOW", self._quit)
        _set_icon(self.win, art)
        outer = tk.Frame(self.win, bg="#06090e")
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        self.body = tk.Frame(outer, bg=NAVY)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)
        self.head = tk.Canvas(self.body, bg=NAVY_HI, height=110, highlightthickness=0, bd=0)
        self.head.pack(fill="x")
        self.head.bind("<Configure>", lambda _e: _header(self.head, art, "Whispers in the Court",
                                                         "Court Brain  ·  keep it open while you play", 110))
        self.page = tk.Frame(self.body, bg=NAVY)
        self.page.pack(fill="both", expand=True)
        self._build_main()
        dark_title_bar(self.win)
        self.root.after(100, self._drain)
        self.root.after(300, self._poll)

    # =============================================================== main view
    def _build_main(self) -> None:
        a = self.art
        self.main = tk.Frame(self.page, bg=NAVY)
        self.main.pack(fill="both", expand=True)
        chips = tk.Frame(self.main, bg=NAVY, padx=14, pady=8)
        chips.pack(fill="x")
        self.chips: dict[str, tuple[tk.Canvas, tk.Label]] = {}
        for i, (key, label) in enumerate((("game", "Game"), ("player2", "AI"), ("campaign", "Campaign"),
                                          ("date", "Date in game"), ("setup", "Your settings"))):
            cell = tk.Frame(chips, bg=NAVY)
            cell.grid(row=i, column=0, sticky="w", pady=1)
            dot = tk.Canvas(cell, width=12, height=12, bg=NAVY, highlightthickness=0)
            dot.pack(side="left", padx=(0, 6))
            tk.Label(cell, text=label + ":", fg=GOLD, bg=NAVY, font=a.kind).pack(side="left")
            val = tk.Label(cell, text="…", fg=TEXT, bg=NAVY, font=a.note)
            val.pack(side="left", padx=(6, 0))
            self.chips[key] = (dot, val)

        # How often something new happens on its own. What follows from the
        # ruler's deeds is not governed by this: the AI decides it.
        Bar(self.main, "Unprompted events", font=a.bar).pack(fill="x", padx=14, pady=(4, 4))
        freq = tk.Frame(self.main, bg=NAVY, padx=14)
        freq.pack(fill="x")
        row = tk.Frame(freq, bg=NAVY)
        row.pack(fill="x")
        self.freq_buttons: dict[str, Plate] = {}
        for key, label, _text in FREQ_CHOICES:
            b = Plate(row, label, lambda k=key: self._choose_freq(k), font=a.note)
            b.pack(side="left", padx=(0, 5), pady=(2, 4))
            self.freq_buttons[key] = b
        self.freq_note = tk.Label(freq, text="", fg=TEXT_DIM, bg=NAVY, font=a.small, justify="left", anchor="w",
                                  wraplength=WRAP)
        self.freq_note.pack(fill="x", pady=(0, 6))

        Bar(self.main, "Record of the court", font=a.bar).pack(fill="x", padx=14, pady=(4, 4))
        foot = tk.Frame(self.main, bg=NAVY_LO, padx=14, pady=8)
        foot.pack(side="bottom", fill="x")
        if self.on_launch:
            Plate(foot, "Start EU5", self.on_launch, font=a.btn, style="gold", bg=NAVY_LO).pack(side="left")
        Plate(foot, "Settings", lambda: self.show_setup(first=False), font=a.btn,
              bg=NAVY_LO).pack(side="left", padx=(8, 0))
        Plate(foot, "The AI", self.show_ai, font=a.btn, bg=NAVY_LO).pack(side="left", padx=(8, 0))
        Plate(foot, "AI instructions", self.open_instructions, font=a.btn, bg=NAVY_LO).pack(side="left", padx=8)
        Plate(foot, "Close", self._quit, font=a.btn, style="red", bg=NAVY_LO).pack(side="right")
        links = tk.Frame(self.main, bg=NAVY, padx=14)
        links.pack(side="bottom", fill="x", pady=(0, 4))
        for text, cmd in (("Open the memory folder", self._open_data), ("Open the full log", self._open_log)):
            lab = tk.Label(links, text=text, fg=BLUE, bg=NAVY, font=a.small, cursor="hand2")
            lab.pack(side="left", padx=(0, 16))
            lab.bind("<Button-1>", lambda _e, c=cmd: c())

        frame, inner = inset(self.main)
        frame.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.text = tk.Text(inner, bg=INSET, fg=TEXT, relief="flat", wrap="word", highlightthickness=0,
                            borderwidth=0, padx=10, pady=8, font=a.note, cursor="arrow", spacing1=1,
                            spacing3=2, selectbackground=GOLD_DEEP)
        scroll = ThinScroll(inner, self.text)
        scroll.pack(side="right", fill="y", padx=(0, 3), pady=6)
        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.bind("<MouseWheel>", lambda e: self.text.yview_scroll(int(-e.delta / 60), "units"))
        self.text.tag_configure("time", foreground=GOLD_DIM, font=a.small)
        self.text.tag_configure("msg", foreground=TEXT)
        self.text.tag_configure("gold", foreground=GOLD_HI)
        self.text.tag_configure("good", foreground=GOOD)
        self.text.tag_configure("bad", foreground=BAD)
        self.text.tag_configure("why", foreground=TEXT_DIM, font=a.small, lmargin1=58, lmargin2=58)
        self.text.tag_configure("sub", foreground=TEXT_DIM, lmargin1=58, lmargin2=58)
        self.text.configure(state="disabled")

    def _choose_freq(self, key: str) -> None:
        self._show_freq(key)
        if self.on_frequency:
            self.on_frequency(key)

    def _show_freq(self, key: str) -> None:
        if key == self._freq:
            return
        self._freq = key
        for k, b in self.freq_buttons.items():
            b.set_style("gold" if k == key else "blue")
        desc = next((t for k, _n, t in FREQ_CHOICES if k == key), "")
        self.freq_note.configure(text=desc + " The consequences of what you do always come, when and if the AI "
                                             "judges they would, and stories already begun go on.")

    # ============================================================ setup pages
    def show_setup(self, *, first: bool, on_done: Callable[[], None] | None = None) -> None:
        """The choices that shape the campaign: at the first start, or from Settings."""
        self.main.pack_forget()
        self._choices = {"language": prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language) or "English",
                         "frequency": self.cfg.event_frequency, "difficulty": self.cfg.difficulty,
                         "provider": getattr(self.cfg, "ai_provider", "player2") or "player2"}
        for p in PROVIDER_KEY_URL:
            self._choices[f"{p}_api_key"] = str(getattr(self.cfg, f"{p}_api_key", "") or "")
            self._choices[f"{p}_model"] = str(getattr(self.cfg, f"{p}_model", "") or PROVIDER_MODEL[p])
        self._setup_first, self._setup_done_cb, self._step = first, on_done, 0
        self._only_ai = False
        self.setup = tk.Frame(self.page, bg=NAVY)
        self.setup.pack(fill="both", expand=True)
        self._draw_step()

    def show_ai(self) -> None:
        """Straight to the AI that writes: which provider, the key, the model."""
        if getattr(self, "setup", None) is not None and self.setup.winfo_exists():
            self._close_setup()
        self.show_setup(first=False)
        self._only_ai, self._step = True, 3
        self._draw_step()

    def _draw_step(self) -> None:
        a = self.art
        for child in self.setup.winfo_children():
            child.destroy()
        steps = ("Language", "Events", "Difficulty", "The AI", "Your instructions")
        crumbs = tk.Frame(self.setup, bg=NAVY, padx=14, pady=8)
        crumbs.pack(fill="x")
        for i, name in enumerate(steps):
            if self._only_ai and i != 3:
                continue
            tk.Label(crumbs, text=f"{i + 1}. {name}", bg=NAVY, font=a.kind,
                     fg=GOLD_HI if i == self._step else (TEXT_DIM if i > self._step else GOLD_DIM)
                     ).pack(side="left", padx=(0, 16))
        nav = tk.Frame(self.setup, bg=NAVY_LO, padx=14, pady=8)
        nav.pack(side="bottom", fill="x")
        body = tk.Frame(self.setup, bg=NAVY, padx=14)
        body.pack(fill="both", expand=True)

        def para(text: str, *, dim: bool = True) -> None:
            tk.Label(body, text=text, fg=TEXT_DIM if dim else TEXT, bg=NAVY, font=a.note if dim else a.text,
                     justify="left", anchor="w", wraplength=WRAP).pack(fill="x", pady=(0, 8))

        if self._step == 0:
            Bar(body, "The language of the court", font=a.bar).pack(fill="x", pady=(2, 8))
            if self._setup_first:
                para("Welcome. A few choices before the court assembles; you can change all of them later "
                     "from Settings.", dim=False)
            para("In which language should the characters, the events, the chronicle and the advisor speak "
                 "to you? Write it in your own words - for example English, Italiano, Español, Français, "
                 "Deutsch, Polski, Português, Русский, 日本語.")
            box, box_in = inset(body)
            box.pack(fill="x", pady=(0, 8))
            self._lang = tk.Entry(box_in, bg=INSET, fg=TEXT, insertbackground=GOLD_HI, relief="flat",
                                  font=a.text, highlightthickness=0)
            self._lang.pack(fill="x", padx=10, pady=8)
            self._lang.insert(0, self._choices["language"])
            self._lang.focus_set()
            para("The interface of the mod and of this window stays in English. Any language the AI model "
                 "knows well will work; the most common ones work best.")
        elif self._step == 1:
            Bar(body, "Unprompted events", font=a.bar).pack(fill="x", pady=(2, 8))
            para("Besides answering what you do, the court brings you things on its own: affairs of "
                 "government, the ruler's daily life, the life of the realm, matters from abroad. How often?")
            self._cards = {}
            for key, name, text in FREQ_CHOICES:
                card = Choice(body, a, name, text, lambda k=key: self._pick("frequency", k))
                card.pack(fill="x", pady=2)
                self._cards[key] = card
            para("Game time: one month is about 30 days. A realm in trouble gets events a little more often. "
                 "The consequences of your own deeds are never limited by this: the AI decides if and when "
                 "they come, as the world would.")
            self._pick("frequency", self._choices["frequency"])
        elif self._step == 2:
            Bar(body, "Difficulty", font=a.bar).pack(fill="x", pady=(2, 8))
            para("How hard is the court? It changes how people treat you, how much the consequences of "
                 "your decisions weigh in the game, and how soon a troubled realm breeds plots and risings.")
            self._cards = {}
            for key, name, text in DIFF_CHOICES:
                card = Choice(body, a, name, text, lambda k=key: self._pick("difficulty", k))
                card.pack(fill="x", pady=2)
                self._cards[key] = card
            self._pick("difficulty", self._choices["difficulty"])
        elif self._step == 3:
            Bar(body, "The AI that writes", font=a.bar).pack(fill="x", pady=(2, 8))
            para("Court Brain uses Player2 by default. You can instead connect it to Google Gemini with a free "
                 "key of your own, or to OpenRouter (any of hundreds of models, with your own credits). Voices "
                 "and dictation still go through the Player2 app.")
            self._cards = {}
            for key, name, text in PROVIDER_CHOICES:
                card = Choice(body, a, name, text, lambda k=key: self._pick("provider", k))
                card.pack(fill="x", pady=2)
                self._cards[key] = card
            self._prov = tk.Frame(body, bg=NAVY)
            self._prov.pack(fill="x", pady=(6, 0))
            self._pick("provider", self._choices["provider"])
        else:
            Bar(body, "Your instructions (optional)", font=a.bar).pack(fill="x", pady=(2, 8))
            para("You can tell the AI in your own words how the characters should speak, what events you want, "
                 "how things should be narrated and what to avoid - and, if you like, rewrite the instructions "
                 "Court Brain gives it.", dim=False)
            para("It is entirely optional: the court works well as it is. You can do it now or at any time "
                 "with the \"AI instructions\" button.")
            Plate(body, "Open the AI instructions", self.open_instructions, font=a.btn).pack(anchor="w", pady=6)

        if self._step > 0 and not self._only_ai:
            Plate(nav, "Back", lambda: self._go(-1), font=a.btn, bg=NAVY_LO).pack(side="left")
        if not self._setup_first:
            Plate(nav, "Cancel", self._close_setup, font=a.btn, bg=NAVY_LO).pack(side="left", padx=8)
        last = self._step == len(steps) - 1 or self._only_ai
        Plate(nav, ("Assemble the court" if self._setup_first else "Save") if last else "Next",
              (self._finish if last else lambda: self._go(1)), font=a.btn, style="gold",
              bg=NAVY_LO).pack(side="right")

    def _pick(self, what: str, key: str) -> None:
        if what == "provider":
            self._keep_provider_fields()
        self._choices[what] = key
        for k, card in self._cards.items():
            card.set(k == key)
        if what == "provider":
            self._draw_provider_fields()

    def _draw_provider_fields(self) -> None:
        """For Gemini or Mistral: the player's key, the model, and where to get a free key."""
        a = self.art
        for child in self._prov.winfo_children():
            child.destroy()
        self._key_entry = self._model_entry = None
        p = self._choices["provider"]
        if p not in PROVIDER_KEY_URL:
            return
        link = tk.Label(self._prov, text=f"\u2192 Get your key here: {PROVIDER_KEY_URL[p]}", fg=BLUE, bg=NAVY,
                        font=a.small, cursor="hand2", anchor="w")
        link.pack(fill="x", pady=(0, 4))
        link.bind("<Button-1>", lambda _e, u=PROVIDER_KEY_URL[p]: webbrowser.open(u))
        for label, attr, secret in (("API key", "api_key", True), ("Model", "model", False)):
            row = tk.Frame(self._prov, bg=NAVY)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, fg=GOLD, bg=NAVY, font=a.kind, width=8, anchor="w").pack(side="left")
            box, box_in = inset(row)
            box.pack(side="left", fill="x", expand=True)
            entry = tk.Entry(box_in, bg=INSET, fg=TEXT, insertbackground=GOLD_HI, relief="flat", font=a.note,
                             highlightthickness=0, show="\u2022" if secret else "")
            entry.pack(fill="x", padx=8, pady=5)
            entry.insert(0, self._choices[f"{p}_{attr}"])
            if secret:
                self._key_entry = entry
            else:
                self._model_entry = entry
        tk.Label(self._prov, text="The key is kept only in Court Brain's settings on this computer. Leave the "
                                  "model as it is unless you want another one.", fg=TEXT_DIM, bg=NAVY, font=a.small,
                 justify="left", anchor="w", wraplength=WRAP).pack(fill="x", pady=(4, 0))

    def _keep_provider_fields(self) -> None:
        p = self._choices.get("provider", "")
        if p in PROVIDER_KEY_URL and getattr(self, "_key_entry", None) is not None:
            try:
                self._choices[f"{p}_api_key"] = self._key_entry.get().strip()
                self._choices[f"{p}_model"] = self._model_entry.get().strip() or PROVIDER_MODEL[p]
            except tk.TclError:
                pass

    def _go(self, delta: int) -> None:
        if self._step == 0:
            lang = self._lang.get().strip()
            if not lang:
                self._lang.configure(bg=mix(INSET, BAD, 0.25))
                return
            self._choices["language"] = lang
        if self._step == 3:
            self._keep_provider_fields()
            p = self._choices["provider"]
            if delta > 0 and p in PROVIDER_KEY_URL and not self._choices[f"{p}_api_key"]:
                if self._key_entry is not None:
                    self._key_entry.configure(bg=mix(INSET, BAD, 0.25))
                return
            self._key_entry = None
        self._step = max(0, min(4, self._step + delta))
        self._draw_step()

    def _finish(self) -> None:
        if self._step == 3:
            self._keep_provider_fields()
            p = self._choices["provider"]
            if p in PROVIDER_KEY_URL and not self._choices[f"{p}_api_key"]:
                if getattr(self, "_key_entry", None) is not None:
                    self._key_entry.configure(bg=mix(INSET, BAD, 0.25))
                return
        c = self._choices
        changed = False
        for opt in ("ai_provider", *(f"{p}_{f}" for p in PROVIDER_KEY_URL for f in ("api_key", "model"))):
            value = c["provider"] if opt == "ai_provider" else c[opt]
            if getattr(self.cfg, opt, None) != value:
                setattr(self.cfg, opt, value)
                self.save_option(opt, value)
                changed = True
        if changed and self.on_provider:
            self.on_provider()
        self.cfg.language = c["language"]
        self.cfg.difficulty = c["difficulty"]
        self.save_option("language", c["language"])
        self.save_option("difficulty", c["difficulty"])
        if self.on_frequency:
            self.on_frequency(c["frequency"])
        else:
            self.cfg.event_frequency = c["frequency"]
            self.save_option("event_frequency", c["frequency"])
        if not self.cfg.setup_done:
            self.cfg.setup_done = True
            self.save_option("setup_done", True)
        self.log(f"Settings: the AI writes in {c['language']}; unprompted events "
                 f"{FREQ_NAMES.get(c['frequency'], c['frequency']).lower()}; difficulty "
                 f"{DIFF_NAMES.get(c['difficulty'], c['difficulty']).lower()}; the AI: "
                 f"{PROVIDER_NAMES.get(c['provider'], c['provider']).split(' - ')[0].split(' (')[0]}.")
        done = self._setup_done_cb
        self._close_setup()
        if done:
            done()

    def _close_setup(self) -> None:
        self.setup.destroy()
        self.main.pack(fill="both", expand=True)

    def open_instructions(self) -> None:
        if self._editor is not None and self._editor.alive():
            self._editor.win.lift()
            return
        self._editor = InstructionsEditor(self.root, self.art, self.instructions_file, log=self.log)

    # ==================================================================== log
    def log(self, message: str) -> None:
        """Thread-safe."""
        self._lines.put(message)
        if self.log_file is not None:
            try:
                if self.log_file.is_file() and self.log_file.stat().st_size > LOG_LIMIT:
                    self.log_file.replace(self.log_file.with_suffix(".old.log"))
                with open(self.log_file, "a", encoding="utf-8") as fh:
                    fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
            except OSError:
                pass

    def _drain(self) -> None:
        added = False
        self.text.configure(state="normal")
        try:
            while True:
                self._write(self._lines.get_nowait())
                added = True
        except queue.Empty:
            pass
        if added:
            lines = int(self.text.index("end-1c").split(".")[0])
            if lines > MAX_LINES:
                self.text.delete("1.0", f"{lines - MAX_LINES}.0")
            self.text.see("end")
        self.text.configure(state="disabled")
        self.root.after(150, self._drain)

    def _write(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        first, *rest = msg.split("\n")
        stripped = first.strip()
        if first.startswith("  ") and stripped.startswith("why:"):
            self.text.insert("end", stripped + "\n", "why")
            return
        if first.startswith("  "):
            self.text.insert("end", stripped + "\n", ("sub", "bad") if _BAD.search(stripped) else "sub")
            return
        tone = "bad" if _BAD.search(first) else "gold" if _GOLD.search(first) else \
            "good" if _GOOD.search(first) else "msg"
        self.text.insert("end", stamp + "  ", "time")
        self.text.insert("end", first + "\n", tone)
        for line in rest:
            self.text.insert("end", line + "\n", "why")

    # ================================================================= status
    def _dot(self, key: str, color: str, text: str) -> None:
        dot, val = self.chips[key]
        dot.delete("all")
        dot.create_oval(2, 2, 10, 10, fill=color, outline=mix(color, "#000000", 0.45))
        val.configure(text=text)

    def _poll(self) -> None:
        try:
            s = self.status_fn() if self.status_fn else {}
        except Exception:
            s = {}
        if s:
            self._dot("game", GOOD if s.get("game") else GOLD_DIM,
                      "connected" if s.get("game") else "waiting for a game")
            p2, who = s.get("player2"), s.get("provider") or "Player2"
            model = f" ({s['model']})" if s.get("model") else ""
            self._dot("player2", GOOD if p2 else (BAD if p2 is False else GOLD_DIM),
                      f"{who}{model} answers" if p2 else (
                          (f"{who} does not answer - open the Player2 app" if who == "Player2" else
                           f"{who} does not answer - check the key in Settings") if p2 is False else f"{who} …"))
            camp = s.get("campaign") or ""
            self._dot("campaign", GOOD if camp else GOLD_DIM,
                      (camp + (f" · {s['memories']} memories" if s.get("memories") else "")) if camp else "-")
            if s.get("frequency"):
                self._show_freq(s["frequency"])
            nxt = s.get("next_event")
            when = ("" if nxt is None else "  ·  no unprompted events" if nxt < 0
                    else "  ·  an event may come now" if nxt == 0
                    else f"  ·  next event in ~{nxt} days")
            busy = s.get("busy")
            self._dot("date", GOLD if busy else (GOOD if s.get("date") else GOLD_DIM),
                      (s.get("date") or "-") + ("  ·  the court is writing" if busy else when))
        lang = prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)
        custom = prompts.custom()
        n = len(custom["additions"]) + len(custom["overrides"])
        self._dot("setup", GOLD, f"AI language {lang} · difficulty {DIFF_NAMES.get(self.cfg.difficulty, '?')}"
                                 + (f" · {n} custom instructions" if n else ""))
        self.root.after(1000, self._poll)

    # ================================================================ buttons
    def _open_data(self) -> None:
        if self.data_dir is not None and hasattr(os, "startfile"):
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                os.startfile(self.data_dir)          # noqa: S606 - the player's own folder
            except OSError:
                pass

    def _open_log(self) -> None:
        if self.log_file is not None and self.log_file.is_file() and hasattr(os, "startfile"):
            try:
                os.startfile(self.log_file)          # noqa: S606
            except OSError:
                pass

    def _quit(self) -> None:
        if self.on_quit:
            self.on_quit()
        self.root.quit()


# ==========================================================================
# The AI's instructions, in the player's hands
# ==========================================================================

class InstructionsEditor:
    """Write your own instructions for the AI, or rewrite the built-in ones.

    Changes take effect with the next thing the AI writes, and are kept in
    instructions.json. Court Brain still enforces what the AI may do in the
    game and the form of its answers, whatever the text says.
    """

    INTRO_ADD = ("Your own words, added to what the AI is told. Leave a box empty to add nothing. They "
                 "take effect with the next thing the AI writes.")
    INTRO_EDIT = ("The instructions Court Brain gives the AI, in full. Edit them to change how it writes. "
                  "The rules of the game - what may be proposed, how answers are formatted - are enforced "
                  "by Court Brain whatever the text says. \"Reset to default\" brings back the original.")

    def __init__(self, root: tk.Tk, art: Art, path: Path | None, *, log: Callable[[str], None]) -> None:
        self.art, self.path, self.log = art, path, log
        self.keys: list[tuple[str, str]] = []            # (kind, key) per list row; kind "" = header
        self.current: tuple[str, str] | None = None
        self.win = tk.Toplevel(root)
        self.win.title("AI instructions - Whispers in the Court")
        self.win.configure(bg=GOLD)
        self.win.geometry("980x680")
        self.win.minsize(760, 480)
        _set_icon(self.win, art)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        outer = tk.Frame(self.win, bg="#06090e")
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        body = tk.Frame(outer, bg=NAVY)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        left = tk.Frame(body, bg=NAVY, padx=12, pady=12, width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        Bar(left, "Sections", font=art.bar).pack(fill="x", pady=(0, 6))
        lframe, linner = inset(left)
        lframe.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(linner, bg=INSET, fg=TEXT, font=art.note, relief="flat", highlightthickness=0,
                                  selectbackground=GOLD_DEEP, selectforeground=GOLD_HI, activestyle="none",
                                  borderwidth=0, exportselection=False)
        self.listbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._select())
        self._row("", "YOUR INSTRUCTIONS")
        for key, (title, _help) in prompts.ADDITIONS.items():
            self._row("add", key, title)
        self._row("", "")
        self._row("", "BUILT-IN (ADVANCED)")
        for key, (title, _help, _default) in prompts.EDITABLE.items():
            self._row("edit", key, title)

        right = tk.Frame(body, bg=NAVY, padx=12, pady=12)
        right.pack(side="left", fill="both", expand=True)
        self.title = tk.Label(right, text="", fg=GOLD_HI, bg=NAVY, font=art.speaker, anchor="w")
        self.title.pack(fill="x")
        self.help = tk.Label(right, text="", fg=TEXT_DIM, bg=NAVY, font=art.note, anchor="w", justify="left",
                             wraplength=600)
        self.help.pack(fill="x", pady=(2, 8))
        foot = tk.Frame(right, bg=NAVY)
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        self.b_reset = Plate(foot, "Reset to default", self._reset, font=art.btn)
        self.b_reset.pack(side="left")
        Plate(foot, "Save", self._save, font=art.btn, style="gold").pack(side="right")
        self.state = tk.Label(foot, text="", fg=GOOD, bg=NAVY, font=art.small)
        self.state.pack(side="right", padx=12)
        eframe, einner = inset(right)
        eframe.pack(fill="both", expand=True)
        self.editor = tk.Text(einner, bg=INSET, fg=TEXT, insertbackground=GOLD_HI, relief="flat", wrap="word",
                              font=art.note, padx=10, pady=8, highlightthickness=0, undo=True,
                              selectbackground=GOLD_DEEP)
        scroll = ThinScroll(einner, self.editor)
        scroll.pack(side="right", fill="y", padx=(0, 3), pady=6)
        self.editor.pack(side="left", fill="both", expand=True)
        self.editor.configure(yscrollcommand=scroll.set)
        self.editor.bind("<<Modified>>", lambda _e: self._modified())
        dark_title_bar(self.win)
        self.listbox.selection_set(1)
        self._select()

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _row(self, kind: str, key: str, title: str = "") -> None:
        self.keys.append((kind, key))
        self.listbox.insert("end", ("   " + title) if kind else key)
        i = self.listbox.size() - 1
        if not kind:
            self.listbox.itemconfig(i, fg=GOLD, selectbackground=INSET, selectforeground=GOLD)

    def _select(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        kind, key = self.keys[sel[0]]
        if not kind:                                    # a heading: stay where we were
            if self.current:
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(self.keys.index(self.current))
            return
        if self.current and self.current != (kind, key):
            self._save(quiet=True)
        self.current = (kind, key)
        data = prompts.custom()
        if kind == "add":
            title, help_ = prompts.ADDITIONS[key]
            text = data["additions"].get(key, "")
            self.help.configure(text=help_ + "\n\n" + self.INTRO_ADD)
            self.b_reset.set_text("Clear")
        else:
            title, help_, default = prompts.EDITABLE[key]
            text = data["overrides"].get(key, default)
            self.help.configure(text=help_ + "\n\n" + self.INTRO_EDIT)
            self.b_reset.set_text("Reset to default")
        self.title.configure(text=title)
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.editor.edit_modified(False)
        self.editor.edit_reset()
        self._show_state()

    def _show_state(self, note: str = "") -> None:
        if not self.current:
            return
        kind, key = self.current
        data = prompts.custom()
        custom = key in (data["additions"] if kind == "add" else data["overrides"])
        self.state.configure(text=note or ("customised" if custom else ("empty" if kind == "add" else "default")),
                             fg=GOOD if note else (GOLD if custom else TEXT_DIM))

    def _modified(self) -> None:
        if self.editor.edit_modified():
            self.state.configure(text="not saved", fg=BAD)

    def _save(self, quiet: bool = False) -> None:
        if not self.current:
            return
        kind, key = self.current
        text = self.editor.get("1.0", "end").strip()
        data = prompts.custom()
        if kind == "add":
            data["additions"][key] = text
        elif text and text != prompts.EDITABLE[key][2].strip():
            data["overrides"][key] = text
        else:
            data["overrides"].pop(key, None)
        prompts.set_custom(data)
        if self.path is not None:
            try:
                prompts.save_custom(self.path)
            except OSError as exc:
                self.state.configure(text=f"could not save: {exc}", fg=BAD)
                return
        self.editor.edit_modified(False)
        if not quiet:
            self._show_state("saved")
            self.log(f"AI instructions saved: {self.title.cget('text')}.")

    def _reset(self) -> None:
        if not self.current:
            return
        kind, key = self.current
        self.editor.delete("1.0", "end")
        if kind == "edit":
            self.editor.insert("1.0", prompts.EDITABLE[key][2])
        self._save()

    def close(self) -> None:
        self._save(quiet=True)
        self.win.destroy()
