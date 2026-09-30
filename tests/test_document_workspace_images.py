"""Image inputs retain workspace controls and use the vision content contract."""

from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from my_agents.document_workspace.formats import (
    CERTIFIED_ARTIFACT_EXTENSIONS,
    document_format_for_filename,
)
from my_agents.document_workspace.provider import OpenAIDocumentWorkspaceProvider
from my_agents.settings import Settings, get_settings

from .test_document_workspace import _client, _signup_login


def image_bytes(format_name="PNG", *, animated=False):
    buffer = BytesIO()
    first = Image.new("RGB", (8, 8), "red")
    options = {}
    if animated:
        options = {
            "save_all": True,
            "append_images": [Image.new("RGB", (8, 8), "blue")],
            "duration": 100,
            "loop": 0,
        }
    first.save(buffer, format=format_name, **options)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "extension,format_name,mime",
    [
        (".jpg", "JPEG", "image/jpeg"),
        (".jpeg", "JPEG", "image/jpeg"),
        (".png", "PNG", "image/png"),
        (".webp", "WEBP", "image/webp"),
        (".gif", "GIF", "image/gif"),
    ],
)
def test_image_upload_and_workspace_execution_preserve_original_bytes(
    monkeypatch, extension, format_name, mime
):
    client, provider = _client(monkeypatch)
    _signup_login(client)
    formats = client.get("/capabilities/document-workspace").json()["formats"]
    image_formats = {item["extension"]: item for item in formats if item["category"] == "image"}
    assert set(image_formats) == {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    assert image_formats[extension]["analysis_supported"] is True
    assert image_formats[extension]["mime_types"] == [mime]
    assert image_formats[extension]["artifact_status"] == "unavailable"
    assert extension not in CERTIFIED_ARTIFACT_EXTENSIONS
    conversation_id = client.post("/conversations", json={"title": "Image"}).json()["id"]
    data = image_bytes(format_name)
    uploaded = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={"file": (f"picture{extension.upper()}", BytesIO(data), mime)},
        data={"provider_consent": "true"},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    assert attachment["category"] == "image" and attachment["content_type"] == mime
    assert attachment["extension"] == extension
    assert list(provider.uploaded.values()) == [data]
    client.patch("/assistant/preferences", json={"assistant_model": "gpt-6-astra"})
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Describe the picture", "attachment_ids": [attachment["id"]]},
    )
    assert response.status_code == 200, response.text
    assert provider.image_file_ids == ["file-1"]
    assert response.json()["assistant_model"] == "gpt-5.6-sol"


def test_generic_mime_image_is_detected_without_changing_its_bytes(monkeypatch):
    client, provider = _client(monkeypatch)
    _signup_login(client)
    conversation_id = client.post("/conversations", json={"title": "Image"}).json()["id"]
    data = image_bytes()
    response = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={"file": ("photo.png", BytesIO(data), "application/octet-stream")},
        data={"provider_consent": "true"},
    )
    assert response.status_code == 201
    assert response.json()["content_type"] == "image/png"
    assert list(provider.uploaded.values()) == [data]


@pytest.mark.parametrize(
    "filename,format_name,mime,animated",
    [
        ("image.png", None, "image/png", False),
        ("image.jpg", "PNG", "image/jpeg", False),
        ("image.jpg", "JPEG", "image/png", False),
        ("image.gif", "GIF", "image/gif", True),
        ("image.png", "PNG", "image/png", True),
        ("image.webp", "WEBP", "image/webp", True),
    ],
)
def test_invalid_mismatched_and_animated_images_fail_before_transfer(
    monkeypatch, filename, format_name, mime, animated
):
    client, provider = _client(monkeypatch)
    _signup_login(client)
    conversation_id = client.post("/conversations", json={"title": "Image"}).json()["id"]
    data = b"not an image" if format_name is None else image_bytes(format_name, animated=animated)
    response = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={"file": (filename, BytesIO(data), mime)},
        data={"provider_consent": "true"},
    )
    assert response.status_code == 415, response.text
    assert response.json()["code"] == "unsupported_attachment_type"
    if animated:
        assert "Animated" in response.json()["detail"]
    assert provider.uploaded == {}


def test_image_consent_and_pixel_safety_checks_precede_transfer(monkeypatch):
    client, provider = _client(monkeypatch)
    _signup_login(client)
    conversation_id = client.post("/conversations", json={"title": "Image"}).json()["id"]
    data = image_bytes()
    path = f"/conversations/{conversation_id}/attachments"
    missing_consent = client.post(path, files={"file": ("photo.png", BytesIO(data), "image/png")})
    assert missing_consent.status_code == 422
    no_consent = client.post(
        path,
        files={"file": ("photo.png", BytesIO(data), "image/png")},
        data={"provider_consent": "false"},
    )
    assert no_consent.status_code == 400
    assert no_consent.json()["code"] == "document_provider_consent_required"
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 40)
    too_many_pixels = client.post(
        path,
        files={"file": ("photo.png", BytesIO(data), "image/png")},
        data={"provider_consent": "true"},
    )
    assert too_many_pixels.status_code == 415
    assert provider.uploaded == {}


