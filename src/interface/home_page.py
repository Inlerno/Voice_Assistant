"""The home page: a welcome screen with the living orb that turns into the dialog with the assistant."""
import math
import tkinter as tk
from datetime import datetime

import customtkinter as ctk
from PIL import Image, ImageTk

from src.interface import graphics as g
from src.interface import theme as t
from src.interface import wallpaper
from src.interface.orb import OrbPlayer, R_FRACTION
from src.interface.widgets import is_dark, make_button, make_menu, popup_menu, scale_of

# Width of the dialog column on a wide window, in units of the interface
CONTENT_WIDTH = 860
PLACEHOLDER = "Спросите о чём угодно…"
CAPABILITIES_QUESTION = "Что ты умеешь?"
# The picture of the orb with its glow and rings, in units of the interface (the ball itself is about half of it)
ORB_SIZE = 244
# Where the parts of the welcome screen are above the input field (in units): the orb, the wordmark, the greeting
ORB_OFFSET = -172
WORDMARK_OFFSET = -128
GREETING_OFFSET = {"orb": -64, "logo": -66, "minimal": -52}
HISTORY_TIME_FORMAT = "%H:%M"
# The wallpaper dies out FADE_GAP units above the input field, over FADE_LENGTH of the height of the page
FADE_GAP = 40
FADE_LENGTH = 0.34


def greeting(now=None, name=""):
    hour = (now or datetime.now()).hour
    if hour < 5:
        text = "Доброй ночи"
    elif hour < 12:
        text = "Доброе утро"
    elif hour < 18:
        text = "Добрый день"
    else:
        text = "Добрый вечер"
    name = " ".join((name or "").split())
    return f"{text}, {name}!" if name else f"{text}!"


class HeroCanvas(tk.Canvas):
    """The background of the welcome screen with what stands on it: the orb or the wordmark and the greeting.

    A canvas is used (and not widgets) so the glow, the wallpaper and the orb can lie one over another with soft edges."""

    def __init__(self, master, app):
        super().__init__(master, highlightthickness=0, bd=0)
        self.app = app
        self.factor = scale_of(master)
        self.dark = is_dark()
        self._photo = None
        self._spark = None
        self._redraw_job = None
        self._shown = True
        self.background = self.create_image(0, 0, anchor="nw")
        self.player = OrbPlayer(self, 0, 0, int(ORB_SIZE * self.factor), state=app.state, dark=self.dark,
                                animated=app.prefs.animations)
        self.wordmark = self.create_text(0, 0, text="Astra", anchor="center")
        self.spark = self.create_image(0, 0, anchor="center")
        self.greeting = self.create_text(0, 0, anchor="center")
        # The ball answers to a click like the card of the state in the menu does: it switches the microphone
        self.tag_bind(self.player.item, "<Button-1>", self._on_orb_click)
        self.tag_bind(self.player.item, "<Motion>", self._on_orb_motion)
        self.tag_bind(self.player.item, "<Leave>", lambda _e: self.configure(cursor=""))
        self.bind("<Configure>", self._schedule_redraw)
        self.apply_prefs()

    # The vertical center of the welcome screen (in the units of the page): the input field sits here
    @staticmethod
    def center_y(height):
        return max(int(height * 0.52), 262)

    def _on_ball(self, event):
        x, y = self.coords(self.player.item)
        return math.hypot(event.x - x, event.y - y) <= self.player.size * R_FRACTION * 1.25

    def _on_orb_motion(self, event):
        self.configure(cursor="hand2" if self._on_ball(event) else "")

    def _on_orb_click(self, event):
        if self._on_ball(event):
            self.app.toggle_listening()

    # ---------- What is shown ----------

    def apply_prefs(self):
        prefs = self.app.prefs
        hero = prefs.hero
        self.itemconfigure(self.player.item, state="normal" if hero == "orb" else "hidden")
        for item in (self.wordmark, self.spark):
            self.itemconfigure(item, state="normal" if hero == "logo" else "hidden")
        self.player.configure(animated=prefs.animations)
        self._sync_visibility()
        self.itemconfigure(self.wordmark, fill=t.pick(t.TEXT, self.dark),
                           font=(t.FONT, -int(76 * self.factor), "bold"))
        size = 30 if hero == "minimal" else 26
        self.itemconfigure(self.greeting, fill=t.pick(t.TEXT, self.dark), font=(t.FONT, -int(size * self.factor), "bold"))
        self.refresh_greeting()
        self._spark = ImageTk.PhotoImage(g.sparkle_image(int(34 * self.factor), t.pick(t.ACCENT_VIVID, self.dark)))
        self.itemconfigure(self.spark, image=self._spark)
        self._redraw()

    def set_state(self, state):
        self.player.configure(state=state)

    def set_shown(self, shown):
        self._shown = shown
        self._sync_visibility()

    def _sync_visibility(self):
        self.player.set_visible(self._shown and self.app.prefs.hero == "orb")

    def refresh_greeting(self):
        self.itemconfigure(self.greeting, text=greeting(name=self.app.prefs.user_name))

    # ---------- Drawing ----------

    def _schedule_redraw(self, _event=None):
        # Resizing a window sends many events, the picture is rebuilt once the window settles
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(80, self._redraw)

    def _background_image(self, width, height, centre):
        prefs = self.app.prefs
        base = t.pick(t.BG, self.dark)
        if prefs.background == "flat":
            return None
        if prefs.background == "image" and prefs.has_wallpaper():
            picture = wallpaper.render_file(prefs.wallpaper_path, width, height, prefs.wallpaper_dim,
                                            prefs.wallpaper_blur * self.factor, base)
            if picture is not None:
                # The picture dies out before the input field, so the rounded corners of the field and of the buttons
                # under it (they are painted in the color of the page) do not show as squares on the picture
                end = centre - FADE_GAP * self.factor
                return wallpaper.fade_into(picture, base, end - FADE_LENGTH * height, end)
        return g.glow_for_theme(self.dark).resize((width, height), Image.BICUBIC)

    def _redraw(self):
        self._redraw_job = None
        width, height = self.winfo_width(), self.winfo_height()
        if width < 20 or height < 20:
            return
        self.configure(bg=t.pick(t.BG, self.dark))
        centre = self.center_y(height / self.factor) * self.factor
        picture = self._background_image(width, height, centre)
        self._photo = ImageTk.PhotoImage(picture) if picture is not None else None
        self.itemconfigure(self.background, image=self._photo or "")
        factor = self.factor
        hero = self.app.prefs.hero
        self.player.move(width / 2, centre + ORB_OFFSET * factor)
        self.coords(self.wordmark, width / 2 - 20 * factor, centre + WORDMARK_OFFSET * factor)
        if hero == "logo":
            # The sparkle sits at the end of the word (a hidden item has no box to measure)
            left, top, right, bottom = self.bbox(self.wordmark)
            self.coords(self.spark, right + 24 * factor, top + 20 * factor)
        self.coords(self.greeting, width / 2, centre + GREETING_OFFSET[hero] * factor)


