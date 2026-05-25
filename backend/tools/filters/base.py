# -*- coding: utf-8 -*-
"""Filter base class."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseFilter(ABC):
    """Base class for command output filters."""

    @abstractmethod
    def apply(self, output: str, *, exit_code: int = 0, command: str = "") -> str:
        """Filter the raw command output and return compressed version."""
        ...
