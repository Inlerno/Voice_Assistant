"""Timers, alarms, reminders and tasks: stored in a JSON file and checked by a background thread."""
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_FILE = DATA_DIR / "planner.json"

TIMER, ALARM, REMINDER = "timer", "alarm", "reminder"
KINDS = (TIMER, ALARM, REMINDER)
KIND_TITLES = {TIMER: "Таймер", ALARM: "Будильник", REMINDER: "Напоминание"}
# A reminder that came due while the program was closed still fires, an old alarm or timer is just dropped
MISSED_REMINDER_LIMIT = timedelta(days=2)
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _now():
    return datetime.now().replace(microsecond=0)


def next_repeat(moment, repeat, now=None):
    """The next moment of a repeating alarm after now ("daily" or "weekdays")."""
    now = now or _now()
    moment += timedelta(days=1)
    while moment <= now or (repeat == "weekdays" and moment.weekday() >= 5):
        moment += timedelta(days=1)
    return moment


class Planner:
    """Everything with a time (timers, alarms, reminders) and the list of tasks.

    on_fire(item) is called from the checking thread when something comes due; on_change() after every change
    of the data. Both may be None."""

    def __init__(self, path=None, on_fire=None, on_change=None, clock=_now):
        self.path = Path(path) if path else DEFAULT_FILE
        self.on_fire = on_fire
        self.on_change = on_change
        self.clock = clock
        self._lock = threading.RLock()
        self._items = []
        self._tasks = []
        self._stop = threading.Event()
        self._thread = None
        self._load()

    # ---------- Storage ----------

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            logger.exception("Не удалось прочитать %s, список дел начат заново", self.path)
            return
        items = [i for i in data.get("items", []) if i.get("kind") in KINDS and i.get("due")]
        self._tasks = [t for t in data.get("tasks", []) if t.get("text")]
        now = self.clock()
        for item in items:
            due = datetime.strptime(item["due"], TIME_FORMAT)
            if due > now:
                self._items.append(item)
            elif item["kind"] == REMINDER and now - due < MISSED_REMINDER_LIMIT:
                item["missed"] = True
                self._items.append(item)
            elif item.get("repeat"):
                item["due"] = next_repeat(due, item["repeat"], now).strftime(TIME_FORMAT)
                self._items.append(item)

    def _save(self):
        data = {"items": self._items, "tasks": self._tasks}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            logger.exception("Не удалось сохранить %s", self.path)

    def _changed(self):
        self._save()
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                logger.exception("on_change callback error")

    # ---------- Timers, alarms and reminders ----------

    def _add(self, kind, due, text="", repeat=None, length=None):
        item = {
            "id": uuid.uuid4().hex[:8], "kind": kind, "text": text.strip(), "repeat": repeat,
            "due": due.strftime(TIME_FORMAT), "length": length,
        }
        with self._lock:
            self._items.append(item)
            self._items.sort(key=lambda i: i["due"])
            self._changed()
        return dict(item)

    def add_timer(self, seconds, text=""):
        return self._add(TIMER, self.clock() + timedelta(seconds=int(seconds)), text, length=int(seconds))

    def add_alarm(self, moment, text="", repeat=None):
        return self._add(ALARM, moment, text, repeat)

    def add_reminder(self, moment, text, repeat=None):
        return self._add(REMINDER, moment, text, repeat)

    def items(self, kind=None):
        with self._lock:
            return [dict(i) for i in self._items if kind is None or i["kind"] == kind]

    def find(self, item_id):
        with self._lock:
            return next((dict(i) for i in self._items if i["id"] == item_id), None)

    def remove(self, item_id):
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i["id"] != item_id]
            removed = len(self._items) != before
            if removed:
                self._changed()
        return removed

    def clear(self, kind):
        """Removes everything of one kind, returns how many items were removed."""
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i["kind"] != kind]
            count = before - len(self._items)
            if count:
                self._changed()
        return count

    def snooze(self, item, minutes=5):
        """Repeats a fired item after some minutes (a timer comes back as a timer)."""
        due = self.clock() + timedelta(minutes=minutes)
        kind = TIMER if item["kind"] == TIMER else REMINDER
        text = item.get("text") or ("" if kind == TIMER else KIND_TITLES[item["kind"]])
        return self._add(kind, due, text, length=minutes * 60 if kind == TIMER else None)

    # ---------- Checking ----------

    def check(self):
        """Fires everything that is due; returns the fired items. Called by the thread, also by the tests."""
        now = self.clock()
        fired = []
        with self._lock:
            for item in list(self._items):
                due = datetime.strptime(item["due"], TIME_FORMAT)
                if due > now:
                    continue
                fired.append(dict(item))
                if item.get("repeat"):
                    item["due"] = next_repeat(due, item["repeat"], now).strftime(TIME_FORMAT)
                else:
                    self._items.remove(item)
            if fired:
                self._items.sort(key=lambda i: i["due"])
                self._changed()
        for item in fired:
            if self.on_fire:
                try:
                    self.on_fire(item)
                except Exception:
                    logger.exception("on_fire callback error")
        return fired

    def start(self, interval=0.5):
        if self._thread is not None:
            return

        def loop():
            while not self._stop.wait(interval):
                try:
                    self.check()
                except Exception:
                    logger.exception("Ошибка при проверке таймеров")

        self._thread = threading.Thread(target=loop, daemon=True, name="planner")
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---------- Tasks ----------

    def add_task(self, text):
        task = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "done": False,
                "created": self.clock().strftime(TIME_FORMAT)}
        with self._lock:
            self._tasks.append(task)
            self._changed()
        return dict(task)

    def tasks(self, done=None):
        with self._lock:
            return [dict(t) for t in self._tasks if done is None or t["done"] == done]

    def set_done(self, task_id, done=True):
        with self._lock:
            for task in self._tasks:
                if task["id"] == task_id:
                    task["done"] = bool(done)
                    self._changed()
                    return True
        return False

    def remove_task(self, task_id):
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if t["id"] != task_id]
            removed = len(self._tasks) != before
            if removed:
                self._changed()
        return removed

    def clear_done(self):
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if not t["done"]]
            count = before - len(self._tasks)
            if count:
                self._changed()
        return count


def seconds_left(item, now=None):
    now = now or _now()
    return (datetime.strptime(item["due"], TIME_FORMAT) - now).total_seconds()

