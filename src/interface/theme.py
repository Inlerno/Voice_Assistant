"""Colors, fonts, shapes and assistant states of the interface.

Colors are (light theme, dark theme) pairs, customtkinter picks the right one by itself. The palette is made from
the settings of the user by apply(): the accent, how tinted the backgrounds are and how deep the dark theme is.
The names the pages use (t.BG, t.ACCENT ...) are always read at the moment of drawing, never kept in a constant,
so a new scheme reaches every widget that is built after apply().

Names of the palette: BG, SURFACE, SURFACE_ALT, BORDER, TEXT, MUTED, HOVER, ACCENT, ACCENT_TEXT, ACCENT_HOVER,
ACCENT_SOFT (a tinted fill: the open page, a chosen row), ACCENT_VIVID (lines, sliders, focus, the orb),
BUBBLE_USER, BUBBLE_USER_TEXT, BUBBLE_BOT.
"""
import logging

from src.core import risky
from src.interface import palette

# Fonts (Segoe UI is the native Windows font, Tk falls back to another one elsewhere)
DEFAULT_FONT = "Segoe UI"
FONT = DEFAULT_FONT
FONT_MONO = "Consolas"

# How round the corners are: 1 is the original look, 0 is square
RADIUS = 1.0

# Status colors: they do not depend on the accent
OK = palette.STATUS_COLORS["OK"]
WARN = palette.STATUS_COLORS["WARN"]
ERROR = palette.STATUS_COLORS["ERROR"]
OFF = palette.STATUS_COLORS["OFF"]
VIOLET = palette.STATUS_COLORS["VIOLET"]

# Colors that depend on the palette are filled by apply(); ACCENT_FAMILY is the (hue, chroma) of the accent
MIC_STATUS_COLORS = {}
ACCENT_FAMILY = (0.0, 0.0)


def r(value, limit=None):
    """A corner radius made from the one of the original design: it follows the roundness set by the user."""
    scaled = max(0, round(value * RADIUS))
    return scaled if limit is None else min(scaled, limit)


def pill(height):
    """The radius of a button or a field of the given height: a full pill, less round when the corners are sharper."""
    return min(r(height / 2), int(height // 2))


def apply(preferences=None):
    """Makes the palette, the font and the roundness from the settings (without them: the original look)."""
    global FONT, RADIUS, ACCENT_FAMILY
    if preferences is None:
        family = palette.accent_family(palette.DEFAULT_PRESET)
        scheme = palette.build_scheme(family)
        FONT, RADIUS = DEFAULT_FONT, 1.0
    else:
        family = palette.accent_family(preferences.accent)
        scheme = preferences.scheme()
        FONT = preferences.font
        RADIUS = preferences.radius
    ACCENT_FAMILY = (family.hue, family.chroma)
    globals().update(scheme)
    MIC_STATUS_COLORS.clear()
    MIC_STATUS_COLORS.update({
        "ok": OK,
        "quiet": WARN,
        "loud": WARN,
        "silent": ERROR,
        "error": ERROR,
        "cancelled": scheme["MUTED"],
    })


apply()


# Assistant states: title, hint, dot color, dimmed dot color (for the pulse animation)
STATES = {
    "idle": ("Ожидание", "Скажите «{activation}»", OFF, OFF),
    "listening": ("Слушаю", "Говорите команду", OK, ("#157650", "#25905F")),
    "processing": ("Выполняю", "Обрабатываю запрос", WARN, WARN),
    "speaking": ("Говорю", "Астра отвечает", VIOLET, ("#503A9A", "#6F5AB8")),
    "paused": ("Микрофон выключен", "Включите микрофон, чтобы продолжить", ERROR, ERROR),
}

# Services shown in the status bar: AI (language model), STT (speech recognition), TTS (speech synthesis),
# TG (the Telegram bot: orders from a chat)
SERVICES = (("ai", "AI"), ("stt", "STT"), ("tts", "TTS"), ("tg", "TG"))
SERVICE_COLORS = {"ok": OK, "busy": WARN, "error": ERROR, "off": OFF, "unknown": OFF}
SERVICE_TITLES = {
    "ok": "работает",
    "busy": "занят",
    "error": "ошибка",
    "off": "выключен",
    "unknown": "ещё не проверялся",
}

APPEARANCE_LABELS = {"Тёмная": "Dark", "Светлая": "Light", "Системная": "System"}

# Pages of the top navigation: key, title, icon
PAGES = (
    ("home", "Главная", "home"),
    ("planner", "Дела", "check"),
    ("apps", "Приложения", "grid"),
    ("commands", "Команды", "terminal"),
    ("voice", "Голос", "mic"),
    ("tts", "Озвучка", "speaker"),
    ("telegram", "Telegram", "plane"),
    ("log", "Журнал", "document"),
    ("settings", "Настройки", "sliders"),
)

# How the pages are grouped in the side menu: the last group sits at the bottom of it
NAV_SECTIONS = (
    ("home", "planner", "apps", "commands"),
    ("voice", "tts", "telegram"),
    ("log", "settings"),
)

# Phrases for the page of plans and applications: what to say, what it does
PLANNER_EXAMPLES = (
    ("Поставь таймер на десять минут", "Таймер; можно «на полчаса», «на час тридцать»"),
    ("Разбуди меня в семь тридцать", "Будильник; «по будням», «каждый день», «завтра в шесть»"),
    ("Напомни через час позвонить маме", "Напоминание; «в восемь вечера», «завтра в девять»"),
    ("Добавь задачу купить молоко", "Новая задача; «что в списке задач» — прочитать их"),
    ("Выполни задачу два", "Отметить задачу по номеру или по словам"),
    ("Какие таймеры", "Что осталось; «отмени все таймеры» — убрать"),
    ("Открой калькулятор", "Запуск программы из списка «Приложения»"),
)

LOG_LINES_LIMIT = 500
LOG_LEVEL_TAGS = {logging.WARNING: "warning", logging.ERROR: "error", logging.CRITICAL: "error"}

# Commands that change the state of the computer: they are started from the interface only after a confirmation
# (the list is shared with the Telegram bot, so it lives in the core)
RISKY_COMMANDS = risky.RISKY_COMMANDS


def pick(color, dark):
    """The color of a (light, dark) pair for the given mode."""
    return color[1] if dark else color[0]
