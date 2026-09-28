import json
import math
from pathlib import Path

import ezdxf
import pymupdf
import pytest
from fastapi.testclient import TestClient

from server import agent, db, drawing, main
from server.domain import complete_draft_geometry
from server.manufacturing import prepare_manufacturing, geometry_blockers
from server.workflow import invalidate_dependents, children_ready


def tube():
    return {'shape_type': 'tube', 'overall_length_mm': 100, 'outer_diameter_mm': 50, 'inner_diameter_mm': 30}


def test_no_invented_dimensions_or_proportional_repair():
    for name in ('轴', '闷板', '陌生支架'):
        draft = complete_draft_geometry({}, {'name': name})
        assert not draft.get('segments')
        assert not draft.get('outer_diameter_mm')
        assert geometry_blockers(draft)
    g = {'shape_type': 'rotational', 'overall_length_mm': 100,
         'segments': [{'length_mm': 80, 'diameter_mm': 20}, {'length_mm': 60, 'diameter_mm': 30}]}
    assert [s['length_mm'] for s in complete_draft_geometry(g, {'name': '轴'})['segments']] == [80, 60]
    assert geometry_blockers(g)
    g['segments'][0]['length_mm'] = None
    solved = complete_draft_geometry(g, {'name': '轴'})
    assert solved['segments'][0]['length_mm'] == 40
    assert solved['segments'][0]['basis'] == '差值推算'


def test_allowance_direction_provenance_and_finite_values():
    g = tube()
    g['manufacturing'] = {'allowances': [
        {'dimension': field, 'kind': kind, 'per_side_mm': amount, 'faces': faces,
         'basis': '用户指定', 'reason': '精加工留量'}
        for field, kind, amount, faces in [('outer_diameter_mm', 'external', 1, 2),
                                           ('inner_diameter_mm', 'bore', .5, 2),
                                           ('overall_length_mm', 'axial', 2, 2)]]}
    planned = prepare_manufacturing(g)
    assert [a['blank_mm'] for a in planned['manufacturing']['allowances']] == [52, 29, 104]
    assert planned['outer_diameter_mm'] == 50  # finish geometry unchanged
    g['manufacturing']['allowances'][0]['per_side_mm'] = math.inf
    assert geometry_blockers(g)
    g['manufacturing']['allowances'][0]['per_side_mm'] = 1
    g['manufacturing']['allowances'][0]['reason'] = ''
    assert geometry_blockers(g)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB', tmp_path / 'db.sqlite')
    monkeypatch.setattr(db, 'FILES', tmp_path / 'files')
    monkeypatch.setattr(drawing, 'FILES', tmp_path / 'files')
    db.init()
    with db.connect() as con:
        con.execute('INSERT INTO projects(id,name,source_path,analysis,created_at,stage) VALUES (?,?,?,?,?,?)',
                    ('p', '装配体', '', json.dumps({'assembly_process': {'approval_status':'approved','pdf_path':'root.pdf'}}), db.now(), 'MBOM已确认'))
        for ident in ('a', 'b', 'leaf'):
            con.execute('INSERT INTO parts(id,project_id,name,material,drawing_no,geometry,process,specifications,updated_at,drawing_pdf,status) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, 'p', ident, '45', ident, json.dumps({**tube(), 'approval_status':'approved'}),
                         json.dumps({'approval_status':'approved', 'pdf_path': 'process.pdf', 'steps':[{'operation':'检验'}]}),
                         json.dumps({'reference_approval':{'status':'approved'}}), db.now(), 'drawing.pdf', '已归档'))
        for ident,parent,child in [('1',None,'a'),('2',None,'b'),('3','a','leaf'),('4','b','leaf')]:
            con.execute('INSERT INTO mbom_links(id,project_id,parent_id,child_id) VALUES (?,?,?,?)', (ident,'p',parent,child))
    return 'p'


def test_edit_shared_child_invalidates_both_parents_and_root(project):
    main.patch_part('leaf', main.PartPatch(material='Q355B'))
    current = db.project_bundle(project)
    for p in current['parts']:
        assert p['geometry']['approval_status'] == 'pending'
        assert p['process']['approval_status'] == 'pending'
        assert not p['process'].get('pdf_path')
    assert not current['analysis']['assembly_process'].get('pdf_path')
    assert not children_ready(current, 'a')[0]
    with pytest.raises(Exception): main.save_part('a')


def test_api_cannot_forge_approvals_and_buttons_share_stage_gate(project):
    with TestClient(main.app) as client:
        edited = client.patch('/api/parts/leaf', json={'geometry': {**tube(), 'outer_diameter_mm':52, 'approval_status':'approved'},
                                                      'specifications': {'reference_approval': {'status':'approved'}}})
        assert edited.status_code == 200
        assert edited.json()['geometry']['approval_status'] == 'pending'
        response = client.post('/api/parts/leaf/generate/process', json={})
        assert response.status_code == 409
        with db.connect() as con:
            con.execute("UPDATE projects SET stage='MBOM待确认' WHERE id='p'")
        assert client.post('/api/parts/leaf/generate/drawing', json={}).status_code == 409


def test_invalid_geometry_cannot_be_approved(project):
    with TestClient(main.app) as client:
        client.patch('/api/parts/leaf', json={'geometry':{**tube(), 'inner_diameter_mm':100}})
        response = client.post('/api/parts/leaf/approve/drawing', json={})
        assert response.status_code == 409


