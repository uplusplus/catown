from services.log_secret_codec import (
    encode_log_secrets_in_text,
    encode_secret_ascii_shift,
    encode_sensitive_headers_for_logging,
)


def test_encode_secret_ascii_shift_advances_each_character():
    assert encode_secret_ascii_shift("sk-ABC123") == "tl.BCD234"


def test_encode_log_secrets_in_json_payload():
    secret = "sk-ABC123"
    payload = (
        '{"provider":{"apiKey":"sk-ABC123"},'
        '"headers":{"authorization":"Bearer sk-ABC123"}}'
    )

    encoded = encode_log_secrets_in_text(payload)

    assert secret not in encoded
    assert '"apiKey":"tl.BCD234"' in encoded
    assert '"authorization":"Bearer tl.BCD234"' in encoded


def test_encode_log_secrets_in_plain_text_payload():
    secret = "sk-ABC123"
    text = '{"apiKey":"sk-ABC123"} Authorization: Bearer sk-ABC123 ?api_key=sk-ABC123'

    encoded = encode_log_secrets_in_text(text)

    assert secret not in encoded
    assert '"apiKey":"tl.BCD234"' in encoded
    assert "Bearer tl.BCD234" in encoded
    assert "api_key=tl.BCD234" in encoded


def test_encode_sensitive_headers_for_logging_keeps_header_with_encoded_value():
    secret = "sk-ABC123"

    encoded = encode_sensitive_headers_for_logging(
        {
            "Authorization": "Bearer sk-ABC123",
            "X-OpenAI-Api-Key": "sk-ABC123",
            "Content-Type": "application/json",
        }
    )

    assert secret not in str(encoded)
    assert encoded["Authorization"] == "Bearer tl.BCD234"
    assert encoded["X-OpenAI-Api-Key"] == "tl.BCD234"
    assert encoded["Content-Type"] == "application/json"


def test_llm_header_capture_encodes_authorization_header():
    from llm.client import _sanitize_http_headers

    encoded = _sanitize_http_headers({"Authorization": "Bearer sk-ABC123"})

    assert encoded == {"Authorization": "Bearer tl.BCD234"}
