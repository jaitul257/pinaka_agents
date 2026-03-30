#!/bin/bash
# Railway cron runner — calls the appropriate endpoint based on CRON_JOB env var.
# Each Railway cron service sets CRON_JOB and calls this script.

BASE_URL="${APP_URL:-https://pinaka-agents-production-198b5.up.railway.app}"

call_cron() {
  echo "Calling $1..."
  curl -s -X POST "${BASE_URL}${1}" \
    -H "X-Cron-Secret: ${CRON_SECRET}" \
    -H "Content-Type: application/json" \
    --max-time 30
  echo ""
}

case "${CRON_JOB}" in
  every-30-min)
    call_cron "/cron/reconcile-orders"
    call_cron "/cron/crafting-updates"
    call_cron "/cron/abandoned-carts"
    ;;
  daily-stats)
    call_cron "/cron/daily-stats"
    call_cron "/cron/sync-products"
    ;;
  morning-digest)
    call_cron "/cron/morning-digest"
    ;;
  weekly)
    call_cron "/cron/weekly-rollup"
    call_cron "/cron/weekly-finance"
    ;;
  *)
    echo "Unknown CRON_JOB: ${CRON_JOB}"
    echo "Valid values: every-30-min, daily-stats, morning-digest, weekly"
    exit 1
    ;;
esac
