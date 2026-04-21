"""In-memory log tail used by the standalone monitor logs page."""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _MonitorLogHandler(logging.Handler):
    def __init__(self, owner: "MonitorLogBuffer") -> None:
        super().__init__()
        self._owner = owner

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            self._owner.append(record, formatted)
        except Exception:
            # Never let monitor capture break the app's primary logging flow.
            self.handleError(record)


class MonitorLogBuffer:
    def __init__(self, max_entries: int = 2000) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._next_id = 1
        self._handler = _MonitorLogHandler(self)
        self._handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DEFAULT_LOG_DATE_FORMAT))
        self._installed = False

    def install(self) -> None:
        with self._lock:
            if self._installed:
                return
            self._installed = True

        root_logger = logging.getLogger()
        if self._handler not in root_logger.handlers:
            root_logger.addHandler(self._handler)

        # Uvicorn often disables propagation for these loggers, so attach directly.
        for logger_name in ("uvicorn.error", "uvicorn.access"):
            logger = logging.getLogger(logger_name)
            if not logger.propagate and self._handler not in logger.handlers:
                logger.addHandler(self._handler)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._next_id = 1

    def append(self, record: logging.LogRecord, formatted: str) -> None:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone().isoformat()
        entry = {
            "id": 0,
            "created_at": timestamp,
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "line": formatted,
            "pathname": record.pathname,
            "lineno": record.lineno,
            "thread_name": record.threadName,
            "process": record.process,
        }
        with self._lock:
            entry["id"] = self._next_id
            self._next_id += 1
            self._entries.append(entry)

    def latest_id(self) -> int:
        with self._lock:
            if not self._entries:
                return 0
            return int(self._entries[-1]["id"])

    def list_entries(
        self,
        *,
        limit: int = 200,
        after_id: int | None = None,
        level: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_level = (level or "all").strip().lower()
        if normalized_level == "warn":
            normalized_level = "warning"
        query_text = (query or "").strip().lower()

        with self._lock:
            snapshot = list(self._entries)

        if after_id is not None:
            snapshot = [entry for entry in snapshot if int(entry["id"]) > after_id]

        if normalized_level and normalized_level != "all":
            snapshot = [entry for entry in snapshot if str(entry["level"]).lower() == normalized_level]

        if query_text:
            snapshot = [
                entry
                for entry in snapshot
                if query_text in str(entry["line"]).lower()
                or query_text in str(entry["message"]).lower()
                or query_text in str(entry["logger"]).lower()
            ]

        if after_id is None and limit > 0:
            snapshot = snapshot[-limit:]
        elif after_id is not None and limit > 0:
            snapshot = snapshot[:limit]

        return [dict(entry) for entry in snapshot]


monitor_log_buffer = MonitorLogBuffer()
