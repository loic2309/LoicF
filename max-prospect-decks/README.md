# max prospect decks , how to build one

A small kit to produce a personalised **max** sales deck for a prospect in ~20 minutes.
Each deck is a single self-contained HTML file (no build step, no dependencies) that
opens in any browser and exports to a clean landscape PDF.

> House style is part of the brand. Two non-negotiables:
> 1. **"max" is always lowercase**, including titles and the browser tab.
> 2. **Never use an em-dash (`—`) in prose.** Use a comma, colon, parentheses or period.

---

## Folder layout

```
Max presentations/
├── README.md                       ← this guide
├── _TEMPLATE/
│   └── max-deck-template.html      ← copy this to start a new deck
├── Onboard/
│   ├── max-onboard-deck.html
│   └── max-onboard-deck.pdf
└── deBottomline/
    └── max-debottomline-deck.html
```

One folder per prospect, named after the company. File named `max-<company>-deck.html`.

---

## Build a new deck in 6 steps

1. **Copy the template**
   ```
   cp -R "_TEMPLATE" "<CompanyName>"
   mv "<CompanyName>/max-deck-template.html" "<CompanyName>/max-<company>-deck.html"
   ```

2. **Research the prospect.** Read their homepage. You need:
   - one line on what they do (+ a real tagline if you can quote one),
   - their customer segments (these become the cards on slide 01),
   - the triggers that create demand for their offer (drives the 3 routines).

3. **Fill the placeholders.** Search the file for `{{` and replace every
   `{{PLACEHOLDER}}`. They are commented inline so you know what each one is.

4. **Tailor the two slides that must be specific:**
   - **Signal library (slide 03):** delete categories/signals that are irrelevant to
     the prospect, then fix the per-category count chips **and** the number word in the
     title (e.g. "Sixteen ways…"). See the full signal list below.
   - **3 routines (slide 05):** each routine = a real signal (in bold) + what it does
     for *this* prospect, concretely. Daily / Weekly / On-demand cadence works well.

5. **Pricing (slide 07):** keep the plans the prospect should see.
   - Default shows **Monthly** + **12-month** side by side.
   - For annual-only, set the grid to one column and keep the 12-month card:
     `<div class="price-grid" style="grid-template-columns:1fr;max-width:520px;margin:38px auto 0">`

6. **Review:** read every slide once for lowercase "max" and zero em-dashes.

---

## Present it

Open the HTML in a browser. Navigate with **→ / Space** (next), **← (prev)**,
**Home / End**, or the bar at the bottom. Slide 06 ("open max") is your cue to
switch to the live product.

---

## Export to PDF (landscape, one slide per page)

Requires Google Chrome. From the deck's folder:

```
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="max-<company>-deck.pdf" \
  --virtual-time-budget=8000 \
  "file://$(pwd)/max-<company>-deck.html"
```

The template's `@media print` block already sets a 1280×720 landscape page so each
slide maps to exactly one page.

---

## Deck structure (9 slides)

| # | Slide | Reusable as-is? |
|---|-------|-----------------|
| Cover | Hook + one-line max pitch | Edit company + hook |
| 01 | What the prospect does + their customer segments | **Specific** |
| 02 | What max is (Sees / Finds / Drafts) | As-is |
| 03 | Signal library | **Specific** (trim signals) |
| 04 | The database (100M+ / email+LinkedIn / phone soon) | As-is |
| 05 | 3 routines | **Specific** |
| 06 | Live demo switch | As-is |
| 07 | Pricing | Pick the plans |
| 08 | Close | Edit company name |

---

## Design tokens (already in the template, do not drift)

| Token | Value | Use |
|-------|-------|-----|
| Titles | `Noto Serif` 400, `#0A0A0A`, letter-spacing -1.2px | h1/h2, one italic/gradient accent word |
| Body / UI | `Inter` 400–600, `#6E6E73` | paragraphs, cards |
| Background | `#FFFFFF`, alt sections `#FAFAFA` | |
| Hairlines | `#EAEAEA` | borders, the signature vertical frame |
| Accent (punchy blue) | `--accent #2F54EB` → `--accent-2 #6E97FF` | gradient accent words, dots, pills |
| CTA / dark | `#0A0A0A`, white text, radius 6px | "best value" badge |
| Logo | `https://yourmax.ai/logo-max.svg` | wordmark, lowercase |

---

## The full signal catalogue (pick what's relevant per prospect)

**LinkedIn activity (8)** , Job Offers · Republished Job Offers · Recruitment Campaign ·
Job Changes · Fundraising · Mergers & Acquisitions · Social Mentions · Reactions
**Brand & competitor pull (4)** , Company Page Engagement · Influencer Engagement ·
Competitor Social Reactions · Competitor Relationships
**Radar / visitors (2)** , Radar Website · Radar Sortlist
**Public sector (2)** , Public Tenders · Public Contract Award
**New companies (2)** , Company Registration · Google Maps New Business Listing (France)

That's 18 total. The template ships with the 16 that apply to most B2B prospects
(Radar Sortlist and Google Maps dropped); add them back if a prospect is on Sortlist
or France-focused on local openings.

---

## Pricing reference

| Plan | Price | Credits | ≈ per lead |
|------|-------|---------|-----------|
| Monthly (6-month commitment) | €199 / mo | 663 / month | €0.30 |
| 12-month beta (best value) | €2,100 | 7,950 | €0.26 |

1 credit = 1 qualified lead (named decision-maker with contact details). Paid beta,
early pricing locked in for beta partners.
