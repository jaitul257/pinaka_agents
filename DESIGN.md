# Design System — Pinaka Jewellery

## Product Context
- **What this is:** AI-powered e-commerce platform for premium handcrafted diamond tennis bracelets, DTC on Shopify
- **Who it's for:** Customers shopping for $500-5000 fine jewelry online (77% mobile), plus solo founder monitoring via Streamlit dashboard and Slack
- **Space/industry:** Luxury fine jewelry, competing with Mejuri, Catbird, Brilliant Earth, Vrai
- **Project type:** Shopify storefront (customer-facing) + internal dashboard + Slack integration
- **Philosophy:** So beautiful customers are awestruck, so simple they purchase without friction. Image > Name > Price > Buy.

## Aesthetic Direction
- **Direction:** Luxury/Refined with warm Indian heritage undertone
- **Decoration level:** Intentional — gold hairline rules as section dividers, warm off-white backgrounds. The jewelry is the decoration, everything else steps back.
- **Mood:** The confident warmth of old-world Indian goldsmithing meets clean modern presentation. Like walking into a family jeweler's shop that happens to have impeccable taste. Not cold-European luxury (Tiffany, Cartier). Not budget-artisan either.
- **Reference sites:** mejuri.com (DTC layout), catbird.com (intimate editorial), sophiebillebrahe.com (minimal luxury), vrai.com (lab-grown positioning)
- **Core principle:** Less is more. Every element earns its place. No clutter, no information overload. The page should feel like the bracelet looks: elegant, warm, effortless.

## Typography
- **Display/Hero:** Cormorant Garamond — warm serif with Didot-level elegance but better screen rendering. Pairs naturally with gold accents. Use for headings, hero text, brand moments.
- **Body:** DM Sans — clean geometric sans-serif without being generic. Good x-height for readability. Use for all body copy, UI labels, navigation, buttons.
- **UI/Labels:** DM Sans (same as body, use weight 500-600 for labels)
- **Data/Tables:** Geist Mono — excellent tabular-nums support, clean monospace for financial data, metrics, and code. Use for all numeric displays, stat cards, specs, and prices.
- **Code:** Geist Mono
- **Loading:** Google Fonts CDN
  ```html
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400;1,500&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=Geist+Mono:wght@300;400;500&display=swap" rel="stylesheet">
  ```
- **Scale:**

