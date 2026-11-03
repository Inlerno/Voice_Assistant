"""A small client of the Telegram Bot API: plain HTTPS calls with requests.

The token of a bot is a password to it, so it never leaves this module in a readable form: every error that is
raised here, and every line of the log that goes through the filter below, has the token cut out of it.
"""
import logging
import re
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"
# What one message may hold; a longer text is sent in pieces
MESSAGE_LIMIT = 4096
# What a bot can download (the limit of Telegram)
FILE_LIMIT = 20 * 1024 * 1024

_TOKEN_FORM = re.compile(r"\d{3,}:[A-Za-z0-9_-]{10,}")
# A token inside a text: the digits of the bot, a colon and the secret part
_TOKEN_IN_TEXT = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")
_known_tokens = set()


def valid_token(token):
    """Whether the text looks like a token of a bot (123456789:AAE...). The check is made before any request, so an
    odd text can never become a part of an address."""
    # fullmatch: `$` would let a line break at the end through
    return isinstance(token, str) and bool(_TOKEN_FORM.fullmatch(token))


def redact(text):
    """The text without any token in it."""
    text = str(text)
    for token in sorted(_known_tokens, key=len, reverse=True):
        text = text.replace(token, "<токен>")
    return _TOKEN_IN_TEXT.sub("<токен>", text)


class TokenFilter(logging.Filter):
    """For the handlers of the log: a token that gets into any line, from any library, is cut out of it."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        # The text of an error that is logged with its traceback is made later, by the formatter; it is made here,
        # so that it can be cleaned too (the formatter uses what is already there)
        try:
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:
            pass
        return True


def protect_log():
    """Puts the filter on every handler of the log (it is done once per handler)."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, TokenFilter) for f in handler.filters):
            handler.addFilter(TokenFilter())


