"""Contracts for BaseAgent — these are the invariants that Phase 13 depends on.

Breaking one of these means feedback loops, memory compaction, and cross-model
skeptic cannot be trusted. Change them deliberately, not accidentally.
"""

from src.agents.base import AgentResult, BaseAgent


def test_confidence_defaults_to_unknown_not_high():
    """Agents that forget the CONFIDENCE tag must NOT be treated as confident.

    Prior default was 'high' — which silently laundered every parser miss
    into high-confidence signal. Phase 13 outcome feedback would reinforce
    agents that happened to get away with skipping the tag. Fail open.
    """
    assert AgentResult(success=True, message="no tag here").confidence == "unknown"


def test_extract_confidence_missing_returns_unknown():
    assert BaseAgent._extract_confidence("some output without the tag") == "unknown"


def test_extract_confidence_high():
    assert BaseAgent._extract_confidence("did the thing [CONFIDENCE: high]") == "high"


def test_extract_confidence_medium():
    assert BaseAgent._extract_confidence("[CONFIDENCE: medium]") == "medium"


def test_extract_confidence_low():
    assert BaseAgent._extract_confidence("hedging [CONFIDENCE: low]") == "low"


def test_extract_confidence_case_insensitive():
    assert BaseAgent._extract_confidence("[confidence: HIGH]") == "high"
