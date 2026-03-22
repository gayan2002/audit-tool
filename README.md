# Page Audit Tool — v3

An AI-powered single-page website auditor. Works on all tech stacks. Drop in a URL and get factual metrics plus grounded AI insights in seconds.

Built for EIGHT25MEDIA's AI-Native Software Engineer assignment.

---

## Live Demo

| Link | Description |
|------|-------------|
| **App** | https://audit-tool-production-6990.up.railway.app |
| **Prompt Logs** | https://audit-tool-production-6990.up.railway.app/logs |
| **GitHub** | https://github.com/gayan2002/audit-tool |

> The `/logs` endpoint returns the last 5 prompt logs as JSON — showing the full system prompt, structured metrics payload sent to the AI, and raw model output before parsing.

---

## Quick Start

```bash
git clone https://github.com/your-username/audit-tool
cd audit-tool

pip install -r requirements.txt
python -m playwright install chromium

cp .env.example .env
# Edit .env and add: OPENROUTER_API_KEY=your-key-here

python main.py
# Open http://localhost:8000
```

---

## Architecture Overview

Three clearly separated layers — each with one job, no cross-contamination.

```
User (browser)
     │  POST /audit { url }
     ▼
┌─────────────────────────────────────────────────────────┐
│  main.py — FastAPI                                       │
│  Validates input, calls scraper, calls AI, returns JSON │
└────────────────┬──────────────────┬─────────────────────┘
                 │                  │
                 ▼                  ▼
┌────────────────────┐   ┌───────────────────────────────┐
│  scraper.py        │   │  ai_engine.py                  │
│                    │   │                                │
│  3-tier strategy:  │   │  Builds structured JSON prompt │
│  1. Static fetch   │   │  Calls OpenRouter API          │
│  2. Next.js SSR    │   │  Saves prompt log              │
│  3. Playwright SPA │   │  Returns parsed JSON insights  │
│                    │   └───────────────────────────────┘
│  Returns plain     │
│  dict of metrics   │
└────────────────────┘
```

---

## 3-Tier Scraping Strategy

The scraper works on all web tech stacks by trying each tier in order:

| Tier | Method | Works For | Speed |
|------|--------|-----------|-------|
| 1 | requests + BeautifulSoup | WordPress, PHP, Django, plain HTML | ~0.5s |
| 2 | Detect `__NEXT_DATA__` JSON in static HTML | Next.js, Gatsby, React SSR | ~0.5s |
| 3 | Playwright headless Chromium | React SPA, Vue, Angular, Svelte | ~5-8s |

Playwright uses `wait_until='load'` (never `networkidle`) and blocks 15+ analytics domains to prevent timeout hangs on heavy e-commerce sites.

---

## Metrics Extracted

**SEO:**
- Meta title + character length + flag (optimal / too_short / truncated)
- Meta description + character length + flag
- Canonical tag presence and URL
- Schema.org markup detected (application/ld+json)
- H1 actual text content

**Content:**
- Word count (nav, header, footer, aside excluded — main content only)
- H1 / H2 / H3 counts
- Heading hierarchy issues (missing_h1, duplicate_h1, h3_before_h2)

**Conversion:**
- CTA count (buttons + CTA-class/text links)
- CTA density per 1000 words
- Internal vs external links

**Accessibility:**
- Total images + % missing alt text

**Video:**
- Native `<video>` tags, YouTube embeds, Vimeo embeds
- Missing captions, missing poster images, missing iframe titles

---

## AI Design Decisions

### Hallucination Reduction Techniques

**1. Phrase ban on hedging language**
The system prompt explicitly bans: "likely", "probably", "may", "could", "might", "perhaps", "seems". These words are signals that the model is guessing. Banning them forces the model to only state what the data shows.

**2. Explicit unknown boundary**
The system prompt lists everything the model does NOT have access to: page speed, mobile responsiveness, analytics, rankings. Without this, models fill knowledge gaps with plausible-sounding inventions.

**3. Industry benchmarks injected into system prompt**
Rather than letting the model invent thresholds, the prompt provides them explicitly:
- Meta title: 50-60 chars (source: Google Search Central)
- Meta description: 120-155 chars (source: Google Search Central / John Mueller)
- CTA density: 1-5 per 1000 words (source: CXL Institute, NNGroup)
- Word count: 600+ for B2B pages (source: Backlinko, HubSpot aggregate research)

