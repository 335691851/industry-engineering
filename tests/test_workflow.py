"""End-to-end workflow with deterministic AI responses and an isolated SQLite database."""
import json
import time

import ezdxf
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from server import agent, ai, db, drawing, engineering_skills, main, memory, mbom
from server.drawing_standard import PRIMARY_STANDARD, choose_scale, validate_geometry


def test_upload_mbom_generate_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB', tmp_path / 'workflow.db')
    monkeypatch.setattr(db, 'FILES', tmp_path / 'files')
    monkeypatch.setattr(main, 'FILES', tmp_path / 'files')
    monkeypatch.setattr(drawing, 'FILES', tmp_path / 'files')
    monkeypatch.setattr(agent, '_invoke_specialist',
                        lambda name, prompt, tools, message, recursion_limit=12:
                        (tools[0].invoke({}), tools[1].invoke({})))
    monkeypatch.setattr(ai, 'analyze_assembly', lambda path: ({
        'assembly_name': '测试轴', 'drawing_no': 'A-01', 'technical_requirements': ['热压装配'],
        'parts': [
            {'name': '轴头', 'kind': '部件', 'parent_name': '测试轴', 'quantity': 2, 'material': '45'},
            {'name': '轴', 'kind': '零件', 'parent_name': '轴头', 'quantity': 1, 'material': '45'},
        ], 'review_items': []}, '装配图测试'))
    monkeypatch.setattr(agent, 'refine_mbom', lambda analysis, text: mbom.validate_plan(mbom.candidates_to_plan(analysis)))
    monkeypatch.setattr(ai, 'draft_part', lambda project, part, instruction: {
        'summary': '轮廓草案', 'overall_length_mm': 100,
        'manufacturing': {'allowances': [{'dimension':'overall_length_mm', 'kind':'axial',
                                         'faces':2, 'per_side_mm':None}]},
        'segments': [{'length_mm': None, 'diameter_mm': 40, 'basis': '待确认'},
                     {'length_mm': 60, 'diameter_mm': 40, 'basis': '图纸标注'}],
        'review_items': []})
    monkeypatch.setattr(ai, 'draft_process', lambda project, part, instruction: {
        'title': '工艺流程', 'summary': '数量1×5=5件，需处理', 'steps': [{'seq': 1, 'operation': '加工' if part else '热压装配',
                                    'equipment': '车床', 'description': '=HYPERLINK("https://invalid.example")', 'inspection': '尺寸复核'},
                                   {'seq': 2, 'operation': '成品检验', 'equipment': '', 'description': '核对图纸', 'inspection': '记录'}],
        'review_items': []})
    with TestClient(main.app) as client:
        history_card = db.ROOT / 'sample' / '示例' / '输出' / '收卷轴工艺流程单.xlsx'
        with history_card.open('rb') as stream:
            uploaded_memory = client.post('/api/memory', data={'category': 'process_example'},
                                          files={'file': ('测试轴历史工艺.xlsx', stream,
                                                          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}).json()
        assert uploaded_memory['step_count'] > 0
        assert any(item['id'] == uploaded_memory['id'] for item in client.get('/api/memory').json())
        assert memory.retrieve('测试轴历史工艺', 'process_example', 1)[0]['id'] == uploaded_memory['id']
        rejected = client.post('/api/memory', data={'category': 'process_example'},
                               files={'file': ('bad.xlsx', b'PKinvalid', 'application/octet-stream')})
        assert rejected.status_code == 422
        source = db.ROOT / 'sample' / '示例' / '输入' / '装配体.pdf'
        with source.open('rb') as stream:
            missing_drawing_no = client.post('/api/projects/upload',
                                             files={'file': ('test.pdf', stream, 'application/pdf')})
        assert missing_drawing_no.status_code == 422
        with source.open('rb') as stream:
            created = client.post('/api/projects/upload', data={'name': '测试轴', 'drawing_no': 'USER-01'},
                                  files={'file': ('test.pdf', stream, 'application/pdf')}).json()
        assert created['name'] == '测试轴'
        assert created['drawing_no'] == 'USER-01'
        project_id = created['id']
        analyzed = client.post(f'/api/projects/{project_id}/analyze').json()
        assert analyzed['stage'] == 'MBOM待确认'
        assert analyzed['drawing_no'] == 'USER-01'
        assert len(analyzed['parts']) == 2
        assert next(p for p in analyzed['parts'] if p['name'] == '轴')['parent_id'] == next(p for p in analyzed['parts'] if p['name'] == '轴头')['id']
        drawing_tool = agent._tools(project_id, None)[2]
        assert 'error' in json.loads(drawing_tool.invoke({'target': '轴', 'instruction': '生成草案'}))
        assert 'error' in json.loads(drawing_tool.invoke({'target': '另一个项目的零件', 'instruction': ''}))
        second_head = client.post(f'/api/projects/{project_id}/parts', json={'name': '第二轴头', 'kind': '部件'}).json()
        shaft_id = next(p for p in analyzed['parts'] if p['name'] == '轴')['id']
        reused = client.post(f'/api/projects/{project_id}/mbom-links',
                             json={'parent_id': second_head['id'], 'child_id': shaft_id, 'quantity': 1})
        assert reused.status_code == 200, reused.text
        shaft_uses = [l for l in reused.json()['mbom_links'] if l['child_id'] == shaft_id]
        assert len(shaft_uses) == 2
        assert client.delete(f"/api/mbom-links/{next(l for l in shaft_uses if l['parent_id'] == second_head['id'])['id']}").status_code == 200
        assert client.delete(f"/api/parts/{second_head['id']}").status_code == 200
        extra = client.post(f'/api/projects/{project_id}/parts', json={'name': '误识别件'}).json()
        assert client.delete(f"/api/parts/{extra['id']}").status_code == 200
        assert client.delete(f"/api/parts/{next(p for p in analyzed['parts'] if p['name'] == '轴头')['id']}").status_code == 409
        items = [{key: p[key] for key in ('id', 'name', 'parent_id', 'quantity', 'kind', 'material', 'drawing_no')}
                 for p in analyzed['parts']]
        invalid = [dict(p) for p in items]
        invalid[0]['parent_id'] = invalid[1]['id']
        assert client.patch(f'/api/projects/{project_id}/mbom', json={'parts': invalid}).status_code == 422
        confirmed = client.patch(f'/api/projects/{project_id}/mbom', json={'parts': items}).json()
        assert confirmed['stage'] == 'MBOM已确认'
        assert all(link['confidence'] == '已确认' for link in confirmed['mbom_links'])
        editable_links = [{key: link[key] for key in ('id', 'parent_id', 'child_id', 'quantity')}
                          for link in confirmed['mbom_links']]
        updated_links = client.patch(f'/api/projects/{project_id}/mbom',
                                     json={'parts': items, 'links': editable_links})
        assert updated_links.status_code == 200, updated_links.text
        process_tool = agent._tools(project_id, None)[3]
        generated = None
        for target_name in ('轴', '轴头'):
            current = client.get(f'/api/projects/{project_id}').json()
            target = next(p for p in current['parts'] if p['name'] == target_name)
            approved_reference = client.post(f"/api/parts/{target['id']}/approve/reference", json={})
            assert approved_reference.status_code == 200, approved_reference.text
            generated = json.loads(drawing_tool.invoke({'target': target_name, 'instruction': '生成草案'}))
            assert generated['output'] == 'drawing'
            assert client.post(f"/api/parts/{target['id']}/approve/drawing", json={}).status_code == 200
            generated_process = json.loads(process_tool.invoke({'target': target_name, 'instruction': '生成工艺'}))
            assert generated_process['output'] == 'process'
            assert client.post(f"/api/parts/{target['id']}/approve/process", json={}).status_code == 200

        assert generated and generated['object_name'] == '轴头'
        shaft = next(p for p in client.get(f'/api/projects/{project_id}').json()['parts'] if p['name'] == '轴')
        assert client.get(f"/api/parts/{shaft['id']}/files/pdf").status_code == 200
        generated_dxf = ezdxf.readfile(shaft['drawing_dxf'])
        assert len(generated_dxf.modelspace().query('DIMENSION')) >= 2
        assert {'OUTLINE', 'CENTER', 'DIMENSIONS', 'TEXT', 'REVIEW'} <= {layer.dxf.name for layer in generated_dxf.layers}

        assembly_tool = agent._tools(project_id, None)[4]
        assembly_process = json.loads(assembly_tool.invoke({'instruction': '生成装配工艺'}))
        assert assembly_process['output'] == 'process'
        assert client.post(f'/api/projects/{project_id}/approve-process').status_code == 200
        final = client.get(f'/api/projects/{project_id}').json()
        assert final['stage'] == '草案待审核'
        assert final['analysis']['assembly_process']['pdf_path']
        assert final['analysis']['assembly_process']['xlsx_path']
        assert client.get(f'/api/projects/{project_id}/process.xlsx').status_code == 200
        for part in final['parts']:
            assert part['geometry']['segments'][0]['basis'] == '差值推算'
            assert part['geometry']['approval_status'] == 'approved'
            assert part['process']['approval_status'] == 'approved'
            assert part['drawing_pdf'] and part['drawing_dxf'] and part['process']['pdf_path']
            assert part['process']['xlsx_path']
            assert client.get(f"/api/parts/{part['id']}/files/process-xlsx").status_code == 200
            workbook = load_workbook(part['process']['xlsx_path'], read_only=True, data_only=True)
            assert workbook.active['A8'].value == 1
            assert workbook.active['B8'].value
            assert workbook.active['D8'].data_type == 's'
            assert workbook.active['D8'].value.startswith("'=")
            workbook.close()
            assert '5件' not in part['process']['summary']
            assert client.get(f"/api/parts/{part['id']}/files/dxf").status_code == 200
            if part['drawing_dwg']:
                assert client.get(f"/api/parts/{part['id']}/files/dwg").status_code == 200
            else:
                assert part['geometry'].get('dwg_warning')
        archived = client.post(f"/api/parts/{final['parts'][0]['id']}/save").json()
        assert archived['status'] == '已归档'
        archive = client.post(f'/api/projects/{project_id}/archive').json()
        assert archive['stage'] == '已归档'
        assert all(p['status'] == '已归档' for p in archive['parts'])


def test_drawing_number_does_not_inject_reference_parts():
    plan = mbom.candidates_to_plan({'drawing_no': 'JX,LTJ-04-05-01', 'parts': [
        {'key': 'actual', 'name': '当前实际零件', 'kind': '零件'}]})
    assert [p['key'] for p in plan['parts']] == ['actual']
    assert len(plan['links']) == 1


def test_industrial_skills_follow_step_evidence_and_object_semantics():
    project = {
        'analysis': {'technical_requirements': ['焊后去应力，检查同轴度']},
        'mbom_links': [{'parent_id': 'assembly', 'child_id': 'roller'}],
    }
    roller = {'id': 'roller', 'name': '辊筒', 'kind': '零件', 'material': 'Q355B',
              'geometry': {}, 'specifications': {}}
    drawing = engineering_skills.skill_context('drawing', project, roller, '复核外径公差')
    assert drawing['object_semantics']['family'] == 'general_part'
    assert {'drawing', 'tolerance', 'welding', 'heat_treatment', 'inspection'} <= set(drawing['selected_skills'])
    process = engineering_skills.skill_context('part_process', project, roller, '编制制造流程')
    assert 'machining' in process['selected_skills']
    assert 'drawing' not in process['selected_skills']


def test_unknown_part_uses_generic_boundary_instead_of_reel_template():
    project = {'analysis': {'technical_requirements': []}, 'mbom_links': []}
    part = {'id': 'x', 'name': '异形支架', 'kind': '零件', 'geometry': {}, 'specifications': {}}
    semantics = engineering_skills.object_semantics(project, part)
    assert semantics['family'] == 'general_part'
    assert '轴头' not in semantics['boundary']


def test_drawing_export_uses_iso_profile(tmp_path):
    part = {
        'name': '测试筒体', 'drawing_no': 'ISO-T-001', 'material': 'Q355B',
        'geometry': {'shape_type': 'tube', 'overall_length_mm': 1554,
                     'outer_diameter_mm': 159, 'inner_diameter_mm': 125,
                     'technical_requirements': ['去除毛刺和锐边']},
    }
    svg = drawing.svg_preview(part)
    assert PRIMARY_STANDARD['code'] in svg
    assert 'width="420mm" height="297mm"' in svg
    assert '纵向全剖视图' in svg
    assert '自适应' not in svg
    assert '筒体长度 1554' not in svg
    assert validate_geometry(part) == []
    assert choose_scale(1554, 159, 205, 105)[1] == '1:10'

    output = tmp_path / 'tube.dxf'
    assert drawing.draw_dxf(part, output)
    doc = ezdxf.readfile(output)
    layers = {layer.dxf.name: layer.dxf.lineweight for layer in doc.layers}
    assert layers['OUTLINE'] == 70
    assert layers['CENTER'] == 35
    assert {'HIDDEN', 'HATCH', 'TITLE'} <= set(layers)
