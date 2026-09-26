"""Tests that RingClient builds the documented request shapes.

None of these tests touch the network: `urllib.request.urlopen` is patched.
Per SPEC.md, this app never calls a live Ring host
because no simulator is documented, so what matters here is that the request
we *would* send matches the documented API shape, not that a live call
succeeds.
"""

import json
from unittest.mock import MagicMock, patch

from threshold.ring_client import RingClient


def _mock_response(body: dict):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(body).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False
    return mock_resp


@patch("threshold.ring_client.urllib.request.urlopen")
def test_fetch_event_history_hits_documented_path_with_bearer_auth(mock_urlopen):
    mock_urlopen.return_value = _mock_response({"data": []})
    client = RingClient(access_token="tok-123")

    client.fetch_event_history("device-1", event_types=["motion.human", "ding"])

    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.full_url.startswith(
        "https://api.amazonvision.com/v1/history/devices/device-1/events?"
    )
    assert "event_types=motion.human%2Cding" in sent_request.full_url
    assert sent_request.get_header("Authorization") == "Bearer tok-123"
    assert sent_request.get_method() == "GET"


@patch("threshold.ring_client.urllib.request.urlopen")
def test_request_clip_download_is_a_post_with_json_body(mock_urlopen):
    mock_urlopen.return_value = _mock_response({"status": "processing"})
    client = RingClient(access_token="tok-123")

    client.request_clip_download("device-1", "2026-09-22T08:00:00Z", "2026-09-22T08:05:00Z")

    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.get_method() == "POST"
    assert sent_request.full_url == "https://api.amazonvision.com/v1/devices/device-1/media/video/download"
    body = json.loads(sent_request.data)
    assert body == {"start_time": "2026-09-22T08:00:00Z", "end_time": "2026-09-22T08:05:00Z"}


@patch("threshold.ring_client.urllib.request.urlopen")
def test_pause_integration_is_a_patch_to_documented_endpoint(mock_urlopen):
    mock_urlopen.return_value = _mock_response({"status": "awaiting"})
    client = RingClient(access_token="tok-123")

    client.pause_integration()

    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.get_method() == "PATCH"
    assert sent_request.full_url == "https://api.amazonvision.com/v1/accounts/me/app-integrations"


def test_base_url_matches_documented_ring_api_host():
    # This is the one place a change to the base URL would be caught.
    client = RingClient(access_token="tok-123")
    assert client.base_url == "https://api.amazonvision.com"
