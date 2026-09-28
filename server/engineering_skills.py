"""Industrial skill routing for each engineering workflow step.

These are prompt-level, auditable capabilities. They do not give the model arbitrary
code or filesystem access; they tell it which industrial reasoning and output contract
apply to the current object and step.
"""
from __future__ import annotations

import json


SKILLS = {
    'evidence': '''证据与可追溯：区分图纸明示、用户指定、几何推算、标准引用、历史样例和未知项；
每个关键结论标明依据，未知项保持为空并进入待复核。''',
    'mbom': '''MBOM 与装配关系：按可制造、可采购、可独立装配和可独立检验的边界识别层级；
区分零部件定义与装配使用关系，支持复用，校核每上级用量、装配顺序、接口和可达性。''',
    'drawing': '''机械制图：以 ISO 128-1:2020 为总体表达主规范，并按 ISO 128-2/3、ISO 129-1、
ISO 5455、ISO 5456-2、ISO 5457 和 ISO 7200 分别约束线型、视图/剖视、尺寸、公称比例、
投影法、图幅与标题栏。先确定当前对象实体边界，再组织最少但充分的视图、剖视、基准、尺寸链、
公差、配合、形位公差、粗糙度、材料和技术要求；装配尺寸只能作为接口证据，不能直接冒充单件尺寸。''',
    'tolerance': '''尺寸、公差与配合：保留图纸原符号和基准；检查尺寸链闭合、配合对象一致性、
形位公差适用要素和粗糙度对应表面；不得凭常识填入未标数值。''',
    'allowance': '''制造留量：先定义本件交付状态与后续加工，再区分成品尺寸、毛坯尺寸和工序中间尺寸。
按材料、毛坯制造方法、装夹、粗精加工、热处理/焊接变形提出带理由的单边余量。
外径加两侧余量，内径减两侧余量，轴向按加工面数计算。ISO 制图标准不提供本件加工余量。
通过 calculate_machining_allowance 做确定性试算，工艺建议待人工审核，禁止覆盖成品公差。''',
    'machining': '''机加工：结合毛坯、材料、基准和精度安排下料、粗加工、半精加工、热处理衔接、
精加工、磨削及检验；考虑装夹可达性、余量、基准统一和变形控制。''',
    'welding': '''焊接：识别接头形式、坡口、装配定位、焊接次序、变形与焊后处理；
只有源图或已审核工艺有依据时才能给出焊材、参数和检测等级。''',
    'heat_treatment': '''热处理：根据材料牌号、硬度/组织要求和加工阶段判断工序位置；
温度、时间、冷却介质和硬度范围没有明确依据时必须留空待复核。''',
    'assembly': '''装配：校核零件齐套、配合面清理、方向、定位基准、过盈/间隙、压装或热装、
紧固/焊接顺序、过程测量和最终功能检验。''',
    'inspection': '''检验：把关键尺寸、配合、形位、粗糙度、焊缝、热处理结果和装配功能映射为检验项；
方法、量具、频次和验收值必须与证据及工序阶段一致。''',
}


STEP_CONTRACTS = {
    'mbom': '''本步骤只输出多级 MBOM：唯一零部件定义、父子使用关系、每上级用量、关系证据、
置信度和待复核项。不要生成零件尺寸或工艺参数。''',
    'drawing': '''本步骤输出可编辑的单件制造图数据：对象语义、实体边界、几何、特征、尺寸、
公差/配合、基准、粗糙度、材料、技术要求、证据与待复核项，并给出视图/剖视规划。
输出必须可由 ISO 128-1:2020 制图器确定性渲染；不要把工艺建议写成图纸明示要求。''',
    'part_process': '''本步骤输出零件制造工艺：毛坯、基准、工序顺序、设备、工装、操作说明、
过程检验、依据和待复核项。工序必须以当前零件已确认图纸为输入。''',
    'assembly_process': '''本步骤输出部件或总装工艺：齐套、清理、接口复测、定位、装配/连接顺序、
过程测量、最终检验和待复核项；不要重复下级零件自己的完整机加工路线。''',
    'conversation': '''本步骤先识别用户意图和当前对象状态，再回答或调用对应工程工具；
修改或重新生成必须将用户参数完整传给工具。''',
}


