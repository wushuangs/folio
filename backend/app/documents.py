import csv
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageOps

from .ocr import recognize
from .parsing import parse_pdf, split_page

FORMATS = {'.pdf', '.txt', '.md', '.docx', '.csv', '.xlsx', '.png', '.jpg', '.jpeg', '.webp'}
MAX_EXPANDED = 100 * 1024 * 1024


def source_path(document):
    from .config import UPLOADS
    return UPLOADS / f'{document.id}.{document.file_type}'


def checked_archive(path):
    archive = zipfile.ZipFile(path)
    if sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED or len(archive.infolist()) > 10000:
        archive.close()
        raise ValueError('压缩文档解压体积或文件数量超限。')
    return archive


def decode_text(data):
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('文本编码无法识别，请转换为 UTF-8。')


def validate_file(path, suffix):
    try:
        if suffix == '.pdf':
            if b'%PDF-' not in path.read_bytes()[:1024]:
                raise ValueError('文件不是有效的 PDF。')
        elif suffix in {'.docx', '.xlsx'}:
            with checked_archive(path) as archive:
                required = 'word/document.xml' if suffix == '.docx' else 'xl/workbook.xml'
                if required not in archive.namelist():
                    raise ValueError('文件内容与扩展名不符。')
        elif suffix in {'.png', '.jpg', '.jpeg', '.webp'}:
            with Image.open(path) as image:
                if image.width * image.height > 40_000_000:
                    raise ValueError('图片超过 4000 万像素。')
                image.verify()
        elif '\x00' in decode_text(path.read_bytes()):
            raise ValueError('文本文件包含无效二进制内容。')
    except (zipfile.BadZipFile, ET.ParseError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('文档损坏或格式不正确。') from exc


def _sections(path, suffix):
    if suffix in {'.txt', '.md'}:
        value = decode_text(path.read_bytes())
        return [(f'正文 · 第 {i+1} 节', value[start:start+12000]) for i,start in enumerate(range(0,len(value),12000))]
    if suffix == '.csv':
        value = decode_text(path.read_bytes())
        try:
            dialect = csv.Sniffer().sniff(value[:4096], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        rows=[]
        for row in csv.reader(io.StringIO(value), dialect):
            if len(rows)>=100000:raise ValueError('CSV 超过 10 万行，请拆分后上传。')
            if len(row)>512:raise ValueError('CSV 超过 512 列，请拆分后上传。')
            rows.append(row)
        return _row_sections(rows, '表格')
    if suffix in {'.png', '.jpg', '.jpeg', '.webp'}:
        with Image.open(path) as image:
            return [('图片 OCR', recognize(ImageOps.exif_transpose(image)))]
    with checked_archive(path) as archive:
        if suffix == '.docx':
            root = ET.fromstring(archive.read('word/document.xml'))
            ns = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            lines = []
            body=root.find('w:body', ns)
            if body is None:raise ValueError('Word 文档缺少正文。')
            for child in body:
                if child.tag.endswith('}tbl'):
                    for row in child.findall('w:tr', ns):
                        lines.append(' | '.join(''.join(node.text or '' for node in cell.findall('.//w:t',ns)) for cell in row.findall('w:tc',ns)))
                else:
                    lines.append(''.join(node.text or '' for node in child.findall('.//w:t',ns)))
            text = '\n'.join(lines)
            return [(f'Word · 第 {i+1} 节',text[start:start+12000]) for i,start in enumerate(range(0,len(text),12000))]
        ns = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        shared=[]
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared=[''.join(x.itertext()) for x in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        relations = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        paths={x.attrib['Id']:x.attrib['Target'] for x in relations}
        result=[]
        for sheet in workbook.findall('s:sheets/s:sheet',ns):
            rid=sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target=paths.get(rid,'')
            member=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            if '..' in Path(member).parts:
                raise ValueError('不支持的工作表路径。')
            rows=[]
            for row in ET.fromstring(archive.read(member)).findall('.//s:sheetData/s:row',ns):
                cells=[]
                for cell in row.findall('s:c',ns):
                    letters=re.sub('[^A-Z]','',cell.attrib.get('r','A'))
                    index=0
                    for letter in letters:
                        index=index*26+ord(letter)-64
                    if index>512:
                        raise ValueError('工作表列数超过 512，请按业务范围拆分。')
                    while len(cells)<max(1,index):cells.append('')
                    value=cell.findtext('s:v','',ns)
                    if cell.attrib.get('t')=='s':value=shared[int(value)]
                    elif cell.attrib.get('t')=='inlineStr':value=''.join(cell.find('s:is',ns).itertext())
                    cells[max(0,index-1)]=value
                rows.append(cells)
                if len(rows)>100000:raise ValueError('工作表超过 10 万行，请拆分后上传。')
            result.extend(_row_sections(rows,sheet.attrib['name']))
        return result


def _row_sections(rows, title):
    if not rows:return []
    header=rows[0]
    lines=[' | '.join(header)]
    for row in rows[1:]:
        if any(row):
            lines.append('；'.join(f'{header[i] if i<len(header) and header[i] else f"列{i+1}"}：{value}' for i,value in enumerate(row) if value))
    return [(f'{title} · 行 {start+1}–{min(start+100,len(lines))}', '\n'.join(lines[start:start+100])) for start in range(0,len(lines),100)]


def quality_report(pages, chunks, file_type, ocr_pages=None):
    lengths={page:0 for page in range(1,pages+1)}
    for chunk in chunks:lengths[chunk['page']]=lengths.get(chunk['page'],0)+len(chunk['content'])
    empty=[page for page,size in lengths.items() if size==0]
    sparse=[page for page,size in lengths.items() if 0<size<40]
    garbled=[page for page in lengths if any(c['content'].count('\ufffd')/max(1,len(c['content']))>.01 for c in chunks if c['page']==page)]
    warnings=[]
    if empty:warnings.append(f'{len(empty)} 个位置未提取到文字（可能为空白页、目录或未识别扫描页）')
    if sparse:warnings.append(f'{len(sparse)} 个位置文字较少，请核对完整性')
    if garbled:warnings.append('检测到疑似乱码，请核对原文件编码')
    if ocr_pages:warnings.append('包含 OCR 识别内容，请重点核对数字、单位与表格')
    return {'status':'warning' if warnings else 'passed','file_type':file_type,'pages':pages,'chunks':len(chunks),'characters':sum(lengths.values()),'empty_pages':empty,'sparse_pages':sparse,'garbled_pages':garbled,'ocr_pages':ocr_pages or [],'warnings':warnings,'page_characters':lengths,'note':'规则检查只能发现异常，不能证明解析内容完整或正确。'}


def parse_document(path, file_type, progress=None):
    ocr_pages=[]
    if file_type=='pdf':
        pages,chunks=parse_pdf(path,progress,ocr_pages=ocr_pages)
    else:
        try:
            sections=_sections(path,'.'+file_type)
        except (ET.ParseError,KeyError,IndexError,zipfile.BadZipFile) as exc:
            raise ValueError('文档结构无法解析，请检查文件完整性。') from exc
        pages=len(sections)
        chunks=[]
        for page,(heading,value) in enumerate(sections,1):
            for part in split_page(value):
                chunks.append({'page':page,'ordinal':len(chunks),'heading':heading,'content':part,'kind':'body','context':part,'group_key':f'p{page}-{len(chunks)}'})
            if progress:progress(page,pages)
        if file_type in {'png','jpg','jpeg','webp'}:ocr_pages=[1]
        if sum(len(c['content']) for c in chunks)<10:raise ValueError('未识别到足够文字，请核对文件或图片清晰度。')
    report=quality_report(pages,chunks,file_type,ocr_pages)
    if file_type=='xlsx':
        report['warnings'].append('Excel 公式不在服务端计算，仅使用文件中已缓存的结果；日期可能保留为原始序列值。')
        report['status']='warning'
    if file_type=='docx':
        report['warnings'].append('Word 按逻辑节定位，不对应排版页码；嵌入图片和文本框可能未提取，请核对。')
        report['status']='warning'
    return pages,chunks,report
