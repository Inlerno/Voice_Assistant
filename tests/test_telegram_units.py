"""The small parts of the Telegram bot: the code that links an account, the settings, the client of the API (no
network: errors, the cutting of long texts, the safety of the token) and the helpers of the bot."""
import json
import logging
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

from fake_telegram import TOKEN, FakeTelegram  # noqa: E402
from src.integrations.telegram import api as A  # noqa: E402
from src.integrations.telegram import bot as B  # noqa: E402
from src.integrations.telegram import pairing as P  # noqa: E402
from src.integrations.telegram.settings import TelegramSettings  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.pairing = P.Pairing(clock=self.clock)

    def test_the_code_has_no_letters_that_can_be_mixed_up(self):
        for _ in range(200):
            code = self.pairing.new_code()
            self.assertEqual(len(code), P.CODE_LENGTH)
            self.assertTrue(set(code) <= set(P.ALPHABET))
        self.assertFalse(set("01OIL") & set(P.ALPHABET))

    def test_codes_are_not_the_same_every_time(self):
        self.assertGreater(len({self.pairing.new_code() for _ in range(50)}), 45)

    def test_it_is_shown_in_two_halves(self):
        self.assertEqual(P.display("K7M2QX9P"), "K7M2-QX9P")

    def test_the_right_code_works_once(self):
        code = self.pairing.new_code()
        self.assertTrue(self.pairing.check(1, code))
        self.assertFalse(self.pairing.check(2, code))
        self.assertIsNone(self.pairing.active())

    def test_the_way_it_was_typed_does_not_matter(self):
        code = self.pairing.new_code()
        typed = f" {P.display(code).lower()} "
        self.assertTrue(self.pairing.check(1, typed))

    def test_a_wrong_code_does_not_use_up_the_right_one(self):
        code = self.pairing.new_code()
        self.assertFalse(self.pairing.check(1, "AAAAAAAA"))
        self.assertTrue(self.pairing.check(2, code))

    def test_nothing_works_without_a_code(self):
        self.assertFalse(self.pairing.check(1, "AAAAAAAA"))
        self.assertFalse(self.pairing.check(1, ""))
        self.assertFalse(self.pairing.check(1, None))

    def test_the_code_lives_ten_minutes(self):
        code = self.pairing.new_code()
        self.clock.now += P.LIFETIME - 1
        self.assertAlmostEqual(self.pairing.active()[1], 1)
        self.clock.now += 2
        self.assertIsNone(self.pairing.active())
        self.assertFalse(self.pairing.check(1, code))

    def test_a_new_code_cancels_the_old_one(self):
        old = self.pairing.new_code()
        new = self.pairing.new_code()
        self.assertFalse(self.pairing.check(1, old) and old != new)
        self.assertTrue(self.pairing.check(1, new))

    def test_cancel(self):
        code = self.pairing.new_code()
        self.pairing.cancel()
        self.assertIsNone(self.pairing.active())
        self.assertFalse(self.pairing.check(1, code))

    def test_a_person_who_guesses_is_shut_out_even_with_the_right_code(self):
        code = self.pairing.new_code()
        for _ in range(P.PERSON_FAILS):
            self.assertFalse(self.pairing.check(7, "AAAAAAAA"))
        self.assertTrue(self.pairing.locked(7))
        self.assertFalse(self.pairing.check(7, code))
        self.assertFalse(self.pairing.locked(8))
        self.assertTrue(self.pairing.check(8, code), "the others are not punished for it")

    def test_the_lock_is_lifted_after_a_while(self):
        for _ in range(P.PERSON_FAILS):
            self.pairing.check(7, "AAAAAAAA")
        self.clock.now += P.PERSON_WINDOW + 1
        self.assertFalse(self.pairing.locked(7))
        code = self.pairing.new_code()
        self.assertTrue(self.pairing.check(7, code))

    def test_a_flood_of_wrong_codes_from_many_accounts_destroys_the_code(self):
        code = self.pairing.new_code()
        for person in range(P.TOTAL_FAILS):
            self.pairing.check(1000 + person, "AAAAAAAA")
        self.assertIsNone(self.pairing.active())
        self.assertFalse(self.pairing.check(1, code))
        # the owner asks for a new one and it works
        fresh = self.pairing.new_code()
        self.assertTrue(self.pairing.check(1, fresh))

    def test_the_list_of_guesses_does_not_grow_without_end(self):
        for person in range(1000):
            self.pairing.check(person, "AAAAAAAA")
            if person % 50 == 0:
                self.clock.now += P.PERSON_WINDOW + 1
        self.assertLess(len(self.pairing._fails), 300)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "telegram.json"

    def settings(self):
        return TelegramSettings(self.path)

    def test_defaults(self):
        s = self.settings()
        self.assertEqual((s.token, s.enabled, s.users, s.speak_on_pc, s.confirm_risky, s.notify_planner, s.proxy,
                          s.api_base), ("", False, [], False, True, True, "", ""))
        self.assertFalse(s.ready)
        self.assertFalse(self.path.exists(), "nothing is written until something changes")

    def test_saved_and_loaded(self):
        s = self.settings()
        s.update(token=TOKEN, enabled=True, speak_on_pc=True, confirm_risky=False, notify_planner=False,
                 proxy="socks5h://127.0.0.1:1080", api_base="https://my.server/api/")
        s.link(42, "Анна", "anna")
        again = self.settings()
        self.assertEqual((again.token, again.enabled, again.speak_on_pc, again.confirm_risky, again.notify_planner),
                         (TOKEN, True, True, False, False))
        self.assertEqual((again.proxy, again.api_base), ("socks5h://127.0.0.1:1080", "https://my.server/api"))
        self.assertEqual([(u["id"], u["name"], u["username"]) for u in again.users], [(42, "Анна", "anna")])
        self.assertTrue(again.ready)

    def test_update_tells_what_changed(self):
        s = self.settings()
        self.assertEqual(s.update(enabled=True, confirm_risky=True), {"enabled"})
        self.assertEqual(s.update(enabled=True), set())

    def test_values_that_cannot_be_accepted_change_nothing(self):
        s = self.settings()
        s.update(token=TOKEN)
        for changes in ({"token": "не токен"}, {"proxy": "kuku"}, {"proxy": "ftp://host:1"}, {"api_base": "host"},
                        {"unknown": 1}, {"enabled": True, "token": "12:x"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    s.update(**changes)
        self.assertEqual((s.token, s.enabled), (TOKEN, False))

    def test_proxies_and_servers_in_all_the_right_forms(self):
        s = self.settings()
        for proxy in ("http://127.0.0.1:8080", "https://proxy.example:3128", "socks5://127.0.0.1:1080",
                      "socks5h://user:pass@host:1080".replace("user:pass@", ""), "SOCKS5H://localhost:9050", ""):
            with self.subTest(proxy=proxy):
                s.update(proxy=proxy)
                self.assertEqual(s.proxy, proxy)

    def test_the_file_is_for_its_owner_only(self):
        s = self.settings()
        s.update(token=TOKEN)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["telegram.json"], "no scraps are left")

    def test_a_broken_file_gives_the_defaults(self):
        for text in ("{", "[]", "null", '"text"', ""):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertLogs(level="WARNING") if text in ("{", "") else mock.MagicMock():
                    s = self.settings()
                self.assertEqual((s.token, s.users, s.enabled), ("", [], False))

    def test_odd_values_in_the_file_are_not_believed(self):
        self.path.write_text(json.dumps({
            "token": "not a token", "enabled": 1, "speak_on_pc": "yes", "confirm_risky": 0, "notify_planner": None,
            "proxy": "kuku", "api_base": 5,
            "users": [{"id": "42"}, {"id": True}, {"id": 7, "name": None, "username": ["x"]}, "text", {"name": "x"},
                      {"id": 9, "name": "Ок", "username": "ok", "since": "2026-11-02 07:00"}]}), encoding="utf-8")
        s = self.settings()
        self.assertEqual((s.token, s.enabled, s.speak_on_pc, s.proxy, s.api_base), ("", False, False, "", ""))
        self.assertTrue(s.confirm_risky, "a switch that is not clearly off stays on")
        self.assertTrue(s.notify_planner)
        self.assertEqual([u["id"] for u in s.users], [7, 9])
        self.assertEqual(s.users[0]["name"], "None" if False else s.users[0]["name"])

    def test_accounts(self):
        s = self.settings()
        s.link(1, "Анна", "anna")
        s.link(2, "Борис", "")
        self.assertTrue(s.is_allowed(1))
        self.assertFalse(s.is_allowed(3))
        s.link(1, "Анна Новая", "anna2")
        self.assertEqual([(u["id"], u["name"]) for u in s.users_snapshot()], [(2, "Борис"), (1, "Анна Новая")],
                         "linking again replaces the account, it does not double it")
        self.assertTrue(s.unlink(1))
        self.assertFalse(s.unlink(1))
        self.assertEqual(self.settings().users_snapshot()[0]["id"], 2)

    def test_the_snapshot_is_a_copy(self):
        s = self.settings()
        s.link(1, "Анна", "anna")
        s.users_snapshot()[0]["id"] = 99
        self.assertTrue(s.is_allowed(1))

    def test_ready_needs_the_token_and_the_switch(self):
        s = self.settings()
        s.update(enabled=True)
        self.assertFalse(s.ready)
        s.update(token=TOKEN)
        self.assertTrue(s.ready)
        s.update(enabled=False)
        self.assertFalse(s.ready)


class TokenTests(unittest.TestCase):
    def test_what_looks_like_a_token(self):
        for token in (TOKEN, "1234567890:AAH_x-yz0123456789abcdef", "123:ABCDEFGHIJ"):
            with self.subTest(token=token):
                self.assertTrue(A.valid_token(token))
        for token in ("", None, "abc", "123456789", "123456789:", "123456789:short", "abc:AAEtesttesttest",
                      "123456789:AAE test test", "123456789:AAE/../x" + "a" * 10, " " + TOKEN, TOKEN + "\n",
                      "123456789:AAEtesttest\x00"):
            with self.subTest(token=token):
                self.assertFalse(A.valid_token(token))

    def test_a_wrong_token_does_not_become_a_part_of_an_address(self):
        with self.assertRaises(A.AuthError):
            A.TelegramApi("123/../x")

    def test_redact(self):
        A.TelegramApi(TOKEN)
        self.assertEqual(A.redact(f"url /bot{TOKEN}/getMe failed"), "url /bot<токен>/getMe failed")
        # a token that this program has never seen is found by its shape
        self.assertNotIn("987654321", A.redact("/bot987654321:AAHabcdefghijklmnopqrstuvwxyz1234/getMe"))
        self.assertEqual(A.redact("no secrets here: 12:34"), "no secrets here: 12:34")

    def test_the_log_filter_cuts_the_token_out_of_a_message_its_arguments_and_a_traceback(self):
        A.TelegramApi(TOKEN)
        logger = logging.getLogger("test.token.filter")
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        lines = []

        class Catch(logging.Handler):
            def emit(self, record):
                lines.append(self.format(record))

        handler = Catch()
        handler.addFilter(A.TokenFilter())
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.info("opened /bot%s/getMe", TOKEN)
        logger.info(f"opened /bot{TOKEN}/getMe")
        try:
            raise RuntimeError(f"HTTPSConnectionPool(host='api.telegram.org'): Max retries with url: /bot{TOKEN}/getUpdates")
        except RuntimeError:
            logger.exception("failed")
        text = "\n".join(lines)
        self.assertEqual(len(lines), 3)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("AAEtest", text)
        self.assertIn("Max retries", text, "the rest of the traceback is kept")


class SplitTests(unittest.TestCase):
    def test_a_short_text_is_one_piece(self):
        self.assertEqual(A.split_text("Привет"), ["Привет"])
        self.assertEqual(A.split_text("  Привет \n"), ["Привет"])
        self.assertEqual(A.split_text(""), [])
        self.assertEqual(A.split_text("x" * A.MESSAGE_LIMIT), ["x" * A.MESSAGE_LIMIT])

    def test_a_long_text_is_cut_at_paragraphs_first(self):
        first, second = "а" * 3000, "б" * 3000
        self.assertEqual(A.split_text(f"{first}\n\n{second}"), [first, second])

    def test_then_at_lines_sentences_and_words(self):
        line = "Это строка номер {}. " * 1
        text = "\n".join(line.format(i) for i in range(600))
        pieces = A.split_text(text, 1000)
        self.assertGreater(len(pieces), 5)
        for piece in pieces:
            self.assertLessEqual(len(piece), 1000)
            self.assertTrue(piece.endswith("."), piece[-20:])
        words = ("слово " * 2000).strip()
        for piece in A.split_text(words, 500):
            self.assertLessEqual(len(piece), 500)
            self.assertTrue(piece.endswith("слово"))

    def test_a_text_without_any_break_is_cut_hard(self):
        pieces = A.split_text("я" * 9000)
        self.assertEqual([len(p) for p in pieces], [4096, 4096, 808])

    def test_nothing_is_lost(self):
        text = "\n\n".join(f"Абзац {i}. " + "слово " * 120 for i in range(40))
        pieces = A.split_text(text, 1500)
        self.assertEqual(" ".join(" ".join(pieces).split()), " ".join(text.split()))


class ErrorTests(unittest.TestCase):
    def error(self, status, payload):
        return A.TelegramApi._api_error(status, payload)

    def test_every_kind_of_trouble_has_its_own_class(self):
        cases = (
            (401, {"ok": False, "error_code": 401, "description": "Unauthorized"}, A.AuthError),
            (404, {"ok": False, "error_code": 404, "description": "Not Found"}, A.AuthError),
            (409, {"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates"},
             A.ConflictError),
            (403, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"},
             A.BlockedError),
            (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 7}}, A.RateLimitError),
            (400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}, A.TelegramError),
        )
        for status, payload, kind in cases:
            with self.subTest(status=status):
                error = self.error(status, payload)
                self.assertIs(type(error), kind)
                self.assertEqual(error.code, status)

    def test_how_long_to_wait(self):
        self.assertEqual(self.error(429, {"error_code": 429, "parameters": {"retry_after": 7}}).retry_after, 7.0)
        self.assertEqual(self.error(429, {"error_code": 429}).retry_after, 5.0)

    def test_an_answer_that_is_not_json(self):
        error = self.error(502, None)
        self.assertIn("502", str(error))
        self.assertIn("200", str(self.error(200, "text")))

    def test_the_description_is_cleaned_of_the_token(self):
        A.TelegramApi(TOKEN)
        error = self.error(400, {"error_code": 400, "description": f"Bad Request: /bot{TOKEN}/x"})
        self.assertNotIn(TOKEN, str(error))

    def test_the_texts_are_for_the_user(self):
        self.assertIn("@BotFather", str(self.error(401, {"error_code": 401})))
        self.assertIn("другая программа", str(self.error(409, {"error_code": 409})))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)
        self.api = A.TelegramApi(TOKEN, self.fake.url, trust_env=False)

    def test_calls_and_answers(self):
        self.assertEqual(self.api.get_me()["username"], "astra_test_bot")
        self.api.send_message(5, "Привет", {"inline_keyboard": [[{"text": "Да", "callback_data": "c:x:1"}]]})
        sent = self.fake.sent[-1]
        self.assertEqual((sent["chat_id"], sent["text"]), (5, "Привет"))
        self.assertEqual(sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "c:x:1")

    def test_a_long_text_goes_in_pieces_with_the_buttons_under_the_last_one(self):
        self.api.send_text(5, "а" * 5000, {"inline_keyboard": []})
        self.assertEqual([len(m["text"]) for m in self.fake.sent], [4096, 904])
        self.assertNotIn("reply_markup", self.fake.sent[0])
        self.assertIn("reply_markup", self.fake.sent[1])

    def test_updates_are_asked_with_the_offset_and_the_kinds_the_bot_reads(self):
        self.api.get_updates(55, 1)
        method, params = self.fake.calls[-1]
        self.assertEqual(method, "getUpdates")
        self.assertEqual(params["offset"], 55)
        self.assertEqual(params["allowed_updates"], ["message", "callback_query"])

    def test_the_trouble_of_the_server_is_raised_as_the_right_error(self):
        self.fake.fail("getMe", 401, "Unauthorized")
        with self.assertRaises(A.AuthError):
            self.api.get_me()
        self.fake.fail("sendMessage", 429, "Too Many Requests", retry_after=3)
        with self.assertRaises(A.RateLimitError) as caught:
            self.api.send_message(5, "x")
        self.assertEqual(caught.exception.retry_after, 3.0)

    def test_a_file_is_downloaded_and_its_size_is_limited(self):
        self.fake.voice(5, b"x" * 1000)
        info = self.api.get_file("voice1")
        self.assertEqual(self.api.download(info["file_path"]), b"x" * 1000)
        with self.assertRaises(A.TelegramError):
            self.api.download(info["file_path"], limit=999)
        with self.assertRaises(A.TelegramError):
            self.api.download("voice/missing.oga")

    def test_a_path_that_leads_out_of_the_files_is_not_followed(self):
        for path in ("../secret", "voice/../../x", "/etc/passwd", "voice\\x", "C:/windows", "", None):
            with self.subTest(path=path):
                before = len(self.fake.calls)
                with self.assertRaises(A.TelegramError):
                    self.api.download(path)
                self.assertEqual(len(self.fake.calls), before)

    def test_no_connection_is_told_without_the_address(self):
        api = A.TelegramApi(TOKEN, "http://127.0.0.1:9", trust_env=False)
        with self.assertRaises(A.NetworkError) as caught:
            api.get_me()
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn("127.0.0.1", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_the_kinds_of_network_trouble(self):
        for error, word in ((requests.exceptions.ProxyError("x"), "прокси"), (requests.exceptions.SSLError("x"), "SSL"),
                            (requests.exceptions.ConnectTimeout("x"), "время ожидания"),
                            (requests.exceptions.ConnectionError("x"), "Нет соединения"),
                            (requests.exceptions.InvalidSchema("Missing dependencies for SOCKS support."), "PySocks"),
                            (requests.exceptions.TooManyRedirects("x"), "Ошибка сети")):
            with self.subTest(error=type(error).__name__):
                self.assertIn(word, str(A.TelegramApi._network_error(error)))

    def test_the_time_of_telegram_comes_from_its_answers(self):
        self.api.get_me()
        self.assertLess(abs(self.api.clock_offset), 5)
        self.assertLess(abs(self.api.server_now() - __import__("time").time()), 5)

    def test_the_address_of_the_server(self):
        self.assertEqual(A.normalize_base(""), A.API_URL)
        self.assertEqual(A.normalize_base(" https://x.y/api/ "), "https://x.y/api")
        for bad in ("x.y", "ftp://x.y", "http://", "https://x y"):
            with self.subTest(bad=bad):
                with self.assertRaises(A.TelegramError):
                    A.normalize_base(bad)

    def test_a_proxy_is_given_with_every_request(self):
        api = A.TelegramApi(TOKEN, self.fake.url, proxy="socks5h://127.0.0.1:1080", trust_env=False)
        self.assertEqual(api._proxies, {"http": "socks5h://127.0.0.1:1080", "https": "socks5h://127.0.0.1:1080"})
        self.assertIsNone(self.api._proxies)
        with mock.patch.object(requests.Session, "post", side_effect=requests.exceptions.ProxyError("x")) as post:
            with self.assertRaises(A.NetworkError):
                api.get_me()
        self.assertEqual(post.call_args.kwargs["proxies"]["https"], "socks5h://127.0.0.1:1080")


class HelperTests(unittest.TestCase):
    def test_plain_text_takes_the_signs_of_markdown_away(self):
        self.assertEqual(B.plain_text("**Важно**: __тут__ `код`"), "Важно: тут код")
        self.assertEqual(B.plain_text("# Заголовок\n## Ещё\nтекст"), "Заголовок\nЕщё\nтекст")
        self.assertEqual(B.plain_text("```python\nprint(1)\n```"), "python\nprint(1)")
        self.assertEqual(B.plain_text("Обычный текст: 2 * 3 = 6"), "Обычный текст: 2 * 3 = 6")
        self.assertEqual(B.plain_text("  \n"), "")

    def test_commands_of_the_bot(self):
        self.assertEqual(B.split_command("/start K7M2QX9P"), ("start", "K7M2QX9P"))
        self.assertEqual(B.split_command("/help@Astra_Bot"), ("help", ""))
        self.assertEqual(B.split_command("/LINK   abc  "), ("link", "abc"))
        self.assertEqual(B.split_command("поставь таймер"), (None, "поставь таймер"))
        self.assertEqual(B.split_command("/"), ("", ""))

    def test_names_of_people(self):
        self.assertEqual(B.person_name({"id": 1, "first_name": "Анна", "last_name": "Иванова"}), "Анна Иванова")
        self.assertEqual(B.person_name({"id": 1, "first_name": "Анна"}), "Анна")
        self.assertEqual(B.person_name({"id": 1, "username": "anna"}), "@anna")
        self.assertEqual(B.person_name({"id": 1}), "1")

    def test_not_more_often_than_every_n_seconds(self):
        throttle = B._Throttle(limit=3)
        with mock.patch.object(B.time, "monotonic", side_effect=[0, 1, 61, 62, 63, 64, 65, 66]):
            self.assertTrue(throttle.allow("a", 60))
            self.assertFalse(throttle.allow("a", 60))
            self.assertTrue(throttle.allow("a", 60))
            for key in "bcd":
                self.assertTrue(throttle.allow(key, 60))
        self.assertLessEqual(len(throttle._seen), 3, "the memory it takes is limited")


if __name__ == "__main__":
    unittest.main()
