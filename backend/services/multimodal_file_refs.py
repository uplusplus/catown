# -*- coding: utf-8 -*-
"""Persistent references for server-cached multimodal files."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from models.database import CachedMultimodalFile
from services.multimodal_log_redaction import register_multimodal_data_uri_reference


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def upsert_cached_multimodal_file(
    db: Session,
    *,
    project: Any | None,
    chatroom_id: int | None,
    relative_path: str,
    file_name: str,
    mime_type: str | None,
    file_size: int,
    sha256: str | None,
    file_id: str | None = None,
    source: str = "chat_upload",
    message_id: int | None = None,
) -> CachedMultimodalFile:
    """Create or refresh the DB row that makes a logged file_id recoverable."""

    normalized_path = str(relative_path or "").replace("\\", "/").strip()
    project_id = getattr(project, "id", None)
    workspace_path = str(getattr(project, "workspace_path", "") or "") or None
    now = datetime.now()
    normalized_file_id = str(file_id or "").strip()

    existing = None
    if normalized_file_id:
        existing = (
            db.query(CachedMultimodalFile)
            .filter(CachedMultimodalFile.file_id == normalized_file_id)
            .first()
        )
    if existing is None:
        query = db.query(CachedMultimodalFile).filter(
            CachedMultimodalFile.project_id == project_id,
            CachedMultimodalFile.file_path == normalized_path,
        )
        existing = query.first()
    if existing is None and sha256:
        existing = (
            db.query(CachedMultimodalFile)
            .filter(
                CachedMultimodalFile.project_id == project_id,
                CachedMultimodalFile.sha256 == sha256,
                CachedMultimodalFile.file_path == normalized_path,
            )
            .first()
        )

    record_kwargs = {
        "project_id": project_id,
        "chatroom_id": chatroom_id,
        "workspace_path": workspace_path,
        "file_path": normalized_path,
        "file_name": file_name,
        "mime_type": (mime_type or None),
        "file_size": int(file_size or 0),
        "sha256": sha256,
        "source": source,
        "message_id": message_id,
        "created_at": now,
        "last_seen_at": now,
    }
    if normalized_file_id:
        record_kwargs["file_id"] = normalized_file_id
    record = existing or CachedMultimodalFile(**record_kwargs)
    if existing is None:
        db.add(record)

    record.chatroom_id = chatroom_id if chatroom_id is not None else record.chatroom_id
    record.workspace_path = workspace_path or record.workspace_path
    record.file_name = file_name or record.file_name
    record.mime_type = (mime_type or None) or record.mime_type
    record.file_size = int(file_size or record.file_size or 0)
    record.sha256 = sha256 or record.sha256
    record.source = source or record.source
    record.last_seen_at = now
    if message_id is not None:
        record.message_id = message_id

    db.flush()
    return record


def link_cached_multimodal_file_to_message(
    db: Session,
    *,
    file_id: str | None,
    message_id: int | None,
    chatroom_id: int | None = None,
) -> bool:
    """Attach an existing cached file reference to the saved chat message."""

    normalized_file_id = str(file_id or "").strip()
    if not normalized_file_id or message_id is None:
        return False
    record: Optional[CachedMultimodalFile] = (
        db.query(CachedMultimodalFile)
        .filter(CachedMultimodalFile.file_id == normalized_file_id)
        .first()
    )
    if record is None:
        return False
    changed = False
    if record.message_id != message_id:
        record.message_id = message_id
        changed = True
    if chatroom_id is not None and record.chatroom_id != chatroom_id:
        record.chatroom_id = chatroom_id
        changed = True
    record.last_seen_at = datetime.now()
    return changed


def register_data_uri_for_cached_file(
    *,
    data_uri: str,
    file_id: str | None,
    mime_type: str | None,
    file_name: str | None,
    file_size: int | None,
    sha256: str | None,
) -> None:
    """Make a just-built inline LLM data URI recoverable in later log copies."""

    if not file_id:
        return
    register_multimodal_data_uri_reference(
        data_uri,
        file_id=file_id,
        mime_type=mime_type,
        file_name=file_name,
        file_size=file_size,
        sha256=sha256,
    )
