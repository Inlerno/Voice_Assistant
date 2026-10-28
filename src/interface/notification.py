"""A small window on top of everything when a timer, an alarm or a reminder comes due."""
import customtkinter as ctk

from src.interface import graphics as g
from src.interface import theme as t
from src.interface.widgets import make_button
from src.productivity.planner import ALARM, KIND_TITLES, REMINDER, TIMER

KIND_ICONS = {TIMER: "hourglass", ALARM: "clock", REMINDER: "bell"}
WIDTH, HEIGHT, GAP, MARGIN = 380, 176, 12, 24
SNOOZE_MINUTES = 5


class Notification(ctk.CTkToplevel):
    def __init__(self, app, item, message, on_close):
        super().__init__(app.root, fg_color=t.BG)
        self.app = app
        self.item = item
        self.on_close = on_close
        self.title("Астра")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.dismiss)

        card = ctk.CTkFrame(self, corner_radius=18, fg_color=t.SURFACE, border_width=1, border_color=t.BORDER)
        card.pack(fill="both", expand=True, padx=8, pady=8)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 4))
        ctk.CTkLabel(head, text=f"  {KIND_TITLES[item['kind']]}", image=g.icon(KIND_ICONS[item["kind"]], t.ACCENT, 22),
                     compound="left", font=(t.FONT, 16, "bold"), text_color=t.TEXT).pack(side="left")
        ctk.CTkLabel(card, text=message, font=(t.FONT, 14), text_color=t.TEXT, anchor="w", justify="left",
                     wraplength=int(WIDTH - 70)).pack(fill="x", padx=18, pady=(2, 8))
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=18, pady=(0, 14))
        make_button(buttons, "Готово", self.dismiss, "primary", width=110, height=34).pack(side="left")
        make_button(buttons, f"Ещё {SNOOZE_MINUTES} мин", self.snooze, "secondary", width=120, height=34).pack(
            side="left", padx=8)

    def place_at(self, index):
        """Notifications stack up from the bottom right corner of the screen.

        The size of a window is multiplied by the display scaling by customtkinter, its position is not."""
        factor = self._get_window_scaling()
        x = int(self.winfo_screenwidth() - (WIDTH + MARGIN) * factor)
        y = int(self.winfo_screenheight() - (HEIGHT + 60 + index * (HEIGHT + GAP)) * factor)
        self.geometry(f"{WIDTH}x{HEIGHT}+{max(0, x)}+{max(0, y)}")
        self.lift()

    def snooze(self):
        self.app.assistant.planner.snooze(self.item, SNOOZE_MINUTES)
        self.dismiss()

    def dismiss(self):
        self.on_close(self)
        self.destroy()
