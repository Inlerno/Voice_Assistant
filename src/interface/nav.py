"""The parts of the window around the pages: the side menu (it can be folded), the top menu and the status bar.

Every one of them is built once from the current palette; when the look is changed the whole window is built again
(see VoiceAssistantApp.rebuild), so they only have to be able to show a page as the open one and a state of the
assistant.
"""
import customtkinter as ctk

from src.interface import graphics as g
from src.interface import theme as t
from src.interface.orb import OrbView
from src.interface.widgets import StatusLight, Tooltip, is_dark, make_icon_button, scale_of

SIDEBAR_WIDTH = 244
SIDEBAR_FOLDED_WIDTH = 78
TOPBAR_HEIGHT = 64
STATUSBAR_HEIGHT = 40
ICON_SIZE = 20


def shortcut_of(page_key):
    """The keys that open a page: Ctrl and the number of the page in the list of pages."""
    keys = [key for key, _, _ in t.PAGES]
    return f"Ctrl+{keys.index(page_key) + 1}"


def hline(master):
    """A thin line of the color of the borders."""
    return ctk.CTkFrame(master, height=1, corner_radius=0, fg_color=t.BORDER)


def click_target(widgets, command):
    """Makes the widgets and everything inside them react to a click."""
    for widget in widgets:
        try:
            widget.configure(cursor="hand2")
        except Exception:
            pass
        widget.bind("<Button-1>", lambda _e: command(), add="+")


