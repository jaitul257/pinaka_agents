"""Create the 6 SendGrid dynamic templates for Phase 9.2.

Templates:
  - pinaka_lifecycle   (shared — takes {{subject}} + {{email_body}})
  - pinaka_welcome_1   4Cs primer
  - pinaka_welcome_2   how we make a bracelet
  - pinaka_welcome_3   founder note
  - pinaka_welcome_4   the atelier up close
  - pinaka_welcome_5   one open door

Idempotent. If a template with the same name already exists, skip creation
and print the existing ID. Prints the env var assignments at the end —
pipe through `railway variables --set` or paste manually.

USAGE:
    .venv/bin/python scripts/create_sendgrid_lifecycle_templates.py --create
    .venv/bin/python scripts/create_sendgrid_lifecycle_templates.py --create --set-on-railway

Requires SENDGRID_API_KEY on Railway (loaded via `railway run` wrapper) or env.
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Any

import httpx

SENDGRID_BASE = "https://api.sendgrid.com/v3"

# Shared CSS — inline for email-client compatibility. Cream bg, warm charcoal
# text, saffron accent, Cormorant Garamond headings via Google Fonts (modern
# clients only, falls back to serif).
BRAND_STYLES = """
  body { margin: 0; padding: 0; background: #FAF7F2; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #2C2825; }
  .wrap { max-width: 560px; margin: 0 auto; padding: 40px 32px; background: #FFFFFF; }
  .logo { font-family: 'Cormorant Garamond', Georgia, serif; font-size: 24px; font-weight: 400; color: #2C2825; letter-spacing: 0.5px; margin: 0 0 32px; }
  .h1 { font-family: 'Cormorant Garamond', Georgia, serif; font-size: 28px; font-weight: 400; line-height: 1.2; margin: 0 0 20px; color: #2C2825; }
  .p { font-size: 16px; line-height: 1.6; color: #2C2825; margin: 0 0 16px; }
  .muted { color: #6B6560; font-size: 14px; line-height: 1.5; }
  .divider { height: 1px; background: #E8E2D9; margin: 28px 0; border: 0; }
  .signoff { font-family: 'Cormorant Garamond', Georgia, serif; font-size: 18px; color: #2C2825; margin: 24px 0 8px; }
  .cta { display: inline-block; padding: 12px 24px; background: #D4A017; color: #2C2825; text-decoration: none; font-weight: 600; border-radius: 6px; margin: 12px 0; }
  .footer { font-size: 12px; color: #9E9893; margin-top: 32px; padding-top: 20px; border-top: 1px solid #E8E2D9; }
"""


def _shell(body_html: str, preview: str = "") -> str:
    """Wrap body HTML in the branded shell."""
    preview_line = f'<span style="display:none;opacity:0;color:#FAF7F2;">{preview}</span>' if preview else ""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Pinaka Jewellery</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500&display=swap" rel="stylesheet">
  <style>{BRAND_STYLES}</style>
</head>
<body>
  {preview_line}
  <div class="wrap">
    <div class="logo">Pinaka Jewellery</div>
    {body_html}
    <div class="footer">
      Pinaka Jewellery &middot; pinakajewellery.com<br>
      If you'd rather not receive these, just reply and say so — I read every one.
    </div>
  </div>
</body>
</html>"""


# Shared lifecycle wrapper — body drafted by Claude at send time.
LIFECYCLE_BODY = """<h1 class="h1">Hi {{customer_name}},</h1>
<div class="p" style="white-space: pre-line;">{{email_body}}</div>"""


# Welcome series content. Each is static, educational, under 150 words.
WELCOME = [
    {
        "slug": "pinaka_welcome_1",
        "subject": "Welcome to Pinaka — a short primer",
        "preview": "A one-minute read on what the 4Cs actually mean.",
        "body": """<h1 class="h1">Welcome, {{customer_name}}.</h1>
<p class="p">Before anything else, a short primer on the four things that make a diamond what it is. You'll see these letters — 4Cs — everywhere. Here is what they actually mean.</p>
<p class="p"><strong>Cut</strong> is the only one shaped by human hands. It decides how light returns to your eye. It's why two stones of the same size can look wildly different.</p>
<p class="p"><strong>Color</strong> runs D to Z. D is colorless. We use D through G in our bracelets.</p>
<p class="p"><strong>Clarity</strong> is about how clean the stone is inside. We stay in the VS1/VS2 range — no visible inclusions to the naked eye.</p>
<p class="p"><strong>Carat</strong> is weight, not size. A well-cut 1-carat looks larger than a poorly-cut 1.2.</p>
<p class="p">That's the whole vocabulary. Next time, I'll show you how we actually set 50 of them into a bracelet without breaking anything.</p>
<p class="signoff">Warm,<br>{{founder_name}}</p>""",
    },
    {
        "slug": "pinaka_welcome_2",
        "subject": "How we make a bracelet",
        "preview": "The 15-day journey from stone selection to your wrist.",
        "body": """<h1 class="h1">The fifteen days.</h1>
<p class="p">Every Pinaka bracelet takes 15 business days. Here's what happens in that window.</p>
<p class="p"><strong>Days 1–3: Stone matching.</strong> We select each diamond by eye — they have to agree with each other in color temperature and cut profile. Machines can't do this; the human eye is still the final filter.</p>
<p class="p"><strong>Days 4–7: Forming the gold.</strong> We draw 14k or 18k wire, bend it into the links by hand, and weld each joint. No stamping, no casting — old-school work that takes longer but wears better.</p>
<p class="p"><strong>Days 8–11: Hand setting.</strong> Fifty diamonds into fifty prongs, one at a time. This is the slowest step and the one that shows most if rushed.</p>
<p class="p"><strong>Days 12–14: Final polish.</strong> Rhodium plating for white gold; deep warm polish for yellow. Quality check against a loupe.</p>
<p class="p"><strong>Day 15: Dispatch.</strong> Insured, tracked, hand-delivered to the door.</p>
<p class="signoff">Warm,<br>{{founder_name}}</p>""",
    },
    {
        "slug": "pinaka_welcome_3",
        "subject": "A note from Jaitul",
        "preview": "Why I started Pinaka — the short version.",
        "body": """<h1 class="h1">The short version.</h1>
<p class="p">A few years ago my mother asked for a tennis bracelet to mark a milestone. Every good one I found was either priced like it came with a yacht or built to look like it cost a fraction of what it did.</p>
<p class="p">I grew up around jewelers — my family is from the Indian diamond trade — and I knew what fine jewelry actually costs to make. I also knew the markups the brand name adds.</p>
<p class="p">So I built Pinaka: handcrafted fine diamond bracelets at the cost of the materials and the hands, plus a fair margin to keep the lights on.</p>
<p class="p">No stores. No middlemen. No theatrical discount sales. Just one thing, made carefully, priced honestly.</p>
<p class="p">Thanks for being here.</p>
<p class="signoff">Warm,<br>{{founder_name}}</p>""",
    },
    {
        "slug": "pinaka_welcome_4",
        "subject": "The atelier, up close",
        "preview": "What a setter's workbench looks like at 9am.",
        "body": """<h1 class="h1">Where it all happens.</h1>
<p class="p">A jewelry setter's workbench doesn't look like much. A leather pad, a bench peg, a loupe, three sizes of pliers, and a bowl of uncut diamonds.</p>
<p class="p">What it <em>is</em>, though, is concentration. Our senior setter Dilip doesn't talk from 9am to 1pm. He sets diamonds. The only sound is the tap of his graver against the prongs.</p>
<p class="p">Each diamond takes four to six minutes to set. A single bracelet is three to four hours of silence, one stone at a time.</p>
<p class="p">We could buy a machine that does this faster. We've tried. The machine can't tell when a diamond is seated slightly wrong and needs a retry — which is, honestly, most of the time. So the hands stay.</p>
<p class="p">This is what you pay for, when you pay for fine jewelry. The hours nobody sees.</p>
<p class="signoff">Warm,<br>{{founder_name}}</p>""",
    },
    {
        "slug": "pinaka_welcome_5",
        "subject": "One open door",
        "preview": "If you'd like to talk to me before buying, I keep slots open.",
        "body": """<h1 class="h1">An open door.</h1>
<p class="p">This is the fifth and last welcome note. Not every brand needs to send five emails — I know. But this is the one that matters most, so I'll keep it short.</p>
<p class="p">If you're thinking about a bracelet and you'd like to talk to a human before spending five thousand dollars, I keep fifteen-minute slots open most weekdays. You'd get me, not a salesperson — we don't have salespeople.</p>
<p class="p">Reply to this email with "call" and a day that works, and I'll send a Zoom link.</p>
<p class="p">No pressure if the answer is no. I just wanted you to know the door is there.</p>
<p class="signoff">Warm,<br>{{founder_name}}</p>""",
    },
]


def _get_api_key() -> str:
    key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not key:
        print("ERROR: SENDGRID_API_KEY not set. Run via `railway run python ...`")
        sys.exit(1)
    return key


def _list_templates(api_key: str) -> list[dict[str, Any]]:
    """Return all dynamic templates on the account."""
    url = f"{SENDGRID_BASE}/templates?generations=dynamic&page_size=200"
    with httpx.Client(timeout=20) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    resp.raise_for_status()
    return resp.json().get("result", [])


def _create_template(api_key: str, name: str) -> dict[str, Any]:
    url = f"{SENDGRID_BASE}/templates"
    with httpx.Client(timeout=20) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"name": name, "generation": "dynamic"},
        )
    resp.raise_for_status()
    return resp.json()


def _add_version(
    api_key: str, template_id: str, name: str, subject: str, html: str
) -> dict[str, Any]:
    url = f"{SENDGRID_BASE}/templates/{template_id}/versions"
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "template_id": template_id,
                "active": 1,
                "name": name,
                "subject": subject,
                "html_content": html,
                "generate_plain_content": True,
            },
        )
    resp.raise_for_status()
    return resp.json()


def _railway_set(var: str, value: str) -> None:
    """Set a Railway env var via CLI."""
    subprocess.run(
        ["railway", "variables", "--set", f"{var}={value}"],
        check=True,
    )


def main(create: bool, set_on_railway: bool) -> int:
    api_key = _get_api_key()
    existing = _list_templates(api_key)
    existing_by_name = {t["name"]: t for t in existing}

    # 1. Lifecycle shared template
    configs = [
        {
            "slug": "pinaka_lifecycle",
            "subject": "{{subject}}",
            "preview": "A personal note from Pinaka.",
            "body": LIFECYCLE_BODY,
            "env_var": "SENDGRID_LIFECYCLE_TEMPLATE_ID",
        },
    ] + [
        {
            "slug": w["slug"],
            "subject": w["subject"],
            "preview": w["preview"],
            "body": w["body"],
            "env_var": f"SENDGRID_WELCOME_{idx}_TEMPLATE_ID",
        }
        for idx, w in enumerate(WELCOME, start=1)
    ]

    results: list[tuple[str, str, str]] = []

    for cfg in configs:
        name = cfg["slug"]
        html = _shell(cfg["body"], preview=cfg["preview"])
        existing_tpl = existing_by_name.get(name)
        if existing_tpl:
            template_id = existing_tpl["id"]
            print(f"• {name}: exists (id={template_id}) — skipping creation")
        elif create:
            tpl = _create_template(api_key, name)
            template_id = tpl["id"]
            _add_version(api_key, template_id, name + "_v1", cfg["subject"], html)
            print(f"• {name}: created (id={template_id})")
        else:
            print(f"• {name}: would create (dry-run; pass --create to write)")
            continue

        results.append((cfg["env_var"], template_id, name))

    print()
    print("── Environment variables ──")
    for env_var, tpl_id, _ in results:
        print(f"  {env_var}={tpl_id}")

    if set_on_railway and results:
        print()
        print("── Setting on Railway ──")
        for env_var, tpl_id, _ in results:
            try:
                _railway_set(env_var, tpl_id)
                print(f"  ✓ {env_var}")
            except subprocess.CalledProcessError as e:
                print(f"  ✗ {env_var} — {e}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true", help="Actually create missing templates")
    parser.add_argument("--set-on-railway", action="store_true", help="Set resulting IDs as Railway env vars")
    args = parser.parse_args()
    sys.exit(main(create=args.create, set_on_railway=args.set_on_railway))
