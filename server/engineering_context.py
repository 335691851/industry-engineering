"""Build the evidence and constraint packet consumed by drawing/process Agents.

The packet is product agnostic.  It describes where the current object sits in
the MBOM, which approved interfaces surround it, and which assembly dimensions
and manufacturing requirements may constrain its delivered state.  Models may
propose missing values from this packet, while deterministic validation remains
responsible for deciding whether the proposal can be approved.
"""
from __future__ import annotations

from copy import deepcopy


PRECISION_TERMS = ('公差', '配合', '精度', '粗糙度', '同轴', '圆跳动', '垂直度', '平行度', '基准', '间隙', '过盈')
PROCESS_TERMS = ('加工', '车', '铣', '磨', '镗', '钻', '焊', '热装', '压装', '热处理', '淬火', '回火', '调质', '镀', '检验')


def _text_items(value):
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item)
            elif isinstance(item, dict):
                label = item.get('label') or item.get('name') or item.get('requirement')
                val = item.get('value')
                if label:
                    result.append(f'{label}: {val}' if val not in (None, '') else str(label))
        return result
    return []


def _approved_result(part):
    geometry = part.get('geometry') or {}
    process = part.get('process') or {}
    return {
        'id': part.get('id'), 'name': part.get('name'), 'kind': part.get('kind'),
        'drawing_no': part.get('drawing_no'), 'material': part.get('material'),
        'geometry': deepcopy(geometry) if geometry.get('approval_status') == 'approved' else {},
        'process': deepcopy(process) if process.get('approval_status') == 'approved' else {},
    }


def build_generation_context(project, part):
    """Return the structured engineering input for one target object."""
    links = project.get('mbom_links') or []
    parts = project.get('parts') or []
    by_id = {item['id']: item for item in parts}
    parent_links = [link for link in links if link.get('child_id') == part.get('id')]
    child_links = [link for link in links if link.get('parent_id') == part.get('id')]
    parent_ids = {link.get('parent_id') for link in parent_links if link.get('parent_id')}
    sibling_ids = {
        link.get('child_id') for link in links
        if link.get('parent_id') in parent_ids and link.get('child_id') != part.get('id')
    }
    analysis = project.get('analysis') or {}
    requirements = []
    for key in ('technical_requirements', 'requirements', 'review_items'):
        requirements.extend(_text_items(analysis.get(key)))
    assembly_dimensions = []
    for key in ('dimensions', 'drawing_dimensions', 'key_dimensions'):
        value = analysis.get(key)
        if isinstance(value, list):
            assembly_dimensions.extend(deepcopy(value))
    precision = [item for item in requirements if any(term in item for term in PRECISION_TERMS)]
    process = [item for item in requirements if any(term in item for term in PROCESS_TERMS)]
    existing = part.get('geometry') or {}
    return {
        'target': {
            'id': part.get('id'), 'name': part.get('name'), 'kind': part.get('kind'),
            'drawing_no': part.get('drawing_no'), 'material': part.get('material'),
            'specifications': deepcopy(part.get('specifications') or {}),
            'user_overrides': deepcopy(existing.get('user_overrides') or {}),
        },
        'topology': {
            'parents': [_approved_result(by_id[item]) for item in parent_ids if item in by_id],
            'root_assembly': ({'id': project.get('id'), 'name': project.get('name'),
                               'drawing_no': project.get('drawing_no')} if any(
                                   link.get('parent_id') is None for link in parent_links) else None),
            'children': [{**_approved_result(by_id[link['child_id']]), 'quantity': link.get('quantity', 1),
                          'relationship_evidence': link.get('evidence', ''),
                          'relationship_confidence': link.get('confidence', '')}
                         for link in child_links if link.get('child_id') in by_id],
            'siblings': [by_id[item].get('name') for item in sibling_ids if item in by_id],
            'usages': [{'parent_id': link.get('parent_id'), 'quantity': link.get('quantity', 1),
                        'evidence': link.get('evidence', ''), 'confidence': link.get('confidence', '')}
                       for link in parent_links],
        },
        'assembly_constraints': {
            'dimensions': assembly_dimensions,
            'technical_requirements': requirements,
            'precision_requirements': precision,
            'process_requirements': process,
        },
        'existing_engineering': {
            'geometry': deepcopy(existing),
            'process': deepcopy(part.get('process') or {}),
        },
        'inference_policy': {
            'goal': '形成当前对象合格交付状态的成品制造定义，而非复制装配外形',
            'source_priority': ['用户锁定参数', '已审核单件参考图', '装配图明确标注',
                                '已审核相邻接口与尺寸链计算', '材料和工艺规则建议'],
            'allowed': ['从闭合尺寸链计算唯一缺失值', '从已审核配合界面建立候选尺寸',
                        '根据材料、毛坯和工艺路线提出独立加工余量建议'],
            'required_output': ['成品几何与完整视图', '尺寸与精度/配合', '基准与形位要求',
                                '表面要求', '毛坯及加工余量', '制造与检验技术要求'],
            'review_rule': '推导值必须记录 basis、来源和置信度；冲突不得静默覆盖，进入待复核项。',
        },
    }
