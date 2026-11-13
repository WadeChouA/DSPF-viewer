import streamlit as st
import pandas as pd

def parse_dds(file_content):
    """解析 DSPF (DDS 格式) 檔案"""
    elements = []
    lines = file_content.split('\n')
    
    for line in lines:
        if len(line) < 45 or (len(line) > 6 and line[6] == '*'):
            continue
            
        row_str = line[38:41].strip()
        col_str = line[41:44].strip()
        
        if not row_str.isdigit() or not col_str.isdigit():
            continue
            
        row = int(row_str)
        col = int(col_str)
        
        text_part = line[44:].strip()
        if text_part.startswith("'"):
            end_idx = text_part.find("'", 1)
            if end_idx != -1:
                text = text_part[1:end_idx]
                elements.append({'type': 'constant', 'row': row, 'col': col, 'text': text, 'length': len(text), 'usage': ''})
        else:
            length_str = line[29:34].strip()
            if length_str.isdigit():
                length = int(length_str)
                usage = line[37:38].strip().upper()
                if usage not in ['I', 'O', 'B']:
                    usage = 'O'
                elements.append({'type': 'field', 'row': row, 'col': col, 'text': '', 'length': length, 'usage': usage})
    return elements

# --- 網頁介面開始 ---
st.set_page_config(page_title="AS400 綠幕模擬器", layout="centered")
st.title("📟 AS400 DSPF 綠幕預覽器")
st.write("上傳你的 DDS 原始碼，立刻預覽 5250 終端機排版！")

uploaded_file = st.file_uploader("選擇 DSPF 文字檔", type=["txt", "dds"])

if uploaded_file is not None:
    # 讀取上傳的檔案內容
    string_data = uploaded_file.getvalue().decode("utf-8", errors="ignore")
    elements = parse_dds(string_data)
    
    # 建立 24x80 空白畫布
    screen_buffer = [[' ' for _ in range(80)] for _ in range(24)]
    
    table_data = [] # 用來存下方表格的資料
    
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
                    
                # 加入表格資料
                usage_desc = {'I': '輸入 (Input)', 'O': '輸出 (Output)', 'B': '雙向 (Both)'}.get(el['usage'], '未知')
                table_data.append({"行號 (Row)": el['row'], "列號 (Col)": el['col'], "長度": el['length'], "屬性": usage_desc})
                
            for i, char in enumerate(content):
                if c + i < 80:
                    screen_buffer[r][c + i] = char
                    
    screen_text = "\n".join("".join(row) for row in screen_buffer)
    
    # 顯示綠幕預覽區 (使用 Markdown 注入 CSS 來模擬黑底綠字)
    st.subheader("🖥️ 畫面預覽")
    st.markdown(f"""
        <div style="background-color: black; padding: 20px; border-radius: 10px; overflow-x: auto;">
            <pre style="color: #00FF00; font-family: 'Consolas', monospace; font-size: 14px; font-weight: bold; line-height: 1.2;">{screen_text}</pre>
        </div>
    """, unsafe_allow_html=True)
    
    # 顯示下方規格表
    st.subheader("📋 欄位規格清單")
    if table_data:
        df = pd.DataFrame(table_data)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("沒有找到任何變數欄位。")
