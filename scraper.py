"""
scraper.py
----------
Universal metric extraction. Works on ALL tech stacks.

3-tier strategy:
  Tier 1 — Static fetch (requests + BS4)
            Works for: WordPress, PHP, Django, plain HTML
  Tier 2 — Next.js __NEXT_DATA__ extraction
            Works for: Next.js, Gatsby (SSR apps with hydration JSON)
  Tier 3 — Playwright headless browser
            Works for: React, Vue, Angular, Svelte SPAs
            Uses `load` + analytics blocking (never networkidle)

Fine-tune updates applied:
  - Meta title/desc char length flags (optimal/too_short/truncated)
  - Heading hierarchy validator (missing_h1, duplicate_h1, h3_before_h2)
  - Canonical tag detection
  - Schema.org markup detection
  - h1_texts extracted for AI context
  - Word count now excludes nav, header, footer, aside (main content only)
"""

import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin


# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

BLOCKED_DOMAINS = [
    "google-analytics.com", "analytics.google.com", "googletagmanager.com",
    "doubleclick.net", "googlesyndication.com", "facebook.net",
    "facebook.com/tr", "connect.facebook.net", "hotjar.com", "clarity.ms",
    "segment.com", "cdn.segment.io", "mixpanel.com", "amplitude.com",
    "intercom.io", "crisp.chat", "hs-scripts.com", "hsforms.net",
    "twitter.com/i/adsct", "ads.linkedin.com",
]

# Primary CTAs — direct conversion actions
PRIMARY_CTA_PATTERN = re.compile(
    r'^(book\s?(now|direct|a\s?room|online)?|reserve(\s?now)?|'
    r'check\s?availability|get\s?a\s?quote|contact\s?us|'
    r'request\s?a?\s?demo|book\s?a\s?demo|get\s?started|'
    r'start\s?free(\s?trial)?|try\s?free|sign\s?up|subscribe|'
    r'get\s?in\s?touch|talk\s?to\s?us|buy\s?now|shop\s?now|'
    r'download\s?now|schedule\s?a?\s?call|enquire(\s?now)?|'
    r'apply\s?now|order\s?now|add\s?to\s?cart|checkout)$',
    re.IGNORECASE
)

# Secondary CTAs — engagement / discovery actions
SECONDARY_CTA_PATTERN = re.compile(
    r'^(discover\s?more|learn\s?more|explore|find\s?out\s?more|'
    r'read\s?more|see\s?more|show\s?me|view\s?(all|more|details)?|'
    r'browse|see\s?details|more\s?details|view\s?offer|'
    r'see\s?offer|see\s?packages|view\s?rooms|see\s?rooms|'
    r'find\s?out|know\s?more)$',
    re.IGNORECASE
)

# UI components — not CTAs, exclude from count
UI_BUTTON_BLOCKLIST = {
    "close", "open", "menu", "search", "filter", "sort", "apply",
    "next", "previous", "prev", "back", "forward", "submit",
    "play", "pause", "stop", "mute", "unmute", "cancel", "reset",
    "clear", "ok", "yes", "no", "confirm", "decline", "accept",
    "login", "log in", "sign in", "register", "logout", "log out",
    "select", "choose", "toggle", "show more", "show less",
    "load more", "×", "✕", "›", "‹", "»", "«", "+", "-", ">", "<",
}

YOUTUBE_PATTERN = re.compile(r'(youtube\.com/embed|youtu\.be)', re.IGNORECASE)
VIMEO_PATTERN   = re.compile(r'vimeo\.com', re.IGNORECASE)


# ------------------------------------------------------------------ #
# Public entry point
# ------------------------------------------------------------------ #
def scrape_page(url: str) -> dict:
    html, render_method = _fetch_html(url)
    return _extract_metrics(html, url, render_method)


# ------------------------------------------------------------------ #
# Tier routing
# ------------------------------------------------------------------ #
def _fetch_html(url: str) -> tuple:
    # ── Tier 1: Static fetch ──────────────────────────────────────── #
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        html = response.text
    except requests.exceptions.Timeout:
        raise ValueError(f"Request timed out for {url}")
    except requests.exceptions.HTTPError as e:
        raise ValueError(f"HTTP error {e.response.status_code} for {url}")
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Failed to reach {url}: {str(e)}")

    # ── Tier 2: Next.js __NEXT_DATA__ check ──────────────────────── #
    if "__NEXT_DATA__" in html or "self.__next_f.push" in html:
        soup_check = BeautifulSoup(html, "html.parser")
        for tag in soup_check(["script", "style", "noscript", "head"]):
            tag.decompose()
        text_check = soup_check.get_text(separator=" ", strip=True)
        wc = len(re.findall(r'\b\w+\b', text_check))
        if wc >= 30:
            return html, "next.js-ssr"
        return _fetch_with_playwright(url), "playwright"

    # ── Check if static HTML has real content ─────────────────────── #
    soup_check = BeautifulSoup(html, "html.parser")
    for tag in soup_check(["script", "style", "noscript", "head"]):
        tag.decompose()
    text_check = soup_check.get_text(separator=" ", strip=True)
    wc      = len(re.findall(r'\b\w+\b', text_check))
    h_count = len(soup_check.find_all(["h1", "h2", "h3"]))

    if wc >= 50 or h_count > 0:
        return html, "static"

    # ── Tier 3: Playwright for JS-rendered pages ──────────────────── #
    try:
        return _fetch_with_playwright(url), "playwright"
    except Exception as e:
        error_msg = str(e).lower()
        # If Playwright binary missing (hosting env) — fall back to static HTML
        if "executable doesn't exist" in error_msg or "playwright" in error_msg:
            return html, "static-fallback"
        raise


