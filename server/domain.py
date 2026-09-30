"""Deterministic checks around AI drafts. These do not replace an engineer review."""
import re


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _dimension_number(value):
    """Read the nominal value from a human-facing assembly dimension."""
    match = re.search(r'(?<![\d.])(?:[ΦØ⌀]\s*)?([0-9]+(?:\.[0-9]+)?)', str(value or ''))
    return float(match.group(1)) if match else None


def _shape_hint(part, geometry):
    shape = geometry.get('shape_type')
    if shape in {'tube', 'plate', 'rotational'}:
        return shape
    text = ' '.join(str(part.get(key) or '') for key in ('name', 'kind', 'drawing_no'))
    if any(word in text for word in ('筒', '套', '管')):
        return 'tube'
    if any(word in text for word in ('板', '盘', '法兰', '垫片')):
        return 'plate'
    if any(word in text for word in ('轴', '销', '辊', '杆')):
        return 'rotational'
    return None


def complete_candidate_geometry(geometry, part, project=None, history=None):
    """Complete a drawable *candidate* when extraction left an empty outline.

    This is intentionally separate from :func:`complete_draft_geometry`: callers
    opt in only for drawing generation.  Values are selected from assembly
    labels, then a previously approved same-part drawing, and finally a
    conservative parametric family template.  Every filled value is marked as
    an engineering derivation and remains behind the normal human approval gate.
    """
    from copy import deepcopy
    from .manufacturing import geometry_blockers

    result = deepcopy(geometry or {})
    if not geometry_blockers({**result, 'manufacturing': {}}):
        return result
    shape = _shape_hint(part, result)
    if not shape:
        return result
    result['shape_type'] = shape
    reviews = list(result.get('review_items') or [])
    evidence = dict(result.get('dimension_evidence') or {})
    origins = {}

    # Exact-name approved history is deliberately stronger than a generic
    # template, but never stronger than a value already extracted this run.
    target_name = str(part.get('name') or '').strip()
    historical = None
    for item in history or []:
        candidate = item.get('geometry') or {}
        if (str(item.get('name') or '').strip() == target_name and
                candidate.get('approval_status') == 'approved' and
                candidate.get('shape_type') == shape):
            historical = candidate
            break

    dimensions = ((project or {}).get('analysis') or {}).get('dimensions') or []
    labelled = []
    for item in dimensions:
        if not isinstance(item, dict):
            continue
        value = _dimension_number(item.get('value'))
        if value:
            labelled.append((str(item.get('label') or ''), value, str(item.get('value') or '')))

    def labelled_value(words):
        relevant = [(label, value, raw) for label, value, raw in labelled
                    if any(word in label for word in words)]
        named = [item for item in relevant if target_name and target_name in item[0]]
        return (named or relevant or [(None, None, None)])[0]

    def fill(field, value, source, confidence='低'):
        if result.get(field) is not None or not _positive(value):
            return
        result[field] = round(float(value), 4)
        origins[field] = source
        evidence[field] = {
            'method': 'derived', 'token_ids': [], 'state': 'part_delivery',
            'derivation': source, 'input_fields': ['MBOM对象语义', '装配约束', '历史工程结果'],
            'confidence': confidence, 'checks': [],
        }

    if shape in {'tube', 'plate'}:
        _, assembly_od, raw_od = labelled_value(('外径', '直径'))
        length_words = ('长度', '总长', '筒长') if shape == 'tube' else ('厚度', '板厚')
        _, assembly_length, raw_length = labelled_value(length_words)
        axial_field = 'overall_length_mm' if shape == 'tube' else 'thickness_mm'
        history_axial = (historical or {}).get(axial_field)
        fill('outer_diameter_mm', assembly_od,
             f'由装配尺寸标签“{raw_od}”形成当前对象外径候选', '中')
        fill('outer_diameter_mm', (historical or {}).get('outer_diameter_mm'),
             '复用同名已审核历史图纸的外径作为候选', '中')
        od = result.get('outer_diameter_mm')
        fill('outer_diameter_mm', 100, '未取得数值时按通用回转件参数化模板建立可编辑基准轮廓')
        od = result['outer_diameter_mm']
        fill('inner_diameter_mm', (historical or {}).get('inner_diameter_mm'),
             '复用同名已审核历史图纸的内径作为候选', '中')
        fill('inner_diameter_mm', od * (0.8 if shape == 'tube' else 0.5),
             '按对象族参数化模板给出可制造候选；壁厚/孔径须人工复核')
        fill(axial_field, assembly_length,
             f'由装配尺寸标签“{raw_length}”形成当前对象轴向尺寸候选', '中')
        fill(axial_field, history_axial,
             '复用同名已审核历史图纸的轴向尺寸作为候选', '中')
        fill(axial_field, max(od * (10 if shape == 'tube' else .2), 20),
             '按对象族参数化模板给出可编辑轴向尺寸候选')
        if result['inner_diameter_mm'] >= result['outer_diameter_mm']:
            result['inner_diameter_mm'] = round(result['outer_diameter_mm'] * .6, 4)
            origins['inner_diameter_mm'] = '为形成封闭实体，将冲突孔径收敛为外径的60%候选值'
    else:
        history_segments = deepcopy((historical or {}).get('segments') or [])
        if not result.get('segments') and history_segments:
            result['segments'] = history_segments
            origins['segments'] = '复用同名已审核历史图纸的完整分段轮廓作为候选'
        if not result.get('segments'):
            _, diameter, raw_diameter = labelled_value(('外径', '轴径', '直径'))
            _, length, raw_length = labelled_value(('长度', '总长'))
            diameter = diameter or 50
            length = length or max(diameter * 4, 100)
            result['segments'] = [{
                'length_mm': round(length, 4), 'diameter_mm': round(diameter, 4),
                'basis': '工程推导', 'note': '参数化单段基准轮廓，待按装配接口细化台阶特征',
            }]
            result['overall_length_mm'] = round(length, 4)
            origins['segments.0.length_mm'] = f'由“{raw_length}”或对象族模板形成长度候选'
            origins['segments.0.diameter_mm'] = f'由“{raw_diameter}”或对象族模板形成直径候选'
        elif not _positive(result.get('overall_length_mm')):
            result['overall_length_mm'] = round(sum(
                item.get('length_mm', 0) for item in result['segments'] if _positive(item.get('length_mm'))), 4)
        for field, source in origins.items():
            evidence[field] = {
                'method': 'derived', 'token_ids': [], 'state': 'part_delivery',
                'derivation': source, 'input_fields': ['MBOM对象语义', '装配约束', '历史工程结果'],
                'confidence': '低', 'checks': [],
            }

    if origins:
        details = '；'.join(f'{key}：{source}' for key, source in origins.items())
        reviews.append('系统为避免空白图纸已生成可编辑工程候选：' + details + '。请在审核前核对或调整。')
        result['candidate_inference'] = {
            'status': 'provisional', 'fields': list(origins),
            'policy': 'assembly-history-parametric-v1',
        }
        result['confidence'] = '低' if any('模板' in value for value in origins.values()) else result.get('confidence', '中')
    result['dimension_evidence'] = evidence
    result['review_items'] = list(dict.fromkeys(reviews))
    return result


