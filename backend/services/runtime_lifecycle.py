# -*- coding: utf-8 -*-
"""Process-local runtime lifecycle state."""

from __future__ import annotations

import threading


_shutdown_event = threading.Event()


def mark_runtime_starting() -> None:
    _shutdown_event.clear()


def mark_runtime_shutting_down() -> None:
    _shutdown_event.set()


def runtime_is_shutting_down() -> bool:
    return _shutdown_event.is_set()
