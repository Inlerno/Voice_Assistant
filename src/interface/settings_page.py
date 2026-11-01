"""The settings: the look of the window (colors, font, shapes), the home page, the models and the program itself.

Everything the user changes here is applied at once; the controls only tell the application what changed
(VoiceAssistantApp.change) and the application decides what has to be rebuilt.
"""
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from src.interface import graphics as g
from src.interface import palette, wallpaper
from src.interface import preferences as P
from src.interface import theme as t
from src.interface.pages import ModelSection, scrolling_page
from src.interface.widgets import (
    Card, Segmented, SettingRow, Swatch, divider, make_button, make_slider, make_switch, spacer,
)

TABS = (("look", "Оформление"), ("home", "Главная"), ("models", "Модели и о программе"))
APPEARANCE_CHOICES = (("Dark", "Тёмная"), ("Light", "Светлая"), ("System", "Системная"))
NAV_OPTIONS = (("left", "Слева"), ("top", "Сверху"))
HERO_OPTIONS = (("orb", "Шар"), ("logo", "Логотип"), ("minimal", "Минимализм"))
BACKGROUND_OPTIONS = (("glow", "Свечение"), ("flat", "Однотонный"), ("image", "Картинка"))
# Fonts that look good in the interface; only the installed ones are offered (the current one is always in the list)
FONT_CHOICES = (
    "Segoe UI", "Segoe UI Variable Text", "Bahnschrift", "Calibri", "Candara", "Corbel", "Tahoma", "Verdana",
    "Trebuchet MS", "Arial", "Cambria", "Georgia", "Constantia", "Inter", "Roboto", "Open Sans", "Noto Sans",
    "Ubuntu", "DejaVu Sans",
)
PICTURE_TYPES = [("Картинки", "*.jpg *.jpeg *.png *.webp *.bmp"), ("Все файлы", "*.*")]


def available_fonts(current):
    """The fonts to choose from: the nice ones that are installed, and the one that is used now."""
    try:
        installed = set(tkfont.families())
    except Exception:
        installed = set()
    fonts = [name for name in FONT_CHOICES if name in installed]
    if current not in fonts:
        fonts.insert(0, current)
    return fonts


