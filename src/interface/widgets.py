"""Small building blocks shared by the pages: buttons, cards, switches, tooltips and short messages."""
import logging
import tkinter as tk

import customtkinter as ctk

from src.interface import graphics as g
from src.interface import palette
from src.interface import theme as t

logger = logging.getLogger(__name__)


def is_dark():
    """True while the interface is shown in the dark theme (the system theme is already resolved by customtkinter)."""
    return ctk.get_appearance_mode() == "Dark"


def make_menu(master):
    """A pop-up menu in the colors of the theme (customtkinter has no menus of its own, so the colors are set by hand)."""
    dark = is_dark()
    return tk.Menu(
        master, tearoff=0, bd=0, font=(t.FONT, 11), bg=t.pick(t.SURFACE, dark), fg=t.pick(t.TEXT, dark),
        activebackground=t.pick(t.ACCENT, dark), activeforeground=t.pick(t.ACCENT_TEXT, dark),
        disabledforeground=t.pick(t.MUTED, dark),
    )


def popup_menu(menu, x, y):
    """Shows the menu at the point of the screen and lets go of the pointer when it is closed."""
    try:
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


def scale_of(widget):
    """The scaling of the interface (Windows display scaling and the scale chosen by the user: 1.0, 1.25 ...).

    customtkinter multiplies sizes and positions given to its widgets by this factor, while winfo_* and the
    geometry events report real pixels: they have to be divided by the factor before they are passed back."""
    try:
        return float(widget._get_widget_scaling())
    except Exception:
        return 1.0


def button_style(kind):
    """The colors of a kind of button; they are read when the button is made, so a new palette reaches new buttons."""
    if kind == "primary":
        return dict(fg_color=t.ACCENT, hover_color=t.ACCENT_HOVER, text_color=t.ACCENT_TEXT, border_width=0)
    if kind == "secondary":
        return dict(fg_color="transparent", hover_color=t.HOVER, text_color=t.TEXT, border_width=1,
                    border_color=t.BORDER)
    if kind == "soft":
        return dict(fg_color=t.ACCENT_SOFT, hover_color=t.ACCENT_SOFT, text_color=t.TEXT, border_width=0)
    return dict(fg_color="transparent", hover_color=t.HOVER, text_color=t.TEXT, border_width=0)


def make_button(master, text="", command=None, kind="primary", width=110, height=36, image=None, font_size=13,
                tooltip=None, **options):
    """A round button: kind is primary (filled), secondary (outlined), soft (tinted) or ghost (flat)."""
    style = button_style(kind)
    style.update(options)
    button = ctk.CTkButton(
        master, text=text, command=command, width=width, height=height, corner_radius=t.pill(height),
        font=(t.FONT, font_size), image=image, compound="left" if text else "center",
        **style,
    )
    if tooltip:
        button.tooltip = Tooltip(button, tooltip)
    return button


def make_icon_button(master, icon_name, command=None, size=40, color=None, tooltip=None, **options):
    """A round flat button with an icon only; set_icon() changes the picture."""
    color = color or t.TEXT
    style = dict(fg_color="transparent", hover_color=t.HOVER)
    style.update(options)
    button = ctk.CTkButton(
        master, text="", image=g.icon(icon_name, color, int(size * 0.5)), width=size, height=size,
        corner_radius=t.pill(size), command=command, **style,
    )
    button.icon_size = int(size * 0.5)

    def set_icon(name, new_color=None):
        button.configure(image=g.icon(name, new_color or color, button.icon_size))

    button.set_icon = set_icon
    if tooltip:
        button.tooltip = Tooltip(button, tooltip)
    return button


def make_progress(master, **options):
    return ctk.CTkProgressBar(master, height=12, corner_radius=t.pill(12), fg_color=t.BORDER,
                              progress_color=t.ACCENT_VIVID, **options)


def make_entry(master, placeholder="", height=40, **options):
    """A round input field whose border lights up in the accent color while the text is typed in it."""
    style = dict(
        height=height, corner_radius=t.pill(height), border_color=t.BORDER, fg_color=t.SURFACE_ALT, text_color=t.TEXT,
        placeholder_text_color=t.MUTED, placeholder_text=placeholder, font=(t.FONT, 13),
    )
    style.update(options)
    entry = ctk.CTkEntry(master, **style)
    if style.get("border_width", 2) and style.get("fg_color") != "transparent":
        entry.bind("<FocusIn>", lambda _e: entry.configure(border_color=t.ACCENT_VIVID), add="+")
        entry.bind("<FocusOut>", lambda _e: entry.configure(border_color=t.BORDER), add="+")
    return entry


