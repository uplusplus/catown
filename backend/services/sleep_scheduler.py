# -*- coding: utf-8 -*-
"""
Sleep Scheduler — background memory consolidation during agent idle time.

Runs periodically (every 5 minutes) and checks each agent's sleep conditions:
1. Is the agent idle for ``idle_threshold_minutes``?
2. Is the current time within the preferred sleep window (optional)?

When conditions are met, performs memory consolidation:
- Summarize and compress short-term memory into project memory
- Deduplicate and clean up old project memory entries
- Generate vector embeddings for unembedded long-term memories
- Prune memories beyond ``max_retain_days``

Configuration per agent in ``agents.json``:
```json
"sleep": {
    "enabled": true,
    "idle_threshold_minutes": 30,
    "preferred_window_start": "23:00",
    "preferred_window_end": "07:00",
    "max_retain_days": 30,
    "long_term_max_tokens": 100000
}
```
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("catown.sleep_scheduler")

# How often the scheduler checks for agents to consolidate (seconds)
_CHECK_INTERVAL_SECONDS = 300  # 5 minutes

# Minimum time between consolidations for the same agent (seconds)
_MIN_CONSOLIDATION_INTERVAL_SECONDS = 3600  # 1 hour

# Background task reference (prevents GC)
_scheduler_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# Agent sleep config loading
# ---------------------------------------------------------------------------

def _load_agent_sleep_configs() -> Dict[str, Dict[str, Any]]:
    """Load sleep configs for all agents from agents.json."""
    try:
        from config import settings
        config_file = settings.AGENT_CONFIG_FILE
        if not os.path.exists(config_file):
            return {}
        with open(config_file, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        configs = {}
        for agent_type, agent_data in data.get("agents", {}).items():
            sleep_cfg = agent_data.get("sleep", {})
            if sleep_cfg.get("enabled", True):
                configs[agent_type] = sleep_cfg
        return configs
    except Exception as exc:
        logger.warning("[SleepScheduler] Failed to load agent configs: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Idle detection
# ---------------------------------------------------------------------------

def _get_agent_last_activity(agent_type: str) -> Optional[datetime]:
    """Get the timestamp of the agent's most recent activity."""
    try:
        from models.database import get_db, Message, Agent, TaskRun

        db = next(get_db())
        try:
            # Find agent by type
            agent = db.query(Agent).filter(Agent.agent_type == agent_type).first()
            if agent is None:
                return None

            # Check recent messages
            last_msg = (
                db.query(Message)
                .filter(Message.agent_id == agent.id)
                .order_by(Message.created_at.desc())
                .first()
            )
            last_msg_time = last_msg.created_at if last_msg else None

            # Check recent task runs
            last_run = (
                db.query(TaskRun)
                .filter(TaskRun.agent_name == agent_type)
                .order_by(TaskRun.created_at.desc())
                .first()
            )
            last_run_time = last_run.created_at if last_run else None

            # Return the most recent activity
            times = [t for t in [last_msg_time, last_run_time] if t is not None]
            return max(times) if times else None
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[SleepScheduler] Failed to get last activity for %s: %s", agent_type, exc)
        return None


def _is_agent_idle(agent_type: str, idle_threshold_minutes: int) -> bool:
    """Check if an agent has been idle for longer than the threshold."""
    last_activity = _get_agent_last_activity(agent_type)
    if last_activity is None:
        # No activity recorded → consider idle
        return True
    threshold = timedelta(minutes=idle_threshold_minutes)
    return datetime.now() - last_activity > threshold


# ---------------------------------------------------------------------------
# Sleep window check
# ---------------------------------------------------------------------------

