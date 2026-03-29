# TODOS — Pinaka Jewellery AI E-Commerce (Shopify DTC)

## P0 — Blocks Launch

### Product Photography
- **What:** Complete professional product photography for Diamond Tennis Bracelet (natural + lab-grown variants). Lifestyle shots, sparkle/movement video, detail shots, wrist shots.
- **Why:** Cannot sell a $2K diamond bracelet with stock photos or AI-generated images. This is the P0 blocker for the entire Shopify launch.
- **Effort:** S (human: 1 day shoot + editing)
- **Depends on:** Inventory in hand
- **Added:** 2026-03-25 via /plan-ceo-review
- **Updated:** 2026-03-29 (Shopify pivot)

### Shopify Account + Domain Setup
- **What:** Create Shopify account (free trial), claim branded .com domain, set up Dawn theme with one product page. Create Google Merchant Center + Meta Business Manager accounts (approval timelines run in parallel with photography).
- **Why:** These have approval timelines. Start now so they're ready when photos are done.
- **Effort:** S (human: ~2-3 hours setup)
- **Depends on:** Nothing (can start immediately)
- **Added:** 2026-03-29 via /office-hours

### SendGrid Domain Authentication (SPF/DKIM)
- **What:** Set up SendGrid account, configure SPF and DKIM DNS records for the branded domain. Required for the AI customer service email flow (inbound parsing + transactional sending).
- **Why:** Without domain authentication, reply emails to customers land in spam. This is a DNS configuration task that should run in parallel with Shopify account setup.
- **Effort:** S (human: ~30 min DNS setup + 24-48h propagation)
- **Depends on:** Branded domain (from Shopify Account + Domain Setup)
- **Added:** 2026-03-29 via /plan-eng-review (outside voice finding: Shopify Email API is for campaigns, not transactional replies)

## P1 — Important, Not Blocking Launch

### Chargeback Prevention Strategy
- **What:** Document chargeback response runbook. Implement: signature confirmation (already in plan), order confirmation emails with delivery photos, dispute evidence package template. Consider chargeback insurance for orders >$5,000.
- **Why:** High-value jewelry orders are chargeback targets. Having a documented evidence trail (signature + photos + tracking) is the best defense.
- **Effort:** S (human: ~2 hours runbook / CC: ~15 min code)
- **Depends on:** Shipping module implementation
- **Added:** 2026-03-27 via /plan-eng-review

### Revenue Model Grounding
- **What:** Build a realistic customer acquisition model with conversion rate assumptions, traffic estimates from Google Shopping + Meta ads, and monthly sales projections grounded in DTC jewelry benchmarks.
- **Why:** Outside voice flagged unrealistic projections without a conversion model. Need real data from the concentrated ad sprint (Month 1, $3-5K).
- **Effort:** S (human: ~3 hours / CC: ~30 min)
- **Depends on:** Nothing (can model before launch, validate with real data after)
- **Added:** 2026-03-25 via /plan-ceo-review
- **Updated:** 2026-03-29 (reframed for Shopify DTC)

### Webhook Health Monitoring
- **What:** Periodic check (daily cron or part of morning digest) that all Shopify webhook subscriptions are active. If Shopify auto-deleted a subscription (due to repeated 5-second timeout failures), re-register and alert in Slack.
- **Why:** Shopify auto-deletes webhook subscriptions after repeated delivery failures. The 30-min reconciliation cron catches missed individual events, but if the subscription itself is deleted, no events arrive until re-registered. Monitoring prevents silent data loss.
- **Effort:** S (CC: ~15 min)
- **Depends on:** Shopify webhook registration (Phase 1 implementation)
- **Added:** 2026-03-29 via /plan-eng-review (outside voice finding: Shopify 5-second timeout risk)

### Competitive Intelligence
- **What:** Research DTC jewelry brands (Mejuri, Catbird, Brilliant Earth) for pricing, ad strategy, content approach. Monitor Google Shopping competitive landscape.
- **Why:** Competitive awareness informs ad creative, pricing, and positioning.
- **Effort:** S (human: ~2 hours research)
- **Depends on:** Nothing
- **Added:** 2026-03-25 via /plan-ceo-review
- **Updated:** 2026-03-29 (reframed for DTC competitors)

## P2 — Deferred Scope (from CEO Review Expansion Ceremony)

### Predictive Reorder/Anniversary Reminders
- **What:** Track customer purchase dates and anniversary milestones. AI-draft personalized reminder emails ("It's been a year since your tennis bracelet. Time for matching earrings?")
- **Why:** Lifecycle marketing for repeat purchases. Deferred because need a customer base first.
- **Effort:** S (CC: ~15-30 min)
- **Depends on:** Active customer base with repeat purchase data
- **Added:** 2026-03-29 via /plan-ceo-review (deferred expansion #4)

### Dynamic Product Description A/B Testing
- **What:** Generate multiple Claude-written product descriptions and A/B test for conversion rate. Track which descriptions drive more checkouts.
- **Why:** Optimize conversion through copy. Deferred because need traffic volume for statistical significance.
- **Effort:** M (CC: ~1-2 hours)
- **Depends on:** 50+ daily sessions (from ad sprint)
- **Added:** 2026-03-29 via /plan-ceo-review (deferred expansion #5)

### Founder Voice Learning System
- **What:** Train the AI on founder's approved draft edits to learn the brand voice over time. After 20+ approved drafts, the AI should match the founder's tone without edits.
- **Why:** Reduces founder review burden over time. Deferred because need approved draft history first.
- **Effort:** S (CC: ~15-30 min)
- **Depends on:** 20+ approved AI drafts in the system
- **Added:** 2026-03-29 via /plan-ceo-review (deferred expansion #6)

## P3 — Future Scope

### International Shipping Rules Engine
- **What:** Multi-currency, localized carriers, tax compliance by jurisdiction.
- **Why:** Year 2-3 expansion to UK/EU/AU markets.
- **Effort:** L (CC: ~4 hours)
- **Added:** 2026-03-25 via /plan-ceo-review

### Multi-Tenant Platform Play
- **What:** Architect AI agents as a reusable platform for other e-commerce sellers.
- **Why:** Second revenue stream. Revisit after $50K+ annual revenue validates the agent architecture.
- **Effort:** XL (CC: ~2-3 weeks)
- **Added:** 2026-03-25 via /plan-ceo-review
- **Updated:** 2026-03-29 (revised revenue target from $250K to $50K)
