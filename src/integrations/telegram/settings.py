"""Settings of the Telegram bot: the token, who may give orders, a few switches. They are kept in data/telegram.json.

The file holds the token (a password to the bot), so it is written for its owner only where the system allows that,
the folder data/ is not in git and the token is never shown in the program except in a field that hides it.
"""
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from src.integrations.telegram.api import valid_token
from src.productivity.planner import DATA_DIR

logger = logging.getLogger(__name__)

SETTINGS_FILE = DATA_DIR / "telegram.json"
PROXY_FORM = re.compile(r"(https?|socks4|socks4a|socks5|socks5h)://[^\s/]+(:\d+)?/?", re.IGNORECASE)
BASE_FORM = re.compile(r"https?://[^\s/]+(/\S*)?", re.IGNORECASE)


class TelegramSettings:
    def __init__(self, path=None):
        self.path = Path(path) if path else SETTINGS_FILE
        self._lock = threading.RLock()
        self.token = ""
        self.enabled = False
        # The accounts that may give orders: {"id": 123, "name": "Аня", "username": "anya", "since": "2026-11-02 07:00"}
        self.users = []
        # Say the answers aloud on the computer too, not only write them to the chat
        self.speak_on_pc = False
        # Ask for a tap on a button before a command that turns the computer off and the like
        self.confirm_risky = True
        # Send the timers, alarms and reminders that come due to the chat too (the person may be far from the computer)
        self.notify_planner = True
        # A proxy for the connection to Telegram: http://host:port or socks5h://host:port
        self.proxy = ""
        # Another address of the Bot API (a mirror or a server of your own); empty: the official one
        self.api_base = ""
        self.load()

    # ---------- File ----------

    def load(self):
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return
            except (OSError, ValueError):
                logger.warning("Не удалось прочитать настройки Telegram (%s), беру значения по умолчанию", self.path.name)
                return
            if not isinstance(data, dict):
                return
            token = data.get("token")
            self.token = token.strip() if isinstance(token, str) and valid_token(token.strip()) else ""
            self.enabled = data.get("enabled") is True
            self.speak_on_pc = data.get("speak_on_pc") is True
            self.confirm_risky = data.get("confirm_risky") is not False
            self.notify_planner = data.get("notify_planner") is not False
            proxy, base = data.get("proxy"), data.get("api_base")
            self.proxy = proxy.strip() if isinstance(proxy, str) and PROXY_FORM.fullmatch(proxy.strip()) else ""
            self.api_base = base.strip().rstrip("/") if isinstance(base, str) and BASE_FORM.fullmatch(base.strip()) else ""
            self.users = []
            for item in data.get("users") if isinstance(data.get("users"), list) else []:
                if isinstance(item, dict) and isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool):
                    self.users.append({"id": item["id"], "name": str(item.get("name") or ""),
                                       "username": str(item.get("username") or ""), "since": str(item.get("since") or "")})

    def save(self):
        with self._lock:
            data = {"token": self.token, "enabled": self.enabled, "users": self.users,
                    "speak_on_pc": self.speak_on_pc, "confirm_risky": self.confirm_risky,
                    "notify_planner": self.notify_planner, "proxy": self.proxy, "api_base": self.api_base}
            temporary = self.path.with_name(self.path.name + ".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass
                os.replace(temporary, self.path)
            except OSError:
                logger.exception("Не удалось сохранить настройки Telegram")
                try:
                    temporary.unlink()
                except OSError:
                    pass

    # ---------- Changes ----------

    def update(self, **changes):
        """Changes the settings and saves them. Returns the names of those that really changed.
        A value that cannot be accepted raises ValueError with a text for the user, and nothing is changed."""
        with self._lock:
            clean = {}
            for name, value in changes.items():
                if name == "token":
                    value = (value or "").strip()
                    if value and not valid_token(value):
                        raise ValueError("Токен выглядит неверно. Он должен быть такого вида: 123456789:AAE-...")
                elif name == "proxy":
                    value = (value or "").strip()
                    if value and not PROXY_FORM.fullmatch(value):
                        raise ValueError("Прокси записывается так: http://адрес:порт или socks5h://адрес:порт")
                elif name == "api_base":
                    value = (value or "").strip().rstrip("/")
                    if value and not BASE_FORM.fullmatch(value):
                        raise ValueError("Адрес сервера должен начинаться с http:// или https://")
                elif name in ("enabled", "speak_on_pc", "confirm_risky", "notify_planner"):
                    value = bool(value)
                else:
                    raise ValueError(f"Неизвестная настройка: {name}")
                clean[name] = value
            changed = {name for name, value in clean.items() if getattr(self, name) != value}
            for name in changed:
                setattr(self, name, clean[name])
            if changed:
                self.save()
            return changed

    # ---------- Accounts ----------

    @property
    def ready(self):
        """The bot may work: it is switched on and has a token."""
        return self.enabled and bool(self.token)

    def is_allowed(self, user_id):
        with self._lock:
            return any(user["id"] == user_id for user in self.users)

    def users_snapshot(self):
        with self._lock:
            return [dict(user) for user in self.users]

    def link(self, user_id, name="", username=""):
        with self._lock:
            self.users = [user for user in self.users if user["id"] != user_id]
            self.users.append({"id": user_id, "name": name, "username": username,
                               "since": datetime.now().strftime("%Y-%m-%d %H:%M")})
            self.save()

    def unlink(self, user_id):
        """Returns True when there was such an account."""
        with self._lock:
            remaining = [user for user in self.users if user["id"] != user_id]
            found = len(remaining) != len(self.users)
            if found:
                self.users = remaining
                self.save()
            return found
