"""The Telegram bot of the assistant: orders from a chat (text or voice), answers in the same chat.

How it works
  * The poller thread asks Telegram for new messages (long polling) and sorts them out at once: linking of an
    account, the commands of the bot itself (/help, /unlink, /cancel), taps on buttons. A message of a linked person
    becomes a job in the queue, or - when a command waits for an answer to its question - that answer.
  * The worker thread takes the jobs one by one: it turns a voice message into text (the file of Telegram is
    decoded and recognized by the same Vosk the microphone uses), asks the host (the assistant) to run the command
    on its own thread and sends what the assistant said back to the chat.
  * Only accounts that were linked with a one-time code are obeyed; the others get one polite hint a minute. Only
    private chats count. Old messages (the computer was off or offline) are not obeyed. A command that turns the
    computer off and the like waits for a tap on a button.

The host (the assistant) gives three things: check(text) tells whether the command is dangerous, run(text, io, note)
runs it (returns a Future) and transcribe(pcm, rate) recognizes speech. See Assistant.handle_remote.
"""
import logging
import queue
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from src.audio.audio_decode import MAX_SECONDS, AudioDecodeError, decode_audio
from src.integrations.telegram.api import (
    FILE_LIMIT, AuthError, BlockedError, ConflictError, NetworkError, RateLimitError, TelegramApi, TelegramError,
    protect_log, redact, split_text,
)
from src.integrations.telegram.pairing import Pairing, display

logger = logging.getLogger(__name__)

# How long a request for new messages is held by Telegram, in seconds
POLL_TIMEOUT = 20
# A message that is older than this (in Telegram's time) is not obeyed: the order may have lost its meaning
STALE_SECONDS = 120
# How long a command waits for the answer to its question, in seconds
ANSWER_TIMEOUT = 90
# The longest a command may take before the chat is told it is too long, in seconds
JOB_TIMEOUT = 15 * 60
# How long the buttons "Yes / Cancel" stay valid, in seconds
CONFIRM_SECONDS = 60
# Messages waiting for their turn from one chat
MAX_QUEUED = 5
# At most this many messages of one person in this many seconds
RATE_MESSAGES, RATE_SECONDS = 20, 60
# A text that is longer than this goes to the chat at once, not at the end of the command
FLUSH_SIZE = 3500
# The pause before the next try after failures in a row, in seconds
BACKOFF = (2, 4, 8, 16, 30, 60)
CONFLICT_WAIT = 15
# The "typing..." sign is shown this often while a command works, in seconds
TYPING_EVERY = 4
# One stranger gets a hint not more often than this, in seconds
STRANGER_EVERY = 60

COMMANDS = (
    ("help", "Что я умею"),
    ("cancel", "Отменить вопрос, на который я жду ответ"),
    ("unlink", "Отвязать этот аккаунт"),
)

HELP_TEXT = (
    "Я Астра, ассистент вашего компьютера. Пишите команды текстом или присылайте голосовые сообщения: "
    "я выполню их на компьютере и отвечу здесь.\n\n"
    "Например:\n"
    "• поставь таймер на десять минут\n"
    "• разбуди меня в семь тридцать\n"
    "• напомни через час позвонить маме\n"
    "• добавь задачу купить молоко\n"
    "• открой калькулятор\n"
    "• сколько времени\n"
    "• выключи компьютер (сначала спрошу, кнопкой)\n"
    "Всё остальное я передам языковой модели.\n\n"
    "/cancel — отменить вопрос, на который я жду ответ\n"
    "/unlink — отвязать этот аккаунт"
)
STRANGER_TEXT = (
    "Это личный бот ассистента Астра, он слушается только привязанного аккаунта.\n"
    "Чтобы привязать свой: в приложении Астра откройте страницу «Telegram», нажмите «Привязать аккаунт» "
    "и отправьте мне полученный код: /link КОД"
)
ASK_HINT = "↩ Жду ответ: текстом или голосовым. /cancel — отменить."

