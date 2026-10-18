"""Small synchronous R2 boundary; callers own authorization and object lifecycles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn, Protocol
from urllib.parse import quote, urlsplit

if TYPE_CHECKING:
    from my_agents.settings import Settings


class StorageUnavailable(RuntimeError):
    """Object storage failed without exposing credentials or provider response bodies."""


class StorageObjectNotFound(StorageUnavailable):
    """The requested object has not been uploaded or no longer exists."""


class StorageObjectTooLarge(StorageUnavailable):
    """Reading the object would exceed the caller's accepted byte limit."""


class ObjectStore(Protocol):
    """Private object operations; keys and access decisions remain application-owned."""

    def presign_put(
        self,
        key: str,
        *,
        content_type: str,
        expires_seconds: int,
        content_length: int | None = None,
    ) -> str: ...

    def presign_get(self, key: str, *, filename: str, expires_seconds: int) -> str: ...

    def read(self, key: str, *, max_bytes: int) -> bytes: ...

    def put(self, key: str, content: bytes, *, content_type: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def copy(self, source_key: str, destination_key: str) -> None: ...


def get_object_store(settings: Settings) -> ObjectStore:
    """Create a provider lazily, without probing R2 or consulting default AWS credentials."""
    if settings.storage_provider != "r2":
        raise StorageUnavailable("Object storage is not configured.")
    return R2ObjectStore(settings)


class R2ObjectStore:
    """S3-compatible private R2 access, with bounded retries and bounded reads."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        endpoint = settings.r2_endpoint_url
        bucket = settings.r2_bucket_name
        access_key = settings.r2_access_key_id
        secret_key = settings.r2_secret_access_key
        if not endpoint or not bucket:
            raise StorageUnavailable("Object storage is not configured.")
        _validate_endpoint(str(endpoint))
        self._bucket = bucket
        if client is not None:
            self._client = client
            return
        if not access_key or not secret_key:
            raise StorageUnavailable("Object storage is not configured.")
        try:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=str(endpoint),
                region_name="auto",
                aws_access_key_id=access_key.get_secret_value(),
                aws_secret_access_key=secret_key.get_secret_value(),
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=5,
                    read_timeout=30,
                    retries={"mode": "standard", "total_max_attempts": 3},
                    s3={"addressing_style": "path"},
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            )
        except Exception:
            raise StorageUnavailable("Object storage is unavailable.") from None

    def presign_put(
        self,
        key: str,
        *,
        content_type: str,
        expires_seconds: int,
        content_length: int | None = None,
    ) -> str:
        """Issue a reusable staging upload URL; the service must seal verified bytes."""
        _validate_key(key)
        _validate_content_type(content_type)
        _validate_expiry(expires_seconds)
        if content_length is not None:
            _validate_byte_limit(content_length)
        params: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": key,
            "ContentType": content_type,
        }
        if content_length is not None:
            params["ContentLength"] = content_length
        return self._presign("put_object", params=params, expires_seconds=expires_seconds)

    def presign_get(self, key: str, *, filename: str, expires_seconds: int) -> str:
        _validate_key(key)
        _validate_expiry(expires_seconds)
        # Never turn an original filename into a response header or inline HTML response.
        leaf = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        leaf = "".join(
            character for character in leaf if ord(character) >= 32 and ord(character) != 127
        )
        encoded_filename = quote(leaf or "download", safe="")
        disposition = f"attachment; filename=\"download\"; filename*=UTF-8''{encoded_filename}"
        return self._presign(
            "get_object",
            params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": disposition,
                "ResponseContentType": "application/octet-stream",
                "ResponseCacheControl": "private, no-store",
            },
            expires_seconds=expires_seconds,
        )

    def read(self, key: str, *, max_bytes: int) -> bytes:
        _validate_key(key)
        _validate_byte_limit(max_bytes)
        body = None
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            content_length = response.get("ContentLength")
            if content_length is not None:
                if not isinstance(content_length, int) or isinstance(content_length, bool):
                    raise StorageUnavailable("Object storage returned an invalid response.")
                if content_length < 0:
                    raise StorageUnavailable("Object storage returned an invalid response.")
                if content_length > max_bytes:
                    raise StorageObjectTooLarge("The stored object exceeds the accepted size.")
            parts: list[bytes] = []
            received = 0
            while True:
                chunk = body.read(min(64 * 1024, max_bytes - received + 1))
                if not isinstance(chunk, bytes):
                    raise StorageUnavailable("Object storage returned an invalid response.")
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise StorageObjectTooLarge("The stored object exceeds the accepted size.")
                parts.append(chunk)
            if content_length is not None and received != content_length:
                raise StorageUnavailable("The stored object could not be read completely.")
            return b"".join(parts)
        except StorageUnavailable:
            raise
        except Exception as exc:
            _raise_storage_error(exc)
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    # Do not replace a meaningful bounded-read error with provider details.
                    pass

    def put(self, key: str, content: bytes, *, content_type: str) -> None:
        _validate_key(key)
        _validate_content_type(content_type)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentLength=len(content),
                ContentType=content_type,
                ContentDisposition="attachment",
                CacheControl="private, no-store",
            )
        except Exception as exc:
            _raise_storage_error(exc)

    def delete(self, key: str) -> None:
        _validate_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _is_missing(exc):
                return
            _raise_storage_error(exc)

    def copy(self, source_key: str, destination_key: str) -> None:
        """Copy a sealed original, never a browser-writable staging object."""
        _validate_key(source_key)
        _validate_key(destination_key)
        try:
            self._client.copy_object(
                Bucket=self._bucket,
                Key=destination_key,
                CopySource={"Bucket": self._bucket, "Key": source_key},
            )
        except Exception as exc:
            _raise_storage_error(exc)

    def _presign(self, operation: str, *, params: dict[str, object], expires_seconds: int) -> str:
        try:
            return self._client.generate_presigned_url(
                operation,
                Params=params,
                ExpiresIn=expires_seconds,
            )
        except Exception as exc:
            _raise_storage_error(exc)


def _is_missing(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    return isinstance(error, dict) and error.get("Code") in {"NoSuchKey", "NotFound", "404"}


def _raise_storage_error(exc: Exception) -> NoReturn:
    if _is_missing(exc):
        raise StorageObjectNotFound("The stored object was not found.") from None
    raise StorageUnavailable("Object storage is unavailable.") from None


def _validate_endpoint(endpoint: str) -> None:
    try:
        parsed = urlsplit(endpoint)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.endswith(".r2.cloudflarestorage.com")
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise StorageUnavailable("Object storage requires a private R2 HTTPS endpoint.")


def _validate_key(key: str) -> None:
    if (
        not key
        or len(key.encode("utf-8")) > 1024
        or any(part in {"", ".", ".."} for part in key.split("/"))
        or any(ord(character) < 32 or ord(character) == 127 for character in key)
    ):
        raise ValueError("An application-generated object key is required.")


def _validate_content_type(content_type: str) -> None:
    if (
        not content_type
        or len(content_type) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in content_type)
    ):
        raise ValueError("A valid object content type is required.")


def _validate_expiry(expires_seconds: int) -> None:
    if (
        not isinstance(expires_seconds, int)
        or isinstance(expires_seconds, bool)
        or not 1 <= expires_seconds <= 604800
    ):
        raise ValueError("Presigned URL expiry must be between one second and seven days.")


def _validate_byte_limit(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("An object byte limit must be a non-negative integer.")