def _is_in_sleep_window(start_str: str, end_str: str) -> bool:
    """Check if the current time is within the preferred sleep window.

    Handles overnight windows (e.g., "23:00" to "07:00").
    """
    try:
        now = datetime.now()
        now_minutes = now.hour * 60 + now.minute

        start_parts = start_str.strip().split(":")
        end_parts = end_str.strip().split(":")
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])

        if start_minutes <= end_minutes:
            # Same-day window (e.g., 09:00 to 17:00)
            return start_minutes <= now_minutes <= end_minutes
        else:
            # Overnight window (e.g., 23:00 to 07:00)
            return now_minutes >= start_minutes or now_minutes <= end_minutes
    except Exception:
        # If parsing fails, don't restrict by window
        return True


def _should_consolidate(agent_type: str, sleep_cfg: Dict[str, Any]) -> bool:
    """Determine if an agent should undergo sleep consolidation."""
    idle_threshold = sleep_cfg.get("idle_threshold_minutes", 30)
    if not _is_agent_idle(agent_type, idle_threshold):
        return False

    # Check sleep window (if configured)
    window_start = sleep_cfg.get("preferred_window_start", "")
    window_end = sleep_cfg.get("preferred_window_end", "")
    if window_start and window_end:
        if not _is_in_sleep_window(window_start, window_end):
            return False

    return True


# ---------------------------------------------------------------------------
# Memory consolidation
# ---------------------------------------------------------------------------

