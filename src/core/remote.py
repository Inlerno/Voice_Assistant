"""Phrases that come from outside the microphone: the field of the window and the chats.

normalize_query() brings a typed phrase to the form of a spoken one. RemoteHost is what the assistant gives to the
Telegram bot (see src/integrations/telegram/bot.py): to check a command before it runs, to run it and to recognize a
voice message.
"""
import re
from typing import NamedTuple, Optional

from src.core.risky import LOCAL_ONLY_COMMANDS, RISKY_COMMANDS

_SIGNS = re.compile(r"[^\w\s:]|(?<!\d):|:(?!\d)")


def normalize_query(text):
    """A phrase as a spoken command arrives: lower case, no punctuation (a colon between digits stays: 7:30)."""
    return re.sub(r"\s+", " ", _SIGNS.sub(" ", (text or "").lower())).strip()


class Verdict(NamedTuple):
    """What the bot has to know about a phrase before the command runs."""
    query: str                      # the phrase in the form of a spoken command; empty when there is nothing in it
    effect: Optional[str] = None    # what a dangerous command does to the computer ("выключит компьютер")
    local_only: bool = False        # a command that cannot be given from a chat


# The state of the bot as the light of the status bar shows it
BOT_LIGHTS = {"ok": "ok", "connecting": "busy", "error": "error"}


def show_bot_in_window(bot, window):
    """The state of the bot goes to the status bar and to the page of the window, what the owner has to see at once
    (a new account, a stranger who wrote to the bot) goes to the dialog."""

    def on_status(state, _text):
        window.set_service("tg", BOT_LIGHTS.get(state, "off"))
        window.telegram_changed()

    bot.on_status = on_status
    bot.on_change = window.telegram_changed
    bot.on_notice = window.notice
    window.set_service("tg", BOT_LIGHTS.get(bot.state, "off"))


class RemoteHost:
    def __init__(self, assistant):
        self.assistant = assistant

    def check(self, text):
        query = normalize_query(text)
        if not query:
            return Verdict("")
        command = self.assistant.match_command(query)
        make = command.get("make") if command else None
        return Verdict(query, RISKY_COMMANDS.get(make), make in LOCAL_ONLY_COMMANDS)

    def run(self, text, io, note=None):
        """Starts the command on the assistant thread; returns a Future, or None when the text is empty."""
        return self.assistant.handle_remote(text, io, note)

    def transcribe(self, pcm, rate):
        """(text, {"decode_ms", "audio_s"}) for a recording of 16-bit mono PCM."""
        return self.assistant.speech_recognizer.transcribe(pcm, rate)
