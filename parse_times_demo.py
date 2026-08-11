import re
import csv
import json

with open('D:/MySQL表的数据/zjgj_scenic-03.sql', 'r', encoding='utf-8') as f:
    lines = f.readlines()

records = []
for line in lines:
    if not line.strip().startswith('INSERT'):
        continue
    match = re.search(r'VALUES\s*\((.*)\);?\s*$', line)
    if not match:
        continue
    values_str = match.group(1)

    fields = []
    current = ''
    in_quote = False
    escaped = False
    for ch in values_str:
        if escaped:
            current += ch
            escaped = False
            continue
        if ch == '\\':
            current += ch
            escaped = True
            continue
        if ch == "'" and not in_quote:
            in_quote = True
            current += ch
        elif ch == "'" and in_quote:
            current += ch
            in_quote = False
        elif ch == ',' and not in_quote:
            fields.append(current.strip())
            current = ''
        else:
            current += ch
    if current.strip():
        fields.append(current.strip())

    if len(fields) >= 15:
        id_val = fields[0]
        scenic_name = fields[3].strip("'")
        open_time = fields[14].strip("'")
        records.append((id_val, scenic_name, open_time))


def unescape_json(s):
    return s.replace('\\"', '"')


def parse_date(date_str):
    try:
        parts = date_str.split('-')
        return (int(parts[0]), int(parts[1]))
    except:
        return None


def ranges_overlap(start1, end1, start2, end2):
    """Check if two date ranges overlap"""
    doy = lambda m, d: sum([0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][:m]) + d
    s1 = doy(start1[0], start1[1])
    e1 = doy(end1[0], end1[1])
    s2 = doy(start2[0], start2[1])
    e2 = doy(end2[0], end2[1])
    if s1 <= e1:
        r1 = set(range(s1, e1 + 1))
    else:
        r1 = set(range(s1, 367)) | set(range(1, e1 + 1))
    if s2 <= e2:
        r2 = set(range(s2, e2 + 1))
    else:
        r2 = set(range(s2, 367)) | set(range(1, e2 + 1))
    return bool(r1 & r2)


def parse_time_minutes(t_str):
    """Parse 'HH:MM' or 'H:MM' to minutes since midnight"""
    t_str = t_str.strip()
    parts = t_str.split(':')
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return None


def time_ranges_overlap(t1_list, t2_list):
    """Check if any time range in t1_list overlaps with any in t2_list"""
    for t1 in t1_list:
        t1_parts = t1.split('-')
        if len(t1_parts) != 2:
            continue
        s1 = parse_time_minutes(t1_parts[0])
        e1 = parse_time_minutes(t1_parts[1])
        if s1 is None or e1 is None:
            continue
        for t2 in t2_list:
            t2_parts = t2.split('-')
            if len(t2_parts) != 2:
                continue
            s2 = parse_time_minutes(t2_parts[0])
            e2 = parse_time_minutes(t2_parts[1])
            if s2 is None or e2 is None:
                continue
            # Check overlap: two ranges overlap if start1 < end2 AND start2 < end1
            if s1 < e2 and s2 < e1:
                return True
    return False


conflict_text = []
conflict_real_overlap = []  # Real time overlaps
conflict_segmented = []  # Segmented hours (non-overlapping, intentional)
conflict_time_format = []
conflict_json_error = []

