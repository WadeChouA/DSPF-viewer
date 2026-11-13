import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import html
import re
import unicodedata

def get_display_width(text):
    """計算字串在 AS400 5250 畫面上的實際佔用格數（中文字/全形字佔 2 格，英文/半形佔 1 格）"""
    width = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ('F', 'W', 'A'):
            width += 2
        else:
            width += 1
    return width

def has_dbcs(text):
    """判斷字串中是否包含中文/全形字元（AS400 EBCDIC 會在前後加上 SO/SI 控制碼）"""
    return any(unicodedata.east_asian_width(ch) in ('F', 'W', 'A') for ch in text)

def parse_dds_by_record(file_content, default_ref_len=6):
    """解析 DSPF 檔案，支援絕對座標、+n 相對座標、R 參照欄位與 DATE/TIME 系統欄位"""
    records = {}
    current_record = None
    lines = file_content.splitlines()
    
    # 追蹤前一個元素的結束位置，用於計算 +n 相對座標
    last_row = 1
    last_end_col = 1

    for line in lines:
        if not line.strip():
            continue
            
        # 1. 偵測並跳過 12-byte 偏移量 (或根據 A 的位置動態定位)
        offset = None
        if len(line) > 17 and line[17].upper() == 'A' and (len(line) <= 18 or line[18] in (' ', '*', 'R', 'O', 'N')):
            offset = 12
        elif len(line) > 5 and line[5].upper() == 'A':
            offset = 0
        else:
            continue
            
        # 略過註解行 (第 7 格為 '*')
        if len(line) > offset + 6 and line[offset + 6] == '*':
            continue
            
        # 2. 判斷是否為新的 Record Format 定義 (第 17 格為 'R')
        name_type = line[offset + 16 : offset + 17].upper() if len(line) > offset + 16 else ''
        if name_type == 'R':
            record_name = line[offset + 18 : offset + 28].strip()
            if record_name:
                current_record = record_name
                if current_record not in records:
                    records[current_record] = []
                last_row = 1
                last_end_col = 1
            continue
            
        if not current_record:
            current_record = 'GLOBAL'
            if current_record not in records:
                records[current_record] = []

        if len(line) < offset + 44:
            continue
            
        # 提取 DDS 各固定欄位
        field_name = line[offset + 18 : offset + 28].strip()
        ref_ind = line[offset + 28 : offset + 29].strip().upper()
        length_str = line[offset + 29 : offset + 34].strip()
        data_type = line[offset + 34 : offset + 35].strip().upper()
        dec_pos = line[offset + 35 : offset + 37].strip()
        usage = line[offset + 37 : offset + 38].strip().upper()
        row_str = line[offset + 38 : offset + 41].strip()
        col_str = line[offset + 41 : offset + 44].strip()
        text_part = line[offset + 44:].strip()

        # 處理隱藏欄位 (Usage = 'H')：無螢幕座標，但需列入規格表
        if usage == 'H' and field_name:
            length = int(length_str) if length_str.isdigit() else default_ref_len
            records[current_record].append({
                'type': 'field', 'row': 0, 'col': 0,
                'text': '', 'length': length, 'usage': 'H',
                'name': field_name, 'ref': (ref_ind == 'R'),
                'data_type': data_type, 'keywords': text_part
            })
            continue

        # 3. 判斷座標 (支援絕對座標 與 +n 相對座標)
        row = None
        col = None
        if row_str.isdigit() and col_str.isdigit():
            row = int(row_str)
            col = int(col_str)
        elif (row_str == '' or row_str.isdigit()) and col_str.startswith('+'):
            offset_str = col_str[1:].strip()
            if offset_str.isdigit():
                plus_n = int(offset_str)
                row = int(row_str) if row_str.isdigit() else last_row
                col = last_end_col + plus_n
        
        if row is None or col is None:
            continue

        # 4. 判斷元素類型
        if text_part.startswith("'"):
            # 常數文字 (Constant)
            end_idx = text_part.find("'", 1)
            if end_idx != -1:
                text = text_part[1:end_idx]
            else:
                # 處理跨行接續的常數文字 (結尾為 -)
                text = text_part[1:].rstrip('-')
            
            disp_w = get_display_width(text)
            records[current_record].append({
                'type': 'constant', 'row': row, 'col': col,
                'text': text, 'length': disp_w, 'usage': '', 'name': ''
            })
            last_row = row
            # 若常數含中文字，AS400 計算 +n 時會包含 SO/SI 佔位，加 2 格讓後續欄位對齊更精準
            dbcs_pad = 2 if has_dbcs(text) else 0
            last_end_col = col + disp_w + dbcs_pad

        elif not field_name and text_part.upper().startswith(('DATE', 'TIME', '*DATE', '*TIME', 'SYSNAME', 'USER')):
            # 系統關鍵字常數 (如 DATE EDTCDE(Y))
            kw = text_part.split()[0].upper()
            sys_len = 8
            records[current_record].append({
                'type': 'constant', 'row': row, 'col': col,
                'text': 'YY/MM/DD' if 'DATE' in kw else 'HH:MM:SS',
                'length': sys_len, 'usage': 'O', 'name': kw
            })
            last_row = row
            last_end_col = col + sys_len

        elif field_name or length_str.isdigit() or ref_ind == 'R':
            # 變數欄位 (具名或未具名欄位)
            is_ref = (ref_ind == 'R' or 'REFFLD' in text_part.upper())
            if length_str.isdigit():
                length = int(length_str)
            else:
                # 若為 R (參照外部檔案欄位)，嘗試從 EDTWRD 推算顯示長度，否則使用預設長度
                edtwrd_match = re.search(r"EDTWRD\('([^']+)'\)", text_part)
                if edtwrd_match:
                    length = len(edtwrd_match.group(1))
                else:
                    length = default_ref_len

            if usage not in ['I', 'O', 'B', 'H', 'M']:
                usage = 'O'

            # 計算在畫面上的實際顯示寬度 (若含 EDTWRD 則以編輯字樣長度為準)
            edtwrd_match = re.search(r"EDTWRD\('([^']+)'\)", text_part)
            disp_len = len(edtwrd_match.group(1)) if edtwrd_match else length

            records[current_record].append({
                'type': 'field', 'row': row, 'col': col,
                'text': '', 'length': length, 'disp_len': disp_len,
                'usage': usage, 'name': field_name,
                'ref': is_ref, 'data_type': data_type, 'keywords': text_part
            })
            last_row = row
            last_end_col = col + disp_len

    return records