def _text(project, part, instruction):
    analysis = project.get('analysis') or {}
    values = [instruction, json.dumps(analysis.get('technical_requirements') or [], ensure_ascii=False)]
    if part:
        values.extend([part.get('name', ''), part.get('kind', ''), part.get('material', ''),
                       json.dumps(part.get('geometry') or {}, ensure_ascii=False),
                       json.dumps(part.get('specifications') or {}, ensure_ascii=False)])
    return ' '.join(str(value or '') for value in values)


def object_semantics(project, part):
    """Build a business-semantic object profile from MBOM position and known geometry."""
    if part is None:
        return {'role': 'root_assembly', 'family': 'assembly', 'has_children': True,
                'boundary': '项目根装配体；包含 MBOM 直接下级，只生成装配关系和装配工艺。'}
    links = project.get('mbom_links') or []
    part_id = part.get('id')
    children = [link for link in links if link.get('parent_id') == part_id]
    parents = [link for link in links if link.get('child_id') == part_id]
    name = str(part.get('name') or '')
    shape = str((part.get('geometry') or {}).get('shape_type') or '')
    if children:
        family = 'assembly'
        boundary = '子装配部件；边界由直接下级、连接方式与装配接口定义。'
    elif shape in {'tube', 'plate', 'rotational'}:
        family = shape
        boundary = '沿用已有几何分类，仍须根据当前参考图与审核参数校核实体边界。'
    else:
        family = 'general_part'
        boundary = '从图纸视图、截面、孔腔、明细序号和装配关系识别几何类型；不能仅凭名称固定模型。'
    return {'role': 'subassembly' if children else 'part', 'family': family,
            'has_children': bool(children), 'parent_count': len(parents), 'boundary': boundary}


def select_skills(step, project, part=None, instruction=''):
    """Select industrial skills from step requirements and available evidence."""
    text = _text(project, part, instruction)
    semantics = object_semantics(project, part)
    selected = ['evidence']
    if step == 'mbom':
        selected.extend(['mbom', 'assembly', 'inspection'])
    elif step == 'drawing':
        selected.extend(['drawing', 'tolerance', 'allowance', 'inspection'])
    elif step == 'part_process':
        selected.extend(['machining', 'allowance', 'inspection'])
    elif step == 'assembly_process':
        selected.extend(['assembly', 'inspection'])
    if any(word in text for word in ('焊', '坡口', '焊缝')):
        selected.append('welding')
    if any(word in text for word in ('热处理', '淬火', '回火', '调质', '退火', '正火', '去应力')):
        selected.append('heat_treatment')
    if any(word in text for word in ('配合', '公差', '粗糙度', '形位', '同轴', '跳动')):
        selected.append('tolerance')
    if semantics['has_children'] and step in ('part_process', 'assembly_process'):
        selected.append('assembly')
    return list(dict.fromkeys(selected))


def skill_context(step, project, part=None, instruction=''):
    semantics = object_semantics(project, part)
    selected = select_skills(step, project, part, instruction)
    return {
        'step': step,
        'output_contract': STEP_CONTRACTS[step],
        'object_semantics': semantics,
        'selected_skills': selected,
        'skill_instructions': [SKILLS[name] for name in selected],
    }


def prompt_block(step, project, part=None, instruction=''):
    context = skill_context(step, project, part, instruction)
    return f'''当前步骤与工业技能路由：{json.dumps(context, ensure_ascii=False)}
必须服从 output_contract。先按 object_semantics 确定对象边界，再使用 selected_skills 完成推理；
未选中的专业能力如被用户输入或图纸证据触发，可补充使用，但必须说明触发依据。'''
