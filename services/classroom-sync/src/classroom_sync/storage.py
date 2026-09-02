"""Private object storage boundary for compressed evidence chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class StorageUnavailable(Exception):
    """The private evidence bucket could not accept a write."""


@dataclass(frozen=True)
class StoredObject:
    body: bytes
    content_type: str


class PrivateObjectStorage(Protocol):
    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None: ...

    def get_bytes(self, key: str) -> StoredObject: ...

    def delete_bytes(self, key: str) -> None: ...


class Boto3PrivateObjectStorage:
    """Minimal adapter that keeps evidence private by omitting every public ACL."""

    def __init__(self, client: object, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None:
        put_object = getattr(self._client, "put_object", None)
        if not callable(put_object):
            raise StorageUnavailable("s3_client_misconfigured")
        try:
            put_object(Bucket=self._bucket, Key=key, Body=body, ContentType=content_type)
        except Exception as error:  # boto3 exposes multiple transport exception classes.
            raise StorageUnavailable("s3_put_failed") from error

    def get_bytes(self, key: str) -> StoredObject:
        get_object = getattr(self._client, "get_object", None)
        if not callable(get_object):
            raise StorageUnavailable("s3_client_misconfigured")
        try:
            response = get_object(Bucket=self._bucket, Key=key)
            if not isinstance(response, dict):
                raise TypeError("s3_get_response_invalid")
            stream = response.get("Body")
            read = getattr(stream, "read", None)
            if not callable(read):
                raise TypeError("s3_get_body_invalid")
            try:
                body = read()
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            if not isinstance(body, bytes):
                raise TypeError("s3_get_body_invalid")
            content_type = response.get("ContentType")
            return StoredObject(
                body=body,
                content_type=content_type if isinstance(content_type, str) else "application/octet-stream",
            )
        except Exception as error:
            raise StorageUnavailable("s3_get_failed") from error

    def delete_bytes(self, key: str) -> None:
        delete_object = getattr(self._client, "delete_object", None)
        if not callable(delete_object):
            raise StorageUnavailable("s3_client_misconfigured")
        try:
            delete_object(Bucket=self._bucket, Key=key)
        except Exception as error:
            raise StorageUnavailable("s3_delete_failed") from error
