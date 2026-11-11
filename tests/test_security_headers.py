"""Security headers and the request size limit (set in the app because Render
serves it without the nginx in deploy/)."""


def test_security_headers_on_every_response(client):
    for path in ("/auth/login", "/api/v1/health", "/does-not-exist"):
        response = client.get(path)
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_hsts_only_over_https(client):
    assert "Strict-Transport-Security" not in client.get("/auth/login").headers
    secure = client.get("/auth/login", base_url="https://localhost")
    assert secure.headers["Strict-Transport-Security"].startswith("max-age=31536000")


def test_form_embed_page_can_be_framed(client):
    response = client.get("/forms/no-such-token/embed")
    assert "X-Frame-Options" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_oversized_request_is_rejected(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 1024
    response = client.post(
        "/api/v1/n8n/listings/import",
        data=b"x" * 2048,
        content_type="application/json",
        headers={"X-N8N-Secret": app.config["N8N_MASTER_SECRET"]},
    )
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "payload_too_large"
