echo "======================================="
echo "📘 開始靜態 Books 爬蟲"
echo "======================================="
python dual_source_scraper/src/scrape_static.py --pages 8

echo "======================================="
echo "💬 開始動態 Quotes 爬蟲"
echo "======================================="
python dual_source_scraper/src/scrape_dynamic.py --pages 10

echo "======================================="
echo "📈 開始增量 Quotes"
echo "======================================="
python dual_source_scraper/src/scrape_dynamic_incremental.py --pages 10 --prev data/quotes_dynamic_20251015_p5.csv

echo "======================================="
echo "📗 開始增量 Books"
echo "======================================="
python dual_source_scraper/src/scrape_static_incremental.py --pages 8 --prev data/books_static_20251015_p5.csv 

echo "🎉 --- 增量更新完成 ---"