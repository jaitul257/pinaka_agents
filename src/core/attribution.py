"""Extract attribution parameters (gclid, fbclid, utm_*) from Shopify landing_site URLs."""

import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

ATTRIBUTION_PARAMS = ("gclid", "fbclid", "utm_source", "utm_medium", "utm_campaign")


def extract_attribution(order_data: dict[str, Any]) -> dict[str, str | None]:
    """Parse attribution params from Shopify order's landing_site field.

    Returns a dict with keys: gclid, fbclid, utm_source, utm_medium, utm_campaign.
    All values are None if not present or URL is malformed.
    """
    result: dict[str, str | None] = {k: None for k in ATTRIBUTION_PARAMS}

    landing_site = order_data.get("landing_site") or ""
    if not landing_site:
        return result

    try:
        parsed = urlparse(landing_site)
        params = parse_qs(parsed.query)
        for key in ATTRIBUTION_PARAMS:
            values = params.get(key)
            if values:
                result[key] = values[0]
    except Exception:
        logger.warning("Failed to parse landing_site URL: %s", landing_site[:200])

    return result