| Level | Size | Weight | Font | Use |
|-------|------|--------|------|-----|
| hero | 52px / 3.25rem | 400 | Cormorant | Shopify hero headline |
| h1 | 36px / 2.25rem | 400 | Cormorant | Page titles, section heads |
| h2 | 32px / 2rem | 400 | Cormorant | Sub-section heads |
| h3 | 18px / 1.125rem | 600 | DM Sans | Card titles, widget heads |
| body | 16-17px / 1rem | 400 | DM Sans | Body copy, descriptions |
| nav | 15px / 0.9375rem | 500 | DM Sans | Navigation links, secondary text |
| small | 14px / 0.875rem | 400 | DM Sans | Footer links, captions |
| label | 11-12px / 0.6875rem | 600 | DM Sans | Labels, uppercase + 1-2px tracking |
| price-lg | 26px / 1.625rem | 500 | Geist Mono | PDP primary price |
| price-sm | 17px / 1.0625rem | 500 | Geist Mono | Card prices |
| data | 12-13px / 0.8125rem | 400 | Geist Mono | Specs, delivery info, table cells |
| stat | 26px / 1.625rem | 500 | Geist Mono | Stat card primary numbers |

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
| `--accent` | `#D4A017` | Primary accent (saffron). CTAs, active states, delivery info. **Button text: use `--text-primary` (#2C2825) on gold, not white** (white on gold fails AA contrast at 2.5:1; charcoal on gold passes at 5.2:1) |
| `--accent-hover` | `#B8890F` | Accent hover/pressed state |
| `--accent-subtle` | `rgba(212, 160, 23, 0.12)` | Accent backgrounds (selected options, story section) |
| `--gold` | `#C5A55A` | Decorative. Hairline rules, dividers, ornamental |
| `--gold-light` | `rgba(197, 165, 90, 0.15)` | Gold tint backgrounds |
| `--border` | `#E8E2D9` | Default borders |
| `--border-light` | `#F0EBE3` | Subtle borders, table rows |

### Semantic Colors (gem-named)
| Token | Hex | Name | Use |
|-------|-----|------|-----|
| `--success` | `#2E7D4F` | Emerald | Order shipped, action confirmed |
| `--success-bg` | `rgba(46, 125, 79, 0.08)` | — | Success alert background |
| `--warning` | `#C17E1A` | Amber | In-progress items, pending |
| `--warning-bg` | `rgba(193, 126, 26, 0.08)` | — | Warning alert background |
| `--error` | `#C4392D` | Ruby | Fraud flags, failures |
| `--error-bg` | `rgba(196, 57, 45, 0.08)` | — | Error alert background |
| `--info` | `#3B7EC5` | Sapphire | Informational messages |
| `--info-bg` | `rgba(59, 126, 197, 0.08)` | — | Info alert background |

### Dark Mode
Strategy: reduce saturation 10-20%, warm up backgrounds, increase accent brightness.

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
| `3xl` | 56px | Section vertical rhythm |

## Layout
- **Approach:** Grid-disciplined for storefront and dashboard
- **Max content width:** 1440px with 48px side padding
- **Grid:** Shopify: 12-column grid, 3-col product grid on desktop, 2-col on mobile. Dashboard: single column, full-width.
- **Border radius:**

| Token | Value | Use |
|-------|-------|-----|
| `sm` | 4px | Small elements, tags, badges |
| `md` | 8px | Buttons, inputs, option pills |
| `lg` | 10px | Cards, images, panels |
| `full` | 9999px | Pills, status tags, avatar circles |

## Shadows
| Token | Value | Use |
|-------|-------|-----|
| `sm` | `0 2px 8px rgba(44, 40, 37, 0.05)` | Cards at rest |
| `md` | `0 6px 24px rgba(44, 40, 37, 0.07)` | Hover states, PDP image |
| `lg` | `0 8px 32px rgba(44, 40, 37, 0.08)` | Hero image, modals |

## Motion
- **Approach:** Minimal-functional — subtle transitions. No bouncing, no parallax. Luxury is calm.
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:**

| Token | Value | Use |
|-------|-------|-----|
| `micro` | 50-100ms | Button feedback, toggle |
| `short` | 150-250ms | Hover states, focus rings |
| `medium` | 250-400ms | Panel open/close, card lift |
| `long` | 400-700ms | Hero fade-in, skeleton shimmer |

---

## Shopify Storefront Sections

### Navigation
- **Structure:** Simple horizontal. Brand left, 3 links center (Shop, Craftsmanship, About), Search + Cart right.
- **Style:** DM Sans 15px weight 500, text-secondary color, no underlines. Hover: text-primary.
- **Mobile:** Hamburger left, brand center, cart right. Full-screen drawer with links stacked vertically in Cormorant at 28px.
- **No mega-menu.** Catalog is focused. A restrained dropdown only if assortment grows significantly.

### Hero Section
- **Layout:** Split composition — 2fr text / 3fr image. Text left, product image dominant right.
- **Headline:** Cormorant Garamond 52px, weight 400, line-height 1.1.
- **Subtext:** One sentence, DM Sans 17px, text-secondary. No more than 15 words.
- **CTA:** One button only ("Shop Bracelets"). Saffron background, charcoal text, 15px weight 600, 14px/36px padding, 8px radius.
- **Image:** 5:4 aspect ratio. Product on cream linen with hard directional light and visible shadow. 10px radius, subtle box-shadow.
- **Mobile:** Image on top (4:3), headline below at 32px, single CTA.
- **Anti-patterns:** No carousels. No secondary links. No announcement bar badges. No "DESIGN PREVIEW" labels. One message, one action.

### Collection Page
- **Layout:** 3-column grid desktop, 2-column mobile. 24px gaps.
- **Product cards:** Square images (1:1 aspect ratio), 10px radius. Below image: price (Geist Mono 17px), name (DM Sans 17px), positioning line (Cormorant italic 15px).
- **Price-first display:** Price appears before product name. If the price is fair, show it proudly.
- **Positioning lines:** Each product gets a one-line identity ("The one you never take off.") in Cormorant italic.
- **Filtering:** Minimal pill filters only when catalog grows. Categories + price range. No sidebar filters.
- **Editorial insertion:** Every 6 products, insert one full-width editorial tile (craft close-up + short copy). Grid-column: span all.
- **Card hover:** translateY(-3px) lift, 0.3s ease.

### Product Detail Page (PDP)
- **Layout:** 50/50 split on desktop (image left, buy right). Single column on mobile.
- **Image:** 1:1 square, 10px radius, subtle shadow. Below: 4 small thumbnail squares (on-wrist, sparkle, clasp, motion clip).
- **Buy box (sticky on scroll):**
  1. Product name — Cormorant 36px
  2. Price — Geist Mono 26px
  3. One-sentence description — DM Sans 16px, text-secondary
  4. Specs bar — Geist Mono 12px, muted, bordered top/bottom
  5. Metal selector (Yellow/White/Rose Gold) — pill buttons, 14px
  6. Wrist size selector — pill buttons
  7. Delivery line — Geist Mono 12px, accent color: "Ships in 15 business days"
  8. Add to Bag button — full-width, saffron, 16px
  9. Trust line — single line, muted, 13px: "Free insured shipping · 7-day returns · Lifetime warranty"
- **Philosophy:** Everything above the fold. No accordions hiding critical info. The buy flow should feel inevitable, not like a maze.

### Trust Signals
- Structural, not decorative. No tiny badge icons.
- "Ships in 15 business days" near the CTA, in accent color (saffron).
- Trust line below CTA: shipping, returns, warranty in one quiet sentence.
- Below-fold sections: craft timeline (5 steps), founder note, materials passport.
- No star ratings on collection page. Reviews only on PDP if earned.

### Atelier Ledger (unique to Pinaka)
- Live scrolling timeline of recently crafted bracelets: product name, metal, carat, city shipped to, status.
- Status tags: Emerald "Shipped", Amber "Polishing" / "Setting".
- Makes made-to-order feel alive and credible. More ownable than generic reviews.
- Powered by order data from Supabase.

### Craft Timeline
- 5-step horizontal strip: Stone Matching > Gold Forming > Hand Setting > Final Polish > Dispatch.
- Each step: number, name, one-line description. Grid of 5 on desktop, 2-col on mobile.
- Clean cards with subtle shadow.

### Founder Note
- Section with accent-subtle background (saffron at 12% opacity).
- Cormorant italic blockquote from Jaitul. Personal, direct, short.
- Signed with name and "Founder" title.

### Footer
- **Newsletter:** Centered. Cormorant 24px heading, one-line sub, email input + "Join" button.
- **Columns:** 4-column grid — Shop, Help, About, Connect.
- **Links:** DM Sans 14px, text-secondary. Hover: text-primary.
- **Bottom:** Copyright left, Privacy + Terms right. 12px, muted.
- **No "Powered by Shopify."**

### Mobile Patterns
- **Sticky bottom bar on PDP:** Price left, "Add to Bag" right. Appears after scrolling past the buy box.
- **Cart:** Slide-in drawer from right, not a separate page. Show item, 15-day timeline, Checkout button.
- **Touch targets:** All tappable elements minimum 44px tall.
- **Image-first:** On mobile, product image always loads before text.
- **No permanent bottom tab bar.** That's for mass-market apps, not luxury.

### Photography Direction
- **Primary:** Studio-editorial on warm cream surfaces. Hard directional light from upper-left. Visible shadows (proves dimensionality).
- **Secondary:** On-wrist shots for PDP media rail (tight crop on wrist, neutral wardrobe).
- **Process:** Documentation-style craft photos (tools, loupes, workbench). Intentionally less polished than product shots.
- **No lifestyle context photography** on the main site. No brunch, no beach, no linen sheets. The bracelet IS the subject.
- **Image specs:** 2048x2048px minimum for Shopify. WebP format. Lazy loading below fold.
- **Hero shot:** Bracelet in S-curve on cream linen, shot from ~30 degrees above.

### Anti-Patterns (never use)
- Auto-rotating hero carousels
- Black backgrounds with gold text
- Floating review widgets
- 10 tiny trust badges under every CTA
- App-like bottom nav with 5 icons
- "Timeless elegance" / "crafted for you" / "elevate your style" empty luxury copy
- Fake sparkle effects or lens-flare diamonds
- Oversized empty whitespace with no compositional tension
- Purple/violet gradients
- 3-column feature grid with icons in colored circles

---

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-27 | Initial design system created | Created by /design-consultation. Warm luxury direction chosen to differentiate from cold-European competitors. |
| 2026-03-27 | Cream background (#FAF7F2) over pure white | Risk: differentiates from every competitor. Signals heritage and warmth. |
| 2026-03-27 | Saffron accent (#D4A017) over blue/teal | Risk: Indian heritage nod. Pairs with gold. Nobody in jewelry uses this. |
| 2026-03-27 | Gem-named semantic colors | Emerald/Amber/Ruby/Sapphire for success/warning/error/info. On-brand for a jeweler. |
| 2026-03-27 | Cormorant Garamond over Playfair Display | Warmer character, better italic, lighter weight options for screen. |
| 2026-03-29 | Button text: charcoal on gold, not white on gold | White on --accent = 2.5:1 (fails AA). Charcoal on --accent = 5.2:1 (passes AA). |
| 2026-04-02 | Shopify storefront design system | Updated for customer-facing store. Split hero, 1:1 product cards, price-first display, Atelier Ledger, streamlined PDP buy flow. Research: Mejuri, Catbird, Vrai, Brilliant Earth + Codex/Claude outside voices. |
| 2026-04-02 | Max width 1440px | Content fills modern screens without "pipe in center" effect. 48px side padding. |
| 2026-04-02 | Square product images (1:1) | Prevents oversized tall cards. Consistent, compact grid. |
| 2026-04-02 | Simplicity-first philosophy | Less information, bigger fonts, fewer buttons. Purchase flow should be beautiful and frictionless. Customer should barely notice they're checking out. |
| 2026-04-02 | Atelier Ledger as social proof | Live timeline of recent orders replaces generic reviews. Unique to Pinaka, powered by existing Supabase order data. |
| 2026-04-02 | Price-first product display | Price shown before product name. Fair pricing shown proudly, not hidden. |
