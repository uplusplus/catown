# -*- coding: utf-8 -*-
"""
Vector Memory Service — semantic search over agent memories using ChromaDB.

Provides vector-based long-term memory as a complement to the existing
SQL-based Memory table. Enables semantic (embedding-based) retrieval
instead of just keyword matching.

Design:
- ChromaDB persistent client stored at ``{CATOWN_HOME}/state/chromadb/``
- Collection per agent: ``memories_agent_{id}``
- Metadata filters: agent_id, project_id, memory_type, importance
- Graceful degradation: if ChromaDB is unavailable, all operations
  return empty/no-op (existing SQL search still works)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("catown.vector_memory")

# ---------------------------------------------------------------------------
# ChromaDB client (lazy singleton)
# ---------------------------------------------------------------------------

_client = None
_client_error = None


def _get_chromadb_path() -> str:
    """Return the ChromaDB persistent storage path."""
    catown_home = os.environ.get("CATOWN_HOME", os.path.expanduser("~/.catown"))
    return os.path.join(catown_home, "state", "chromadb")


def _get_client():
    """Get or create the ChromaDB client (lazy initialization)."""
    global _client, _client_error
    if _client is not None:
        return _client
    if _client_error is not None:
        return None
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        db_path = _get_chromadb_path()
        os.makedirs(db_path, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=db_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info("[VectorMemory] ChromaDB initialized at %s", db_path)
        return _client
    except ImportError:
        _client_error = "chromadb not installed"
        logger.info("[VectorMemory] chromadb not installed, vector memory disabled")
        return None
    except Exception as exc:
        _client_error = str(exc)
        logger.warning("[VectorMemory] Failed to initialize ChromaDB: %s", exc)
        return None


def is_available() -> bool:
    """Check if ChromaDB is available."""
    return _get_client() is not None


# ---------------------------------------------------------------------------
# Collection management
# ---------------------------------------------------------------------------

def _collection_name(agent_id: int) -> str:
    """Get the collection name for an agent."""
    return f"memories_agent_{agent_id}"


def _get_or_create_collection(agent_id: int):
    """Get or create a ChromaDB collection for an agent."""
    client = _get_client()
    if client is None:
        return None
    try:
        return client.get_or_create_collection(
            name=_collection_name(agent_id),
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as exc:
        logger.warning("[VectorMemory] Failed to get collection for agent %s: %s", agent_id, exc)
        return None


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def add_memory(
    *,
    agent_id: int,
    content: str,
    memory_type: str = "fact",
    importance: int = 5,
    project_id: int | None = None,
    source: str = "extraction",
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Add a memory to the vector store.

    Returns True if successful, False if ChromaDB is unavailable.
    """
    collection = _get_or_create_collection(agent_id)
    if collection is None:
        return False

    normalized_content = str(content or "").strip()
    if not normalized_content or len(normalized_content) < 10:
        return False

    # Build a unique ID
    memory_id = f"mem_{agent_id}_{int(time.time() * 1000)}_{hash(normalized_content) % 100000:05d}"

    # Build metadata
    meta = {
        "agent_id": agent_id,
        "memory_type": str(memory_type or "fact").strip(),
        "importance": min(max(int(importance or 5), 1), 10),
        "project_id": int(project_id) if project_id is not None else -1,
        "source": str(source or "extraction").strip(),
        "created_at": datetime.now().isoformat(),
        "content_length": len(normalized_content),
    }
    if metadata and isinstance(metadata, dict):
        for key, value in metadata.items():
            if key not in meta and isinstance(value, (str, int, float, bool)):
                meta[key] = value

    try:
        collection.add(
            ids=[memory_id],
            documents=[normalized_content],
            metadatas=[meta],
        )
        logger.info(
            "[VectorMemory] Added memory for agent %s: %s... (%d chars)",
            agent_id, normalized_content[:60], len(normalized_content),
        )
        return True
    except Exception as exc:
        logger.warning("[VectorMemory] Failed to add memory: %s", exc)
        return False


