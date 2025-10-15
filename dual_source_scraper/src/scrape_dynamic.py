#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic scraper for Next Apple Taiwan (新蘋果新聞網).
- Source: https://tw.nextapple.com/
- Outputs: data/nextapple_dynamic_YYYYMMDD_n{count}.csv
- Fields: id, source, title, url, author, category, date (YYYYMMDD), last_seen_at
- Features: JS rendering via Playwright, robots.txt check, scroll-to-load,
  response-timeout retry, dedup, date normalization, error logging.
"""

import argparse
import csv
import hashlib
import logging
import os
import random
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse
from urllib import robotparser

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------------- Config ----------------------
BASE_URL = "https://tw.nextapple.com/"
SOURCE_NAME = "nextapple_tw"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

ARTICLE_HREF_RE = re.compile(r"^/(?:[a-z0-9\-]+/)?article/[^/]+/?$", re.I)

# ---------------------- Logging ----------------------
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("nextapple_dynamic_scraper")
logger.setLevel(logging.INFO)
fh = logging.FileHandler("logs/nextapple_dynamic_errors.log", encoding="utf-8")
fh.setLevel(logging.WARNING)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
fh.setFormatter(fmt)
ch.setFormatter(fmt)
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
        # fallback: allow only same-origin
        return url.startswith(BASE_URL)
    return rp.can_fetch(UA, url)

def norm_date(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")

def md5_id(text: str, url: str) -> str:
    """Stable id: md5(title + url)."""
    h = hashlib.md5()
    h.update((text.strip() + "|" + url.strip()).encode("utf-8"))
    return h.hexdigest()

def to_abs(href: str) -> str:
    if href.startswith("http"):
        return href
    return urljoin(BASE_URL, href)

def same_host(url: str) -> bool:
    return urlparse(url).netloc.endswith("nextapple.com.tw")

def parse_meta(page, name=None, prop=None) -> str | None:
    """Get meta content by name= or property=."""
    if name:
        el = page.query_selector(f'meta[name="{name}"]')
        if el:
            v = el.get_attribute("content")
            if v:
                return v.strip()
    if prop:
        el = page.query_selector(f'meta[property="{prop}"]')
        if el:
            v = el.get_attribute("content")
            if v:
                return v.strip()
    return None

def extract_article_links(page, max_links: int) -> list[str]:
    """Collect article links from current page (after rendering & optional scrolling)."""
    links = set()
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        if ARTICLE_HREF_RE.match(href):
            url = to_abs(href)
            if same_host(url):
                links.add(url)
        if len(links) >= max_links:
            break
    return list(links)

def scroll_to_load(page, rounds: int = 6, pause: float = 0.8):
    """Try to trigger lazy/infinite loading."""
    for i in range(rounds):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(pause)

def goto_with_retry(page, url: str, max_tries: int = 5, base_delay: float = 1.0, wait_selector: str | None = None):
    """Navigate with simple exponential backoff for flaky network."""
    if not can_fetch(url):
        raise PermissionError(f"Disallowed by robots.txt: {url}")

    last_exc = None
    for i in range(max_tries):
        try:
            resp = page.goto(url, timeout=30000, wait_until="domcontentloaded")
            # Some responses may be None (file:// etc.)
            if resp and resp.status >= 400:
                if resp.status == 404:
                    return resp
                if resp.status in (500, 502, 503, 504):
                    delay = base_delay * (2 ** i) + random.uniform(0, 0.5)
                    logger.warning(f"{resp.status} on {url}; retry in {delay:.1f}s (attempt {i+1}/{max_tries})")
                    time.sleep(delay)
                    continue
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=15000)
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

def parse_article(page) -> dict | None:
    """Extract article fields via robust meta-first strategy."""
    url = page.url

    # Prefer og:title / standard <h1>
    title = (
        parse_meta(page, prop="og:title")
        or (page.query_selector("h1") and page.query_selector("h1").inner_text().strip())
        or ""
    ).strip()

    # Category: article:section or breadcrumb text fallback
    category = (
        parse_meta(page, prop="article:section")
        or (page.query_selector('[class*="breadcrumb"] a:last-child') and page.query_selector('[class*="breadcrumb"] a:last-child').inner_text().strip())
        or ""
    ).strip()

    # Author: article:author or name=author or visible byline
    author = (
        parse_meta(page, prop="article:author")
        or parse_meta(page, name="author")
        or (page.query_selector('[class*="author"], [class*="byline"]') and page.query_selector('[class*="author"], [class*="byline"]').inner_text().strip())
        or ""
    ).strip()

    # Published time: article:published_time (ISO) or <time datetime=...>
    published_iso = (
        parse_meta(page, prop="article:published_time")
        or (page.query_selector("time[datetime]") and page.query_selector("time[datetime]").get_attribute("datetime"))
    )
    if published_iso:
        try:
            dt = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y%m%d")
        except Exception:
            date_str = norm_date(datetime.now())
    else:
        date_str = norm_date(datetime.now())

    if not title:
        return None

    rec_id = md5_id(title, url)

    return {
        "id": rec_id,
        "source": SOURCE_NAME,
        "title": title,
        "url": url,
        "author": author,
        "category": category,
        "date": date_str,
        "last_seen_at": norm_date(datetime.now()),
    }

# ---------------------- Main ----------------------
def scrape_nextapple(section_path: str = "/", limit: int = 50, outdir: str = "data", headless: bool = True) -> str:
    """
    section_path: "/" (首頁) 或像 "/realtime", "/entertainment", "/society" 等欄位入口
    limit: 最大文章數
    """
    os.makedirs(outdir, exist_ok=True)
    crawl_time = datetime.now()
    today = norm_date(crawl_time)

    rows = []
    seen_urls = set()

    start_url = urljoin(BASE_URL, section_path.lstrip("/"))
    if not start_url.endswith("/"):
        start_url += "/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--disable-dev-shm-usage"])
        context = browser.new_context(user_agent=UA)
        page = context.new_page()

        # 進入入口頁
        logger.info(f"Open section: {start_url}")
        goto_with_retry(page, start_url)
        # 嘗試捲動載入更多
        scroll_to_load(page, rounds=8, pause=0.8)

        # 抽取入口頁的文章連結
        links = extract_article_links(page, max_links=limit * 2)  # 抓多一點，之後去重
        logger.info(f"Found {len(links)} candidate links on section page.")

        # 若不足，嘗試點擊「更多/下一頁」類元素（容錯，不強制）
        try:
            more_btn = page.query_selector('a[href*="page"] , button:has-text("更多"), button:has-text("載入更多")')
            if more_btn:
                respectful_delay()
                more_btn.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                scroll_to_load(page, rounds=4, pause=0.8)
                links2 = extract_article_links(page, max_links=limit * 2)
                links = list({*links, *links2})
                logger.info(f"After load-more, total candidates: {len(links)}")
        except Exception as e:
            logger.info(f"No load-more flow: {e}")

        # 逐篇進入文章頁擷取
        for i, url in enumerate(links, start=1):
            if len(rows) >= limit:
                break
            if url in seen_urls:
                continue
            seen_urls.add(url)

            respectful_delay()
            logger.info(f"[{len(rows)+1}/{limit}] Parse article: {url}")
            try:
                goto_with_retry(page, url, wait_selector="h1")
                art = parse_article(page)
                if art:
                    rows.append(art)
                else:
                    logger.info(f"Skip (no title): {url}")
            except PermissionError as e:
                logger.warning(str(e))
            except Exception as e:
                logger.warning(f"Article parse failed: {e}")

        browser.close()

    if not rows:
        raise SystemExit("No rows scraped. Check connectivity/selectors/section path.")

    # DataFrame + cleaning
    df = pd.DataFrame(rows, columns=[
        "id", "source", "title", "url", "author",
        "category", "date", "last_seen_at"
    ])

    # Dedup
    before = len(df)
    df = df.drop_duplicates(subset=["source", "id"]).reset_index(drop=True)
    after = len(df)
    if after < before:
        logger.info(f"Deduplicated: {before - after} duplicates removed.")

    # Date sanity (YYYYMMDD)
    for col in ("date", "last_seen_at"):
        bad = ~df[col].astype(str).str.fullmatch(r"\d{8}")
        if bad.any():
            df.loc[bad, col] = today

    out_path = os.path.join(outdir, f"nextapple_dynamic_{today}_n{len(df)}.csv")
    df.to_csv(out_path, index=False, quoting=csv.QUOTE_MINIMAL)
    logger.info(f"Saved {len(df)} rows -> {out_path}")
    return out_path

def main():
    ap = argparse.ArgumentParser(description="Dynamic scraper for Next Apple Taiwan (新蘋果新聞網).")
    ap.add_argument("--section", type=str, default="/", help="入口路徑：/（首頁）、/realtime、/entertainment 等")
    ap.add_argument("--limit", type=int, default=50, help="最大文章數")
    ap.add_argument("--outdir", type=str, default="data", help="輸出資料夾")
    ap.add_argument("--no-headless", action="store_true", help="用可見瀏覽器（除錯用）")
    args = ap.parse_args()

    try:
        scrape_nextapple(
            section_path=args.section,
            limit=args.limit,
            outdir=args.outdir,
            headless=not args.no_headless
        )
    except Exception as e:
        logger.error(f"Scrape failed: {e}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()