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
    """判斷字串中是否包含中文/全形字元"""
    return any(unicodedata.east_asian_width(ch) in ('F', 'W', 'A') for ch in text)

def detect_offset(line):
    """偵測 AS400 DSPF 原始碼是否有 12-byte 序號前綴"""
    if len(line) > 17 and line[17].upper() == 'A' and (len(line) <= 18 or line[18] in (' ', '*', 'R', 'O', 'N')):
        return 12
    elif len(line) > 5 and line[5].upper() == 'A':
        return 0
    return None

def parse_ref_files(ref_contents):
    """
    解析上傳的外部參照檔 (如 LAYOUT.txt)。
    支援省略第 6 格 'A' 的格式，並自動處理單行或被壓縮成同一行的欄位定義。
    """
    ref_dict = {}
    # 匹配模式：(可選前綴) + 欄位名稱 + 長度(1~5位數) + 型態(A/S/O/P/B/J/E或空白) + 小數位(可選) + 後續關鍵字(COLHDG/TEXT/EDTCDE等或行尾)
    pattern = re.compile(
        r'(?:^|\s)(?!R\s|K\s)([A-Z0-9_#@$]{2,10})\s+(\d{1,5})([ASOPBJE\s])(?:\s*(\d{1,2}))?(?=\s+(?:COLHDG\vert{}TEXT\vert{}EDTCDE\vert{}EDTWRD\vert{}VALUES\vert{}CHECK\vert{}COMP\vert{}RANGE\vert{}ALIAS\vert{}DFT)\vert{}\s*$)',
        re.IGNORECASE | re.MULTILINE
    )
    
    for content in ref_contents:
        # 1. 先逐行處理標準固定欄位或省略 'A' 的行
        for line in content.splitlines():
            if not line.strip():
                continue
            # 排除註解行 (含 ** 或 A* 或 /*)
            stripped = line.strip()
            if stripped.startswith('*') or stripped.startswith('/*') or 'A*' in line[:25] or '**' in line[:25]:
                continue
                
            for match in pattern.finditer(line):
                field_name = match.group(1).strip().upper()
                length_str = match.group(2).strip()
                data_type = match.group(3).strip().upper()
                dec_pos = (match.group(4) or '').strip()
                
                # 若型態為空白，若有小數位則為 S (Signed Numeric)，否則為 A (Character)
                if not data_type:
                    data_type = 'S' if dec_pos != '' else 'A'
                    
                if field_name and length_str.isdigit():
                    ref_dict[field_name] = {
                        'length': int(length_str),
                        'data_type': data_type,
                        'dec_pos': dec_pos
                    }
    return ref_dict

