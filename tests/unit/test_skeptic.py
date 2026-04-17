"""Contracts for Phase 13.3 — cross-model skeptic.

The critical behaviors to lock in:
  1. Soft-fail: reviewer outage or bad JSON must NOT block sends.
  2. Asymmetric rubric is in the prompt — not implemented in code, but
     the prompt text must reference the +5/−10 framing.
  3. Verdict values are constrained — {pass, revise, block}.
  4. Rubric score is clamped to [-10, +5].
"""

from unittest.mock import patch

import pytest

from src.agents import skeptic


# ── System prompt carries the asymmetric rubric ──

def test_system_prompt_has_asymmetric_rubric():
    """Prompt MUST tell the reviewer about asymmetric scoring — that's the
    whole mechanism preventing over-rejection."""
    prompt = skeptic.SYSTEM_PROMPT
    assert "+5" in prompt
    assert "−10" in prompt or "-10" in prompt
    assert "reject a clean draft" in prompt.lower() or "unnecessary" in prompt.lower()


def test_system_prompt_names_pinaka_voice_rules():
    """If the prompt forgets brand constraints, the reviewer will happily
    approve off-voice drafts. These are non-negotiable."""
    prompt = skeptic.SYSTEM_PROMPT.lower()
    assert "jaitul" in prompt                  # sign-off
    assert "15 business days" in prompt         # lead time
    assert "never: mention ai" in prompt or "never: discount" in prompt


def test_system_prompt_forbids_preamble():
    """JSON-only output is load-bearing for parsing."""
    assert "ONLY this JSON" in skeptic.SYSTEM_PROMPT or "no preamble" in skeptic.SYSTEM_PROMPT


# ── Review parsing ──

def test_parse_review_strict_json():
    raw = '{"verdict": "pass", "findings": [], "score": 5, "rationale": "fine"}'
    out = skeptic._parse_review(raw)
    assert out["verdict"] == "pass"
    assert out["score"] == 5
    assert out["rationale"] == "fine"


def test_parse_review_extracts_embedded_json():
    """If GPT pre-ambles, we still extract the first JSON object."""
    raw = 'Here is my review: {"verdict": "block", "findings": ["fabricated ETA"], "score": 4, "rationale": "invented tracking"}'
    out = skeptic._parse_review(raw)
    assert out["verdict"] == "block"
    assert out["findings"] == ["fabricated ETA"]


def test_parse_review_unparseable_falls_back_to_pass():
    """Soft-fail: we NEVER block sends because the reviewer returned garbage.
    Failing closed would make every OpenAI hiccup stop the customer service
    pipeline."""
    out = skeptic._parse_review("not json at all, just words")
    assert out["verdict"] == "pass"
    assert "unparseable" in out["rationale"]


def test_parse_review_empty_string_pass():
    out = skeptic._parse_review("")
    assert out["verdict"] == "pass"


def test_parse_review_clamps_score_above_max():
    raw = '{"verdict": "block", "findings": [], "score": 99, "rationale": "x"}'
    out = skeptic._parse_review(raw)
    assert out["score"] == 5.0  # clamped


def test_parse_review_clamps_score_below_min():
    raw = '{"verdict": "pass", "findings": [], "score": -50, "rationale": "x"}'
    out = skeptic._parse_review(raw)
    assert out["score"] == -10.0


def test_parse_review_normalizes_unknown_verdict_to_pass():
    raw = '{"verdict": "maybe", "findings": [], "score": 0, "rationale": "x"}'
    out = skeptic._parse_review(raw)
    assert out["verdict"] == "pass"


def test_parse_review_findings_not_a_list_coerced():
    raw = '{"verdict": "revise", "findings": "just one string", "score": 1, "rationale": "x"}'
    out = skeptic._parse_review(raw)
    assert out["findings"] == ["just one string"]


def test_parse_review_caps_findings_length():
    long = "x" * 1000
    raw = f'{{"verdict": "revise", "findings": ["{long}"], "score": 1, "rationale": "x"}}'
    out = skeptic._parse_review(raw)
    assert len(out["findings"][0]) <= 300


def test_parse_review_caps_findings_count():
    findings = [f'"f{i}"' for i in range(20)]
    raw = f'{{"verdict": "revise", "findings": [{",".join(findings)}], "score": 1, "rationale": "x"}}'
    out = skeptic._parse_review(raw)
    assert len(out["findings"]) <= 10


# ── Soft-fail when API key is missing ──

@pytest.mark.asyncio
async def test_review_soft_passes_without_api_key():
    """No OPENAI_API_KEY configured → PASS by default. We do not block
    customer service just because the secondary reviewer is misconfigured."""
    with patch("src.agents.skeptic.settings") as mock_settings:
        mock_settings.openai_api_key = ""
        result = await skeptic.review_customer_email_draft(
            draft_text="Hi Jane, your order is on its way. Warm regards, Jaitul at Pinaka Jewellery",
        )
    assert result.verdict == "pass"
    assert "unavailable" in result.rationale.lower()
    assert result.review_id is None


# ── Reviewer model is a different provider ──

def test_reviewer_is_not_claude():
    """The whole design point is cross-model. If someone changes this to a
    Claude model, the LLM-Council / Kamoi findings stop applying."""
    assert "gpt" in skeptic.REVIEWER_MODEL.lower() or "openai" in skeptic.REVIEWER_MODEL.lower()
    assert "claude" not in skeptic.REVIEWER_MODEL.lower()


def test_drafter_and_reviewer_are_different():
    """Paranoia: make sure we never accidentally configure the same model
    for both sides — same-model self-critique is specifically what this
    phase was designed to avoid."""
    assert skeptic.DRAFTER_MODEL != skeptic.REVIEWER_MODEL
