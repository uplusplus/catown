import pytest


def _pdf_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _make_text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({_pdf_literal(text)}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_offset = len(body)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        xref += f"{offset:010d} 00000 n \n"
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )
    return body + xref.encode("ascii") + trailer.encode("ascii")


@pytest.mark.asyncio
async def test_analyze_document_extracts_pdf_text(tmp_path):
    from tools.analyze_document import AnalyzeDocumentTool
    from tools.file_operations import reset_active_workspace, set_active_workspace

    pdf_path = tmp_path / "uploads" / "sample.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes(_make_text_pdf("Hello PDF Analysis"))

    token = set_active_workspace(str(tmp_path))
    try:
        result = await AnalyzeDocumentTool().execute(
            file_path="uploads/sample.pdf",
            mode="extract",
            max_pages=1,
        )
    finally:
        reset_active_workspace(token)

    assert result["success"] is True
    assert result["status"] == "extracted"
    assert "Hello PDF Analysis" in result["result"]
    assert result["metadata"]["pages_read"] == 1
    assert result["metadata"]["total_pages"] == 1


@pytest.mark.asyncio
async def test_analyze_document_calls_llm_with_extracted_text(tmp_path, monkeypatch):
    from tools.analyze_document import AnalyzeDocumentTool
    from tools.file_operations import reset_active_workspace, set_active_workspace

    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(_make_text_pdf("Critical warranty clause"))
    captured = {}

    class FakeLLMClient:
        model = "test-model"

        async def chat(self, messages, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            return "The document contains a warranty clause."

    monkeypatch.setattr("llm.client.get_default_llm_client", lambda: FakeLLMClient())

    token = set_active_workspace(str(tmp_path))
    try:
        result = await AnalyzeDocumentTool().execute(
            file_path="spec.pdf",
            mode="analyze",
            prompt="Summarize legal risks.",
            max_pages=1,
        )
    finally:
        reset_active_workspace(token)

    assert result["success"] is True
    assert result["status"] == "analyzed"
    assert "warranty clause" in result["result"]
    assert "Critical warranty clause" in captured["messages"][0]["content"]
    assert captured["kwargs"]["temperature"] == 0.2
    assert result["metadata"]["model"] == "test-model"


@pytest.mark.asyncio
async def test_analyze_document_rejects_path_escape(tmp_path):
    from tools.analyze_document import AnalyzeDocumentTool
    from tools.file_operations import reset_active_workspace, set_active_workspace

    outside_pdf = tmp_path.parent / "outside.pdf"
    outside_pdf.write_bytes(_make_text_pdf("Outside workspace"))

    token = set_active_workspace(str(tmp_path))
    try:
        result = await AnalyzeDocumentTool().execute(file_path=str(outside_pdf))
    finally:
        reset_active_workspace(token)

    assert result["success"] is False
    assert result["status"] == "invalid_path"
    assert "escapes workspace" in result["result"]
