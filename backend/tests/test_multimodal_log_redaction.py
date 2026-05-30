import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_redact_multimodal_payload_removes_data_uri_bytes():
    from services.multimodal_log_redaction import (
        clear_multimodal_data_uri_references,
        redact_multimodal_payload,
        register_multimodal_data_uri_reference,
    )

    clear_multimodal_data_uri_references()
    image_data_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAE="
    pdf_data_uri = "data:application/pdf;base64,JVBERi0xLjQK"
    register_multimodal_data_uri_reference(
        image_data_uri,
        file_id="file_image123",
        mime_type="image/png",
        file_name="sample.png",
        file_size=21,
        sha256="image-sha",
    )
    register_multimodal_data_uri_reference(
        pdf_data_uri,
        file_id="file_pdf123",
        mime_type="application/pdf",
        file_name="spec.pdf",
        file_size=9,
        sha256="pdf-sha",
    )

    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect this file"},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_uri},
                    },
                    {
                        "type": "file",
                        "file": {"filename": "spec.pdf", "file_data": pdf_data_uri},
                    },
                ],
            }
        ]
    }

    redacted = redact_multimodal_payload(payload)
    rendered = json.dumps(redacted, ensure_ascii=False)

    assert "iVBORw0KGgoAAAANSUhEUgAAAAE=" not in rendered
    assert "JVBERi0xLjQK" not in rendered
    assert "data:image/png;base64" not in rendered
    assert "data:application/pdf;base64" not in rendered
    assert "cached_file" in rendered
    assert 'file_id=\\"file_image123\\"' in rendered
    assert 'file_id=\\"file_pdf123\\"' in rendered
    assert 'mime=\\"image/png\\"' in rendered
    assert 'mime=\\"application/pdf\\"' in rendered
    assert "Inspect this file" in rendered


def test_audit_json_dump_sanitizes_multimodal_messages():
    from services.multimodal_log_redaction import (
        clear_multimodal_data_uri_references,
        register_multimodal_data_uri_reference,
    )
    from services.audit_recorder import _json_dumps_safe

    clear_multimodal_data_uri_references()
    raw_pdf = "JVBERi0xLjQK"
    pdf_data_uri = f"data:application/pdf;base64,{raw_pdf}"
    register_multimodal_data_uri_reference(
        pdf_data_uri,
        file_id="file_audit123",
        mime_type="application/pdf",
        file_name="audit.pdf",
        file_size=9,
    )
    rendered = _json_dumps_safe(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "review"},
                    {"type": "file", "file": {"file_data": pdf_data_uri}},
                ],
            }
        ]
    )

    assert rendered is not None
    assert raw_pdf not in rendered
    assert "data:application/pdf;base64" not in rendered
    assert 'file_id=\\"file_audit123\\"' in rendered
    assert 'mime=\\"application/pdf\\"' in rendered
    assert "review" in rendered
