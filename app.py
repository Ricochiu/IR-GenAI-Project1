import streamlit as st
import pandas as pd
import os
from pathlib import Path

# ==========================================================
# 🛠 使用者設定區（可自行修改）
# ==========================================================
# 專案根目錄：此檔案所在的上層目錄
import streamlit as st
import pandas as pd
import os
from pathlib import Path

# ==========================================================
# 🛠 使用者設定區（使用相對路徑）
# ==========================================================
# 此檔案：compare/src.py
# 資料夾層級：src.py ← compare ← IR-GenAI-Project1
# 因此往上「一層」就能找到 data/ 和 reports/
CURRENT_DIR = Path(__file__).resolve().parent  # compare/
PROJECT_ROOT = CURRENT_DIR
while PROJECT_ROOT != PROJECT_ROOT.parent:
    if (PROJECT_ROOT / "data").exists() and (PROJECT_ROOT / "reports").exists():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent

DATA_DIR = PROJECT_ROOT / "data"
REPORT_DIR = PROJECT_ROOT / "reports"

# 預設檔案
STATIC_FILE = DATA_DIR / "books_static_20251014_p8.csv"
DYNAMIC_FILE = DATA_DIR / "quotes_dynamic_20251014_p8.csv"

# ==========================================================
# 🌐 Streamlit 頁面設定
# ==========================================================
st.set_page_config(page_title="期中專案成果", layout="wide")
st.title("雙來源網頁爬蟲與差異分析 成果展示")

# ==========================================================
# 📂 使用者選擇資料來源
# ==========================================================
source_choice = st.selectbox("請選擇要查看的資料來源：", ["靜態網站 (books_static)", "動態網站 (quotes_dynamic)"])

if "static" in source_choice:
    source_name = "books_static"
    data_file = STATIC_FILE
else:
    source_name = "quotes_dynamic"
    data_file = DYNAMIC_FILE

# ==========================================================
# 📊 顯示摘要圖
# ==========================================================
summary_chart_path = REPORT_DIR / f"chart_comp_{source_name}_summary.png"

if summary_chart_path.exists():
    st.header("資料變動摘要")
    st.image(str(summary_chart_path), caption="顯示新增、刪除與修改的資料筆數。")
else:
    st.warning(f"找不到摘要圖表：{summary_chart_path}")

# ==========================================================
# 🔍 顯示資料表格與搜尋功能
# ==========================================================
st.header("資料檢視與搜尋")
if data_file.exists():
    df = pd.read_csv(data_file)

    keyword = st.text_input("輸入關鍵字搜尋標題 (Title):")
    if keyword:
        result_df = df[df["title"].str.contains(keyword, case=False, na=False)]
        st.dataframe(result_df)
    else:
        st.dataframe(df)
else:
    st.error(f"找不到資料檔案：{data_file}")