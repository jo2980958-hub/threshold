import json

from threshold.app import ThresholdApp
from threshold.webhook import sign


def _signed(secret, payload):
    raw = json.dumps(payload).encode("utf-8")
    return raw, sign(secret, raw)


def _event_payload(request_id, hour=8, minute=5, event_type="motion_detected", sub_type="human"):
    return {
        "request_id": request_id,
        "device_id": "front_door_1",
        "event_type": event_type,
        "created_at": f"2026-09-22T{hour:02d}:{minute:02d}:00Z",
        "attributes": {"sub_type": sub_type} if sub_type else {},
    }


def test_handle_webhook_accepts_correctly_signed_event():
    app = ThresholdApp(webhook_secret="secret")
    raw, sig = _signed("secret", _event_payload("evt-1"))

    status, body = app.handle_webhook(raw, sig)

    assert status == 200
    assert body["status"] == "recorded"
    assert len(app.log) == 1


def test_handle_webhook_rejects_bad_signature():
    app = ThresholdApp(webhook_secret="secret")
    raw, _ = _signed("secret", _event_payload("evt-1"))

    status, body = app.handle_webhook(raw, "not-the-right-signature")

    assert status == 401
    assert len(app.log) == 0


def test_handle_webhook_deduplicates_redelivered_request_id():
    app = ThresholdApp(webhook_secret="secret")
    raw, sig = _signed("secret", _event_payload("evt-1"))

    first = app.handle_webhook(raw, sig)
    second = app.handle_webhook(raw, sig)

    assert first[1]["status"] == "recorded"
    assert second[1]["status"] == "duplicate, ignored"
    assert len(app.log) == 1


def test_handle_webhook_rejects_malformed_payload():
    app = ThresholdApp(webhook_secret="secret")
    payload = _event_payload("evt-1")
    del payload["device_id"]
    raw = json.dumps(payload).encode("utf-8")
    sig = sign("secret", raw)

    status, body = app.handle_webhook(raw, sig)

    assert status == 400
    assert "malformed" in body["error"]


def test_handle_webhook_ignores_non_door_event_types_but_still_accepts():
    app = ThresholdApp(webhook_secret="secret")
    raw, sig = _signed("secret", _event_payload("evt-1", event_type="device_online", sub_type=None))

    status, body = app.handle_webhook(raw, sig)

    assert status == 200
    assert body["status"] == "ignored (not door activity)"
    assert len(app.log) == 0


def test_events_persist_across_app_instances_via_sqlite(tmp_path):
    db_path = tmp_path / "threshold.db"

    app1 = ThresholdApp(webhook_secret="secret", db_path=str(db_path))
    raw, sig = _signed("secret", _event_payload("evt-1"))
    app1.handle_webhook(raw, sig)
    app1.store.close()

    app2 = ThresholdApp(webhook_secret="secret", db_path=str(db_path))
    assert len(app2.log) == 1
