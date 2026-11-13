import streamlit as st
import pandas as pd
import html

def parse_dds(file_content):
    """解析 DSPF (DDS 格式) 檔案，自動處理 12-byte 序號前綴"""
    elements = []
    lines = file_content.split('\n')
    
    for line in lines:
        if not line.strip():
            continue
            
        # 偵測是否有 12 bytes 的序號+日期前綴 (SRCPF 標準格式)
        # DDS 格式的規格類型 (例如 'A') 通常在第 6 個字元(索引5)。若有前綴，則會退後到第 18 個字元(索引17)。
        offset = 0
        if len(line) > 17 and line[17].upper() == 'A':
            offset = 12
        elif len(line) > 5 and line[5].upper() == 'A':
            offset = 0
        else:
            continue # 不是標準的畫面/欄位定義行，直接跳過
            
        # 確保字串長度足夠提取資料
        if len(line) < offset + 44:
            continue
            
        # 加上 offset 後，精準抓取對應欄位
        row_str = line[offset + 38 : offset + 41].strip()
        col_str = line[offset + 41 : offset + 44].strip()
        
        if not row_str.isdigit() or not col_str.isdigit():
            continue
            
        row = int(row_str)
        col = int(col_str)
        
        # 判斷是否為常數文字 (包在單引號內)
        text_part = line[offset + 44:].strip()
        if text_part.startswith("'"):
            end_idx = text_part.find("'", 1)
            if end_idx != -1:
                text = text_part[1:end_idx]
                elements.append({'type': 'constant', 'row': row, 'col': col, 'text': text, 'length': len(text), 'usage': ''})
        else:
            # 抓取變數欄位長度與屬性
            length_str = line[offset + 29 : offset + 34].strip()
            if length_str.isdigit():
                length = int(length_str)
                usage = line[offset + 37 : offset + 38].strip().upper()
                if usage not in ['I', 'O', 'B']:
                    usage = 'O'
                elements.append({'type': 'field', 'row': row, 'col': col, 'text': '', 'length': length, 'usage': usage})
    return elements

# --- 網頁介面開始 ---
st.set_page_config(page_title="AS400 綠幕模擬器", layout="wide")
st.title("📟 AS400 DSPF 綠幕預覽器")
st.write("上傳你的 DDS 原始碼，立刻預覽 5250 終端機排版！")

uploaded_file = st.file_uploader("選擇 DSPF 文字檔", type=["txt", "dds"])

if uploaded_file is not None:
    # 讀取上傳的檔案內容
    string_data = uploaded_file.getvalue().decode("utf-8", errors="ignore")
    elements = parse_dds(string_data)
    
    # 建立 24x80 空白畫布
    screen_buffer = [[' ' for _ in range(80)] for _ in range(24)]
    table_data = []
    
    for el in elements:
        r = el['row'] - 1
        c = el['col'] - 1
        
        if 0 <= r < 24 and 0 <= c < 80:
            if el['type'] == 'constant':
                content = el['text']
            else:
                if el['usage'] == 'I':
                    content = '_' * el['length']
                elif el['usage'] == 'B':
                    content = 'B' * el['length']
                else:
                    content = 'X' * el['length']
                    
                usage_desc = {'I': '輸入 (Input)', 'O': '輸出 (Output)', 'B': '雙向 (Both)'}.get(el['usage'], '未知')
                table_data.append({"行號 (Row)": el['row'], "列號 (Col)": el['col'], "長度": el['length'], "屬性": usage_desc})
                
            for i, char in enumerate(content):
                if c + i < 80:
                    screen_buffer[r][c + i] = char
                    
    # 將畫面轉成字串
    screen_text = "\n".join("".join(row) for row in screen_buffer)
    
    # 【關鍵修復】跳脫特殊字元 (例如 < 或 >)，避免破壞 HTML 結構
    safe_screen_text = html.escape(screen_text)
    
    # 顯示綠幕預覽區
    st.subheader("🖥️ 畫面預覽")
    st.markdown(f"""
        <div style="background-color: black; padding: 20px; border-radius: 10px; overflow-x: auto; margin-bottom: 20px;">
            <pre style="color: #00FF00; font-family: 'Consolas', 'Courier New', monospace; font-size: 16px; font-weight: bold; line-height: 1.2; margin: 0;">{safe_screen_text}</pre>
        </div>
    """, unsafe_allow_html=True)
    
    # 顯示下方規格表
    st.subheader("📋 欄位規格清單")
    if table_data:
        df = pd.DataFrame(table_data)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("沒有找到任何有效的變數欄位，請確認檔案格式。")
