import pytest

from threshold.webhook import DeliveryDeduplicator, InvalidSignature, sign, verify_signature


def test_sign_is_deterministic():
    body = b'{"request_id":"a"}'
    assert sign("secret", body) == sign("secret", body)


def test_verify_signature_accepts_correct_signature():
    body = b'{"request_id":"a"}'
    sig = sign("secret", body)
    verify_signature("secret", body, sig)  # should not raise


def test_verify_signature_rejects_wrong_secret():
    body = b'{"request_id":"a"}'
    sig = sign("wrong-secret", body)
    with pytest.raises(InvalidSignature):
        verify_signature("secret", body, sig)


def test_verify_signature_rejects_tampered_body():
    body = b'{"request_id":"a"}'
    sig = sign("secret", body)
    with pytest.raises(InvalidSignature):
        verify_signature("secret", b'{"request_id":"b"}', sig)


def test_verify_signature_rejects_missing_signature():
    body = b'{"request_id":"a"}'
    with pytest.raises(InvalidSignature):
        verify_signature("secret", body, "")


def test_deduplicator_flags_repeat_request_id():
    dedup = DeliveryDeduplicator()
    assert dedup.seen_before("evt-1") is False
    dedup.mark_seen("evt-1")
    assert dedup.seen_before("evt-1") is True
    assert dedup.seen_before("evt-2") is False


def test_deduplicator_evicts_after_ttl(monkeypatch):
    import threshold.webhook as webhook_mod

    times = iter([1000.0, 1000.0, 1000.0 + 100])
    monkeypatch.setattr(webhook_mod.time, "time", lambda: next(times))

    dedup = DeliveryDeduplicator(ttl_seconds=50)
    dedup.mark_seen("evt-1")  # recorded at t=1000
    assert dedup.seen_before("evt-1") is True  # checked at t=1000, still fresh
    assert dedup.seen_before("evt-1") is False  # checked at t=1100, expired
