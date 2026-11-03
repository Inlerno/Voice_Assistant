"""The page "Telegram": the bot that takes orders from a chat (text and voice messages) and answers there.

The page only shows the state of the bot (assistant.telegram) and changes its settings (assistant.telegram_settings);
everything that waits for the network (checking a token) runs in a thread and comes back through the events of the
window, as everywhere else.
"""
import logging
import threading
import webbrowser
from tkinter import messagebox

import customtkinter as ctk

from src.integrations.telegram.api import TelegramError, valid_token
from src.interface import theme as t
from src.interface.pages import scrolling_page
from src.interface.widgets import Card, SettingRow, divider, make_button, make_entry, make_switch, scale_of, spacer

logger = logging.getLogger(__name__)

STEPS = (
    "В Telegram откройте @BotFather, отправьте ему /newbot и ответьте на вопросы. В конце он пришлёт токен.",
    "Вставьте токен выше и нажмите «Сохранить и включить».",
    "Нажмите «Привязать аккаунт» и отправьте боту код, который появится здесь.",
    "Пишите боту команды или присылайте голосовые сообщения так же, как говорите Астре.",
)
EXAMPLES = (
    "«поставь таймер на десять минут», «разбуди меня в семь тридцать», «добавь задачу купить молоко»",
    "«открой калькулятор», «сколько времени», «какая погода» (город бот спросит в чате)",
    "любой другой вопрос уйдёт языковой модели, ответ придёт в чат",
)
LIMITS = (
    "«Выключи компьютер» и другие команды, которые меняют состояние компьютера, ждут нажатия кнопки в чате.",
    "Команды, которые нажимают клавиши на экране («напиши текст», «закрой программу»), и «пока» работают "
    "только на самом компьютере: из чата не видно, какое окно открыто.",
)


