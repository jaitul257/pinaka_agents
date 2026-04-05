"""Brand DNA loader — formalizes Pinaka's tone/voice/anti-patterns into a structured object.

Sources (single source of truth for ad creative prompts):
    1. DESIGN.md — palette, typography, anti-pattern copy phrases
    2. voice_examples Supabase table — founder-approved edits (few-shot RAG)
    3. Hard-coded rules from src/listings/generator.py SYSTEM_PROMPT (the banned "beautiful" word)

The loaded BrandDNA is cached at module level with mtime-aware invalidation — editing
DESIGN.md during a session automatically bumps the hash, and cached draft rows can
detect staleness via `brand_dna_hash` comparison.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.database import Database

logger = logging.getLogger(__name__)

# DESIGN.md lives at repo root. Resolve once at import time.
_DESIGN_MD_PATH = Path(__file__).resolve().parent.parent.parent / "DESIGN.md"


@dataclass
class BrandDNA:
    """Snapshot of Pinaka's brand rules for ad creative generation."""

    tone: str
    palette: dict[str, str] = field(default_factory=dict)
    banned_words: list[str] = field(default_factory=list)
    banned_phrases: list[str] = field(default_factory=list)
    required_phrases: list[str] = field(default_factory=list)
    voice_examples: list[str] = field(default_factory=list)
    source_mtime: float = 0.0
    content_hash: str = ""

    def as_prompt_context(self) -> str:
        """Render the DNA into a prompt-friendly block. Used by AdCreativeGenerator."""
        parts = [
            "## Brand tone",
            self.tone,
            "",
            "## Banned words (NEVER use these in copy)",
            ", ".join(self.banned_words) if self.banned_words else "(none)",
            "",
            "## Banned phrases (empty-luxury copy — never use)",
            "\n".join(f"- {p}" for p in self.banned_phrases) if self.banned_phrases else "(none)",
            "",
            "## Required phrases (include at least one per variant when relevant)",
            "\n".join(f"- {p}" for p in self.required_phrases) if self.required_phrases else "(none)",
        ]
        if self.voice_examples:
            parts.extend([
                "",
                "## Voice examples (founder-edited, match this tone)",
                "\n".join(f'- "{ex}"' for ex in self.voice_examples[:5]),
            ])
        return "\n".join(parts)


# ── Hard-coded baseline (from src/listings/generator.py SYSTEM_PROMPT) ──

_TONE_BASELINE = (
    "Warm, personal, confident — like a family jeweler who genuinely cares about the occasion "
    "the piece marks. Not cold-European luxury. Indian heritage warmth meets clean modern presentation."
)

_BANNED_WORDS_BASELINE = [
    # From listings/generator.py SYSTEM_PROMPT: "Never use the word 'beautiful'"
    "beautiful",
    # From DESIGN.md Decisions Log: avoid empty luxury language
    "stunning",
    "gorgeous",
]

_REQUIRED_PHRASES_BASELINE = [
    # From listings/generator.py + DESIGN.md Trust Signals:
    "Ships in 15 business days",
    "Free Lifetime Care",
]

# DESIGN.md Anti-Patterns section heading marker
_ANTI_PATTERNS_HEADER_RE = re.compile(r"^###\s+Anti-Patterns.*$", re.MULTILINE | re.IGNORECASE)
# Color token rows like: | `--accent` | `#D4A017` | Primary accent ...
_COLOR_ROW_RE = re.compile(
    r"^\|\s*`(--[a-z-]+)`\s*\|\s*`(#[0-9A-Fa-f]{3,6})`",
    re.MULTILINE,
)