class TelegramError(Exception):
    """Something went wrong; the text is written for the user and has no token in it."""

    def __init__(self, message, code=None, retry_after=None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class AuthError(TelegramError):
    """Telegram does not know this token."""


class ConflictError(TelegramError):
    """Another program reads the updates of this bot, or the bot has a webhook."""


class BlockedError(TelegramError):
    """The person has blocked the bot or never started it: nothing can be sent to them."""


class RateLimitError(TelegramError):
    """Too many requests: Telegram asks to wait (retry_after seconds)."""


class NetworkError(TelegramError):
    """No answer from Telegram: no internet, a proxy that does not work, a timeout."""


def normalize_base(base_url):
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return API_URL
    if not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", base):
        raise TelegramError("Адрес сервера Telegram должен начинаться с http:// или https://.")
    return base


def split_text(text, limit=MESSAGE_LIMIT):
    """The text in pieces of at most `limit` characters: cut at the end of a paragraph, a line, a sentence or a word,
    the way that is the farthest in the text but not in its first third."""
    text = str(text).strip()
    pieces = []
    while len(text) > limit:
        window = text[:limit]
        floor = limit // 3
        cut = 0
        for mark, shift in (("\n\n", 0), ("\n", 0), (". ", 1), ("! ", 1), ("? ", 1), (" ", 0)):
            at = window.rfind(mark)
            if at > floor:
                cut = at + shift
                break
        if not cut:
            cut = limit
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        pieces.append(text)
    return pieces


class TelegramApi:
    def __init__(self, token, base_url=API_URL, proxy="", timeout=20, trust_env=True):
        if not valid_token(token):
            raise AuthError("Токен бота выглядит неверно. Он должен быть такого вида: 123456789:AAE-... "
                            "Его выдаёт @BotFather.")
        self.token = token
        _known_tokens.add(token)
        self.base_url = normalize_base(base_url)
        self.proxy = (proxy or "").strip()
        self.timeout = timeout
        # False: ignore the proxy settings of the system (the tests talk to a server on this computer)
        self.trust_env = trust_env
        # Telegram time minus the time of this computer, in seconds: a wrong clock must not make messages "old"
        self.clock_offset = 0.0
        self._local = threading.local()

    # ---------- Requests ----------

    def _session(self):
        # One session per thread: the polling and the answers never wait for each other's connection
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._local.session = requests.Session()
            session.trust_env = self.trust_env
        return session

    @property
    def _proxies(self):
        # Given with every request: the proxy of the environment would win over the one set in the session
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def server_now(self):
        """The time of Telegram now, in seconds since 1970."""
        return time.time() + self.clock_offset

    def _note_server_time(self, response):
        try:
            stamp = parsedate_to_datetime(response.headers.get("Date", "")).timestamp()
            self.clock_offset = stamp - time.time()
        except Exception:
            pass

    @staticmethod
    def _network_error(error):
        # The text of the original error holds the address with the token, so only its kind is told
        if isinstance(error, requests.exceptions.InvalidSchema) and "SOCKS" in str(error).upper():
            text = "Для SOCKS-прокси нужен пакет PySocks. Запустите Astra.bat ещё раз: он доустановит недостающее."
        elif isinstance(error, requests.exceptions.ProxyError):
            text = "Не удаётся подключиться через прокси. Проверьте его адрес."
        elif isinstance(error, requests.exceptions.SSLError):
            text = "Ошибка защищённого соединения с Telegram (SSL)."
        elif isinstance(error, (requests.exceptions.Timeout,)):
            text = "Telegram не отвечает: время ожидания вышло."
        elif isinstance(error, requests.exceptions.ConnectionError):
            text = "Нет соединения с Telegram. Проверьте интернет или укажите прокси."
        else:
            text = f"Ошибка сети ({type(error).__name__})."
        logger.debug("Сетевая ошибка Telegram: %s", redact(error))
        return NetworkError(text)

    def call(self, method, params=None, timeout=None):
        """One call of the API; returns the "result" of the answer."""
        url = f"{self.base_url}/bot{self.token}/{method}"
        try:
            response = self._session().post(url, json=params or {}, timeout=timeout or self.timeout,
                                            proxies=self._proxies)
        except requests.RequestException as error:
            raise self._network_error(error) from None
        self._note_server_time(response)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("ok") is True:
            return payload.get("result")
        raise self._api_error(response.status_code, payload)

    @staticmethod
    def _api_error(status, payload):
        info = payload if isinstance(payload, dict) else {}
        code = info.get("error_code") or status
        description = str(info.get("description") or "")
        retry_after = (info.get("parameters") or {}).get("retry_after")
        if code in (401, 404):
            return AuthError("Telegram не принимает этот токен. Проверьте его в @BotFather.", code)
        if code == 409:
            return ConflictError("Этот бот уже читает другая программа (или у него включён webhook). "
                                 "Остановите её или возьмите другой токен.", code)
        if code == 429:
            return RateLimitError("Telegram просит подождать: слишком много запросов.", code,
                                  retry_after=float(retry_after or 5))
        if code == 403:
            return BlockedError("Telegram не даёт писать этому человеку: он остановил бота.", code)
        if not isinstance(payload, dict):
            return TelegramError(f"Telegram ответил непонятно (HTTP {status}).", code)
        return TelegramError(f"Ошибка Telegram ({code}): {redact(description)}", code)

    # ---------- Methods of the Bot API ----------

    def get_me(self):
        return self.call("getMe")

    def delete_webhook(self):
        # A bot with a webhook cannot be read with getUpdates; the updates that wait are kept
        return self.call("deleteWebhook", {"drop_pending_updates": False})

    def set_my_commands(self, commands):
        return self.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})

    def get_updates(self, offset=None, timeout=20, allowed=("message", "callback_query")):
        params = {"timeout": timeout, "allowed_updates": list(allowed)}
        if offset is not None:
            params["offset"] = offset
        # the server holds the request for `timeout` seconds: the wait for the answer is longer
        return self.call("getUpdates", params, timeout=timeout + 15) or []

    def send_message(self, chat_id, text, reply_markup=None):
        params = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return self.call("sendMessage", params)

    def send_text(self, chat_id, text, reply_markup=None):
        """A text of any length: the long one goes in pieces, the keyboard (if any) under the last one."""
        pieces = split_text(text) or [""]
        result = None
        for index, piece in enumerate(pieces):
            last = index == len(pieces) - 1
            result = self.send_message(chat_id, piece, reply_markup if last else None)
        return result

    def send_chat_action(self, chat_id, action="typing"):
        return self.call("sendChatAction", {"chat_id": chat_id, "action": action})

    def answer_callback_query(self, callback_id, text=None):
        params = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
        return self.call("answerCallbackQuery", params)

    def edit_message_text(self, chat_id, message_id, text):
        """Changes the text of a message of the bot and takes the buttons away."""
        return self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                             "reply_markup": {"inline_keyboard": []}})

    def get_file(self, file_id):
        return self.call("getFile", {"file_id": file_id})

    def download(self, file_path, limit=FILE_LIMIT):
        """The bytes of a file that getFile has pointed to."""
        parts = str(file_path or "").split("/")
        if not file_path or file_path.startswith("/") or ".." in parts or "\\" in file_path or ":" in parts[0]:
            raise TelegramError("Telegram прислал странный путь к файлу.")
        url = f"{self.base_url}/file/bot{self.token}/{quote(file_path, safe='/')}"
        try:
            with self._session().get(url, stream=True, timeout=self.timeout, proxies=self._proxies) as response:
                if response.status_code != 200:
                    raise TelegramError(f"Не удалось скачать файл (HTTP {response.status_code}).", response.status_code)
                total, chunks = 0, []
                for chunk in response.iter_content(64 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise TelegramError("Файл слишком большой.")
                    chunks.append(chunk)
        except requests.RequestException as error:
            raise self._network_error(error) from None
        return b"".join(chunks)
