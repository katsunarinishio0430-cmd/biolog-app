import streamlit as st
import pandas as pd
import google.generativeai as genai
from PIL import Image
import json
import os
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==========================================
# 設定: APIキー & シート設定
# ==========================================
# あなたのAPIキーをここに入れてください
DEFAULT_API_KEY = "AIzaSyBOlQW_7uW0g62f_NujUBlMDpWtpefHidc" 

# クラウド(Secrets)にあればそれを使い、なければ直書きを使う
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    genai.configure(api_key=DEFAULT_API_KEY)

SHEET_NAME = "biolog_db"
JSON_FILE = "service_account.json" 

# ワークシート名
WS_WORKOUT = "workout_log"
WS_MEAL = "meal_log"
WS_SUMMARY = "daily_summary"

# ==========================================
# データ操作関数 (ハイブリッド対応版)
# ==========================================
def connect_to_sheet():
    """スプレッドシートに接続する（ローカル/クラウド両対応）"""
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    # 1. クラウドのSecretsに設定がある場合（本番環境）
    if "gcp_service_account" in st.secrets:
        # SecretsからJSON文字列を読み込んで辞書化
        key_dict = json.loads(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
    
    # 2. ローカルにjsonファイルがある場合（開発環境）
    elif os.path.exists(JSON_FILE):
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, scope)
    
    else:
        st.error("認証情報が見つかりません。Secretsを設定するかjsonファイルを置いてください。")
        return None

    client = gspread.authorize(creds)
    return client.open(SHEET_NAME)

def init_sheets():
    try:
        sh = connect_to_sheet()
        if not sh: return
        titles = [ws.title for ws in sh.worksheets()]
        
        def create_if_missing(title, header):
            if title not in titles:
                ws = sh.add_worksheet(title=title, rows=100, cols=20)
                ws.append_row(header)
        
        create_if_missing(WS_WORKOUT, ["Date", "Day", "Exercise", "Weight", "Reps", "Sets", "Duration", "Burned_Cal"])
        create_if_missing(WS_MEAL, ["Date", "Day", "Menu_Name", "Calories", "Protein", "Fat", "Carbs"])
        create_if_missing(WS_SUMMARY, ["Date", "Intake", "Burned", "Balance", "P", "F", "C"])
    except Exception as e:
        st.error(f"接続エラー: {e}")

def load_data(worksheet_name):
    try:
        sh = connect_to_sheet()
        ws = sh.worksheet(worksheet_name)
        data = ws.get_all_records()
        return pd.DataFrame(data)
    except:
        return pd.DataFrame()

def save_to_sheet(worksheet_name, data_dict):
    sh = connect_to_sheet()
    ws = sh.worksheet(worksheet_name)
    ws.append_row(list(data_dict.values()))

def update_daily_summary_sheet():
    df_w = load_data(WS_WORKOUT)
    df_m = load_data(WS_MEAL)
    summary_data = {}
    
    if not df_w.empty:
        df_w['Burned_Cal'] = pd.to_numeric(df_w['Burned_Cal'], errors='coerce').fillna(0)
        daily_workout = df_w.groupby('Day')['Burned_Cal'].sum().to_dict()
        for day, cal in daily_workout.items():
            if day not in summary_data: summary_data[day] = {'Intake': 0, 'Burned': 0, 'P': 0, 'F': 0, 'C': 0}
            summary_data[day]['Burned'] = cal

    if not df_m.empty:
        cols = ['Calories', 'Protein', 'Fat', 'Carbs']
        for c in cols: df_m[c] = pd.to_numeric(df_m[c], errors='coerce').fillna(0)
        daily_meal = df_m.groupby('Day')[cols].sum()
        for day, row in daily_meal.iterrows():
            if day not in summary_data: summary_data[day] = {'Intake': 0, 'Burned': 0, 'P': 0, 'F': 0, 'C': 0}
            summary_data[day]['Intake'] += row['Calories']
            summary_data[day]['P'] += row['Protein']
            summary_data[day]['F'] += row['Fat']
            summary_data[day]['C'] += row['Carbs']

    rows = []
    for day, data in summary_data.items():
        balance = data['Intake'] - data['Burned']
        rows.append([day, int(data['Intake']), int(data['Burned']), int(balance), 
                     round(data['P'], 1), round(data['F'], 1), round(data['C'], 1)])
    
    if rows:
        df_sum = pd.DataFrame(rows, columns=["Date", "Intake", "Burned", "Balance", "P", "F", "C"])
        df_sum = df_sum.sort_values("Date", ascending=False)
        sh = connect_to_sheet()
        ws = sh.worksheet(WS_SUMMARY)
        ws.clear()
        ws.append_row(["Date", "Intake", "Burned", "Balance", "P", "F", "C"])
        ws.append_rows(df_sum.values.tolist())
        return df_sum
    return pd.DataFrame()

