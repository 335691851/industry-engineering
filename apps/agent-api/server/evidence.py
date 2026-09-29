"""Local PDF evidence extraction. OCR is evidence, never dimensional truth."""
import base64
import hashlib
import json
import os
import threading
import re
from io import BytesIO
from pathlib import Path

import pymupdf

_lock = threading.Lock()
_ocr = None
VERSION = 'evidence-2'


def unrotate_box(box, width, height, rotation, scale):
    """Map clockwise-rotated raster coordinates back to unrotated PDF points."""
    points = [(y, height-x) if rotation == 90 else (width-x, height-y) if rotation == 180
              else (width-y, x) if rotation == 270 else (x,y) for x,y in box]
    return [min(x for x,y in points)/scale, min(y for x,y in points)/scale,
            max(x for x,y in points)/scale, max(y for x,y in points)/scale]


def needs_ocr(page, words):
    text = ''.join(str(w[4]) for w in words)
    damaged = text.count('\ufffd') / max(1, len(text)) > .02
    image_area = sum(abs(pymupdf.Rect(i['bbox']).get_area()) for i in page.get_image_info())
    return len(words) < 40 or damaged or image_area > page.rect.get_area()*.25


def run_ocr(pix, scale):
    from PIL import Image
    global _ocr
    tokens = []
    with _lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        original = Image.open(BytesIO(pix.tobytes('png'))).convert('RGB')
        for rotation in (0, 90):
            raster = original if rotation == 0 else original.transpose(Image.Transpose.ROTATE_270)
            buffer = BytesIO()
            raster.save(buffer, format='PNG')
            result, _ = _ocr(buffer.getvalue())
            for box, text, confidence in result or []:
                bbox = unrotate_box(box, original.width, original.height, rotation, scale)
                # The rotated pass supplements vertical dimensions. Retain different
                # readings in the same region as alternatives, never silently choose.
                if any(t['text'] == text and all(abs(a-b)<3 for a,b in zip(t['bbox'],bbox)) for t in tokens):
                    continue
                tokens.append({'text':text, 'bbox':bbox, 'method':'rapidocr',
                               'rotation':rotation, 'confidence':float(confidence)})
    return tokens


def detail_regions(page_info, limit=3):
    candidates = []
    for token in page_info['tokens']:
        box = token['bbox']; text = token['text']
        if not re.search(r'\d', text):
            continue
        w, h = box[2]-box[0], box[3]-box[1]
        score = 3*bool(re.search(r'[±φΦØ⌀]|[+]\d|[Hh][67]', text)) + 2*(h>w*1.4)
        score += int(token.get('confidence') is not None and token['confidence'] < .9)
        candidates.append((score, token))
    selected = []
    for _, token in sorted(candidates, key=lambda item:item[0], reverse=True):
        x0,y0,x1,y1 = token['bbox']; cx,cy=(x0+x1)/2,(y0+y1)/2
        if any(abs(cx-(b[0]+b[2])/2)<50 and abs(cy-(b[1]+b[3])/2)<50 for _,b in selected):
            continue
        selected.append((token['id'], [max(0,x0-40),max(0,y0-30),
                                      min(page_info['width'],x1+40),min(page_info['height'],y1+30)]))
        if len(selected) == limit:
            break
    return selected


from .native_rpc import native


@native('pdf_page')
def extract_page(path, index, mode='auto'):
    with pymupdf.open(path) as doc:
        page=doc[index]
        words=page.get_text('words',sort=True)
        tokens=[{'text':w[4],'bbox':list(w[:4]),'method':'pdf_text','confidence':None} for w in words]
        warnings=[]
        if needs_ocr(page,words) and mode!='off':
            try:
                scale=min(3,2400/max(page.rect.width,page.rect.height))
                tokens.extend(run_ocr(page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False),scale))
            except Exception as exc:
                warnings.append(f'page {index+1}: OCR unavailable ({type(exc).__name__}); visual verification required')
        for number,token in enumerate(tokens): token['id']=f'p{index+1}-t{number+1}'
        return {'page':index+1,'width':page.rect.width,'height':page.rect.height,'tokens':tokens,'warnings':warnings}


