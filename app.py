import streamlit as st
import pandas as pd
import html
import re

def parse_dds_by_record(file_content):
    """解析 DSPF 檔案，並依據 Record Format 分類畫面元素"""
    records = {}
    current_record = None
    
    lines = file_content.split('\n')
    
    for line in lines:
        if not line.strip():
            continue
            
        # 1. 偵測並跳過 12-byte 偏移量
        offset = 0
        if len(line) > 17 and line[17].upper() == 'A':
            offset = 12
        elif len(line) > 5 and line[5].upper() == 'A':
            offset = 0
        else:
            continue
            
        # 2. 判斷是否為新的 Record Format 定義 (A 後面有 R，且第 16-25 byte 有名稱)
        name_type = line[offset + 16 : offset + 17].upper()
        if name_type == 'R':
            record_name = line[offset + 18 : offset + 28].strip()
            if record_name:
                current_record = record_name
                if current_record not in records:
                    records[current_record] = []
            continue
            
        # 若還沒抓到任何 Record，則將元素歸類到 'GLOBAL'
        if not current_record:
            current_record = 'GLOBAL'
            if current_record not in records:
                records[current_record] = []

        # 3. 抓取行號與列號
        if len(line) < offset + 44:
            continue
            
        row_str = line[offset + 38 : offset + 41].strip()
        col_str = line[offset + 41 : offset + 44].strip()
        
        if not row_str.isdigit() or not col_str.isdigit():
            continue
            
        row = int(row_str)
        col = int(col_str)
        
        # 4. 判斷常數或變數欄位
        text_part = line[offset + 44:].strip()
        if text_part.startswith("'"):
            # 常數文字
            end_idx = text_part.find("'", 1)
            if end_idx != -1:
                text = text_part[1:end_idx]
                records[current_record].append({
                    'type': 'constant', 'row': row, 'col': col, 
                    'text': text, 'length': len(text), 'usage': '', 'name': ''
                })
        else:
            # 變數欄位
            length_str = line[offset + 29 : offset + 34].strip()
            field_name = line[offset + 18 : offset + 28].strip() # 欄位名稱
            
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
                    content = ' ' * el['length'] # 隱藏欄位不顯示
                else:
                    content = 'X' * el['length']
                    
                usage_desc = {'I': '輸入 (I)', 'O': '輸出 (O)', 'B': '雙向 (B)', 'H': '隱藏 (H)'}.get(el['usage'], el['usage'])
                table_data.append({
                    "欄位名稱": el['name'], "行號 (Row)": el['row'], "列號 (Col)": el['col'], 
                    "長度": el['length'], "屬性": usage_desc
                })
                
            for i, char in enumerate(content):
                if c + i < 80:
                    # 避免覆蓋已有字元
                    screen_buffer[r][c + i] = char
                    
    screen_text = "\n".join("".join(row) for row in screen_buffer)
    return screen_text, table_data

# --- 網頁介面開始 ---
st.set_page_config(page_title="AS400 綠幕模擬器", layout="wide")
st.title("📟 AS400 DSPF 綠幕預覽器 (多畫面版)")
st.write("上傳你的 DDS 原始碼，支援切換不同 Record 畫面！")

uploaded_file = st.file_uploader("選擇 DSPF 文字檔", type=["txt", "dds"])

if uploaded_file is not None:
    string_data = uploaded_file.getvalue().decode("utf-8", errors="ignore")
    
    # 解析檔案，取得所有 records
    records_dict = parse_dds_by_record(string_data)
    
    if not records_dict:
        st.error("無法解析此檔案，請確認是否為標準的 AS400 DDS 原始碼。")
    else:
        # 在側邊欄或畫面上方提供選擇 Record 的下拉選單
        record_names = list(records_dict.keys())
        selected_record = st.selectbox("請選擇要預覽的畫面格式 (Record Format)：", record_names)
        
        # 取得選擇的畫面元素並渲染
        elements = records_dict[selected_record]
        screen_text, table_data = render_screen(elements)
        
        safe_screen_text = html.escape(screen_text)
        
        # 顯示綠幕
        st.subheader(f"🖥️ 畫面預覽：{selected_record}")
        st.markdown(f"""
            <div style="background-color: black; padding: 20px; border-radius: 10px; overflow-x: auto; margin-bottom: 20px;">
                <pre style="color: #00FF00; font-family: 'Consolas', 'Courier New', monospace; font-size: 16px; font-weight: bold; line-height: 1.2; margin: 0;">{safe_screen_text}</pre>
            </div>
        """, unsafe_allow_html=True)
        
        # 顯示欄位表格
        st.subheader("📋 變數欄位規格清單")
        if table_data:
            df = pd.DataFrame(table_data)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("此畫面沒有任何變數欄位。")
