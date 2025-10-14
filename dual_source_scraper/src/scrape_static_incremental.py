#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Incremental static scraper for Books to Scrape.
- 只抓取「上一次沒有的新書」
- 支援輸入爬取頁數 (--pages)
- 輸出: data/books_static_incremental_YYYYMMDD.csv
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
BASE_URL = "https://books.toscrape.com/"
CATALOG_BASE = urljoin(BASE_URL, "catalogue/")
PAGE_URL = lambda n: urljoin(CATALOG_BASE, f"page-{n}.html")
SOURCE_NAME = "books_toscrape"

def md5_id(title: str, price: str) -> str:
    """穩定 ID：md5(title + price)"""
    h = hashlib.md5()
    h.update((title.strip() + "|" + str(price).strip()).encode("utf-8"))
    return h.hexdigest()

# ---------------------- 主爬蟲邏輯 ----------------------
def scrape_books_incremental(num_pages=5, outdir="data", prev_file=None, headless=True):
    """
    第二次爬蟲只抓取「第一次沒有的」書籍項目。
    判斷是否為新項目時，不看 id；改用 (title_normalized, price_rounded)。
    """
    os.makedirs(outdir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    # 1️⃣ 讀舊資料做 key 集合： (normalized_title, rounded_price)
    seen_keys = set()
    if prev_file and os.path.exists(prev_file):
        try:
            prev_df = pd.read_csv(prev_file, usecols=["title", "price/value"])
            # 標準化：title 去前後空白、小寫；price 轉 float 後四捨五入到 2 位
            prev_df["__title_norm"] = prev_df["title"].astype(str).str.strip().str.lower()
            prev_df["__price_norm"] = pd.to_numeric(prev_df["price/value"], errors="coerce").round(2)
            seen_keys = set(zip(prev_df["__title_norm"], prev_df["__price_norm"]))
            print(f"✅ 已載入舊資料鍵值 {len(seen_keys)} 組（以 title+price 判斷重複）。")
        except Exception as e:
            print(f"⚠️ 無法讀取舊資料作為對照（將視為全新抓取）：{e}")

    # 2️⃣ 開始爬蟲
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        for page_no in range(1, num_pages + 1):
            url = BASE_URL if page_no == 1 else PAGE_URL(page_no)
            print(f"📚 正在爬第 {page_no} 頁：{url}")
            try:
                page.goto(url, timeout=20000)
                page.wait_for_selector(".product_pod", timeout=10000)
            except PWTimeout:
                print(f"⚠️ 第 {page_no} 頁載入逾時，略過。")
                continue
            except Exception as e:
                print(f"⚠️ 第 {page_no} 頁載入失敗：{e}")
                continue

            books = page.query_selector_all(".product_pod")
            if not books:
                print(f"⚠️ 第 {page_no} 頁沒有找到任何書籍，停止。")
                break

            for b in books:
                title = (b.query_selector("h3 a").get_attribute("title") or "").strip()
                price_text = (b.query_selector(".price_color").inner_text() or "").strip().replace("£", "")
                try:
                    price_val = float(price_text)
                except Exception:
                    # 若價格解析失敗，視為無效，跳過
                    continue

                url_here = urljoin(url, b.query_selector("h3 a").get_attribute("href"))

                # 3️⃣ 用 (title_norm, price_norm) 判斷是否舊資料
                title_norm = title.lower()
                price_norm = round(price_val, 2)
                cur_key = (title_norm, price_norm)
                if cur_key in seen_keys:
                    continue  # 舊資料，跳過

                # 仍可寫入 id（不影響“是否新增”的判斷）
                rec_id = md5_id(title, price_val)

                row = {
                    "id": rec_id,
                    "source": SOURCE_NAME,
                    "title": title,
                    "url": url_here,
                    "author/vendor": "",                      # 此站無作者欄
                    "category": "",                           # 需要可再補抓分類
                    "price/value": price_val,
                    "date": today,
                    "last_seen_at": today,
                }
                rows.append(row)

            time.sleep(random.uniform(0.8, 1.3))

        browser.close()

    # 4️⃣ 輸出結果
    if not rows:
        print("⚠️ 沒有發現任何新書（依 title+price 判斷）。")
        return None

    df_new = pd.DataFrame(rows)
    out_path = os.path.join(outdir, f"books_static_incremental_{today}.csv")
    df_new.to_csv(out_path, index=False)
    print(f"✅ 已儲存 {len(df_new)} 筆新書 -> {out_path}")
    return out_path
# ---------------------- 主程式 ----------------------
def main():
    ap = argparse.ArgumentParser(description="Incremental scraper for Books to Scrape.")
    ap.add_argument("--pages", type=int, required=True, help="要爬的頁數 (例: 5 或 10)")
    ap.add_argument("--outdir", type=str, default="data", help="輸出資料夾")
    ap.add_argument("--prev", type=str, default=None, help="上一版 CSV 檔案路徑")
    ap.add_argument("--no-headless", action="store_true", help="開啟瀏覽器視窗")
    args = ap.parse_args()

    scrape_books_incremental(
        num_pages=args.pages,
        outdir=args.outdir,
        prev_file=args.prev,
        headless=not args.no_headless
    )

if __name__ == "__main__":
    main()