def render_screen(elements):
    """將解析出來的元素繪製到 24x80 的二維陣列中，正確處理中文字雙倍寬度"""
    # 每個儲存格存放要顯示的字串；若某格被中文字的後半部佔用，設為 None
    screen_buffer = [[' ' for _ in range(80)] for _ in range(24)]
    table_data = []
    
    for el in elements:
        if el['type'] == 'field':
            usage_desc = {'I': '輸入 (I)', 'O': '輸出 (O)', 'B': '雙向 (B)', 'H': '隱藏 (H)'}.get(el['usage'], el['usage'])
            len_display = f"{el['length']} (參照 R)" if el.get('ref') else str(el['length'])
            table_data.append({
                "欄位名稱": el['name'],
                "行號 (Row)": el['row'] if el['row'] > 0 else '-',
                "列號 (Col)": el['col'] if el['col'] > 0 else '-',
                "長度": len_display,
                "型態": el.get('data_type', '') or ('REF' if el.get('ref') else ''),
                "屬性": usage_desc,
                "關鍵字 / 備註": el.get('keywords', '')
            })

        r = el['row'] - 1
        c = el['col'] - 1
        
        if 0 <= r < 24 and 0 <= c < 80:
            if el['type'] == 'constant':
                content = el['text']
            else:
                d_len = el.get('disp_len', el['length'])
                if el['usage'] == 'I':
                    content = '_' * d_len
                elif el['usage'] == 'B':
                    content = 'B' * d_len
                elif el['usage'] == 'H':
                    continue
                else:
                    content = 'O' * d_len
                    
            curr_c = c
            for ch in content:
                if curr_c >= 80:
                    break
                w = get_display_width(ch)
                if w == 2:
                    screen_buffer[r][curr_c] = ch
                    if curr_c + 1 < 80:
                        screen_buffer[r][curr_c + 1] = None # 佔用第 2 格
                    curr_c += 2
                else:
                    screen_buffer[r][curr_c] = ch
                    curr_c += 1
                    
    lines = []
    for row in screen_buffer:
        line_str = "".join(cell for cell in row if cell is not None)
        lines.append(line_str)
        
    screen_text = "\n".join(lines)
    return screen_text, table_data

