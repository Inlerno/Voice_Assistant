"""Spoken commands about the voice of the assistant: change it, speak faster, quieter..."""
import re

RATE_STEP = 0.15
VOLUME_STEP = 0.2

_CHANGE = re.compile(r"\b(смени|сменить|поменяй|поменять|измени|изменить|другой|следующий|новый)\s+(свой\s+|твой\s+)?голос")
_NAMED = re.compile(r"\b(?:говори|говорить|включи|поставь|используй|выбери)?\s*(?:голосом|голос)\s+(.+)$")
_WHICH = re.compile(r"(какой\s+(у\s+тебя\s+)?голос|каким\s+голосом\s+(ты\s+)?говоришь)")
_FASTER = re.compile(r"\b(говори|читай)\s+(быстрее|побыстрее|быстро)\b")
_SLOWER = re.compile(r"\b(говори|читай)\s+(медленнее|помедленнее|медленно)\b")
_LOUDER = re.compile(r"\b(говори|читай)\s+(громче|погромче)\b")
_QUIETER = re.compile(r"\b(говори|читай)\s+(тише|потише)\b")
# words that follow the name of the voice but are not a part of it
_FILLER = {"пожалуйста", "теперь", "дальше"}


def handle(tts, query):
    """Runs the command and returns what to say (with the new voice), or None when it is not such a command."""
    query = (query or "").lower().strip()
    if not query:
        return None
    if _WHICH.search(query):
        voice = tts.current_voice()
        return f"Сейчас я говорю голосом {voice.name}." if voice else "Сейчас у меня нет голоса."
    if _CHANGE.search(query):
        voice = tts.next_voice()
        return f"Теперь я говорю голосом {voice.name}." if voice else "Других голосов нет."
    if _FASTER.search(query):
        tts.set_rate(tts.settings.rate + RATE_STEP)
        return "Говорю быстрее."
    if _SLOWER.search(query):
        tts.set_rate(tts.settings.rate - RATE_STEP)
        return "Говорю медленнее."
    if _LOUDER.search(query):
        tts.set_volume(tts.settings.volume + VOLUME_STEP)
        return "Говорю громче."
    if _QUIETER.search(query):
        tts.set_volume(tts.settings.volume - VOLUME_STEP)
        return "Говорю тише."
    named = _NAMED.search(query)
    if named:
        words = [w for w in named.group(1).split() if w not in _FILLER]
        spoken = " ".join(words)
        if not spoken:
            return None
        voice = tts.find_voice(spoken)
        if voice is None:
            return None
        tts.set_voice(voice.id)
        return f"Теперь я говорю голосом {voice.name}."
    return None
