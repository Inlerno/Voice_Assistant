"""Workarounds for the bugs of customtkinter that show up when the window is built again."""


def forget_destroyed_widgets():
    """The drop-down menu of a combo box or an option menu is never taken off the list of customtkinter's scaling
    callbacks when it is destroyed, so the next change of the scale calls a menu that is gone and fails.
    The callbacks of the widgets that do not exist any more are removed from the list here."""
    try:
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    except ImportError:
        return 0
    removed = 0
    for callbacks in list(ScalingTracker.window_widgets_dict.values()):
        alive = []
        for callback in callbacks:
            owner = getattr(callback, "__self__", None)
            try:
                gone = owner is not None and not owner.winfo_exists()
            except Exception:
                gone = True
            if gone:
                removed += 1
            else:
                alive.append(callback)
        callbacks[:] = alive
    return removed