def normalize_material(value):
    """The material column stores the designation, never the model's explanation."""
    result = re.split(r'[（(，,；;\n]', str(value or '').strip(), 1)[0].strip()
    return result[:80] if result not in ('未知', '未标注', '待确认') else ''


def complete_draft_geometry(geometry, part):
    """Preserve measured geometry. Only solve one explicitly missing span."""
    from copy import deepcopy
    from .manufacturing import number, prepare_manufacturing
    geometry = deepcopy(geometry or {})
    reviews = list(geometry.get('review_items') or [])
    shape = geometry.get('shape_type')
    segments = geometry.get('segments') or []
    if shape is None:
        shape = 'rotational' if segments else 'unsupported'
        geometry['shape_type'] = shape
    if shape == 'rotational':
        overall = geometry.get('overall_length_mm')
        missing = [i for i, span in enumerate(segments) if span.get('length_mm') is None]
        known = [span.get('length_mm') for span in segments if span.get('length_mm') is not None]
        if number(overall) and len(missing) == 1 and all(number(x) for x in known):
            remainder = round(overall - sum(known), 4)
            if remainder > 0:
                segments[missing[0]]['length_mm'] = remainder
                segments[missing[0]]['basis'] = '差值推算'
                reviews.append(f'第 {missing[0]+1} 个已识别轮廓段长 {remainder:g} mm 为闭合尺寸链推算，须复核。')
        geometry['segments'] = segments
    geometry['review_items'] = list(dict.fromkeys(reviews))
    geometry = prepare_manufacturing(geometry)
    from .engineering_model import build_model
    geometry['engineering_model'] = build_model(geometry, part)
    return geometry


