"""Starting programs by voice: a built-in list, the Start menu shortcuts and the programs the user added."""
import json
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from rapidfuzz import fuzz

from src.productivity.planner import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_FILE = DATA_DIR / "apps.json"
MATCH_THRESHOLD = 82
OPEN_WORDS = {"открой", "открыть", "открывай", "запусти", "запустить", "запускай", "стартуй"}
FILLER_WORDS = {"мне", "пожалуйста", "приложение", "приложения", "программу", "программа", "программы", "ка", "плиз"}

# name, the ways to say it, what to start (a program, a document or an address that Windows knows how to open)
BUILTIN_APPS = (
    ("Блокнот", ("блокнот", "notepad"), "notepad"),
    ("Калькулятор", ("калькулятор", "калькулятора", "calculator"), "calc"),
    ("Проводник", ("проводник", "файлы", "мои файлы", "explorer"), "explorer"),
    ("Paint", ("пейнт", "паинт", "рисовалка", "paint"), "mspaint"),
    ("Диспетчер задач", ("диспетчер задач", "диспетчер"), "taskmgr"),
    ("Командная строка", ("командная строка", "командную строку", "консоль", "cmd"), "cmd"),
    ("PowerShell", ("повершелл", "пауэршелл", "powershell"), "powershell"),
    ("Параметры Windows", ("параметры", "настройки windows", "настройки виндовс", "настройки системы"), "ms-settings:"),
    ("Панель управления", ("панель управления", "панель"), "control"),
    ("Ножницы", ("ножницы", "скриншот", "снимок экрана"), "snippingtool"),
    ("Word", ("ворд", "word", "вордом"), "winword"),
    ("Excel", ("эксель", "эксел", "excel"), "excel"),
    ("PowerPoint", ("повер поинт", "павер поинт", "powerpoint", "презентации"), "powerpnt"),
    ("Microsoft Edge", ("эдж", "edge", "майкрософт эдж"), "msedge"),
    ("Firefox", ("фаерфокс", "файрфокс", "firefox"), "firefox"),
    ("Visual Studio Code", ("вс код", "визуал студио код", "vscode", "vs code"), "code"),
    ("Steam", ("стим", "steam"), "steam://open/main"),
    ("Spotify", ("спотифай", "spotify"), "spotify:"),
    ("Камера", ("камера", "камеру"), "microsoft.windows.camera:"),
)

_SKIP_SHORTCUTS = re.compile(r"uninstall|удал|readme|справк|help|website|веб-?сайт|документац|license|лицензи", re.I)