_MARKDOWN = (
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"),
    (re.compile(r"__(.+?)__", re.S), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
    (re.compile(r"`{1,3}"), ""),
)
_CANCELLED = object()


def plain_text(text):
    """The text for a chat: the signs of Markdown that a language model likes to add are taken away
    (a chat shows them as they are)."""
    text = str(text)
    for pattern, replacement in _MARKDOWN:
        text = pattern.sub(replacement, text)
    return text.strip()


def person_name(user):
    """How a person is called in the program: "Анна Иванова", or @login, or the number."""
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part).strip()
    if name:
        return name
    return f"@{user['username']}" if user.get("username") else str(user.get("id"))


def split_command(text):
    """("start", "K7M2QX9P") for "/start K7M2QX9P", ("help", "") for "/help@MyBot"; (None, text) for a plain text."""
    if not text.startswith("/"):
        return None, text
    head, _, argument = text.partition(" ")
    return head[1:].split("@")[0].lower(), argument.strip()


@dataclass
class Incoming:
    """A message of a linked person, ready to be worked on."""
    chat_id: int
    user_id: int
    who: str
    text: str = ""
    voice: dict = None


@dataclass
class Job:
    incoming: Incoming
    text: str = None        # the text to run, when it is known already (a command that was confirmed)
    note: str = None
    confirmed: bool = False


@dataclass
class Pending:
    """A dangerous command that waits for a tap on a button."""
    chat_id: int
    user_id: int
    who: str
    text: str
    note: str
    prompt: str
    created: float = field(default_factory=time.monotonic)


@dataclass
class _Run:
    """What belongs to one start of the bot: a restart makes a new one, and the old threads finish by themselves."""
    api: TelegramApi
    stop: threading.Event = field(default_factory=threading.Event)
    jobs: queue.Queue = field(default_factory=queue.Queue)
    poller: threading.Thread = None
    worker: threading.Thread = None
    after: threading.Thread = None      # the poller of the previous start: two readers of one bot would quarrel


class _Throttle:
    """"Not more often than every N seconds" for any number of keys; the memory it takes is limited."""

    def __init__(self, limit=500):
        self._seen = OrderedDict()
        self._limit = limit

    def allow(self, key, every):
        now = time.monotonic()
        last = self._seen.get(key)
        if last is not None and now - last < every:
            return False
        self._seen[key] = now
        self._seen.move_to_end(key)
        while len(self._seen) > self._limit:
            self._seen.popitem(last=False)
        return True


class _Session:
    """What is going on in one chat: how many messages wait, and whether a command waits for an answer."""

    def __init__(self):
        self.cond = threading.Condition()
        self.queued = 0
        self.waiting = False
        self.answer = None

    def expect(self):
        with self.cond:
            self.waiting, self.answer = True, None

    def deliver(self, item):
        """Gives the message to the command that asks. False when nothing asks (or the answer is here already)."""
        with self.cond:
            if not self.waiting or self.answer is not None:
                return False
            self.answer = item
            self.cond.notify_all()
            return True

    def take(self, timeout, stopped):
        deadline = time.monotonic() + timeout
        with self.cond:
            while self.answer is None and not stopped():
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(min(left, 0.5))
            answer, self.answer = self.answer, None
            return answer

    def done(self):
        with self.cond:
            self.waiting, self.answer = False, None


class Reply:
    """What the assistant says while one command works. The phrases are collected and go to the chat as one
    message (a command that says ten sentences would otherwise turn the chat into a column of lines)."""

    def __init__(self, bot, run, chat_id):
        self.bot, self.run, self.chat_id = bot, run, chat_id
        self._parts = []
        self._size = 0
        self._lock = threading.RLock()
        self.closed = False

    def add(self, text):
        text = plain_text(text)
        if not text:
            return
        with self._lock:
            if self.closed:
                logger.info("Telegram: ответ пришёл после конца команды и не отправлен: %s", text[:80])
                return
            self._parts.append(text)
            self._size += len(text)
            full = self._size >= FLUSH_SIZE
        if full:
            self.flush()

    def flush(self, footer=None, markup=None, close=False):
        with self._lock:
            parts, self._parts, self._size = self._parts, [], 0
            if footer:
                parts.append(footer)
            if close:
                self.closed = True
            if parts:
                self.bot._send(self.run, self.chat_id, "\n".join(parts), markup)


