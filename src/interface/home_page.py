"""The home page: a welcome screen that turns into the dialog with the assistant."""
import tkinter as tk
from datetime import datetime

import customtkinter as ctk
from PIL import Image, ImageTk

from src.interface import graphics as g
from src.interface import theme as t
from src.interface.widgets import make_button

# Width of the dialog column on a wide window, in pixels
CONTENT_WIDTH = 860
PLACEHOLDER = "Спросите о чём угодно…"
CAPABILITIES_QUESTION = "Что ты умеешь?"


def greeting(now=None):
    hour = (now or datetime.now()).hour
    if hour < 5:
        return "Доброй ночи!"
    if hour < 12:
        return "Доброе утро!"
    if hour < 18:
        return "Добрый день!"
    return "Добрый вечер!"


def is_dark():
    return ctk.get_appearance_mode() == "Dark"


class HeroCanvas(tk.Canvas):
    """The background of the welcome screen: a soft glow, the Astra wordmark and the greeting."""

    def __init__(self, master):
        super().__init__(master, highlightthickness=0, bd=0)
        self._photo = None
        self._spark = None
        self._redraw_job = None
        self.dark = True
        self.background = self.create_image(0, 0, anchor="nw")
        self.wordmark = self.create_text(0, 0, text="Astra", font=(t.FONT, 58, "bold"), anchor="center")
        self.spark = self.create_image(0, 0, anchor="center")
        self.greeting = self.create_text(0, 0, text=greeting(), font=(t.FONT, 20, "bold"), anchor="center")
        self.bind("<Configure>", self._schedule_redraw)
        self.apply_theme()

    # The vertical center of the welcome screen: the input field sits here
    @staticmethod
    def center_y(height):
        return max(int(height * 0.52), 250)

    def apply_theme(self):
        self.dark = is_dark()
        self.configure(bg=t.pick(t.BG, self.dark))
        self.itemconfigure(self.wordmark, fill=t.pick(t.TEXT, self.dark))
        self.itemconfigure(self.greeting, fill=t.pick(t.TEXT, self.dark))
        self._spark = ImageTk.PhotoImage(g.sparkle_image(34, t.pick(t.ACCENT, self.dark)))
        self.itemconfigure(self.spark, image=self._spark)
        self._redraw()

    def refresh_greeting(self):
        self.itemconfigure(self.greeting, text=greeting())

    def _schedule_redraw(self, _event=None):
        # Resizing a window sends many events, the picture is rebuilt once the window settles
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(80, self._redraw)

    def _redraw(self):
        self._redraw_job = None
        width, height = self.winfo_width(), self.winfo_height()
        if width < 20 or height < 20:
            return
        glow = g.glow_image(self.dark).resize((width, height), Image.BICUBIC)
        self._photo = ImageTk.PhotoImage(glow)
        self.itemconfigure(self.background, image=self._photo)
        centre = self.center_y(height)
        self.coords(self.wordmark, width / 2 - 20, centre - 128)
        left, top, right, bottom = self.bbox(self.wordmark)
        self.coords(self.spark, right + 24, top + 20)
        self.coords(self.greeting, width / 2, centre - 62)


class InputBar(ctk.CTkFrame):
    """The input field: a text command, the list of commands, the microphone switch and the send button."""

    def __init__(self, master, app):
        super().__init__(master, height=58, corner_radius=29, fg_color=t.SURFACE, border_width=1,
                         border_color=t.BORDER)
        self.app = app
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_propagate(False)

        self.plus = ctk.CTkButton(
            self, text="", image=g.icon("plus", t.TEXT, 20), width=40, height=40, corner_radius=20,
            fg_color="transparent", hover_color=t.HOVER, command=self.open_plus_menu,
        )
        self.plus.grid(row=0, column=0, padx=(10, 2))
        self.entry = ctk.CTkEntry(
            self, placeholder_text=PLACEHOLDER, border_width=0, fg_color="transparent", height=40,
            font=(t.FONT, 15), text_color=t.TEXT, placeholder_text_color=t.MUTED,
        )
        self.entry.grid(row=0, column=1, sticky="ew", padx=6)
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.entry.bind("<KeyRelease>", lambda _e: self._update_send())
        self.slash = make_button(self, "/", self.open_slash_menu, "secondary", width=40, height=36, font_size=15)
        self.slash.grid(row=0, column=2, padx=(0, 4))
        self.mic = ctk.CTkButton(
            self, text="", image=g.icon("mic", t.TEXT, 20), width=40, height=40, corner_radius=20,
            fg_color="transparent", hover_color=t.HOVER, command=app.toggle_listening,
        )
        self.mic.grid(row=0, column=3, padx=(0, 2))
        self.send = ctk.CTkButton(
            self, text="", image=g.icon("send", t.MUTED, 20), width=40, height=40, corner_radius=20,
            fg_color=t.SURFACE_ALT, hover_color=t.SURFACE_ALT, command=self.submit,
        )
        self.send.grid(row=0, column=4, padx=(0, 9))

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

    # The menus are plain Tk menus: customtkinter has none, so they get the colors of the current theme by hand
    def _menu(self):
        dark = is_dark()
        return tk.Menu(
            self, tearoff=0, bd=0, font=(t.FONT, 11),
            bg=t.pick(t.SURFACE, dark), fg=t.pick(t.TEXT, dark),
            activebackground=t.pick(t.ACCENT, dark), activeforeground=t.pick(t.ACCENT_TEXT, dark),
        )

    def _popup(self, menu, widget):
        x, y = widget.winfo_rootx(), widget.winfo_rooty() - 4
        try:
            menu.tk_popup(x, max(0, y - menu.winfo_reqheight()))
        finally:
            menu.grab_release()

    def open_plus_menu(self):
        menu = self._menu()
        menu.add_command(label="Новый диалог", command=self.app.new_dialog)
        menu.add_command(label="Проверить микрофон", command=self.app.open_microphone_check)
        menu.add_command(label="Открыть журнал", command=lambda: self.app.show_page("log"))
        self._popup(menu, self.plus)

    def open_slash_menu(self):
        menu = self._menu()
        phrases = self.app.command_phrases()
        if not phrases:
            menu.add_command(label="Команды не найдены", state="disabled")
        for phrase in phrases:
            menu.add_command(label=phrase.capitalize(), command=lambda p=phrase: self.fill(p))
        self._popup(menu, self.slash)


