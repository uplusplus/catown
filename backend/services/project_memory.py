# -*- coding: utf-8 -*-
"""
Project Memory Service — file-based project-level memory.

Stores memories as Markdown files under ``{workspace}/.catown/memory/``.
Categories: decisions.md, conventions.md, issues.md, context.md

Design follows PRD §4.6.2 and ADR-004.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("catown.project_memory")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CATOWN_DIR = ".catown"
_MEMORY_DIR = "memory"

# Category files
CATEGORY_FILES: Dict[str, str] = {
    "decision": "decisions.md",
    "convention": "conventions.md",
    "issue": "issues.md",
    "context": "context.md",
}

CATEGORY_LABELS: Dict[str, str] = {
    "decision": "关键决策",
    "convention": "代码/架构约定",
    "issue": "遇到的问题与方案",
    "context": "项目上下文",
}

# Default category when type is unclear
DEFAULT_CATEGORY = "context"

# Maximum characters per category file before warning
_MAX_FILE_CHARS = 200_000


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MemoryEntry:
    """A single memory entry extracted from conversation or written directly."""
    content: str
    category: str = DEFAULT_CATEGORY
    importance: int = 5
    source_agent: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")


@dataclass
class ProjectMemoryIndex:
    """Lightweight index of what's in a project's memory."""
    workspace: str
    categories: Dict[str, int] = field(default_factory=dict)  # category -> entry count
    total_entries: int = 0

    @classmethod
    def scan(cls, workspace: str) -> "ProjectMemoryIndex":
        """Scan a workspace and build the index."""
        idx = cls(workspace=workspace)
        mem_dir = _memory_dir(workspace)
        if not mem_dir.exists():
            return idx
        for cat, fname in CATEGORY_FILES.items():
            fpath = mem_dir / fname
            if fpath.exists():
                content = fpath.read_text(encoding="utf-8", errors="replace")
                count = content.count("\n## ")
                idx.categories[cat] = count
                idx.total_entries += count
        return idx


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _memory_dir(workspace: str) -> Path:
    """Return the .catown/memory/ directory for a workspace."""
    return Path(workspace) / _CATOWN_DIR / _MEMORY_DIR


def _category_file(workspace: str, category: str) -> Path:
    """Return the file path for a given category."""
    fname = CATEGORY_FILES.get(category, CATEGORY_FILES[DEFAULT_CATEGORY])
    return _memory_dir(workspace) / fname


def ensure_memory_dir(workspace: str) -> Path:
    """Create the memory directory if it doesn't exist."""
    d = _memory_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def read_category(workspace: str, category: str, limit: int = 20) -> str:
    """Read the content of a category file, returning the last `limit` entries."""
    fpath = _category_file(workspace, category)
    if not fpath.exists():
        return ""
    content = fpath.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        return ""
    # Split by ## headers, keep the last `limit` sections
    sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)
    sections = [s.strip() for s in sections if s.strip()]
    if limit and len(sections) > limit:
        sections = sections[-limit:]
    return "\n\n".join(sections)


