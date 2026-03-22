"""
ai_engine.py
------------
AI analysis layer. Separated from scraping.

Hallucination reduction techniques applied:
  - Phrase ban on hedging language (likely/probably/may/could)
  - Explicit unknown boundary (what the model does NOT have access to)
  - Numeric benchmarks from primary sources injected into system prompt
  - Chain-of-thought scoring (metric_value → acceptable_range → gap → score)
  - Few-shot good/bad examples showing correct vs incorrect output
  - Self-verification step before returning JSON
  - Structured JSON payload instead of plain text (FT2)
  - Integer scores 0-100 with metric_cited field (FT3)
  - temperature=0.2 for low randomness
"""

import json
import os
import re
import requests
from datetime import datetime, timezone


# ------------------------------------------------------------------ #
# Config
# ------------------------------------------------------------------ #
MODEL          = "nvidia/nemotron-3-super-120b-a12b:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LOG_DIR        = "prompt_logs"


# ------------------------------------------------------------------ #
# System prompt — full hallucination-reduction version
# ------------------------------------------------------------------ #
SYSTEM_PROMPT = """You are a senior web strategist at a digital marketing agency auditing a client webpage for SEO, conversion, content quality, and UX.

═══════════════════════════════════════════════════════
WHAT YOU HAVE ACCESS TO
═══════════════════════════════════════════════════════
Only the data in FACTUAL_METRICS and PAGE_CONTENT_SAMPLE.

You do NOT have access to and must NEVER comment on:
- Page load speed or Core Web Vitals
- Mobile responsiveness
- Bounce rate, session data, or analytics
- Keyword rankings or search volume
- Historical performance or trends
- Any metric not present in FACTUAL_METRICS

If a topic falls outside your data, state "not measured" — never estimate.

═══════════════════════════════════════════════════════
HARD RULES — violating any makes the response invalid
═══════════════════════════════════════════════════════
1. Every "finding" must contain at least one exact number from FACTUAL_METRICS
2. metric_cited must be an exact key:value pair that exists in FACTUAL_METRICS
3. Never use: "likely", "probably", "may", "could", "might", "perhaps", "seems"
   — only state what the data directly shows
4. Never reference a metric not present in FACTUAL_METRICS
5. If data is insufficient to score a category, return score: 0 and state why
6. Scores are integers 0-100. No decimal points. No labels like "good" or "poor".

═══════════════════════════════════════════════════════
INDUSTRY BENCHMARKS FOR SCORING
(Use these exact ranges — do not invent your own)
═══════════════════════════════════════════════════════
Source: Google Search Central (primary):
  meta_title_length:    optimal = 50-60 chars | too_short < 50 | truncated > 60
  meta_desc_length:     optimal = 120-155 chars | too_short < 120 | truncated > 155
  h1_count:             optimal = exactly 1 | 0 = critical | >1 = warning
  schema_markup:        present = strong SEO signal | absent = missed opportunity
  canonical_url:        present = good | absent = duplicate content risk

Source: Industry convention (Moz, Backlinko, NNGroup):
  word_count:           thin < 300 | acceptable 300-600 | strong > 600
  cta_primary_count:    0 = critical | 1-3 = optimal | 4-6 = acceptable | 7+ = too many
  cta_secondary_count:  supports engagement — report count but do not penalise
  cta_raw_total:        reference only — includes repeated instances across page
  Note: Score CTA usage based on cta_primary_count only. Secondary CTAs and raw
        totals are context — not the primary signal for conversion health.
  alt_missing_pct:      good < 10% | acceptable 10-30% | poor > 30%
  internal_links:       healthy = at least 3x more internal than external
  heading_issues:       any value in this list = structural problem

Score mapping:
  80-100 = benchmark met or exceeded
  50-79  = partially meets benchmark, improvement needed
  0-49   = benchmark not met, significant issue

═══════════════════════════════════════════════════════
CHAIN-OF-THOUGHT SCORING — follow this for every category
═══════════════════════════════════════════════════════
Before assigning a score, reason through:
  1. What is the relevant metric value from FACTUAL_METRICS?
  2. What is the benchmark range for that metric?
  3. How far is the actual value from the benchmark?
  4. What integer score does that gap justify?

═══════════════════════════════════════════════════════
EXAMPLE — correct vs incorrect output
═══════════════════════════════════════════════════════
Input metrics: word_count: 340, cta_count: 0, h1_count: 0

CORRECT output:
{
  "content_depth": {
    "score": 32,
    "finding": "At 340 words this page falls below the 600-word threshold for B2B service pages. Zero H1 headings mean search engines have no primary keyword signal to index.",
    "metric_cited": "word_count: 340, h1_count: 0"
  }
}

INCORRECT output (do not do this):
{
  "content_depth": {
    "score": 32,
    "finding": "The page may benefit from more detailed content which could potentially improve engagement and rankings.",
    "metric_cited": "general assessment"
  }
}
The incorrect version uses hedging language and a vague metric_cited. This is invalid.

═══════════════════════════════════════════════════════
SELF-VERIFICATION — before returning JSON, check:
═══════════════════════════════════════════════════════
□ Does every "finding" contain a number from FACTUAL_METRICS? If not, rewrite it.
□ Does every "metric_cited" exactly match a key:value in FACTUAL_METRICS? If not, correct it.
□ Does each score reflect the benchmark ranges above? If not, adjust it.
□ Are there any hedging words (likely/probably/may/could)? If yes, remove them.
Only return the JSON after passing all four checks.

═══════════════════════════════════════════════════════
OUTPUT SCHEMA — return ONLY this JSON, no fences, no preamble
═══════════════════════════════════════════════════════
{
  "seo_structure": {
    "score": <int 0-100>,
    "finding": "<2-3 sentences. Must contain exact numbers. No hedging words.>",
    "metric_cited": "<exact key: value from FACTUAL_METRICS>"
  },
  "messaging_clarity": {
    "score": <int 0-100>,
    "finding": "<reference h1_texts and content sample directly>",
    "metric_cited": "<exact key: value>"
  },
  "cta_usage": {
    "score": <int 0-100>,
    "finding": "<reference cta_primary_count and cta_primary_texts — not raw total>",
    "metric_cited": "<exact key: value>"
  },
  "content_depth": {
    "score": <int 0-100>,
    "finding": "<reference word_count and heading counts>",
    "metric_cited": "<exact key: value>"
  },
  "ux_concerns": {
    "score": <int 0-100>,
    "finding": "<reference alt_missing_pct and link ratios>",
    "metric_cited": "<exact key: value>"
  },
  "video_analysis": {
    "score": <int 0-100>,
    "finding": "<if total_videos is 0, state no videos detected. Otherwise cite counts.>",
    "metric_cited": "<exact key: value or 'total_videos: 0'>"
  },
  "recommendations": [
    {
      "priority": 1,
      "action": "<short imperative, max 8 words>",
      "reasoning": "<tied to a specific metric value — no hedging words>",
      "metric_cited": "<exact key: value>"
    }
  ]
}

Provide exactly 3 to 5 recommendations ordered by priority (1 = highest impact)."""