def countdown(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class TelegramPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self.bot = app.assistant.telegram
        self.settings = app.assistant.telegram_settings
        self._checking = False
        self._tick = None
        self._users_shown = None
        body, _ = scrolling_page(self, "Telegram",
                                 "Управляйте ассистентом из чата: пишите команды или присылайте голосовые сообщения")
        self._build_bot(body, 1)
        self._build_accounts(body, 2)
        self._build_options(body, 3)
        self._build_help(body, 4)
        self.refresh()
        # the state may have changed while the page was not shown
        self.bind("<Map>", lambda _e: self.refresh())

    # ---------- Building ----------

    def _build_bot(self, body, row):
        card = Card(body, "Бот", "Принимает команды из Telegram и отвечает в том же чате. "
                                 "Работает, пока запущена Астра и есть интернет.")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=22, pady=(0, 6))
        line.grid_columnconfigure(1, weight=1)
        self.dot = ctk.CTkFrame(line, width=12, height=12, corner_radius=t.pill(12), fg_color=t.OFF)
        self.dot.grid(row=0, column=0, padx=(0, 10))
        self.status = ctk.CTkLabel(line, text="", font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w",
                                   justify="left", wraplength=420)
        self.status.grid(row=0, column=1, sticky="w")
        self.switch = make_switch(line, self._toggle, self.settings.enabled)
        self.switch.grid(row=0, column=2, padx=(12, 0))
        line.bind("<Configure>", lambda e: self.status.configure(
            wraplength=max(160, int(e.width / scale_of(line)) - 110)), add="+")
        divider(card, pady=(8, 10))
        ctk.CTkLabel(card, text="Токен бота", font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w").pack(
            fill="x", padx=22)
        card.add_text("Токен выдаёт @BotFather. Он хранится только на этом компьютере, в файле data/telegram.json, "
                      "и нигде не показывается.", color=t.MUTED, size=12, pady=(0, 8))
        entry_line = ctk.CTkFrame(card, fg_color="transparent")
        entry_line.pack(fill="x", padx=22)
        entry_line.grid_columnconfigure(0, weight=1)
        self.token_entry = make_entry(entry_line, "123456789:AAE…", height=38, show="•")
        self.token_entry.grid(row=0, column=0, sticky="ew")
        self.token_entry.bind("<Return>", lambda _e: self.save_token())
        self.save_button = make_button(entry_line, "Сохранить и включить", self.save_token, "primary", width=210,
                                       height=38)
        self.save_button.grid(row=0, column=1, padx=(10, 0))
        self.remove_button = make_button(entry_line, "Удалить токен", self.remove_token, "ghost", width=130,
                                         height=38)
        spacer(card, 14)
        self.token_note = card.add_text("", color=t.MUTED, size=12, pady=(0, 14), collapse=True)

    def _build_accounts(self, body, row):
        card = Card(body, "Аккаунты", "Бот слушается только привязанных аккаунтов. Всем остальным он не отвечает.")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        self.users_box = ctk.CTkFrame(card, fg_color="transparent")
        self.users_box.pack(fill="x", padx=14)
        self.users_box.grid_columnconfigure(0, weight=1)
        divider(card, pady=(8, 12))
        area = ctk.CTkFrame(card, fg_color="transparent")
        area.pack(fill="x", padx=22, pady=(0, 16))
        area.grid_columnconfigure(1, weight=1)
        self.pair_button = make_button(area, "Привязать аккаунт", self.start_pairing, "primary", width=190, height=38)
        self.pair_button.grid(row=0, column=0, sticky="w")
        self.pair_hint = ctk.CTkLabel(area, text="", font=(t.FONT, 12), text_color=t.MUTED, anchor="w",
                                      justify="left", wraplength=420)
        self.pair_hint.grid(row=0, column=1, sticky="w", padx=(14, 0))
        area.bind("<Configure>", lambda e: self.pair_hint.configure(
            wraplength=max(160, int(e.width / scale_of(area)) - 230)), add="+")
        self.pair_panel = ctk.CTkFrame(area, corner_radius=t.r(16), fg_color=t.SURFACE_ALT, border_width=1,
                                       border_color=t.BORDER)
        self.pair_panel.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.pair_panel.grid_remove()
        ctk.CTkLabel(self.pair_panel, text="Откройте бота в Telegram и отправьте ему это сообщение:",
                     font=(t.FONT, 13), text_color=t.TEXT, anchor="w").pack(fill="x", padx=18, pady=(14, 4))
        self.pair_code = ctk.CTkLabel(self.pair_panel, text="", font=(t.FONT_MONO, 28, "bold"),
                                      text_color=t.ACCENT_VIVID, anchor="w")
        self.pair_code.pack(fill="x", padx=18)
        self.pair_timer = ctk.CTkLabel(self.pair_panel, text="", font=(t.FONT, 12), text_color=t.MUTED, anchor="w")
        self.pair_timer.pack(fill="x", padx=18, pady=(2, 10))
        buttons = ctk.CTkFrame(self.pair_panel, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(0, 14))
        self.link_button = make_button(buttons, "Открыть бота в Telegram", self.open_link, "secondary", width=210,
                                       height=34, font_size=12)
        self.link_button.pack(side="left", padx=(0, 8))
        make_button(buttons, "Скопировать", self.copy_code, "secondary", width=120, height=34, font_size=12).pack(
            side="left", padx=(0, 8))
        make_button(buttons, "Отменить код", self.cancel_pairing, "ghost", width=120, height=34, font_size=12).pack(
            side="left")

    def _build_options(self, body, row):
        card = Card(body, "Параметры")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        settings = self.settings
        confirm = SettingRow(card, "Подтверждать опасные команды",
                             "Выключение, перезагрузка, сон и очистка корзины выполняются после нажатия кнопки «Да» "
                             "в чате.")
        self.confirm_switch = make_switch(confirm.control, lambda v: self._set(confirm_risky=v),
                                          settings.confirm_risky)
        self.confirm_switch.pack()
        aloud = SettingRow(card, "Озвучивать ответы на компьютере",
                           "Обычно ответ приходит только в чат. Включите, чтобы Астра говорила его вслух и дома.")
        self.aloud_switch = make_switch(aloud.control, lambda v: self._set(speak_on_pc=v), settings.speak_on_pc)
        self.aloud_switch.pack()
        remind = SettingRow(card, "Присылать таймеры и напоминания в чат",
                            "Сработавший таймер, будильник или напоминание придёт и в Telegram, "
                            "даже если вы далеко от компьютера.")
        self.remind_switch = make_switch(remind.control, lambda v: self._set(notify_planner=v),
                                         settings.notify_planner)
        self.remind_switch.pack()
        connection = SettingRow(card, "Если Telegram не открывается",
                                "Укажите прокси, например socks5h://127.0.0.1:1080 или http://127.0.0.1:8080. "
                                "Адрес сервера нужен, только если у вас свой сервер Bot API.", stacked=True)
        fields = connection.control
        fields.grid_columnconfigure(0, weight=1)
        self.proxy_entry = make_entry(fields, "Прокси (необязательно)", height=38, width=330)
        self.proxy_entry.grid(row=0, column=0, sticky="w")
        self.base_entry = make_entry(fields, "Адрес сервера Bot API (необязательно)", height=38, width=330)
        self.base_entry.grid(row=1, column=0, sticky="w", pady=(8, 0))
        make_button(fields, "Применить", self.apply_connection, "secondary", width=120, height=38).grid(
            row=0, column=1, rowspan=2, padx=(10, 0))
        for index, widget in enumerate((confirm, remind, aloud, connection)):
            widget.pack(fill="x", padx=22)
            if index < 3:
                divider(card, pady=(12, 12))
        spacer(card, 6)
        self.connection_note = card.add_text("", color=t.MUTED, size=12, pady=(8, 14), collapse=True)
        self.proxy_entry.insert(0, settings.proxy)
        self.base_entry.insert(0, settings.api_base)

    def _build_help(self, body, row):
        card = Card(body, "Как подключить и что можно писать")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 18))
        for number, step in enumerate(STEPS, start=1):
            card.add_text(f"{number}. {step}", size=13, pady=(0, 6))
        card.add_text("Примеры", size=13, bold=True, pady=(10, 4))
        for example in EXAMPLES:
            card.add_text(f"• {example}", color=t.MUTED, size=12, pady=(0, 3))
        card.add_text("Что учесть", size=13, bold=True, pady=(10, 4))
        for limit in LIMITS:
            card.add_text(f"• {limit}", color=t.MUTED, size=12, pady=(0, 3))
        spacer(card, 14)

    # ---------- Showing the state ----------

    def refresh(self):
        settings, bot = self.settings, self.bot
        colors = {"ok": t.OK, "connecting": t.WARN, "error": t.ERROR}
        self.dot.configure(fg_color=colors.get(bot.state, t.OFF))
        self.status.configure(text=bot.status_text)
        self.switch.set(settings.enabled)
        self.confirm_switch.set(settings.confirm_risky)
        self.remind_switch.set(settings.notify_planner)
        self.aloud_switch.set(settings.speak_on_pc)
        self.token_entry.configure(
            placeholder_text="Токен сохранён" if settings.token else "123456789:AAE…")
        if settings.token:
            self.save_button.configure(text="Заменить токен")
            self.remove_button.grid(row=0, column=2, padx=(6, 0))
        else:
            self.save_button.configure(text="Сохранить и включить")
            self.remove_button.grid_remove()
        self._show_users()
        self._show_pairing()

    def _show_users(self):
        users = self.settings.users_snapshot()
        signature = tuple((u["id"], u["name"], u["username"], u["since"]) for u in users)
        if signature == self._users_shown:
            return
        self._users_shown = signature
        for child in self.users_box.winfo_children():
            child.destroy()
        if not users:
            ctk.CTkLabel(self.users_box, text="Пока никто не привязан: бот никого не слушается.",
                         font=(t.FONT, 13), text_color=t.MUTED, anchor="w").grid(row=0, column=0, sticky="w",
                                                                                  padx=10, pady=(4, 4))
            return
        for index, user in enumerate(users):
            frame = ctk.CTkFrame(self.users_box, corner_radius=t.r(14), fg_color=t.SURFACE_ALT)
            frame.grid(row=index, column=0, sticky="ew", pady=(0, 6))
            frame.grid_columnconfigure(0, weight=1)
            name = user["name"] or str(user["id"])
            details = " · ".join(part for part in (f"@{user['username']}" if user["username"] else "",
                                                   f"привязан {user['since']}" if user["since"] else "") if part)
            ctk.CTkLabel(frame, text=name, font=(t.FONT, 14, "bold"), text_color=t.TEXT, anchor="w").grid(
                row=0, column=0, sticky="w", padx=16, pady=(10, 0))
            ctk.CTkLabel(frame, text=details or f"id {user['id']}", font=(t.FONT, 12), text_color=t.MUTED,
                         anchor="w").grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))
            make_button(frame, "Отвязать", lambda u=user: self.unlink(u), "secondary", width=100, height=32,
                        font_size=12).grid(row=0, column=1, rowspan=2, padx=14)

    def _show_pairing(self):
        info = self.bot.pairing_info()
        running = self.bot.running
        self.pair_button.configure(state="normal" if running else "disabled")
        if info is None:
            self.pair_panel.grid_remove()
            self.pair_hint.configure(text="" if running else "Сначала вставьте токен и включите бота.")
            self._cancel_tick()
            return
        code, link, left = info
        self.pair_code.configure(text=f"/link {code}")
        self.pair_timer.configure(text=f"Код действует ещё {countdown(left)} и работает один раз.")
        self.pair_hint.configure(text="")
        if link:
            self.link_button.pack(side="left", padx=(0, 8), before=self.link_button.master.winfo_children()[1])
        else:
            self.link_button.pack_forget()
        self.pair_panel.grid()
        if self._tick is None:
            self._tick = self.after(1000, self._on_tick)

    def _on_tick(self):
        self._tick = None
        if not self.winfo_exists():
            return
        if self.winfo_ismapped():
            self._show_pairing()
        else:
            # the page is hidden: a code that is used up or out of date is noticed when the page is shown again
            self._tick = self.after(1000, self._on_tick) if self.bot.pairing_info() is not None else None

    def _cancel_tick(self):
        if self._tick is not None:
            try:
                self.after_cancel(self._tick)
            except Exception:
                logger.debug("Не удалось остановить таймер кода", exc_info=True)
            self._tick = None

    def destroy(self):
        self._cancel_tick()
        super().destroy()

    def _note(self, label, text, kind="info"):
        label.configure(text=text, text_color={"error": t.ERROR, "ok": t.OK}.get(kind, t.MUTED))

    # ---------- Actions ----------

    def _set(self, **changes):
        try:
            self.settings.update(**changes)
        except ValueError as error:
            self.app.toast.show(str(error), "error", 4000)

    def _toggle(self, value):
        if value and not self.settings.token:
            self.switch.set(False)
            self._note(self.token_note, "Сначала вставьте токен бота.", "error")
            return
        self._set(enabled=value)
        if value:
            self.bot.start()
        else:
            self.bot.stop()
        self.refresh()

    def save_token(self):
        token = self.token_entry.get().strip()
        if not token:
            self._note(self.token_note, "Вставьте токен, который прислал @BotFather.", "error")
            return
        if not valid_token(token):
            self._note(self.token_note, "Токен выглядит неверно. Он такого вида: 123456789:AAE-…", "error")
            return
        if self._checking:
            return
        self._checking = True
        self.save_button.configure(text="Проверяю…", state="disabled")
        self._note(self.token_note, "Спрашиваю у Telegram, что это за бот…")
        threading.Thread(target=self._check_token, args=(token,), daemon=True, name="telegram-check").start()

    def _check_token(self, token):
        me, error = None, None
        try:
            me = self.bot.verify_token(token)
        except TelegramError as problem:
            error = str(problem)
        except Exception:
            logger.exception("Не удалось проверить токен Telegram")
            error = "Не удалось проверить токен. Подробности в журнале."
        self.app.events.put(("telegram_checked", token, me, error))

    def remove_token(self):
        if not messagebox.askyesno("Удалить токен", "Бот перестанет работать, пока вы не вставите токен снова. "
                                                    "Привязанные аккаунты останутся.", parent=self):
            return
        self.settings.update(token="", enabled=False)
        self.bot.stop()
        self.token_entry.delete(0, "end")
        self._note(self.token_note, "")
        self.refresh()

    def handle_event(self, kind, *args):
        if kind == "telegram_checked":
            token, me, error = args
            self._checking = False
            self.save_button.configure(state="normal")
            if error:
                self.refresh()
                self._note(self.token_note, error, "error")
                return
            self.settings.update(token=token, enabled=True)
            self.token_entry.delete(0, "end")
            self.bot.restart()
            name = (me or {}).get("username")
            self.refresh()
            self._note(self.token_note, f"Бот @{name} найден и включён. Теперь привяжите свой аккаунт ниже."
                       if name else "Бот найден и включён. Теперь привяжите свой аккаунт ниже.", "ok")
        else:
            self.refresh()

    def start_pairing(self):
        if not self.bot.running:
            return
        self.bot.new_pairing()
        self._show_pairing()

    def cancel_pairing(self):
        self.bot.pairing.cancel()
        self._show_pairing()

    def copy_code(self):
        info = self.bot.pairing_info()
        if info is None:
            return
        self.clipboard_clear()
        self.clipboard_append(f"/link {info[0]}")
        self.app.toast.show("Сообщение скопировано, вставьте его в чат с ботом", "ok", 3000)

    def open_link(self):
        info = self.bot.pairing_info()
        if info is not None and info[1]:
            webbrowser.open(info[1])

    def unlink(self, user):
        name = user["name"] or str(user["id"])
        if messagebox.askyesno("Отвязать аккаунт", f"Бот перестанет слушаться аккаунта «{name}».", parent=self):
            self.bot.unlink(user["id"])
            self.refresh()

    def apply_connection(self):
        try:
            changed = self.settings.update(proxy=self.proxy_entry.get(), api_base=self.base_entry.get())
        except ValueError as error:
            self._note(self.connection_note, str(error), "error")
            return
        if changed and self.settings.ready:
            self.bot.restart()
        self._note(self.connection_note, "Сохранено." if changed else "Ничего не изменилось.", "ok")
        self.refresh()