class InputBar(ctk.CTkFrame):
    """The input field: a text command, the list of commands, the microphone switch and the send button."""

    def __init__(self, master, app):
        super().__init__(master, height=58, corner_radius=t.pill(58), fg_color=t.SURFACE, border_width=1,
                         border_color=t.BORDER)
        self.app = app
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_propagate(False)

        self.plus = self._round_button(g.icon("plus", t.TEXT, 20), self.open_plus_menu)
        self.plus.grid(row=0, column=0, padx=(10, 2))
        self.entry = ctk.CTkEntry(
            self, placeholder_text=PLACEHOLDER, border_width=0, fg_color="transparent", height=40,
            font=(t.FONT, 15), text_color=t.TEXT, placeholder_text_color=t.MUTED,
        )
        self.entry.grid(row=0, column=1, sticky="ew", padx=6)
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.entry.bind("<KeyRelease>", lambda _e: self._update_send())
        self.slash = make_button(self, "/", self.open_slash_menu, "secondary", width=40, height=36, font_size=15,
                                 tooltip="Список команд")
        self.slash.grid(row=0, column=2, padx=(0, 4))
        self.mic = self._round_button(g.icon("mic", t.TEXT, 20), app.toggle_listening)
        self.mic.grid(row=0, column=3, padx=(0, 2))
        self.send = self._round_button(g.icon("send", t.MUTED, 20), self.submit, fg_color=t.SURFACE_ALT,
                                       hover_color=t.SURFACE_ALT)
        self.send.grid(row=0, column=4, padx=(0, 9))

    def _round_button(self, image, command, **style):
        options = dict(fg_color="transparent", hover_color=t.HOVER)
        options.update(style)
        return ctk.CTkButton(self, text="", image=image, width=40, height=40, corner_radius=t.pill(40),
                             command=command, **options)

    def submit(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._update_send()
        self.app.submit_text(text)

    def _update_send(self):
        has_text = bool(self.entry.get().strip())
        self.send.configure(
            image=g.icon("send", t.ACCENT_TEXT if has_text else t.MUTED, 20),
            fg_color=t.ACCENT if has_text else t.SURFACE_ALT,
            hover_color=t.ACCENT_HOVER if has_text else t.SURFACE_ALT,
        )

    def set_listening(self, listening):
        self.mic.configure(image=g.icon("mic" if listening else "mic_off", t.TEXT if listening else t.ERROR, 20))

    def fill(self, text):
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self.entry.focus_set()
        self._update_send()

    def _popup(self, menu, widget):
        x, y = widget.winfo_rootx(), widget.winfo_rooty() - 4
        popup_menu(menu, x, max(0, y - menu.winfo_reqheight()))

    def open_plus_menu(self):
        menu = make_menu(self)
        menu.add_command(label="Новый диалог", command=self.app.new_dialog)
        menu.add_command(label="Проверить микрофон", command=self.app.open_microphone_check)
        menu.add_command(label="Открыть журнал", command=lambda: self.app.show_page("log"))
        menu.add_separator()
        menu.add_command(label="Поиск и команды", command=self.app.open_palette)
        self._popup(menu, self.plus)

    def open_slash_menu(self):
        menu = make_menu(self)
        phrases = self.app.command_phrases()
        if not phrases:
            menu.add_command(label="Команды не найдены", state="disabled")
        for phrase in phrases:
            menu.add_command(label=phrase.capitalize(), command=lambda p=phrase: self.fill(p))
        self._popup(menu, self.slash)


class HomePage(ctk.CTkFrame):
    """A welcome screen with the input field in the middle; with the first message it becomes a dialog."""

    def __init__(self, master, app, history=()):
        super().__init__(master, fg_color=t.BG, corner_radius=0)
        self.app = app
        self.hero_mode = True
        self.wrap_length = 460
        self.side_padding = 16
        self.message_labels = []
        self.rows = []

        self.canvas = HeroCanvas(self, app)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.chat = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self._add_top_space()
        self.chat._parent_canvas.bind("<Configure>", self._on_chat_resize, add="+")
        self.bar = InputBar(self, app)
        self.chips = ctk.CTkFrame(self, fg_color="transparent")
        self.mic_chip = make_button(
            self.chips, "Выключить микрофон", app.toggle_listening, "secondary", width=190,
            image=g.icon("mic", t.TEXT, 18),
        )
        self.mic_chip.grid(row=0, column=0, padx=5)
        make_button(
            self.chips, CAPABILITIES_QUESTION, lambda: app.submit_text(CAPABILITIES_QUESTION), "secondary",
            width=160,
        ).grid(row=0, column=1, padx=5)
        make_button(
            self.chips, "Дела", lambda: app.show_page("planner"), "secondary", width=110,
            image=g.icon("check", t.TEXT, 18),
        ).grid(row=0, column=2, padx=5)
        make_button(
            self.chips, "Команды", lambda: app.show_page("commands"), "secondary", width=130,
            image=g.icon("terminal", t.TEXT, 18),
        ).grid(row=0, column=3, padx=5)
        for role, text, meta, stamp in history:
            self.add_message(role, text, meta, stamp)
        self.bind("<Configure>", lambda _e: self.layout())
        self.layout()

    # ---------- Layout ----------

    def layout(self):
        # winfo_* give real pixels, place() and configure() of customtkinter expect pixels before the scaling
        factor = scale_of(self)
        real_width, real_height = self.winfo_width(), self.winfo_height()
        if real_width < 50 or real_height < 50:
            return
        width, height = int(real_width / factor), int(real_height / factor)
        bar_width = max(420, min(780, int(width * 0.74)))
        self.bar.configure(width=bar_width)
        if self.hero_mode:
            centre = self.canvas.center_y(height)
            self.bar.place(x=width // 2, y=centre, anchor="center")
            if self.app.prefs.show_chips:
                self.chips.place(x=width // 2, y=centre + 46, anchor="n")
            else:
                self.chips.place_forget()
        else:
            self.chat.configure(width=width, height=max(120, height - 98))
            self.chat.place(x=0, y=0)
            self.bar.place(x=width // 2, y=height - 22, anchor="s")
            self.chips.place_forget()
        self.bar.lift()
        # The side padding is in real pixels: it is set with the plain pack_configure
        side = max(int(16 * factor), (real_width - int(CONTENT_WIDTH * factor)) // 2)
        if side != self.side_padding:
            self.side_padding = side
            for row in self.rows:
                row.pack_configure(padx=side)

    def set_listening(self, listening):
        self.bar.set_listening(listening)
        self.mic_chip.configure(
            text="Выключить микрофон" if listening else "Включить микрофон",
            image=g.icon("mic" if listening else "mic_off", t.TEXT if listening else t.ERROR, 18),
        )

    def set_state(self, state):
        self.canvas.set_state(state)

    def set_shown(self, shown):
        """The page is open (or covered by another one): the orb only plays while it can be seen."""
        self.canvas.set_shown(shown)

    def apply_prefs(self):
        """The settings of the home page have changed: they are applied in place."""
        self.canvas.apply_prefs()
        self.layout()

    # ---------- Dialog ----------

    def add_message(self, role, text, meta=None, stamp=None):
        text = (text or "").strip()
        if not text:
            return
        if self.hero_mode:
            self._enter_dialog()
        stamp = stamp or datetime.now()

        if role == "system":
            # A service note, for example how the wake word was checked: small and quiet, without a bubble
            label = ctk.CTkLabel(
                self.chat, text=f"{stamp.strftime('%H:%M:%S')} · {text}", font=(t.FONT, 11),
                text_color=t.MUTED, wraplength=self.wrap_length, justify="left", anchor="w",
            )
            label.pack(fill="x", pady=(0, 8))
            label.pack_configure(padx=self.side_padding + 8)
            self.rows.append(label)
            self.message_labels.append(label)
            self._scroll_down()
            return

        is_user = role == "user"
        if is_user:
            text = text[:1].upper() + text[1:]
        side = "e" if is_user else "w"
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", pady=(0, 12))
        row.pack_configure(padx=self.side_padding)
        self.rows.append(row)
        bubble = ctk.CTkFrame(row, corner_radius=t.r(20), fg_color=t.BUBBLE_USER if is_user else t.BUBBLE_BOT)
        bubble.pack(anchor=side)
        label = ctk.CTkLabel(
            bubble, text=text, font=(t.FONT, 14), text_color=t.BUBBLE_USER_TEXT if is_user else t.TEXT,
            wraplength=self.wrap_length, justify="left", anchor="w",
        )
        label.pack(padx=16, pady=11)
        self.message_labels.append(label)
        author = "Вы" if is_user else "Астра"
        footer = f"{author} · {stamp.strftime(HISTORY_TIME_FORMAT)}"
        if meta and self.app.prefs.show_timings:
            footer += f" · ⏱ {meta}"
        ctk.CTkLabel(row, text=footer, font=(t.FONT, 11), text_color=t.MUTED).pack(anchor=side, padx=6, pady=(3, 0))
        for widget in (bubble, label):
            widget.bind("<Button-3>", lambda event, value=text: self._open_copy_menu(event, value), add="+")
        self._scroll_down()

    def _open_copy_menu(self, event, text):
        menu = make_menu(self)
        menu.add_command(label="Копировать", command=lambda: self._copy(text))
        popup_menu(menu, event.x_root, event.y_root)

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.app.toast.show("Скопировано", "ok", 1400)

    def _add_top_space(self):
        ctk.CTkFrame(self.chat, height=14, fg_color="transparent").pack()

    def _scroll_down(self):
        self.after(30, self._to_bottom)

    def _to_bottom(self):
        try:
            self.chat._parent_canvas.yview_moveto(1.0)
        except tk.TclError:
            pass

    def _enter_dialog(self):
        self.hero_mode = False
        self.canvas.place_forget()
        self.canvas.set_shown(False)
        self.layout()

    # Back to the welcome screen
    def clear(self):
        for child in self.chat.winfo_children():
            child.destroy()
        self._add_top_space()
        self.rows.clear()
        self.message_labels.clear()
        self.chat.place_forget()
        self.hero_mode = True
        self.canvas.refresh_greeting()
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lower(self.canvas)      # the canvas has its own lower(), for the items on it
        self.canvas.set_shown(self.app.current_page == "home")
        self.layout()

    def _on_chat_resize(self, event):
        wrap = max(240, min(620, int(event.width / scale_of(self) * 0.62)))
        if abs(wrap - self.wrap_length) < 8:
            return
        self.wrap_length = wrap
        for label in self.message_labels:
            label.configure(wraplength=wrap)

    def destroy(self):
        # The jobs of the canvas must not run for a page that is gone
        try:
            self.canvas.player.close()
            if self.canvas._redraw_job is not None:
                self.canvas.after_cancel(self.canvas._redraw_job)
                self.canvas._redraw_job = None
        except Exception:
            pass
        super().destroy()