class RemoteIO:
    """What a command that works for a chat can do: say something there and ask the person a question."""

    def __init__(self, bot, run, incoming, reply):
        self.bot, self.run, self.incoming, self.reply = bot, run, incoming, reply
        self.label = f"Telegram · {incoming.who}"
        self.said = 0

    @property
    def speak_aloud(self):
        """Whether the answers are also said on the computer."""
        return self.bot.settings.speak_on_pc

    def say(self, text):
        self.said += 1
        self.reply.add(text)

    def ask(self):
        """The next message of the person (the phrase is its text, or what was said in a voice message).
        None when it did not come in time, was cancelled or could not be understood."""
        session = self.bot._session(self.incoming.chat_id)
        session.expect()
        try:
            self.reply.flush(footer=ASK_HINT)
            answer = session.take(ANSWER_TIMEOUT, self.run.stop.is_set)
        finally:
            session.done()
        if answer is None or answer is _CANCELLED:
            return None
        text, _ = self.bot._text_of(self.run, answer, self.reply)
        return text


class TelegramBot:
    def __init__(self, settings, host, on_status=None, on_change=None, on_notice=None, trust_env=True):
        self.settings = settings
        self.host = host
        # on_status(state, text): "off", "connecting", "ok" or "error" and a line for the window
        self.on_status = on_status
        # on_change(): the list of linked accounts or the code has changed
        self.on_change = on_change
        # on_notice(text): something the owner should see at once (a new account, a stranger)
        self.on_notice = on_notice
        self.trust_env = trust_env
        self.pairing = Pairing()
        self.username = ""
        self.state, self.status_text = "off", "Выключен"
        self._lock = threading.RLock()
        self._run = None
        self._sessions = {}
        self._pending = {}
        self._rate = {}
        self._strangers = _Throttle()
        self._notices = _Throttle()

    # ---------- Start and stop ----------

    def start(self):
        """Starts reading the chat (the old reading, if there is one, is stopped first).
        Returns False when the settings do not allow it; the reason is in the status."""
        with self._lock:
            previous = self._run
            if previous is not None:
                previous.stop.set()
                previous.jobs.put(None)
                self._run = None
            if not self.settings.token:
                self._set_state("off", "Нужен токен бота")
                return False
            if not self.settings.enabled:
                self._set_state("off", "Выключен")
                return False
            try:
                api = TelegramApi(self.settings.token, self.settings.api_base, self.settings.proxy,
                                  trust_env=self.trust_env)
            except TelegramError as error:
                self._set_state("error", str(error))
                return False
            protect_log()
            run = _Run(api, after=previous.poller if previous is not None else None)
            run.poller = threading.Thread(target=self._poll, args=(run,), daemon=True, name="telegram-poll")
            run.worker = threading.Thread(target=self._work, args=(run,), daemon=True, name="telegram-work")
            self._run = run
            self._set_state("connecting", "Подключаюсь к Telegram…")
            run.poller.start()
            run.worker.start()
            return True

    def stop(self):
        """Stops the bot; does not wait (a request for messages may be held by Telegram for some seconds)."""
        with self._lock:
            run, self._run = self._run, None
            if run is not None:
                run.stop.set()
                run.jobs.put(None)
            self._pending.clear()
            self._set_state("off", "Выключен")

    def restart(self):
        """Applies new settings: the token, the proxy, the switch."""
        return self.start()

    @property
    def running(self):
        return self._run is not None

    def _set_state(self, state, text):
        with self._lock:
            if (state, text) == (self.state, self.status_text):
                return
            self.state, self.status_text = state, text
        self._call(self.on_status, state, text)

    def _status(self, run, state, text):
        # A run that has been stopped must not speak for the bot any more
        if run is self._run:
            self._set_state(state, text)

    @staticmethod
    def _call(callback, *args):
        if callback is not None:
            try:
                callback(*args)
            except Exception:
                logger.exception("Telegram: ошибка в обработчике интерфейса")

    def _changed(self):
        self._call(self.on_change)

    def _notice(self, text):
        self._call(self.on_notice, text)

    # ---------- For the window ----------

    def verify_token(self, token):
        """Asks Telegram who this bot is: its data, or TelegramError. It waits for the network, so a window calls
        it from a thread of its own."""
        api = TelegramApi(token, self.settings.api_base, self.settings.proxy, trust_env=self.trust_env)
        return api.get_me()

    def new_pairing(self):
        """A new code for linking an account: (code as it is shown, the link that opens the bot with the code in it
        or "", seconds it is valid)."""
        self.pairing.new_code()
        return self.pairing_info()

    def pairing_info(self):
        active = self.pairing.active()
        if active is None:
            return None
        code, left = active
        link = f"https://t.me/{self.username}?start={code}" if self.username else ""
        return display(code), link, left

    def unlink(self, user_id):
        found = self.settings.unlink(user_id)
        if found:
            self._changed()
        return found

    def broadcast(self, text):
        """Writes to every linked account on the bot's own account (a timer that has come due, for example). It does
        not wait: the sending goes on in a thread of its own. Returns how many accounts it is going to write to."""
        with self._lock:
            run = self._run
        users = self.settings.users_snapshot()
        text = plain_text(text)
        if run is None or not users or not text:
            return 0

        def send():
            for user in users:
                # an account that was unlinked while the text was waiting gets nothing
                if self.settings.is_allowed(user["id"]):
                    self._send(run, user["id"], text)

        threading.Thread(target=send, daemon=True, name="telegram-notify").start()
        return len(users)

    # ---------- Reading the chat ----------

    def _poll(self, run):
        if run.after is not None:
            # The old reader may still hold its request; two readers of one bot make Telegram answer "conflict"
            run.after.join(timeout=POLL_TIMEOUT + 20)
        api = run.api
        offset, failures, ready = None, 0, False
        while not run.stop.is_set():
            try:
                if not ready:
                    me = api.get_me() or {}
                    self.username = me.get("username") or ""
                    api.delete_webhook()
                    try:
                        api.set_my_commands(COMMANDS)
                    except TelegramError:
                        logger.info("Telegram: меню команд бота не обновлено")
                    ready = True
                    self._changed()
                updates = api.get_updates(offset, POLL_TIMEOUT)
                if failures or self.state != "ok":
                    self._status(run, "ok", f"Работает · @{self.username}" if self.username else "Работает")
                failures = 0
                skipped = {}
                for update in updates:
                    update_id = update.get("update_id")
                    if not isinstance(update_id, int):
                        continue
                    offset = update_id + 1
                    self._dispatch(run, update, skipped)
                for chat_id, count in skipped.items():
                    self._send(run, chat_id, f"Пропустила сообщений: {count}. Они пришли, пока ассистент был "
                                             f"недоступен (больше {STALE_SECONDS // 60} мин назад). "
                                             f"Если команда ещё нужна, отправьте её заново.")
                self._expire_pending()
            except AuthError as error:
                self._status(run, "error", str(error))
                return
            except ConflictError as error:
                ready = False
                self._status(run, "error", str(error))
                run.stop.wait(CONFLICT_WAIT)
            except RateLimitError as error:
                run.stop.wait(min(error.retry_after or 5, 60))
            except TelegramError as error:
                failures += 1
                delay = BACKOFF[min(failures, len(BACKOFF)) - 1]
                self._status(run, "error", f"{error} Повтор через {delay} с.")
                run.stop.wait(delay)
            except Exception:
                logger.exception("Telegram: неожиданная ошибка при чтении сообщений")
                failures += 1
                delay = BACKOFF[min(failures, len(BACKOFF)) - 1]
                self._status(run, "error", f"Внутренняя ошибка (подробности в журнале). Повтор через {delay} с.")
                run.stop.wait(delay)

    def _dispatch(self, run, update, skipped):
        try:
            if isinstance(update.get("message"), dict):
                self._on_message(run, update["message"], skipped)
            elif isinstance(update.get("callback_query"), dict):
                self._on_callback(run, update["callback_query"])
        except Exception:
            logger.exception("Telegram: не удалось обработать сообщение")

    # ---------- Messages ----------

    def _on_message(self, run, message, skipped):
        chat, sender = message.get("chat") or {}, message.get("from") or {}
        user_id = sender.get("id")
        # Only a private talk with a person counts: nobody else can take part in it
        if (chat.get("type") != "private" or sender.get("is_bot") or not isinstance(user_id, int)
                or chat.get("id") != user_id):
            return
        text = (message.get("text") or "").strip()
        command, argument = split_command(text)
        allowed = self.settings.is_allowed(user_id)
        if command in ("start", "link"):
            self._on_pairing(run, sender, command, argument, allowed)
            return
        if not allowed:
            self._on_stranger(run, sender)
            return
        age = run.api.server_now() - (message.get("date") or 0)
        if age > STALE_SECONDS:
            skipped[user_id] = skipped.get(user_id, 0) + 1
            return
        if not self._rate_ok(run, user_id):
            return
        if command == "help":
            self._send(run, user_id, HELP_TEXT)
        elif command == "unlink":
            self.settings.unlink(user_id)
            self._changed()
            self._notice(f"Telegram: аккаунт {person_name(sender)} отвязан.")
            self._send(run, user_id, "Аккаунт отвязан, больше я его не слушаюсь. Чтобы привязать снова, возьмите "
                                     "новый код в приложении.")
        elif command == "cancel":
            session = self._session(user_id)
            if not session.deliver(_CANCELLED):
                self._send(run, user_id, "Сейчас нечего отменять.")
        elif command is not None:
            self._send(run, user_id, "У бота нет такой команды. Все они в /help.")
        else:
            incoming = self._incoming(message, sender)
            if incoming is None:
                self._send(run, user_id, "Я понимаю текст и голосовые сообщения. Пришлите одно из них.")
            else:
                self._accept(run, incoming)

    @staticmethod
    def _incoming(message, sender):
        who = person_name(sender)
        text = (message.get("text") or "").strip()
        voice = message.get("voice") or message.get("audio")
        if isinstance(voice, dict) and voice.get("file_id"):
            return Incoming(sender["id"], sender["id"], who, voice=voice)
        if text:
            return Incoming(sender["id"], sender["id"], who, text=text)
        return None

    def _accept(self, run, incoming, job=None):
        session = self._session(incoming.chat_id)
        if job is None:
            if session.deliver(incoming):
                return
            if session.queued >= MAX_QUEUED:
                self._send(run, incoming.chat_id, "Слишком много команд ждут своей очереди. Подождите немного.")
                return
            job = Job(incoming)
        with session.cond:
            session.queued += 1
        run.jobs.put(job)

    def _session(self, chat_id):
        with self._lock:
            session = self._sessions.get(chat_id)
            if session is None:
                session = self._sessions[chat_id] = _Session()
            return session

    def _rate_ok(self, run, user_id):
        now = time.monotonic()
        with self._lock:
            stamps = self._rate.setdefault(user_id, deque())
            while stamps and now - stamps[0] > RATE_SECONDS:
                stamps.popleft()
            if len(stamps) < RATE_MESSAGES:
                stamps.append(now)
                return True
        if self._notices.allow(("rate", user_id), RATE_SECONDS):
            self._send(run, user_id, "Слишком много сообщений подряд. Подождите минуту.")
        return False

    def _on_stranger(self, run, sender):
        if not self._strangers.allow(sender["id"], STRANGER_EVERY):
            return
        name = person_name(sender)
        logger.warning("Telegram: пишет не привязанный аккаунт %s (id %s), команды не выполняются", name, sender["id"])
        self._notice(f"Telegram: не привязанный аккаунт {name} написал боту. Команды не выполняются.")
        self._send(run, sender["id"], STRANGER_TEXT)

    def _on_pairing(self, run, sender, command, code, allowed):
        user_id = sender["id"]
        if allowed:
            self._send(run, user_id, "Этот аккаунт уже привязан.\n\n" + HELP_TEXT)
            return
        if not code:
            self._on_stranger(run, sender)
            return
        if self.pairing.locked(user_id):
            self._send(run, user_id, "Слишком много неверных кодов. Подождите 15 минут или возьмите новый код "
                                     "в приложении.")
            return
        if not self.pairing.check(user_id, code):
            logger.warning("Telegram: неверный или старый код привязки от %s (id %s)", person_name(sender), user_id)
            self._send(run, user_id, "Код не подходит или устарел. Возьмите новый на странице «Telegram» "
                                     "в приложении Астра.")
            return
        name = person_name(sender)
        self.settings.link(user_id, name, sender.get("username") or "")
        logger.info("Telegram: привязан аккаунт %s (id %s)", name, user_id)
        self._notice(f"Telegram: привязан аккаунт {name}.")
        self._changed()
        self._send(run, user_id, "Готово, аккаунт привязан!\n\n" + HELP_TEXT)

    # ---------- Buttons ----------

    def _on_callback(self, run, query):
        api = run.api
        sender = query.get("from") or {}
        message = query.get("message") or {}
        user_id = sender.get("id")
        chat_id = (message.get("chat") or {}).get("id")
        callback_id = query.get("id")

        def answer(text=None):
            try:
                api.answer_callback_query(callback_id, text)
            except TelegramError:
                pass

        def edit(text):
            try:
                api.edit_message_text(chat_id, message.get("message_id"), text)
            except TelegramError:
                pass

        parts = str(query.get("data") or "").split(":")
        if not isinstance(user_id, int) or chat_id != user_id or not self.settings.is_allowed(user_id):
            answer()
            return
        if len(parts) != 3 or parts[0] != "c":
            answer()
            return
        with self._lock:
            pending = self._pending.get(parts[1])
            mine = pending is not None and pending.user_id == user_id and pending.chat_id == chat_id
            if mine:
                del self._pending[parts[1]]
        if pending is not None and not mine:
            # a button that belongs to somebody else changes nothing, not even for its owner
            answer()
            return
        if pending is None or time.monotonic() - pending.created > CONFIRM_SECONDS:
            answer("Время вышло. Отправьте команду заново.")
            edit("⌛ Время подтверждения вышло. Отправьте команду заново.")
            return
        if parts[2] == "1":
            answer("Выполняю")
            edit(f"{pending.prompt}\n✅ Подтверждено")
            incoming = Incoming(chat_id, user_id, pending.who)
            self._accept(run, incoming, Job(incoming, text=pending.text, note=pending.note, confirmed=True))
        else:
            answer("Отменено")
            edit(f"{pending.prompt}\n✖ Отменено")

    def _expire_pending(self):
        now = time.monotonic()
        with self._lock:
            for token in [token for token, item in self._pending.items() if now - item.created > CONFIRM_SECONDS]:
                del self._pending[token]

    def _ask_to_confirm(self, run, incoming, text, note, effect, reply):
        token = secrets.token_urlsafe(6)
        prompt = f"Эта команда {effect}. Выполнить?"
        with self._lock:
            self._pending[token] = Pending(incoming.chat_id, incoming.user_id, incoming.who, text, note, prompt)
        reply.add(prompt)
        reply.flush(markup={"inline_keyboard": [[
            {"text": "Да, выполнить", "callback_data": f"c:{token}:1"},
            {"text": "Отмена", "callback_data": f"c:{token}:0"},
        ]]})

    # ---------- Jobs ----------

    def _work(self, run):
        while not run.stop.is_set():
            try:
                job = run.jobs.get(timeout=1)
            except queue.Empty:
                continue
            if job is None:
                return
            session = self._session(job.incoming.chat_id)
            with session.cond:
                session.queued = max(0, session.queued - 1)
            try:
                self._run_job(run, job)
            except Exception:
                logger.exception("Telegram: ошибка при выполнении команды")

    def _run_job(self, run, job):
        incoming = job.incoming
        if not self.settings.is_allowed(incoming.user_id):
            return
        reply = Reply(self, run, incoming.chat_id)
        try:
            text, note = job.text, job.note
            if text is None:
                self._typing(run, incoming.chat_id)
                text, note = self._text_of(run, incoming, reply)
                if not text:
                    return
            verdict = self.host.check(text)
            if verdict is None or not verdict.query:
                reply.add("Слушаю вас. Напишите или скажите, что сделать.")
                return
            if verdict.local_only:
                reply.add("Эту команду можно выполнить только на самом компьютере.")
                return
            if verdict.effect and self.settings.confirm_risky and not job.confirmed:
                self._ask_to_confirm(run, incoming, text, note, verdict.effect, reply)
                return
            self._execute(run, incoming, text, note, reply)
        except Exception:
            logger.exception("Telegram: не удалось выполнить команду")
            reply.add("Не получилось выполнить команду. Подробности в журнале на компьютере.")
        finally:
            reply.flush(close=True)

    def _execute(self, run, incoming, text, note, reply):
        io = RemoteIO(self, run, incoming, reply)
        future = self.host.run(text, io, note)
        if future is None:
            reply.add("Слушаю вас. Напишите или скажите, что сделать.")
            return
        deadline = time.monotonic() + JOB_TIMEOUT
        while True:
            try:
                future.result(timeout=TYPING_EVERY)
                break
            except FutureTimeout:
                if run.stop.is_set():
                    return
                if time.monotonic() > deadline:
                    reply.add("Команда выполняется слишком долго. Я не жду её, ответ может прийти позже.")
                    return
                self._typing(run, incoming.chat_id)
        if io.said == 0:
            reply.add("Готово.")

    def _text_of(self, run, incoming, reply):
        """(the text of a message, a note for the dialog). A voice message is downloaded, decoded and recognized.
        The text is None when nothing can be made of the message; the person is told why."""
        if incoming.voice is None:
            return incoming.text, None
        voice = incoming.voice
        if (voice.get("duration") or 0) > MAX_SECONDS:
            reply.add(f"Голосовое слишком длинное: я разбираю не больше {int(MAX_SECONDS)} секунд.")
            return None, None
        if (voice.get("file_size") or 0) > FILE_LIMIT:
            reply.add("Файл слишком большой: Telegram не отдаёт ботам больше 20 МБ.")
            return None, None
        try:
            info = run.api.get_file(voice["file_id"]) or {}
            audio = decode_audio(run.api.download(info.get("file_path")))
            text, stats = self.host.transcribe(audio.pcm, audio.rate)
        except AudioDecodeError as error:
            reply.add(str(error))
            return None, None
        except TelegramError as error:
            reply.add(f"Не получилось забрать голосовое из Telegram: {error}")
            return None, None
        except Exception:
            logger.exception("Telegram: не удалось распознать голосовое")
            reply.add("Не получилось распознать голосовое. Подробности в журнале на компьютере.")
            return None, None
        if not text:
            reply.add("Не расслышала ни слова. Скажите чётче или напишите текстом.")
            return None, None
        reply.add(f"🎙 «{text}»")
        return text, f"голосовое · распознавание {stats['decode_ms']:.0f} мс"

    def _typing(self, run, chat_id):
        try:
            run.api.send_chat_action(chat_id)
        except TelegramError:
            pass

    # ---------- Sending ----------

    def _send(self, run, chat_id, text, markup=None):
        """Sends a text (in pieces if it is long), trying again when Telegram asks to wait or the network blinks.
        Never raises: an answer that cannot be delivered must not break the command."""
        pieces = split_text(text) or [""]
        for index, piece in enumerate(pieces):
            keyboard = markup if index == len(pieces) - 1 else None
            for attempt in range(3):
                try:
                    run.api.send_message(chat_id, piece, keyboard)
                    break
                except RateLimitError as error:
                    run.stop.wait(min(error.retry_after or 3, 30))
                except NetworkError as error:
                    if attempt == 2:
                        logger.warning("Telegram: сообщение не отправлено: %s", error)
                        return False
                    run.stop.wait(1.5 * (attempt + 1))
                except BlockedError:
                    logger.warning("Telegram: человек остановил бота, ответ не отправлен")
                    return False
                except TelegramError as error:
                    logger.warning("Telegram: сообщение не отправлено: %s", redact(error))
                    return False
            else:
                logger.warning("Telegram: сообщение не отправлено: Telegram просит подождать")
                return False
        return True