def test_repeated_approval_preserves_downstream_and_exports_reviewed_state(project, monkeypatch):
    monkeypatch.setattr(main, 'resume_agent_workflow', lambda _: None)
    with TestClient(main.app) as client:
        before = main.require_part('leaf')
        assert client.post('/api/parts/leaf/approve/drawing', json={}).status_code == 200
        assert main.require_part('leaf')['process'] == before['process']
        client.patch('/api/parts/leaf', json={'geometry': {**tube(), 'outer_diameter_mm': 52}})
        assert client.post('/api/parts/leaf/approve/drawing', json={}).status_code == 200
        approved = main.require_part('leaf')
        with pymupdf.open(approved['drawing_pdf']) as pdf:
            assert '已审核' in ''.join(page.get_text() for page in pdf)


def test_exports_share_all_notes_and_sheet_count(tmp_path):
    text = '零件长技术要求末尾必须保留'
    part = {'name':'筒体', 'material':'45', 'drawing_no':'QA', 'geometry':{
        **tube(), 'technical_requirements':[f'{i}：'+text*8 for i in range(30)]}}
    pdf = tmp_path/'drawing.pdf'; dxf = tmp_path/'drawing.dxf'
    drawing.draw_pdf(part,pdf); assert drawing.draw_dxf(part,dxf)
    document = pymupdf.open(pdf)
    combined = ''.join(page.get_text() for page in document)
    assert '29' in combined and text in combined
    assert len(document)>1
    svg=drawing.svg_preview(part)
    assert text in svg and '29' in svg
    cad=ezdxf.readfile(dxf)
    sheets=[layout for layout in cad.layouts if layout.name.startswith('A3-')]
    assert len(sheets)==len(document)
    assert any('29' in e.dxf.text for layout in sheets for e in layout.query('TEXT'))


def test_conversation_parameter_tool_uses_same_validation(project):
    tool = next(t for t in agent._tools(project,'leaf') if t.name=='update_part_parameters')
    result = json.loads(tool.invoke({'patch_json':json.dumps({'geometry':{'outer_diameter_mm':54}})}))
    assert 'error' not in result
    assert result['geometry']['outer_diameter_mm']==54
    assert result['geometry']['user_overrides']['outer_diameter_mm']==54
    assert result['geometry']['approval_status']=='pending'


def test_cad_load_edit_native_dimensions_save_and_preserve_entities(project, tmp_path, monkeypatch):
    from server import cad_editor
    monkeypatch.setattr(cad_editor, 'convert_dwg', lambda _: None)
    doc=ezdxf.new('R2018',setup=True);doc.units=4;msp=doc.modelspace()
    line=msp.add_line((0,0),(100,0));msp.add_circle((50,20),10)
    msp.add_ellipse((30,40),major_axis=(20,0),ratio=.5)
    path=tmp_path/'input.dxf';doc.saveas(path)
    with TestClient(main.app) as client:
        response=client.post('/api/parts/leaf/cad/upload',files={'file':('input.dxf',path.read_bytes())})
        assert response.status_code==200,response.text
        session=response.json();assert session['units']==4
        body={'revision':session['revision'],'layout':'Model','operations':[
            {'action':'update','handle':line.dxf.handle,'values':{'length':120}},
            {'action':'add','type':'DIMENSION','values':{'p1':[0,0],'p2':[120,0],'base':[0,-15]}},
            {'action':'add','type':'TEXT','values':{'insert':[0,45],'text':'加工基准','height':3.5}}]}
        prefix='/api/parts/leaf/cad/'+session['session_id']
        preview=client.post(prefix+'/preview',json=body)
        assert preview.status_code==200,preview.text
        assert any(e['type']=='DIMENSION' for e in preview.json()['entities'])
        saved=client.post(prefix+'/save',json=body)
        assert saved.status_code==200,saved.text
        restored=ezdxf.readfile(saved.json()['drawing_dxf']);space=restored.modelspace()
        assert len(space.query('ELLIPSE'))==1
        assert (space.query('LINE')[0].dxf.end-space.query('LINE')[0].dxf.start).magnitude==120
        assert len(space.query('DIMENSION'))==1
        assert main.require_part('leaf')['geometry']['approval_status']=='pending'
        assert not main.require_part('a')['drawing_pdf']
        assert client.post(prefix+'/save',json=body).status_code==409
        body['operations']=[{'action':'update','handle':line.dxf.handle,'values':{'length':-1}}]
        assert client.post(prefix+'/preview',json=body).status_code==422


def test_broken_tube_large_view_real_dimensions_and_no_reasoning(tmp_path):
    from server.drawing_iso import make_scenes
    part={'name':'辊筒','drawing_no':'QA-T','material':'45','geometry':{
        'shape_type':'tube','overall_length_mm':1554,'outer_diameter_mm':159,'inner_diameter_mm':125,
        'inner_tolerance':'+0.04/0','chamfer_mm':.5,
        'machining_zones':[{'end':'left','length_mm':270},{'end':'right','length_mm':270}],
        'review_items':['不应该堆积在制造图上的推理文字'],'technical_requirements':['去毛刺。']}}
    scenes=make_scenes(part);assert len(scenes)==1
    content=drawing.svg_preview(part)
    assert '1554' in content and '270' in content and '+0.04' in content
    assert '断开' in content and '推理文字' not in content
    assert any(i['type']=='text' and i.get('rotation')==-90 for i in scenes[0].items)
    path=tmp_path/'tube.dxf';drawing.draw_dxf(part,path)
    cad=ezdxf.readfile(path)
    # Modelspace remains full length: a broken sheet is never a shortened part.
    from ezdxf import bbox
    outlines=cad.modelspace().query('LWPOLYLINE[layer=="OUTLINE"]')
    assert bbox.extents(outlines).size.x==1554
