# -*- coding: utf-8 -*-
"""
Screenshot Compare Tool — visual regression testing via pixel-level comparison.

Supports three actions:
  capture_baseline  — capture a page screenshot as baseline
  capture_actual    — capture a page screenshot as actual
  compare           — compare baseline vs actual, return diff percentage and diff image

Uses Pillow for image processing and delegates page capture to the existing
ScreenshotTool (screenshot.py).

Ref: ADR-007 (UI/UX Pro Max)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

from tools.base import BaseTool

logger = logging.getLogger("catown.screenshot_compare")

# Viewport presets: name -> (width, height)
_VIEWPORT_PRESETS = {
    "desktop": (1920, 1080),
    "tablet": (768, 1024),
    "mobile": (375, 812),
}


def _resolve_viewport(viewport: str) -> tuple[int, int]:
    """Return (width, height) for a viewport preset name."""
    key = str(viewport or "desktop").strip().lower()
    return _VIEWPORT_PRESETS.get(key, _VIEWPORT_PRESETS["desktop"])


async def _capture_page(
    url: str,
    output_path: str,
    width: int,
    height: int,
) -> str:
    """Capture a page screenshot using the existing ScreenshotTool."""
    from tools.screenshot import ScreenshotTool

    tool = ScreenshotTool()
    result_json = await tool.execute(
        url=url,
        output_path=output_path,
        width=width,
        height=height,
        full_page=False,
    )

    # ScreenshotTool returns a JSON string on success
    try:
        data = json.loads(result_json)
        if data.get("success"):
            return data["path"]
    except (json.JSONDecodeError, TypeError):
        pass

    # If it returned an error string, propagate it
    raise RuntimeError(f"Screenshot capture failed: {result_json}")


def _compare_images(
    baseline_path: str,
    actual_path: str,
    diff_path: str,
    threshold: float,
) -> Dict[str, Any]:
    """Compare two images pixel-by-pixel and generate a diff image.

    Returns a dict with:
      - diff_percentage: float (0-1)
      - diff_path: str
      - total_pixels: int
      - diff_pixels: int
      - passed: bool (diff_percentage <= threshold)
    """
    from PIL import Image, ImageChops, ImageDraw

    baseline = Image.open(baseline_path).convert("RGB")
    actual = Image.open(actual_path).convert("RGB")

    # Resize actual to match baseline if dimensions differ
    if baseline.size != actual.size:
        actual = actual.resize(baseline.size, Image.LANCZOS)

    # Compute pixel-level difference
    diff = ImageChops.difference(baseline, actual)

    # Convert to grayscale to measure magnitude
    gray_diff = diff.convert("L")

    # Count pixels that differ (non-zero)
    pixels = list(gray_diff.getdata())
    total_pixels = len(pixels)
    diff_pixels = sum(1 for p in pixels if p > 0)

    diff_percentage = diff_pixels / total_pixels if total_pixels > 0 else 0.0

    # Generate a visual diff image: highlight changed regions in red
    diff_visual = baseline.copy()
    draw = ImageDraw.Draw(diff_visual)

    # Create a mask of differing pixels
    width, height = baseline.size
    for y in range(height):
        for x in range(width):
            if gray_diff.getpixel((x, y)) > 0:
                # Overlay red with intensity proportional to difference
                diff_val = gray_diff.getpixel((x, y))
                r_actual, g_actual, b_actual = actual.getpixel((x, y))
                # Blend: 50% red highlight + 50% actual
                draw.point(
                    (x, y),
                    fill=(
                        min(255, diff_val + r_actual // 2),
                        g_actual // 2,
                        b_actual // 2,
                    ),
                )

    diff_visual.save(diff_path)

    return {
        "diff_percentage": round(diff_percentage, 6),
        "diff_path": diff_path,
        "total_pixels": total_pixels,
        "diff_pixels": diff_pixels,
        "passed": diff_percentage <= threshold,
    }


class ScreenshotCompareTool(BaseTool):
    """Visual regression testing via screenshot comparison."""

    name = "screenshot_compare"
    description = (
        "Capture and compare page screenshots for visual regression testing. "
        "Actions: 'capture_baseline' (save baseline), 'capture_actual' (save actual), "
        "'compare' (diff baseline vs actual, returns diff percentage and visual diff image)."
    )

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["capture_baseline", "capture_actual", "compare"],
                    "description": (
                        "Action to perform: "
                        "'capture_baseline' = capture and save baseline screenshot; "
                        "'capture_actual' = capture and save actual screenshot; "
                        "'compare' = compare two screenshots and return diff."
                    ),
                },
                "url": {
                    "type": "string",
                    "description": "Page URL to capture (required for capture actions).",
                },
                "viewport": {
                    "type": "string",
                    "enum": ["desktop", "tablet", "mobile"],
                    "description": "Viewport preset: 'desktop' (1920x1080), 'tablet' (768x1024), 'mobile' (375x812). Default: desktop.",
                },
                "baseline_path": {
                    "type": "string",
                    "description": "Path to baseline screenshot (required for compare action).",
                },
                "actual_path": {
                    "type": "string",
                    "description": "Path to actual screenshot (required for compare action).",
                },
                "threshold": {
                    "type": "number",
                    "description": "Diff threshold 0-1. If diff_percentage > threshold, comparison fails. Default: 0.05.",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory to save screenshots. Default: system temp directory.",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        url: str = "",
        viewport: str = "desktop",
        baseline_path: str = "",
        actual_path: str = "",
        threshold: float = 0.05,
        output_dir: str = "",
        **kwargs,
    ) -> str:
        action = str(action or "").strip().lower()

        if action == "capture_baseline":
            return await self._capture(url, viewport, output_dir, label="baseline")
        elif action == "capture_actual":
            return await self._capture(url, viewport, output_dir, label="actual")
        elif action == "compare":
            return await self._compare(baseline_path, actual_path, threshold, output_dir)
        else:
            return json.dumps({
                "success": False,
                "error": f"Unknown action '{action}'. Valid: capture_baseline, capture_actual, compare.",
            }, ensure_ascii=False)

    async def _capture(
        self,
        url: str,
        viewport: str,
        output_dir: str,
        label: str,
    ) -> str:
        """Capture a page screenshot."""
        if not url:
            return json.dumps({
                "success": False,
                "error": "url is required for capture actions.",
            }, ensure_ascii=False)

        width, height = _resolve_viewport(viewport)

        # Determine output path
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"{label}_{viewport}.png")
        else:
            output_path = tempfile.mktemp(
                suffix=".png",
                prefix=f"screenshot_{label}_",
            )

        try:
            path = await _capture_page(url, output_path, width, height)
            file_size = os.path.getsize(path)
            return json.dumps({
                "success": True,
                "action": f"capture_{label}",
                "path": path,
                "url": url,
                "viewport": viewport,
                "size": f"{width}x{height}",
                "size_bytes": file_size,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e),
            }, ensure_ascii=False)

    async def _compare(
        self,
        baseline_path: str,
        actual_path: str,
        threshold: float,
        output_dir: str,
    ) -> str:
        """Compare two screenshots."""
        if not baseline_path or not actual_path:
            return json.dumps({
                "success": False,
                "error": "baseline_path and actual_path are required for compare action.",
            }, ensure_ascii=False)

        if not os.path.isfile(baseline_path):
            return json.dumps({
                "success": False,
                "error": f"Baseline file not found: {baseline_path}",
            }, ensure_ascii=False)

        if not os.path.isfile(actual_path):
            return json.dumps({
                "success": False,
                "error": f"Actual file not found: {actual_path}",
            }, ensure_ascii=False)

        # Determine diff output path
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            diff_path = os.path.join(output_dir, "diff.png")
        else:
            diff_path = tempfile.mktemp(suffix=".png", prefix="screenshot_diff_")

        try:
            result = await asyncio.to_thread(
                _compare_images,
                baseline_path,
                actual_path,
                diff_path,
                threshold,
            )
            return json.dumps({
                "success": True,
                "action": "compare",
                "baseline": baseline_path,
                "actual": actual_path,
                "diff_image": result["diff_path"],
                "diff_percentage": result["diff_percentage"],
                "total_pixels": result["total_pixels"],
                "diff_pixels": result["diff_pixels"],
                "threshold": threshold,
                "passed": result["passed"],
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e),
            }, ensure_ascii=False)
