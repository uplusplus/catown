# -*- coding: utf-8 -*-
"""
Unit tests for ScreenshotCompareTool.
"""
import asyncio
import json
import os
import sys
import tempfile

import pytest

# Add backend directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.screenshot_compare import ScreenshotCompareTool, _resolve_viewport, _compare_images


class TestViewportPresets:
    """Test viewport resolution."""

    def test_desktop_preset(self):
        assert _resolve_viewport("desktop") == (1920, 1080)

    def test_tablet_preset(self):
        assert _resolve_viewport("tablet") == (768, 1024)

    def test_mobile_preset(self):
        assert _resolve_viewport("mobile") == (375, 812)

    def test_default_is_desktop(self):
        assert _resolve_viewport("") == (1920, 1080)
        assert _resolve_viewport(None) == (1920, 1080)

    def test_unknown_defaults_to_desktop(self):
        assert _resolve_viewport("tv") == (1920, 1080)


class TestCompareImages:
    """Test pixel-level image comparison."""

    def test_identical_images_pass(self, tmp_path):
        """Two identical images should have 0% diff."""
        from PIL import Image

        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        baseline = str(tmp_path / "baseline.png")
        actual = str(tmp_path / "actual.png")
        diff = str(tmp_path / "diff.png")
        img.save(baseline)
        img.save(actual)

        result = _compare_images(baseline, actual, diff, threshold=0.05)

        assert result["diff_percentage"] == 0.0
        assert result["diff_pixels"] == 0
        assert result["total_pixels"] == 10000
        assert result["passed"] is True
        assert os.path.isfile(diff)

    def test_different_images_detect_changes(self, tmp_path):
        """Two different images should have non-zero diff."""
        from PIL import Image

        img1 = Image.new("RGB", (100, 100), color=(255, 0, 0))
        img2 = Image.new("RGB", (100, 100), color=(0, 255, 0))
        baseline = str(tmp_path / "baseline.png")
        actual = str(tmp_path / "actual.png")
        diff = str(tmp_path / "diff.png")
        img1.save(baseline)
        img2.save(actual)

        result = _compare_images(baseline, actual, diff, threshold=0.01)

        assert result["diff_percentage"] > 0.0
        assert result["diff_pixels"] > 0
        assert result["passed"] is False  # All pixels differ

    def test_partial_diff_passes_threshold(self, tmp_path):
        """Small diff should pass when under threshold."""
        from PIL import Image

        img1 = Image.new("RGB", (100, 100), color=(255, 255, 255))
        img2 = img1.copy()
        # Change just 1 pixel
        img2.putpixel((50, 50), (0, 0, 0))
        baseline = str(tmp_path / "baseline.png")
        actual = str(tmp_path / "actual.png")
        diff = str(tmp_path / "diff.png")
        img1.save(baseline)
        img2.save(actual)

        result = _compare_images(baseline, actual, diff, threshold=0.05)

        assert result["diff_percentage"] == pytest.approx(1 / 10000, abs=1e-6)
        assert result["diff_pixels"] == 1
        assert result["passed"] is True  # Well under 5% threshold

    def test_different_sizes_resize_actual(self, tmp_path):
        """Actual image gets resized to match baseline."""
        from PIL import Image

        img1 = Image.new("RGB", (100, 100), color=(255, 0, 0))
        img2 = Image.new("RGB", (200, 200), color=(255, 0, 0))
        baseline = str(tmp_path / "baseline.png")
        actual = str(tmp_path / "actual.png")
        diff = str(tmp_path / "diff.png")
        img1.save(baseline)
        img2.save(actual)

        result = _compare_images(baseline, actual, diff, threshold=0.05)

        # After resize, colors should be similar → low diff
        assert result["passed"] is True


