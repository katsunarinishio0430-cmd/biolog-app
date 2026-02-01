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
import re

# ==========================================
# 設定: APIキー & シート設定
# ==========================================
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

# ワークシート名
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
        if "gcp_service_account" in st.secrets:
            key_dict = json.loads(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
    except:
        pass

    if creds is None:
        if os.path.exists(JSON_FILE):
            creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, scope)
        else:
            st.warning("認証情報が見つかりません。")
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

# ==========================================
# ロジック関数 (AI & 計算)
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

# ★Helper: AIの出力をクリーンなJSONにする関数
def clean_json_text(text):
    text = text.replace('```json', '').replace('```', '').strip()
    # 最初の{から最後の}までを抽出（余計な文章をカット）
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
    return text

# ★修正版: Gemini Pro Vision (画像用)
def analyze_meal_image(image):
    model = genai.GenerativeModel('gemini-pro-vision') # 安定版モデルに変更
    prompt = """
    この食事画像を解析し、栄養素を推定してください。
    必ず以下のJSONキーのみを持つJSONデータを出力してください。余計な会話は不要です。
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
        json_text = clean_json_text(response.text)
        return json.loads(json_text)
    except Exception as e:
        return {"error": str(e)}

# ★修正版: Gemini Pro (テキスト用)
def estimate_nutrition_from_text(text):
    model = genai.GenerativeModel('gemini-pro') # 安定版モデルに変更
    prompt = f"""
    以下の食事メニューの栄養素（カロリー、PFC）を一般的な基準で推定してください。
    メニュー名: {text}
    
    必ず以下のJSONキーのみを持つJSONデータを出力してください。冒頭の挨拶などは不要です。
    {{
      "menu_name": "メニュー名",
      "calories": 整数(kcal),
      "protein": 少数(g),
      "fat": 少数(g),
      "carbs": 少数(g)
    }}
    """
    try:
        response = model.generate_content(prompt)
        json_text = clean_json_text(response.text)
        return json.loads(json_text)
    except Exception as e:
        return {"error": str(e)}

def generate_advice(days=7):
    df_w = load_data(WS_WORKOUT)
    df_s = load_data(WS_SUMMARY)
    
    workout_text = "データなし"
    nutrition_text = "データなし"
    
    if not df_w.empty and 'Day' in df_w.columns:
        df_w['Day'] = pd.to_datetime(df_w['Day'])
        recent_w = df_w[df_w['Day'] >= (datetime.now() - timedelta(days=days))]
        if not recent_w.empty:
            summary = recent_w.groupby('Exercise').agg(
                Max_Weight=('Weight', 'max'),
                Total_Volume=('Volume', 'sum'),
                Count=('Date', 'count')
            ).to_string()
            workout_text = f"【直近{days}日間のトレーニング実績】\n{summary}"

    if not df_s.empty and 'Date' in df_s.columns:
        df_s['Date'] = pd.to_datetime(df_s['Date'])
        recent_s = df_s[df_s['Date'] >= (datetime.now() - timedelta(days=days))]
        if not recent_s.empty:
            summary = recent_s[['Date', 'Intake', 'Total_Out', 'Balance', 'P', 'F', 'C']].to_string(index=False)
            nutrition_text = f"【直近{days}日間の栄養摂取状況】\n{summary}"

    prompt = f"""
    あなたは非常に優秀で、かつ科学的根拠（Evidence-Based）を重視する厳格なパーソナルトレーナー兼栄養士です。
    以下のデータに基づき、現状の評価と次週のアクションプランをレポートしてください。

    ### ユーザーデータ
    {workout_text}
    {nutrition_text}

    ### レポート要件 (Markdown)
    1. **トレーニング分析**: 漸進性負荷は達成できているか？部位の偏りは？
    2. **栄養分析**: カロリー収支とPFCバランスの評価。
    3. **アクションプラン**: 具体的な修正点（種目、重量、食事内容）。
    """
    
    model = genai.GenerativeModel('gemini-pro') # 安定版モデルに変更
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"エラーが発生しました: {str(e)}"

# ==========================================
# UI構築
# ==========================================
st.set_page_config(layout="wide", page_title="Bio-Log Cloud V2")
st.title("☁️ Bio-Log Cloud V2 (JST)")

if 'sheet_init' not in st.session_state:
    init_sheets()
    st.session_state.sheet_init = True

if 'workout_queue' not in st.session_state:
    st.session_state.workout_queue = []

if 'meal_form_data' not in st.session_state:
    st.session_state.meal_form_data = {
        "menu": "", "cal": 0, "p": 0.0, "f": 0.0, "c": 0.0
    }

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
tab1, tab2, tab3, tab4 = st.tabs(["📊 カロリー収支", "📈 漸進性負荷分析", "📝 記録入力", "🤖 AIコーチ"])

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
                "Date": st.column_config.TextColumn("日付"),
                "Total_Out": st.column_config.NumberColumn("総消費 (基礎+運動)", format="%d kcal"),
                "Balance": st.column_config.ProgressColumn("収支", format="%d kcal", min_value=-1000, max_value=1000),
            },
            use_container_width=True, hide_index=True
        )

with tab2:
    st.subheader("💪 Progressive Overload Tracker")
    df_w = load_data(WS_WORKOUT)
    
    if not df_w.empty:
        required_cols = ['Weight', 'Reps', 'Sets', 'Volume']
        for col in required_cols:
             if col not in df_w.columns:
                 df_w[col] = 0 
             else:
                 df_w[col] = pd.to_numeric(df_w[col], errors='coerce').fillna(0)

        if 'Exercise' in df_w.columns:
            unique_exercises = df_w['Exercise'].unique()
            if len(unique_exercises) > 0:
                selected_ex = st.selectbox("分析する種目を選択", unique_exercises)
                df_chart = df_w[df_w['Exercise'] == selected_ex].sort_values("Date")
                
                if not df_chart.empty:
                    c = alt.Chart(df_chart).mark_line(point=True).encode(
                        x='Date',
                        y=alt.Y('Volume', title='総負荷量 (kg×reps×sets)'),
                        tooltip=['Date', 'Weight', 'Reps', 'Sets', 'Volume', 'Notes'] 
                    ).properties(title=f"{selected_ex} のボリューム推移")
                    st.altair_chart(c, use_container_width=True)
                    
                    c2 = alt.Chart(df_chart).mark_line(point=True, color='orange').encode(
                        x='Date',
                        y=alt.Y('Weight', title='扱う重量 (kg)', scale=alt.Scale(zero=False)),
                        tooltip=['Date', 'Weight']
                    ).properties(title=f"{selected_ex} の重量推移")
                    st.altair_chart(c2, use_container_width=True)
            else:
                st.info("データはありますが、種目が見つかりません。")
        else:
            st.warning("シートの形式が古いため、分析できません。新しいデータを記録すると修正されます。")
    else:
        st.info("まだトレーニングデータがありません。")

with tab3:
    st.subheader("📅 日時設定 (JST)")
    JST = timezone(timedelta(hours=9), 'JST')
    
    if 'default_date' not in st.session_state:
        st.session_state.default_date = datetime.now(JST).date()
    if 'default_time' not in st.session_state:
        st.session_state.default_time = datetime.now(JST).time()

    c_date, c_time = st.columns(2)
    input_date = c_date.date_input("日付", value=st.session_state.default_date)
    input_time = c_time.time_input("時間", value=st.session_state.default_time)
    
    target_datetime = datetime.combine(input_date, input_time)
    formatted_date = target_datetime.strftime("%Y-%m-%d %H:%M")
    formatted_day = target_datetime.strftime("%Y-%m-%d")

    st.divider()
    
    col_w, col_m = st.columns(2)
    
    with col_w:
        st.subheader("🏋️ 筋トレ入力")
        
        with st.form("workout_add_form"):
            ex_categories = {
                "胸": ["ベンチプレス", "ダンベルベンチプレス", "インクラインダンベルプレス", "ディップス"],
                "背中": ["懸垂", "ラットプルダウン", "ロー", "ダンベルロー", "ケーブルロー"],
                "脚": ["スクワット", "デッドリフト", "レッグプレス", "レッグエクステンション", "レッグカール"],
                "肩": ["ショルダープレス", "サイドレイズ", "ケーブルサイドレイズ"],
                "その他": ["アームカール", "ランニング"]
            }
            flat_ex_list = []
            for cat, items in ex_categories.items():
                flat_ex_list.extend(items)
            
            ex_name = st.selectbox("種目", flat_ex_list)
            
            weight_in = st.number_input("重量(kg)", min_value=0.0, value=60.0, step=2.5)
            reps_in = st.number_input("回数", min_value=0, value=10, step=1)
            sets_in = st.number_input("セット", min_value=1, value=1, step=1)
            duration_in = st.number_input("時間(分)", min_value=0, value=5, step=1)
            
            notes_in = st.text_area("メモ (フォームの修正点など)", height=80, placeholder="例: 肘が開きすぎないように注意")
            
            add_to_queue = st.form_submit_button("リストに追加 (まだ保存されません)")
            
            if add_to_queue:
                workout_burn = round(6.0 * weight * (duration_in / 60) * 1.05, 1)
                volume = weight_in * reps_in * sets_in
                
                item = {
                    "Date": formatted_date,
                    "Day": formatted_day,
                    "Exercise": ex_name, "Weight": weight_in, "Reps": reps_in, 
                    "Sets": sets_in, "Duration": duration_in, "Burned_Cal": workout_burn,
                    "Volume": volume,
                    "Notes": notes_in 
                }
                st.session_state.workout_queue.append(item)
                st.success(f"リストに追加: {ex_name}")

        st.markdown("#### 📝 送信待ちリスト")
        
        if len(st.session_state.workout_queue) > 0:
            df_queue = pd.DataFrame(st.session_state.workout_queue)
            st.dataframe(df_queue[["Exercise", "Weight", "Reps", "Sets", "Notes"]], hide_index=True)
            
            if st.button("クラウドに一括保存", type="primary"):
                with st.spinner("送信中..."):
                    save_rows_to_sheet(WS_WORKOUT, st.session_state.workout_queue)
                    update_daily_summary_sheet(daily_base_burn)
                    st.session_state.workout_queue = []
                    st.success("全てのデータを保存しました！")
                    st.rerun()
            
            if st.button("リストをクリア"):
                st.session_state.workout_queue = []
                st.rerun()
        else:
            st.info("ここにセットが追加されます")

    with col_m:
        st.subheader("🥗 食事")
        
        input_method = st.radio("入力方法", ["📸 画像解析", "✏️ テキスト検索", "🖐️ 完全手動"], horizontal=True)
        
        if input_method == "📸 画像解析":
            img_file = st.file_uploader("画像", type=["jpg", "png"])
            if img_file and st.button("解析実行"):
                with st.spinner('解析中...'):
                    res = analyze_meal_image(Image.open(img_file))
                    if "error" not in res:
                        st.session_state.meal_form_data = {
                            "menu": res.get('menu_name', ''),
                            "cal": res.get('calories', 0),
                            "p": res.get('protein', 0.0),
                            "f": res.get('fat', 0.0),
                            "c": res.get('carbs', 0.0)
                        }
                        st.success("解析完了！下で確認してください。")
                    else:
                        st.error(f"解析エラー: {res.get('error')}")

        elif input_method == "✏️ テキスト検索":
            text_query = st.text_input("食べたものを入力 (例: 牛丼 並盛, プロテインバー)", placeholder="例: 鶏むね肉のサラダ")
            if st.button("栄養素を自動推測"):
                if text_query:
                    with st.spinner('AIが成分表を検索中...'):
                        res = estimate_nutrition_from_text(text_query)
                        if "error" not in res:
                            st.session_state.meal_form_data = {
                                "menu": res.get('menu_name', text_query),
                                "cal": res.get('calories', 0),
                                "p": res.get('protein', 0.0),
                                "f": res.get('fat', 0.0),
                                "c": res.get('carbs', 0.0)
                            }
                            st.success(f"推測完了: {res.get('menu_name')}")
                        else:
                            st.error(f"エラーが発生しました: {res.get('error')}")
                else:
                    st.warning("メニュー名を入力してください。")

        st.divider()
        st.write("▼ 内容を確認・修正して保存")

        with st.form("meal_save_form"):
            menu_name = st.text_input("メニュー名", value=st.session_state.meal_form_data["menu"])
            cal_in = st.number_input("カロリー (kcal)", value=st.session_state.meal_form_data["cal"])
            
            c1, c2, c3 = st.columns(3)
            p_in = c1.number_input("P (g)", value=float(st.session_state.meal_form_data["p"]))
            f_in = c2.number_input("F (g)", value=float(st.session_state.meal_form_data["f"]))
            c_in = c3.number_input("C (g)", value=float(st.session_state.meal_form_data["c"]))
            
            meal_submit = st.form_submit_button("食事を保存", type="primary")
            
            if meal_submit:
                data = {
                    "Date": formatted_date,
                    "Day": formatted_day,
                    "Menu": menu_name, "Cal": cal_in,
                    "P": p_in, "F": f_in, "C": c_in
                }
                save_to_sheet(WS_MEAL, data)
                update_daily_summary_sheet(daily_base_burn)
                
                st.session_state.meal_form_data = {"menu": "", "cal": 0, "p": 0.0, "f": 0.0, "c": 0.0}
                st.success(f"保存しました: {menu_name}")
                st.rerun()

# --- Tab 4: AIコーチ ---
with tab4:
    st.header("🤖 AI分析レポート")
    st.write("直近1週間のトレーニングと食事データを分析し、客観的なアドバイスを作成します。")
    
    if st.button("📝 レポートを作成する"):
        with st.spinner("AIがデータを分析中..."):
            advice = generate_advice(days=7)
            st.markdown("---")
            st.markdown(advice)
