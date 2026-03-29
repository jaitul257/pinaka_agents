# Design System — Pinaka Jewellery

## Product Context
- **What this is:** AI-powered e-commerce platform for Diamond Tennis Bracelets, starting on Etsy with 6 autonomous agent modules
- **Who it's for:** Solo founder operating two UI surfaces: Streamlit monitoring dashboard (internal ops) and Slack Block Kit (AI draft approval). Future: Shopify storefront (customer-facing, month 4)
- **Space/industry:** Luxury fine jewelry, competing with Mejuri, Catbird, Brilliant Earth, and Etsy jewelry sellers
- **Project type:** Internal dashboard + messaging integration (now), e-commerce storefront (future)

## Aesthetic Direction
- **Direction:** Luxury/Refined with warm undertone
- **Decoration level:** Intentional — subtle gold hairline rules as section dividers, warm off-white backgrounds with barely-there texture. The jewelry is the decoration, everything else steps back.
- **Mood:** The confident warmth of old-world Indian goldsmithing meets clean modern presentation. Like walking into a family jeweler's shop that happens to have impeccable taste. Not cold-European luxury (Tiffany, Cartier). Not budget-artisan either.
- **Reference sites:** mejuri.com (DTC layout), catbird.com (intimate editorial), sophiebillebrahe.com (minimal luxury), vrai.com (lab-grown positioning)

## Typography
- **Display/Hero:** Cormorant Garamond — warm serif with Didot-level elegance but better screen rendering. Pairs naturally with gold accents. Use for headings, hero text, brand moments.
- **Body:** DM Sans — clean geometric sans-serif without being generic. Good x-height for readability. Use for all body copy, UI labels, navigation, buttons.
- **UI/Labels:** DM Sans (same as body, use weight 500 for labels)
- **Data/Tables:** Geist Mono — excellent tabular-nums support, clean monospace for financial data, metrics, and code. Use for all numeric displays, stat cards, and data tables.
- **Code:** Geist Mono
- **Loading:** Google Fonts CDN
  ```html
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400;1,500&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=Geist+Mono:wght@300;400;500&display=swap" rel="stylesheet">
  ```
- **Scale:**
  | Level | Size | Weight | Font | Use |
  |-------|------|--------|------|-----|
  | hero | 56px / 3.5rem | 400 | Cormorant | Shopify hero headline |
  | h1 | 36px / 2.25rem | 400 | Cormorant | Page titles, section heads |
  | h2 | 28px / 1.75rem | 500 | Cormorant | Sub-section heads |
  | h3 | 20px / 1.25rem | 600 | DM Sans | Card titles, widget heads |
  | body | 16px / 1rem | 400 | DM Sans | Body copy, descriptions |
  | small | 14px / 0.875rem | 400 | DM Sans | Secondary text, captions |
  | label | 11px / 0.6875rem | 600 | DM Sans | Labels, uppercase + 1-2px tracking |
  | data | 14px / 0.875rem | 400 | Geist Mono | Table cells, metric values |
  | stat | 28px / 1.75rem | 500 | Geist Mono | Stat card primary numbers |

## Color
- **Approach:** Restrained — warm cream base, charcoal text, gold/saffron accent. Every competitor does black+white+silver. Pinaka does warm charcoal + cream + gold, rooted in Indian heritage.

