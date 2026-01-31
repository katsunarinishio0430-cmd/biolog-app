import streamlit as st
import pandas as pd
import google.generativeai as genai
from PIL import Image
import json
import os
from datetime import datetime, date, timedelta, timezone
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import altair as alt

# ==========================================
# 設定: APIキー & シート設定
# ==========================================
DEFAULT_API_KEY = "AIzaSyBOlQW_7uW0g62f_NujUBlMDpWtpefHidc" 

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
# データ操作関数 (高速化対応)
# ==========================================
def connect_to_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    if "gcp_service_account" in st.secrets:
        key_dict = json.loads(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
    elif os.path.exists(JSON_FILE):
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, scope)
    else:
        st.error("認証情報が見つかりません。")
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
        
        create_if_missing(WS_WORKOUT, ["Date", "Day", "Exercise", "Weight", "Reps", "Sets", "Duration", "Burned_Cal", "Volume"])
        create_if_missing(WS_MEAL, ["Date", "Day", "Menu_Name", "Calories", "Protein", "Fat", "Carbs"])
        create_if_missing(WS_SUMMARY, ["Date", "Intake", "Total_Out", "Balance", "P", "F", "C", "Base_Metabolism"])
    except Exception as e:
        st.error(f"接続エラー: {e}")

@st.cache_data(ttl=60)
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
    load_data.clear()

# ==========================================
# ロジック関数
# ==========================================
def calculate_bmr(weight, height, age, gender):
    if gender == "男性":
        return (10 * weight) + (6.25 * height) - (5 * age) + 5
    else:
        return (10 * weight) + (6.25 * height) - (5 * age) - 161

def update_daily_summary_sheet(base_metabolism):
    load_data.clear() 
    df_w = load_data(WS_WORKOUT)
    df_m = load_data(WS_MEAL)
    summary_data = {}
    
    if not df_w.empty:
        df_w['Burned_Cal'] = pd.to_numeric(df_w['Burned_Cal'], errors='coerce').fillna(0)
        daily_workout = df_w.groupby('Day')['Burned_Cal'].sum().to_dict()
        for day, cal in daily_workout.items():
            if day not in summary_data: 
                summary_data[day] = {'Intake': 0, 'Workout_Burn': 0, 'P': 0, 'F': 0, 'C': 0}
            summary_data[day]['Workout_Burn'] = cal

    if not df_m.empty:
        cols = ['Calories', 'Protein', 'Fat', 'Carbs']
        for c in cols: df_m[c] = pd.to_numeric(df_m[c], errors='coerce').fillna(0)
        daily_meal = df_m.groupby('Day')[cols].sum()
        for day, row in daily_meal.iterrows():
            if day not in summary_data: 
                summary_data[day] = {'Intake': 0, 'Workout_Burn': 0, 'P': 0, 'F': 0, 'C': 0}
            summary_data[day]['Intake'] += row['Calories']
            summary_data[day]['P'] += row['Protein']
            summary_data[day]['F'] += row['Fat']
            summary_data[day]['C'] += row['Carbs']

    rows = []
    for day, data in summary_data.items():
        total_out = base_metabolism + data['Workout_Burn']
        balance = data['Intake'] - total_out
        rows.append([day, int(data['Intake']), int(total_out), int(balance), 
                     round(data['P'], 1), round(data['F'], 1), round(data['C'], 1), int(base_metabolism)])
    
    if rows:
        df_sum = pd.DataFrame(rows, columns=["Date", "Intake", "Total_Out", "Balance", "P", "F", "C", "Base_Metabolism"])
        df_sum = df_sum.sort_values("Date", ascending=False)
        
        sh = connect_to_sheet()
        ws = sh.worksheet(WS_SUMMARY)
        ws.clear()
        ws.append_row(["Date", "Intake", "Total_Out", "Balance", "P", "F", "C", "Base_Metabolism"])
        ws.append_rows(df_sum.values.tolist())
        return df_sum
    return pd.DataFrame()

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
st.set_page_config(layout="wide", page_title="Bio-Log Cloud V2")
st.title("☁️ Bio-Log Cloud V2")

if 'sheet_init' not in st.session_state:
    init_sheets()
    st.session_state.sheet_init = True

# --- サイドバー ---
with st.sidebar:
    st.header("🧬 ユーザー・代謝設定")
    gender = st.radio("性別", ["男性", "女性"])
    age = st.number_input("年齢", min_value=10, max_value=100, value=21)
    height = st.number_input("身長 (cm)", min_value=100.0, max_value=250.0, value=170.0, step=0.1)
    weight = st.number_input("体重 (kg)", min_value=30.0, max_value=200.0, value=65.0, step=0.1)
    
    st.subheader("生活活動レベル")
    activity_level = st.selectbox(
        "日常の運動強度", 
        ("低い (デスクワーク・勉強)", "普通 (通学・立ち仕事)", "高い (肉体労働・部活)"),
        index=1
    )
    
    if "低い" in activity_level: factor = 1.2
    elif "普通" in activity_level: factor = 1.375
    else: factor = 1.55
    
    bmr_pure = calculate_bmr(weight, height, age, gender)
    daily_base_burn = bmr_pure * factor
    
    st.markdown("---")
    st.metric("基礎代謝 (BMR)", f"{int(bmr_pure)} kcal")
    st.metric("1日の基準消費 (TDEE)", f"{int(daily_base_burn)} kcal", help="筋トレ以外の生活活動を含みます")

