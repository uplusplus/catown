# -*- coding: utf-8 -*-
"""Document analysis tools for PDF attachments."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from services.multimodal_config import multimodal_max_upload_size_bytes
from services.tool_governance import build_structured_tool_result
from tools.base import BaseTool
from tools.file_operations import get_active_workspace

logger = logging.getLogger("catown.analyze_document")

DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_CHARS = 12000
MAX_PAGES_LIMIT = 200
MAX_CHARS_LIMIT = 50000


def _workspace_root() -> Optional[str]:
    workspace = get_active_workspace()
    return os.path.realpath(workspace) if workspace else None


def _resolve_document_path(file_path: str) -> Path:
    workspace = _workspace_root()
    if not workspace:
        raise RuntimeError("No active workspace configured.")

    workspace_path = Path(workspace).expanduser().resolve()
    normalized = (file_path or "").replace("\\", "/").strip()

    if os.path.isabs(normalized):
        resolved = Path(normalized).expanduser().resolve()
    else:
        resolved = (workspace_path / normalized).resolve()

    try:
        resolved.relative_to(workspace_path)
    except ValueError:
        raise ValueError(f"Document path escapes workspace: {file_path}")

    if not resolved.exists():
        raise FileNotFoundError(f"Document file not found: {file_path}")
    if not resolved.is_file():
        raise ValueError(f"Path is not a file: {file_path}")
    return resolved


def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)


def _extract_pdf_text(
    file_path: Path,
    *,
    page_start: int,
    max_pages: int,
    max_chars: int,
) -> Dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PDF text extraction requires the optional dependency 'pypdf'. "
            "Install backend requirements before using analyze_document."
        ) from exc

    reader = PdfReader(str(file_path))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as exc:  # pragma: no cover - pypdf encryption branches vary.
            raise ValueError(f"PDF is encrypted and could not be decrypted: {file_path.name}") from exc

    total_pages = len(reader.pages)
    start_index = max(0, page_start - 1)
    if start_index >= total_pages:
        raise ValueError(f"page_start {page_start} is beyond the PDF page count ({total_pages}).")

    end_index = min(total_pages, start_index + max_pages)
    page_sections: list[str] = []
    extracted_chars = 0
    truncated_by_chars = False
    pages_with_text = 0

    for index in range(start_index, end_index):
        try:
            page_text = reader.pages[index].extract_text() or ""
        except Exception as exc:
            page_text = f"[Page extraction failed: {exc}]"
        page_text = page_text.strip()
        if page_text:
            pages_with_text += 1
        else:
            page_text = "[No extractable text found on this page.]"

        section = f"--- Page {index + 1} ---\n{page_text}"
        remaining = max_chars - extracted_chars
        if remaining <= 0:
            truncated_by_chars = True
            break
        if len(section) > remaining:
            section = section[:remaining].rstrip() + "\n[Truncated due to max_chars limit.]"
            truncated_by_chars = True
            page_sections.append(section)
            extracted_chars = max_chars
            break
        page_sections.append(section)
        extracted_chars += len(section)

    extracted_text = "\n\n".join(page_sections).strip()
    return {
        "text": extracted_text,
        "total_pages": total_pages,
        "page_start": page_start,
        "pages_read": end_index - start_index,
        "pages_with_text": pages_with_text,
        "truncated": bool(truncated_by_chars or end_index < total_pages),
        "truncated_by_chars": truncated_by_chars,
    }


class AnalyzeDocumentTool(BaseTool):
    """Extract and optionally analyze PDF document content."""

    name = "analyze_document"
    description = (
        "Analyze a PDF document from the workspace. Extracts PDF text locally and, "
        "when a prompt is provided, asks the LLM to analyze the extracted content. "
        "Use this as the fallback for PDF attachments when direct multimodal file "
        "input is unsupported or when page-scoped evidence is needed."
    )

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the PDF file, relative to the active workspace.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional analysis instruction. If omitted, the tool returns extracted text.",
                    "default": "",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "extract", "analyze"],
                    "description": "'extract' returns text only; 'analyze' sends extracted text to the LLM; 'auto' analyzes only when prompt is provided.",
                    "default": "auto",
                },
                "page_start": {
                    "type": "integer",
                    "description": "1-based first page to read.",
                    "default": 1,
                    "minimum": 1,
                },
                "max_pages": {
                    "type": "integer",
                    "description": "Maximum number of pages to read.",
                    "default": DEFAULT_MAX_PAGES,
                    "minimum": 1,
                    "maximum": MAX_PAGES_LIMIT,
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum extracted characters to include in the tool result or LLM prompt.",
                    "default": DEFAULT_MAX_CHARS,
                    "minimum": 1000,
                    "maximum": MAX_CHARS_LIMIT,
                },
            },
            "required": ["file_path"],
        }

    async def execute(
        self,
        file_path: str,
        prompt: str = "",
        mode: str = "auto",
        page_start: int = 1,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_chars: int = DEFAULT_MAX_CHARS,
        **kwargs,
    ) -> Dict[str, Any]:
        file_path = str(file_path or "").strip()
        prompt = str(prompt or "").strip()
        mode = str(mode or "auto").strip().lower()
        if mode not in {"auto", "extract", "analyze"}:
            mode = "auto"

        if not file_path:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="[Analyze Document] Error: file_path is required.",
                success=False,
                status="missing_file_path",
            )

        page_start = _coerce_int(page_start, default=1, minimum=1, maximum=MAX_PAGES_LIMIT)
        max_pages = _coerce_int(max_pages, default=DEFAULT_MAX_PAGES, minimum=1, maximum=MAX_PAGES_LIMIT)
        max_chars = _coerce_int(max_chars, default=DEFAULT_MAX_CHARS, minimum=1000, maximum=MAX_CHARS_LIMIT)

        try:
            resolved_path = _resolve_document_path(file_path)
        except FileNotFoundError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Document] Error: {exc}",
                success=False,
                status="file_not_found",
            )
        except ValueError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Document] Error: {exc}",
                success=False,
                status="invalid_path",
            )
        except RuntimeError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Document] Error: {exc}",
                success=False,
                status="no_workspace",
            )

        file_size = resolved_path.stat().st_size
        max_document_size_bytes = multimodal_max_upload_size_bytes()
        if file_size > max_document_size_bytes:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=(
                    f"[Analyze Document] Error: document file too large: {file_size} bytes "
                    f"(max {max_document_size_bytes} bytes)."
                ),
                success=False,
                status="file_too_large",
            )
        if resolved_path.suffix.lower() != ".pdf":
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="[Analyze Document] Error: only PDF documents are supported in this release.",
                success=False,
                status="unsupported_file_type",
            )

        try:
            extraction = _extract_pdf_text(
                resolved_path,
                page_start=page_start,
                max_pages=max_pages,
                max_chars=max_chars,
            )
        except RuntimeError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Document] Error: {exc}",
                success=False,
                status="missing_dependency",
            )
        except ValueError as exc:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Document] Error: {exc}",
                success=False,
                status="pdf_error",
            )
        except Exception as exc:
            logger.error("[AnalyzeDocument] PDF extraction failed: %s", exc, exc_info=True)
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Document] PDF extraction failed: {exc}",
                success=False,
                status="pdf_error",
            )

        extracted_text = str(extraction["text"] or "").strip()
        metadata = {
            "document_path": str(resolved_path),
            "document_name": resolved_path.name,
            "document_size_bytes": file_size,
            "page_start": extraction["page_start"],
            "pages_read": extraction["pages_read"],
            "total_pages": extraction["total_pages"],
            "pages_with_text": extraction["pages_with_text"],
            "truncated": extraction["truncated"],
            "truncated_by_chars": extraction["truncated_by_chars"],
            "mode": mode,
        }

        if int(extraction["pages_with_text"] or 0) <= 0:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=(
                    f"[Analyze Document] No extractable text was found in '{file_path}'. "
                    "It may be a scanned PDF; use OCR/image analysis in a later pipeline stage."
                ),
                success=False,
                status="no_extractable_text",
                metadata=metadata,
            )

        should_analyze = mode == "analyze" or (mode == "auto" and bool(prompt))
        if not should_analyze:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=(
                    f"[Analyze Document] Extracted text from '{file_path}' "
                    f"(pages {extraction['page_start']}-{extraction['page_start'] + extraction['pages_read'] - 1}):\n\n"
                    f"{extracted_text}"
                ),
                success=True,
                status="extracted",
                metadata=metadata,
            )

        if not prompt:
            return build_structured_tool_result(
                tool_name=self.name,
                result_text="[Analyze Document] Error: prompt is required when mode='analyze'.",
                success=False,
                status="missing_prompt",
                metadata=metadata,
            )

        try:
            from llm.client import get_default_llm_client, get_llm_client_for_agent

            agent_name = kwargs.get("agent_name")
            if agent_name:
                try:
                    llm_client = get_llm_client_for_agent(agent_name)
                except Exception:
                    llm_client = get_default_llm_client()
            else:
                llm_client = get_default_llm_client()

            analysis_prompt = (
                "Analyze the following PDF text extracted by Catown.\n\n"
                f"Document: {resolved_path.name}\n"
                f"Pages included: {extraction['page_start']}-"
                f"{extraction['page_start'] + extraction['pages_read'] - 1} of {extraction['total_pages']}\n"
                f"User request:\n{prompt}\n\n"
                f"Extracted PDF text:\n{extracted_text}\n\n"
                "Ground the answer in the extracted text. Mention if the text appears incomplete or truncated."
            )
            result = await llm_client.chat(
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0.2,
                max_tokens=4000,
            )
            metadata["model"] = getattr(llm_client, "model", "")
            metadata["prompt"] = prompt
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=result or "(No analysis result returned)",
                success=True,
                status="analyzed",
                metadata=metadata,
            )
        except Exception as exc:
            logger.error("[AnalyzeDocument] LLM analysis failed: %s", exc, exc_info=True)
            return build_structured_tool_result(
                tool_name=self.name,
                result_text=f"[Analyze Document] LLM analysis failed: {exc}",
                success=False,
                status="llm_error",
                metadata=metadata,
            )
