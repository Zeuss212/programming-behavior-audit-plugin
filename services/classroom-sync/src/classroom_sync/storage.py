"""Private object storage boundary for compressed evidence chunks."""

from __future__ import annotations

from typing import Protocol


class StorageUnavailable(Exception):
    """The private evidence bucket could not accept a write."""


class PrivateObjectStorage(Protocol):
    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None: ...


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