def parse_dds_by_record(file_content, ref_dict, default_ref_len=6):
    """解析 DSPF 檔案，支援 REFFLD 查表、+n 動態重算與相對座標"""
    records = {}
    current_record = None
    lines = file_content.splitlines()

    for line in lines:
        if not line.strip():
            continue
            
        offset = detect_offset(line)
        if offset is None or (len(line) > offset + 6 and line[offset + 6] == '*'):
            continue
            
        name_type = line[offset + 16 : offset + 17].upper() if len(line) > offset + 16 else ''
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
            
        field_name = line[offset + 18 : offset + 28].strip()
        ref_ind = line[offset + 28 : offset + 29].strip().upper()
        length_str = line[offset + 29 : offset + 34].strip()
        data_type = line[offset + 34 : offset + 35].strip().upper()
        dec_pos = line[offset + 35 : offset + 37].strip()
        usage = line[offset + 37 : offset + 38].strip().upper()
        row_str = line[offset + 38 : offset + 41].strip()
        col_str = line[offset + 41 : offset + 44].strip()
        text_part = line[offset + 44:].strip()

        pos_mode = None
        raw_row = None
        raw_col = None

        if usage == 'H' and field_name:
            pos_mode = 'HIDDEN'
        elif row_str.isdigit() and col_str.isdigit():
            pos_mode = 'ABS'
            raw_row = int(row_str)
            raw_col = int(col_str)
        elif (row_str == '' or row_str.isdigit()) and col_str.startswith('+'):
            offset_str = col_str[1:].strip()
            if offset_str.isdigit():
                pos_mode = 'REL'
                raw_row = int(row_str) if row_str.isdigit() else None
                raw_col = int(offset_str)

        if pos_mode is None:
            continue

        # 1. 常數文字
        if text_part.startswith("'"):
            end_idx = text_part.find("'", 1)
            text = text_part[1:end_idx] if end_idx != -1 else text_part[1:].rstrip('-')
            disp_w = get_display_width(text)
            records[current_record].append({
                'type': 'constant', 'pos_mode': pos_mode, 'raw_row': raw_row, 'raw_col': raw_col,
                'text': text, 'length': disp_w, 'disp_len': disp_w, 'usage': '', 'name': ''
            })

        # 2. 系統關鍵字 (DATE / TIME)
        elif not field_name and text_part.upper().startswith(('DATE', 'TIME', '*DATE', '*TIME', 'SYSNAME', 'USER')):
            kw = text_part.split()[0].upper()
            sys_len = 8
            records[current_record].append({
                'type': 'constant', 'pos_mode': pos_mode, 'raw_row': raw_row, 'raw_col': raw_col,
                'text': 'YY/MM/DD' if 'DATE' in kw else 'HH:MM:SS',
                'length': sys_len, 'disp_len': sys_len, 'usage': 'O', 'name': kw
            })

        # 3. 變數欄位 (含 R / REFFLD 處理)
        elif field_name or length_str.isdigit() or ref_ind == 'R':
            is_ref = (ref_ind == 'R' or 'REFFLD' in text_part.upper())
            
            # 抓取 REFFLD(xxx) 指定的目標欄位名稱，若無則預設與自身欄位同名
            reffld_match = re.search(r"REFFLD\(\s*([A-Za-z0-9_#@$]+)", text_part, re.IGNORECASE)
            target_ref_name = reffld_match.group(1).upper() if reffld_match else field_name.upper()
            
            ref_source = ""
            if length_str.isdigit():
                length = int(length_str)
                ref_source = "DDS 直接定義"
            elif target_ref_name in ref_dict:
                # 成功從上傳的 LAYOUT 參照檔查到真實長度！
                length = ref_dict[target_ref_name]['length']
                if not data_type:
                    data_type = ref_dict[target_ref_name]['data_type']
                if not dec_pos:
                    dec_pos = ref_dict[target_ref_name]['dec_pos']
                ref_source = f"LAYOUT ({target_ref_name})"
            else:
                edtwrd_match = re.search(r"EDTWRD\('([^']+)'\)", text_part)
                if edtwrd_match:
                    length = len(edtwrd_match.group(1).replace('/', '').replace(':', '').replace('-', ''))
                    ref_source = "EDTWRD 推算"
                elif 'VALUES(' in text_part.upper():
                    val_match = re.search(r"VALUES\(\s*'([^']+)'", text_part, re.IGNORECASE)
                    length = len(val_match.group(1)) if val_match else 1
                    ref_source = "VALUES 推算"
                else:
                    length = default_ref_len
                    ref_source = f"預設值 (未找到 {target_ref_name})"

            if usage not in ['I', 'O', 'B', 'H', 'M']:
                usage = 'O'

            edtwrd_match = re.search(r"EDTWRD\('([^']+)'\)", text_part)
            disp_len = len(edtwrd_match.group(1)) if edtwrd_match else length

            type_display = f"{data_type} ({dec_pos})" if dec_pos else (data_type or 'A')

            records[current_record].append({
                'type': 'field', 'pos_mode': pos_mode, 'raw_row': raw_row, 'raw_col': raw_col,
                'text': '', 'length': length, 'disp_len': disp_len,
                'usage': usage, 'name': field_name, 'ref': is_ref,
                'ref_target': target_ref_name if is_ref else '',
                'ref_source': ref_source,
                'data_type': type_display, 'keywords': text_part
            })

    return records

