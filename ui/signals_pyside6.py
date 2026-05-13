from PySide6.QtCore import QObject, Signal


class BotSignals(QObject):
    """
    Thread-safe signals for Bot ↔ UI communication (PySide6 version).
    """
    log_message = Signal(str)
    update_item_status = Signal(str, str)
    update_stat = Signal(str, str)
    update_overlay = Signal(list)
    bot_stopped = Signal()
