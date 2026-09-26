"""A client against the documented Ring API shapes.

Real base URL, real auth header, real endpoint paths and JSON:API response
handling. Never pointed at a live host in this app's tests or demo: no Ring
simulator is documented anywhere, real testing needs a US-located device on a
paid subscription, clip retrieval needs continuous recording, and event
history has no backfill. The boundary is
explicit: this class exists so the code exercises the real request shapes,
but the demo replays signed fixture webhooks instead (see replay.py) rather
than calling `base_url`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class RingAPIError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Ring API error {status}: {body}")
        self.status = status
        self.body = body


@dataclass
class RingClient:
    """Server-to-server Ring API client.

    Ring's CORS policy blocks browser calls, so this is server-side only,
    matching the documented "all calls must be server to server" rule.
    """

    access_token: str
    base_url: str = "https://api.amazonvision.com"
    timeout_seconds: float = 10.0

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request("GET", f"{self.base_url}{path}", params=params)

    def _request(
        self,
        method: str,
        url: str,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise RingAPIError(exc.code, exc.read().decode("utf-8", "replace")) from exc

    def fetch_event_history(
        self,
        device_id: str,
        event_types: list[str] | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """GET /v1/history/devices/{device_id}/events. No backfill, see module docstring."""
        params: dict[str, str] = {}
        if event_types:
            params["event_types"] = ",".join(event_types)
        if cursor:
            params["cursor"] = cursor
        return self._get(f"/v1/history/devices/{device_id}/events", params or None)

    def request_clip_download(self, device_id: str, start: str, end: str) -> dict[str, Any]:
        """POST .../media/video/download. Needs a continuous-recording plan; unused by
        the core flow, which works from event metadata alone on any plan."""
        return self._request(
            "POST",
            f"{self.base_url}/v1/devices/{device_id}/media/video/download",
            body={"start_time": start, "end_time": end},
        )

    def pause_integration(self) -> dict[str, Any]:
        """PATCH /v1/accounts/me/app-integrations, the only lifecycle lever a
        partner has; there is no API to change a user's device selection."""
        return self._request(
            "PATCH", f"{self.base_url}/v1/accounts/me/app-integrations", body={"status": "awaiting"}
        )