def calculate_positions(elements, custom_lengths):
    """根據欄位長度動態計算所有 +n 元素的實際座標"""
    last_row = 1
    last_end_col = 1
    resolved = []

    for idx, el in enumerate(elements):
        item = el.copy()
        if item['type'] == 'field' and idx in custom_lengths:
            new_len = custom_lengths[idx]
            item['length'] = new_len
            item['disp_len'] = new_len

        mode = item['pos_mode']
        if mode == 'HIDDEN':
            item['row'] = 0
            item['col'] = 0
        elif mode == 'ABS':
            item['row'] = item['raw_row']
            item['col'] = item['raw_col']
        elif mode == 'REL':
            item['row'] = item['raw_row'] if item['raw_row'] is not None else last_row
            item['col'] = last_end_col + item['raw_col']

        if mode in ('ABS', 'REL'):
            last_row = item['row']
            if item['type'] == 'constant':
                dbcs_pad = 2 if has_dbcs(item['text']) else 0
                last_end_col = item['col'] + item['disp_len'] + dbcs_pad
            else:
                last_end_col = item['col'] + item['disp_len']

        resolved.append(item)
    return resolved

def render_screen(resolved_elements):
    """繪製 24x80 綠幕畫布"""
    screen_buffer = [[' ' for _ in range(80)] for _ in range(24)]
    
    for el in resolved_elements:
        r = el['row'] - 1
        c = el['col'] - 1
        
        if 0 <= r < 24 and 0 <= c < 80:
            if el['type'] == 'constant':
                content = el['text']
            else:
                d_len = el['disp_len']
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
                        screen_buffer[r][curr_c + 1] = None
                    curr_c += 2
                else:
                    screen_buffer[r][curr_c] = ch
                    curr_c += 1
                    
    lines = ["".join(cell for cell in row if cell is not None) for row in screen_buffer]
    return "\n".join(lines)

def decode_bytes(raw_bytes, selected_enc):
    try:
        return raw_bytes.decode(selected_enc)
    except UnicodeDecodeError:
        try:
            return raw_bytes.decode("cp950")
        except UnicodeDecodeError:
            return raw_bytes.decode("utf-8", errors="replace")

# --- 網頁介面開始 ---
st.set_page_config(page_title="AS400 綠幕模擬器 (支援 LAYOUT)", layout="wide")
st.title("📟 AS400 DSPF 綠幕預覽器 (支援 LAYOUT 參照檔 & 動態調整)")

col1, col2 = st.columns([2, 1])
with col1:
    encoding_option = st.selectbox(
        "1. 選擇檔案編碼 (若中文出現亂碼請切換)：",
        ["UTF-8 (現代標準)", "Big5 / CP950 (繁體中文)", "Shift_JIS (日文)", "GBK (簡體中文)"]
    )
with col2:
    default_ref_len = st.number_input("未找到參照時的預設長度 (R)：", min_value=1, max_value=30, value=6)

encoding_map = {
    "UTF-8 (現代標準)": "utf-8",
    "Big5 / CP950 (繁體中文)": "cp950",
    "Shift_JIS (日文)": "shift_jis",
    "GBK (簡體中文)": "gbk"
}
selected_enc = encoding_map[encoding_option]

u_col1, u_col2 = st.columns(2)
with u_col1:
    uploaded_file = st.file_uploader("2. 上傳 DSPF 畫面檔 (必填)", type=["txt", "dds", "dspf"])
with u_col2:
    uploaded_refs = st.file_uploader(
        "3. 上傳參照檔如 LAYOUT.txt (選填，支援無 'A' 前綴之 PF 規格檔)",
        type=["txt", "dds", "pf"], accept_multiple_files=True
    )

