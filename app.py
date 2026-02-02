import streamlit as st
import pandas as pd
import google.generativeai as genai
from PIL import Image
import json
import os
import re
from datetime import datetime, date, timedelta, timezone
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import altair as alt

# ==========================================
# 設定
# ==========================================
st.set_page_config(layout="wide", page_title="Bio-Log Cloud V2")

# APIキー設定（Streamlit CloudのSecrets対応）
DEFAULT_API_KEY = "AIzaSyBOlQW_7uW0g62f_NujUBlMDpWtpefHidc" 
try:
    if "GEMINI_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    else:
        genai.configure(api_key=DEFAULT_API_KEY)
except:
    genai.configure(api_key=DEFAULT_API_KEY)

SHEET_NAME = "biolog_db"
JSON_FILE = "service_account.json" 

WS_WORKOUT = "workout_log"
WS_MEAL = "meal_log"
WS_SUMMARY = "daily_summary"

# ==========================================
# データ操作関数
# ==========================================
def connect_to_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = None
    try:
        # Streamlit Secretsから読み込み
        if "gcp_service_account" in st.secrets:
            key_dict = json.loads(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
    except:
        pass

    # ローカルファイルから読み込み（フォールバック）
    if creds is None:
        if os.path.exists(JSON_FILE):
            creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, scope)
        else:
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
        create_if_missing(WS_WORKOUT, ["Date", "Day", "Exercise", "Weight", "Reps", "Sets", "Duration", "Burned_Cal", "Volume", "Notes"])
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

def save_rows_to_sheet(worksheet_name, data_list):
    sh = connect_to_sheet()
    ws = sh.worksheet(worksheet_name)
    rows = [list(d.values()) for d in data_list]
    ws.append_rows(rows)
    load_data.clear()

def save_to_sheet(worksheet_name, data_dict):
    save_rows_to_sheet(worksheet_name, [data_dict])

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
        if 'Burned_Cal' in df_w.columns:
            df_w['Burned_Cal'] = pd.to_numeric(df_w['Burned_Cal'], errors='coerce').fillna(0)
            if 'Day' in df_w.columns:
                daily_workout = df_w.groupby('Day')['Burned_Cal'].sum().to_dict()
                for day, cal in daily_workout.items():
                    if day not in summary_data: 
                        summary_data[day] = {'Intake': 0, 'Workout_Burn': 0, 'P': 0, 'F': 0, 'C': 0}
                    summary_data[day]['Workout_Burn'] = cal

    if not df_m.empty:
        cols = ['Calories', 'Protein', 'Fat', 'Carbs']
        available_cols = [c for c in cols if c in df_m.columns]
        if available_cols and 'Day' in df_m.columns:
            for c in available_cols: df_m[c] = pd.to_numeric(df_m[c], errors='coerce').fillna(0)
            daily_meal = df_m.groupby('Day')[available_cols].sum()
            for day, row in daily_meal.iterrows():
                if day not in summary_data: 
                    summary_data[day] = {'Intake': 0, 'Workout_Burn': 0, 'P': 0, 'F': 0, 'C': 0}
                if 'Calories' in row: summary_data[day]['Intake'] += row['Calories']
                if 'Protein' in row: summary_data[day]['P'] += row['Protein']
                if 'Fat' in row: summary_data[day]['F'] += row['Fat']
                if 'Carbs' in row: summary_data[day]['C'] += row['Carbs']

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

# ==========================================
# AI関連関数 (Gemini 1.5対応)
# ==========================================
def clean_json_text(text):
    text = text.replace('```json', '').replace('```', '').strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match: return match.group(0)
    return text

def analyze_meal_image(image):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = """
    この食事画像を解析し、栄養素を推定してください。
    以下のJSONフォーマットのみを出力してください。
    キーは必ず英語小文字を使用すること。
    { "menu_name": "メニュー名", "calories": 整数, "protein": 少数, "fat": 少数, "carbs": 少数 }
    """
    try:
        response = model.generate_content([prompt, image])
        if not response.parts:
            return {"error": "AI応答生成エラー（Safety Filter等）"}
        return json.loads(clean_json_text(response.text))
    except Exception as e:
        return {"error": str(e)}

