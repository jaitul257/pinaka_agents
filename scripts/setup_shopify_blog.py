"""One-time helper to discover the Shopify blog_id and set it on Railway.

Run AFTER you've re-authorized the Shopify app with the new `write_content`
scope (visit the app install URL after Railway deploys the scope bump).

    railway run .venv/bin/python scripts/setup_shopify_blog.py

Picks the "News" blog if it exists, otherwise the first blog returned.
"""

import json
import os
import subprocess
import sys

import httpx


def main() -> int:
    shop = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    api_version = os.environ.get("SHOPIFY_API_VERSION", "2025-01")

    if not shop or not token:
        print("ERROR: SHOPIFY_SHOP_DOMAIN or SHOPIFY_ACCESS_TOKEN not set.")
        print("Tip: run via `railway run python ...`.")
        return 1

    url = f"https://{shop}/admin/api/{api_version}/blogs.json"
    resp = httpx.get(url, headers={"X-Shopify-Access-Token": token}, timeout=15)
    if resp.status_code == 403:
        print("ERROR 403: token missing `read_content`/`write_content` scope.")
        print("Re-install the app at https://" + shop.replace(".myshopify.com", "") +
              ".myshopify.com/admin/oauth/install_custom_app?client_id=<CLIENT_ID>")
        print("Or visit your app's install URL in the Partners dashboard.")
        return 1
    if resp.status_code != 200:
        print(f"ERROR {resp.status_code}: {resp.text[:300]}")
        return 1

    blogs = resp.json().get("blogs", [])
    if not blogs:
        print("No blogs found. Create one in Shopify admin → Online Store → Blogs.")
        return 1

    # Prefer "News"; fall back to first
    chosen = next((b for b in blogs if (b.get("title", "").lower()) == "news"), blogs[0])
    blog_id = chosen["id"]
    title = chosen.get("title", "—")
    print(f"Found blog: '{title}' (id={blog_id})")
    print()
    print("Available blogs:")
    for b in blogs:
        marker = "  ★" if b["id"] == blog_id else "   "
        print(f"{marker} {b['id']}: {b.get('title', '—')}")

    print()
    print(f"Setting SHOPIFY_BLOG_ID={blog_id} on Railway...")
    subprocess.run(
        ["railway", "variables", "--set", f"SHOPIFY_BLOG_ID={blog_id}"],
        check=True,
    )
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