def _fetch_with_playwright(url: str) -> str:
    import asyncio
    import concurrent.futures

    async def _run():
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ValueError(
                "Playwright not installed. Run: "
                "pip install playwright && python -m playwright install chromium"
            )

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            context = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800},
                java_script_enabled=True,
            )
            page = await context.new_page()

            async def block_analytics(route, request):
                req_url = request.url.lower()
                for domain in BLOCKED_DOMAINS:
                    if domain in req_url:
                        await route.abort()
                        return
                await route.continue_()

            await page.route("**/*", block_analytics)

            try:
                await page.goto(url, wait_until="load", timeout=30000)
            except Exception:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    await browser.close()
                    raise ValueError(f"Could not load page: {str(e)}")

            try:
                await page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass

            await page.wait_for_timeout(2500)
            html = await page.content()
            await browser.close()

        return html

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run())
        return future.result()


# ------------------------------------------------------------------ #
# Metric extraction — works on any HTML string
# ------------------------------------------------------------------ #
def _extract_metrics(html: str, url: str, render_method: str) -> dict:
    soup        = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(url).netloc

    # ── 1. Meta title + description (BEFORE stripping head) ──────── #
    title_tag  = soup.find("title")
    meta_title_text = title_tag.get_text(strip=True) if title_tag else ""

    desc_tag = (
        soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or
        soup.find("meta", attrs={"property": re.compile("^og:description$", re.I)}) or
        soup.find("meta", attrs={"name": re.compile("^twitter:description$", re.I)})
    )
    meta_desc_text = desc_tag.get("content", "").strip() if desc_tag else ""

    # Meta length flags (FT1)
    meta_title_length = len(meta_title_text)
    meta_title_flag = (
        "too_short"  if meta_title_length < 50
        else "truncated" if meta_title_length > 60
        else "optimal"
    )
    meta_desc_length = len(meta_desc_text)
    meta_desc_flag = (
        "too_short"  if meta_desc_length < 120
        else "truncated" if meta_desc_length > 155
        else "optimal"
    )

    # ── 2. Heading hierarchy (BEFORE stripping) ───────────────────── #
    h1_tags = soup.find_all("h1")
    h2_tags = soup.find_all("h2")
    h3_tags = soup.find_all("h3")

    heading_issues = []
    if len(h1_tags) == 0:
        heading_issues.append("missing_h1")
    if len(h1_tags) > 1:
        heading_issues.append("duplicate_h1")
    if len(h3_tags) > 0 and len(h2_tags) == 0:
        heading_issues.append("h3_before_h2")

    h1_texts = [h.get_text(strip=True) for h in h1_tags]

    # ── 3. Canonical tag (BEFORE stripping) ───────────────────────── #
    canonical_tag = soup.find("link", rel="canonical")
    canonical_url = canonical_tag["href"] if canonical_tag else None

    # ── 4. Schema.org markup (BEFORE stripping) ───────────────────── #
    schema_scripts   = soup.find_all("script", attrs={"type": "application/ld+json"})
    has_schema_markup = len(schema_scripts) > 0

    # ── 5. Video detection (BEFORE stripping) ─────────────────────── #
    videos = _extract_video_metrics(soup)

    # ── 6. Strip structural chrome + non-visible elements ─────────── #
    # FT4: Remove nav/header/footer/aside FIRST — word count = main content only
    for tag in soup(["nav", "footer", "header", "aside",
                     "script", "style", "noscript", "head", "svg"]):
        tag.decompose()

    visible_text = soup.get_text(separator=" ", strip=True)
    word_count   = len(re.findall(r'\b\w+\b', visible_text))

    # ── 7. Headings (post-strip count for display) ────────────────── #
    h1 = len(h1_tags)
    h2 = len(h2_tags)
    h3 = len(h3_tags)

    # ── 8. CTAs — three-tier: primary / secondary / raw ──────────── #
    primary_texts   = set()
    secondary_texts = set()
    raw_cta_count   = 0

    # Scan all buttons
    for b in soup.find_all("button"):
        text = b.get_text(strip=True).lower().strip()
        if not text or text in UI_BUTTON_BLOCKLIST:
            continue
        raw_cta_count += 1
        if PRIMARY_CTA_PATTERN.match(text):
            primary_texts.add(text)
        elif SECONDARY_CTA_PATTERN.match(text):
            secondary_texts.add(text)

    # Scan all links
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower().strip()
        if not text or text in UI_BUTTON_BLOCKLIST:
            continue
        if PRIMARY_CTA_PATTERN.match(text) or SECONDARY_CTA_PATTERN.match(text):
            raw_cta_count += 1
            if PRIMARY_CTA_PATTERN.match(text):
                primary_texts.add(text)
            elif SECONDARY_CTA_PATTERN.match(text):
                secondary_texts.add(text)

    cta_primary_count   = len(primary_texts)    # unique conversion CTAs
    cta_secondary_count = len(secondary_texts)  # unique engagement CTAs
    cta_count           = cta_primary_count     # used for scoring (primary only)

    # ── 9. Links ─────────────────────────────────────────────────── #
    internal_links = 0
    external_links = 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript"):
            continue
        full_url    = urljoin(url, href)
        link_domain = urlparse(full_url).netloc
        if (
            link_domain.replace("www.", "") == base_domain.replace("www.", "")
            or not link_domain
        ):
            internal_links += 1
        else:
            external_links += 1

    # ── 10. Images ───────────────────────────────────────────────── #
    images      = soup.find_all("img")
    total_imgs  = len(images)
    missing_alt = sum(1 for img in images if not img.get("alt", "").strip())
    missing_alt_pct = round(missing_alt / total_imgs * 100, 1) if total_imgs > 0 else 0.0

    # ── 11. Content sample for AI ─────────────────────────────────── #
    page_content_sample = " ".join(visible_text.split())[:4000]

    return {
        "url":            url,
        "render_method":  render_method,
        "word_count":     word_count,
        "headings": {
            "h1": h1, "h2": h2, "h3": h3,
        },
        "h1_texts":       h1_texts,
        "heading_issues": heading_issues,
        "cta_count":            cta_count,
        "cta_primary_count":    cta_primary_count,
        "cta_secondary_count":  cta_secondary_count,
        "cta_raw_total":        raw_cta_count,
        "cta_primary_texts":    sorted(primary_texts),
        "cta_secondary_texts":  sorted(secondary_texts),
        "links": {
            "internal": internal_links,
            "external": external_links,
            "total":    internal_links + external_links,
        },
        "images": {
            "total":           total_imgs,
            "missing_alt":     missing_alt,
            "missing_alt_pct": missing_alt_pct,
        },
        "videos": videos,
        "meta": {
            "title":            meta_title_text or "Not found",
            "title_length":     meta_title_length,
            "title_flag":       meta_title_flag,
            "description":      meta_desc_text or "Not found",
            "description_length": meta_desc_length,
            "description_flag": meta_desc_flag,
            "canonical_url":    canonical_url,
            "has_schema_markup": has_schema_markup,
        },
        "page_content_sample": page_content_sample,
    }