def add_memories_batch(
    *,
    agent_id: int,
    memories: list[dict[str, Any]],
) -> int:
    """Add multiple memories in batch. Returns number of successfully added."""
    collection = _get_or_create_collection(agent_id)
    if collection is None:
        return 0

    ids = []
    documents = []
    metadatas = []

    for i, mem in enumerate(memories):
        content = str(mem.get("content", "")).strip()
        if not content or len(content) < 10:
            continue

        memory_id = f"mem_{agent_id}_{int(time.time() * 1000)}_{i:04d}_{hash(content) % 100000:05d}"
        meta = {
            "agent_id": agent_id,
            "memory_type": str(mem.get("memory_type", "fact")).strip(),
            "importance": min(max(int(mem.get("importance", 5)), 1), 10),
            "project_id": int(mem.get("project_id", -1) or -1),
            "source": str(mem.get("source", "batch")).strip(),
            "created_at": datetime.now().isoformat(),
            "content_length": len(content),
        }
        ids.append(memory_id)
        documents.append(content)
        metadatas.append(meta)

    if not ids:
        return 0

    try:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        logger.info("[VectorMemory] Batch added %d memories for agent %s", len(ids), agent_id)
        return len(ids)
    except Exception as exc:
        logger.warning("[VectorMemory] Batch add failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Search operations
# ---------------------------------------------------------------------------

def search_memories(
    *,
    agent_id: int,
    query: str,
    max_results: int = 5,
    min_importance: int = 1,
    project_id: int | None = None,
    memory_type: str | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over an agent's memories.

    Returns a list of dicts with 'content', 'score', 'metadata' keys.
    """
    collection = _get_or_create_collection(agent_id)
    if collection is None:
        return []

    normalized_query = str(query or "").strip()
    if not normalized_query:
        return []

    # Build where filter
    where_conditions: list[dict[str, Any]] = [
        {"importance": {"gte": min_importance}},
    ]
    if project_id is not None:
        where_conditions.append({"project_id": int(project_id)})
    if memory_type:
        where_conditions.append({"memory_type": str(memory_type).strip()})

    where_filter = None
    if len(where_conditions) == 1:
        where_filter = where_conditions[0]
    elif len(where_conditions) > 1:
        where_filter = {"$and": where_conditions}

    try:
        # Check if collection is empty
        if collection.count() == 0:
            return []

        results = collection.query(
            query_texts=[normalized_query],
            n_results=min(max_results, collection.count()),
            where=where_filter if where_filter else None,
            include=["documents", "metadatas", "distances"],
        )

        memories = []
        if results and results.get("documents") and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i] if results.get("distances") else 1.0
                # ChromaDB cosine distance: 0 = identical, 2 = opposite
                # Convert to similarity score: 1 - (distance / 2)
                score = 1.0 - (distance / 2.0) if distance is not None else 0.0
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                memories.append({
                    "content": doc,
                    "score": round(score, 4),
                    "metadata": meta,
                })

        return memories
    except Exception as exc:
        logger.warning("[VectorMemory] Search failed for agent %s: %s", agent_id, exc)
        return []


# ---------------------------------------------------------------------------
# Delete operations
# ---------------------------------------------------------------------------

def delete_memory(agent_id: int, memory_id: str) -> bool:
    """Delete a specific memory by ID."""
    collection = _get_or_create_collection(agent_id)
    if collection is None:
        return False
    try:
        collection.delete(ids=[memory_id])
        return True
    except Exception as exc:
        logger.warning("[VectorMemory] Delete failed: %s", exc)
        return False


def delete_memories_for_agent(agent_id: int) -> bool:
    """Delete all memories for an agent (use with caution)."""
    client = _get_client()
    if client is None:
        return False
    try:
        client.delete_collection(_collection_name(agent_id))
        logger.info("[VectorMemory] Deleted all memories for agent %s", agent_id)
        return True
    except Exception as exc:
        logger.warning("[VectorMemory] Bulk delete failed for agent %s: %s", agent_id, exc)
        return False


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_memory_stats(agent_id: int) -> dict[str, Any]:
    """Get statistics about an agent's vector memory."""
    collection = _get_or_create_collection(agent_id)
    if collection is None:
        return {"available": False, "error": _client_error or "ChromaDB not initialized"}
    try:
        count = collection.count()
        return {
            "available": True,
            "agent_id": agent_id,
            "collection": _collection_name(agent_id),
            "total_memories": count,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def get_all_stats() -> dict[str, Any]:
    """Get stats for all agents' vector memories."""
    client = _get_client()
    if client is None:
        return {"available": False, "error": _client_error or "ChromaDB not initialized"}
    try:
        collections = client.list_collections()
        stats = {}
        for col in collections:
            name = col.name if hasattr(col, "name") else str(col)
            if name.startswith("memories_agent_"):
                agent_id_str = name.replace("memories_agent_", "")
                try:
                    agent_id = int(agent_id_str)
                    stats[agent_id] = get_memory_stats(agent_id)
                except ValueError:
                    pass
        return {
            "available": True,
            "collections": len(stats),
            "agents": stats,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}
