"""Runtime helpers for Qt Quick Controls style configuration."""

from __future__ import annotations

import os


def ensure_qtquickcontrols_style(default_style: str = "Basic") -> str:
    """Ensure a non-native Qt Quick Controls style is active.

    Native styles can reject background/contentItem customization in QML controls.
    """
    current_style = str(os.environ.get("QT_QUICK_CONTROLS_STYLE", "")).strip()
    if current_style:
        return current_style

    resolved_style = str(default_style).strip() or "Basic"
    os.environ["QT_QUICK_CONTROLS_STYLE"] = resolved_style
    return resolved_style
