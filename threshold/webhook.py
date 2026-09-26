"""Ring webhook signature verification and delivery dedup.

Per the documented Ring webhook contract: payloads are signed HMAC-SHA256 and
carried in an `X-Signature` header, the receiving endpoint must return 200
within 5 seconds, and the receiver is responsible for deduplicating on
`request_id` because Ring may redeliver.

This module implements the real verification scheme. It is used both by the
demo webhook receiver (`server.py`) and by the fixture replayer
(`replay.py`), which signs fixtures with a shared secret before "delivering"
them, so the same code path that would validate a live Ring webhook is what
validates the replayed one.
"""

from __future__ import annotations

import hashlib
import hmac
import time


class InvalidSignature(ValueError):
    """Raised when an inbound payload's signature does not match."""


def sign(secret: str, raw_body: bytes) -> str:
    """Compute the X-Signature value Ring would send for `raw_body`."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, raw_body: bytes, signature: str) -> None:
    """Raise InvalidSignature unless `signature` matches `raw_body`.

    Uses constant-time comparison, since this is exactly the kind of check
    that must not leak timing information about the correct signature.
    """
    expected = sign(secret, raw_body)
    if not hmac.compare_digest(expected, signature or ""):
        raise InvalidSignature("signature does not match payload")


class DeliveryDeduplicator:
    """Tracks seen `request_id`s so a redelivered webhook is a no-op.

    In-memory and unbounded-but-time-boxed is fine for a hackathon demo; a
    production integration would back this with the same store as the event
    log so dedup survives a restart.
    """

    def __init__(self, ttl_seconds: int = 24 * 60 * 60) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def seen_before(self, request_id: str) -> bool:
        self._evict_expired()
        return request_id in self._seen

    def mark_seen(self, request_id: str) -> None:
        self._seen[request_id] = time.time()

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [rid for rid, ts in self._seen.items() if now - ts > self._ttl]
        for rid in expired:
            del self._seen[rid]
