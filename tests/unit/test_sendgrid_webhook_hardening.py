"""Contracts for Phase 13.1 / 13.3 — SendGrid webhook signature verification
and custom_args attribution.

Two things these tests lock in:
  1. verify_sendgrid_signature NEVER raises. Any crypto exception = reject.
     A crash-on-bad-input handler is worse than a reject — it exposes
     FastAPI's default 500 page to an attacker.
  2. _build_email_context produces a SendGrid-friendly dict (or None),
     never an empty dict that gets shipped. Empty custom_args would waste
     bytes + confuse downstream consumers.
"""

from src.agents.outcomes import verify_sendgrid_signature
from src.core.email import _build_email_context


# ── Signature verification is defensive ──

def test_verify_rejects_missing_signature():
    assert verify_sendgrid_signature(b"body", "", "1700000000", "key") is False


def test_verify_rejects_missing_timestamp():
    assert verify_sendgrid_signature(b"body", "sig", "", "key") is False


def test_verify_rejects_missing_public_key():
    assert verify_sendgrid_signature(b"body", "sig", "1700000000", "") is False


def test_verify_rejects_garbage_signature_no_raise():
    """Garbage inputs must never crash the handler."""
    result = verify_sendgrid_signature(
        b"body", "not-valid-base64!@#$", "1700000000", "also-garbage",
    )
    assert result is False


def test_verify_rejects_short_random_key():
    """Valid-looking base64 that isn't actually a key → false, no crash."""
    result = verify_sendgrid_signature(
        b"body", "c2lnbmF0dXJl", "1700000000", "bm90LWEta2V5Cg==",
    )
    assert result is False


# ── Real ECDSA signature happy-path ──

def test_verify_accepts_real_ecdsa_signature():
    """Round-trip: sign a payload with a generated P-256 key and verify."""
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    # Serialize public key the way SendGrid ships it (PEM → base64)
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_b64 = base64.b64encode(pem).decode("ascii")

    payload = b'[{"event":"delivered","email":"x@y.com"}]'
    timestamp = "1700000000"
    signature = private_key.sign(
        timestamp.encode("utf-8") + payload,
        ec.ECDSA(hashes.SHA256()),
    )
    signature_b64 = base64.b64encode(signature).decode("ascii")

    assert verify_sendgrid_signature(payload, signature_b64, timestamp, public_key_b64) is True


def test_verify_rejects_tampered_payload():
    """Same key, same signature, but body mutated → reject."""
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_b64 = base64.b64encode(pem).decode("ascii")

    payload = b"original"
    timestamp = "1700000000"
    sig = private_key.sign(timestamp.encode() + payload, ec.ECDSA(hashes.SHA256()))
    sig_b64 = base64.b64encode(sig).decode()

    tampered = b"tampered"
    assert verify_sendgrid_signature(tampered, sig_b64, timestamp, public_key_b64) is False


# ── _build_email_context: None instead of empty dict ──

def test_build_context_returns_none_when_nothing_provided():
    assert _build_email_context() is None


def test_build_context_returns_none_when_all_empty():
    assert _build_email_context(agent_name="", action_type="", audit_log_id=None) is None


def test_build_context_with_full_attribution():
    ctx = _build_email_context(
        agent_name="retention", action_type="lifecycle_welcome_1",
        audit_log_id="abc-123", entity_type="customer", entity_id=42,
    )
    assert ctx == {
        "agent_name": "retention",
        "action_type": "lifecycle_welcome_1",
        "audit_log_id": "abc-123",
        "entity_type": "customer",
        "entity_id": "42",
    }


def test_build_context_truncates_long_values():
    long_string = "x" * 500
    ctx = _build_email_context(agent_name="retention", action_type=long_string)
    assert len(ctx["action_type"]) <= 200


def test_build_context_skips_empty_entity_id():
    ctx = _build_email_context(agent_name="retention", action_type="welcome_1",
                                entity_id="")
    # entity_id='' should NOT appear — empty-string is semantically "unknown"
    assert "entity_id" not in ctx
    assert ctx == {"agent_name": "retention", "action_type": "welcome_1"}


def test_build_context_includes_entity_id_zero():
    """entity_id=0 is a valid id (edge case — some DBs use 0). Don't drop it."""
    ctx = _build_email_context(agent_name="retention", action_type="welcome_1",
                                entity_id=0)
    assert ctx["entity_id"] == "0"
