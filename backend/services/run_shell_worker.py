# -*- coding: utf-8 -*-
"""Detached worker entrypoint for tracked run_shell processes."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.run_shell_processes import run_tracked_run_shell_worker


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2
    return run_tracked_run_shell_worker(argv[1])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
