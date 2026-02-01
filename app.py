import streamlit as st
import google.generativeai as genai
import pandas as pd
from PIL import Image

# 1. ページ設定（最初に行う必要があります）
st.set_page_config(page_title="Bio-Log Cloud V2", layout="wide")

# ==========================================
# デバッグ用：バージョン確認エリア
# （問題が解決したら後で削除してください）
# ==========================================
st.write("---")
st.warning(f"🔍 Debug Info: google-generativeai version: {genai.__version__}")
st.write("If version is < 0.8.3, requirements.txt was not applied.")
st.write("---")
# ==========================================

# 2. API設定
# Streamlit CloudのSecrets、またはローカル環境でのフォールバック
try:
    if "GEMINI_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    else:
        # ローカルテスト用（必要なければ削除可）
        # genai.configure(api_key="YOUR_LOCAL_API_KEY") 
        st.info("API Key not found in secrets.")
except Exception as e:
    st.error(f"API Configuration Error: {e}")

# 3. AI解析関数
def estimate_nutrition_from_text(text):
    """
    テキスト入力から栄養素を推測する関数
    """
    try:
        # モデル指定：gemini-1.5-flash
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # プロンプト作成（必要に応じて調整してください）
        prompt = f"""
        以下の食事内容から、カロリー、タンパク質(P)、脂質(F)、炭水化物(C)を推定し、
        JSON形式（キー: calories, protein, fat, carbs）のみで出力してください。
        
        食事内容: {text}
        """
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error: {str(e)}"

# 4. UI実装（メイン画面）
st.title("Bio-Log Cloud V2 🧬")

# 入力フォーム
user_input = st.text_area("食事内容を入力してください（例：鶏胸肉 200g、白米 150g）")

if st.button("栄養素を計算する"):
    if user_input:
        with st.spinner("Gemini 1.5 Flash is analyzing..."):
            result = estimate_nutrition_from_text(user_input)
            st.success("解析完了")
            st.markdown(result)
    else:
        st.warning("テキストを入力してください。")
