# -*- coding: utf-8 -*-
"""Single-writer telemetry persistence for high-frequency SQLite writes."""
from __future__ import annotations

import json
import logging
import queue
import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config import settings
from models.audit import Event, LLMCall, MonitorNetworkBlob, MonitorNetworkRecord, ToolCall
from models.database import NetworkAuditSessionLocal, TelemetrySessionLocal, network_audit_engine

logger = logging.getLogger("catown.telemetry")


@dataclass
class _Request:
    kind: str
    payload: dict[str, Any]
    done: threading.Event | None = None
    result: Any = None
    error: Exception | None = None


class TelemetryWriter:
    """Serialize telemetry writes through one background writer thread."""

    def __init__(self) -> None:
        self._queue: queue.Queue[_Request | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._batch_interval_seconds = 0.2
        self._max_batch_size = 50

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
        req = _Request(kind="__flush__", payload={}, done=threading.Event())
        self._queue.put(req)
        return bool(req.done and req.done.wait(timeout))

    def enqueue_network_record(self, payload: dict[str, Any]) -> None:
        self.start()
        self._queue.put(_Request(kind="network_record", payload=dict(payload)))

    def create_network_record(self, payload: dict[str, Any], *, timeout: float = 5.0) -> int:
        return int(self._request("create_network_record", payload, timeout=timeout))

    def create_llm_call(self, payload: dict[str, Any], *, timeout: float = 5.0) -> int:
        return int(self._request("create_llm_call", payload, timeout=timeout))

    def finalize_llm_call(self, payload: dict[str, Any], *, timeout: float = 5.0) -> None:
        self._request("finalize_llm_call", payload, timeout=timeout)

    def create_tool_call(self, payload: dict[str, Any], *, timeout: float = 5.0) -> int:
        return int(self._request("create_tool_call", payload, timeout=timeout))

    def create_event(self, payload: dict[str, Any], *, timeout: float = 5.0) -> int:
        return int(self._request("create_event", payload, timeout=timeout))

    def _request(self, kind: str, payload: dict[str, Any], *, timeout: float) -> Any:
        self.start()
        req = _Request(kind=kind, payload=dict(payload), done=threading.Event())
        self._queue.put(req)
        if not req.done.wait(timeout):
            raise TimeoutError(f"Telemetry writer timed out waiting for {kind}")
        if req.error is not None:
            raise req.error
        return req.result

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                first = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if first is None:
                break

            batch = [first]
            batch_started = time.monotonic()
            while len(batch) < self._max_batch_size:
                remaining = self._batch_interval_seconds - (time.monotonic() - batch_started)
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is None:
                    self._queue.put(None)
                    break
                batch.append(item)

            self._process_batch(batch)
            for _ in batch:
                self._queue.task_done()

    def _process_batch(self, batch: list[_Request]) -> None:
        network_requests = [
            req for req in batch if req.kind in {"network_record", "create_network_record"}
        ]
        telemetry_requests = [
            req for req in batch if req.kind not in {"network_record", "create_network_record"}
        ]

        if network_requests:
            self._process_network_batch(network_requests)
        if telemetry_requests:
            self._process_telemetry_batch(telemetry_requests)

    def _process_telemetry_batch(self, batch: list[_Request]) -> None:
        db = TelemetrySessionLocal()
        try:
            for req in batch:
                if req.kind == "__flush__":
                    continue
                req.result = self._dispatch(db, req.kind, req.payload)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("[TelemetryWriter] Failed to persist batch: %s", exc)
            for req in batch:
                if req.kind != "__flush__":
                    req.error = exc
        finally:
            db.close()
            for req in batch:
                if req.done is not None:
                    req.done.set()

    def _process_network_batch(self, batch: list[_Request]) -> None:
        db = NetworkAuditSessionLocal()
        try:
            MonitorNetworkBlob.__table__.create(bind=network_audit_engine, checkfirst=True)
            MonitorNetworkRecord.__table__.create(bind=network_audit_engine, checkfirst=True)
            for req in batch:
                req.result = self._dispatch(db, req.kind, req.payload)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("[TelemetryWriter] Failed to persist network audit batch: %s", exc)
            for req in batch:
                req.error = exc
        finally:
            db.close()
            for req in batch:
                if req.done is not None:
                    req.done.set()

    def _dispatch(self, db, kind: str, payload: dict[str, Any]) -> Any:
        if kind == "network_record":
            row = self._build_network_record(db, payload)
            db.add(row)
            return None
        if kind == "create_network_record":
            row = self._build_network_record(db, payload)
            db.add(row)
            db.flush()
            return int(row.id)
        if kind == "create_llm_call":
            row = LLMCall(**self._filtered_model_payload(LLMCall, payload))
            db.add(row)
            db.flush()
            return int(row.id)
        if kind == "finalize_llm_call":
            row = db.query(LLMCall).filter(LLMCall.id == int(payload["id"])).first()
            if row is None:
                raise ValueError(f"LLMCall {payload['id']} not found")
            for key, value in payload.items():
                if key != "id":
                    setattr(row, key, value)
            db.add(row)
            return None
        if kind == "create_tool_call":
            row = ToolCall(**self._filtered_model_payload(ToolCall, payload))
            db.add(row)
            db.flush()
            return int(row.id)
        if kind == "create_event":
            row = Event(**self._filtered_model_payload(Event, payload))
            db.add(row)
            db.flush()
            return int(row.id)
        raise ValueError(f"Unknown telemetry writer request kind: {kind}")

    def _build_network_record(self, db, normalized: dict[str, Any]) -> MonitorNetworkRecord:
        raw_request, raw_request_blob_id = self._extract_network_payload_blob(
            db,
            normalized.get("raw_request", ""),
            kind="request",
        )
        raw_response, raw_response_blob_id = self._extract_network_payload_blob(
            db,
            normalized.get("raw_response", ""),
            kind="response",
        )
        return MonitorNetworkRecord(
            created_at=self._coerce_datetime(normalized["created_at"]),
            task_run_id=normalized.get("task_run_id"),
            chatroom_id=normalized.get("chatroom_id"),
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
            raw_request=raw_request,
            raw_response=raw_response,
            raw_request_blob_id=raw_request_blob_id,
            raw_response_blob_id=raw_response_blob_id,
            request_headers_json=json.dumps(normalized["request_headers"], ensure_ascii=False),
            response_headers_json=json.dumps(normalized["response_headers"], ensure_ascii=False),
            metadata_json=json.dumps(normalized["metadata"], ensure_ascii=False),
        )

    def _extract_network_payload_blob(self, db, value: Any, *, kind: str) -> tuple[str, int | None]:
        text = str(value or "")
        if not text:
            return "", None

        data = text.encode("utf-8")
        if len(data) <= settings.MONITOR_NETWORK_RAW_INLINE_MAX_BYTES:
            return text, None

        digest = hashlib.sha256(data).hexdigest()
        relative_path = Path(digest[:2]) / f"{digest}.txt"
        absolute_path = settings.NETWORK_AUDIT_PAYLOADS_DIR / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        if not absolute_path.exists():
            absolute_path.write_bytes(data)
        blob = MonitorNetworkBlob(
            kind=kind,
            content_sha256=digest,
            content_bytes=len(data),
            storage_path=relative_path.as_posix(),
            content_type="text/plain; charset=utf-8",
        )
        db.add(blob)
        db.flush()
        return "", int(blob.id)

    @staticmethod
    def _filtered_model_payload(model_cls: type[Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Drop unknown fields so telemetry schema drift does not break runtime paths."""

        table = getattr(model_cls, "__table__", None)
        if table is None:
            return dict(payload)
        allowed = {column.key for column in table.columns}
        return {key: value for key, value in payload.items() if key in allowed}

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
