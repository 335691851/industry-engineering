import json
from pathlib import Path

from server.engineering_model import build_model
from server.manufacturing import prepare_manufacturing
from scripts.evaluate_engineering import evaluate


def fixture():
    return json.loads((Path(__file__).parent/'fixtures/tube_case.json').read_text(encoding='utf-8'))


def test_stock_state_drives_allowances_without_product_specific_rule():
    case = fixture()
    g = {**case['expected'], 'manufacturing': {'stock_dimensions': case['stock']}}
    result = build_model(g, {'id': 'any-part'})
    values = {a['dimension']: a['per_side_mm'] for a in result['calculations']['allowances']}
    assert values == {'outer_diameter_mm': 2.5, 'inner_diameter_mm': 1, 'overall_length_mm': 3}
    assert not result['validation']['errors']
    assert result['calculations']['wall_thickness_mm'] == 14.5
    assert result['validation']['status'] == 'pending_human_review'


def test_incompatible_stock_and_unlocated_evidence_are_not_approved():
    g = {'shape_type':'tube', 'outer_diameter_mm':100, 'inner_diameter_mm':80, 'overall_length_mm':200,
         'manufacturing': {'stock_dimensions': {'outer_diameter_mm':90}}}
    result = build_model(g, {})
    assert result['validation']['status'] == 'blocked'
    assert result['validation']['warnings']
    assert all(not d['verified'] for d in result['dimensions'])


def test_case_rejects_assembly_outline_and_checks_end_regions():
    case = fixture()
    good = {**case['expected'], 'machining_zones': case['zones']}
    assert evaluate(good, case)['passed']
    bad = {**good, 'overall_length_mm':1971.5, 'outer_diameter_mm':150}
    assert not evaluate(bad, case)['passed']


def test_kernel_validates_base_solid():
    from server.cad_kernel import validate_solid
    result = validate_solid(fixture()['expected'])
    if result['status'] == 'unavailable':
        import pytest
        pytest.skip('optional CAD kernel not installed')
    assert result['status'] == 'valid'
    assert result['bounding_box_mm'] == [154,154,1554]


def test_stock_evidence_does_not_block_delivery_but_cross_state_dimension_does():
    from server.manufacturing import geometry_blockers
    g = {**fixture()['expected'], 'dimension_evidence': {
        'stock_dimensions.outer_diameter_mm': {'state':'stock'},
        'inner_diameter_mm': {'state':'part_delivery'}}}
    assert not geometry_blockers(g)
    g['dimension_evidence']['inner_diameter_mm']['state'] = 'post_assembly'
    assert geometry_blockers(g)


def test_optional_unknown_allowance_is_warning_but_negative_is_blocker():
    from server.manufacturing import geometry_blockers
    g = {**fixture()['expected'], 'manufacturing': {'allowances': [
        {'dimension':'inner_diameter_mm','kind':'bore','per_side_mm':None,'faces':2}]}}
    assert not geometry_blockers(g)
    assert build_model(g,{})['validation']['warnings']
    g['manufacturing']['allowances'][0]['per_side_mm'] = -1
    assert geometry_blockers(g)
