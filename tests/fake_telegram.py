"""A stand-in for the Telegram Bot API for tests: a small HTTP server on this computer.

It understands the calls the bot makes (getMe, deleteWebhook, setMyCommands, getUpdates with long polling, sendMessage,
sendChatAction, answerCallbackQuery, editMessageText, getFile and the download of a file), keeps a record of
everything that was sent and lets a test play the part of a person: write a text, send a voice message, tap a button.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "123456789:AAEtesttesttesttesttesttesttest_-x"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            params = json.loads(raw) if raw else {}
        except ValueError:
            params = {}
        prefix = f"/bot{self.server.fake.token}/"
        if not self.path.startswith(prefix):
            self._send(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
            return
        status, body = self.server.fake.handle(self.path[len(prefix):], params)
        self._send(status, body)

    def do_GET(self):
        prefix = f"/file/bot{self.server.fake.token}/"
        if not self.path.startswith(prefix):
            self._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})
            return
        data = self.server.fake.files_by_path.get(self.path[len(prefix):])
        if data is None:
            self._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})
        else:
            self._send(200, data, "application/octet-stream")


class FakeTelegram:
    def __init__(self, token=TOKEN, username="astra_test_bot", hold=0.2):
        self.token = token
        self.username = username
        # How long getUpdates holds the request when there is nothing to give, in seconds (the real one: 20-50)
        self.hold = hold
        self.cond = threading.Condition()
        self.updates = []
        self.next_id = 100
        self.calls = []                 # (method, params) of every call, in order
        self.sent = []                  # the params of every sendMessage
        self.edits = []                 # the params of every editMessageText
        self.answers = []               # the params of every answerCallbackQuery
        self.files = {}                 # file_id -> bytes
        self.files_by_path = {}
        self.failures = {}              # method -> [(http status, body)] that are given one after another
        self.commands = None
        self._message_id = 1000
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.fake = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # ---------- What the server does ----------

    def handle(self, method, params):
        with self.cond:
            self.calls.append((method, params))
            queued = self.failures.get(method)
            if queued:
                return queued.pop(0)
        if method == "getMe":
            return 200, {"ok": True, "result": {"id": 123456789, "is_bot": True, "username": self.username,
                                                "first_name": "Astra"}}
        if method == "deleteWebhook":
            return 200, {"ok": True, "result": True}
        if method == "setMyCommands":
            self.commands = params.get("commands")
            return 200, {"ok": True, "result": True}
        if method == "getUpdates":
            return 200, {"ok": True, "result": self._updates(params)}
        if method == "sendMessage":
            with self.cond:
                self._message_id += 1
                self.sent.append(params)
                self.cond.notify_all()
                return 200, {"ok": True, "result": {"message_id": self._message_id, "chat": {"id": params["chat_id"]}}}
        if method == "sendChatAction":
            return 200, {"ok": True, "result": True}
        if method == "answerCallbackQuery":
            with self.cond:
                self.answers.append(params)
                self.cond.notify_all()
            return 200, {"ok": True, "result": True}
        if method == "editMessageText":
            with self.cond:
                self.edits.append(params)
                self.cond.notify_all()
            return 200, {"ok": True, "result": True}
        if method == "getFile":
            file_id = params.get("file_id")
            if file_id not in self.files:
                return 400, {"ok": False, "error_code": 400, "description": "Bad Request: wrong file_id"}
            return 200, {"ok": True, "result": {"file_id": file_id, "file_path": f"voice/{file_id}.oga"}}
        return 404, {"ok": False, "error_code": 404, "description": "Not Found"}

    def _updates(self, params):
        offset = params.get("offset")
        deadline = time.monotonic() + min(float(params.get("timeout") or 0), self.hold)
        with self.cond:
            if offset is not None:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
            while not self.updates:
                left = deadline - time.monotonic()
                if left <= 0:
                    return []
                self.cond.wait(left)
            return list(self.updates)

    # ---------- A person writes ----------

    def _push(self, **body):
        with self.cond:
            self.next_id += 1
            update = {"update_id": self.next_id, **body}
            self.updates.append(update)
            self.cond.notify_all()
            return update

    def _message(self, user_id, extra, first_name="Анна", username="anna", date=None, chat_type="private",
                 is_bot=False, chat_id=None):
        self._message_id += 1
        message = {"message_id": self._message_id,
                   "from": {"id": user_id, "is_bot": is_bot, "first_name": first_name, "username": username},
                   "chat": {"id": user_id if chat_id is None else chat_id, "type": chat_type},
                   "date": int(time.time()) if date is None else date}
        message.update(extra)
        return message

    def text(self, user_id, text, **options):
        return self._push(message=self._message(user_id, {"text": text}, **options))

    def voice(self, user_id, data, duration=2, **options):
        file_id = f"voice{len(self.files) + 1}"
        self.files[file_id] = data
        self.files_by_path[f"voice/{file_id}.oga"] = data
        extra = {"voice": {"file_id": file_id, "file_unique_id": "u" + file_id, "duration": duration,
                           "mime_type": "audio/ogg", "file_size": len(data)}}
        return self._push(message=self._message(user_id, extra, **options))

    def sticker(self, user_id, **options):
        return self._push(message=self._message(user_id, {"sticker": {"file_id": "x"}}, **options))

    def tap(self, user_id, data, message_id=1, chat_id=None):
        query = {"id": f"cb{self.next_id}", "from": {"id": user_id, "is_bot": False, "first_name": "Анна"},
                 "message": {"message_id": message_id, "chat": {"id": user_id if chat_id is None else chat_id,
                                                                  "type": "private"}},
                 "data": data}
        return self._push(callback_query=query)

    # ---------- What a test looks at ----------

    def texts(self, chat_id=None):
        with self.cond:
            return [m["text"] for m in self.sent if chat_id is None or m["chat_id"] == chat_id]

    def wait_for(self, check, timeout=8.0):
        """Waits until check() is true (it is called with the lock held); returns whether it happened."""
        deadline = time.monotonic() + timeout
        with self.cond:
            while not check():
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.cond.wait(min(left, 0.1))
            return True

    def wait_text(self, containing, chat_id=None, timeout=8.0):
        """The first sent text that contains the words, or None."""
        found = []

        def check():
            for message in self.sent:
                if (chat_id is None or message["chat_id"] == chat_id) and containing in message["text"]:
                    found.append(message)
                    return True
            return False

        return found[0] if self.wait_for(check, timeout) else None

    def count(self, method):
        with self.cond:
            return sum(1 for name, _ in self.calls if name == method)

    def fail(self, method, status, description="", times=1, retry_after=None):
        body = {"ok": False, "error_code": status, "description": description}
        if retry_after is not None:
            body["parameters"] = {"retry_after": retry_after}
        with self.cond:
            self.failures.setdefault(method, []).extend([(status, body)] * times)