if uploaded_file is not None:
    ref_contents = [decode_bytes(f.getvalue(), selected_enc) for f in uploaded_refs] if uploaded_refs else []
    ref_dict = parse_ref_files(ref_contents)
    if ref_dict:
        st.success(f"✅ 已從 LAYOUT 參照檔成功載入 {len(ref_dict)} 個欄位定義！(例如：PAT01={ref_dict.get('PAT01', {}).get('length', '?')}, TTR02={ref_dict.get('TTR02', {}).get('length', '?')}, FUA01={ref_dict.get('FUA01', {}).get('length', '?')})")
    elif uploaded_refs:
        st.warning("⚠️ 已上傳參照檔，但未解析出任何欄位，請確認檔案內容。")

    string_data = decode_bytes(uploaded_file.getvalue(), selected_enc)
    records_dict = parse_dds_by_record(string_data, ref_dict, default_ref_len=default_ref_len)
    
    if not records_dict:
        st.error("無法解析此檔案，請確認是否為標準的 AS400 DDS 原始碼。")
    else:
        record_names = list(records_dict.keys())
        selected_records = st.multiselect(
            "4. 請選擇要預覽的畫面格式 (可複選疊加，例如同時選 S1 與 SCMD)：",
            record_names,
            default=[record_names[0]]
        )
        
        if selected_records:
            combined_elements = []
            for rec in selected_records:
                combined_elements.extend(records_dict[rec])
                
            initial_resolved = calculate_positions(combined_elements, {})
            
            st.subheader("📋 變數欄位規格清單（💡 可直接在表格內點擊修改「顯示長度」，上方綠幕會即時重算！）")
            
            field_rows = []
            field_Element_Indices = []
            for idx, el in enumerate(initial_resolved):
                if el['type'] == 'field':
                    usage_desc = {'I': '輸入 (I)', 'O': '輸出 (O)', 'B': '雙向 (B)', 'H': '隱藏 (H)'}.get(el['usage'], el['usage'])
                    field_Element_Indices.append(idx)
                    field_rows.append({
                        "欄位名稱": el['name'],
                        "行號 (Row)": el['row'] if el['row'] > 0 else 0,
                        "列號 (Col)": el['col'] if el['col'] > 0 else 0,
                        "資料長度": el['length'],
                        "顯示長度 (可點擊修改)": el['disp_len'],
                        "長度來源": el['ref_source'],
                        "型態(小數)": el.get('data_type', ''),
                        "屬性": usage_desc,
                        "關鍵字 / REFFLD": el.get('keywords', '')
                    })

            custom_lengths = {}
            if field_rows:
                df = pd.DataFrame(field_rows)
                edited_df = st.data_editor(
                    df,
                    disabled=["欄位名稱", "行號 (Row)", "列號 (Col)", "資料長度", "長度來源", "型態(小數)", "屬性", "關鍵字 / REFFLD"],
                    use_container_width=True,
                    key=f"editor_{'_'.join(selected_records)}"
                )
                for row_i, el_idx in enumerate(field_Element_Indices):
                    new_val = int(edited_df.loc[row_i, "顯示長度 (可點擊修改)"])
                    if new_val != initial_resolved[el_idx]['disp_len']:
                        custom_lengths[el_idx] = new_val
            else:
                st.info("此畫面沒有任何變數欄位。")

            final_resolved = calculate_positions(combined_elements, custom_lengths) if custom_lengths else initial_resolved
            screen_text = render_screen(final_resolved)
            safe_screen_text = html.escape(screen_text)
            
            st.subheader(f"🖥️ 畫面預覽：{' + '.join(selected_records)}")
            html_block = f"""
            <div style="background-color: #000000; padding: 20px; border-radius: 10px; border: 2px solid #333; overflow-x: auto; display: inline-block; min-width: 95%;">
                <pre style="color: #00FF00; font-family: 'MS Gothic', 'MingLiU', 'DFKai-SB', 'Consolas', 'Courier New', monospace; font-size: 16px; font-weight: bold; line-height: 1.25; margin: 0; letter-spacing: 0px;">{safe_screen_text}</pre></div>
            """
            components.html(html_block, height=520,
