#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Incremental dynamic scraper for Quotes to Scrape (JS-rendered).
- 只抓取「上一次沒有的新引言」
- 支援輸入爬取頁數 (--pages)
- 輸出: data/quotes_dynamic_incremental_YYYYMMDD.csv
"""

import argparse
import os
import hashlib
import pandas as pd
from datetime import datetime
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import time
import random

# ---------------------- 基本設定 ----------------------
BASE_URL = "https://quotes.toscrape.com/"
JS_BASE  = urljoin(BASE_URL, "js/")
PAGE_URL = lambda n: urljoin(JS_BASE, f"page/{n}/")
SOURCE_NAME = "quotes_toscrape_js"

def md5_id(text: str, author: str) -> str:
    """穩定 ID：md5(title + author)"""
    h = hashlib.md5()
    h.update((text.strip() + "|" + author.strip()).encode("utf-8"))
    return h.hexdigest()

# ---------------------- 主爬蟲邏輯 ----------------------
def scrape_quotes_incremental(num_pages=5, outdir="data", prev_file=None, headless=True):
    """
    第二次爬蟲只抓取「第一次沒有的」項目。
    """
    os.makedirs(outdir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    # 1️⃣ 載入舊資料建立 seen_ids 集合
    seen_ids = set()
    if prev_file and os.path.exists(prev_file):
        try:
            prev_df = pd.read_csv(prev_file, usecols=["id"])
            seen_ids = set(prev_df["id"].astype(str))
            print(f"✅ 已載入舊資料 {len(seen_ids)} 筆 ID，將跳過重複項目。")
        except Exception as e:
            print(f"⚠️ 無法讀取舊資料: {e}")

    # 2️⃣ 啟動 Playwright 爬取新資料
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        for page_no in range(1, num_pages + 1):
            url = JS_BASE if page_no == 1 else PAGE_URL(page_no)
            print(f"🕸 正在爬第 {page_no} 頁：{url}")
            try:
                page.goto(url, timeout=20000)
                page.wait_for_selector(".quote", timeout=10000)
            except PWTimeout:
                print(f"⚠️ 第 {page_no} 頁載入逾時，略過。")
                continue
            except Exception as e:
                print(f"⚠️ 第 {page_no} 頁載入失敗：{e}")
                continue

            quotes = page.query_selector_all(".quote")
            if not quotes:
                print(f"⚠️ 第 {page_no} 頁沒有找到任何引言，停止。")
                break

            for q in quotes:
                text = q.query_selector("span.text").inner_text().strip("“”\"'")
                author = q.query_selector(".author").inner_text().strip()
                tags = [t.inner_text() for t in q.query_selector_all(".tag")]
                qid = md5_id(text, author)

                if qid in seen_ids:
                    continue  # 跳過舊項目

                row = {
                    "id": qid,
                    "source": SOURCE_NAME,
                    "title": text,
                    "url": url,
                    "author/vendor": author,
                    "category": ",".join(tags),
                    "date": today,
                    "last_seen_at": today,
                }
                rows.append(row)

            time.sleep(random.uniform(0.8, 1.3))

        browser.close()

    # 3️⃣ 輸出結果
    if not rows:
        print("⚠️ 沒有發現任何新項目（增量爬蟲無輸出）。")
        return None

    df_new = pd.DataFrame(rows)
    out_path = os.path.join(outdir, f"quotes_dynamic_incremental_{today}.csv")
    df_new.to_csv(out_path, index=False)
    print(f"✅ 已儲存 {len(df_new)} 筆新資料 -> {out_path}")
    return out_path

# ---------------------- 主程式 ----------------------
def main():
    ap = argparse.ArgumentParser(description="Incremental scraper for Quotes to Scrape (JS).")
    ap.add_argument("--pages", type=int, required=True, help="要爬的頁數 (例: 5 或 10)")
    ap.add_argument("--outdir", type=str, default="data", help="輸出資料夾")
    ap.add_argument("--prev", type=str, default=None, help="上一版 CSV 檔案路徑")
    ap.add_argument("--no-headless", action="store_true", help="開啟瀏覽器視窗")
    args = ap.parse_args()

    scrape_quotes_incremental(
        num_pages=args.pages,
        outdir=args.outdir,
        prev_file=args.prev,
        headless=not args.no_headless
    )

if __name__ == "__main__":
    main()