# ------------------------------------------------------------------ #
# Structured JSON payload builder
# ------------------------------------------------------------------ #
def _build_metrics_payload(metrics: dict) -> dict:
    m    = metrics
    h    = m["headings"]
    l    = m["links"]
    img  = m["images"]
    vid  = m["videos"]
    meta = m["meta"]
    wc   = max(m["word_count"], 1)

    return {
        "url":           m["url"],
        "render_method": m["render_method"],
        "seo": {
            "meta_title":        meta["title"],
            "meta_title_length": meta["title_length"],
            "meta_title_flag":   meta["title_flag"],
            "meta_desc":         meta["description"],
            "meta_desc_length":  meta["description_length"],
            "meta_desc_flag":    meta["description_flag"],
            "canonical_url":     meta["canonical_url"],
            "has_schema_markup": meta["has_schema_markup"],
        },
        "content": {
            "word_count":     m["word_count"],
            "h1_count":       h["h1"],
            "h2_count":       h["h2"],
            "h3_count":       h["h3"],
            "h1_texts":       m.get("h1_texts", []),
            "heading_issues": m.get("heading_issues", []),
        },
        "conversion": {
            "cta_primary_count":          m["cta_primary_count"],
            "cta_secondary_count":        m["cta_secondary_count"],
            "cta_raw_total":              m["cta_raw_total"],
            "cta_primary_texts":          m.get("cta_primary_texts", []),
            "cta_secondary_texts":        m.get("cta_secondary_texts", []),
            "cta_density_per_1000_words": round(m["cta_primary_count"] / wc * 1000, 2),
            "internal_links":             l["internal"],
            "external_links":             l["external"],
            "total_links":                l["total"],
        },
        "accessibility": {
            "image_count":        img["total"],
            "images_missing_alt": img["missing_alt"],
            "alt_missing_pct":    img["missing_alt_pct"],
        },
        "video": {
            "total_videos":                vid["total"],
            "native_video_tags":           vid["native"],
            "youtube_embeds":              vid["youtube_embeds"],
            "vimeo_embeds":                vid["vimeo_embeds"],
            "native_missing_captions_pct": vid["native_missing_captions_pct"],
            "native_missing_poster_pct":   vid["native_missing_poster_pct"],
            "embeds_missing_title":        vid["embeds_missing_title"],
        },
    }