_TRANSLIT_PAIRS = (
    ("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("ts", "ц"), ("ya", "я"), ("yu", "ю"), ("yo", "ё"),
    ("ph", "ф"), ("ck", "к"), ("oo", "у"), ("ee", "и"), ("th", "т"), ("kh", "х"),
)
_TRANSLIT_LETTERS = dict(zip("abdefghijklmnopqrstuvwxyz", "абдефгхижклмнопкрстуввксиз"))


def translit(text):
    """"telegram" -> "телеграм": a rough Latin to Cyrillic reading, enough to match what the recognizer writes."""
    text = text.lower()
    for latin, cyrillic in _TRANSLIT_PAIRS:
        text = text.replace(latin, cyrillic)
    out = []
    for index, char in enumerate(text):
        if char == "c":
            following = text[index + 1:index + 2]
            out.append("с" if following in ("e", "i", "y") else "к")
        else:
            out.append(_TRANSLIT_LETTERS.get(char, char))
    return "".join(out)


def normalize(text):
    return " ".join(re.findall(r"[a-zа-я0-9]+", (text or "").lower().replace("ё", "е")))


def extract_app_name(query):
    """"открой, пожалуйста, калькулятор" -> "калькулятор"; None when the phrase is not about starting a program."""
    words = normalize(query).split()
    if not words or words[0] not in OPEN_WORDS:
        return None
    rest = [w for w in words[1:] if w not in FILLER_WORDS]
    return " ".join(rest) or None


class AppLauncher:
    def __init__(self, path=None, opener=None, shortcut_dirs=None):
        self.path = Path(path) if path else DEFAULT_FILE
        self._opener = opener or self._default_open
        self._shortcut_dirs = shortcut_dirs if shortcut_dirs is not None else self._start_menu_dirs()
        self._lock = threading.Lock()
        self.user_apps = self._load()
        self._menu_apps = None

    # ---------- Lists ----------

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            logger.exception("Не удалось прочитать %s", self.path)
            return []
        return [a for a in data if isinstance(a, dict) and a.get("name") and a.get("target")]

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.user_apps, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.exception("Не удалось сохранить %s", self.path)

    @staticmethod
    def _start_menu_dirs():
        dirs = []
        for variable in ("ProgramData", "APPDATA"):
            base = os.environ.get(variable)
            if base:
                dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        return dirs

    def menu_apps(self):
        """Shortcuts of the Start menu (read once, on the first use)."""
        with self._lock:
            if self._menu_apps is None:
                found = {}
                for folder in self._shortcut_dirs:
                    if not Path(folder).is_dir():
                        continue
                    for shortcut in Path(folder).rglob("*.lnk"):
                        name = shortcut.stem
                        if _SKIP_SHORTCUTS.search(name) or name.lower() in found:
                            continue
                        found[name.lower()] = {"name": name, "aliases": [], "target": str(shortcut), "source": "menu"}
                self._menu_apps = sorted(found.values(), key=lambda a: a["name"].lower())
            return list(self._menu_apps)

    def all_apps(self):
        apps = [{"name": n, "aliases": list(a), "target": t, "source": "user", "index": i}
                for i, (n, a, t) in enumerate(self.user_app_tuples())]
        apps += [{"name": n, "aliases": list(a), "target": t, "source": "builtin"} for n, a, t in BUILTIN_APPS]
        known = {normalize(a["name"]) for a in apps}
        apps += [a for a in self.menu_apps() if normalize(a["name"]) not in known]
        return apps

    def user_app_tuples(self):
        return [(a["name"], tuple(a.get("aliases") or ()), a["target"]) for a in self.user_apps]

    # ---------- Changes ----------

    def add_app(self, name, target, aliases=()):
        name = name.strip()
        if not name or not target:
            raise ValueError("Нужны название и путь")
        self.user_apps = [a for a in self.user_apps if normalize(a["name"]) != normalize(name)]
        self.user_apps.append({"name": name, "aliases": [a.strip() for a in aliases if a.strip()], "target": str(target)})
        self._save()

    def remove_app(self, name):
        before = len(self.user_apps)
        self.user_apps = [a for a in self.user_apps if normalize(a["name"]) != normalize(name)]
        if len(self.user_apps) != before:
            self._save()
            return True
        return False

    # ---------- Finding and starting ----------

    @staticmethod
    def _spellings(app):
        names = [app["name"]] + list(app.get("aliases") or ())
        spellings = set()
        for name in names:
            plain = normalize(name)
            if plain:
                spellings.add(plain)
                if re.search(r"[a-z]", plain):
                    spellings.add(translit(plain))
        return spellings

    def find(self, text):
        """The best program for the words of a phrase: (app, score) or (None, 0)."""
        query = normalize(text)
        if not query:
            return None, 0
        best, best_score = None, 0
        for app in self.all_apps():
            for spelling in self._spellings(app):
                score = 100 if spelling == query else fuzz.ratio(query, spelling)
                if score > best_score:
                    best, best_score = app, score
        if best_score < MATCH_THRESHOLD:
            return None, best_score
        return best, best_score

    def open(self, app):
        self._opener(app["target"])
        logger.info("Запущено приложение: %s (%s)", app["name"], app["target"])

    @staticmethod
    def _default_open(target):
        if hasattr(os, "startfile"):
            os.startfile(target)
        else:
            subprocess.Popen(["xdg-open", target] if sys.platform != "darwin" else ["open", target])
