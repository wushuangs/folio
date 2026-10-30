import re
import statistics
from collections import Counter

import pdfplumber

PARSER_VERSION = 'layout-v2'
HEADING = re.compile(r'^(\d{1,2}(?:\.\d+){0,3})\s+(.{2,55})$')
TOC = re.compile(r'^(\d{1,2}(?:\.\d+){0,3})\s+(.+?)\s*[.．…·]{3,}\s*(\d+)\s*$')
PAGE_NUMBER = re.compile(r'第\s*\d+\s*页\s*共\s*\d+\s*页|^\s*\d+\s*/\s*\d+\s*$')


def split_page(text: str, limit=380, overlap=50):
    clean = '\n'.join(re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines() if line.strip())
    result, start = [], 0
    while start < len(clean):
        end = min(start + limit, len(clean))
        if end < len(clean):
            boundary = max(clean.rfind('\n', start + limit//2, end), clean.rfind('。', start+limit//2, end))
            if boundary > start:
                end = boundary + 1
        if clean[start:end].strip():
            result.append(clean[start:end].strip())
        if end == len(clean):
            break
        start = max(start+1, end-overlap)
    return result


def _clean(value):
    return re.sub(r'[ \t]+', ' ', value or '').strip()


def table_records(table):
    """保留几何列位置；空值不前移，纵向合并的主键可以向下继承。"""
    rows = table.extract()
    if len(rows) < 2:
        return []
    first = rows[0]
    header_cells = table.rows[0].cells
    headers = [(i, _clean(value), (header_cells[i][0]+header_cells[i][2])/2) for i,value in enumerate(first) if _clean(value) and header_cells[i]]
    if len(headers) < 2 or any(re.fullmatch(r'[\d.()%-]+',h[1]) for h in headers):
        # 没有可信表头时按原行/列顺序保留，不发明字段归属。
        return [' | '.join(_clean(c) for c in row) for row in rows if any(_clean(c) for c in row)]
    result, previous_cells, section = [], {}, ''
    for raw, geometry in zip(rows[1:],table.rows[1:]):
        cells = [(i,_clean(v),geometry.cells[i]) for i,v in enumerate(raw) if _clean(v) and geometry.cells[i]]
        if not cells:
            continue
        if len(cells)==1 and cells[0][2][0] <= table.bbox[0]+3 and cells[0][2][2] >= table.bbox[2]-3:
            section = cells[0][1]
            previous_cells = {}
            continue
        values = [''] * len(headers)
        for _,value,rect in cells:
            center = (rect[0]+rect[2])/2
            target = min(range(len(headers)),key=lambda j:abs(headers[j][2]-center))
            values[target] = (values[target]+' '+value).strip()
            previous_cells[target] = (value,rect[3])
        row_top=min(cell[1] for cell in geometry.cells if cell)
        for j in range(len(headers)):
            if not values[j] and j in previous_cells and previous_cells[j][1] > row_top+1:
                values[j]=previous_cells[j][0]
        pairs = [f'{h[1]}：{value}' for h,value in zip(headers,values) if value]
        if pairs:
            result.append((f'分组：{section}；' if section else '') + '；'.join(pairs))
    return result


def parse_pdf(path, progress=None):
    chunks = []
    try:
        pdf = pdfplumber.open(path)
    except Exception as exc:
        raise ValueError('PDF 无法打开，可能已加密或损坏；请先解除密码保护。') from exc
    with pdf:
        total = len(pdf.pages)
        if total > 1500:
            raise ValueError('当前版本最多支持 1500 页 PDF。')
        if not total:
            raise ValueError('PDF 没有可解析的页面。')
        known, toc_pages, edges = {}, set(), Counter()
        for pno,page in enumerate(pdf.pages[:min(5,total)]):
            matches = []
            for line in (page.extract_text() or '').splitlines():
                m = TOC.match(line.strip())
                if m:
                    known[m.group(1)] = re.sub(r'\s+','',m.group(2))
                    matches.append(m)
            if len(matches) >= 3:
                toc_pages.add(pno)
        for index,page in enumerate(pdf.pages):
            if progress:
                progress(index+1,total*2)
            lines = (page.extract_text() or '').splitlines()
            edges.update(set(_clean(x) for x in lines[:2]+lines[-2:] if x.strip()))
            page.flush_cache()
        repeated = {line for line,count in edges.items() if count >= max(6,total*.5) and len(line)<120}
        heading = '文档正文'
        for pno,page in enumerate(pdf.pages):
            if progress:
                progress(total+pno+1,total*2)
            if pno in toc_pages:
                page.flush_cache()
                continue
            all_lines = page.extract_text_lines() or []
            tables = page.find_tables()
            events = []
            for line in all_lines:
                value = _clean(line['text'])
                if not value or PAGE_NUMBER.search(value) or value in repeated:
                    continue
                center_y = (line['top']+line['bottom'])/2
                center_x = (line['x0']+line['x1'])/2
                if any(t.bbox[0] <= center_x <= t.bbox[2] and t.bbox[1] <= center_y <= t.bbox[3] for t in tables):
                    continue
                events.append((line['top'],'line',value))
            for index,table in enumerate(tables):
                caption = ''
                preceding = [line for line in all_lines if line['bottom'] <= table.bbox[1]+1 and re.match(r'^表\s*\d+',line['text'])]
                if preceding:
                    caption = _clean(max(preceding,key=lambda line:line['bottom'])['text'])
                events.append((table.bbox[1],'table',(index,caption,table)))
            events.sort(key=lambda row:row[0])
            buf = []
            def add(content,kind='body',context='',group_key=''):
                if content.strip():
                    chunks.append({'page':pno+1,'ordinal':len(chunks),'heading':heading,'content':content.strip(),'kind':kind,'context':context,'group_key':group_key})
            def flush():
                if not buf:
                    return
                parts = split_page('\n'.join(buf))
                for index,part in enumerate(parts):
                    context = '\n'.join(parts[max(0,index-1):index+2])[:1400]
                    add(part,context=context,group_key=f'p{pno+1}-body{len(chunks)}')
                buf.clear()
            for _,kind,payload in events:
                if kind == 'line':
                    m = HEADING.match(payload)
                    is_heading = m and ((m.group(1) in known and re.sub(r'\s+','',m.group(2)) == known[m.group(1)]) or (not known and len(payload)<35 and not re.search(r'[。；：=]|mm|\d+[VAW]',m.group(2))))
                    if is_heading:
                        flush()
                        heading = payload
                    elif not re.fullmatch(r'[_.．…\s]+',payload):
                        buf.append(payload)
                else:
                    flush()
                    index,caption,table = payload
                    rows = table_records(table)
                    for rindex,row in enumerate(rows):
                        prefix = f'{heading}\n{caption}\n'.strip()
                        siblings = '\n'.join(rows[max(0,rindex-1):rindex+2])
                        for part in split_page(row,limit=440,overlap=40):
                            add(f'{caption}\n{part}'.strip(),'table_row',f'{prefix}\n{siblings}'[:1600],f'p{pno+1}-t{index}-g{rindex//3}')
            flush()
            page.flush_cache()
    if sum(len(c['content']) for c in chunks)<100:
        raise ValueError('未提取到足够文字。扫描件请先 OCR 后上传。')
    return total,chunks
