import streamlit as st
import pandas as pd
import html

def parse_dds_by_record(file_content):
    """解析 DSPF 檔案，並依據 Record Format 分類畫面元素"""
    records = {}
    current_record = None
    lines = file_content.split('\n')
    
    for line in lines:
        if not line.strip():
            continue
            
        offset = 0
        if len(line) > 17 and line[17].upper() == 'A':
            offset = 12
        elif len(line) > 5 and line[5].upper() == 'A':
            offset = 0
        else:
            continue
            
        name_type = line[offset + 16 : offset + 17].upper()
        if name_type == 'R':
            record_name = line[offset + 18 : offset + 28].strip()
            if record_name:
                current_record = record_name
                if current_record not in records:
                    records[current_record] = []
            continue
            
        if not current_record:
            current_record = 'GLOBAL'
            if current_record not in records:
                records[current_record] = []

        if len(line) < offset + 44:
            continue
            
        row_str = line[offset + 38 : offset + 41].strip()
        col_str = line[offset + 41 : offset + 44].strip()
        
        if not row_str.isdigit() or not col_str.isdigit():
            continue
            
        row = int(row_str)
        col = int(col_str)
        
        text_part = line[offset + 44:].strip()
        if text_part.startswith("'"):
            end_idx = text_part.find("'", 1)
            if end_idx != -1:
                text = text_part[1:end_idx]
                records[current_record].append({
                    'type': 'constant', 'row': row, 'col': col, 
                    'text': text, 'length': len(text), 'usage': '', 'name': ''
                })
        else:
            length_str = line[offset + 29 : offset + 34].strip()
            field_name = line[offset + 18 : offset + 28].strip()
            if length_str.isdigit():
                length = int(length_str)
                usage = line[offset + 37 : offset + 38].strip().upper()
                if usage not in ['I', 'O', 'B', 'H', 'M']:
                    usage = 'O'
                records[current_record].append({
                    'type': 'field', 'row': row, 'col': col, 
                    'text': '', 'length': length, 'usage': usage, 'name': field_name
                })
    return records

def render_screen(elements):
    """將解析出來的元素繪製到 24x80 的二維陣列中"""
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
                elif el['usage'] == 'H':
                    content = ' ' * el['length']
                else:
                    content = 'X' * el['length']
                    
                usage_desc = {'I': '輸入 (I)', 'O': '輸出 (O)', 'B': '雙向 (B)', 'H': '隱藏 (H)'}.get(el['usage'], el['usage'])
                table_data.append({
                    "欄位名稱": el['name'], "行號 (Row)": el['row'], "列號 (Col)": el['col'], 
                    "長度": el['length'], "屬性": usage_desc
                })
                
            for i, char in enumerate(content):
                if c + i < 80:
                    screen_buffer[r][c + i] = char
                    
    screen_text = "\n".join("".join(row) for row in screen_buffer)
    return screen_text, table_data

# --- 網頁介面開始 ---
st.set_page_config(page_title="AS400 綠幕模擬器", layout="wide")
st.title("📟 AS400 DSPF 綠幕預覽器")

# 【修正 1】：新增編碼選擇器，解決亂碼問題
encoding_option = st.selectbox(
    "1. 選擇檔案編碼 (若出現亂碼請切換編碼)：",
    ["Shift_JIS (日文/舊系統常見)", "Big5 (繁體中文)", "UTF-8 (網頁標準)", "GBK (簡體中文)"]
)
encoding_map = {
    "Shift_JIS (日文/舊系統常見)": "shift_jis",
    "Big5 (繁體中文)": "big5",
    "UTF-8 (網頁標準)": "utf-8",
    "GBK (簡體中文)": "gbk"
}
selected_enc = encoding_map[encoding_option]

uploaded_file = st.file_uploader("2. 選擇 DSPF 文字檔", type=["txt", "dds"])

if uploaded_file is not None:
    try:
        # 使用選擇的編碼讀取檔案，遇到無法解析的字元用 ? 替代，避免程式當機
        string_data = uploaded_file.getvalue().decode(selected_enc, errors="replace")
    except Exception as e:
        st.error(f"檔案解碼失敗：{e}")
        string_data = ""

    if string_data:
        records_dict = parse_dds_by_record(string_data)
        
        if not records_dict:
            st.error("無法解析此檔案，請確認是否為標準的 AS400 DDS 原始碼。")
        else:
            record_names = list(records_dict.keys())
            selected_record = st.selectbox("3. 請選擇要預覽的畫面格式 (Record Format)：", record_names)
            
            elements = records_dict[selected_record]
            screen_text, table_data = render_screen(elements)
            
            safe_screen_text = html.escape(screen_text)
            
            st.subheader(f"🖥️ 畫面預覽：{selected_record}")
            
            # 【修正 2】：消除 HTML 內的換行，防止 Markdown 解析器誤判導致標籤外露
            html_content = f'<div style="background-color: black; padding: 20px; border-radius: 10px; overflow-x: auto; margin-bottom: 20px;"><pre style="color: #00FF00; font-family: \'Consolas\', \'Courier New\', monospace; font-size: 16px; font-weight: bold; line-height: 1.2; margin: 0;">{safe_screen_text}</pre></div>'
            st.markdown(html_content, unsafe_allow_html=True)
            
            st.subheader("📋 變數欄位規格清單")
            if table_data:
                df = pd.DataFrame(table_data)
                st.dataframe(df, use_container_width=True)
            else:
                st.info("此畫面沒有任何變數欄位。")