# --- 網頁介面開始 ---
st.set_page_config(page_title="AS400 綠幕模擬器", layout="wide")
st.title("📟 AS400 DSPF 綠幕預覽器")

col1, col2 = st.columns([2, 1])
with col1:
    encoding_option = st.selectbox(
        "1. 選擇檔案編碼 (若中文出現亂碼請切換)：",
        ["UTF-8 (現代標準)", "Big5 / CP950 (繁體中文)", "Shift_JIS (日文)", "GBK (簡體中文)"]
    )
with col2:
    default_ref_len = st.number_input("外部參照欄位 (R) 預設顯示長度：", min_value=1, max_value=30, value=6)

encoding_map = {
    "UTF-8 (現代標準)": "utf-8",
    "Big5 / CP950 (繁體中文)": "cp950",
    "Shift_JIS (日文)": "shift_jis",
    "GBK (簡體中文)": "gbk"
}
selected_enc = encoding_map[encoding_option]

uploaded_file = st.file_uploader("2. 選擇 DSPF 文字檔", type=["txt", "dds"])

if uploaded_file is not None:
    raw_bytes = uploaded_file.getvalue()
    # 自動嘗試解碼：若選定的編碼失敗，嘗試常見編碼
    try:
        string_data = raw_bytes.decode(selected_enc)
    except UnicodeDecodeError:
        try:
            string_data = raw_bytes.decode("cp950")
        except UnicodeDecodeError:
            string_data = raw_bytes.decode("utf-8", errors="replace")

    if string_data:
        records_dict = parse_dds_by_record(string_data, default_ref_len=default_ref_len)
        
        if not records_dict:
            st.error("無法解析此檔案，請確認是否為標準的 AS400 DDS 原始碼。")
        else:
            record_names = list(records_dict.keys())
            # 支援多選疊加預覽 (例如主畫面 S1 疊加底部功能鍵 SCMD)
            selected_records = st.multiselect(
                "3. 請選擇要預覽的畫面格式 (可複選疊加，例如同時選 S1 與 SCMD)：",
                record_names,
                default=[record_names[0]]
            )
            
            if selected_records:
                combined_elements = []
                for rec in selected_records:
                    combined_elements.extend(records_dict[rec])
                    
                screen_text, table_data = render_screen(combined_elements)
                safe_screen_text = html.escape(screen_text)
                
                st.subheader(f"🖥️ 畫面預覽：{' + '.join(selected_records)}")
                
                # 使用 components.html 獨立渲染，徹底解決 </pre></div> 標籤外露與中英文對齊問題
                html_block = f"""
                <div style="background-color: #000000; padding: 20px; border-radius: 10px; border: 2px solid #333; overflow-x: auto; display: inline-block; min-width: 95%;">
                    <pre style="color: #00FF00; font-family: 'MS Gothic', 'MingLiU', 'DFKai-SB', 'Consolas', 'Courier New', monospace; font-size: 16px; font-weight: bold; line-height: 1.25; margin: 0; letter-spacing: 0px;">{safe_screen_text}</pre>
                </div>
                """
                components.html(html_block, height=520, scrolling=True)
                
                st.subheader("📋 變數欄位規格清單")
                if table_data:
                    df = pd.DataFrame(table_data)
                    st.dataframe(df, use_container_width=True)
                else:
                    st.info("此畫面沒有任何變數欄位。")
