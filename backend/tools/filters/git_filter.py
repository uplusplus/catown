# -*- coding: utf-8 -*-
"""Git output filter — compress git status/diff/log/commit/push output."""

from __future__ import annotations

import re

from tools.filters.base import BaseFilter
from tools.output_filter import register_filter


class GitStatusFilter(BaseFilter):
    """Compress git status output to file change summary."""

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        lines = output.strip().splitlines()
        if not lines:
            return output

        categories: dict[str, list[str]] = {
            "modified": [],
            "new": [],
            "deleted": [],
            "renamed": [],
            "untracked": [],
            "other": [],
        }

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("On branch"):
                continue
            if "Changes to be committed" in stripped:
                continue
            if "Changes not staged" in stripped:
                continue
            if "Untracked files" in stripped:
                continue
            if "no changes added" in stripped:
                continue

            # Parse git status porcelain-style or standard format
            if ":" in stripped:
                prefix = stripped.split(":")[0].strip().lower()
                if "new file" in prefix:
                    categories["new"].append(stripped)
                elif "deleted" in prefix:
                    categories["deleted"].append(stripped)
                elif "renamed" in prefix:
                    categories["renamed"].append(stripped)
                elif "modified" in prefix:
                    categories["modified"].append(stripped)
                else:
                    categories["other"].append(stripped)
            elif stripped.startswith("??"):
                categories["untracked"].append(stripped[2:].strip())
            elif "\t" in stripped:
                # Porcelain format: XY\tfile
                xy = stripped.split("\t")[0]
                path = stripped.split("\t", 1)[1] if "\t" in stripped else stripped
                if "M" in xy:
                    categories["modified"].append(path)
                elif "A" in xy:
                    categories["new"].append(path)
                elif "D" in xy:
                    categories["deleted"].append(path)
                elif "R" in xy:
                    categories["renamed"].append(path)
                elif "?" in xy:
                    categories["untracked"].append(path)
                else:
                    categories["other"].append(path)
            else:
                categories["other"].append(stripped)

        parts: list[str] = []
        for label, items in categories.items():
            if items:
                parts.append(f"{label}: {len(items)}")
                if len(items) <= 5:
                    parts.extend(f"  {item}" for item in items)

        return "\n".join(parts) if parts else "(no changes)"


class GitDiffFilter(BaseFilter):
    """Compress git diff to statistics."""

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        lines = output.strip().splitlines()
        if not lines:
            return output

        files_changed = 0
        insertions = 0
        deletions = 0
        file_names: list[str] = []

        for line in lines:
            if line.startswith("diff --git"):
                files_changed += 1
                # Extract file name
                match = re.search(r"b/(.+)$", line)
                if match:
                    file_names.append(match.group(1))
            elif line.startswith("+++") and not line.startswith("+++ /dev/null"):
                pass  # counted via insertions
            elif line.startswith("---") and not line.startswith("--- /dev/null"):
                pass  # counted via deletions
            elif line.startswith("+") and not line.startswith("+++"):
                insertions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        # Check for stat summary line at the end
        for line in reversed(lines):
            stat_match = re.search(
                r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?",
                line,
            )
            if stat_match:
                files_changed = int(stat_match.group(1))
                insertions = int(stat_match.group(2) or 0)
                deletions = int(stat_match.group(3) or 0)
                break

        parts = [f"diff: {files_changed} file(s) changed, +{insertions}/-{deletions}"]
        if file_names and len(file_names) <= 10:
            parts.append("files: " + ", ".join(file_names))
        elif file_names:
            parts.append(f"files: {', '.join(file_names[:5])} ... and {len(file_names) - 5} more")

        return "\n".join(parts)


class GitLogFilter(BaseFilter):
    """Compress git log to commit count and summary."""

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        lines = output.strip().splitlines()
        if not lines:
            return output

        commits: list[str] = []
        current_hash = ""
        current_msg = ""

        for line in lines:
            if re.match(r"^[0-9a-f]{7,40}\s", line):
                if current_hash:
                    commits.append(f"  {current_hash[:8]} {current_msg}")
                parts = line.split(None, 1)
                current_hash = parts[0]
                current_msg = parts[1] if len(parts) > 1 else ""
            elif line.strip() and not line.startswith("Author") and not line.startswith("Date"):
                if not current_msg:
                    current_msg = line.strip()

        if current_hash:
            commits.append(f"  {current_hash[:8]} {current_msg}")

        result = [f"log: {len(commits)} commit(s)"]
        if len(commits) <= 10:
            result.extend(commits)
        else:
            result.extend(commits[:5])
            result.append(f"  ... and {len(commits) - 5} more")

        return "\n".join(result)


class GitCommitFilter(BaseFilter):
    """Compress git commit output to one-liner."""

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        if exit_code != 0:
            return output  # Keep full error output

        # Extract branch and commit hash
        branch_match = re.search(r"\[(\S+)\s+([a-f0-9]+)\]", output)
        if branch_match:
            return f"ok [{branch_match.group(1)} {branch_match.group(2)[:8]}]"

        hash_match = re.search(r"[a-f0-9]{7,40}", output)
        if hash_match:
            return f"ok {hash_match.group()[:8]}"

        return "ok"


class GitPushFilter(BaseFilter):
    """Compress git push output."""

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        if exit_code != 0:
            return output

        # Look for "branch → remote" pattern
        push_match = re.search(r"(\S+)\s*->\s*(\S+)", output)
        if push_match:
            return f"pushed {push_match.group(1)} → {push_match.group(2)}"

        if "Everything up-to-date" in output:
            return "already up-to-date"

        return "pushed"


class GitPullFilter(BaseFilter):
    """Compress git pull output."""

    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        if exit_code != 0:
            return output

        fast_match = re.search(r"Fast-forward", output)
        if fast_match:
            return "pulled (fast-forward)"

        files_match = re.search(r"(\d+) files? changed", output)
        if files_match:
            return f"pulled: {files_match.group(0)}"

        return "pulled"


# Register filters
register_filter("git status", GitStatusFilter)
register_filter("git diff", GitDiffFilter)
register_filter("git log", GitLogFilter)
register_filter("git commit", GitCommitFilter)
register_filter("git push", GitPushFilter)
register_filter("git pull", GitPullFilter)