# ==========================================
# 関数定義: 計算・AI
# ==========================================
def calculate_calories(weight_kg, duration_min, mets=6.0):
    return round(mets * weight_kg * (duration_min / 60) * 1.05, 1)

def analyze_meal_image(image):
    model = genai.GenerativeModel('gemini-flash-latest')
    prompt = """
    この食事画像を解析し、栄養素を推定してください。
    必ず以下のJSONフォーマットのみを出力してください。
    {
      "menu_name": "メニュー名",
      "calories": 整数(kcal),
      "protein": 少数(g),
      "fat": 少数(g),
      "carbs": 少数(g)
    }
    """
    try:
        response = model.generate_content([prompt, image])
        text = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(text)
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# UI構築
# ==========================================
st.set_page_config(layout="wide", page_title="Bio-Log Cloud")
st.title("☁️ Bio-Log Cloud")

if 'sheet_init' not in st.session_state:
    with st.spinner("データベースに接続中..."):
        init_sheets()
        st.session_state.sheet_init = True

with st.sidebar:
    st.header("⚙️ 設定")
    body_weight = st.number_input("体重 (kg)", value=65.0, step=0.1)

st.subheader("📅 日次レポート")
if st.button("🔄 最新に更新"):
    with st.spinner("集計中..."):
        summary_df = update_daily_summary_sheet()
else:
    summary_df = load_data(WS_SUMMARY)

if not summary_df.empty:
    st.dataframe(
        summary_df,
        column_config={
            "Date": st.column_config.TextColumn("日付", frozen=True),
            "Balance": st.column_config.ProgressColumn("収支", format="%d kcal", min_value=-1000, max_value=1000),
        },
        use_container_width=True, hide_index=True
    )

st.divider()
tab1, tab2 = st.tabs(["🏋️ 筋トレ入力", "🥗 食事入力"])

with tab1:
    EXERCISE_LIST = ["ベンチプレス", "スクワット", "デッドリフト", "懸垂", "ショルダープレス", "ランニング", "その他"]
    ex_name = st.selectbox("種目", EXERCISE_LIST)
    c1, c2 = st.columns(2)
    weight = c1.number_input("重量(kg)", 60.0)
    reps = c1.number_input("回数", 10)
    sets = c2.number_input("セット", 3)
    duration = c2.number_input("時間(分)", 10)
    burned = calculate_calories(body_weight, duration)
    
    if st.button("記録をクラウドに保存", type="primary"):
        data = {
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Day": datetime.now().strftime("%Y-%m-%d"),
            "Exercise": ex_name, "Weight": weight, "Reps": reps, 
            "Sets": sets, "Duration": duration, "Burned_Cal": burned
        }
        with st.spinner("保存中..."):
            save_to_sheet(WS_WORKOUT, data)
            update_daily_summary_sheet()
            st.success("保存完了！")
            st.rerun()

with tab2:
    uploaded_file = st.file_uploader("食事画像", type=["jpg", "png"])
    if uploaded_file and st.button("解析してクラウド保存"):
        with st.spinner('AI解析 & 送信中...'):
            image = Image.open(uploaded_file)
            result = analyze_meal_image(image)
            if "error" not in result:
                data = {
                    "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Day": datetime.now().strftime("%Y-%m-%d"),
                    "Menu_Name": result.get('menu_name'),
                    "Calories": result.get('calories'),
                    "Protein": result.get('protein'),
                    "Fat": result.get('fat'),
                    "Carbs": result.get('carbs')
                }
                save_to_sheet(WS_MEAL, data)
                update_daily_summary_sheet()
                st.success(f"保存: {result.get('menu_name')}")
                st.rerun()