# --- メインエリア ---
tab1, tab2, tab3 = st.tabs(["📊 カロリー収支", "📈 漸進性負荷分析", "📝 記録入力"])

with tab1:
    if st.button("🔄 最新データに更新"):
        load_data.clear()
        with st.spinner("TDEEを含めて再計算中..."):
            summary_df = update_daily_summary_sheet(daily_base_burn)
    else:
        summary_df = load_data(WS_SUMMARY)

    if not summary_df.empty:
        st.dataframe(
            summary_df,
            column_config={
                "Date": st.column_config.TextColumn("日付", frozen=True),
                "Total_Out": st.column_config.NumberColumn("総消費 (基礎+運動)", format="%d kcal"),
                "Balance": st.column_config.ProgressColumn("収支", format="%d kcal", min_value=-1000, max_value=1000),
            },
            use_container_width=True, hide_index=True
        )

with tab2:
    st.subheader("💪 Progressive Overload Tracker")
    df_w = load_data(WS_WORKOUT)
    
    if not df_w.empty:
        for col in ['Weight', 'Reps', 'Sets', 'Volume']:
             df_w[col] = pd.to_numeric(df_w[col], errors='coerce').fillna(0)

        unique_exercises = df_w['Exercise'].unique()
        selected_ex = st.selectbox("分析する種目を選択", unique_exercises)
        
        df_chart = df_w[df_w['Exercise'] == selected_ex].sort_values("Date")
        
        if not df_chart.empty:
            c = alt.Chart(df_chart).mark_line(point=True).encode(
                x='Date',
                y=alt.Y('Volume', title='総負荷量 (kg×reps×sets)'),
                tooltip=['Date', 'Weight', 'Reps', 'Sets', 'Volume']
            ).properties(title=f"{selected_ex} のボリューム推移")
            st.altair_chart(c, use_container_width=True)
            
            c2 = alt.Chart(df_chart).mark_line(point=True, color='orange').encode(
                x='Date',
                y=alt.Y('Weight', title='扱う重量 (kg)', scale=alt.Scale(zero=False)),
                tooltip=['Date', 'Weight']
            ).properties(title=f"{selected_ex} の重量推移")
            st.altair_chart(c2, use_container_width=True)
        else:
            st.warning("この種目のデータはまだありません。")

with tab3:
    st.subheader("📅 日時設定")
    
    # ★ここが修正ポイント: 日本時間(JST)を取得して初期値にする
    JST = timezone(timedelta(hours=9), 'JST')
    
    # Session Stateを使って「最初に開いた時の時間」を保存し、勝手に更新されないようにする
    if 'default_date' not in st.session_state:
        st.session_state.default_date = datetime.now(JST).date()
    if 'default_time' not in st.session_state:
        st.session_state.default_time = datetime.now(JST).time()

    c_date, c_time = st.columns(2)
    input_date = c_date.date_input("日付", value=st.session_state.default_date)
    # 時間の入力を、ユーザーが変更しない限り固定
    input_time = c_time.time_input("時間", value=st.session_state.default_time)
    
    target_datetime = datetime.combine(input_date, input_time)
    formatted_date = target_datetime.strftime("%Y-%m-%d %H:%M")
    formatted_day = target_datetime.strftime("%Y-%m-%d")

    st.divider()
    
    col_w, col_m = st.columns(2)
    
    with col_w:
        with st.form("workout_form"):
            st.subheader("🏋️ 筋トレ")
            ex_list = ["ベンチプレス", "スクワット", "デッドリフト", "懸垂", "ショルダープレス", "アームカール", "ランニング"]
            ex_name = st.selectbox("種目", ex_list)
            weight_in = st.number_input("重量(kg)", 60.0, step=2.5)
            reps_in = st.number_input("回数", 10, step=1)
            sets_in = st.number_input("セット", 3, step=1)
            duration_in = st.number_input("時間(分)", 10, step=5)
            
            submitted_w = st.form_submit_button("筋トレを保存", type="primary")
            
            if submitted_w:
                workout_burn = round(6.0 * weight * (duration_in / 60) * 1.05, 1)
                volume = weight_in * reps_in * sets_in
                
                data = {
                    "Date": formatted_date,
                    "Day": formatted_day,
                    "Exercise": ex_name, "Weight": weight_in, "Reps": reps_in, 
                    "Sets": sets_in, "Duration": duration_in, "Burned_Cal": workout_burn,
                    "Volume": volume
                }
                save_to_sheet(WS_WORKOUT, data)
                update_daily_summary_sheet(daily_base_burn)
                st.success(f"保存完了! ({formatted_date})")

    with col_m:
        st.subheader("🥗 食事")
        img_file = st.file_uploader("画像", type=["jpg", "png"])
        if img_file and st.button("解析して保存"):
            with st.spinner('解析中...'):
                res = analyze_meal_image(Image.open(img_file))
                if "error" not in res:
                    data = {
                        "Date": formatted_date,
                        "Day": formatted_day,
                        "Menu": res.get('menu_name'), "Cal": res.get('calories'),
                        "P": res.get('protein'), "F": res.get('fat'), "C": res.get('carbs')
                    }
                    save_to_sheet(WS_MEAL, data)
                    update_daily_summary_sheet(daily_base_burn)
                    st.success(f"保存: {res.get('menu_name')} ({formatted_date})")
                    
