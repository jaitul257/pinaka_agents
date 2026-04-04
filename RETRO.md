# Retrospective — Pinaka Agents

Last updated: 2026-04-02

## How to Use This File
- **Read this before starting any new work.** It captures what happened, what worked, what didn't, and what to do differently.
- **Update after every push to main.** Add a new entry with: what shipped, what went well, what was painful, and lessons learned.
- **Keep entries short.** 3-5 bullets per push. This is a learning log, not a changelog.

---

## Push Log

### 2026-04-02 — Phase 6.0 Design System (Shopify Storefront)
**What shipped:** Updated DESIGN.md with complete Shopify storefront design system (hero, collection, PDP, navigation, mobile patterns, photography direction, Atelier Ledger, anti-patterns). TODO.md updated with Phase 6.0 tasks.

**What went well:**
- Outside design voices (Codex + Claude subagent) produced specific, opinionated proposals that were genuinely useful for synthesis
- Competitive research via browse (Mejuri, Catbird, Vrai) grounded decisions in real-world patterns
- Iterative preview refinement caught issues early (image too tall, page too wide, too much clutter)

**What was painful:**
- Preview went through 4 iterations because the initial version was too information-dense. User wanted simplicity, not a design system showcase.
- AI mockup generation failed (no OpenAI API key configured for gstack design binary)
- First preview had light/dark toggle blocking the Cart button

**Lessons learned:**
- Start simple, add complexity only when asked. The user's design philosophy: "so easy customers don't realize they're checking out"
- For e-commerce: Image > Name > Price > Buy. Everything else is secondary.
- Always constrain max-width (1440px) on wide screens. Unconstrained layouts look broken.
- Square (1:1) product images prevent oversized cards. Avoid tall aspect ratios (3:4, 4:5) for grid items.
- Keep preview iterations fast. Show, get feedback, fix. Don't over-explain.

---

### Pre-Phase 6 — Phases 1-5 Complete (as of 2026-04-01)
**What shipped:** Full AI ops system — product intelligence, listing generation, shipping/fraud, marketing analytics, finance tracking, customer service. 126 tests passing. Deployed on Railway.

**Lessons from prior phases (consolidated):**
- Always mock `AsyncDatabase` with `AsyncMock`, not `MagicMock`
- Mock external APIs at client class level, not httpx internals
- Railway auto-deploys on push to main — be careful with what lands on main
- Cron jobs managed via cron-job.org API, not Railway native crons
- Meta Ads token expires every 60 days — monitor expiry
- Check Railway env vars before asking user for secrets
