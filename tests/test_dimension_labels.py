from server.drawing_standard import dimensional_text


def test_model_explanations_do_not_become_dimension_labels():
    assert dimensional_text(1554, '参考图未直接标注筒体长度公差') == '1554'
    assert dimensional_text(150, '参考尺寸 (Ø150)，无独立公差标注', 'Φ') == 'Φ150'
    assert dimensional_text(125, 'φ125 +0.04/0（两端配合段）', 'Φ') == 'Φ125+0.04/0'


def test_explicit_deviations_and_fits_remain_visible():
    assert dimensional_text(50, '±0.008', 'Φ') == 'Φ50±0.008'
    assert dimensional_text(60, '+0.072/+0.053', 'Φ') == 'Φ60+0.072/+0.053'
    assert dimensional_text(125, 'H7', 'Φ') == 'Φ125H7'
    assert dimensional_text(125, '可能 +0.04/0', 'Φ') == 'Φ125'


def test_allowance_must_agree_with_stock_dimensions():
    from server.manufacturing import prepare_manufacturing
    data = {'inner_diameter_mm': 125, 'manufacturing': {
        'stock_dimensions': {'inner_diameter_mm': 123},
        'allowances': [{'dimension': 'inner_diameter_mm', 'kind': 'bore',
                       'per_side_mm': 8, 'faces': 2, 'basis': '图纸推算', 'reason': '来料与交付尺寸差值'}]}}
    result = prepare_manufacturing(data)
    assert '不一致' in result['manufacturing']['allowances'][0]['error']
    data['manufacturing']['allowances'][0]['per_side_mm'] = 1
    assert prepare_manufacturing(data)['manufacturing']['allowances'][0]['error'] == ''