def spacer(master, height):
    """An empty space at the end of a card: it stays a little away from the edge, otherwise it hides a bit of the border."""
    frame = ctk.CTkFrame(master, width=1, height=height, fg_color="transparent")
    frame.pack(pady=(0, 3))
    return frame


class Switch(ctk.CTkButton):
    """An on/off switch drawn with Pillow: the knob has a thin outline, so it is seen on any background
    (the switch of customtkinter has a plain knob, which disappears on a white card)."""

    SIZE = (46, 26)

    def __init__(self, master, command=None, value=False):
        self.value = bool(value)
        self.command = command
        super().__init__(master, text="", width=self.SIZE[0], height=self.SIZE[1], fg_color="transparent",
                         hover=False, border_width=0, corner_radius=0, image=self._picture(), command=self.toggle)

    def _picture(self):
        return g.switch_image(self.value, t.ACCENT_VIVID, palette.mix(t.pick(t.SURFACE, False), t.pick(t.MUTED, False), 0.5),
                              palette.mix(t.pick(t.SURFACE, True), t.pick(t.MUTED, True), 0.5), self.SIZE)

    def get(self):
        return self.value

    def set(self, value):
        self.value = bool(value)
        self.configure(image=self._picture())

    def toggle(self):
        self.set(not self.value)
        if self.command:
            self.command(self.value)


def make_switch(master, command=None, value=False):
    """A switch; command gets the new state (True or False); the state is in switch.value."""
    return Switch(master, command, value)


def make_slider(master, bounds, value, command=None, steps=None, release=None, **options):
    """A slider in the color of the accent. command(value) runs while it moves, release(value) when it is let go."""
    slider = ctk.CTkSlider(
        master, from_=bounds[0], to=bounds[1], number_of_steps=steps, progress_color=t.ACCENT_VIVID,
        button_color=t.ACCENT_VIVID, button_hover_color=t.ACCENT_VIVID, fg_color=t.BORDER, command=command, **options,
    )
    slider.set(value)
    if release is not None:
        slider.bind("<ButtonRelease-1>", lambda _e: release(slider.get()), add="+")
        slider.bind("<KeyRelease>", lambda _e: release(slider.get()), add="+")
    return slider


class Tooltip:
    """A small hint that appears when the pointer rests on a widget; text can be a string or a function."""

    DELAY_MS = 450

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def _schedule(self, _event=None):
        self.hide()
        self._job = self.widget.after(self.DELAY_MS, self.show)

    def show(self):
        self._job = None
        text = self.text() if callable(self.text) else self.text
        if not text or not self.widget.winfo_exists():
            return
        window = ctk.CTkToplevel(self.widget, fg_color=t.BORDER)
        window.withdraw()
        window.overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except Exception:
            pass
        ctk.CTkLabel(window, text=text, font=(t.FONT, 12), text_color=t.TEXT, fg_color=t.SURFACE,
                     corner_radius=t.r(8), padx=10, pady=5).pack(padx=1, pady=1)
        window.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - window.winfo_reqwidth() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        screen_w, screen_h = self.widget.winfo_screenwidth(), self.widget.winfo_screenheight()
        x = max(4, min(x, screen_w - window.winfo_reqwidth() - 4))
        if y + window.winfo_reqheight() > screen_h - 4:
            y = self.widget.winfo_rooty() - window.winfo_reqheight() - 8
        window.geometry(f"+{x}+{y}")
        window.deiconify()
        self.window = window

    def hide(self, _event=None):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None