def _parse_design_md(text: str) -> tuple[dict[str, str], list[str]]:
    """Extract color palette + anti-pattern phrases from DESIGN.md.

    Returns (palette_dict, anti_pattern_phrases).

    Anti-pattern extraction: finds `### Anti-Patterns` heading (or `### Anti-Patterns (never use)`),
    then captures every bullet item ("- foo") until the next heading.
    """
    # Palette: every color row across the whole file
    palette = {}
    for match in _COLOR_ROW_RE.finditer(text):
        token, hex_val = match.group(1), match.group(2)
        palette[token] = hex_val

    # Anti-patterns: find the header, capture bullets until the next heading
    banned_phrases: list[str] = []
    header_match = _ANTI_PATTERNS_HEADER_RE.search(text)
    if header_match:
        tail = text[header_match.end():]
        # Stop at next heading (## or ### or ---)
        stop = re.search(r"^(#{2,3}\s|---\s*$)", tail, re.MULTILINE)
        section = tail[: stop.start()] if stop else tail
        for line in section.splitlines():
            line = line.strip()
            if line.startswith("-"):
                # Strip leading dash + surrounding quotes
                phrase = line.lstrip("- ").strip().strip('"').strip("'")
                if phrase:
                    banned_phrases.append(phrase)

    return palette, banned_phrases


# ── Cache (module-level, mtime-aware) ──

_CACHED_DNA: BrandDNA | None = None
_CACHED_MTIME: float = 0.0


def load(force_refresh: bool = False, _db: Database | None = None) -> BrandDNA:
    """Load BrandDNA from DESIGN.md + voice_examples table.

    Caches at module level. If DESIGN.md mtime changes between calls, the cache
    is invalidated automatically — no restart needed during development.

    Args:
        force_refresh: Skip the cache check. Used by tests and dashboard "Regenerate DNA" button.
        _db: Injected database (for tests). Defaults to a new Database() instance.

    Raises:
        RuntimeError: if DESIGN.md is missing or unreadable. Fail loud — silent empty
                      palette would produce worse prompts than a clear error.
    """
    global _CACHED_DNA, _CACHED_MTIME

    if not _DESIGN_MD_PATH.exists():
        raise RuntimeError(
            f"BrandDNA: DESIGN.md not found at {_DESIGN_MD_PATH}. "
            "This file is the source of truth for brand tone/palette/anti-patterns."
        )

    current_mtime = _DESIGN_MD_PATH.stat().st_mtime

    if not force_refresh and _CACHED_DNA is not None and current_mtime == _CACHED_MTIME:
        return _CACHED_DNA

    try:
        text = _DESIGN_MD_PATH.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"BrandDNA: failed to read DESIGN.md: {e}") from e

    palette, banned_phrases = _parse_design_md(text)

    # Voice examples: pull up to 5 recent founder-edited drafts from the shared pool
    voice_examples: list[str] = []
    try:
        db = _db or Database()
        # Use existing sync Database method surface — prune handles the pool
        raw = (
            db._client.table("voice_examples")  # type: ignore[attr-defined]
            .select("edited_draft")
            .eq("was_edited", True)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        voice_examples = [row["edited_draft"] for row in (raw.data or []) if row.get("edited_draft")]
    except Exception:
        # Non-fatal. Empty voice_examples just means no few-shot priming.
        logger.exception("BrandDNA: failed to pull voice_examples from Supabase (non-fatal)")

    content = text + "\n" + "\n".join(voice_examples)
    content_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()

    dna = BrandDNA(
        tone=_TONE_BASELINE,
        palette=palette,
        banned_words=list(_BANNED_WORDS_BASELINE),
        banned_phrases=banned_phrases,
        required_phrases=list(_REQUIRED_PHRASES_BASELINE),
        voice_examples=voice_examples,
        source_mtime=current_mtime,
        content_hash=content_hash,
    )

    _CACHED_DNA = dna
    _CACHED_MTIME = current_mtime
    logger.info(
        "BrandDNA loaded (mtime=%s, hash=%s, %d palette entries, %d banned phrases, %d voice examples)",
        current_mtime,
        content_hash[:8],
        len(palette),
        len(banned_phrases),
        len(voice_examples),
    )
    return dna


def _reset_cache_for_tests() -> None:
    """Test helper — invalidate the module-level cache without force_refresh=True."""
    global _CACHED_DNA, _CACHED_MTIME
    _CACHED_DNA = None
    _CACHED_MTIME = 0.0