for id_val, name, open_time in records:
    if not open_time.startswith('{'):
        conflict_text.append([id_val, name, open_time, '文本类-非JSON格式'])
        continue

    unescaped = unescape_json(open_time)
    try:
        data = json.loads(unescaped)
    except json.JSONDecodeError as e:
        conflict_json_error.append([id_val, name, open_time[:200], f'JSON解析错误: {str(e)}'])
        continue

    periods = data.get('s', [])
    if not periods:
        conflict_json_error.append([id_val, name, open_time[:200], 'JSON解析错误: empty s array'])
        continue

    # Check time format
    has_format_issue = False
    for p in periods:
        for t_str in p.get('t', []):
            if '-' in t_str:
                parts = t_str.split('-')
                for pt in parts:
                    pt = pt.strip()
                    if pt and re.match(r'^\d{1}:\d{2}$', pt):
                        has_format_issue = True
                        break
                if has_format_issue:
                    break
        if has_format_issue:
            break
    if has_format_issue:
        conflict_time_format.append([id_val, name, open_time, '时间格式不规范(缺少前导零)'])

    # Check for multi-period conflicts
    if len(periods) <= 1:
        continue

    has_real_overlap = False
    has_segmented = False
    overlap_details = []

    for i in range(len(periods)):
        for j in range(i + 1, len(periods)):
            p1 = periods[i]
            p2 = periods[j]

            d1_parts = p1.get('d', '').split('~')
            d2_parts = p2.get('d', '').split('~')
            if len(d1_parts) != 2 or len(d2_parts) != 2:
                continue

            s1 = parse_date(d1_parts[0])
            e1 = parse_date(d1_parts[1])
            s2 = parse_date(d2_parts[0])
            e2 = parse_date(d2_parts[1])
            if not all([s1, e1, s2, e2]):
                continue

            if not ranges_overlap(s1, e1, s2, e2):
                continue

            w1 = set(p1.get('w', []))
            w2 = set(p2.get('w', []))
            common_days = w1 & w2
            if not common_days:
                continue

            t1 = p1.get('t', [])
            t2 = p2.get('t', [])
            if t1 == t2:
                continue  # Same times = no conflict

            if time_ranges_overlap(t1, t2):
                has_real_overlap = True
                overlap_details.append(f'd1={p1["d"]} d2={p2["d"]} w={sorted(common_days)} t1={t1} t2={t2}')
            else:
                has_segmented = True

    if has_real_overlap:
        detail_str = '; '.join(overlap_details[:3])  # Max 3 details
        conflict_real_overlap.append([id_val, name, open_time, f'时间真正重叠冲突: {detail_str}'])
    elif has_segmented:
        conflict_segmented.append([id_val, name, open_time, '分时段开放(非重叠)'])

print(f'总记录数: {len(records)}')
print(f'\n=== 冲突分析结果 ===')
print(f'1. 文本类(非JSON格式): {len(conflict_text)} 条')
print(f'2. 时间真正重叠冲突: {len(conflict_real_overlap)} 条')
print(f'3. 分时段开放(非重叠): {len(conflict_segmented)} 条')
print(f'4. 时间格式不规范: {len(conflict_time_format)} 条')
print(f'5. JSON解析错误: {len(conflict_json_error)} 条')

# Combine all results
all_conflicts = conflict_text + conflict_real_overlap + conflict_segmented + conflict_time_format + conflict_json_error

output_path = 'D:/MySQL表的数据/scenic_time_conflicts_03.csv'
with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(['id', 'scenic_name', 'scenic_open_time', 'conflict_type'])
    writer.writerows(all_conflicts)

print(f'\n总计输出: {len(all_conflicts)} 条')
print(f'已保存到: {output_path}')

# Examples of real overlaps
print(f'\n=== 真正重叠冲突示例(前15条) ===')
for item in conflict_real_overlap[:15]:
    print(f'  ID={item[0]}, Name={item[1]}')
    try:
        data = json.loads(unescape_json(item[2]))
        for p in data.get('s', []):
            print(f'    d={p["d"]}, w={p["w"]}, t={p["t"]}')
    except:
        print(f'    Raw: {item[2][:100]}')

# Summary of text types
print(f'\n=== 文本类分布(前10) ===')
text_types = {}
for item in conflict_text:
    txt = item[2]
    text_types[txt] = text_types.get(txt, 0) + 1
for txt, cnt in sorted(text_types.items(), key=lambda x: -x[1])[:10]:
    print(f'  [{txt}] -> {cnt} 条')
