"""Security unit tests (Phase 10-12)."""


from app.infrastructure.security.middleware import (
    is_private_ip,
    sanitize_text_input,
    validate_business_name,
    validate_external_url,
)


def test_private_ip_detection():
    assert is_private_ip("127.0.0.1") is True
    assert is_private_ip("10.0.0.1") is True
    assert is_private_ip("192.168.1.1") is True
    assert is_private_ip("8.8.8.8") is False
    assert is_private_ip("1.1.1.1") is False


def test_ssrf_block_private_ip():
    ok, reason = validate_external_url("http://127.0.0.1/image.jpg")
    assert ok is False
    assert "private" in reason or "localhost" in reason or "not allowed" in reason

    ok, reason = validate_external_url("http://10.0.0.1/admin")
    assert ok is False

    ok, reason = validate_external_url("https://8.8.8.8/image.jpg")
    assert ok is True


def test_ssrf_block_schemes():
    ok, _ = validate_external_url("file:///etc/passwd")
    assert ok is False

    ok, _ = validate_external_url("ftp://example.com/file")
    assert ok is False

    ok, _ = validate_external_url("https://example.com/image.jpg")
    assert ok is True

    ok, _ = validate_external_url("http://example.com/image.jpg")
    assert ok is True


def test_ssrf_block_localhost():
    ok, _ = validate_external_url("http://localhost/image.jpg")
    assert ok is False


def test_sanitize_text():
    assert sanitize_text_input("hello") == "hello"
    assert sanitize_text_input("  hello  ") == "hello"
    assert "\x00" not in sanitize_text_input("hello\x00world")
    assert len(sanitize_text_input("a" * 2000, max_length=100)) == 100


def test_business_name_validation():
    ok, _ = validate_business_name("My Business")
    assert ok is True

    ok, _ = validate_business_name("کسب‌وکار من")
    assert ok is True

    ok, _ = validate_business_name("")
    assert ok is False

    ok, _ = validate_business_name("<script>alert(1)</script>")
    assert ok is False

    ok, _ = validate_business_name("javascript:alert(1)")
    assert ok is False