# ------------------------------------------------------------------ #
# Video extraction
# ------------------------------------------------------------------ #
def _extract_video_metrics(soup: BeautifulSoup) -> dict:
    native_videos = soup.find_all("video")
    native_count  = len(native_videos)

    missing_captions = sum(
        1 for v in native_videos
        if not v.find("track", attrs={"kind": re.compile(r"subtitles|captions", re.I)})
    )
    missing_poster = sum(
        1 for v in native_videos if not v.get("poster", "").strip()
    )
    missing_aria = sum(
        1 for v in native_videos
        if not v.get("aria-label", "").strip() and not v.get("title", "").strip()
    )

    iframes        = soup.find_all("iframe")
    youtube_embeds = [
        f for f in iframes
        if YOUTUBE_PATTERN.search(f.get("src", "") or f.get("data-src", ""))
    ]
    vimeo_embeds = [
        f for f in iframes
        if VIMEO_PATTERN.search(f.get("src", "") or f.get("data-src", ""))
    ]
    embed_missing_title = sum(
        1 for f in youtube_embeds + vimeo_embeds
        if not f.get("title", "").strip()
    )

    mc_pct = round(missing_captions / native_count * 100, 1) if native_count else 0.0
    mp_pct = round(missing_poster   / native_count * 100, 1) if native_count else 0.0

    return {
        "total":                       native_count + len(youtube_embeds) + len(vimeo_embeds),
        "native":                      native_count,
        "youtube_embeds":              len(youtube_embeds),
        "vimeo_embeds":                len(vimeo_embeds),
        "native_missing_captions":     missing_captions,
        "native_missing_captions_pct": mc_pct,
        "native_missing_poster":       missing_poster,
        "native_missing_poster_pct":   mp_pct,
        "native_missing_aria":         missing_aria,
        "embeds_missing_title":        embed_missing_title,
    }
