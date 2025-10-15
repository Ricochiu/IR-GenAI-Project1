#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UDN Sports category scraper（含 snapshot 模式 + 相對路徑）
------------------------------------------------------------
執行範例：
    python udn_sports_category_100.py --limit 100 --snapshot
資料夾結構：
    data/    → 主資料（每日快照）
    logs/    → 爬取日誌
    report/  → diff_YYYYMMDD.csv, summary.json
"""

import argparse
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from urllib import robotparser
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---------------------- 相對路徑設定 ----------------------
BASE_PATH = os.path.abspath(".")
DATA_DIR = os.path.join(BASE_PATH, "data")
LOG_DIR = os.path.join(BASE_PATH, "logs")
REPORT_DIR = os.path.join(BASE_PATH, "report")

for d in [DATA_DIR, LOG_DIR, REPORT_DIR]:
    os.makedirs(d, exist_ok=True)

LOGF = os.path.join(LOG_DIR, "udn_sports.log")

# ---------------------- 其他設定 ----------------------
DEFAULT_UA = "udn-sports-scraper/1.0 (academic use)"
UA = {"User-Agent": DEFAULT_UA}
SOURCE = "UDN Sports"
BASE = "https://udn.com"

logging.basicConfig(
    filename=LOGF,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ---------------------- 共用工具 ----------------------
def robots_allowed(url, ua=DEFAULT_UA):
    rp = robotparser.RobotFileParser()
    rp.set_url(urljoin(BASE, "/robots.txt"))
    try:
        rp.read()
        allowed = rp.can_fetch(ua, url)
        if not allowed:
            logging.warning(f"robots disallow: {url}")
        return allowed
    except Exception as e:
        logging.warning(f"robots read failed: {e}; fallback allow")
        return True

def fetch_with_retry(url, headers, timeout=12, max_attempts=5, base_backoff=1.0):
    backoff = base_backoff
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"server responded {r.status_code}")
            r.raise_for_status()
            return r.text
        except Exception as e:
            logging.warning(f"fetch attempt {attempt} failed for {url}: {e}")
            if attempt == max_attempts:
                logging.error(f"fetch failed after {max_attempts} attempts: {url}")
                raise
            time.sleep(backoff)
            backoff *= 2  # 指數退避

def fetch(url):
    if not robots_allowed(url):
        raise RuntimeError(f"Blocked by robots.txt: {url}")
    return fetch_with_retry(url, UA, timeout=12, max_attempts=5)

def compute_id(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]

def normalize_date(published: str):
    if not published:
        return "", ""
    try:
        dt = datetime.fromisoformat(re.sub(r"Z$", "+00:00", published))
        return dt.isoformat(), dt.strftime("%Y%m%d")
    except Exception:
        pass
    m = re.search(r"\d{4}-\d{2}-\d{2}", published)
    if m:
        try:
            dt = datetime.strptime(m.group(0), "%Y-%m-%d")
            return dt.isoformat(), dt.strftime("%Y%m%d")
        except Exception:
            return published, ""
    return published, ""

def guess_category(url: str) -> str:
    m = re.search(r"/news/story/(\d+)/", url)
    return m.group(1) if m else ""

def to_float_or_none(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except Exception:
        return None

# ---------------------- 解析邏輯 ----------------------
def parse_list_page(html, base=BASE):
    soup = BeautifulSoup(html, "lxml")
    items = []
    for a in soup.select("a"):
        href = a.get("href") or ""
        if "/news/story/" in href and not href.endswith("#") and "video" not in href:
            items.append(urljoin(base, href.split("?")[0]))
    seen, out = set(), []
    for u in items:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def parse_article(url: str) -> dict:
    html = fetch(url)
    soup = BeautifulSoup(html, "lxml")

    og = soup.find("meta", property="og:title")
    title = (og.get("content") or "").strip() if og else ""
    if not title:
        title = soup.title.get_text(strip=True) if soup.title else ""

    mt = soup.find("meta", property="article:published_time")
    published_raw = (mt.get("content") or "").strip() if mt else ""
    if not published_raw:
        t = soup.find("time")
        if t:
            published_raw = (t.get("datetime") or t.get_text(strip=True) or "").strip()

    published_iso, date_yyyymmdd = normalize_date(published_raw)

    author = ""
    for sel in [
        'meta[name="author"]',
        'span.author',
        'a.author',
        '.article-content__author',
        '.article_author',
        '.author__name',
    ]:
        node = soup.select_one(sel)
        if node:
            author = (node.get("content") or node.get_text(strip=True) or "").strip()
            if author:
                break

    body = ""
    art = soup.find("article")
    if art:
        ps = [p.get_text(" ", strip=True) for p in art.find_all("p")]
        body = "\n".join([p for p in ps if p])

    return {
        "id": compute_id(url),
        "source": SOURCE,
        "title": title,
        "url": url,
        "author/vendor": author,
        "category": guess_category(url),
        "published": published_iso or published_raw,
        "date": date_yyyymmdd,
        "price/value": None,
        "last_seen_at": datetime.now().strftime("%Y%m%d"),
        "content": body,
    }

def paginate_collect(start_url: str, want=100, pause=0.8):
    collected, page = [], 1
    while len(collected) < want:
        url = f"{start_url}?p={page}" if page > 1 else start_url
        try:
            html = fetch(url)
        except Exception as e:
            logging.error(f"list page fetch failed: {url} -> {e}")
            break

        urls = parse_list_page(html, BASE)
        if not urls:
            logging.info(f"no urls found at page {page}")
            break

        for u in urls:
            if len(collected) >= want:
                break
            try:
                rec = parse_article(u)
                collected.append(rec)
                print(f"[{len(collected)}/{want}] {rec['title'][:60]}")
                time.sleep(pause)
            except Exception as e:
                logging.error(f"parse article failed: {u} -> {e}")
                print("skip:", u)
                time.sleep(pause / 2)

        page += 1
        time.sleep(pause)

    df = pd.DataFrame(collected).drop_duplicates(subset=["url"]).head(want)
    return df

# ---------------------- 增量/差異 ----------------------
def load_previous_df(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            return pd.read_csv(path, dtype=str)
        except Exception as e:
            logging.error(f"read previous csv failed: {e}")
    return pd.DataFrame()

def generate_diff(old_df: pd.DataFrame, new_df: pd.DataFrame, key="url"):
    today_str = datetime.now().strftime("%Y%m%d")
    diff_csv = os.path.join(REPORT_DIR, f"diff_{today_str}.csv")
    summary_json = os.path.join(REPORT_DIR, "summary.json")

    if old_df is None or old_df.empty:
        added = sorted(new_df[key].dropna().unique().tolist())
        pd.DataFrame({"status": ["ADDED"] * len(added), key: added}).to_csv(diff_csv, index=False)
        summary = {"date": datetime.now().strftime("%Y-%m-%d"), "added": len(added), "removed": 0, "changed": 0, "output_csv": diff_csv}
        with open(summary_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        return diff_csv, summary

    old_df = old_df.drop_duplicates(subset=[key])
    new_df = new_df.drop_duplicates(subset=[key])

    old_keys = set(old_df[key].dropna().unique().tolist())
    new_keys = set(new_df[key].dropna().unique().tolist())

    added_keys = sorted(list(new_keys - old_keys))
    removed_keys = sorted(list(old_keys - new_keys))

    merged = new_df.merge(old_df, on=key, how="inner", suffixes=("_new", "_old"))
    changed = int(((merged.get("title_new") != merged.get("title_old")) | (merged.get("date_new") != merged.get("date_old"))).sum())

    pd.DataFrame(
        {"status": (["ADDED"] * len(added_keys)) + (["REMOVED"] * len(removed_keys)), key: added_keys + removed_keys}
    ).to_csv(diff_csv, index=False)

    summary = {"date": datetime.now().strftime("%Y-%m-%d"), "added": len(added_keys), "removed": len(removed_keys), "changed": changed, "output_csv": diff_csv}
    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return diff_csv, summary

# ---------------------- 主程式 ----------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="https://udn.com/news/cate/2/7227")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--pause", type=float, default=0.8)
    ap.add_argument("--ua", default=DEFAULT_UA)
    ap.add_argument("--snapshot", action="store_true", help="若開啟則輸出每日快照檔")
    args = ap.parse_args()

    UA["User-Agent"] = args.ua
    today_str = datetime.now().strftime("%Y%m%d")

    # 決定輸出檔案名稱
    out_path = os.path.join(DATA_DIR, f"udn_sports_{today_str}.csv" if args.snapshot else "udn_sports_100.csv")

    # 若非 snapshot 模式則載入舊資料
    old_df = load_previous_df(out_path) if not args.snapshot else pd.DataFrame()

    df = paginate_collect(args.start, want=args.limit, pause=args.pause)

    cols = ["id", "source", "title", "url", "author/vendor", "category", "published", "date", "price/value", "last_seen_at", "content"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df["price/value"] = df["price/value"].apply(to_float_or_none)
    df["source"] = SOURCE
    df = df.reindex(columns=cols)
    df.to_csv(out_path, index=False)
    print(f"Saved {len(df)} rows -> {out_path}")

    diff_csv, summary = generate_diff(old_df, df)
    print(f"Diff saved -> {diff_csv}")
    print(f"Summary -> {summary}")

if __name__ == "__main__":
    main()