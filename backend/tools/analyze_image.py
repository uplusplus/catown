# -*- coding: utf-8 -*-
"""
Analyze Image Tool — multimodal image understanding via LLM vision.

Sends an image (as base64 data URI) along with a text prompt to the LLM
for analysis. Supports detail levels: low, high, auto.

Ref: ADR-006 P0 (图片理解)
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.base import BaseTool
from tools.file_operations import get_active_workspace

logger = logging.getLogger("catown.analyze_image")

# Supported image MIME types
SUPPORTED_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}

# Max file size: 20MB
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024


def _workspace_root() -> Optional[str]:
    """Return the active workspace root directory, or None."""
    workspace = get_active_workspace()
    return os.path.realpath(workspace) if workspace else None


def _resolve_image_path(image_path: str) -> Path:
    """
    Resolve an image path relative to the workspace.

    Supports:
    - Absolute paths (within workspace)
    - Relative paths (resolved against workspace root)
    """
    workspace = _workspace_root()
    if not workspace:
        raise RuntimeError("No active workspace configured.")

    workspace_path = Path(workspace).expanduser().resolve()
    normalized = (image_path or "").replace("\\", "/").strip()

    if os.path.isabs(normalized):
        resolved = Path(normalized).expanduser().resolve()
    else:
        resolved = (workspace_path / normalized).resolve()

    # Security: ensure path doesn't escape workspace
    try:
        resolved.relative_to(workspace_path)
    except ValueError:
        raise ValueError(f"Image path escapes workspace: {image_path}")

    if not resolved.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    if not resolved.is_file():
        raise ValueError(f"Path is not a file: {image_path}")

    return resolved


def _image_to_data_uri(file_path: Path) -> str:
    """Read an image file and convert to a base64 data URI."""
    file_size = file_path.stat().st_size
    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"Image file too large: {file_size} bytes "
            f"(max {MAX_FILE_SIZE_BYTES} bytes)"
        )

    suffix = file_path.suffix.lower()
    mime_type = SUPPORTED_IMAGE_TYPES.get(suffix)
    if not mime_type:
        # Try to guess from the file
        guessed, _ = mimetypes.guess_type(str(file_path))
        if guessed and guessed.startswith("image/"):
            mime_type = guessed
        else:
            raise ValueError(
                f"Unsupported image type: {suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_IMAGE_TYPES.keys()))}"
            )

    raw = file_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


class AnalyzeImageTool(BaseTool):
    """Tool for analyzing images using LLM vision capabilities."""

    name = "analyze_image"
    description = (
        "Analyze an image using LLM vision capabilities. "
        "Sends the image along with a text prompt to the multimodal LLM "
        "and returns the analysis result. "
        "Supports PNG, JPEG, GIF, WebP, BMP, and SVG images."
    )

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": (
                        "Path to the image file (relative to workspace or absolute within workspace). "
                        "Example: 'screenshots/login.png' or 'docs/architecture.svg'"
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "The analysis instruction or question about the image. "
                        "Example: 'Describe what you see in this screenshot' or "
                        "'Extract all text from this image' or "
                        "'Identify any UI issues in this design'"
                    ),
                },
                "detail": {
                    "type": "string",
                    "enum": ["low", "high", "auto"],
                    "description": (
                        "Image detail level for the LLM. "
                        "'low' = faster, lower resolution; "
                        "'high' = slower, higher resolution; "
                        "'auto' = let the model decide (default)."
                    ),
                    "default": "auto",
                },
            },
            "required": ["image_path", "prompt"],
        }

    async def execute(
        self,
        image_path: str,
        prompt: str,
        detail: str = "auto",
        **kwargs,
    ) -> Any:
        from services.tool_governance import build_structured_tool_result

        # Validate inputs
        image_path = str(image_path or "").strip()
        prompt = str(prompt or "").strip()
        detail = str(detail or "auto").strip().lower()

        if not image_path:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="[Analyze Image] Error: image_path is required.",
                success=False,
                status="missing_image_path",
            )

        if not prompt:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="[Analyze Image] Error: prompt is required.",
                success=False,
                status="missing_prompt",
            )

        if detail not in ("low", "high", "auto"):
            detail = "auto"

        # Resolve and validate the image file
        try:
            resolved_path = _resolve_image_path(image_path)
        except FileNotFoundError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Image] Error: {exc}",
                success=False,
                status="file_not_found",
            )
        except ValueError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Image] Error: {exc}",
                success=False,
                status="invalid_path",
            )
        except RuntimeError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Image] Error: {exc}",
                success=False,
                status="no_workspace",
            )

        # Convert image to data URI
        try:
            data_uri = _image_to_data_uri(resolved_path)
        except ValueError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Image] Error: {exc}",
                success=False,
                status="image_error",
            )

        # Build multimodal message for LLM
        try:
            from llm.client import get_llm_client_for_agent, get_default_llm_client

            # Try to get the LLM client for the current agent context
            agent_name = kwargs.get("agent_name")
            if agent_name:
                try:
                    llm_client = get_llm_client_for_agent(agent_name)
                except Exception:
                    llm_client = get_default_llm_client()
            else:
                llm_client = get_default_llm_client()

            # Check if the model supports multimodal
            if not llm_client.supports_multimodal():
                logger.warning(
                    "Model '%s' may not support vision. "
                    "Attempting multimodal request anyway.",
                    llm_client.model,
                )

            # Build the multimodal content
            content = LLMClient._prepare_multimodal_content(
                text=prompt,
                images=[{"url": data_uri, "detail": detail}],
                detail=detail,
            )

            messages = [{"role": "user", "content": content}]

            # Call the LLM
            result = await llm_client.chat(
                messages=messages,
                temperature=0.3,  # Lower temp for more precise analysis
                max_tokens=4000,
            )

            file_size = resolved_path.stat().st_size
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=result or "(No analysis result returned)",
                success=True,
                status="analyzed",
                metadata={
                    "image_path": str(resolved_path),
                    "image_name": resolved_path.name,
                    "image_size_bytes": file_size,
                    "image_detail": detail,
                    "prompt": prompt,
                    "model": llm_client.model,
                },
            )

        except Exception as exc:
            logger.error("[AnalyzeImage] LLM call failed: %s", exc, exc_info=True)
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Image] LLM analysis failed: {exc}",
                success=False,
                status="llm_error",
            )


# Import LLMClient at module level for type reference
from llm.client import LLMClient  # noqa: E402