def search_memory(workspace: str, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Search all category files for a query string. Returns matching entries."""
    results = []
    mem_dir = _memory_dir(workspace)
    if not mem_dir.exists():
        return results

    query_lower = query.lower()
    for cat, fname in CATEGORY_FILES.items():
        fpath = mem_dir / fname
        if not fpath.exists():
            continue
        content = fpath.read_text(encoding="utf-8", errors="replace")
        # Split into entries and search
        sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)
        for section in sections:
            if query_lower in section.lower():
                results.append({
                    "category": cat,
                    "category_label": CATEGORY_LABELS.get(cat, cat),
                    "content": section.strip()[:500],
                    "file": str(fpath),
                })
                if len(results) >= max_results:
                    return results
    return results


def get_memory_summary(workspace: str) -> Dict[str, Any]:
    """Get a summary of all project memory."""
    idx = ProjectMemoryIndex.scan(workspace)
    return {
        "workspace": workspace,
        "total_entries": idx.total_entries,
        "categories": {
            cat: {"count": count, "label": CATEGORY_LABELS.get(cat, cat)}
            for cat, count in idx.categories.items()
        },
    }


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def append_entry(workspace: str, entry: MemoryEntry) -> str:
    """Append a memory entry to the appropriate category file.

    Returns the path of the file written to.
    """
    ensure_memory_dir(workspace)
    fpath = _category_file(workspace, entry.category)

    # Build the entry block
    header = f"## [{entry.timestamp}] "
    if entry.source_agent:
        header += f"({entry.source_agent}) "
    header += f"[importance={entry.importance}]"

    block = f"\n\n{header}\n{entry.content.strip()}\n"

    # Append
    with open(fpath, "a", encoding="utf-8") as f:
        f.write(block)

    logger.info(
        "[ProjectMemory] Appended %s entry to %s (%d chars)",
        entry.category, fpath, len(entry.content),
    )

    # Warn if file is getting large
    try:
        size = fpath.stat().st_size
        if size > _MAX_FILE_CHARS:
            logger.warning(
                "[ProjectMemory] %s is %d chars, consider summarization",
                fpath, size,
            )
    except OSError:
        pass

    return str(fpath)


def write_decision(workspace: str, content: str, source_agent: str = "", importance: int = 7) -> str:
    """Convenience: write a decision entry."""
    return append_entry(workspace, MemoryEntry(
        content=content, category="decision", importance=importance, source_agent=source_agent,
    ))


def write_convention(workspace: str, content: str, source_agent: str = "", importance: int = 6) -> str:
    """Convenience: write a convention entry."""
    return append_entry(workspace, MemoryEntry(
        content=content, category="convention", importance=importance, source_agent=source_agent,
    ))


def write_issue(workspace: str, content: str, source_agent: str = "", importance: int = 6) -> str:
    """Convenience: write an issue/solution entry."""
    return append_entry(workspace, MemoryEntry(
        content=content, category="issue", importance=importance, source_agent=source_agent,
    ))


def write_context(workspace: str, content: str, source_agent: str = "", importance: int = 5) -> str:
    """Convenience: write a general context entry."""
    return append_entry(workspace, MemoryEntry(
        content=content, category="context", importance=importance, source_agent=source_agent,
    ))


# ---------------------------------------------------------------------------
# Stage-end summarization
# ---------------------------------------------------------------------------

def summarize_stage_to_project_memory(
    workspace: str,
    stage_name: str,
    agent_name: str,
    summary_text: str,
    artifacts: Optional[List[str]] = None,
    decisions: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Called at the end of a pipeline stage to persist key information.

    Writes:
    - A context entry with the stage summary
    - Decision entries for any key decisions
    - Returns paths of written files
    """
    paths: Dict[str, str] = {}

    # Stage summary → context
    artifact_str = ""
    if artifacts:
        artifact_str = "\n产出物: " + ", ".join(artifacts)

    ctx_content = f"**Stage: {stage_name}** ({agent_name})\n{summary_text}{artifact_str}"
    paths["context"] = write_context(
        workspace, ctx_content, source_agent=agent_name, importance=6,
    )

    # Decisions
    if decisions:
        for dec in decisions:
            paths[f"decision_{dec[:30]}"] = write_decision(
                workspace, dec, source_agent=agent_name, importance=7,
            )

    return paths


# ---------------------------------------------------------------------------
# Integration helpers (for retrieve_memory / save_memory tools)
# ---------------------------------------------------------------------------

def format_project_memory_for_context(workspace: str, max_chars: int = 3000) -> str:
    """Format project memory as a string suitable for injection into LLM context.

    Returns a compact summary of all categories, respecting max_chars.
    """
    if not workspace or not Path(workspace).exists():
        return ""

    parts = []
    for cat in ["decision", "convention", "issue", "context"]:
        content = read_category(workspace, cat, limit=5)
        if content:
            label = CATEGORY_LABELS.get(cat, cat)
            parts.append(f"### {label}\n{content}")

    if not parts:
        return ""

    result = "\n\n".join(parts)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... (truncated)"
    return result
