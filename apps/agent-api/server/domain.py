"""Deterministic checks around AI drafts. These do not replace an engineer review."""
import re


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
