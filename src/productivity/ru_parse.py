"""Understanding of Russian phrases about time: "через пять минут", "в семь тридцать", "завтра в девять утра".

The recognizer writes numbers as words, a typed command may contain digits: both are understood.
Every finder returns the value and the range of tokens it used, so the rest of the phrase (the text of a
reminder, for example) can be cut out.
"""
import re
from datetime import datetime, timedelta

_UNITS = {
    "ноль": 0, "нуль": 0, "один": 1, "одна": 1, "одну": 1, "одного": 1, "одной": 1, "два": 2, "две": 2, "двух": 2,
    "три": 3, "трех": 3, "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7,
    "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
}
_TEENS = {
    "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12, "двенадцати": 12,
    "тринадцать": 13, "тринадцати": 13, "четырнадцать": 14, "четырнадцати": 14, "пятнадцать": 15,
    "пятнадцати": 15, "шестнадцать": 16, "шестнадцати": 16, "семнадцать": 17, "семнадцати": 17,
    "восемнадцать": 18, "восемнадцати": 18, "девятнадцать": 19, "девятнадцати": 19,
}
_TENS = {
    "двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30, "сорок": 40, "сорока": 40,
    "пятьдесят": 50, "пятидесяти": 50, "шестьдесят": 60, "шестидесяти": 60, "семьдесят": 70, "семидесяти": 70,
    "восемьдесят": 80, "восьмидесяти": 80, "девяносто": 90, "девяноста": 90,
}
_HUNDREDS = {
    "сто": 100, "ста": 100, "двести": 200, "двухсот": 200, "триста": 300, "трехсот": 300, "четыреста": 400,
    "четырехсот": 400, "пятьсот": 500, "пятисот": 500, "шестьсот": 600, "шестисот": 600, "семьсот": 700,
    "семисот": 700, "восемьсот": 800, "восьмисот": 800, "девятьсот": 900, "девятисот": 900,
}
_THOUSANDS = {"тысяча", "тысячу", "тысячи", "тысяч"}
# "в половине восьмого": the hour in the genitive ordinal form
_ORDINALS = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5, "шестого": 6, "седьмого": 7,
    "восьмого": 8, "девятого": 9, "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12,
}

SECOND_WORDS = {"секунда", "секунду", "секунды", "секунд", "сек"}
MINUTE_WORDS = {"минута", "минуту", "минуты", "минут", "мин"}
HOUR_WORDS = {"час", "часа", "часов", "ч"}
DAY_WORDS = {"день", "дня", "дней", "сутки", "суток"}
WEEK_WORDS = {"неделя", "неделю", "недели", "недель"}
UNIT_SECONDS = {}
for _words, _seconds in ((SECOND_WORDS, 1), (MINUTE_WORDS, 60), (HOUR_WORDS, 3600), (DAY_WORDS, 86400),
                         (WEEK_WORDS, 604800)):
    for _word in _words:
        UNIT_SECONDS[_word] = _seconds

WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среду": 2, "среда": 2, "четверг": 3, "пятницу": 4, "пятница": 4,
    "субботу": 5, "суббота": 5, "воскресенье": 6,
}
WEEKDAY_NAMES = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]

_TOKEN = re.compile(r"\d+(?::\d{2})?|[а-яa-z]+")


def tokenize(text):
    """Lower case words and numbers; ё is written as е, punctuation is dropped, 7:30 stays one token."""
    return _TOKEN.findall((text or "").lower().replace("ё", "е"))


def _digits(token):
    return token.isdigit()


def read_number(tokens, i):
    """A number written in words or digits that starts at tokens[i]: returns (value, next index) or None.

    "двадцать пять" is one number, "семь тридцать" are two: a word may continue the number only if it is
    smaller in rank than the previous one (hundreds > tens > units)."""
    if i >= len(tokens):
        return None
    if _digits(tokens[i]):
        return int(tokens[i]), i + 1
    total, current, last_rank, j, started = 0, 0, 4, i, False
    while j < len(tokens):
        word = tokens[j]
        if word in _HUNDREDS:
            value, rank = _HUNDREDS[word], 3
        elif word in _TENS:
            value, rank = _TENS[word], 2
        elif word in _TEENS:
            value, rank = _TEENS[word], 1
        elif word in _UNITS:
            value, rank = _UNITS[word], 1
        elif word in _THOUSANDS and started:
            total += (current or 1) * 1000
            current, last_rank = 0, 4
            j += 1
            continue
        else:
            break
        if started and rank >= last_rank:
            break
        # "ноль" never continues a number: "ноль пять" is handled by the clock finder
        if started and word in ("ноль", "нуль"):
            break
        current += value
        last_rank, started = rank, True
        j += 1
    if not started:
        return None
    return total + current, j


def _after_number(tokens, i, words):
    return i < len(tokens) and tokens[i] in words