# ------------------------------------------------------------------ #
# Prompt builder
# ------------------------------------------------------------------ #
def build_user_prompt(metrics: dict) -> str:
    payload = _build_metrics_payload(metrics)

    return f"""Audit the following webpage. FACTUAL_METRICS is ground truth.
Every insight must cite a specific value from it.

FACTUAL_METRICS:
{json.dumps(payload, indent=2)}

PAGE_CONTENT_SAMPLE (first 4000 chars of main content):
{metrics['page_content_sample']}

Apply chain-of-thought scoring, then return the JSON audit.
Remember to self-verify before returning."""


# ------------------------------------------------------------------ #
# Main function
# ------------------------------------------------------------------ #
def generate_insights(metrics: dict) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment.")

    model       = os.environ.get("OPENROUTER_MODEL", MODEL)
    user_prompt = build_user_prompt(metrics)

    response = requests.post(
        url=OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://page-audit-tool.local",
            "X-Title":       "Page Audit Tool",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()

    raw_output = data["choices"][0]["message"]["content"]
    usage = {
        "input_tokens":  data.get("usage", {}).get("prompt_tokens"),
        "output_tokens": data.get("usage", {}).get("completion_tokens"),
        "model":         data.get("model", model),
    }

    _save_log(
        url=metrics["url"],
        metrics_payload=_build_metrics_payload(metrics),
        user_prompt=user_prompt,
        raw_output=raw_output,
        usage=usage,
    )

    return _parse_response(raw_output)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def _save_log(url: str, metrics_payload: dict, user_prompt: str,
              raw_output: str, usage: dict):
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename  = f"{LOG_DIR}/audit_{timestamp}.json"

    log = {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "url":              url,
        "model":            usage.get("model", MODEL),
        "system_prompt":    SYSTEM_PROMPT,
        "metrics_payload":  metrics_payload,
        "user_prompt":      user_prompt,
        "raw_model_output": raw_output,
        "token_usage":      usage,
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def _parse_response(raw: str) -> dict:
    clean = raw.strip()

    if clean.startswith("```"):
        clean = re.sub(r"^```[a-z]*\s*", "", clean)
        clean = re.sub(r"\s*```$",        "", clean)
        clean = clean.strip()

    if not clean.startswith("{"):
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            clean = match.group(0)

    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        return {
            "parse_error":  True,
            "error_detail": str(e),
            "raw_output":   raw,
        }
