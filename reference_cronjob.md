---
name: Cron-job.org API access
description: Cron scheduling managed via cron-job.org API, not Railway native
type: reference
---

Cron jobs managed at https://console.cron-job.org/dashboard via their API.

**API auth:** Bearer token in Authorization header.
**Base URL for jobs:** https://api.cron-job.org/jobs
**Methods:** GET (list), GET /{id} (details), PUT (create), PATCH /{id} (update)
**Cron secret header:** X-Cron-Secret (value stored in Railway CRON_SECRET env var)
**Target URL:** https://pinaka-agents-production-198b5.up.railway.app/cron/{endpoint}

All jobs use: requestMethod=1 (POST), saveResponses=true, requestTimeout=30, UTC timezone.
