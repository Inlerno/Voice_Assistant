"""Voice and text commands for timers, alarms, reminders, tasks and programs.

Productivity.handle(query) returns the answer to say, or None when the phrase is about something else (then the
assistant goes on with its other commands). Phrases come from the recognizer in lower case without punctuation."""
import logging
from datetime import datetime, timedelta

from rapidfuzz import fuzz

from src.productivity import ru_parse as rp
from src.productivity.apps import extract_app_name
from src.productivity.planner import ALARM, REMINDER, TIMER, seconds_left

logger = logging.getLogger(__name__)

CANCEL_WORDS = {"отмени", "отменить", "отмена", "удали", "удалить", "останови", "остановить", "сбрось", "выключи",
                "отключи", "убери", "сними", "очисти", "сотри", "забудь"}
LIST_WORDS = {"какие", "какой", "сколько", "осталось", "покажи", "показать", "скажи", "список", "перечисли",
              "прочитай", "есть", "что", "расскажи", "назови"}
SET_STOP_WORDS = {"поставь", "поставить", "установи", "установить", "запусти", "запустить", "засеки", "включи", "заведи",
                  "создай", "сделай", "мне", "пожалуйста", "и", "на", "в", "через", "меня", "ка", "добавь",
                  "новый", "новое", "новая", "разбуди", "разбудить"}
TASK_ADD_WORDS = {"добавь", "добавить", "запиши", "записать", "запомни", "создай", "создать", "новая", "поставь",
                  "занеси", "внеси"}
TASK_DONE_WORDS = {"выполни", "выполнить", "выполнено", "выполнена", "выполнен", "отметь", "отметить", "заверши",
                   "завершить", "сделано", "сделана", "готово", "готова", "закрой", "вычеркни"}
TASK_LIST_WORDS = LIST_WORDS | {"мои", "мой", "у", "моих"}
TASK_STOP_WORDS = {"в", "во", "на", "список", "списке", "списка", "мои", "мой", "моих", "мне", "задачу", "задача",
                   "задачи", "задач", "задачам", "дел", "дела", "делам", "делах", "новую", "новая", "пожалуйста", "что",
                   "чтобы", "нужно", "надо", "туда", "ещё", "еще"}
ALL_WORDS = {"все", "всё", "всех", "каждый", "каждое"}
DEFAULT_REMINDER_HOUR = 9
MAX_SPOKEN_ITEMS = 5


def _starts(tokens, prefix):
    return any(t.startswith(prefix) for t in tokens)


def _number_words(n):
    return {1: "один", 2: "два", 3: "три", 4: "четыре", 5: "пять"}.get(n, str(n))


