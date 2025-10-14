#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic scraper for Quotes to Scrape (JS-rendered).
- Source: https://quotes.toscrape.com/js/
- Outputs: data/quotes_dynamic_YYYYMMDD_p{pages}.csv
- Fields (example schema): id, source, title, url, author/vendor, category,
  date (YYYYMMDD), price/value, last_seen_at
- Features: JS rendering via Playwright, robots.txt check, retry for flaky nav,
  dedup, date normalization, numeric validation, error logging.
"""

import argparse
import csv
import hashlib
import logging
import os
import random
import time
from datetime import datetime
from urllib.parse import urljoin
from urllib import robotparser

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------------- Config ----------------------
BASE_URL = "https://quotes.toscrape.com/"
JS_BASE = urljoin(BASE_URL, "js/")
PAGE_URL = lambda n: urljoin(JS_BASE, f"page/{n}/")  # js/page/2/
SOURCE_NAME = "quotes_toscrape_js"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ---------------------- Logging ----------------------
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("dynamic_scraper")
logger.setLevel(logging.INFO)
fh = logging.FileHandler("logs/dynamic_errors.log", encoding="utf-8")
fh.setLevel(logging.WARNING)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
fh.setFormatter(fmt)
ch.setFormatter(fmt)
# Avoid duplicate handlers if reloaded
logger.handlers = [fh, ch]

# ---------------------- Helpers ----------------------
def respectful_delay(min_s=0.5, max_s=1.2):
    time.sleep(random.uniform(min_s, max_s))

def can_fetch(url: str) -> bool:
    """Respect robots.txt (conservative fallback if unreadable)."""
    rp = robotparser.RobotFileParser()
    robots_url = urljoin(BASE_URL, "robots.txt")
    try:
        rp.set_url(robots_url)
        rp.read()
    except Exception as e:
        logger.warning(f"robots.txt read failed ({robots_url}): {e}")
        return url.startswith(BASE_URL)
    return rp.can_fetch(UA, url)

def norm_date(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")

def value_from_title(text: str) -> float:
    """Example numeric field: use quote length as a numeric 'value'."""
    return float(len(text.strip()))

def md5_id(text: str, author: str) -> str:
    """Stable id for a quote: md5(text + author)."""
    h = hashlib.md5()
    h.update((text.strip() + "|" + author.strip()).encode("utf-8"))
    return h.hexdigest()

def extract_quotes(page) -> list[dict]:
    """Extract quotes on current rendered page."""
    # Each quote block: .quote
    blocks = page.query_selector_all(".quote")
    items = []
    for b in blocks:
        text = (b.query_selector("span.text") or b).inner_text().strip()
        # strip leading/ending fancy quotes if present
        text = text.strip("“”\"' \n\r\t")
        author = (b.query_selector("small.author") or b).inner_text().strip()
        tags = [t.inner_text().strip() for t in b.query_selector_all(".tags a.tag")]
        url = page.url  # page-level url (no per-quote link on JS version)
        items.append({
            "title": text,
            "author": author,
            "tags": tags,
            "url": url
        })
    return items

def goto_with_retry(page, url: str, max_tries: int = 5, base_delay: float = 1.0):
    """Navigate with simple exponential backoff for flaky network."""
    if not can_fetch(url):
        raise PermissionError(f"Disallowed by robots.txt: {url}")

    last_exc = None
    for i in range(max_tries):
        try:
            resp = page.goto(url, timeout=25000, wait_until="domcontentloaded")
            # Some pages may return None response (file/protocol); guard it
            if resp and resp.status >= 400:
                # 404/5xx: don't retry 404, do limited retry for 5xx
                if resp.status == 404:
                    return resp
                if resp.status in (500, 502, 503, 504):
                    delay = base_delay * (2 ** i) + random.uniform(0, 0.5)
                    logger.warning(f"{resp.status} on {url}; retry in {delay:.1f}s (attempt {i+1}/{max_tries})")
                    time.sleep(delay)
                    continue
            # Wait for quotes rendered
            page.wait_for_selector(".quote", timeout=10000)
            return resp
        except PWTimeout as e:
            last_exc = e
            delay = base_delay * (2 ** i) + random.uniform(0, 0.5)
            logger.warning(f"Timeout loading {url}; retry in {delay:.1f}s (attempt {i+1}/{max_tries})")
            time.sleep(delay)
        except Exception as e:
            last_exc = e
            delay = base_delay * (2 ** i) + random.uniform(0, 0.5)
            logger.warning(f"Error loading {url}: {e}; retry in {delay:.1f}s (attempt {i+1}/{max_tries})")
            time.sleep(delay)
    raise RuntimeError(f"Failed to load after {max_tries} tries: {url} ({last_exc})")

# ---------------------- Main ----------------------
def scrape_quotes(num_pages: int = 5, outdir: str = "data", headless: bool = True) -> str:
    os.makedirs(outdir, exist_ok=True)
    crawl_time = datetime.now()
    today = norm_date(crawl_time)

    rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--disable-dev-shm-usage"])
        context = browser.new_context(user_agent=UA)
        page = context.new_page()

        # Page 1 uses JS_BASE; others use PAGE_URL(n)
        for page_no in range(1, num_pages + 1):
            url = JS_BASE if page_no == 1 else PAGE_URL(page_no)
            logger.info(f"Render page {page_no}: {url}")
            respectful_delay()
            resp = goto_with_retry(page, url)

            # 404: likely reached beyond last page
            if resp and resp.status == 404:
                logger.info(f"Reached end at page {page_no} (HTTP 404).")
                break

            # Extract quotes
            quotes = extract_quotes(page)
            if not quotes:
                logger.info(f"No quotes found on page {page_no}; stopping.")
                break

            for q in quotes:
                title = q["title"]
                author = q["author"]
                tags = q["tags"]
                url_here = q["url"]

                # Build schema-compatible row
                rec_id = md5_id(title, author)
                value_numeric = value_from_title(title)  # simple numeric demo

                row = {
                    "id": rec_id,
                    "source": SOURCE_NAME,
                    "title": title,
                    "url": url_here,
                    "author": author,                     # 這個站點用作者填入
                    "category": ",".join(tags) if tags else "",  # tags 當成分類
                    "date": today,                               # 無發布日 → 爬取日
                    "last_seen_at": today,
                }
                rows.append(row)

        browser.close()

    if not rows:
        raise SystemExit("No rows scraped. Check connectivity or selectors.")

    # DataFrame + cleaning
    df = pd.DataFrame(rows, columns=[
        "id", "source", "title", "url", "author",
        "category", "date", "last_seen_at"
    ])

    # Dedup (same quote+author across pages)
    before = len(df)
    df = df.drop_duplicates(subset=["source", "id"]).reset_index(drop=True)
    after = len(df)
    if after < before:
        logger.info(f"Deduplicated: {before - after} duplicates removed.")

    # Sanity: date fields must be YYYYMMDD
    for col in ("date", "last_seen_at"):
        bad = ~df[col].astype(str).str.fullmatch(r"\d{8}")
        if bad.any():
            df.loc[bad, col] = today

    out_path = os.path.join(outdir, f"quotes_dynamic_{today}_p{num_pages}.csv")
    df.to_csv(out_path, index=False, quoting=csv.QUOTE_MINIMAL)
    logger.info(f"Saved {len(df)} rows -> {out_path}")
    return out_path

def main():
    ap = argparse.ArgumentParser(description="Dynamic scraper for Quotes to Scrape (JS).")
    ap.add_argument("--pages", type=int, default=5, help="要爬的頁數（js/page/{n}）")
    ap.add_argument("--outdir", type=str, default="data", help="輸出資料夾")
    ap.add_argument("--no-headless", action="store_true", help="用可見瀏覽器（除錯用）")
    args = ap.parse_args()

    try:
        scrape_quotes(num_pages=args.pages, outdir=args.outdir, headless=not args.no_headless)
    except Exception as e:
        logger.error(f"Scrape failed: {e}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()