async def _consolidate_agent_memories(
    agent_type: str,
    sleep_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Perform memory consolidation for one agent.

    Steps:
    1. Summarize short-term memories → project memory
    2. Deduplicate/compress project memory
    3. Generate vector embeddings for unembedded memories
    4. Prune old memories beyond max_retain_days
    """
    result = {
        "agent": agent_type,
        "short_term_archived": 0,
        "project_compressed": False,
        "vector_synced": 0,
        "pruned_count": 0,
    }

    try:
        # --- Step 1: Archive short-term memory to project memory ---
        result["short_term_archived"] = await _archive_short_term(agent_type)

        # --- Step 2: Compress project memory if too large ---
        max_tokens = sleep_cfg.get("long_term_max_tokens", 100000)
        result["project_compressed"] = await _compress_project_memory(agent_type, max_tokens)

        # --- Step 3: Sync long-term memories to vector store ---
        result["vector_synced"] = await _sync_vector_store(agent_type)

        # --- Step 4: Prune old memories ---
        max_retain_days = sleep_cfg.get("max_retain_days", 30)
        result["pruned_count"] = await _prune_old_memories(agent_type, max_retain_days)

        logger.info(
            "[SleepScheduler] Consolidated %s: archived=%d, compressed=%s, vector_synced=%d, pruned=%d",
            agent_type,
            result["short_term_archived"],
            result["project_compressed"],
            result["vector_synced"],
            result["pruned_count"],
        )
    except Exception as exc:
        logger.error("[SleepScheduler] Consolidation failed for %s: %s", agent_type, exc)
        result["error"] = str(exc)

    return result


async def _archive_short_term(agent_type: str) -> int:
    """Archive active short-term memories to project memory."""
    try:
        from services.short_term_memory import get_short_term_store, archive_to_project_memory
        from models.database import get_db, Agent, Project

        store = get_short_term_store()
        archived = 0

        for stage_id in list(store.active_stages):
            mem = store.get(stage_id)
            if mem is None or mem.turn_count == 0:
                continue

            # Find workspace for this agent
            db = next(get_db())
            try:
                agent = db.query(Agent).filter(Agent.agent_type == agent_type).first()
                if agent is None:
                    continue

                # Find any project with a workspace
                project = db.query(Project).filter(Project.workspace_path.isnot(None)).first()
                if project is None:
                    continue

                workspace = project.workspace_path
            finally:
                db.close()

            if workspace:
                try:
                    archive_to_project_memory(workspace, mem)
                    store.remove(stage_id)
                    archived += 1
                except Exception as exc:
                    logger.debug("[SleepScheduler] Archive failed for stage %s: %s", stage_id, exc)

        return archived
    except Exception as exc:
        logger.debug("[SleepScheduler] Short-term archive failed: %s", exc)
        return 0


async def _compress_project_memory(agent_type: str, max_tokens: int) -> bool:
    """Compress project memory files if they exceed the token limit.

    Uses a simple heuristic: if a category file exceeds half the limit,
    keep only the most recent entries that fit within the limit.
    """
    try:
        from models.database import get_db, Project

        db = next(get_db())
        try:
            project = db.query(Project).filter(Project.workspace_path.isnot(None)).first()
            if project is None:
                return False
            workspace = project.workspace_path
        finally:
            db.close()

        if not workspace:
            return False

        from services.project_memory import _memory_dir, CATEGORY_FILES, _MAX_FILE_CHARS

        mem_dir = _memory_dir(workspace)
        if not mem_dir.exists():
            return False

        compressed = False
        # Rough char-to-token ratio: ~4 chars per token
        max_chars = max_tokens * 4

        for cat, fname in CATEGORY_FILES.items():
            fpath = mem_dir / fname
            if not fpath.exists():
                continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                if len(content) <= max_chars // len(CATEGORY_FILES):
                    continue

                # Keep the last N sections that fit within the limit
                sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)
                sections = [s.strip() for s in sections if s.strip()]

                target_chars = max_chars // len(CATEGORY_FILES)
                kept = []
                current_size = 0
                for section in reversed(sections):
                    if current_size + len(section) > target_chars:
                        break
                    kept.append(section)
                    current_size += len(section)

                if len(kept) < len(sections):
                    kept.reverse()
                    new_content = "\n\n".join(kept) + "\n"
                    fpath.write_text(new_content, encoding="utf-8")
                    compressed = True
                    logger.info(
                        "[SleepScheduler] Compressed %s: %d → %d entries",
                        fname, len(sections), len(kept),
                    )
            except Exception as exc:
                logger.debug("[SleepScheduler] Compress %s failed: %s", fname, exc)

        return compressed
    except Exception as exc:
        logger.debug("[SleepScheduler] Project memory compression failed: %s", exc)
        return False


async def _sync_vector_store(agent_type: str) -> int:
    """Sync long-term memories that aren't yet in the vector store.

    Finds memories in the SQL database that don't have corresponding
    vector entries and adds them.
    """
    try:
        from services.vector_memory import add_memories_batch, is_available, search_memories

        if not is_available():
            return 0

        from models.database import get_db, Agent, Memory

        db = next(get_db())
        try:
            agent = db.query(Agent).filter(Agent.agent_type == agent_type).first()
            if agent is None:
                return 0

            # Get recent long-term memories
            memories = (
                db.query(Memory)
                .filter(Memory.agent_id == agent.id)
                .filter(Memory.memory_type == "long_term")
                .order_by(Memory.created_at.desc())
                .limit(100)
                .all()
            )

            if not memories:
                return 0

            # Batch add to vector store (ChromaDB deduplicates by content hash)
            mem_dicts = [
                {
                    "content": m.content,
                    "memory_type": m.memory_type,
                    "importance": m.importance or 5,
                    "source": "sleep_sync",
                }
                for m in memories
                if m.content and len(m.content.strip()) >= 10
            ]

            if mem_dicts:
                return add_memories_batch(agent_id=agent.id, memories=mem_dicts)

            return 0
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[SleepScheduler] Vector sync failed: %s", exc)
        return 0


async def _prune_old_memories(agent_type: str, max_retain_days: int) -> int:
    """Remove memories older than max_retain_days."""
    try:
        from models.database import get_db, Agent, Memory

        cutoff = datetime.now() - timedelta(days=max_retain_days)

        db = next(get_db())
        try:
            agent = db.query(Agent).filter(Agent.agent_type == agent_type).first()
            if agent is None:
                return 0

            # Only prune low-importance old memories
            pruned = (
                db.query(Memory)
                .filter(Memory.agent_id == agent.id)
                .filter(Memory.created_at < cutoff)
                .filter(Memory.importance < 7)  # Keep high-importance memories
                .delete()
            )
            db.commit()
            return pruned
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[SleepScheduler] Prune failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Main scheduler loop
# ---------------------------------------------------------------------------

async def _scheduler_loop():
    """Main background loop that checks for agents to consolidate."""
    logger.info("[SleepScheduler] Started (check_interval=%ds)", _CHECK_INTERVAL_SECONDS)

    # Track last consolidation time per agent to avoid too-frequent runs
    last_consolidation: Dict[str, datetime] = {}

    while True:
        try:
            await asyncio.sleep(_CHECK_INTERVAL_SECONDS)

            agent_configs = _load_agent_sleep_configs()
            if not agent_configs:
                continue

            now = datetime.now()

            for agent_type, sleep_cfg in agent_configs.items():
                # Check minimum interval
                last_time = last_consolidation.get(agent_type)
                if last_time and (now - last_time).total_seconds() < _MIN_CONSOLIDATION_INTERVAL_SECONDS:
                    continue

                # Check if consolidation should run
                if not _should_consolidate(agent_type, sleep_cfg):
                    continue

                logger.info("[SleepScheduler] Running consolidation for %s", agent_type)
                result = await _consolidate_agent_memories(agent_type, sleep_cfg)
                last_consolidation[agent_type] = now

                if result.get("error"):
                    logger.warning(
                        "[SleepScheduler] %s consolidation had errors: %s",
                        agent_type, result["error"],
                    )

        except asyncio.CancelledError:
            logger.info("[SleepScheduler] Shutting down")
            break
        except Exception as exc:
            logger.error("[SleepScheduler] Loop error: %s", exc)
            await asyncio.sleep(60)  # Back off on error


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_scheduler():
    """Start the sleep scheduler as a background task."""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        logger.debug("[SleepScheduler] Already running")
        return
    try:
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("[SleepScheduler] Background task created")
    except Exception as exc:
        logger.warning("[SleepScheduler] Failed to start: %s", exc)


def stop_scheduler():
    """Stop the sleep scheduler."""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
        logger.info("[SleepScheduler] Cancelled")
    _scheduler_task = None


def get_scheduler_status() -> Dict[str, Any]:
    """Get the current status of the sleep scheduler."""
    running = _scheduler_task is not None and not _scheduler_task.done()
    agent_configs = _load_agent_sleep_configs()
    return {
        "running": running,
        "check_interval_seconds": _CHECK_INTERVAL_SECONDS,
        "min_consolidation_interval_seconds": _MIN_CONSOLIDATION_INTERVAL_SECONDS,
        "agents_configured": len(agent_configs),
        "agents": {
            agent_type: {
                "enabled": cfg.get("enabled", True),
                "idle_threshold_minutes": cfg.get("idle_threshold_minutes", 30),
                "preferred_window": f"{cfg.get('preferred_window_start', '23:00')}-{cfg.get('preferred_window_end', '07:00')}",
                "max_retain_days": cfg.get("max_retain_days", 30),
            }
            for agent_type, cfg in agent_configs.items()
        },
    }


# ---------------------------------------------------------------------------
# Manual trigger (for testing / BOSS command)
# ---------------------------------------------------------------------------

async def trigger_consolidation(agent_type: str | None = None) -> Dict[str, Any]:
    """Manually trigger memory consolidation for one or all agents.

    Can be called via API or chat command for testing.
    """
    agent_configs = _load_agent_sleep_configs()

    if agent_type:
        if agent_type not in agent_configs:
            return {"error": f"Agent '{agent_type}' not found or sleep not configured"}
        cfg = agent_configs[agent_type]
        result = await _consolidate_agent_memories(agent_type, cfg)
        return {"triggered": [agent_type], "results": [result]}

    # Consolidate all agents
    results = []
    for atype, cfg in agent_configs.items():
        result = await _consolidate_agent_memories(atype, cfg)
        results.append(result)

    return {"triggered": list(agent_configs.keys()), "results": results}
