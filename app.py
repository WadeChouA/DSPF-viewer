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
    """偵測 AS400 原始碼是否有 12-byte 序號前綴"""
    if len(line) > 17 and line[17].upper() == 'A' and (len(line) <= 18 or line[18] in (' ', '*', 'R', 'O', 'N')):
        return 12
    elif len(line) > 5 and line[5].upper() == 'A':
        return 0
    return None

def parse_ref_files(ref_contents):
    """解析上傳的外部參照檔 (如 LAYOUT PF)，建立 {欄位名稱: (長度, 型態, 小數位)} 字典"""
    ref_dict = {}
    for content in ref_contents:
        for line in content.splitlines():
            if not line.strip():
                continue
            offset = detect_offset(line)
            if offset is None or (len(line) > offset + 6 and line[offset + 6] == '*'):
                continue
            if len(line) < offset + 35:
                continue
            name_type = line[offset + 16 : offset + 17].upper()
            if name_type == 'R':
                continue
            field_name = line[offset + 18 : offset + 28].strip().upper()
            length_str = line[offset + 29 : offset + 34].strip()
            data_type = line[offset + 34 : offset + 35].strip().upper()
            dec_pos = line[offset + 35 : offset + 37].strip() if len(line) >= offset + 37 else ''
            
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
        usage = line[offset + 37 : offset + 38].strip().upper()
        row_str = line[offset + 38 : offset + 41].strip()
        col_str = line[offset + 41 : offset + 44].strip()
        text_part = line[offset + 44:].strip()

        # 記錄座標規則 (保留 +n 資訊，以便使用者在表格修改長度時能動態重算位置)
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
                ref_source = f"參照檔 ({target_ref_name})"
            else:
                # 若未上傳參照檔，嘗試從 EDTWRD 或 VALUES 智慧推算
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

            records[current_record].append({
                'type': 'field', 'pos_mode': pos_mode, 'raw_row': raw_row, 'raw_col': raw_col,
                'text': '', 'length': length, 'disp_len': disp_len,
                'usage': usage, 'name': field_name, 'ref': is_ref,
                'ref_target': target_ref_name if is_ref else '',
                'ref_source': ref_source,
                'data_type': data_type, 'keywords': text_part
            })

    return records

def calculate_positions(elements, custom_lengths):
    """根據欄位長度（包含使用者在表格手動修改後的長度），動態計算所有 +n 元素的實際座標"""
    last_row = 1
    last_end_col = 1
    resolved = []

    for idx, el in enumerate(elements):
        item = el.copy()
        # 若使用者在下方表格手動修改了該欄位的顯示長度，優先套用
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
st.set_page_config(page_title="AS400 綠幕模擬器 (支援 REFFLD)", layout="wide")
st.title("📟 AS400 DSPF 綠幕預覽器 (支援 REFFLD 參照檔 & 動態調整)")

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
        "3. 上傳參照檔如 LAYOUT.txt (選填，可上傳多個 PF 以自動解析 REFFLD 長度)",
        type=["txt", "dds", "pf"], accept_multiple_files=True
    )

if uploaded_file is not None:
    # 先解析所有上傳的參照檔 (LAYOUT)
    ref_contents = [decode_bytes(f.getvalue(), selected_enc) for f in uploaded_refs] if uploaded_refs else []
    ref_dict = parse_ref_files(ref_contents)
    if ref_dict:
        st.success(f"✅ 已從參照檔成功載入 {len(ref_dict)} 個外部欄位定義！")

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
                
            # 先進行第一次座標計算，準備產生可編輯表格
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
                        "顯示長度 (可點擊修改)": el['disp_len'],
                        "長度來源": el['ref_source'],
                        "型態": el.get('data_type', '') or ('REF' if el.get('ref') else ''),
                        "屬性": usage_desc,
                        "關鍵字 / REFFLD": el.get('keywords', '')
                    })

            custom_lengths = {}
            if field_rows:
                df = pd.DataFrame(field_rows)
                edited_df = st.data_editor(
                    df,
                    disabled=["欄位名稱", "行號 (Row)", "列號 (Col)", "長度來源", "型態", "屬性", "關鍵字 / REFFLD"],
                    use_container_width=True,
                    key=f"editor_{'_'.join(selected_records)}"
                )
                # 讀取使用者修改後的長度
                for row_i, el_idx in enumerate(field_Element_Indices):
                    new_val = int(edited_df.loc[row_i, "顯示長度 (可點擊修改)"])
                    if new_val != initial_resolved[el_idx]['disp_len']:
                        custom_lengths[el_idx] = new_val
            else:
                st.info("此畫面沒有任何變數欄位。")

            # 若使用者有在表格內修改長度，重新計算所有 +n 欄位座標並渲染綠幕
            final_resolved = calculate_positions(combined_elements, custom_lengths) if custom_lengths else initial_resolved
            screen_text = render_screen(final_resolved)
            safe_screen_text = html.escape(screen_text)
            
            st.subheader(f"🖥️ 畫面預覽：{' + '.join(selected_records)}")
            html_block = f"""
            <div style="background-color: #000000; padding: 20px; border-radius: 10px; border: 2px solid #333; overflow-x: auto; display: inline-block; min-width: 95%;">
                <pre style="color: #00FF00; font-family: 'MS Gothic', 'MingLiU', 'DFKai-SB', 'Consolas', 'Courier New', monospace; font-size: 16px; font-weight: bold; line-height: 1.25; margin: 0; letter-spacing: 0px;">{safe_screen_text}</pre>
            </div>
            """
            components.html(html_block, height=520, scrolling=True)
