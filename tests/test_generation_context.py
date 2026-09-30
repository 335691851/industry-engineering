from server.engineering_context import build_generation_context
from server.evidence import ground_dimensions
from server.manufacturing import geometry_blockers
from server.domain import complete_candidate_geometry
from server.workflow import workflow_state


def approved_part(ident, name):
    return {
        'id': ident, 'name': name, 'kind': '零件', 'drawing_no': ident.upper(), 'material': '45',
        'specifications': {'reference_approval': {'status': 'approved'}},
        'geometry': {'shape_type': 'tube', 'outer_diameter_mm': 50, 'inner_diameter_mm': 30,
                     'overall_length_mm': 100, 'approval_status': 'approved'},
        'process': {'steps': [{'operation': '精加工'}], 'approval_status': 'approved'},
        'drawing_pdf': 'drawing.pdf',
    }


def test_generation_context_contains_topology_assembly_precision_and_process():
    parent = approved_part('parent', '已审核上级')
    target = approved_part('target', '目标零件')
    child = approved_part('child', '已审核下级')
    project = {
        'id': 'project', 'name': '总装', 'drawing_no': 'ASM-01',
        'analysis': {
            'dimensions': [{'label': '装配中心距', 'value': '120±0.1'}],
            'technical_requirements': ['配合面同轴度 0.02', '装配前精加工并检验'],
        },
        'parts': [parent, target, child],
        'mbom_links': [
            {'parent_id': 'parent', 'child_id': 'target', 'quantity': 2, 'evidence': '装配明细', 'confidence': '高'},
            {'parent_id': 'target', 'child_id': 'child', 'quantity': 1, 'evidence': '剖视关系', 'confidence': '高'},
        ],
    }
    context = build_generation_context(project, target)
    assert context['topology']['parents'][0]['name'] == '已审核上级'
    assert context['topology']['children'][0]['name'] == '已审核下级'
    assert context['topology']['usages'][0]['quantity'] == 2
    assert context['assembly_constraints']['dimensions'][0]['label'] == '装配中心距'
    assert context['assembly_constraints']['precision_requirements'] == ['配合面同轴度 0.02']
    assert context['assembly_constraints']['process_requirements'] == ['装配前精加工并检验']


def test_missing_draft_geometry_is_agent_input_not_generation_blocker():
    part = {
        'id': 'target', 'name': '轴', 'specifications': {'reference_approval': {'status': 'approved'}},
        'geometry': {}, 'process': {}, 'drawing_pdf': '',
    }
    project = {'stage': 'MBOM已确认', 'parts': [part], 'mbom_links': []}
    state = workflow_state(project, part)
    assert state['can_generate_drawing']
    assert not state['drawing_blockers']
    assert state['drawing_preflight'] == ['回转体分段尺寸不完整']


def test_traceable_engineering_derivation_does_not_require_fake_ocr_citation():
    geometry = {
        'shape_type': 'tube', 'outer_diameter_mm': 50, 'inner_diameter_mm': 30,
        'overall_length_mm': 100,
        'dimension_evidence': {
            key: {'method': 'derived', 'token_ids': [], 'state': 'part_delivery',
                  'derivation': '由已审核配合界面与闭合尺寸链计算',
                  'input_fields': ['上级配合直径', '装配总长'], 'confidence': '中'}
            for key in ('outer_diameter_mm', 'inner_diameter_mm', 'overall_length_mm')
        },
    }
    grounded = ground_dimensions(geometry, {'sha256': 'x', 'pages': []})
    assert all(not source['checks'] for source in grounded['dimension_evidence'].values())
    assert not geometry_blockers(grounded)


def test_incomplete_derivation_remains_a_hard_review_blocker():
    geometry = {
        'shape_type': 'tube', 'outer_diameter_mm': 50, 'inner_diameter_mm': 30,
        'overall_length_mm': 100,
        'dimension_evidence': {
            'outer_diameter_mm': {'method': 'derived', 'token_ids': [], 'state': 'part_delivery'}
        },
    }
    grounded = ground_dimensions(geometry, {'sha256': 'x', 'pages': []})
    assert geometry_blockers(grounded)


def test_empty_tube_draft_becomes_traceable_drawable_candidate():
    part = {'id': 'roller', 'name': '辊筒', 'kind': '零件', 'geometry': {}}
    project = {
        'analysis': {'dimensions': [
            {'label': '辊筒外径', 'value': 'Φ150±0.2'},
            {'label': '辊筒长度', 'value': '1550'},
        ]}
    }
    result = complete_candidate_geometry({}, part, project)
    assert result['shape_type'] == 'tube'
    assert result['outer_diameter_mm'] == 150
    assert result['overall_length_mm'] == 1550
    assert 0 < result['inner_diameter_mm'] < result['outer_diameter_mm']
    assert result['candidate_inference']['status'] == 'provisional'
    assert result['dimension_evidence']['inner_diameter_mm']['method'] == 'derived'
    assert not geometry_blockers({**result, 'manufacturing': {}})
    assert any('可编辑工程候选' in item for item in result['review_items'])


def test_approved_same_name_history_precedes_generic_template():
    part = {'id': 'plate', 'name': '闷板', 'kind': '零件', 'geometry': {}}
    history = [{'name': '闷板', 'geometry': {
        'shape_type': 'plate', 'outer_diameter_mm': 130,
        'inner_diameter_mm': 60, 'thickness_mm': 30,
        'approval_status': 'approved',
    }}]
    result = complete_candidate_geometry({}, part, {'analysis': {}}, history)
    assert (result['outer_diameter_mm'], result['inner_diameter_mm'], result['thickness_mm']) == (130, 60, 30)
    assert result['confidence'] != '低'
    assert not geometry_blockers({**result, 'manufacturing': {}})


def test_unknown_rotational_part_gets_editable_baseline_not_blank_sheet():
    result = complete_candidate_geometry({}, {'name': '传动轴', 'kind': '零件'}, {'analysis': {}})
    assert result['shape_type'] == 'rotational'
    assert result['segments'][0]['length_mm'] > 0
    assert result['segments'][0]['diameter_mm'] > 0
    assert result['overall_length_mm'] == result['segments'][0]['length_mm']
    assert not geometry_blockers({**result, 'manufacturing': {}})
