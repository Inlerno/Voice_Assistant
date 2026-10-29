"""Colors, fonts and assistant states of the interface.

Colors are (light theme, dark theme) pairs, customtkinter picks the right one by itself.
The look follows the Astra design: warm dark maroon background, soft cream accent, round shapes.
"""
import logging

# Fonts (Segoe UI is the native Windows font, Tk falls back to another one elsewhere)
FONT = "Segoe UI"
FONT_MONO = "Consolas"

BG = ("#F7F0EE", "#1C1214")
SURFACE = ("#FFFFFF", "#2A1C1D")
SURFACE_ALT = ("#F1E6E3", "#34262A")
BORDER = ("#E6D8D4", "#4A3532")
TEXT = ("#2B1A1A", "#F6EEEC")
MUTED = ("#8A7471", "#B39E9A")
HOVER = ("#F0E3DF", "#34252A")
ACCENT = ("#7D3B35", "#F1DDD6")
ACCENT_TEXT = ("#FFFFFF", "#2A1717")
ACCENT_HOVER = ("#662F2A", "#E3C9C0")
BUBBLE_USER = ACCENT
BUBBLE_USER_TEXT = ACCENT_TEXT
BUBBLE_BOT = ("#EFE3DF", "#2E2022")

# Status colors
OK = ("#1E9E68", "#3DDC97")
WARN = ("#C98A12", "#F5B63D")
ERROR = ("#C0392B", "#F2677A")
OFF = ("#A99793", "#7B6764")
VIOLET = ("#6D4FD0", "#A58BFF")

# Assistant states: title, hint, dot color, dimmed dot color (for the pulse animation)
STATES = {
    "idle": ("Ожидание", "Скажите «{activation}»", OFF, OFF),
    "listening": ("Слушаю", "Говорите команду", OK, ("#157650", "#25905F")),
    "processing": ("Выполняю", "Обрабатываю запрос", WARN, WARN),
    "speaking": ("Говорю", "Астра отвечает", VIOLET, ("#503A9A", "#6F5AB8")),
    "paused": ("Микрофон выключен", "Включите микрофон, чтобы продолжить", ERROR, ERROR),
}

# Services shown in the status bar: AI (language model), STT (speech recognition), TTS (speech synthesis)
SERVICES = (("ai", "AI"), ("stt", "STT"), ("tts", "TTS"))
SERVICE_COLORS = {"ok": OK, "busy": WARN, "error": ERROR, "off": OFF, "unknown": OFF}
SERVICE_TITLES = {
    "ok": "работает",
    "busy": "занят",
    "error": "ошибка",
    "off": "выключен",
    "unknown": "ещё не проверялся",
}

# Colors of the microphone check verdict
MIC_STATUS_COLORS = {
    "ok": OK,
    "quiet": WARN,
    "loud": WARN,
    "silent": ERROR,
    "error": ERROR,
    "cancelled": MUTED,
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
    ("log", "Журнал", "document"),
    ("settings", "Настройки", "sliders"),
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
RISKY_COMMANDS = {
    "shutdown": "выключит компьютер",
    "restart": "перезагрузит компьютер",
    "sleep": "переведёт компьютер в спящий режим",
    "empty": "очистит корзину",
    "finish": "завершит работу ассистента",
}


def pick(color, dark):
    """The color of a (light, dark) pair for the given mode."""
    return color[1] if dark else color[0]