class Sidebar(ctk.CTkFrame):
    """The menu at the left: the logo, search, the pages in groups and the card with the state of the assistant.

    On a low window it does not fit: then it is made again in a compact form (lower lines, no button to fold it)."""

    # The height (in units of the interface) that the full form needs; a lower window gets the compact form
    FULL_HEIGHT = 690

    def __init__(self, master, app, folded=False, state="idle"):
        super().__init__(master, width=SIDEBAR_FOLDED_WIDTH if folded else SIDEBAR_WIDTH, corner_radius=0,
                         fg_color=t.SURFACE)
        self.app = app
        self.folded = folded
        self.compact = False
        self.items = {}
        self.orb = None
        self.state_title = self.state_hint = None
        self.active = None
        self.shown_state = (state, "", "")
        self.pack_propagate(False)
        self._build()
        # The line along the right edge separates the menu from the page
        edge = ctk.CTkFrame(self, width=1, corner_radius=0, fg_color=t.BORDER)
        edge._is_edge = True
        edge.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        self._remake_job = None
        self.bind("<Configure>", self._on_resize, add="+")

    # ---------- Building ----------

    def _build(self):
        side = 10 if self.folded else 12
        compact = self.compact
        logo = ctk.CTkLabel(
            self, text="" if self.folded else " Astra", image=g.icon("sparkle", t.ACCENT_VIVID, 24),
            compound="left", font=(t.FONT, 21, "bold"), text_color=t.TEXT, anchor="w",
        )
        logo_pad = (10, 8) if compact else (20, 14)
        if self.folded:
            logo.pack(pady=logo_pad)
        else:
            logo.pack(fill="x", padx=side + 10, pady=logo_pad)

        search_height = 34 if compact else 40
        search = ctk.CTkButton(
            self, text="" if self.folded else "Поиск", image=g.icon("search", t.MUTED, 18),
            compound="left", anchor="center" if self.folded else "w", height=search_height,
            corner_radius=t.pill(search_height), font=(t.FONT, 13), fg_color=t.SURFACE_ALT, hover_color=t.HOVER,
            text_color=t.MUTED, border_width=1, border_color=t.BORDER, command=self.app.open_palette,
            border_spacing=10,
        )
        search.pack(fill="x", padx=side, pady=(0, 6 if compact else 12))
        search.tooltip = Tooltip(search, "Поиск и команды · Ctrl+K")

        titles = {key: (title, icon) for key, title, icon in t.PAGES}
        last = len(t.NAV_SECTIONS) - 1
        for index, section in enumerate(t.NAV_SECTIONS):
            if index == last:
                # What is left of the height goes here, so the last group and the card sit at the bottom
                ctk.CTkFrame(self, fg_color="transparent", width=1, height=1).pack(fill="both", expand=True)
            elif index > 0:
                hline(self).pack(fill="x", padx=side + 6, pady=4 if compact else 8)
            for key in section:
                self._add_item(key, *titles[key], side)

        hline(self).pack(fill="x", padx=side + 6, pady=(4, 6) if compact else (8, 10))
        self._build_state_card(side)

        if not compact:
            fold = ctk.CTkButton(
                self, text="" if self.folded else "Свернуть меню", image=g.icon("sidebar", t.MUTED, 18),
                compound="left", anchor="center" if self.folded else "w", height=36,
                corner_radius=t.r(12, 18), font=(t.FONT, 12), fg_color="transparent", hover_color=t.HOVER,
                text_color=t.MUTED, command=self.app.toggle_sidebar, border_spacing=10,
            )
            fold.pack(fill="x", padx=side, pady=(0, 12))
            fold.tooltip = Tooltip(fold, "Развернуть меню" if self.folded else "Свернуть меню")
        else:
            ctk.CTkFrame(self, fg_color="transparent", width=1, height=6).pack()

    def _add_item(self, key, title, icon_name, side):
        height = 34 if self.compact else 42
        button = ctk.CTkButton(
            self, text="" if self.folded else title, image=g.icon(icon_name, t.MUTED, ICON_SIZE),
            compound="left", anchor="center" if self.folded else "w", height=height,
            corner_radius=t.r(12, height // 2), font=(t.FONT, 14), fg_color="transparent", hover_color=t.HOVER,
            text_color=t.MUTED, command=lambda: self.app.show_page(key), border_spacing=12,
        )
        button.pack(fill="x", padx=side, pady=1 if self.compact else 2)
        button.tooltip = Tooltip(button, f"{title} · {shortcut_of(key)}")
        self.items[key] = (button, icon_name)

    def _build_state_card(self, side):
        dark = is_dark()
        animated = self.app.prefs.animations
        state = self.shown_state[0]
        pad = (0, 4) if self.compact else (0, 8)
        if self.folded:
            self.orb = OrbView(self, 56, t.pick(t.SURFACE, dark), state=state, dark=dark, animated=animated)
            self.orb.pack(pady=(0, 2) if self.compact else (0, 6))
            click_target([self.orb], self.app.toggle_listening)
            self.orb.tooltip = Tooltip(self.orb, self.app.state_tooltip)
            return
        card = ctk.CTkFrame(self, corner_radius=t.r(16), fg_color=t.SURFACE_ALT, border_width=1,
                            border_color=t.BORDER)
        card.pack(fill="x", padx=side, pady=pad)
        card.grid_columnconfigure(1, weight=1)
        self.orb = OrbView(card, 64, t.pick(t.SURFACE_ALT, dark), state=state, dark=dark, animated=animated)
        self.orb.grid(row=0, column=0, rowspan=2, padx=(6, 0), pady=6)
        self.state_title = ctk.CTkLabel(card, text="", font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w")
        self.state_title.grid(row=0, column=1, sticky="sw", padx=(2, 10), pady=(10, 0))
        self.state_hint = ctk.CTkLabel(card, text="", font=(t.FONT, 11), text_color=t.MUTED, anchor="nw",
                                       justify="left", wraplength=118)
        self.state_hint.grid(row=1, column=1, sticky="nw", padx=(2, 10), pady=(0, 10))
        click_target([card, self.orb, self.state_title, self.state_hint], self.app.toggle_listening)
        card.tooltip = Tooltip(card, self.app.state_tooltip)

    # ---------- A low window ----------

    def _on_resize(self, event):
        compact = event.height / scale_of(self) < self.FULL_HEIGHT
        if compact != self.compact and self._remake_job is None:
            self.compact = compact
            self._remake_job = self.after(30, self._remake)

    def _remake(self):
        """Builds the content again in the form that fits (the frame itself and its line stay)."""
        self._remake_job = None
        for child in self.winfo_children():
            if not getattr(child, "_is_edge", False):
                child.destroy()
        self.items = {}
        self._build()
        self.set_active(self.active)
        self.set_state(*self.shown_state)

    # ---------- What is shown ----------

    def set_active(self, key):
        self.active = key
        for name, (button, icon_name) in self.items.items():
            on = name == key
            button.configure(
                fg_color=t.ACCENT_SOFT if on else "transparent", hover_color=t.ACCENT_SOFT if on else t.HOVER,
                text_color=t.TEXT if on else t.MUTED, font=(t.FONT, 14, "bold") if on else (t.FONT, 14),
                image=g.icon(icon_name, t.ACCENT_VIVID if on else t.MUTED, ICON_SIZE),
            )

    def set_state(self, state, title, hint):
        self.shown_state = (state, title, hint)
        self.orb.configure_orb(state=state)
        if self.state_title is not None:
            self.state_title.configure(text=title)
            self.state_hint.configure(text=hint)

    def set_animated(self, animated):
        self.orb.configure_orb(animated=animated)


class TopNav(ctk.CTkFrame):
    """The menu at the top: the logo, the pages in the middle (the captions are dropped in a narrow window)."""

    def __init__(self, master, app):
        super().__init__(master, fg_color=t.BG, corner_radius=0, height=TOPBAR_HEIGHT)
        self.app = app
        self.tabs = {}
        self.titles = {key: title for key, title, _ in t.PAGES}
        self.compact = False
        self.active = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_propagate(False)
        self.logo = ctk.CTkLabel(self, text=" Astra", font=(t.FONT, 19, "bold"), text_color=t.TEXT,
                                 image=g.icon("sparkle", t.ACCENT_VIVID, 22), compound="left")
        self.logo.grid(row=0, column=0, padx=(26, 10), sticky="w")
        self.strip = ctk.CTkFrame(self, fg_color="transparent")
        for column, (key, title, icon_name) in enumerate(t.PAGES):
            button = ctk.CTkButton(
                self.strip, text=title, image=g.icon(icon_name, t.MUTED, 18), compound="left", height=38,
                corner_radius=t.pill(38), font=(t.FONT, 13), fg_color="transparent", hover_color=t.HOVER,
                text_color=t.MUTED, width=40, command=lambda page=key: self.app.show_page(page),
            )
            button.grid(row=0, column=column, padx=2)
            button.tooltip = Tooltip(button, f"{title} · {shortcut_of(key)}")
            self.tabs[key] = (button, icon_name)
        search = make_icon_button(self, "search", self.app.open_palette, size=40, color=t.MUTED,
                                  tooltip="Поиск и команды · Ctrl+K")
        search.grid(row=0, column=2, padx=(10, 22), sticky="e")
        hline(self).place(relx=0, rely=1.0, relwidth=1.0, anchor="sw")
        self.strip.update_idletasks()
        self.full_width = self.strip.winfo_reqwidth()
        self.bind("<Configure>", lambda _e: self.center_tabs())
        self.center_tabs()

    def center_tabs(self):
        """The tabs are in the middle of the window, the logo stays at the left. When the window is too narrow
        for all the captions, only the open tab keeps its caption."""
        factor = scale_of(self)
        logo_width = self.logo.winfo_reqwidth() + int(36 * factor)
        compact = self.winfo_width() < logo_width + self.full_width + int(90 * factor)
        if compact != self.compact:
            self.compact = compact
            self.paint()
            self.strip.update_idletasks()
        half = self.strip.winfo_reqwidth() // 2
        x = max(self.winfo_width() // 2, logo_width + half)
        # place() multiplies the position by the display scaling itself
        self.strip.place(x=x / factor, rely=0.5, anchor="center")

    def set_active(self, key):
        self.active = key
        self.paint()

    def paint(self):
        for key, (button, icon_name) in self.tabs.items():
            on = key == self.active
            button.configure(
                text=self.titles[key] if on or not self.compact else "",
                fg_color=t.ACCENT_SOFT if on else "transparent", hover_color=t.ACCENT_SOFT if on else t.HOVER,
                text_color=t.TEXT if on else t.MUTED, font=(t.FONT, 13, "bold") if on else (t.FONT, 13),
                image=g.icon(icon_name, t.ACCENT_VIVID if on else t.MUTED, 18),
            )


class StatusBar(ctk.CTkFrame):
    """The bar at the bottom: the state (when there is no side menu with its card), the microphone, AI, STT and TTS."""

    def __init__(self, master, app, show_state, state="idle"):
        super().__init__(master, fg_color=t.BG, corner_radius=0, height=STATUSBAR_HEIGHT)
        self.app = app
        self.orb = None
        self.grid_propagate(False)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        hline(self).place(relx=0, rely=0, relwidth=1.0, anchor="nw")
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=(18, 0))
        self.title = ctk.CTkLabel(left, text="", font=(t.FONT, 13, "bold"), text_color=t.TEXT)
        self.hint = ctk.CTkLabel(left, text="", font=(t.FONT, 12), text_color=t.MUTED)
        if show_state:
            dark = is_dark()
            self.orb = OrbView(left, 32, t.pick(t.BG, dark), state=state, dark=dark, animated=app.prefs.animations)
            self.orb.pack(side="left", pady=2)
            self.title.pack(side="left", padx=(4, 8))
            self.hint.pack(side="left")
        else:
            self.hint.configure(text="Ctrl+K — поиск и команды")
            self.hint.pack(side="left", padx=4)

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e", padx=(0, 18))
        self.mic = ctk.CTkLabel(right, text="", font=(t.FONT, 12), text_color=t.MUTED, anchor="e")
        self.mic.pack(side="left", padx=(0, 14))
        self.lights = {}
        for key, caption in t.SERVICES:
            light = StatusLight(right, caption)
            light.pack(side="left", padx=3, pady=7)
            self.lights[key] = light

    def set_state(self, state, title, hint):
        if self.orb is not None:
            self.orb.configure_orb(state=state)
            self.title.configure(text=title)
            self.hint.configure(text=hint)

    def set_animated(self, animated):
        if self.orb is not None:
            self.orb.configure_orb(animated=animated)