def percent(value):
    return f"{round(value * 100)}%"


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self.prefs = app.prefs
        body, _ = scrolling_page(self, "Настройки", "Цвета, шрифт, окно и главная страница меняются сразу")
        self.body = body
        self.tabs = Segmented(body, TABS, app.settings_tab if app.settings_tab in dict(TABS) else "look",
                              self.show_tab, button_width=150)
        self.tabs.grid(row=1, column=0, sticky="w", pady=(0, 14))
        self.groups = {
            "look": self._build_look(body),
            "home": self._build_home(body),
            "models": self._build_models(body),
        }
        for group in self.groups.values():
            group.grid(row=2, column=0, sticky="ew")
        self.show_tab(self.tabs.value)

    def show_tab(self, key):
        self.app.settings_tab = key
        self.tabs.set(key)
        for name, group in self.groups.items():
            if name == key:
                group.grid()
            else:
                group.grid_remove()

    # ---------- Scroll position (kept when the window is built again) ----------

    def scroll_position(self):
        try:
            return self.body._parent_canvas.yview()[0]
        except Exception:
            return 0.0

    def restore_scroll(self, fraction):
        try:
            self.body._parent_canvas.yview_moveto(fraction)
        except Exception:
            pass

    # ---------- Rows ----------

    def _rows(self, card, rows):
        """Puts the rows into the card one under another with thin lines between them."""
        for index, row in enumerate(rows):
            row.pack(fill="x", padx=22, pady=(0, 0))
            if index < len(rows) - 1:
                divider(card, pady=(12, 12))
        spacer(card, 14)

    def _slider_row(self, title, hint, bounds, value, steps, show, key, convert=None, live=None):
        """A slider with its value; the setting is changed when the slider is let go (live, if given, runs while it moves)."""
        row = SettingRow(self._card_for_rows, title, hint)
        label = ctk.CTkLabel(row.control, text=show(value), width=70, font=(t.FONT, 13), text_color=t.MUTED, anchor="e")

        def moving(raw):
            label.configure(text=show(raw))
            if live is not None:
                live(raw)

        def released(raw):
            self.app.change(**{key: convert(raw) if convert else raw})

        slider = make_slider(row.control, bounds, value, command=moving, steps=steps, release=released, width=190)
        slider.pack(side="left")
        label.pack(side="left", padx=(10, 0))
        return row

    def _scale_row(self):
        """The scale of the interface; a screen that is too small for a bigger one stops the slider where it fits."""
        low, high = P.SCALE_RANGE
        limit = self.app.scale_limit()
        hint = "Всё в окне крупнее или мельче"
        if limit < high:
            high = max(limit, low + 0.05)
            hint += f" (на этом экране — не больше {round(high * 100)}%)"
        steps = max(2, round((high - low) / (P.SCALE_RANGE[1] - low) * 14))
        return self._slider_row("Масштаб интерфейса", hint, (low, high), min(self.prefs.scale, high), steps,
                                percent, "scale")

    def _switch_row(self, title, hint, key):
        row = SettingRow(self._card_for_rows, title, hint)
        switch = make_switch(row.control, lambda value: self.app.change(**{key: value}), getattr(self.prefs, key))
        switch.pack()
        return row

    def _segmented_row(self, title, hint, options, value, command):
        row = SettingRow(self._card_for_rows, title, hint)
        Segmented(row.control, options, value, command).pack()
        return row

    # ---------- Look ----------

    def _build_look(self, parent):
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.grid_columnconfigure(0, weight=1)
        prefs = self.prefs

        colors = Card(group, "Цвета", "Один акцентный цвет окрашивает кнопки, выделение и шар. Остальное подберётся под него.")
        colors.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self._card_for_rows = colors
        current = getattr(self.app.assistant, "appearance", "Dark")
        rows = [
            self._segmented_row("Тема", "Тёмная, светлая или как в Windows", APPEARANCE_CHOICES, current,
                                self.app.set_appearance),
            self._accent_row(),
            self._slider_row("Оттенок фона", "Насколько фон и панели окрашены в цвет акцента", palette.TINT_RANGE,
                             prefs.tint, 20, percent, "tint"),
            self._slider_row("Глубина тёмной темы", "Левее — мягче и светлее, правее — глубже и контрастнее",
                             palette.DEPTH_RANGE, prefs.depth, 20, lambda v: f"{round(v * 100):+d}", "depth"),
        ]
        self._rows(colors, rows)

        shapes = Card(group, "Шрифт и размеры", "Подстройте окно под свой экран и зрение.")
        shapes.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self._card_for_rows = shapes
        self._rows(shapes, [
            self._font_row(),
            self._scale_row(),
            self._slider_row("Скругление углов", "От строгих прямых углов до совсем круглых", P.RADIUS_RANGE,
                             prefs.radius, 15, percent, "radius"),
        ])

        window = Card(group, "Окно", "")
        window.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        self._card_for_rows = window
        self._rows(window, [
            self._segmented_row("Меню", "Слева в колонке или сверху вкладками", NAV_OPTIONS, prefs.nav,
                                lambda value: self.app.change(nav=value)),
            self._switch_row("Свернуть боковое меню", "В нём останутся только значки", "nav_collapsed"),
            self._slider_row("Прозрачность окна", "Окно можно сделать полупрозрачным", P.OPACITY_RANGE, prefs.opacity,
                             8, percent, "opacity", live=self.app.preview_opacity),
            self._switch_row("Поверх всех окон", "Окно ассистента не прячется за другими", "always_on_top"),
            self._switch_row("Помнить размер и место окна", "Окно откроется там же, где вы его оставили",
                             "remember_window"),
        ])

        reset = make_button(group, "Сбросить оформление", self.reset, "secondary", width=210)
        reset.grid(row=3, column=0, sticky="w", pady=(0, 18))
        return group

    def _accent_row(self):
        row = SettingRow(self._card_for_rows, "Акцентный цвет", "Выберите готовый или нажмите «+», чтобы взять любой",
                         stacked=True)
        prefs = self.prefs
        for index, (key, title, _, _) in enumerate(palette.PRESETS):
            Swatch(row.control, key, prefs.accent == key, lambda value=key: self.app.change(accent=value),
                   tooltip=title).grid(row=0, column=index, padx=(0, 8))
        column = len(palette.PRESETS)
        if palette.is_hex_color(prefs.accent):
            Swatch(row.control, prefs.accent, True, self.pick_color,
                   tooltip=f"Свой цвет {prefs.accent}. Нажмите, чтобы изменить").grid(row=0, column=column)
        else:
            ctk.CTkButton(
                row.control, text="", image=g.icon("plus", t.MUTED, 16), width=34, height=34,
                corner_radius=t.pill(34), fg_color="transparent", hover_color=t.HOVER, border_width=2,
                border_color=t.BORDER, command=self.pick_color,
            ).grid(row=0, column=column)
        return row

    def pick_color(self):
        prefs = self.prefs
        start = prefs.accent if palette.is_hex_color(prefs.accent) else palette.accent_swatch(prefs.accent)
        chosen = colorchooser.askcolor(color=start, parent=self.app.root, title="Свой акцентный цвет")
        if chosen and chosen[1]:
            self.app.change(accent=chosen[1])

    def _font_row(self):
        row = SettingRow(self._card_for_rows, "Шрифт", "Меняет шрифт во всём окне")
        menu = ctk.CTkOptionMenu(
            row.control, values=available_fonts(self.prefs.font), width=210, height=36, corner_radius=t.pill(36),
            font=(t.FONT, 13), dropdown_font=(t.FONT, 13), fg_color=t.SURFACE_ALT, button_color=t.SURFACE_ALT,
            button_hover_color=t.HOVER, text_color=t.TEXT, dropdown_fg_color=t.SURFACE, dropdown_text_color=t.TEXT,
            dropdown_hover_color=t.ACCENT_SOFT, command=lambda name: self.app.change(font=name),
        )
        menu.set(self.prefs.font)
        menu.pack()
        return row

    def reset(self):
        if messagebox.askyesno("Сброс оформления", "Цвета, шрифт, размеры и вид главной страницы вернутся к "
                               "исходным. Продолжить?", parent=self.app.root):
            self.app.reset_appearance()

    # ---------- Home page ----------

    def _build_home(self, parent):
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.grid_columnconfigure(0, weight=1)
        prefs = self.prefs
        card = Card(group, "Главная страница", "Как выглядит экран приветствия.")
        card.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self._card_for_rows = card
        self._rows(card, [
            self._segmented_row("Вид", "Шар показывает, что делает ассистент: слушает, думает или говорит",
                                HERO_OPTIONS, prefs.hero, lambda value: self.app.change(hero=value)),
            self._name_row(),
            self._switch_row("Быстрые кнопки", "Под полем ввода: микрофон, «Что ты умеешь?», дела, команды",
                             "show_chips"),
            self._switch_row("Время распознавания", "Подпись с миллисекундами под сообщениями", "show_timings"),
            self._switch_row("Анимации", "Шар и плавные эффекты. Выключите на слабом компьютере", "animations"),
        ])

        background = Card(group, "Фон", "Свечение берёт цвет акцента. Можно поставить свою картинку.")
        background.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self._card_for_rows = background
        self._rows(background, [
            self._segmented_row("Фон главной", "", BACKGROUND_OPTIONS, prefs.background,
                                lambda value: self.app.change(background=value)),
            self._wallpaper_row(),
            self._slider_row("Затемнение картинки", "Чем больше, тем лучше читается текст", P.DIM_RANGE,
                             prefs.wallpaper_dim, 18, percent, "wallpaper_dim"),
            self._slider_row("Размытие картинки", "Мягкий фон не отвлекает от диалога", P.BLUR_RANGE,
                             prefs.wallpaper_blur, 24, lambda v: f"{round(v)}", "wallpaper_blur"),
        ])
        return group

    def _name_row(self):
        row = SettingRow(self._card_for_rows, "Ваше имя", "Астра поздоровается по имени: «Добрый вечер, Аня!»")
        entry = ctk.CTkEntry(row.control, width=210, height=36, corner_radius=t.pill(36), border_color=t.BORDER,
                             fg_color=t.SURFACE_ALT, text_color=t.TEXT, placeholder_text="Как к вам обращаться",
                             placeholder_text_color=t.MUTED, font=(t.FONT, 13))
        if self.prefs.user_name:
            entry.insert(0, self.prefs.user_name)
        entry.pack()
        entry.bind("<KeyRelease>", lambda _e: self.app.change(user_name=entry.get()), add="+")
        return row

    def _wallpaper_row(self):
        row = SettingRow(self._card_for_rows, "Своя картинка", "", stacked=True)
        buttons = ctk.CTkFrame(row.control, fg_color="transparent")
        buttons.pack(anchor="w")
        make_button(buttons, "Выбрать…", self.choose_wallpaper, "primary", width=130, height=34,
                    image=g.icon("image", t.ACCENT_TEXT, 18)).pack(side="left")
        self.remove_button = make_button(buttons, "Убрать", self.remove_wallpaper, "secondary", width=100, height=34)
        self.remove_button.pack(side="left", padx=(8, 0))
        self.wallpaper_note = ctk.CTkLabel(row.control, text="", font=(t.FONT, 12), text_color=t.MUTED, anchor="w")
        self.wallpaper_note.pack(anchor="w", pady=(8, 0))
        self.refresh_wallpaper_note()
        return row

    def refresh_wallpaper_note(self):
        path = self.prefs.wallpaper_path
        if self.prefs.has_wallpaper():
            try:
                with Image.open(path) as picture:
                    size = f" ({picture.width}×{picture.height})"
            except Exception:
                size = ""
            self.wallpaper_note.configure(text=f"Картинка выбрана{size}. Она видна, если фон — «Картинка».")
            self.remove_button.configure(state="normal")
        else:
            self.wallpaper_note.configure(text="Картинка не выбрана. Подойдут JPG, PNG, WEBP и BMP.")
            self.remove_button.configure(state="disabled")

    def choose_wallpaper(self):
        path = filedialog.askopenfilename(parent=self.app.root, title="Картинка для главной страницы",
                                          filetypes=PICTURE_TYPES)
        if not path:
            return
        try:
            wallpaper.import_image(path, self.prefs.wallpaper_path)
        except wallpaper.WallpaperError as error:
            messagebox.showwarning("Картинка", str(error), parent=self.app.root)
            return
        self.refresh_wallpaper_note()
        if not self.app.change(background="image"):
            self.app.home.apply_prefs()
        self.app.toast.show("Картинка установлена", "ok")

    def remove_wallpaper(self):
        wallpaper.remove(self.prefs.wallpaper_path)
        self.refresh_wallpaper_note()
        if not self.app.change(background="glow"):
            self.app.home.apply_prefs()

    # ---------- Models and the program ----------

    def _build_models(self, parent):
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.grid_columnconfigure(0, weight=1)
        self.models = ModelSection(group, self.app)
        self.models.grid(row=0, column=0, sticky="ew")
        about = Card(
            group, "О программе",
            "Распознавание речи работает офлайн (Vosk), ответы на свободные вопросы даёт локальная модель через "
            "LM Studio. Подробный журнал сохраняется в logs/assistant.log. Поиск по всему, что умеет Астра, "
            "открывается по Ctrl+K.",
        )
        about.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        spacer(about, 6)
        return group
