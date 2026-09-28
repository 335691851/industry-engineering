from server.result_summary import generation_summary


def test_drawing_summary_contains_results_and_blockers():
    text = generation_summary({'name':'测试件','material':'45','geometry':{
        'shape_type':'tube','outer_diameter_mm':50,'inner_diameter_mm':60,'overall_length_mm':100}},'drawing')
    assert '外径 50 mm' in text and '审核阻塞' in text and '材料：45' in text


def test_process_summary_contains_route_and_inspection():
    text = generation_summary({'name':'测试件','process':{'steps':[
        {'operation':'精车','inspection':'复测配合直径'}],'review_items':['基准待确认']}},'process')
    assert '精车' in text and '复测配合直径' in text and '基准待确认' in text
