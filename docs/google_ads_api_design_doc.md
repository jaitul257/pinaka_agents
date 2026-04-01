# Google Ads API - Tool Design Document

## Company: Pinaka Jewellery
## Tool Name: Pinaka Marketing Automation

---

## 1. Overview

Pinaka Jewellery is a direct-to-consumer fine jewelry e-commerce business operating on Shopify. We are building an internal marketing automation tool that integrates with the Google Ads API for two purposes:

1. **Ad Spend Reporting** - Automated daily pull of campaign spend metrics for ROAS (Return on Ad Spend) calculation
2. **Offline Conversion Upload** - Server-side purchase conversion tracking to improve Smart Bidding optimization

## 2. Architecture

The tool is a Python/FastAPI application deployed on Railway. It runs automated cron jobs that interact with the Google Ads API.

### Components:
- **Ad Spend Sync (Cron)** - Daily job at 6 AM ET that pulls yesterday's spend from Google Ads Reporting API and stores it in our Supabase database alongside Meta ad spend for unified ROAS reporting.
- **Offline Conversion Upload (Webhook)** - When a Shopify order webhook fires with a `gclid` (Google Click ID) present in the landing page URL, we upload the purchase as an offline conversion to Google Ads.

## 3. API Usage

### 3.1 Google Ads Reporting API (Read-Only)

**Endpoint:** `SearchGoogleAdsStream`

**GAQL Query:**
```
SELECT metrics.cost_micros, metrics.conversions, metrics.impressions, metrics.clicks
FROM customer
WHERE segments.date = '{date}'
```

**Frequency:** Once daily (6 AM ET)
**Volume:** 1 API call per day

### 3.2 Offline Conversions API (Write)

**Endpoint:** `UploadClickConversions`

**Data sent per conversion:**
- gclid (Google Click ID from landing page URL)
- conversion_date_time
- conversion_value (order total in USD)
- currency_code: USD

**Frequency:** Per Shopify order (only when gclid is present)
**Volume:** Estimated 5-20 conversions per day

## 4. Authentication

- OAuth 2.0 with refresh token
- Single Google Ads account (advertiser, not MCC)
- Internal use only (no third-party access)

## 5. Data Handling

- No customer PII is sent to Google Ads API
- Only aggregated spend metrics are read
- gclid is a Google-generated click identifier, not PII
- All data stored in encrypted Supabase (Postgres) database

## 6. Rate Limits

Our usage is well within Basic access limits:
- Reporting: 1 call/day
- Conversions: 5-20 calls/day
- Total daily API calls: < 50

## 7. Users

Internal only. The tool runs as automated cron jobs. No external users or UI for the Google Ads integration.