def extract(path):
    from . import db
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    mode = os.getenv('ENGINEERING_OCR', 'auto')
    cache = db.DATA / 'evidence' / f'{digest}-{VERSION}-{mode}.json'
    if cache.exists():
        cached = json.loads(cache.read_text(encoding='utf-8'))
        if not cached.get('warnings'):
            return cached
    packet = {'version': VERSION, 'sha256': digest, 'file': path.name,
              'pages': [], 'warnings': [], 'coordinate_system': 'PDF points, top-left origin'}
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            from .cloud_activity import activity_call
            info=activity_call('pdf_page',extract_page,Path(path),index,mode)
            packet['warnings'].extend(info.pop('warnings',[]))
            packet['pages'].append(info)
    cache.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement: parallel readers never see partial JSON.
    import uuid
    temporary = cache.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(packet, ensure_ascii=False), encoding='utf-8')
    temporary.replace(cache)
    return packet


def visual_input(path, zoom=2, max_pages=2):
    packet = extract(path)
    images, labels = [], []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            rect = page.rect
            clips = [('overview', rect)]
            # Overlap keeps annotations straddling a tile boundary visible.
            for y in (0, .45):
                for x in (0, .45):
                    clips.append((f'tile-{x}-{y}', pymupdf.Rect(rect.width*x, rect.height*y,
                                  rect.width*min(x+.55, 1), rect.height*min(y+.55, 1))))
            for token_id, bbox in detail_regions(packet['pages'][index]):
                clips.append((f'detail-{token_id}', pymupdf.Rect(bbox)))
            for name, clip in clips:
                scale = min(5 if name.startswith('detail-') else zoom, 1800 / max(clip.width, clip.height))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
                images.append('data:image/jpeg;base64,'+base64.b64encode(pix.tobytes('jpeg', jpg_quality=90)).decode())
                labels.append({'image': len(images), 'page': index+1, 'region': name, 'bbox': list(clip)})
    text = json.dumps({'source': packet['sha256'], 'file': packet['file'], 'image_map': labels,
                       'warnings': packet['warnings'], 'pages': packet['pages'],
                       'visual_pages_omitted': max(0, len(packet['pages'])-max_pages)}, ensure_ascii=False)
    return text, images, len(packet['pages'])


def ground_dimensions(result, packet):
    """Resolve citations to stored evidence, retaining uncertainty about meaning."""
    from .manufacturing import dimension_value
    index = {t['id']: dict(t, page=p['page']) for p in packet['pages'] for t in p['tokens']}
    evidence = result.get('dimension_evidence')
    if not isinstance(evidence, dict):
        evidence = {}
    reviews = list(result.get('review_items') or [])
    grounded = {}
    for field, source in evidence.items():
        if not isinstance(source, dict):
            continue
        requested = source.get('token_ids') or []
        if not isinstance(requested, list):
            requested = []
        refs = [index[v] for v in requested if isinstance(v,str) and v in index]
        derived = source.get('method') in ('derived', 'rule')
        item = dict(source, token_ids=[r['id'] for r in refs], file_sha256=packet['sha256'],
                    locations=[{'page':r['page'],'bbox':r['bbox'],'text':r['text'],
                                'method':r['method'],'confidence':r.get('confidence')} for r in refs],
                    verified=False)
        issues = []
        if not derived and (len(refs) != len(requested) or not refs):
            issues.append('缺少有效原图引用')
        if derived and (not source.get('derivation') or not source.get('confidence')):
            issues.append('工程推导缺少输入约束、计算关系或置信度')
        if refs and source.get('page') not in {r['page'] for r in refs}:
            issues.append('引用页码与实际位置不一致')
        value = dimension_value(result, field)
        if not derived and isinstance(value, (int,float)) and not isinstance(value,bool) and refs:
            numbers = []
            for ref in refs:
                # Common drawing OCR confusion: chamfer prefix C0.5 is often
                # read as CO.5.  Correct only an O in a numeric feature prefix,
                # preserving the strict numeric evidence check elsewhere.
                normalized = str(ref['text']).translate(str.maketrans({'，':'.', '。':'.'}))
                normalized = re.sub(r'(?i)(?<=[CRΦØ⌀])O(?=[.\d])', '0', normalized)
                normalized = re.sub(r'(?<=\d)\.\s+(?=\d)', '.', normalized)
                numbers.extend(float(n) for n in re.findall(r'(?<!\d)(?:\d+(?:\.\d+)?|\.\d+)', normalized))
            if not any(abs(n-value)<.0001 for n in numbers):
                issues.append('数值未在引用文字中匹配，须核对局部图或推算依据')
        item['checks'] = issues
        grounded[field] = item
        reviews.extend(f'{field}：{issue}' for issue in issues)
    result['dimension_evidence'] = grounded
    result['review_items'] = list(dict.fromkeys(reviews))
    return result
