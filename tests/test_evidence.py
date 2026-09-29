import pymupdf
from server import db
from server.evidence import extract, visual_input


def test_pdf_evidence_retains_page_coordinates_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path/'data')
    monkeypatch.setenv('ENGINEERING_OCR', 'off')
    source = tmp_path/'drawing.pdf'
    doc = pymupdf.open()
    doc.new_page(width=300,height=200).insert_text((20,30), 'Diameter 42')
    doc.save(source)
    packet = extract(source)
    token = packet['pages'][0]['tokens'][0]
    assert token['id'] == 'p1-t1'
    assert token['bbox'][0] == 20
    assert extract(source) == packet
    text, images, count = visual_input(source, max_pages=1)
    assert count == 1 and 5 <= len(images) <= 8
    assert 'image_map' in text and packet['sha256'] in text


def test_rotated_ocr_coordinates_map_back_to_source():
    from server.evidence import unrotate_box
    assert unrotate_box([[50,20],[70,20],[70,40],[50,40]],100,200,90,2) == [10,65,20,75]


def test_grounding_does_not_accept_invented_or_wrong_page_citations():
    from server.evidence import ground_dimensions
    packet = {'sha256':'hash','pages':[{'page':2,'tokens':[
        {'id':'p2-t1','text':'125 +0.04/0','bbox':[1,2,3,4],'method':'rapidocr','confidence':.8}]}]}
    result = ground_dimensions({'inner_diameter_mm':150,'dimension_evidence':{
        'inner_diameter_mm':{'page':1,'token_ids':['p2-t1','invented']}}},packet)
    evidence = result['dimension_evidence']['inner_diameter_mm']
    assert evidence['token_ids'] == ['p2-t1']
    assert len(evidence['checks']) == 3
    assert evidence['locations'][0]['page'] == 2
    assert evidence['verified'] is False
    from server.manufacturing import geometry_blockers
    result.update(shape_type='tube', outer_diameter_mm=160, overall_length_mm=500)
    assert any('inner_diameter_mm' in e for e in geometry_blockers(result))
    result['user_overrides'] = {'inner_diameter_mm':150}
    assert not geometry_blockers(result)


def test_grounding_normalizes_common_chamfer_ocr_confusion():
    from server.evidence import ground_dimensions
    packet = {'sha256':'hash','pages':[{'page':1,'tokens':[
        {'id':'p1-t1','text':'CO. 5', 'bbox':[1,2,3,4], 'method':'rapidocr', 'confidence':.8}]}]}
    result = ground_dimensions({'chamfer_mm':.5, 'dimension_evidence':{
        'chamfer_mm':{'page':1, 'token_ids':['p1-t1']}}}, packet)
    assert result['dimension_evidence']['chamfer_mm']['checks'] == []