def estimate_nutrition_from_text(text):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    メニュー名「{text}」の一般的な栄養素を推定してください。
    以下のJSONフォーマットのみを出力してください。
    {{ "menu_name": "{text}", "calories": 整数, "protein": 少数, "fat": 少数, "carbs": 少数 }}
    """
    try:
        response = model.generate_content(prompt)
        return json.loads(clean_json_text(response.text))
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# UI構築
# ==========================================
st.title("☁️ Bio-Log Cloud V2 (JST)")

# 初期化処理
if 'sheet_init' not in st.session_state:
    init_sheets()
    st.session_state.sheet_init = True
if 'workout_queue' not in st.session_state:
    st.session_state.workout_queue = []
if 'meal_form_data' not in st.session_state:
    st.session_state.meal_form_data = {"menu": "", "cal": 0, "p": 0.0, "f": 0.0, "c": 0.0}

# サイドバー
with st.sidebar:
    st.header("🧬 設定")
    gender = st.radio("性別", ["男性", "女性"])
    age = st.number_input("年齢", 10, 100, 21)
    height = st.number_input("身長 (cm)", 100.0, 250.0, 170.0, 0.1)
    weight = st.number_input("体重 (kg)", 30.0, 200.0, 65.0, 0.1)
    activity_level = st.selectbox("運動強度", ("低い", "普通", "高い"), index=1)
    
    factor = 1.2 if "低い" in activity_level else (1.375 if "普通" in activity_level else 1.55)
    bmr = calculate_bmr(weight, height, age, gender)
    tdee = bmr * factor
    
    st.markdown("---")
    st.metric("基礎代謝", f"{int(bmr)} kcal")
    st.metric("TDEE", f"{int(tdee)} kcal")
    st.caption(f"Ver: {genai.__version__}")

# タブ構成
tab1, tab2, tab3, tab4 = st.tabs(["📊 収支", "📈 分析", "📝 記録", "🤖 コーチ"])

with tab1:
    if st.button("更新"):
        with st.spinner("更新中..."):
            update_daily_summary_sheet(tdee)
    df = load_data(WS_SUMMARY)
    if not df.empty:
        # width警告対策: use_container_width は st.dataframe では推奨されるためそのまま使用
        st.dataframe(df, use_container_width=True, hide_index=True)

with tab2:
    st.subheader("推移グラフ")
    df_w = load_data(WS_WORKOUT)
    if not df_w.empty and 'Exercise' in df_w.columns:
        for col in ['Weight', 'Reps', 'Sets', 'Volume']:
            if col in df_w.columns: df_w[col] = pd.to_numeric(df_w[col], errors='coerce').fillna(0)
            else: df_w[col] = 0
        
        ex_list = df_w['Exercise'].unique()
        if len(ex_list) > 0:
            sel_ex = st.selectbox("種目", ex_list)
            df_chart = df_w[df_w['Exercise'] == sel_ex].sort_values("Date")
            if not df_chart.empty:
                # 警告対策: use_container_width を外して標準的な描画を試行
                c = alt.Chart(df_chart).mark_line(point=True).encode(
                    x='Date', y='Volume', tooltip=['Date', 'Weight', 'Reps']
                ).properties(title=f"{sel_ex} Volume")
                st.altair_chart(c, theme="streamlit", use_container_width=True)

with tab3:
    col_w, col_m = st.columns(2)
    
    # 筋トレ入力カラム
    with col_w:
        st.subheader("🏋️ 筋トレ")
        with st.form("w_form"):
            # ユーザー指定の種目リスト
            ex_cats = {
                "胸": ["ダンベルベンチプレス", "インクラインダンベルプレス", "ディップス", "ベンチプレス"], 
                "背中": ["ロー", "ラットプルダウン", "ダンベルロー", "ケーブルローロー", "懸垂"], 
                "脚": ["スクワット", "デッドリフト", "レッグプレス", "レッグエクステンション", "レッグカール"], 
                "肩": ["ショルダープレス", "ケーブルサイドレイズ", "サイドレイズ"]
            }
            all_ex = [x for v in ex_cats.values() for x in v]
            ex = st.selectbox("種目", all_ex)
            w = st.number_input("重量", 0.0, value=60.0, step=2.5)
            r = st.number_input("回数", 0, value=10)
            s = st.number_input("セット", 1, value=3)
            memo = st.text_input("メモ")
            
            if st.form_submit_button("リストに追加"):
                vol = w * r * s
                burn = round(6.0 * weight * (10/60) * 1.05, 1)
                now_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
                day_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
                
                item = {"Date": now_str, "Day": day_str, "Exercise": ex, "Weight": w, "Reps": r, "Sets": s, "Duration": 10, "Burned_Cal": burn, "Volume": vol, "Notes": memo}
                st.session_state.workout_queue.append(item)
                st.success(f"追加: {ex}")
        
        if st.session_state.workout_queue:
            st.dataframe(pd.DataFrame(st.session_state.workout_queue)[["Exercise", "Weight", "Reps"]])
            if st.button("一括保存"):
                save_rows_to_sheet(WS_WORKOUT, st.session_state.workout_queue)
                update_daily_summary_sheet(tdee)
                st.session_state.workout_queue = []
                st.success("保存完了")
                st.rerun()
            if st.button("クリア"):
                st.session_state.workout_queue = []
                st.rerun()

    # 食事入力カラム
    with col_m:
        st.subheader("🥗 食事")
        mode = st.radio("入力", ["📸 画像", "✏️ 文字", "🖐️ 手動"], horizontal=True)
        
        if mode == "📸 画像":
            f = st.file_uploader("画像", type=["jpg", "png"])
            if f and st.button("解析"):
                with st.spinner("Gemini 1.5 Flash 解析中..."):
                    res = analyze_meal_image(Image.open(f))
                    if "error" not in res:
                        st.session_state.meal_form_data = { 
                            "menu": res.get("menu_name",""), 
                            "cal": res.get("calories",0), 
                            "p": res.get("protein",0), 
                            "f": res.get("fat",0), 
                            "c": res.get("carbs",0) 
                        }
                        st.success("解析成功")
                    else: st.error(res["error"])
        
        elif mode == "✏️ 文字":
            q = st.text_input("メニュー名")
            if q and st.button("自動推測"):
                with st.spinner("Gemini 1.5 Flash 推測中..."):
                    res = estimate_nutrition_from_text(q)
                    if "error" not in res:
                        st.session_state.meal_form_data = { 
                            "menu": res.get("menu_name", q), 
                            "cal": res.get("calories",0), 
                            "p": res.get("protein",0), 
                            "f": res.get("fat",0), 
                            "c": res.get("carbs",0) 
                        }
                        st.success("推測成功")
                    else: st.error(res["error"])
        
        with st.form("m_form"):
            val = st.session_state.meal_form_data
            name = st.text_input("品名", value=val["menu"])
            cal = st.number_input("kcal", value=val["cal"])
            c1, c2, c3 = st.columns(3)
            p = c1.number_input("P", value=float(val["p"]))
            f = c2.number_input("F", value=float(val["f"]))
            c = c3.number_input("C", value=float(val["c"]))
            
            if st.form_submit_button("保存"):
                now_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
                day_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
                data = { "Date": now_str, "Day": day_str, "Menu": name, "Cal": cal, "P": p, "F": f, "C": c }
                save_to_sheet(WS_MEAL, data)
                update_daily_summary_sheet(tdee)
                st.session_state.meal_form_data = {"menu": "", "cal": 0, "p": 0, "f": 0, "c": 0}
                st.success("保存しました")
                st.rerun()

with tab4:
    st.header("🤖 AIコーチ")
    st.info("ここに将来的なアドバイス機能を実装予定")
