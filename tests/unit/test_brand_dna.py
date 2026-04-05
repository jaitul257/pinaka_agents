"""Tests for BrandDNA loader (Phase 6.1)."""

from unittest.mock import MagicMock, patch

import pytest

from src.marketing import brand_dna
from src.marketing.brand_dna import BrandDNA, _parse_design_md, load


@pytest.fixture(autouse=True)
def _reset_cache():
    brand_dna._reset_cache_for_tests()
    yield
    brand_dna._reset_cache_for_tests()


# ── _parse_design_md ──


def test_parse_design_md_extracts_palette_and_anti_patterns():
    """Regex extract handles the actual DESIGN.md shape."""
    sample = """# Design System

## Color
| Token | Hex | Role |
|-------|-----|------|
| `--bg` | `#FAF7F2` | Page background |
| `--accent` | `#D4A017` | Primary accent |

### Anti-Patterns (never use)
- Auto-rotating hero carousels
- "Timeless elegance" / "crafted for you" empty luxury copy
- Purple/violet gradients

## Motion
- Approach: Minimal
"""
    palette, anti_patterns = _parse_design_md(sample)
    assert palette == {"--bg": "#FAF7F2", "--accent": "#D4A017"}
    assert "Auto-rotating hero carousels" in anti_patterns
    assert any("Timeless elegance" in p for p in anti_patterns)
    assert "Purple/violet gradients" in anti_patterns
    # Must stop at next heading — no "Motion" lines
    assert not any("Minimal" in p for p in anti_patterns)


def test_parse_design_md_empty_when_no_anti_patterns_section():
    """No ### Anti-Patterns header → empty list."""
    sample = "# Just a color\n| `--bg` | `#FFFFFF` | bg |"
    palette, anti_patterns = _parse_design_md(sample)
    assert palette == {"--bg": "#FFFFFF"}
    assert anti_patterns == []


# ── load() ──


def test_load_parses_real_design_md():
    """The actual repo DESIGN.md must parse without error and yield a non-empty palette."""
    with patch("src.marketing.brand_dna.Database") as MockDB:
        mock_db = MagicMock()
        mock_db._client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        MockDB.return_value = mock_db
        dna = load(force_refresh=True)

    assert isinstance(dna, BrandDNA)
    assert dna.palette, "Real DESIGN.md must yield at least one palette entry"
    assert "beautiful" in dna.banned_words
    assert "Ships in 15 business days" in dna.required_phrases
    assert dna.content_hash
    assert dna.source_mtime > 0


def test_load_pulls_voice_examples_from_db():
    """Voice examples RAG query hits the voice_examples table with was_edited=True filter."""
    with patch("src.marketing.brand_dna.Database") as MockDB:
        mock_db = MagicMock()
        # Build the chained mock for supabase-py fluent API
        chain = (
            mock_db._client.table.return_value
            .select.return_value
            .eq.return_value
            .order.return_value
            .limit.return_value
            .execute.return_value
        )
        chain.data = [
            {"edited_draft": "Handcrafted with love in our atelier."},
            {"edited_draft": "Ships in 15 business days, insured."},
        ]
        MockDB.return_value = mock_db

        dna = load(force_refresh=True)

    assert len(dna.voice_examples) == 2
    assert "Handcrafted with love" in dna.voice_examples[0]
    # Verify the query chain called the right table + filter
    mock_db._client.table.assert_called_with("voice_examples")


def test_load_is_cached_on_second_call():
    """Second call with unchanged mtime returns the same object (by id)."""
    with patch("src.marketing.brand_dna.Database") as MockDB:
        mock_db = MagicMock()
        mock_db._client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        MockDB.return_value = mock_db

        dna1 = load(force_refresh=True)
        dna2 = load()  # no force

    assert dna1 is dna2


def test_load_missing_design_md_raises_runtime_error():
    """If DESIGN.md is deleted/moved, load() must fail loudly (not return empty DNA)."""
    from pathlib import Path

    with patch("src.marketing.brand_dna._DESIGN_MD_PATH", Path("/nonexistent/DESIGN.md")):
        with pytest.raises(RuntimeError, match="DESIGN.md not found"):
            load(force_refresh=True)


def test_as_prompt_context_includes_banned_and_required():
    """The prompt context block includes all brand rules."""
    dna = BrandDNA(
        tone="Warm and confident",
        palette={"--accent": "#D4A017"},
        banned_words=["beautiful"],
        banned_phrases=["crafted for you"],
        required_phrases=["Ships in 15 business days"],
        voice_examples=["Handcrafted with love."],
    )
    context = dna.as_prompt_context()
    assert "Warm and confident" in context
    assert "beautiful" in context
    assert "crafted for you" in context
    assert "Ships in 15 business days" in context
    assert "Handcrafted with love" in context
