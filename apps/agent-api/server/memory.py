"""Local, source-linked engineering examples. Retrieval is deliberately transparent."""
import json
from pathlib import Path

import openpyxl

from .db import ROOT, connect, now

STANDARDS = [
    ('ISO 128-1:2020', '技术产品文件：表达的一般原则与基本要求',
     'https://www.iso.org/standard/65296.html'),
    ('ISO 128-2:2022', '技术产品文件：线型、指引线和参考线基本规则',
     'https://www.iso.org/standard/83355.html'),
    ('ISO 128-3:2022', '技术产品文件：视图、剖视和断面规则',
     'https://www.iso.org/standard/83356.html'),
    ('ISO 129-1:2018+Amd 1:2020', '技术产品文件：尺寸和公差表示的一般原则',
     'https://www.iso.org/standard/64007.html'),
    ('ISO 5455:1979', '技术制图：推荐比例及其标识',
     'https://www.iso.org/standard/11500.html'),
    ('ISO 5456-2:1996', '技术制图：正投影表示法',
     'https://www.iso.org/standard/11502.html'),
    ('ISO 5457:1999+Amd 1:2010', '技术产品文件：图纸幅面和布局',
     'https://www.iso.org/standard/29017.html'),
    ('ISO 7200:2004', '技术产品文件：标题栏和文档页眉数据字段',
     'https://www.iso.org/standard/35446.html'),
    ('GB/T 1804-2000', '一般公差：未注公差的线性和角度尺寸',
     'https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=1BF8CFBC644315488F68433EEC2F9D58'),
    ('GB/T 1184-1996', '形状和位置公差：未注公差值',
     'https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=3BB9267B84AF0E621CCF04C5A055280D'),
]


def process_steps_from_xlsx(path):
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        steps = []
        for values in workbook.active.values:
            if not values or len(values) < 3:
                continue
            number = values[0]
            if isinstance(number, str) and number.strip().isdigit():
                number = int(number.strip())
            if not isinstance(number, int) or number <= 0:
                continue
            operation = str(values[1] or '').strip()
            description = str(values[2] or '').strip()
            if operation:
                steps.append({'seq': number, 'operation': operation[:100], 'description': description[:3000]})
            if len(steps) >= 200:
                break
        return steps
    finally:
        workbook.close()


def seed():
    with connect() as con:
        for code, title, url in STANDARDS:
            content = {'code': code, 'scope': title, 'url': url,
                       'rule': ('ISO 128-1:2020 是本系统机械制图总体表达主规范；其他标准按适用范围约束线型、'
                                '视图、尺寸、比例、投影、图幅和标题栏。标准库只保存引用及系统实现规则；'
                                '公差等级和制造数值必须来自原图、受控标准条文或用户确认，缺失时列为待复核。')}
            con.execute('INSERT OR REPLACE INTO engineering_memory VALUES (?,?,?,?,?,?,?)',
                        (code, 'standard', title, json.dumps(content, ensure_ascii=False), url, '', now()))
        folder = ROOT / 'sample' / '示例' / '输出' / '拆解的部件图'
        if folder.is_dir():
            for path in folder.glob('*.pdf'):
                name = path.stem.replace('收卷轴-', '')
                content = {'part_name': name, 'part_type': ('轴' if '轴' in name else '辊筒' if '辊筒' in name else '闷板'),
                           'note': '用户提供的历史零部件图，图中尺寸须由视觉模型读取。'}
                con.execute('INSERT OR REPLACE INTO engineering_memory VALUES (?,?,?,?,?,?,?)',
                            ('sample-drawing-' + name, 'drawing_example', name,
                             json.dumps(content, ensure_ascii=False), '用户附件：示例.rar', str(path), now()))
        card = ROOT / 'sample' / '示例' / '输出' / '收卷轴工艺流程单.xlsx'
        if card.is_file():
            steps = process_steps_from_xlsx(card)
            con.execute('INSERT OR REPLACE INTO engineering_memory VALUES (?,?,?,?,?,?,?)',
                        ('sample-process-reel', 'process_example', '收卷轴工艺流程单',
                         json.dumps({'steps': steps}, ensure_ascii=False), '用户附件：示例.rar', str(card), now()))


def retrieve(name, category=None, limit=4):
    with connect() as con:
        rows = [dict(r) for r in con.execute('SELECT * FROM engineering_memory')]
    if category:
        rows = [r for r in rows if r['category'] == category]
    def score(r):
        title = r['title']
        if title == name:
            return 100
        return sum(2 for char in set(name) if char in title and char not in '零件部件装配') + (3 if r['category'] == 'standard' else 0)
    rows.sort(key=score, reverse=True)
    return [{**r, 'content': json.loads(r['content'])} for r in rows[:limit] if score(r) > 0]