def test_guest_cannot_transfer_an_image(monkeypatch):
    from .test_guest_access_api import _guest_login

    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true")
    client, provider = _client(monkeypatch)
    _guest_login(client)
    conversation_id = client.post("/conversations", json={"title": "Guest image"}).json()["id"]
    response = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={"file": ("photo.png", BytesIO(image_bytes()), "image/png")},
        data={"provider_consent": "true"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "guest_document_workspace_forbidden"
    assert provider.uploaded == {}


def test_image_byte_limit_and_foreign_attachment_cannot_reach_provider(monkeypatch):
    from .test_conversations_api import _signup_login as register_user

    client, provider = _client(monkeypatch)
    _signup_login(client)
    conversation_id = client.post("/conversations", json={"title": "Private image"}).json()["id"]
    data = image_bytes()
    path = f"/conversations/{conversation_id}/attachments"
    attachment = client.post(
        path,
        files={"file": ("photo.png", BytesIO(data), "image/png")},
        data={"provider_consent": "true"},
    ).json()
    other, _other_provider = _client(monkeypatch)
    register_user(other, "other-image@example.com")
    other_conversation = other.post("/conversations", json={"title": "Other"}).json()["id"]
    unauthorized = other.post(
        f"/conversations/{other_conversation}/runs",
        json={"message": "Read it", "attachment_ids": [attachment["id"]]},
    )
    assert unauthorized.status_code == 404
    assert not hasattr(provider, "image_file_ids")
    monkeypatch.setenv("MY_AGENTS_DOCUMENT_WORKSPACE_MAX_COMBINED_BYTES", "1024")
    get_settings.cache_clear()
    oversized = client.post(
        path,
        files={"file": ("photo.png", BytesIO(b"x" * 1025), "image/png")},
        data={"provider_consent": "true"},
    )
    assert oversized.status_code == 413
    assert len(provider.uploaded) == 1


def test_mixed_image_document_turn_preserves_document_routing(monkeypatch):
    client, provider = _client(monkeypatch)
    _signup_login(client)
    conversation_id = client.post("/conversations", json={"title": "Mixed"}).json()["id"]
    ids = []
    for filename, data, mime in (
        ("picture.png", image_bytes(), "image/png"),
        ("notes.txt", b"Supporting notes", "text/plain"),
    ):
        uploaded = client.post(
            f"/conversations/{conversation_id}/attachments",
            files={"file": (filename, BytesIO(data), mime)},
            data={"provider_consent": "true"},
        )
        assert uploaded.status_code == 201
        ids.append(uploaded.json()["id"])
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Compare picture with notes", "attachment_ids": ids},
    )
    assert response.status_code == 200
    assert provider.image_file_ids == ["file-1"]
    assert len(provider.uploaded) == 2


def test_openai_adapter_preserves_image_expiry_and_uses_input_image_in_mixed_request():
    calls = {}

    class Files:
        def create(self, **kwargs):
            calls["upload"] = kwargs
            return SimpleNamespace(id="image-1", bytes=10, filename="image.png")

    class Responses:
        def create(self, **kwargs):
            calls["response"] = kwargs
            return SimpleNamespace(
                id="response-1", output_text="Image analysis", output=[], usage={}
            )

    provider = OpenAIDocumentWorkspaceProvider(
        Settings(_env_file=None, OPENAI_API_KEY="test-only-key"),
        client=SimpleNamespace(files=Files(), responses=Responses()),
    )
    provider.upload_file(
        file=BytesIO(b"image data"),
        filename="image.png",
        content_type="image/png",
        expires_after_seconds=3600,
    )
    assert calls["upload"]["purpose"] == "user_data"
    assert calls["upload"]["expires_after"] == {"anchor": "created_at", "seconds": 3600}
    result = provider.execute(
        container_id="container-1",
        provider_file_ids=["doc-1", "image-1", "doc-2", "image-2"],
        image_file_ids=["image-1", "image-2"],
        instructions="Untrusted inputs",
        prompt="Compare",
        safety_identifier="safety-id",
        reasoning_mode="standard",
        reasoning_effort="medium",
    )
    assert calls["response"]["input"][0]["content"] == [
        {"type": "input_text", "text": "Compare"},
        {"type": "input_file", "file_id": "doc-1"},
        {"type": "input_image", "file_id": "image-1", "detail": "high"},
        {"type": "input_file", "file_id": "doc-2"},
        {"type": "input_image", "file_id": "image-2", "detail": "high"},
    ]
    assert calls["response"]["store"] is False
    assert result.shell_used is False


def test_image_extensions_do_not_add_image_generation_or_download_certification():
    for extension in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        assert document_format_for_filename(f"file{extension}").artifact_status == "unavailable"
