"""Hybrid in-memory + persisted network activity store for the standalone monitor."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from config import settings

logger = logging.getLogger("catown.monitor.network")


class MonitorNetworkBuffer:
    def __init__(self, max_entries: int | None = None) -> None:
        self._entries: deque[dict[str, Any]] = deque(
            maxlen=max_entries or settings.MONITOR_NETWORK_MEMORY_MAX_ENTRIES
        )
        self._lock = threading.Lock()
        self._next_id = 1
        self._last_cleanup_monotonic = 0.0
        self._ready_databases: set[str] = set()
        self._warned_databases: set[str] = set()

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(event)
        persisted = self._persist(normalized) or dict(normalized)
        self._append_memory(persisted)
        try:
            self._maybe_cleanup()
        except Exception as exc:
            self._warn_persistence_fallback(exc)
        return dict(persisted)

    def list_entries(
        self,
        *,
        limit: int = 300,
        after_id: int | None = None,
        category: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        persisted = self._list_persisted(
            limit=limit,
            after_id=after_id,
            category=category,
            query=query,
        )
        if persisted:
            return persisted
        return self._list_memory(limit=limit, after_id=after_id, category=category, query=query)

    def latest_id(self) -> int:
        persisted = self._latest_persisted_id()
        if persisted > 0:
            return persisted
        with self._lock:
            if not self._entries:
                return 0
            return int(self._entries[-1]["id"])

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._next_id = 1
        try:
            if not self._ensure_persisted_table():
                return
            from models.audit import MonitorNetworkRecord
            from models.database import SessionLocal

            db = SessionLocal()
            try:
                db.query(MonitorNetworkRecord).delete()
                db.commit()
            finally:
                db.close()
        except Exception:
            # Tests should still be able to clear the in-memory snapshot even if DB teardown failed.
            return

    def _normalize(self, event: dict[str, Any]) -> dict[str, Any]:
        timestamp = self._system_timestamp()
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        from_entity = str(event.get("from_entity") or "unknown")
        to_entity = str(event.get("to_entity") or "unknown")
        request_direction = str(
            event.get("request_direction")
            or metadata.get("request_direction")
            or f"{from_entity} -> {to_entity}"
        )
        response_direction = str(
            event.get("response_direction")
            or metadata.get("response_direction")
            or f"{to_entity} -> {from_entity}"
        )
        flow_id = str(event.get("flow_id") or metadata.get("flow_id") or "")
        flow_kind = str(event.get("flow_kind") or metadata.get("flow_kind") or "")
        flow_seq = self._int_or_none(event.get("flow_seq"))
        if flow_seq is None:
            flow_seq = self._int_or_none(metadata.get("flow_seq"))
        aggregated = self._bool_or_none(event.get("aggregated"))
        if aggregated is None:
            aggregated = self._bool_or_none(metadata.get("aggregated"))
        if aggregated is None:
            aggregated = True
        normalized = {
            "id": self._int_or_none(event.get("id")) or 0,
            "created_at": self._coerce_datetime_string(event.get("created_at") or timestamp),
            "category": str(event.get("category") or "unknown"),
            "source": str(event.get("source") or "unknown"),
            "protocol": str(event.get("protocol") or "unknown"),
            "from_entity": from_entity,
            "to_entity": to_entity,
            "request_direction": request_direction,
            "response_direction": response_direction,
            "flow_id": flow_id,
            "flow_kind": flow_kind,
            "flow_seq": flow_seq,
            "aggregated": aggregated,
            "method": str(event.get("method") or ""),
            "url": str(event.get("url") or ""),
            "host": str(event.get("host") or ""),
            "path": str(event.get("path") or ""),
            "status_code": self._int_or_none(event.get("status_code")),
            "success": self._bool_or_none(event.get("success")),
            "request_bytes": self._int_or_zero(event.get("request_bytes")),
            "response_bytes": self._int_or_zero(event.get("response_bytes")),
            "total_bytes": self._int_or_zero(event.get("total_bytes")),
            "duration_ms": self._int_or_zero(event.get("duration_ms")),
            "content_type": str(event.get("content_type") or ""),
            "preview": str(event.get("preview") or ""),
            "error": str(event.get("error") or ""),
            "client_source": str(event.get("client_source") or ""),
            "raw_request": str(event.get("raw_request") or ""),
            "raw_response": str(event.get("raw_response") or ""),
            "request_headers": event.get("request_headers") if isinstance(event.get("request_headers"), dict) else {},
            "response_headers": event.get("response_headers") if isinstance(event.get("response_headers"), dict) else {},
            "metadata": {
                **metadata,
                "request_direction": request_direction,
                "response_direction": response_direction,
                "flow_id": flow_id,
                "flow_kind": flow_kind,
                "flow_seq": flow_seq,
                "aggregated": aggregated,
            },
        }

        if normalized["total_bytes"] <= 0:
            normalized["total_bytes"] = normalized["request_bytes"] + normalized["response_bytes"]

        return normalized

    def _append_memory(self, entry: dict[str, Any]) -> None:
        with self._lock:
            if int(entry["id"]) <= 0:
                entry = dict(entry)
                entry["id"] = self._next_id
                self._next_id += 1
            else:
                self._next_id = max(self._next_id, int(entry["id"]) + 1)
            self._entries.append(dict(entry))

    def _list_memory(
        self,
        *,
        limit: int,
        after_id: int | None,
        category: str | None,
        query: str | None,
    ) -> list[dict[str, Any]]:
        normalized_category = (category or "all").strip().lower()
        query_text = (query or "").strip().lower()

        with self._lock:
            snapshot = [dict(entry) for entry in self._entries]

        if after_id is not None:
            snapshot = [entry for entry in snapshot if int(entry["id"]) > after_id]

        if normalized_category and normalized_category != "all":
            snapshot = [entry for entry in snapshot if str(entry.get("category", "")).lower() == normalized_category]

        if query_text:
            snapshot = [entry for entry in snapshot if self._matches_query(entry, query_text)]

        snapshot.sort(key=lambda entry: int(entry["id"]), reverse=True)
        if limit > 0:
            snapshot = snapshot[:limit]
        return snapshot

    def _persist(self, normalized: dict[str, Any]) -> dict[str, Any] | None:
        if not self._ensure_persisted_table():
            return None

        persisted = self._persist_once(normalized)
        if persisted is not None:
            return persisted

        if not self._ensure_persisted_table(force=True):
            return None
        return self._persist_once(normalized)

    def _persist_once(self, normalized: dict[str, Any]) -> dict[str, Any] | None:
        from models.audit import MonitorNetworkRecord
        from models.database import SessionLocal

        db = SessionLocal()
        try:
            created_at = self._coerce_datetime(normalized["created_at"])
            row = MonitorNetworkRecord(
                created_at=created_at,
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
            db.refresh(row)
            persisted = dict(normalized)
            persisted["id"] = int(row.id)
            persisted["created_at"] = self._format_datetime(row.created_at) if row.created_at else normalized["created_at"]
            return persisted
        except Exception as exc:
            db.rollback()
            self._warn_persistence_fallback(exc)
            return None
        finally:
            db.close()

    def _list_persisted(
        self,
        *,
        limit: int,
        after_id: int | None,
        category: str | None,
        query: str | None,
    ) -> list[dict[str, Any]]:
        if not self._ensure_persisted_table():
            return []
        try:
            from models.audit import MonitorNetworkRecord
            from models.database import SessionLocal

            db = SessionLocal()
            try:
                rows = db.query(MonitorNetworkRecord)
                if after_id is not None:
                    rows = rows.filter(MonitorNetworkRecord.id > after_id)

                normalized_category = (category or "all").strip().lower()
                if normalized_category and normalized_category != "all":
                    rows = rows.filter(MonitorNetworkRecord.category == normalized_category)

                query_text = (query or "").strip().lower()
                rows = rows.order_by(MonitorNetworkRecord.id.desc())
                if limit > 0:
                    rows = rows.limit(limit)

                result = [self._row_to_entry(row) for row in rows.all()]
                if query_text:
                    result = [entry for entry in result if self._matches_query(entry, query_text)]
                return result[:limit] if limit > 0 else result
            finally:
                db.close()
        except Exception:
            return []

    def _latest_persisted_id(self) -> int:
        if not self._ensure_persisted_table():
            return 0
        try:
            from models.audit import MonitorNetworkRecord
            from models.database import SessionLocal
            from sqlalchemy import func

            db = SessionLocal()
            try:
                value = db.query(func.max(MonitorNetworkRecord.id)).scalar()
                return int(value or 0)
            finally:
                db.close()
        except Exception:
            return 0

    def _maybe_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup_monotonic < settings.MONITOR_NETWORK_CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup_monotonic = now
        self._cleanup_persisted()

    def _cleanup_persisted(self) -> None:
        if not self._ensure_persisted_table():
            return
        from models.audit import MonitorNetworkRecord
        from models.database import SessionLocal

        db = SessionLocal()
        try:
            cutoff = datetime.now() - timedelta(hours=settings.MONITOR_NETWORK_RETENTION_HOURS)
            db.query(MonitorNetworkRecord).filter(MonitorNetworkRecord.created_at < cutoff).delete()
            db.commit()

            while True:
                total = db.query(MonitorNetworkRecord).count()
                overflow = total - settings.MONITOR_NETWORK_MAX_PERSISTED
                if overflow <= 0:
                    break
                stale_ids = [
                    row_id
                    for (row_id,) in (
                        db.query(MonitorNetworkRecord.id)
                        .order_by(MonitorNetworkRecord.created_at.asc(), MonitorNetworkRecord.id.asc())
                        .limit(overflow)
                        .all()
                    )
                ]
                if not stale_ids:
                    break
                db.query(MonitorNetworkRecord).filter(MonitorNetworkRecord.id.in_(stale_ids)).delete(
                    synchronize_session=False
                )
                db.commit()
        except Exception as exc:
            db.rollback()
            self._warn_persistence_fallback(exc)
        finally:
            db.close()

    def _ensure_persisted_table(self, *, force: bool = False) -> bool:
        try:
            from models.audit import MonitorNetworkRecord
            from models.database import engine
        except Exception as exc:
            self._warn_persistence_fallback(exc)
            return False

        database_key = self._database_key()
        with self._lock:
            if not force and database_key in self._ready_databases:
                return True

        try:
            MonitorNetworkRecord.__table__.create(bind=engine, checkfirst=True)
        except Exception as exc:
            self._warn_persistence_fallback(exc)
            return False

        with self._lock:
            self._ready_databases.add(database_key)
            self._warned_databases.discard(database_key)
        return True

    def _database_key(self) -> str:
        try:
            from models.database import engine

            return str(engine.url)
        except Exception:
            return "<unknown>"

    def _warn_persistence_fallback(self, exc: Exception) -> None:
        database_key = self._database_key()
        with self._lock:
            if database_key in self._warned_databases:
                return
            self._warned_databases.add(database_key)
        logger.warning(
            "Monitor network persistence unavailable for %s; using in-memory fallback: %s",
            database_key,
            exc,
        )

    @staticmethod
    def _row_to_entry(row: Any) -> dict[str, Any]:
        metadata = MonitorNetworkBuffer._json_dict(row.metadata_json)
        from_entity = row.from_entity
        to_entity = row.to_entity
        return {
            "id": int(row.id),
            "created_at": MonitorNetworkBuffer._format_datetime(row.created_at) if row.created_at else None,
            "category": row.category,
            "source": row.source,
            "protocol": row.protocol,
            "from_entity": from_entity,
            "to_entity": to_entity,
            "request_direction": str(metadata.get("request_direction") or f"{from_entity} -> {to_entity}"),
            "response_direction": str(metadata.get("response_direction") or f"{to_entity} -> {from_entity}"),
            "flow_id": str(metadata.get("flow_id") or ""),
            "flow_kind": str(metadata.get("flow_kind") or ""),
            "flow_seq": MonitorNetworkBuffer._int_or_none(metadata.get("flow_seq")),
            "aggregated": metadata.get("aggregated") if isinstance(metadata.get("aggregated"), bool) else True,
            "method": row.method,
            "url": row.url,
            "host": row.host,
            "path": row.path,
            "status_code": row.status_code,
            "success": row.success,
            "request_bytes": row.request_bytes or 0,
            "response_bytes": row.response_bytes or 0,
            "total_bytes": row.total_bytes or 0,
            "duration_ms": row.duration_ms or 0,
            "content_type": row.content_type or "",
            "preview": row.preview or "",
            "error": row.error or "",
            "client_source": row.client_source or "",
            "raw_request": row.raw_request or "",
            "raw_response": row.raw_response or "",
            "request_headers": MonitorNetworkBuffer._json_dict(row.request_headers_json),
            "response_headers": MonitorNetworkBuffer._json_dict(row.response_headers_json),
            "metadata": metadata,
        }

    @staticmethod
    def _json_dict(value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _matches_query(entry: dict[str, Any], query_text: str) -> bool:
        return (
            query_text in str(entry.get("url", "")).lower()
            or query_text in str(entry.get("path", "")).lower()
            or query_text in str(entry.get("from_entity", "")).lower()
            or query_text in str(entry.get("to_entity", "")).lower()
            or query_text in str(entry.get("preview", "")).lower()
            or query_text in str(entry.get("error", "")).lower()
            or query_text in str(entry.get("raw_request", "")).lower()
            or query_text in str(entry.get("raw_response", "")).lower()
        )

    @staticmethod
    def _system_timestamp() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.astimezone().isoformat(timespec="milliseconds") if value.tzinfo else value.isoformat(timespec="milliseconds")

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

    @classmethod
    def _coerce_datetime_string(cls, value: Any) -> str:
        return cls._format_datetime(cls._coerce_datetime(value))

    @staticmethod
    def _int_or_zero(value: Any) -> int:
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bool_or_none(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
        return bool(value)


monitor_network_buffer = MonitorNetworkBuffer()
