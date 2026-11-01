"""The quick search (Ctrl+K): what a typed text finds, in which order."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.interface.command_palette import Entry, MAX_ROWS, score, search  # noqa: E402


def entry(title, keywords=""):
    return Entry(title, "Страница", "home", lambda: None, keywords)


class ScoreTests(unittest.TestCase):
    def test_an_empty_query_matches_everything_equally(self):
        self.assertEqual(score("", entry("Дела")), 1)
        self.assertEqual(score("   ", entry("Дела")), 1)

    def test_the_start_of_the_title_beats_a_part_of_it_and_a_keyword(self):
        page = entry("Таймеры и дела", "будильник")
        self.assertGreater(score("тайм", page), score("дела", page))
        self.assertGreater(score("дела", page), score("будильник", page))

    def test_the_case_does_not_matter(self):
        self.assertEqual(score("ДЕЛА", entry("Дела")), score("дела", entry("Дела")))

    def test_every_word_has_to_be_found(self):
        page = entry("Озвучка", "голос скорость громкость")
        self.assertGreater(score("голос скорость", page), 0)
        self.assertEqual(score("голос принтер", page), 0)

    def test_a_typo_still_finds_the_entry_but_worse_than_the_exact_word(self):
        page = entry("Будильник")
        typo, exact = score("будилник", page), score("будильник", page)
        self.assertGreater(typo, 0)
        self.assertGreater(exact, typo)

    def test_a_different_word_finds_nothing(self):
        self.assertEqual(score("принтер", entry("Дела", "таймер задачи")), 0)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            entry("Главная", "чат диалог"), entry("Дела", "таймер будильник задачи"), entry("Приложения"),
            entry("Команды", "фразы"), entry("Голос", "микрофон"), entry("Озвучка", "tts"), entry("Журнал"),
            entry("Настройки", "оформление цвет тема"),
        ]

    def titles(self, query):
        return [found.title for found in search(self.entries, query)]

    def test_an_empty_query_gives_the_entries_in_their_order(self):
        self.assertEqual(self.titles(""), [item.title for item in self.entries])

    def test_the_best_match_is_the_first(self):
        self.assertEqual(self.titles("дел")[0], "Дела")
        self.assertEqual(self.titles("цвет")[0], "Настройки")

    def test_a_keyword_finds_the_page(self):
        self.assertEqual(self.titles("микрофон"), ["Голос"])

    def test_nothing_is_found_for_a_stranger(self):
        self.assertEqual(self.titles("qwertyuiop"), [])

    def test_the_list_is_limited(self):
        many = [entry(f"Страница {number}") for number in range(40)]
        self.assertEqual(len(search(many, "")), MAX_ROWS)
        self.assertEqual(len(search(many, "страница")), MAX_ROWS)
        self.assertEqual(len(search(many, "страница", limit=3)), 3)

    def test_equal_matches_keep_their_order(self):
        twins = [entry("Один"), entry("Один и два"), entry("Один и три")]
        self.assertEqual([item.title for item in search(twins, "один")], ["Один", "Один и два", "Один и три"])


if __name__ == "__main__":
    unittest.main()