class TestScreenshotCompareTool:
    """Test the ScreenshotCompareTool API."""

    def test_schema(self):
        tool = ScreenshotCompareTool()
        schema = tool.get_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "screenshot_compare"
        props = schema["function"]["parameters"]["properties"]
        assert "action" in props
        assert "url" in props
        assert "viewport" in props
        assert "threshold" in props

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self):
        tool = ScreenshotCompareTool()
        result = await tool.execute(action="unknown_action")
        data = json.loads(result)
        assert data["success"] is False
        assert "Unknown action" in data["error"]

    @pytest.mark.asyncio
    async def test_capture_baseline_requires_url(self):
        tool = ScreenshotCompareTool()
        result = await tool.execute(action="capture_baseline")
        data = json.loads(result)
        assert data["success"] is False
        assert "url is required" in data["error"]

    @pytest.mark.asyncio
    async def test_compare_requires_paths(self):
        tool = ScreenshotCompareTool()
        result = await tool.execute(action="compare")
        data = json.loads(result)
        assert data["success"] is False
        assert "required" in data["error"]

    @pytest.mark.asyncio
    async def test_compare_baseline_not_found(self):
        tool = ScreenshotCompareTool()
        result = await tool.execute(
            action="compare",
            baseline_path="/nonexistent/baseline.png",
            actual_path="/nonexistent/actual.png",
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "Baseline file not found" in data["error"]

    @pytest.mark.asyncio
    async def test_compare_actual_not_found(self, tmp_path):
        from PIL import Image

        img = Image.new("RGB", (10, 10), color=(255, 0, 0))
        baseline = str(tmp_path / "baseline.png")
        img.save(baseline)

        tool = ScreenshotCompareTool()
        result = await tool.execute(
            action="compare",
            baseline_path=baseline,
            actual_path="/nonexistent/actual.png",
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "Actual file not found" in data["error"]

    @pytest.mark.asyncio
    async def test_compare_identical_images(self, tmp_path):
        from PIL import Image

        img = Image.new("RGB", (50, 50), color=(128, 128, 128))
        baseline = str(tmp_path / "baseline.png")
        actual = str(tmp_path / "actual.png")
        img.save(baseline)
        img.save(actual)

        tool = ScreenshotCompareTool()
        result = await tool.execute(
            action="compare",
            baseline_path=baseline,
            actual_path=actual,
            threshold=0.05,
            output_dir=str(tmp_path / "out"),
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["diff_percentage"] == 0.0
        assert data["passed"] is True
        assert os.path.isfile(data["diff_image"])

    @pytest.mark.asyncio
    async def test_compare_different_images_fails_threshold(self, tmp_path):
        from PIL import Image

        img1 = Image.new("RGB", (50, 50), color=(255, 0, 0))
        img2 = Image.new("RGB", (50, 50), color=(0, 255, 0))
        baseline = str(tmp_path / "baseline.png")
        actual = str(tmp_path / "actual.png")
        img1.save(baseline)
        img2.save(actual)

        tool = ScreenshotCompareTool()
        result = await tool.execute(
            action="compare",
            baseline_path=baseline,
            actual_path=actual,
            threshold=0.01,
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["diff_percentage"] > 0.01
        assert data["passed"] is False


class TestToolRegistration:
    """Test that ScreenshotCompareTool is properly registered."""

    def test_tool_is_in_registry(self):
        from tools import tool_registry

        tool = tool_registry.get("screenshot_compare")
        assert tool is not None
        assert isinstance(tool, ScreenshotCompareTool)

    def test_tool_policy_exists(self):
        from tools import tool_registry

        tool = tool_registry.get("screenshot_compare")
        policy = tool.get_policy_payload()
        assert policy["name"] == "screenshot_compare"
        assert policy["risk_level"] == "medium"
        assert policy["approval"]["kind"] == "conditional"
        assert policy["sandbox"]["mode"] == "browser_runtime"

    def test_tool_schema_valid(self):
        from tools import tool_registry

        tool = tool_registry.get("screenshot_compare")
        schema = tool.get_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "screenshot_compare"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
