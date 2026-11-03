"""The code that links a Telegram account to the assistant.

The program shows a code, the owner sends it to the bot, and from then on the bot obeys this account. The code
is made of letters and digits that cannot be mixed up (no 0/O, 1/I/L), lives ten minutes and works once. A person
who guesses wrong several times is shut out for a while, and a flood of wrong guesses from everybody destroys the
code: the owner just asks for a new one.
"""
import hmac
import re
import secrets
import threading
import time
from collections import deque

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
LIFETIME = 10 * 60
# Wrong codes of one person: this many in this time shut them out until the time is over
PERSON_FAILS, PERSON_WINDOW = 5, 15 * 60
# Wrong codes of everybody together: this many destroy the code
TOTAL_FAILS, TOTAL_WINDOW = 20, 10 * 60


def normalize(text):
    """What was typed, as the code is stored: capital letters and digits only ("k7m2-qx9p" -> "K7M2QX9P")."""
    return re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()


def display(code):
    """The code in two halves, as it is easier to read and to type: K7M2-QX9P."""
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


class Pairing:
    def __init__(self, clock=time.monotonic, lifetime=LIFETIME):
        self._clock = clock
        self.lifetime = lifetime
        # The window asks for a code, the bot checks the codes of people: two threads
        self._lock = threading.RLock()
        self._code = None
        self._expires = 0.0
        self._fails = {}
        self._all_fails = deque()

    def new_code(self):
        """A new code; the one that was valid before stops working."""
        with self._lock:
            self._code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
            self._expires = self._clock() + self.lifetime
            return self._code

    def cancel(self):
        with self._lock:
            self._code = None

    def active(self):
        """(code, seconds left) of the valid code, or None."""
        with self._lock:
            if self._code is None:
                return None
            left = self._expires - self._clock()
            if left <= 0:
                self._code = None
                return None
            return self._code, left

    def _recent(self, stamps, window):
        now = self._clock()
        while stamps and now - stamps[0] > window:
            stamps.popleft()
        return stamps

    def locked(self, person):
        """Whether this person has tried too many wrong codes lately."""
        with self._lock:
            stamps = self._fails.get(person)
            return stamps is not None and len(self._recent(stamps, PERSON_WINDOW)) >= PERSON_FAILS

    def check(self, person, given):
        """True when `given` is the valid code; it is used up then. Wrong guesses are counted against `person`."""
        with self._lock:
            if self.locked(person):
                return False
            active = self.active()
            # compare_digest takes time that does not depend on how many characters match
            if active is not None and hmac.compare_digest(active[0].encode(), normalize(given).encode()):
                self._code = None
                self._fails.pop(person, None)
                return True
            if len(self._fails) > 200:
                # strangers must not make the list grow without end: those who are forgiven by now are dropped
                for key in [key for key, stamps in self._fails.items() if not self._recent(stamps, PERSON_WINDOW)]:
                    del self._fails[key]
            self._fails.setdefault(person, deque()).append(self._clock())
            self._all_fails.append(self._clock())
            if len(self._recent(self._all_fails, TOTAL_WINDOW)) >= TOTAL_FAILS:
                self._code = None
                self._all_fails.clear()
            return False