class Toast:
    """A short message at the bottom of the window ("Voice selected"): it goes away by itself."""

    def __init__(self, root):
        self.root = root
        self.frame = None
        self._job = None

    def show(self, text, kind="info", milliseconds=2600):
        self.hide()
        color = {"ok": t.OK, "error": t.ERROR, "warn": t.WARN}.get(kind, t.ACCENT_VIVID)
        frame = ctk.CTkFrame(self.root, corner_radius=t.pill(44), fg_color=t.SURFACE, border_width=1,
                             border_color=t.BORDER)
        ctk.CTkFrame(frame, width=10, height=10, corner_radius=t.pill(10), fg_color=color).pack(
            side="left", padx=(18, 10), pady=17)
        ctk.CTkLabel(frame, text=text, font=(t.FONT, 13), text_color=t.TEXT).pack(side="left", padx=(0, 20))
        frame.place(relx=0.5, rely=1.0, y=-64, anchor="s")
        frame.lift()
        self.frame = frame
        self._job = self.root.after(milliseconds, self.hide)

    def hide(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self.frame is not None:
            try:
                self.frame.destroy()
            except Exception:
                pass
            self.frame = None


class Segmented(ctk.CTkFrame):
    """A row of buttons of which one is chosen: options are (key, caption) pairs, command gets the key."""

    def __init__(self, master, options, value, command=None, button_width=None, height=34):
        super().__init__(master, fg_color="transparent")
        self.command = command
        self.value = value
        self.buttons = {}
        for column, (key, caption) in enumerate(options):
            width = button_width or max(70, 14 + 8 * len(caption))
            button = make_button(self, caption, lambda k=key: self.choose(k), "secondary", width=width, height=height,
                                 font_size=12)
            button.grid(row=0, column=column, padx=(0 if column == 0 else 6, 0))
            self.buttons[key] = button
        self._paint()

    def _paint(self):
        for key, button in self.buttons.items():
            if key == self.value:
                button.configure(fg_color=t.ACCENT, hover_color=t.ACCENT_HOVER, text_color=t.ACCENT_TEXT,
                                 border_width=0)
            else:
                button.configure(fg_color="transparent", hover_color=t.HOVER, text_color=t.TEXT, border_width=1)

    def set(self, key):
        self.value = key
        self._paint()

    def choose(self, key):
        changed = key != self.value
        self.set(key)
        if changed and self.command:
            self.command(key)


class Swatch(ctk.CTkButton):
    """A round button filled with a color of an accent; the chosen one has a ring."""

    def __init__(self, master, accent, selected, command, tooltip=None, size=34):
        family = palette.accent_family(accent)
        vivid = palette.build_scheme(family)["ACCENT_VIVID"]
        self.accent = accent
        super().__init__(
            master, text="", width=size, height=size, corner_radius=t.pill(size), fg_color=vivid,
            hover_color=vivid, border_width=3 if selected else 0, border_color=t.TEXT, command=command,
            image=g.icon("check", ("#FFFFFF", "#101010"), 16) if selected else None,
        )
        if tooltip:
            self.tooltip = Tooltip(self, tooltip)


class CollapsingLabel(ctk.CTkLabel):
    """A label that takes no room while it has no text (an empty label is still a few lines tall)."""

    def __init__(self, master, pack_options, siblings, **options):
        super().__init__(master, **options)
        self._pack_options = pack_options
        # All collapsing labels of the card in their order: they are packed again together to keep it
        self._siblings = siblings
        siblings.append(self)
        self._shown = False

    def configure(self, require_redraw=False, **kwargs):
        super().configure(require_redraw=require_redraw, **kwargs)
        if "text" in kwargs:
            self.sync()

    def sync(self):
        shown = bool(self.cget("text"))
        if shown == self._shown:
            return
        self._shown = shown
        # They are the last widgets of the card, so packing them again in order keeps the order
        for label in self._siblings:
            label.pack_forget()
        for label in self._siblings:
            if label._shown:
                label.pack(**label._pack_options)


class Card(ctk.CTkFrame):
    """A rounded block with a title and a description; its texts re-wrap when the window is resized."""

    def __init__(self, master, title, description=""):
        super().__init__(master, corner_radius=t.r(18), fg_color=t.SURFACE, border_width=1, border_color=t.BORDER)
        self._wrapped = []
        self._collapsing = []
        ctk.CTkLabel(self, text=title, font=(t.FONT, 16, "bold"), text_color=t.TEXT, anchor="w").pack(
            fill="x", padx=22, pady=(18, 0)
        )
        if description:
            self.add_text(description, color=t.MUTED, size=12, pady=(3, 12))
        else:
            spacer(self, 8)
        self.bind("<Configure>", self._rewrap, add="+")

    # A label that follows the width of the card
    # collapse=True: the label is hidden while it is empty (only for the labels at the end of the card)
    def add_text(self, text="", color=t.TEXT, size=13, bold=False, pady=(0, 0), collapse=False):
        options = dict(
            text=text, font=(t.FONT, size, "bold") if bold else (t.FONT, size), text_color=color,
            anchor="w", justify="left", wraplength=500, height=0,
        )
        pack_options = dict(fill="x", padx=22, pady=pady)
        if collapse:
            label = CollapsingLabel(self, pack_options, self._collapsing, **options)
            label.sync()
        else:
            label = ctk.CTkLabel(self, **options)
            label.pack(**pack_options)
        self._wrapped.append(label)
        return label

    def _rewrap(self, event):
        width = max(200, int(event.width / scale_of(self)) - 48)
        for label in self._wrapped:
            if abs(int(label.cget("wraplength")) - width) > 2:
                label.configure(wraplength=width)


class StatusLight(ctk.CTkFrame):
    """A colored dot with a caption: the way the status bar shows AI, STT and TTS."""

    def __init__(self, master, caption):
        super().__init__(master, corner_radius=t.pill(26), fg_color=t.SURFACE_ALT, height=26)
        self.state = "unknown"
        self.dot = ctk.CTkFrame(self, width=9, height=9, corner_radius=t.pill(9), fg_color=t.OFF)
        self.dot.pack(side="left", padx=(10, 6), pady=8)
        ctk.CTkLabel(self, text=caption, font=(t.FONT, 11, "bold"), text_color=t.TEXT).pack(side="left", padx=(0, 10))

    def set_state(self, state):
        self.state = state if state in t.SERVICE_COLORS else "unknown"
        self.dot.configure(fg_color=t.SERVICE_COLORS[self.state])


class SettingRow(ctk.CTkFrame):
    """A line of the settings: the name and a hint on the left, the control on the right (or under them)."""

    def __init__(self, master, title, hint="", stacked=False):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        texts = ctk.CTkFrame(self, fg_color="transparent")
        texts.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(texts, text=title, font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w").pack(anchor="w")
        if hint:
            ctk.CTkLabel(texts, text=hint, font=(t.FONT, 12), text_color=t.MUTED, anchor="w", justify="left",
                         wraplength=560 if stacked else 340).pack(anchor="w")
        self.control = ctk.CTkFrame(self, fg_color="transparent")
        if stacked:
            self.control.grid(row=1, column=0, sticky="w", pady=(10, 0))
        else:
            self.control.grid(row=0, column=1, sticky="e", padx=(16, 0))


def divider(master, padx=22, pady=(10, 10)):
    """A thin line between the rows of a card."""
    line = ctk.CTkFrame(master, height=1, fg_color=t.BORDER)
    line.pack(fill="x", padx=padx, pady=pady)
    return line


def page_header(master, title, subtitle):
    """The title of a page; returns the frame so a page can add a button on its right side."""
    header = ctk.CTkFrame(master, fg_color="transparent")
    header.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(header, text=title, font=(t.FONT, 26, "bold"), text_color=t.TEXT, anchor="w").grid(
        row=0, column=0, sticky="w"
    )
    hint = ctk.CTkLabel(header, text=subtitle, font=(t.FONT, 13), text_color=t.MUTED, anchor="w", justify="left")
    hint.grid(row=1, column=0, sticky="w")
    state = {"width": 0}

    def fit(_event=None):
        """In a narrow window the hint goes to the next line instead of running under the button on the right."""
        taken = 0
        for child in header.grid_slaves():
            if int(child.grid_info().get("column", 0)) > 0:
                taken += child.winfo_reqwidth() + 16
        room = header.winfo_width() - taken
        if room < 60 or abs(room - state["width"]) < 3:
            return
        state["width"] = room
        try:
            hint.configure(wraplength=int(room / scale_of(header)))
        except Exception:
            logger.debug("Не удалось уместить подпись страницы", exc_info=True)

    header.bind("<Configure>", fit, add="+")
    return header


# Width of the content of a page: on a wide window it stays in the middle instead of stretching to the edges
CONTENT_MAX_WIDTH = 920
CONTENT_MARGIN = 24


def center_content(page, container):
    """Keeps everything gridded into the container in a centered column of limited width."""
    state = {"side": None}

    def update(_event=None):
        factor = scale_of(page)
        side = max(int(CONTENT_MARGIN * factor), (page.winfo_width() - int(CONTENT_MAX_WIDTH * factor)) // 2)
        if side == state["side"]:
            return
        state["side"] = side
        for child in container.grid_slaves():
            child.grid_configure(padx=side)

    page.bind("<Configure>", update, add="+")