**4. Chain-of-thought scoring**
The model is instructed to reason through metric value → benchmark → gap → score before returning a number. This prevents contradictory scores and unsupported claims.

**5. Few-shot good/bad examples**
The system prompt includes a correct and incorrect example of the same audit showing the contrast between grounded findings and generic advice.

**6. `metric_cited` field requirement**
Every insight block requires a `metric_cited` field naming the exact key:value from the metrics payload. This makes hallucinations visible in prompt logs — the evaluator can verify every claim.

**7. Self-verification step**
The prompt ends with a four-point checklist the model is instructed to check before returning JSON.

**8. Structured JSON metrics payload**
Metrics are sent as grouped JSON (seo, content, conversion, accessibility, video) rather than a plain text block. This is machine-readable, auditable in prompt logs, and reduces the model's tendency to misread formatted text.

**9. temperature=0.2**
Low temperature keeps the model close to its most likely output, reducing creative but incorrect responses.

### Benchmark Source Transparency

| Benchmark | Source | Type |
|-----------|--------|------|
| Meta title 50-60 chars | Google Search Central | Primary |
| Meta description 120-155 chars | Google Search Central, John Mueller | Primary |
| H1 = exactly 1 | Google Search Central best practice | Primary |
| Schema.org markup | schema.org (Google/Microsoft/Yahoo/Yandex) | Primary |
| Word count 300/600 thresholds | Backlinko, HubSpot aggregate studies | Industry convention |
| CTA density 1-5/1000 words | NNGroup, CXL Institute | Industry convention |
| Alt text < 10% missing | WCAG, Google Lighthouse | Primary (accessibility) |
| Internal link ratio | SEO practitioner convention | Heuristic |

---

## Trade-offs

**Scores are model-generated, not formula-based.**
The AI assigns integer scores based on the benchmarks provided in the prompt. Two runs on the same page may return slightly different scores. The production-grade fix is Python rubrics that calculate scores deterministically — the AI would then only write the finding and recommendation text. This is the highest-priority improvement for a v4.

**CTA detection is heuristic.**
The scraper detects CTAs using class-name patterns and known action text. It will miss CTAs using non-semantic markup or generic class names like `class="link"`.

**Word count excludes nav/header/footer/aside** but may still include sidebars or cookie banners depending on site structure.

**Bot-protected sites** (those that detect headless browsers) return empty pages even with Playwright. Full coverage requires residential proxy rotation, which is out of scope.

**Free OpenRouter models** have rate limits and their model IDs change. Production use should pin a specific paid model.

---

## What I Would Improve With More Time

1. **Deterministic scoring rubrics in Python** — move score calculation out of the AI and into hardcoded Python functions for each metric. AI only writes finding and recommendation text.

2. **Core Web Vitals via Google PageSpeed Insights API** — adds LCP, CLS, FID to the factual metrics layer without any AI estimation.

3. **Playwright with residential proxies** — handles bot-protected e-commerce sites properly.

4. **Redis caching by URL + content hash** — repeat audits are instant and don't burn API credits.

5. **Competitor comparison view** — audit two URLs in parallel and diff the metrics and scores.

6. **Scoring rubric as YAML config** — lets non-engineers tune what "good" means for different site types.

---

## Prompt Logs

Every audit writes a timestamped JSON to `/prompt_logs/`. Each log contains:
- `timestamp`
- `url`
- `model`
- `system_prompt` — the full system prompt including all benchmarks and rules
- `metrics_payload` — the structured JSON sent to the model (machine-readable)
- `user_prompt` — the full constructed prompt
- `raw_model_output` — the model's response before any parsing
- `token_usage` — input and output token counts

Prompt logs are gitignored by default. To include sample logs in the repo, copy them to `/prompt_logs/examples/`.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI + Uvicorn |
| Scraping (static) | requests + BeautifulSoup4 |
| Scraping (JS) | Playwright (Chromium) |
| AI | OpenRouter API (model configurable via .env) |
| Frontend | Vanilla HTML/CSS/JS (no build step) |
| Config | python-dotenv |
