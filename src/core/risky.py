"""Commands that need care. The keys are the "make" of a command in src/config/commands.json."""

# Commands that change the state of the computer: they start only after a confirmation
# (a window in the program, a button in a chat). The value says what the command does.
RISKY_COMMANDS = {
    "shutdown": "выключит компьютер",
    "restart": "перезагрузит компьютер",
    "sleep": "переведёт компьютер в спящий режим",
    "empty": "очистит корзину",
    "finish": "завершит работу ассистента",
}

# Commands that cannot be given from a chat. They work only on the computer itself:
#   hotkey, keyboard_write - press keys in the window that is open there at that moment, and a person who writes
#                            from a chat does not see that window;
#   finish                 - ends the assistant, after which nobody answers in the chat and nothing can start it again.
LOCAL_ONLY_COMMANDS = frozenset({"hotkey", "keyboard_write", "finish"})
