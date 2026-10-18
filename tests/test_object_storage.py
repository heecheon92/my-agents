"""Offline private storage contracts, including the real SDK's signing boundary."""

from __future__ import annotations

import traceback
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
from botocore.exceptions import ClientError
from pydantic import SecretStr

from my_agents.settings import Settings
from my_agents.storage import (
    R2ObjectStore,
    StorageObjectNotFound,
    StorageObjectTooLarge,
    StorageUnavailable,
    get_object_store,
)


def storage_settings(**overrides) -> Settings:
    return Settings(_env_file=None).model_copy(
        update={
            "storage_provider": "r2",
            "r2_endpoint_url": "https://unit-test-account.r2.cloudflarestorage.com",
            "r2_bucket_name": "unit-test-files",
            "r2_access_key_id": SecretStr("test-access-key"),
            "r2_secret_access_key": SecretStr("test-secret-key"),
            **overrides,
        }
    )


class TrackedBody(BytesIO):
    def __init__(self, content: bytes, *, part_size: int | None = None) -> None:
        super().__init__(content)
        self.part_size = part_size
        self.read_limits: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_limits.append(size)
        if self.part_size is not None:
            size = min(size, self.part_size)
        return super().read(size)


class FakeS3Client:
    """In-memory SDK substitute: no disk, credentials, or cloud account needed."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.last_body: TrackedBody | None = None
        self.part_size: int | None = None
        self.reported_length: int | None = None
        self.calls: list[tuple[str, dict]] = []
        self.error: Exception | None = None

    def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error

    def get_object(self, **kwargs) -> dict:
        self._call("get_object", kwargs)
        key = kwargs["Key"]
        if key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "private key"}}, "GetObject"
            )
        content, _content_type = self.objects[key]
        self.last_body = TrackedBody(content, part_size=self.part_size)
        return {
            "Body": self.last_body,
            "ContentLength": len(content) if self.reported_length is None else self.reported_length,
        }

    def put_object(self, **kwargs) -> None:
        self._call("put_object", kwargs)
        self.objects[kwargs["Key"]] = (kwargs["Body"], kwargs["ContentType"])

    def copy_object(self, **kwargs) -> None:
        self._call("copy_object", kwargs)
        self.objects[kwargs["Key"]] = self.objects[kwargs["CopySource"]["Key"]]

    def delete_object(self, **kwargs) -> None:
        self._call("delete_object", kwargs)
        self.objects.pop(kwargs["Key"], None)

    def generate_presigned_url(self, operation, **kwargs) -> str:
        self._call(operation, kwargs)
        return "https://unit-test-account.r2.cloudflarestorage.com/test?signature=fake"


def test_private_original_can_be_copied_read_and_deleted_independently() -> None:
    client = FakeS3Client()
    store = R2ObjectStore(storage_settings(), client=client)
    content = "State 설계 원칙\nKeep exact source content.".encode()

    store.put("originals/personal/document", content, content_type="text/markdown")
    store.copy("originals/personal/document", "originals/group/document")
    store.delete("originals/personal/document")
    store.delete("originals/personal/document")

    assert store.read("originals/group/document", max_bytes=len(content)) == content
    assert client.last_body is not None and client.last_body.closed
    with pytest.raises(StorageObjectNotFound):
        store.read("originals/personal/document", max_bytes=len(content))
    assert all(call[1].get("Bucket") == "unit-test-files" for call in client.calls)
    put = client.calls[0][1]
    assert put["ContentLength"] == len(content)
    assert put["ContentDisposition"] == "attachment"
    assert put["CacheControl"] == "private, no-store"
    assert "ACL" not in put


def test_download_is_bounded_before_reading_oversized_object() -> None:
    client = FakeS3Client()
    client.objects["originals/file"] = (b"too long", "text/plain")
    store = R2ObjectStore(storage_settings(), client=client)

    with pytest.raises(StorageObjectTooLarge):
        store.read("originals/file", max_bytes=2)

    assert client.last_body is not None
    assert client.last_body.read_limits == []
    assert client.last_body.closed


def test_download_checks_actual_size_even_when_response_length_is_wrong() -> None:
    client = FakeS3Client()
    client.objects["originals/file"] = (b"123456789", "text/plain")
    client.reported_length = 1
    store = R2ObjectStore(storage_settings(), client=client)

    with pytest.raises(StorageObjectTooLarge):
        store.read("originals/file", max_bytes=3)

    assert client.last_body is not None
    assert client.last_body.read_limits == [4]
    assert client.last_body.closed


def test_download_accumulates_partial_reads_and_detects_truncation() -> None:
    client = FakeS3Client()
    client.objects["originals/file"] = (b"123456", "text/plain")
    client.part_size = 2
    store = R2ObjectStore(storage_settings(), client=client)
    assert store.read("originals/file", max_bytes=6) == b"123456"
    assert client.last_body is not None
    assert client.last_body.read_limits == [7, 5, 3, 1]
    assert client.last_body.closed

    client.reported_length = 9
    with pytest.raises(StorageUnavailable, match="completely"):
        store.read("originals/file", max_bytes=9)
    assert client.last_body.closed


def test_download_limits_individual_reads_and_accepts_empty_files() -> None:
    client = FakeS3Client()
    content = b"a" * (150 * 1024)
    client.objects["originals/file"] = (content, "text/plain")
    client.objects["originals/empty"] = (b"", "text/plain")
    store = R2ObjectStore(storage_settings(), client=client)
    assert store.read("originals/file", max_bytes=len(content)) == content
    assert client.last_body is not None
    assert max(client.last_body.read_limits) == 64 * 1024
    assert store.read("originals/empty", max_bytes=0) == b""


@pytest.mark.parametrize(
    "operation", ["read", "put", "delete", "copy", "presign_put", "presign_get"]
)
def test_provider_errors_never_reveal_credentials_or_bodies(operation: str) -> None:
    client = FakeS3Client()
    client.error = RuntimeError("sensitive credentials and provider response body")
    store = R2ObjectStore(storage_settings(), client=client)
    operations = {
        "read": lambda: store.read("originals/file", max_bytes=10),
        "put": lambda: store.put("originals/file", b"hello", content_type="text/plain"),
        "delete": lambda: store.delete("originals/file"),
        "copy": lambda: store.copy("originals/file", "originals/copy"),
        "presign_put": lambda: store.presign_put(
            "staging/file", content_type="text/plain", expires_seconds=300
        ),
        "presign_get": lambda: store.presign_get(
            "originals/file", filename="file.txt", expires_seconds=300
        ),
    }

    with pytest.raises(StorageUnavailable) as error:
        operations[operation]()

    assert str(error.value) == "Object storage is unavailable."
    formatted = "".join(traceback.format_exception(error.value))
    assert "sensitive credentials and provider response body" not in formatted


def test_missing_object_deletion_is_idempotent() -> None:
    client = FakeS3Client()
    client.error = ClientError({"Error": {"Code": "NoSuchKey"}}, "DeleteObject")
    R2ObjectStore(storage_settings(), client=client).delete("originals/gone")


def test_presigned_put_binds_content_type_and_exact_length_with_real_sdk() -> None:
    store = R2ObjectStore(storage_settings())
    url = store.presign_put(
        "staging/upload-1", content_type="text/plain", content_length=14, expires_seconds=300
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.hostname == "unit-test-account.r2.cloudflarestorage.com"
    assert parsed.path == "/unit-test-files/staging/upload-1"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["300"]
    assert query["X-Amz-SignedHeaders"] == ["content-length;content-type;host"]
    assert "test-secret-key" not in url


def test_signed_download_forces_attachment_and_preserves_unicode_filename_safely() -> None:
    store = R2ObjectStore(storage_settings())
    url = store.presign_get("originals/document", filename="../원칙\r\n.txt", expires_seconds=90)
    query = parse_qs(urlsplit(url).query)
    disposition = query["response-content-disposition"][0]

    assert (
        disposition == "attachment; filename=\"download\"; filename*=UTF-8''%EC%9B%90%EC%B9%99.txt"
    )
    assert query["response-content-type"] == ["application/octet-stream"]
    assert query["response-cache-control"] == ["private, no-store"]
    assert query["X-Amz-Expires"] == ["90"]


def test_sdk_has_explicit_credentials_endpoint_and_bounded_retry_policy(monkeypatch) -> None:
    captured: dict = {}

    def make_client(service, **kwargs):
        captured.update({"service": service, **kwargs})
        return FakeS3Client()

    monkeypatch.setattr(boto3, "client", make_client)
    store = get_object_store(storage_settings())

    assert isinstance(store, R2ObjectStore)
    assert captured["service"] == "s3"
    assert captured["region_name"] == "auto"
    assert captured["aws_access_key_id"] == "test-access-key"
    assert captured["aws_secret_access_key"] == "test-secret-key"
    config = captured["config"]
    assert config.connect_timeout == 5
    assert config.read_timeout == 30
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"


@pytest.mark.parametrize(
    "overrides",
    [
        {"storage_provider": "disabled"},
        {"r2_endpoint_url": None},
        {"r2_bucket_name": None},
        {"r2_access_key_id": None},
        {"r2_secret_access_key": None},
    ],
)
def test_disabled_or_incomplete_configuration_never_uses_aws_default_credentials(
    overrides, monkeypatch
) -> None:
    def unexpected_client(*args, **kwargs):
        pytest.fail("Missing configuration must not create an SDK client")

    monkeypatch.setattr(boto3, "client", unexpected_client)
    with pytest.raises(StorageUnavailable, match="not configured"):
        get_object_store(storage_settings(**overrides))


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://account.r2.cloudflarestorage.com",
        "https://r2.cloudflarestorage.com.attacker.test",
        "https://public.r2.dev",
        "https://user:secret@account.r2.cloudflarestorage.com",
        "https://account.r2.cloudflarestorage.com/bucket",
        "https://account.r2.cloudflarestorage.com?secret=token",
        "https://account.r2.cloudflarestorage.com#fragment",
        "https://account.r2.cloudflarestorage.com:8443",
    ],
)
def test_r2_only_accepts_private_https_api_endpoints(endpoint: str) -> None:
    with pytest.raises(StorageUnavailable, match="private R2 HTTPS endpoint"):
        R2ObjectStore(storage_settings(r2_endpoint_url=endpoint), client=FakeS3Client())


@pytest.mark.parametrize("jurisdiction", ["", ".eu", ".fedramp"])
def test_jurisdiction_specific_r2_endpoints_are_allowed(jurisdiction: str) -> None:
    R2ObjectStore(
        storage_settings(r2_endpoint_url=f"https://account{jurisdiction}.r2.cloudflarestorage.com"),
        client=FakeS3Client(),
    )


@pytest.mark.parametrize("key", ["", "/file", "parent/../file", "parent//file", "line\nfile"])
def test_unsafe_keys_never_reach_the_sdk(key: str) -> None:
    client = FakeS3Client()
    store = R2ObjectStore(storage_settings(), client=client)
    with pytest.raises(ValueError):
        store.delete(key)
    assert client.calls == []


@pytest.mark.parametrize("expiry", [0, -1, 604801, True])
def test_invalid_signed_url_expiry_is_rejected(expiry: int) -> None:
    client = FakeS3Client()
    store = R2ObjectStore(storage_settings(), client=client)
    with pytest.raises(ValueError):
        store.presign_put("staging/upload", content_type="text/plain", expires_seconds=expiry)
    assert client.calls == []
