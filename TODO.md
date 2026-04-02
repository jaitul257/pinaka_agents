# TODO — Pinaka Agents

Last updated: 2026-04-01

## Phases 1-5: COMPLETE

All core infrastructure shipped and deployed. 126 tests passing. System is live on Railway.

---

## Pending: External Platform Setup (no code needed)

### Google Ads API (blocks Google ad spend sync + offline conversions)
- [ ] Apply for Google Ads Developer Token at ads.google.com → Tools → API Center (5-15 business days)
- [ ] Create OAuth2 credentials in Google Cloud Console
- [ ] Generate refresh token via google-ads auth helper
- [ ] Set Railway env vars: `GOOGLE_ADS_CUSTOMER_ID`, `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`

### Google Merchant Center (blocks Google Shopping ads)
- [ ] Create account at merchants.google.com
- [ ] Link to Google Ads account
- [ ] Create service account in Google Cloud Console
- [ ] Set Railway env vars: `GOOGLE_MERCHANT_ID`, `GOOGLE_SERVICE_ACCOUNT_PATH`

### Google Ads Conversion Action (blocks server-side conversion tracking)
- [ ] Create conversion action in Google Ads → Tools → Conversions → Import → Upload clicks
- [ ] Set Railway: `GOOGLE_ADS_CONVERSION_ACTION_ID`

### Meta Ads Token Renewal
- [ ] Current `META_ADS_ACCESS_TOKEN` expires ~June 1, 2026 (60-day token)
- [ ] Regenerate via Graph API Explorer before expiry
- [ ] Consider setting up a system user token (never expires) once app is added to business portfolio

---

## Phase 6: Planned Features

### 6.1 Automated Ad Creative Generation
- [ ] AI-generated ad copy per product (headline, description, CTA)
- [ ] Product image selection from catalog for ad creatives
- [ ] A/B variant generation (2-3 versions per product)
- [ ] Slack review before pushing to Meta/Google
- **Why:** Currently ad copy is manual. At $75/day budget, creative refresh matters for avoiding ad fatigue.

### 6.2 Review Request Automation
- [ ] Post-delivery review solicitation via email (7-14 days after delivery)
- [ ] Platform-specific links (Google Reviews, Trustpilot)
- [ ] Slack approval before sending
- [ ] Track review rate and sentiment
- **Why:** Social proof drives DTC conversion. Automate the ask.

---

## Operational: Ongoing Maintenance

- [ ] Monitor Meta Ads token expiry (renew before June 1, 2026)
- [ ] Monitor cron job success rates on cron-job.org dashboard
- [ ] Review Sentry for production errors weekly
- [ ] Check ROAS Slack reports weekly and act on budget recommendations
- [ ] Verify Shopify webhook health (auto-recovery runs every 30 min)

---

## Known Gaps (low priority)

- [ ] `apply_budget_change` Slack button logs action but doesn't auto-change Meta/Google budgets (intentional, deferred until API proven stable)
- [ ] `datetime.utcnow()` deprecation warnings in app.py (12 instances, cosmetic, Python 3.12+)
- [ ] Health endpoint does shallow checks only (no real DB/Shopify connectivity test)
- [ ] No CI/CD pipeline (tests run locally, Railway auto-deploys on push)
- [ ] No pytest-cov integration (coverage not measured)
- [ ] Dashboard uses sync Database (not migrated to async, Streamlit has different runtime)