class Productivity:
    def __init__(self, planner, apps, clock=datetime.now):
        self.planner = planner
        self.apps = apps
        self.clock = clock

    def now(self):
        return self.clock().replace(microsecond=0)

    # ---------- Entry points ----------

    def handle(self, query):
        tokens = rp.tokenize(query)
        if not tokens:
            return None
        if _starts(tokens, "таймер") or (tokens[0] in ("засеки", "засечь") and rp.find_duration(tokens)):
            return self.timer(tokens)
        if _starts(tokens, "будильник") or tokens[0] in ("разбуди", "разбудить", "подними"):
            return self.alarm(tokens)
        if _starts(tokens, "напомин") or _starts(tokens, "напомн"):
            return self.reminder(tokens)
        if self._is_task_phrase(tokens):
            return self.task(tokens)
        return None

    def open_app(self, query):
        """"открой калькулятор": starts the program and returns what to say, None when no program fits."""
        name = extract_app_name(query)
        if not name:
            return None
        app, _score = self.apps.find(name)
        if app is None:
            return None
        try:
            self.apps.open(app)
        except OSError:
            logger.exception("Не удалось открыть %s", app["name"])
            return f"Не получилось открыть {app['name']}. Возможно, программа не установлена."
        return f"Открываю {app['name']}"

    # ---------- Timers ----------

    def timer(self, tokens):
        words = set(tokens)
        if words & CANCEL_WORDS:
            return self._cancel(TIMER, "таймер", "таймеры", all_requested=bool(words & ALL_WORDS) or "таймеры" in words)
        found = rp.find_duration(tokens)
        if found is None:
            if words & LIST_WORDS or len(tokens) == 1:
                return self._describe(TIMER)
            return "На сколько поставить таймер? Скажите, например: таймер на десять минут."
        seconds, start, end = found
        label = " ".join(w for w in rp.cut(tokens, (start, end)) if w not in SET_STOP_WORDS
                         and not w.startswith("таймер"))
        if words & {"сколько", "осталось"}:
            return self._describe(TIMER)
        item = self.planner.add_timer(seconds, label)
        suffix = f" — {label}" if label else ""
        return f"Поставила таймер на {rp.describe_duration(item['length'], accusative=True)}{suffix}."

    # ---------- Alarms ----------

    def alarm(self, tokens):
        words = set(tokens)
        if words & CANCEL_WORDS:
            return self._cancel(ALARM, "будильник", "будильники",
                                all_requested=bool(words & ALL_WORDS) or "будильники" in words)
        now = self.now()
        repeat = rp.find_repeat(tokens)
        working = rp.cut(tokens, (repeat[1], repeat[2])) if repeat else tokens
        moment = self._moment(working, now, prefer_morning=True)
        if moment is None:
            if words & LIST_WORDS:
                return self._describe(ALARM)
            return "На какое время поставить будильник? Скажите, например: будильник на семь тридцать."
        kind = repeat[0] if repeat else None
        moment = self._skip_weekend(moment, kind)
        self.planner.add_alarm(moment, repeat=kind)
        when = rp.describe_moment(moment, now)
        if kind:
            when += " и затем " + ("каждый день" if kind == "daily" else "по будням")
        return f"Поставила будильник на {when}."

    # ---------- Reminders ----------

    def reminder(self, tokens):
        words = set(tokens)
        if words & CANCEL_WORDS:
            return self._cancel(REMINDER, "напоминание", "напоминания",
                                all_requested=bool(words & ALL_WORDS) or _starts(tokens, "напоминания"))
        now = self.now()
        repeat = rp.find_repeat(tokens)
        working = rp.cut(tokens, (repeat[1], repeat[2])) if repeat else list(tokens)
        moment, spans = self._moment(working, now, prefer_morning=False, want_spans=True) or (None, [])
        text_tokens = rp.cut(working, *spans) if spans else working
        text = " ".join(w for w in text_tokens if w not in SET_STOP_WORDS and not w.startswith("напомин")
                        and not w.startswith("напомн"))
        for lead in ("что ", "чтобы "):
            if text.startswith(lead) and len(text) > len(lead) + 2:
                text = text[len(lead):]
        if moment is None:
            if not text or words & LIST_WORDS:
                return self._describe(REMINDER)
            return "Когда напомнить? Скажите, например: напомни через десять минут позвонить маме."
        kind = repeat[0] if repeat else None
        moment = self._skip_weekend(moment, kind)
        self.planner.add_reminder(moment, text or "Напоминание", kind)
        when = rp.describe_moment(moment, now)
        if kind:
            when += " и затем " + ("каждый день" if kind == "daily" else "по будням")
        return f"Хорошо, напомню {when}" + (f": {text}." if text else ".")

    @staticmethod
    def _skip_weekend(moment, repeat):
        """"По будням" starts on the next working day."""
        if repeat == "weekdays":
            while moment.weekday() >= 5:
                moment += timedelta(days=1)
        return moment

    # ---------- Time of a phrase ----------

    def _moment(self, tokens, now, prefer_morning, want_spans=False):
        """The moment the phrase talks about: in a while ("через ..."), at a clock time, or on a day."""
        spans = []
        moment = None
        if "через" in tokens:
            found = rp.find_duration(tokens)
            if found:
                seconds, start, end = found
                moment = now + timedelta(seconds=seconds)
                begin = start - 1 if start > 0 and tokens[start - 1] == "через" else start
                spans = [(begin, end)]
        if moment is None:
            clock = rp.find_clock(tokens)
            date = rp.find_date(tokens, now)
            if clock:
                hour, minute, period, start, end = clock
                spans.append((start, end))
                if date:
                    day, d_start, d_end = date
                    spans.append((d_start, d_end))
                    if period is None and hour <= 12 and day == now.date() and not prefer_morning:
                        moment = rp_next(now, hour, minute, period, prefer_morning)
                    else:
                        moment = datetime.combine(day, datetime.min.time()).replace(
                            hour=rp._apply_period(hour, period) if period else hour, minute=minute)
                        if moment <= now:
                            moment = rp_next(now, hour, minute, period, prefer_morning)
                else:
                    moment = rp_next(now, hour, minute, period, prefer_morning)
            elif date:
                day, d_start, d_end = date
                spans.append((d_start, d_end))
                moment = datetime.combine(day, datetime.min.time()).replace(hour=DEFAULT_REMINDER_HOUR)
                if moment <= now:
                    moment = None
            elif not prefer_morning:
                found = rp.find_duration(tokens)
                if found and tokens and tokens[found[1] - 1:found[1]] != []:
                    seconds, start, end = found
                    moment = now + timedelta(seconds=seconds)
                    spans = [(start, end)]
        if moment is None:
            return None
        return (moment, spans) if want_spans else moment

    # ---------- Cancel and read out ----------

    def _cancel(self, kind, single, plural, all_requested):
        items = self.planner.items(kind)
        if not items:
            return f"Нет активных: {plural}."
        if all_requested or len(items) == 1:
            count = self.planner.clear(kind)
            return f"Удалила {plural}: {count}." if count > 1 else f"Удалила {single}."
        self.planner.remove(items[0]["id"])
        return f"Удалила ближайший {single}." if kind != REMINDER else "Удалила ближайшее напоминание."

    def _describe(self, kind):
        items = self.planner.items(kind)
        names = {TIMER: ("таймер", "таймера", "таймеров"), ALARM: ("будильник", "будильника", "будильников"),
                 REMINDER: ("напоминание", "напоминания", "напоминаний")}[kind]
        if not items:
            return {TIMER: "Таймеров нет.", ALARM: "Будильников нет.", REMINDER: "Напоминаний нет."}[kind]
        now = self.now()
        parts = []
        for item in items[:MAX_SPOKEN_ITEMS]:
            if kind == TIMER:
                left = rp.describe_duration(max(1, seconds_left(item, now)))
                parts.append(f"{item['text'] + ': ' if item['text'] else ''}осталось {left}")
            elif kind == ALARM:
                parts.append(rp.describe_moment(datetime.strptime(item["due"], "%Y-%m-%dT%H:%M:%S"), now))
            else:
                when = rp.describe_moment(datetime.strptime(item["due"], "%Y-%m-%dT%H:%M:%S"), now)
                parts.append(f"{when} — {item['text']}")
        total = f"{len(items)} {rp.plural(len(items), names)}"
        return f"Сейчас {total}: " + "; ".join(parts) + "."

    # ---------- Tasks ----------

    @staticmethod
    def _is_task_phrase(tokens):
        words = set(tokens)
        has_task = _starts(tokens, "задач") or "дел" in words or "делах" in words
        if not has_task:
            return False
        return bool(words & (TASK_ADD_WORDS | TASK_DONE_WORDS | TASK_LIST_WORDS | CANCEL_WORDS))

    def task(self, tokens):
        words = set(tokens)
        if words & CANCEL_WORDS:
            return self._task_delete(tokens)
        if words & TASK_DONE_WORDS:
            return self._task_complete(tokens)
        if words & TASK_ADD_WORDS:
            text = self._task_text(tokens)
            if not text:
                return "Что добавить в задачи? Скажите, например: добавь задачу купить молоко."
            self.planner.add_task(text)
            return f"Добавила задачу: {text}."
        return self._task_list()

    @staticmethod
    def _task_text(tokens):
        """The text of a new task: what is left after the command words and "в список задач"."""
        start = 0
        for index, word in enumerate(tokens):
            if word.startswith("задач") or word in ("дел", "дела", "делах"):
                start = index + 1
        tail = [w for w in tokens[start:]]
        while tail and tail[0] in TASK_STOP_WORDS:
            tail.pop(0)
        if tail:
            return " ".join(tail)
        head = [w for w in tokens[:max(start - 1, 0)] if w not in TASK_ADD_WORDS and w not in TASK_STOP_WORDS]
        return " ".join(head)

    def _task_list(self):
        pending = self.planner.tasks(done=False)
        if not pending:
            return "Список задач пуст."
        parts = [f"Номер {i} — {t['text']}" for i, t in enumerate(pending[:MAX_SPOKEN_ITEMS], start=1)]
        more = len(pending) - MAX_SPOKEN_ITEMS
        tail = f" И ещё {more}." if more > 0 else ""
        return f"Задач: {len(pending)}. " + ". ".join(parts) + "." + tail

    def _find_task(self, tokens, pending_only):
        """The task a phrase points at: by number ("задачу два") or by its words."""
        tasks = self.planner.tasks(done=False) if pending_only else self.planner.tasks()
        number = None
        for index in range(len(tokens)):
            parsed = rp.read_number(tokens, index)
            if parsed:
                number = parsed[0]
                break
        if number is not None and 1 <= number <= len(tasks):
            return tasks[number - 1]
        query = " ".join(w for w in tokens if w not in TASK_STOP_WORDS and w not in TASK_DONE_WORDS
                         and w not in CANCEL_WORDS and w not in TASK_ADD_WORDS and not w.startswith("задач"))
        best, best_score = None, 0
        for task in tasks:
            score = max(fuzz.partial_ratio(query, task["text"].lower()), fuzz.ratio(query, task["text"].lower()))
            if score > best_score:
                best, best_score = task, score
        return best if query and best_score >= 75 else None

    def _task_complete(self, tokens):
        task = self._find_task(tokens, pending_only=True)
        if task is None:
            return "Не нашла такую задачу. Скажите номер, например: выполни задачу два."
        self.planner.set_done(task["id"])
        return f"Отметила выполненной: {task['text']}."

    def _task_delete(self, tokens):
        words = set(tokens)
        if "выполненные" in words or "выполненных" in words or "готовые" in words or "сделанные" in words:
            count = self.planner.clear_done()
            return f"Убрала выполненные задачи: {count}." if count else "Выполненных задач нет."
        if words & ALL_WORDS or "список" in words:
            tasks = self.planner.tasks()
            for task in tasks:
                self.planner.remove_task(task["id"])
            return "Очистила список задач." if tasks else "Список задач пуст."
        task = self._find_task(tokens, pending_only=False)
        if task is None:
            return "Не нашла такую задачу. Скажите номер, например: удали задачу два."
        self.planner.remove_task(task["id"])
        return f"Удалила задачу: {task['text']}."


def rp_next(now, hour, minute, period, prefer_morning):
    return rp.next_clock_time(now, hour, minute, period, prefer_morning)
