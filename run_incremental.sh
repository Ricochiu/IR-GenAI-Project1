#!/bin/bash
echo "--- 執行增量更新爬蟲 ---"

# 執行靜態爬蟲，爬取 8 頁
python dual_source_scraper/src/scrape_static.py --pages 8

# 執行動態爬蟲，爬取 8 頁
python dual_source_scraper/src/scrape_dynamic.py --pages 5

echo "--- 增量更新完成 ---"