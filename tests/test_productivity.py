"""Timers, alarms, reminders, tasks and starting programs."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.productivity import ru_parse as rp  # noqa: E402
from src.productivity.apps import AppLauncher, extract_app_name, translit  # noqa: E402
from src.productivity.planner import ALARM, REMINDER, TIMER, Planner  # noqa: E402
from src.productivity.skills import Productivity  # noqa: E402

NOW = datetime(2026, 10, 28, 7, 50, 10)  # a Wednesday


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class ParseTests(unittest.TestCase):
    def duration(self, text):
        found = rp.find_duration(rp.tokenize(text))
        return found and found[0]

    def clock(self, text):
        found = rp.find_clock(rp.tokenize(text))
        return found and found[:3]

    def test_durations(self):
        self.assertEqual(self.duration("таймер на пять минут"), 300)
        self.assertEqual(self.duration("на полчаса"), 1800)
        self.assertEqual(self.duration("на час тридцать"), 5400)
        self.assertEqual(self.duration("через два с половиной часа"), 9000)
        self.assertEqual(self.duration("на двадцать пять минут"), 1500)
        self.assertEqual(self.duration("на 90 секунд"), 90)
        self.assertEqual(self.duration("через час"), 3600)
        self.assertIsNone(self.duration("привет как дела"))

    def test_clock_times(self):
        self.assertEqual(self.clock("будильник на семь тридцать"), (7, 30, None))
        self.assertEqual(self.clock("разбуди в 7:30"), (7, 30, None))
        self.assertEqual(self.clock("в восемь вечера"), (8, 0, "pm"))
        self.assertEqual(self.clock("в половине девятого"), (8, 30, None))
        self.assertEqual(self.clock("без пятнадцати семь"), (6, 45, None))

    def test_a_bare_number_is_not_a_clock(self):
        self.assertIsNone(self.clock("поставь два таймера"))

    def test_next_clock_time(self):
        self.assertEqual(rp.next_clock_time(NOW, 7, 30, prefer_morning=True), datetime(2026, 10, 29, 7, 30))
        self.assertEqual(rp.next_clock_time(NOW, 8, 0, "pm"), datetime(2026, 10, 28, 20, 0))
        # without a period a reminder takes the nearest of morning and evening
        self.assertEqual(rp.next_clock_time(NOW, 8, 30), datetime(2026, 10, 28, 8, 30))

    def test_words_for_answers(self):
        self.assertEqual(rp.describe_duration(300), "5 минут")
        self.assertEqual(rp.describe_duration(5400), "1 час 30 минут")
        self.assertEqual(rp.describe_duration(21), "21 секунда")
        self.assertEqual(rp.describe_duration(21, accusative=True), "21 секунду")
        self.assertEqual(rp.describe_duration(60, accusative=True), "1 минуту")
        self.assertEqual(rp.describe_moment(datetime(2026, 10, 29, 7, 30), NOW), "завтра в 07:30")


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "planner.json"
        self.clock = Clock()
        self.fired = []
        self.changes = 0
        self.planner = Planner(self.path, on_fire=self.fired.append, on_change=self._changed, clock=self.clock)

    def tearDown(self):
        self.dir.cleanup()

    def _changed(self):
        self.changes += 1

    def test_timer_fires_once(self):
        self.planner.add_timer(300, "чай")
        self.clock.now = NOW + timedelta(seconds=299)
        self.assertEqual(self.planner.check(), [])
        self.clock.now = NOW + timedelta(seconds=300)
        fired = self.planner.check()
        self.assertEqual([i["text"] for i in fired], ["чай"])
        self.assertEqual(self.fired[0]["kind"], TIMER)
        self.assertEqual(self.planner.items(), [])
        self.assertEqual(self.planner.check(), [])

    def test_repeating_alarm_is_rescheduled(self):
        self.planner.add_alarm(datetime(2026, 10, 28, 8, 0), repeat="daily")
        self.clock.now = datetime(2026, 10, 28, 8, 0, 1)
        self.assertEqual(len(self.planner.check()), 1)
        (item,) = self.planner.items()
        self.assertEqual(item["due"], "2026-10-29T08:00:00")

    def test_weekday_alarm_skips_the_weekend(self):
        # Friday 30 October 2026
        self.planner.add_alarm(datetime(2026, 10, 30, 7, 0), repeat="weekdays")
        self.clock.now = datetime(2026, 10, 30, 7, 0, 1)
        self.planner.check()
        (item,) = self.planner.items()
        self.assertEqual(item["due"], "2026-11-02T07:00:00")

    def test_data_survives_a_restart(self):
        self.planner.add_reminder(NOW + timedelta(hours=2), "позвонить маме")
        self.planner.add_task("купить молоко")
        again = Planner(self.path, clock=self.clock)
        self.assertEqual([i["text"] for i in again.items(REMINDER)], ["позвонить маме"])
        self.assertEqual([t["text"] for t in again.tasks()], ["купить молоко"])

    def test_missed_reminder_fires_after_start_but_old_timer_is_dropped(self):
        self.planner.add_reminder(NOW + timedelta(minutes=10), "документы")
        self.planner.add_timer(60)
        later = Clock(NOW + timedelta(hours=1))
        again = Planner(self.path, clock=later)
        kinds = [(i["kind"], i.get("missed")) for i in again.items()]
        self.assertEqual(kinds, [(REMINDER, True)])
        self.assertEqual(len(again.check()), 1)

    def test_broken_file_does_not_stop_the_start(self):
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(Planner(self.path, clock=self.clock).items(), [])

    def test_remove_clear_and_snooze(self):
        first = self.planner.add_timer(60)
        self.planner.add_timer(120)
        self.assertTrue(self.planner.remove(first["id"]))
        self.assertFalse(self.planner.remove("nothing"))
        self.assertEqual(self.planner.clear(TIMER), 1)
        item = self.planner.add_alarm(NOW + timedelta(minutes=1), "")
        snoozed = self.planner.snooze(item, 5)
        self.assertEqual(snoozed["kind"], REMINDER)
        self.assertEqual(snoozed["due"], "2026-10-28T07:55:10")

    def test_tasks(self):
        a = self.planner.add_task("первая")
        self.planner.add_task("вторая")
        self.assertTrue(self.planner.set_done(a["id"]))
        self.assertEqual([t["text"] for t in self.planner.tasks(done=False)], ["вторая"])
        self.assertEqual(self.planner.clear_done(), 1)
        self.assertEqual(len(self.planner.tasks()), 1)

    def test_every_change_is_reported(self):
        before = self.changes
        self.planner.add_task("x")
        self.planner.add_timer(10)
        self.assertEqual(self.changes, before + 2)

    def test_saved_file_is_readable_json(self):
        self.planner.add_task("проверка")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["tasks"][0]["text"], "проверка")


class PhraseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.clock = Clock()
        self.planner = Planner(base / "p.json", clock=self.clock)
        self.opened = []
        self.apps = AppLauncher(base / "a.json", opener=self.opened.append, shortcut_dirs=[])
        self.skills = Productivity(self.planner, self.apps, clock=self.clock)

    def tearDown(self):
        self.dir.cleanup()

    def say(self, phrase):
        return self.skills.handle(phrase)

    def test_timer(self):
        self.assertIn("5 минут", self.say("поставь таймер на пять минут"))
        (item,) = self.planner.items(TIMER)
        self.assertEqual(item["length"], 300)
        self.assertEqual(item["due"], "2026-10-28T07:55:10")

    def test_timer_with_a_name(self):
        self.say("таймер на час тридцать чай")
        (item,) = self.planner.items(TIMER)
        self.assertEqual((item["length"], item["text"]), (5400, "чай"))

    def test_timer_without_a_time_asks(self):
        self.assertIn("На сколько", self.say("поставь таймер"))
        self.assertEqual(self.planner.items(), [])

    def test_timer_list_and_cancel(self):
        self.assertEqual(self.say("какие таймеры"), "Таймеров нет.")
        self.say("таймер на пять минут")
        self.say("таймер на десять минут")
        self.assertIn("2 таймера", self.say("какие таймеры"))
        self.say("отмени таймер")
        self.assertEqual(len(self.planner.items(TIMER)), 1)
        self.say("отмени все таймеры")
        self.assertEqual(self.planner.items(TIMER), [])

    def test_alarm_goes_to_tomorrow_when_the_time_has_passed(self):
        self.assertIn("завтра в 07:30", self.say("поставь будильник на семь тридцать"))
        (item,) = self.planner.items(ALARM)
        self.assertEqual(item["due"], "2026-10-29T07:30:00")

    def test_alarm_in_the_evening_and_repeat(self):
        self.say("разбуди меня в шесть утра каждый день")
        (item,) = self.planner.items(ALARM)
        self.assertEqual((item["due"], item["repeat"]), ("2026-10-29T06:00:00", "daily"))

    def test_weekday_alarm_never_starts_on_a_weekend(self):
        self.clock.now = datetime(2026, 10, 30, 12, 0)  # Friday noon: seven o'clock is on Saturday
        self.say("будильник на семь утра по будням")
        (item,) = self.planner.items(ALARM)
        self.assertEqual(item["due"], "2026-11-02T07:00:00")

    def test_reminder_in_a_while(self):
        answer = self.say("напомни через десять минут позвонить маме")
        self.assertIn("позвонить маме", answer)
        (item,) = self.planner.items(REMINDER)
        self.assertEqual((item["text"], item["due"]), ("позвонить маме", "2026-10-28T08:00:10"))

    def test_reminder_at_a_time_and_day(self):
        self.say("напомни завтра в девять утра про встречу")
        (item,) = self.planner.items(REMINDER)
        self.assertEqual((item["text"], item["due"]), ("про встречу", "2026-10-29T09:00:00"))

    def test_reminder_for_a_day_only_is_set_for_nine(self):
        self.say("напомни завтра купить молоко")
        (item,) = self.planner.items(REMINDER)
        self.assertEqual(item["due"], "2026-10-29T09:00:00")

    def test_reminder_without_a_time_asks(self):
        self.assertIn("Когда напомнить", self.say("напомни позвонить"))
        self.assertEqual(self.planner.items(), [])

    def test_typed_clock_time_with_digits(self):
        self.say("будильник на 7:30")
        self.assertEqual(self.planner.items(ALARM)[0]["due"], "2026-10-29T07:30:00")

    def test_tasks(self):
        self.say("добавь задачу купить молоко")
        self.say("добавь в список дел позвонить врачу")
        self.say("запиши в задачи оплатить интернет")
        self.assertEqual([t["text"] for t in self.planner.tasks()],
                         ["купить молоко", "позвонить врачу", "оплатить интернет"])
        self.assertIn("Задач: 3", self.say("что в списке задач"))
        self.assertIn("позвонить врачу", self.say("выполни задачу два"))
        self.assertEqual([t["text"] for t in self.planner.tasks(done=True)], ["позвонить врачу"])
        self.say("отметь задачу оплатить интернет выполненной")
        self.assertEqual(len(self.planner.tasks(done=True)), 2)
        self.say("удали задачу один")
        self.assertEqual([t["text"] for t in self.planner.tasks(done=False)], [])
        self.assertIn("выполненные", self.say("очисти выполненные задачи"))
        self.assertEqual(self.planner.tasks(), [])

    def test_empty_task_list_and_empty_text(self):
        self.assertEqual(self.say("какие у меня задачи"), "Список задач пуст.")
        self.assertIn("Что добавить", self.say("добавь задачу"))

    def test_other_phrases_are_left_alone(self):
        for phrase in ("как дела", "реши задачу по математике", "сколько времени", "включи музыку", "открой браузер"):
            self.assertIsNone(self.say(phrase), phrase)

    def test_open_app(self):
        self.assertEqual(self.skills.open_app("запусти блокнот пожалуйста"), "Открываю Блокнот")
        self.assertEqual(self.opened, ["notepad"])

    def test_unknown_app_is_left_for_the_language_model(self):
        self.assertIsNone(self.skills.open_app("открой что-то странное"))
        self.assertIsNone(self.skills.open_app("какая погода"))
        self.assertEqual(self.opened, [])

    def test_app_that_fails_to_start(self):
        def broken(_target):
            raise FileNotFoundError("no such program")

        self.apps._opener = broken
        self.assertIn("Не получилось", self.skills.open_app("открой калькулятор"))


class AppTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.base = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_translit_and_phrase_cleanup(self):
        self.assertEqual(translit("telegram"), "телеграм")
        self.assertEqual(translit("discord"), "дискорд")
        self.assertEqual(extract_app_name("Открой, пожалуйста, калькулятор"), "калькулятор")
        self.assertIsNone(extract_app_name("какая погода"))

    def test_user_apps_are_saved_and_found(self):
        launcher = AppLauncher(self.base / "a.json", opener=lambda t: None, shortcut_dirs=[])
        launcher.add_app("Фотошоп", "C:/Apps/photoshop.exe")
        again = AppLauncher(self.base / "a.json", shortcut_dirs=[])
        app, score = again.find("фотошоп")
        self.assertEqual((app["name"], app["source"], score), ("Фотошоп", "user", 100))
        self.assertTrue(again.remove_app("фотошоп"))
        self.assertIsNone(again.find("фотошоп")[0])

    def test_start_menu_shortcuts_are_found_by_a_russian_name(self):
        menu = self.base / "Programs"
        (menu / "Telegram Desktop").mkdir(parents=True)
        (menu / "Telegram Desktop" / "Telegram.lnk").write_text("")
        (menu / "Telegram Desktop" / "Uninstall Telegram.lnk").write_text("")
        launcher = AppLauncher(self.base / "a.json", opener=lambda t: None, shortcut_dirs=[menu])
        names = [a["name"] for a in launcher.menu_apps()]
        self.assertEqual(names, ["Telegram"])
        self.assertEqual(launcher.find("телеграм")[0]["name"], "Telegram")

    def test_a_close_but_wrong_name_is_not_started(self):
        launcher = AppLauncher(self.base / "a.json", shortcut_dirs=[])
        self.assertIsNone(launcher.find("калькуляция погоды")[0])


if __name__ == "__main__":
    unittest.main()
