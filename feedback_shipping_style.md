---
name: Jaitul's shipping style — one-shot execution over lengthy planning
description: When user says "one shot it" or "ship to learn", skip plan reviews and execute directly against a solid test foundation
type: feedback
---

Jaitul prefers fast, direct execution over multi-round planning + review cycles when he has strong conviction on a feature.

**Why:**
- 2026-04-05 Phase 6.1: outside voices (Codex + Claude CEO subagent) independently said 6/6 dimensions "kill this plan, premature optimization at $75/day". Jaitul overrode with "ship to learn" and the plan documented the disagreement transparently. 6.1 shipped and worked.
- 2026-04-05 Phase 6.2: user said "lets one shot it with 6.2" — meaning no plan file, no review rounds, no outside voices. I wrote code + tests + smoke test in a single session. Worked because Phase 6.1's 60 tests gave us a solid foundation to build on.

**How to apply:**
- When user says "one shot it", "ship it", "lets go", "ship to learn" — do NOT invoke `/autoplan` or run CEO/eng/design reviews unless the change is architecturally ambiguous or has real blast radius.
- When user overrides an outside-voice recommendation, don't argue twice. Surface the disagreement once clearly, then execute the user's call. The user always has context the models don't.
- Keep the test scaffolding dense on first version of a feature — future iterations ride on that investment. Phase 6.1 tests made Phase 6.2 a 1-hour ship instead of a 3-hour ship.
- Fallback/backwards-compat paths are cheap insurance when shipping fast. A property like `is_meta_ad_ready` that gates new code behind an env var lets the user deploy without breaking anything until they're ready to flip the switch. 3 lines of code, saves a rollback.
- **Don't cut tests to ship faster.** Tests are what make "one shot it" safe. The Phase 6.2 ship passed because 197 tests validated it, not in spite of them.
- When there's real blast radius (destructive ops, shared infra, actions visible to others), STILL ask for confirmation even if the user is in fast-ship mode. Speed doesn't mean skip safety on irreversible actions.

**Anti-pattern to avoid:** invoking `/plan-ceo-review` or `/plan-eng-review` on every feature. Those skills are for genuinely ambiguous decisions, not for every commit. Use them when architecture is in flux, not when the pattern is already established.