class HomePage(ctk.CTkFrame):
    """A welcome screen with the input field in the middle; with the first message it becomes a dialog."""

    def __init__(self, master, app):
        super().__init__(master, fg_color=t.BG, corner_radius=0)
        self.app = app
        self.hero_mode = True
        self.wrap_length = 460
        self.side_padding = 16
        self.message_labels = []
        self.rows = []

        self.canvas = HeroCanvas(self)
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
            self.chips, "Команды", lambda: app.show_page("commands"), "secondary", width=130,
            image=g.icon("terminal", t.TEXT, 18),
        ).grid(row=0, column=2, padx=5)
        self.bind("<Configure>", lambda _e: self.layout())
        self.layout()

    # ---------- Layout ----------

    def layout(self):
        width, height = self.winfo_width(), self.winfo_height()
        if width < 50 or height < 50:
            return
        bar_width = max(420, min(780, int(width * 0.74)))
        if self.hero_mode:
            centre = self.canvas.center_y(height)
            self.bar.configure(width=bar_width)
            self.bar.place(x=width // 2, y=centre, anchor="center")
            self.chips.place(x=width // 2, y=centre + 46, anchor="n")
        else:
            self.chat.configure(width=width, height=max(120, height - 98))
            self.chat.place(x=0, y=0)
            self.bar.configure(width=bar_width)
            self.bar.place(x=width // 2, y=height - 22, anchor="s")
            self.chips.place_forget()
        self.bar.lift()
        side = max(16, (width - CONTENT_WIDTH) // 2)
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

    def apply_theme(self):
        self.canvas.apply_theme()

    # ---------- Dialog ----------

    def add_message(self, role, text, meta=None):
        text = (text or "").strip()
        if not text:
            return
        if self.hero_mode:
            self._enter_dialog()

        if role == "system":
            # A service note, for example how the wake word was checked: small and quiet, without a bubble
            label = ctk.CTkLabel(
                self.chat, text=f"{datetime.now().strftime('%H:%M:%S')} · {text}", font=(t.FONT, 11),
                text_color=t.MUTED, wraplength=self.wrap_length, justify="left", anchor="w",
            )
            label.pack(fill="x", padx=self.side_padding + 8, pady=(0, 8))
            self.rows.append(label)
            self.message_labels.append(label)
            self._scroll_down()
            return

        is_user = role == "user"
        if is_user:
            text = text[:1].upper() + text[1:]
        side = "e" if is_user else "w"
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", pady=(0, 12), padx=self.side_padding)
        self.rows.append(row)
        bubble = ctk.CTkFrame(row, corner_radius=20, fg_color=t.BUBBLE_USER if is_user else t.BUBBLE_BOT)
        bubble.pack(anchor=side)
        label = ctk.CTkLabel(
            bubble, text=text, font=(t.FONT, 14), text_color=t.BUBBLE_USER_TEXT if is_user else t.TEXT,
            wraplength=self.wrap_length, justify="left", anchor="w",
        )
        label.pack(padx=16, pady=11)
        self.message_labels.append(label)
        author = "Вы" if is_user else "Астра"
        footer = f"{author} · {datetime.now().strftime('%H:%M')}"
        if meta:
            footer += f" · ⏱ {meta}"
        ctk.CTkLabel(row, text=footer, font=(t.FONT, 11), text_color=t.MUTED).pack(anchor=side, padx=6, pady=(3, 0))
        self._scroll_down()

    def _add_top_space(self):
        ctk.CTkFrame(self.chat, height=14, fg_color="transparent").pack()

    def _scroll_down(self):
        self.after(30, lambda: self.chat._parent_canvas.yview_moveto(1.0))

    def _enter_dialog(self):
        self.hero_mode = False
        self.canvas.place_forget()
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
        self.canvas.lower()
        self.layout()

    def _on_chat_resize(self, event):
        wrap = max(240, min(620, int(event.width * 0.62)))
        if abs(wrap - self.wrap_length) < 8:
            return
        self.wrap_length = wrap
        for label in self.message_labels:
            label.configure(wraplength=wrap)
