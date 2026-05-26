# -*- coding: utf-8 -*-
"""Single-writer telemetry persistence for high-frequency SQLite writes."""
from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime
from typing import Any

from models.audit import MonitorNetworkRecord
from models.database import TelemetrySessionLocal

logger = logging.getLogger("catown.telemetry")


class TelemetryWriter:
    """Serialize telemetry writes through one background writer thread."""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="telemetry-writer", daemon=True)
            self._thread.start()
            self._started = True

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._started:
                return
            if drain:
                self.flush(timeout=timeout)
            self._stop_event.set()
            self._queue.put(None)
            thread = self._thread
            self._thread = None
            self._started = False
        if thread is not None:
            thread.join(timeout=timeout)

    def flush(self, *, timeout: float = 5.0) -> bool:
        done = threading.Event()
        self._queue.put(("__flush__", {"done": done}))
        return done.wait(timeout)

    def enqueue_network_record(self, payload: dict[str, Any]) -> None:
        self.start()
        self._queue.put(("network_record", dict(payload)))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                break
            kind, payload = item
            try:
                if kind == "__flush__":
                    done = payload.get("done")
                    if isinstance(done, threading.Event):
                        done.set()
                    continue
                if kind == "network_record":
                    self._write_network_record(payload)
            except Exception as exc:
                logger.warning("[TelemetryWriter] Failed to persist %s: %s", kind, exc)
            finally:
                self._queue.task_done()

    def _write_network_record(self, normalized: dict[str, Any]) -> None:
        db = TelemetrySessionLocal()
        try:
            row = MonitorNetworkRecord(
                created_at=self._coerce_datetime(normalized["created_at"]),
                category=normalized["category"],
                source=normalized["source"],
                protocol=normalized["protocol"],
                from_entity=normalized["from_entity"],
                to_entity=normalized["to_entity"],
                method=normalized["method"],
                url=normalized["url"],
                host=normalized["host"],
                path=normalized["path"],
                status_code=normalized["status_code"],
                success=normalized["success"],
                request_bytes=normalized["request_bytes"],
                response_bytes=normalized["response_bytes"],
                total_bytes=normalized["total_bytes"],
                duration_ms=normalized["duration_ms"],
                content_type=normalized["content_type"],
                preview=normalized["preview"],
                error=normalized["error"],
                client_source=normalized["client_source"],
                raw_request=normalized["raw_request"],
                raw_response=normalized["raw_response"],
                request_headers_json=json.dumps(normalized["request_headers"], ensure_ascii=False),
                response_headers_json=json.dumps(normalized["response_headers"], ensure_ascii=False),
                metadata_json=json.dumps(normalized["metadata"], ensure_ascii=False),
            )
            db.add(row)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
                return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            except ValueError:
                pass
        return datetime.now()


telemetry_writer = TelemetryWriter()