def find_duration(tokens):
    """The first length of time: "пять минут", "полчаса", "час тридцать", "два с половиной часа".

    Returns (seconds, start, end) or None."""
    n = len(tokens)
    for start in range(n):
        seconds, j, found = 0, start, False
        while j < n:
            word = tokens[j]
            if word in ("полчаса", "полчас"):
                seconds += 1800
                j, found = j + 1, True
                continue
            if word in ("полторы", "полтора") and j + 1 < n and tokens[j + 1] in UNIT_SECONDS:
                seconds += int(1.5 * UNIT_SECONDS[tokens[j + 1]])
                j, found = j + 2, True
                continue
            if word == "четверть" and j + 1 < n and tokens[j + 1] in HOUR_WORDS:
                seconds += 900
                j, found = j + 2, True
                continue
            number = read_number(tokens, j)
            if number is not None:
                value, after = number
                half = tokens[after:after + 2] == ["с", "половиной"]
                if half:
                    after += 2
                if _after_number(tokens, after, UNIT_SECONDS):
                    unit = UNIT_SECONDS[tokens[after]]
                    seconds += value * unit + (unit // 2 if half else 0)
                    j, found = after + 1, True
                    continue
                break
            if word in UNIT_SECONDS and word not in ("ч", "мин", "сек") and not found:
                # "на час", "через минуту": a unit without a number means one
                seconds += UNIT_SECONDS[word]
                j, found = j + 1, True
                if word in HOUR_WORDS:
                    # "час тридцать": the number after the hour is minutes
                    rest = read_number(tokens, j)
                    if rest is not None and 0 < rest[0] < 60 and not _after_number(tokens, rest[1], UNIT_SECONDS):
                        seconds += rest[0] * 60
                        j = rest[1]
                        if j < n and tokens[j] in MINUTE_WORDS:
                            j += 1
                continue
            break
        if found and seconds > 0:
            return seconds, start, j
    return None


_PERIODS = {"утра": "am", "утром": "am", "дня": "day", "днем": "day", "вечера": "pm", "вечером": "pm",
            "ночи": "night", "ночью": "night"}
_CLOCK_PREPOSITIONS = {"в", "на", "к", "во", "до"}


def _apply_period(hour, period):
    if period == "pm" and hour < 12:
        return hour + 12
    if period == "day":
        return hour + 12 if 1 <= hour < 6 else hour
    if period == "night":
        return 0 if hour == 12 else hour
    if period == "am" and hour == 12:
        return 0
    return hour


def find_clock(tokens):
    """A time of day: "в семь тридцать", "на 7:30", "в восемь вечера", "в половине девятого", "без пятнадцати семь".

    Returns (hour, minute, period, start, end) or None; the period is "am", "pm", "day", "night" or None when
    the phrase does not say it (then the hour may be meant as morning or evening)."""
    n = len(tokens)
    for i in range(n):
        word = tokens[i]
        start = i
        # the preposition is cut out together with the time
        if word in _CLOCK_PREPOSITIONS and i + 1 < n:
            i += 1
            word = tokens[i]
        else:
            start = i
        if word in ("полдень", "полдня"):
            return 12, 0, "day", start, i + 1
        if word == "полночь":
            return 0, 0, "night", start, i + 1
        # 7:30
        if re.fullmatch(r"\d{1,2}:\d{2}", word):
            hour, minute = (int(x) for x in word.split(":"))
            if hour <= 24 and minute < 60:
                j, period = i + 1, None
                if j < n and tokens[j] in _PERIODS:
                    period, j = _PERIODS[tokens[j]], j + 1
                return hour % 24, minute, period, start, j
            continue
        # "в половине восьмого", "в четверть девятого"
        if word in ("половине", "половина") and i + 1 < n and tokens[i + 1] in _ORDINALS:
            hour = _ORDINALS[tokens[i + 1]] - 1
            return hour % 24, 30, None, start, i + 2
        if word == "четверть" and i + 1 < n and tokens[i + 1] in _ORDINALS:
            hour = _ORDINALS[tokens[i + 1]] - 1
            return hour % 24, 15, None, start, i + 2
        # "без пятнадцати восемь"
        if word == "без":
            minutes = read_number(tokens, i + 1)
            if minutes is not None:
                value, after = minutes
                if after < n and tokens[after] in MINUTE_WORDS:
                    after += 1
                hour = read_number(tokens, after)
                if hour is not None and 0 < value < 60 and 1 <= hour[0] <= 24:
                    return (hour[0] - 1) % 24, 60 - value, None, start, hour[1]
            continue
        number = read_number(tokens, i)
        if number is None:
            continue
        hour, after = number
        # a clock needs a preposition or the word "часов"; a bare number is more likely a count
        has_preposition = start != i
        j = after
        said_hours = j < n and tokens[j] in ("час", "часа", "часов")
        if said_hours:
            j += 1
        if not (has_preposition or said_hours):
            continue
        if hour > 24:
            continue
        # a duration ("через пять минут") is not a clock
        if j == after and j < n and tokens[j] in UNIT_SECONDS:
            continue
        minute = 0
        if j < n:
            minutes_number = None
            if tokens[j] in ("ноль", "нуль") and j + 1 < n:
                parsed = read_number(tokens, j + 1)
                if parsed is not None and parsed[0] < 10:
                    minutes_number = (parsed[0], parsed[1])
            else:
                parsed = read_number(tokens, j)
                if parsed is not None and parsed[0] < 60 and not (j == after and tokens[j] in _PERIODS):
                    minutes_number = parsed
            if minutes_number is not None:
                minute, j = minutes_number
                if j < n and tokens[j] in MINUTE_WORDS:
                    j += 1
        period = None
        if j < n and tokens[j] in _PERIODS:
            period, j = _PERIODS[tokens[j]], j + 1
        return hour % 24, minute, period, start, j
    return None


def next_clock_time(now, hour, minute, period=None, prefer_morning=False):
    """The nearest moment in the future that shows hour:minute.

    Without a period an hour up to 12 can be morning or evening: an alarm means morning, a reminder the
    nearest of the two."""
    if period is not None:
        hours = [_apply_period(hour, period)]
    elif hour <= 12 and not prefer_morning:
        hours = [hour % 12, hour % 12 + 12] if hour != 0 else [0, 12]
    else:
        hours = [hour % 24]
    candidates = []
    for h in hours:
        moment = now.replace(hour=h % 24, minute=minute, second=0, microsecond=0)
        if moment <= now:
            moment += timedelta(days=1)
        candidates.append(moment)
    return min(candidates)


def find_date(tokens, now):
    """A day: "завтра", "послезавтра", "сегодня", "в понедельник", "через три дня" (a duration of days).

    Returns (date, start, end) or None."""
    n = len(tokens)
    for i, word in enumerate(tokens):
        if word == "сегодня":
            return now.date(), i, i + 1
        if word == "завтра":
            return (now + timedelta(days=1)).date(), i, i + 1
        if word == "послезавтра":
            return (now + timedelta(days=2)).date(), i, i + 1
        if word in WEEKDAYS:
            start = i - 1 if i > 0 and tokens[i - 1] in ("в", "во", "на", "к") else i
            ahead = (WEEKDAYS[word] - now.weekday()) % 7 or 7
            return (now + timedelta(days=ahead)).date(), start, i + 1
    return None


def find_repeat(tokens):
    """"каждый день", "ежедневно", "по будням": returns ("daily"|"weekdays", start, end) or None."""
    n = len(tokens)
    for i, word in enumerate(tokens):
        if word == "ежедневно":
            return "daily", i, i + 1
        if word == "каждый" and i + 1 < n and tokens[i + 1] == "день":
            return "daily", i, i + 2
        if word == "каждое" and i + 1 < n and tokens[i + 1] in ("утро", "утром"):
            return "daily", i, i + 2
        if word == "по" and i + 1 < n and tokens[i + 1] in ("будням", "будни"):
            return "weekdays", i, i + 2
        if word == "по" and i + 2 < n and tokens[i + 1] == "рабочим" and tokens[i + 2] == "дням":
            return "weekdays", i, i + 3
    return None


def cut(tokens, *spans):
    """The tokens without the given (start, end) ranges."""
    skip = set()
    for start, end in spans:
        skip.update(range(start, end))
    return [word for index, word in enumerate(tokens) if index not in skip]


# ---------- Words for the answers ----------

def plural(number, forms):
    """plural(5, ("минута", "минуты", "минут")) -> "минут"."""
    number = abs(int(number))
    if 11 <= number % 100 <= 14:
        return forms[2]
    if number % 10 == 1:
        return forms[0]
    if 2 <= number % 10 <= 4:
        return forms[1]
    return forms[2]


def describe_duration(seconds, accusative=False):
    """3900 -> "1 час 5 минут"; with accusative=True "на 1 минуту 5 секунд" has the right endings."""
    seconds = max(0, int(round(seconds)))
    minute = ("минуту", "минуты", "минут") if accusative else ("минута", "минуты", "минут")
    second = ("секунду", "секунды", "секунд") if accusative else ("секунда", "секунды", "секунд")
    if seconds < 60:
        return f"{seconds} {plural(seconds, second)}"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} {plural(days, ('день', 'дня', 'дней'))}")
    if hours:
        parts.append(f"{hours} {plural(hours, ('час', 'часа', 'часов'))}")
    if minutes:
        parts.append(f"{minutes} {plural(minutes, minute)}")
    if secs and not days and not hours:
        parts.append(f"{secs} {plural(secs, second)}")
    return " ".join(parts)


def describe_moment(moment, now=None):
    """"сегодня в 18:30", "завтра в 07:00", "в понедельник в 09:00", "27.10 в 09:00"."""
    now = now or datetime.now()
    days = (moment.date() - now.date()).days
    clock = moment.strftime("%H:%M")
    if days == 0:
        return f"сегодня в {clock}"
    if days == 1:
        return f"завтра в {clock}"
    if days == 2:
        return f"послезавтра в {clock}"
    if 2 < days < 7:
        return f"в {WEEKDAY_NAMES[moment.weekday()]} в {clock}"
    return f"{moment.strftime('%d.%m')} в {clock}"