def check_geometry(geometry):
    geometry = dict(geometry or {})
    segments = geometry.get('segments') or []
    reviews = list(geometry.get('review_items') or [])
    if geometry.get('shape_type') == 'tube':
        outer = geometry.get('outer_diameter_mm')
        inner = geometry.get('inner_diameter_mm')
        length = geometry.get('overall_length_mm')
        if not all(isinstance(x, (int, float)) for x in (outer, inner, length)) or not (outer > inner >= 0 and length > 0):
            reviews.append('空心筒体尺寸无效，须核对长度、外径和内孔；不得使用其他对象的尺寸代替。')
        geometry['review_items'] = list(dict.fromkeys(reviews))
        return geometry
    if geometry.get('shape_type') == 'plate':
        outer = geometry.get('outer_diameter_mm')
        inner = geometry.get('inner_diameter_mm')
        thick = geometry.get('thickness_mm')
        if not all(isinstance(x, (int, float)) for x in (outer, inner, thick)) or not (outer > inner >= 0 and thick > 0):
            reviews.append('环板尺寸无效，须核对外径、内孔和厚度。')
        geometry['review_items'] = list(dict.fromkeys(reviews))
        return geometry
    if not segments:
        reviews.append('未取得可确认的回转体轮廓，不能导出可加工 CAD 外形。')
    for index, segment in enumerate(segments, 1):
        if segment.get('length_mm') is None or segment.get('diameter_mm') is None:
            reviews.append(f'轮廓段 {index} 的长度或直径未确认。')
        for key in ('length_mm', 'diameter_mm'):
            value = segment.get(key)
            if value is not None and (not isinstance(value, (int, float)) or value <= 0):
                reviews.append(f'轮廓段 {index} 的 {key} 无效。')
    overall = geometry.get('overall_length_mm')
    if overall is not None:
        try:
            overall = float(overall)
            total = sum(float(s['length_mm']) for s in segments if s.get('length_mm') is not None)
            if all(s.get('length_mm') is not None for s in segments) and abs(total - overall) > 0.05:
                reviews.append(f'分段长度合计 {total:g} mm 与标注总长 {overall:g} mm 不一致，禁止导出 CAD。')
        except (TypeError, ValueError):
            reviews.append('总长数值无效，需复核。')
    geometry['review_items'] = list(dict.fromkeys(reviews))
    from .manufacturing import prepare_manufacturing
    return prepare_manufacturing(geometry)


def check_process(process, project, is_assembly, target=None):
    process = dict(process or {})
    steps = process.get('steps') or []
    reviews = list(process.get('review_items') or [])
    batch_claim = re.compile(r'(?:数量\s*)?\d+\s*[*×xX]\s*\d+\s*=\s*\d+\s*件')
    def remove_batch_claim(value):
        return batch_claim.sub('每上级用量以 MBOM 为准；生产批量待确认', str(value or ''))
    process['summary'] = remove_batch_claim(process.get('summary', ''))
    for step in steps:
        for field in ('description', 'inspection'):
            if field in step:
                step[field] = remove_batch_claim(step[field])
    reviews = [remove_batch_claim(item) for item in reviews]
    requirements = list((target or {}).get('geometry', {}).get('technical_requirements') or [])
    from .manufacturing import prepare_manufacturing
    reviews.extend(a['warning'] for a in prepare_manufacturing((target or {}).get('geometry') or {})['manufacturing']['allowances'] if a.get('warning'))
    if is_assembly:
        requirements += project.get('analysis', {}).get('technical_requirements', [])
    source = ' '.join(requirements)
    if not any(word in source for word in ('热处理', '淬火', '回火', '调质', '退火', '正火')):
        filtered = [s for s in steps if not ('热处理' in str(s.get('operation', '')) and
                    any(word in str(s.get('description', '')) for word in ('如图纸要求', '若图纸', '如需')))]
        if len(filtered) != len(steps):
            reviews.append('已移除仅以“如图纸要求”提出、但源图未明确要求的热处理工序。')
        steps = filtered
    for index, step in enumerate(steps, 1):
        step['seq'] = index
        step['review_required'] = True
        step.setdefault('operation', '待确认工序')
        step.setdefault('equipment', '')
        step.setdefault('description', '')
        step.setdefault('inspection', '待工程师确定检验方法')
    operations = ' '.join(str(s.get('operation', '')) for s in steps)
    if is_assembly and '热压' in source and not any(word in operations for word in ('热装', '热压')):
        reviews.append('装配图要求热压装配，但工艺步骤未列热装/热压。')
    if is_assembly and not any(word in operations for word in ('检验', '检测', '复检')):
        reviews.append('装配工艺缺少最终检验步骤。')
    if any(word in operations for word in ('淬火', '回火', '调质', '退火', '正火')) and not any(word in source for word in ('热处理', '淬火', '回火', '调质', '退火', '正火')):
        reviews.append('热处理未见装配图明确要求，材料、硬度与工艺参数须核实。')
    process['steps'] = steps
    process['review_items'] = list(dict.fromkeys(reviews))
    return process