### Core Palette
| Token | Hex | Role |
|-------|-----|------|
| `--bg` | `#FAF7F2` | Page background (warm cream) |
| `--surface` | `#FFFFFF` | Cards, panels, elevated surfaces |
| `--surface-raised` | `#F5F0E8` | Headers, secondary surfaces |
| `--text-primary` | `#2C2825` | Headings, primary content (warm charcoal, not pure black) |
| `--text-secondary` | `#6B6560` | Body text, descriptions |
| `--text-muted` | `#9E9893` | Labels, captions, metadata |
| `--accent` | `#D4A017` | Primary accent (saffron). CTAs, active states, brand moments. **Button text: use `--text-primary` (#2C2825) on gold, not white** (white on gold fails AA contrast at 2.5:1; charcoal on gold passes at 5.2:1) |
| `--accent-hover` | `#B8890F` | Accent hover/pressed state |
| `--accent-subtle` | `rgba(212, 160, 23, 0.12)` | Accent backgrounds (highlighted blocks) |
| `--gold` | `#C5A55A` | Decorative. Hairline rules, dividers, ornamental |
| `--gold-light` | `rgba(197, 165, 90, 0.15)` | Gold tint backgrounds |
| `--border` | `#E8E2D9` | Default borders |
| `--border-light` | `#F0EBE3` | Subtle borders, table rows |

### Semantic Colors (gem-named)
| Token | Hex | Name | Use |
|-------|-----|------|-----|
| `--success` | `#2E7D4F` | Emerald | Order shipped, action confirmed, healthy status |
| `--success-bg` | `rgba(46, 125, 79, 0.08)` | — | Success alert background |
| `--warning` | `#C17E1A` | Amber | Budget alerts, pending items, degraded status |
| `--warning-bg` | `rgba(193, 126, 26, 0.08)` | — | Warning alert background |
| `--error` | `#C4392D` | Ruby | Fraud flags, failures, urgent items |
| `--error-bg` | `rgba(196, 57, 45, 0.08)` | — | Error alert background |
| `--info` | `#3B7EC5` | Sapphire | Rate limit notices, informational messages |
| `--info-bg` | `rgba(59, 126, 197, 0.08)` | — | Info alert background |

### Dark Mode
Strategy: reduce saturation 10-20%, warm up backgrounds, increase accent brightness for readability.

| Token | Light | Dark |
|-------|-------|------|
| `--bg` | `#FAF7F2` | `#1A1816` |
| `--surface` | `#FFFFFF` | `#242120` |
| `--surface-raised` | `#F5F0E8` | `#2E2A28` |
| `--text-primary` | `#2C2825` | `#F0EBE3` |
| `--text-secondary` | `#6B6560` | `#A39E98` |
| `--accent` | `#D4A017` | `#E0B032` |
| `--border` | `#E8E2D9` | `#3A3634` |

## Spacing
- **Base unit:** 8px
- **Density:** Comfortable — jewelry needs room to breathe, data needs room to be read
- **Scale:**

| Token | Value | Use |
|-------|-------|-----|
| `2xs` | 2px | Hairline gaps |
| `xs` | 4px | Icon padding, tight gaps |
| `sm` | 8px | Inline spacing, small gaps |
| `md` | 16px | Card padding, standard gaps |
| `lg` | 24px | Section padding, card gaps |
| `xl` | 32px | Large section gaps |
| `2xl` | 48px | Page section spacing |
| `3xl` | 64px | Hero section padding |

## Layout
- **Approach:** Grid-disciplined for dashboard (data needs predictability), hybrid for future Shopify (grid for product pages, editorial breaks for brand storytelling)
- **Grid:** Dashboard: single column, full-width. Shopify: 12-column grid, 2-col on tablet, 1-col on mobile.
- **Max content width:** 1200px
- **Border radius:**

| Token | Value | Use |
|-------|-------|-----|
| `sm` | 4px | Small elements, tags, badges |
| `md` | 8px | Buttons, inputs, small cards |
| `lg` | 12px | Cards, panels, modals |
| `full` | 9999px | Pills, avatar circles |

## Shadows
| Token | Value | Use |
|-------|-------|-----|
| `sm` | `0 1px 3px rgba(44, 40, 37, 0.06)` | Cards at rest |
| `md` | `0 4px 12px rgba(44, 40, 37, 0.08)` | Hover states, dropdowns |
| `lg` | `0 8px 24px rgba(44, 40, 37, 0.1)` | Modals, hero sections |

## Motion
- **Approach:** Minimal-functional — subtle fade-ins, smooth transitions. No bouncing, no parallax. Luxury is calm.
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:**

| Token | Value | Use |
|-------|-------|-----|
| `micro` | 50-100ms | Button feedback, toggle |
| `short` | 150-250ms | Hover states, focus rings |
| `medium` | 250-400ms | Panel open/close, page transitions |
| `long` | 400-700ms | Hero fade-in, skeleton shimmer |

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-27 | Initial design system created | Created by /design-consultation. Research: Mejuri, Catbird, Brilliant Earth, Streamlit gallery. Warm luxury direction chosen to differentiate from cold-European competitors. |
| 2026-03-27 | Cream background (#FAF7F2) over pure white | Risk: differentiates from every competitor. Signals heritage and warmth. |
| 2026-03-27 | Saffron accent (#D4A017) over blue/teal | Risk: Indian heritage nod. Pairs with gold. Nobody in Etsy jewelry uses this. |
| 2026-03-27 | Gem-named semantic colors | Emerald/Amber/Ruby/Sapphire for success/warning/error/info. On-brand for a jeweler. |
| 2026-03-27 | Cormorant Garamond over Playfair Display | Both are luxury serifs. Cormorant has warmer character, better italic, lighter weight options for screen. |
| 2026-03-29 | Button text: charcoal on gold, not white on gold | /plan-design-review contrast check: white (#FFF) on --accent (#D4A017) = 2.5:1 (fails AA). --text-primary (#2C2825) on --accent = 5.2:1 (passes AA). Dark on gold reads better and is accessible. |
