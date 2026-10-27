"""Small building blocks shared by the pages: buttons, cards, status lights."""
import customtkinter as ctk

from src.interface import theme as t

_BUTTON_STYLES = {
    "primary": dict(fg_color=t.ACCENT, hover_color=t.ACCENT_HOVER, text_color=t.ACCENT_TEXT, border_width=0),
    "secondary": dict(fg_color="transparent", hover_color=t.HOVER, text_color=t.TEXT, border_width=1,
                      border_color=t.BORDER),
    "ghost": dict(fg_color="transparent", hover_color=t.HOVER, text_color=t.TEXT, border_width=0),
}


def make_button(master, text="", command=None, kind="primary", width=110, height=36, image=None, font_size=13,
                **options):
    """A round button: kind is primary (filled), secondary (outlined) or ghost (flat)."""
    style = dict(_BUTTON_STYLES[kind])
    style.update(options)
    return ctk.CTkButton(
        master, text=text, command=command, width=width, height=height, corner_radius=height // 2,
        font=(t.FONT, font_size), image=image, compound="left" if text else "center",
        **style,
    )


def make_progress(master, **options):
    return ctk.CTkProgressBar(master, height=12, corner_radius=6, fg_color=t.BORDER, progress_color=t.ACCENT,
                              **options)


class Card(ctk.CTkFrame):
    """A rounded block with a title and a description; its texts re-wrap when the window is resized."""

    def __init__(self, master, title, description=""):
        super().__init__(master, corner_radius=18, fg_color=t.SURFACE, border_width=1, border_color=t.BORDER)
        self._wrapped = []
        ctk.CTkLabel(self, text=title, font=(t.FONT, 16, "bold"), text_color=t.TEXT, anchor="w").pack(
            fill="x", padx=22, pady=(18, 0)
        )
        if description:
            self.add_text(description, color=t.MUTED, size=12, pady=(3, 12))
        else:
            ctk.CTkFrame(self, height=8, fg_color="transparent").pack()
        self.bind("<Configure>", self._rewrap, add="+")

    # A label that follows the width of the card
    def add_text(self, text="", color=t.TEXT, size=13, bold=False, pady=(0, 0)):
        label = ctk.CTkLabel(
            self, text=text, font=(t.FONT, size, "bold") if bold else (t.FONT, size), text_color=color,
            anchor="w", justify="left", wraplength=500,
        )
        label.pack(fill="x", padx=22, pady=pady)
        self._wrapped.append(label)
        return label

    def _rewrap(self, event):
        width = max(200, event.width - 48)
        for label in self._wrapped:
            if abs(int(label.cget("wraplength")) - width) > 2:
                label.configure(wraplength=width)


class StatusLight(ctk.CTkFrame):
    """A colored dot with a caption: the way the status bar shows AI, STT and TTS."""

    def __init__(self, master, caption):
        super().__init__(master, corner_radius=14, fg_color=t.SURFACE_ALT, height=26)
        self.state = "unknown"
        self.dot = ctk.CTkFrame(self, width=9, height=9, corner_radius=5, fg_color=t.OFF)
        self.dot.pack(side="left", padx=(10, 6), pady=8)
        ctk.CTkLabel(self, text=caption, font=(t.FONT, 11, "bold"), text_color=t.TEXT).pack(side="left", padx=(0, 10))

    def set_state(self, state):
        self.state = state if state in t.SERVICE_COLORS else "unknown"
        self.dot.configure(fg_color=t.SERVICE_COLORS[self.state])


def page_header(master, title, subtitle):
    """The title of a page; returns the frame so a page can add a button on its right side."""
    header = ctk.CTkFrame(master, fg_color="transparent")
    header.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(header, text=title, font=(t.FONT, 26, "bold"), text_color=t.TEXT, anchor="w").grid(
        row=0, column=0, sticky="w"
    )
    ctk.CTkLabel(header, text=subtitle, font=(t.FONT, 13), text_color=t.MUTED, anchor="w").grid(
        row=1, column=0, sticky="w"
    )
    return header


# Width of the content of a page: on a wide window it stays in the middle instead of stretching to the edges
CONTENT_MAX_WIDTH = 920
CONTENT_MARGIN = 24


def center_content(page, container):
    """Keeps everything gridded into the container in a centered column of limited width."""
    state = {"side": None}

    def update(_event=None):
        side = max(CONTENT_MARGIN, (page.winfo_width() - CONTENT_MAX_WIDTH) // 2)
        if side == state["side"]:
            return
        state["side"] = side
        for child in container.grid_slaves():
            child.grid_configure(padx=side)

    page.bind("<Configure>", update, add="+")
