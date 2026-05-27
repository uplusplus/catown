# -*- coding: utf-8 -*-
"""Tests for the telemetry serial writer."""
import importlib
import os
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _reload_telemetry_modules():
    from sqlalchemy.orm import clear_mappers

    clear_mappers()

    import models.database as db_mod
    import models.audit as audit_mod
    import services.telemetry_writer as writer_mod

    importlib.reload(db_mod)
    importlib.reload(audit_mod)
    importlib.reload(writer_mod)
    return db_mod, audit_mod, writer_mod


def _network_payload(index: int) -> dict:
    return {
        "created_at": datetime(2026, 5, 27, 12, 0, index % 60, tzinfo=timezone.utc).isoformat(),
        "category": "llm",
        "source": "test",
        "protocol": "https",
        "from_entity": "agent",
        "to_entity": "provider",
        "method": "POST",
        "url": f"https://example.com/{index}",
        "host": "example.com",
        "path": f"/{index}",
        "status_code": 200,
        "success": True,
        "request_bytes": 10 + index,
        "response_bytes": 20 + index,
        "total_bytes": 30 + index,
        "duration_ms": 5 + index,
        "content_type": "application/json",
        "preview": f"preview-{index}",
        "error": "",
        "client_source": "pytest",
        "raw_request": "{}",
        "raw_response": "{}",
        "request_headers": {"x-test": str(index)},
        "response_headers": {"content-type": "application/json"},
        "metadata": {"index": index},
    }


class TestTelemetryWriter:
    def test_persists_audit_records_to_telemetry_db(self, fresh_db):
        db_mod, audit_mod, writer_mod = _reload_telemetry_modules()
        db_mod.init_database()

        writer = writer_mod.TelemetryWriter()
        try:
            llm_id = writer.create_llm_call(
                {
                    "run_id": 501,
                    "stage_id": 7,
                    "agent_name": "tester",
                    "turn_index": 2,
                    "model": "gpt-test",
                    "system_prompt": "system",
                    "messages": "[]",
                }
            )
            writer.finalize_llm_call(
                {
                    "id": llm_id,
                    "response_content": "done",
                    "response_tool_calls": "[]",
                    "duration_ms": 25,
                    "token_input": 11,
                    "token_output": 13,
                }
            )
            tool_id = writer.create_tool_call(
                {
                    "llm_call_id": llm_id,
                    "run_id": 501,
                    "stage_id": 7,
                    "agent_name": "tester",
                    "tool_name": "read_file",
                    "arguments": '{"path":"demo.txt"}',
                    "result_summary": "ok",
                    "result_length": 2,
                    "success": True,
                    "duration_ms": 9,
                }
            )
            event_id = writer.create_event(
                {
                    "run_id": 501,
                    "event_type": "tool_call",
                    "agent_name": "tester",
                    "stage_name": "dev",
                    "summary": "tool ok",
                    "payload": '{"ok":true}',
                }
            )
            writer.enqueue_network_record(_network_payload(1))
            assert writer.flush()

            db = db_mod.TelemetrySessionLocal()
            try:
                llm = db.query(audit_mod.LLMCall).filter(audit_mod.LLMCall.id == llm_id).one()
                tool = db.query(audit_mod.ToolCall).filter(audit_mod.ToolCall.id == tool_id).one()
                event = db.query(audit_mod.Event).filter(audit_mod.Event.id == event_id).one()
                records = db.query(audit_mod.MonitorNetworkRecord).all()
            finally:
                db.close()

            assert llm.response_content == "done"
            assert llm.token_input == 11
            assert llm.token_output == 13
            assert tool.llm_call_id == llm_id
            assert event.summary == "tool ok"
            assert len(records) == 1
            assert records[0].host == "example.com"
        finally:
            writer.stop(drain=True)

    def test_serializes_concurrent_creates_without_lock_failures(self, fresh_db):
        db_mod, audit_mod, writer_mod = _reload_telemetry_modules()
        db_mod.init_database()

        writer = writer_mod.TelemetryWriter()
        created_ids = []
        errors = []
        lock = threading.Lock()

        def worker(index: int) -> None:
            try:
                record_id = writer.create_llm_call(
                    {
                        "run_id": 700 + index,
                        "stage_id": 1,
                        "agent_name": f"agent-{index}",
                        "turn_index": index,
                        "model": "gpt-test",
                        "system_prompt": f"sys-{index}",
                        "messages": "[]",
                    },
                    timeout=10.0,
                )
                with lock:
                    created_ids.append(record_id)
            except Exception as exc:  # pragma: no cover - only used for assertion detail
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            assert writer.flush(timeout=10.0)
            assert not errors
            assert len(created_ids) == 20
            assert len(set(created_ids)) == 20

            db = db_mod.TelemetrySessionLocal()
            try:
                rows = db.query(audit_mod.LLMCall).order_by(audit_mod.LLMCall.id.asc()).all()
            finally:
                db.close()

            assert len(rows) == 20
            assert [row.id for row in rows] == sorted(created_ids)
        finally:
            writer.stop(drain=True)

    def test_flush_waits_for_queued_network_records(self, fresh_db):
        db_mod, audit_mod, writer_mod = _reload_telemetry_modules()
        db_mod.init_database()

        writer = writer_mod.TelemetryWriter()
        try:
            for index in range(5):
                writer.enqueue_network_record(_network_payload(index))

            assert writer.flush(timeout=10.0)

            db = db_mod.TelemetrySessionLocal()
            try:
                rows = db.query(audit_mod.MonitorNetworkRecord).order_by(
                    audit_mod.MonitorNetworkRecord.id.asc()
                ).all()
            finally:
                db.close()

            assert len(rows) == 5
            assert rows[0].request_headers_json == '{"x-test": "0"}'
            assert rows[-1].metadata_json == '{"index": 4}'
        finally:
            writer.stop(drain=True)
