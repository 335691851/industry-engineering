import io
import subprocess
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient
from test_engineering_contracts import project
from server import main, cad_editor
from server.cad_studio import merge_edits


def test_rendered_optional_defaults_are_not_block_edits():
    from server.cad_studio import signature
    doc = ezdxf.new()
    first = doc.modelspace().add_line((0, 0), (10, 0))
    second = first.copy()
    second.dxf.extrusion = (0, 0, 1)
    second.dxf.color = 256
    second.dxf.ltscale = 1
    second.dxf.invisible = 0
    second.dxf.lineweight = -1
    assert signature(first) == signature(second)
    second.dxf.end = (11, 0, 0)
    assert signature(first) != signature(second)


def text(doc):
    output = io.StringIO(); doc.write(output); return output.getvalue()


def test_studio_roundtrip_real_engine_and_preservation(project, tmp_path, monkeypatch):
    monkeypatch.setattr(cad_editor, 'convert_dwg', lambda _: None)
    original = ezdxf.new('R2018', setup=True)
    line = original.modelspace().add_line((0, 0), (100, 0))
    original.modelspace().add_ellipse((40, 30), (20, 0), .5)
    paper = original.layouts.new('Manufacturing')
    paper.add_text('PRESERVE ORIGINAL LAYOUT', dxfattribs={'height':3})
    original.saveas(tmp_path/'source.dxf')
    # Run the actual installed JavaScript DXF serializer, not a mocked export.
    script = """
const fs=require('fs'); const {AcDbDatabase,AcDbLine,AcGePoint3d}=require('@mlightcad/data-model');
(async()=>{let d=new AcDbDatabase(); let bytes=fs.readFileSync(process.argv[1]);
await d.read(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),{readOnly:false},'dxf');
fs.writeFileSync(process.argv[2],d.dxfOut());
const line=[...d.tables.blockTable.modelSpace.newIterator()].find(e=>e.type==='Line');
line.endPoint={x:125,y:0,z:0};
fs.writeFileSync(process.argv[3],d.dxfOut());})().catch(e=>{console.error(e);process.exit(1)});
"""
    subprocess.run(['node','-e',script,str(tmp_path/'source.dxf'),str(tmp_path/'baseline.dxf'),str(tmp_path/'edited.dxf')],check=True,cwd=Path(__file__).resolve().parents[1])
    baseline = ezdxf.readfile(tmp_path/'baseline.dxf')
    edited = ezdxf.readfile(tmp_path/'edited.dxf')
    edited.modelspace().add_linear_dim(base=(0,-10),p1=(0,0),p2=(125,0),override={'dimpost':'<>'}).render()
    with TestClient(main.app) as client:
        prefix='/api/parts/leaf/cad/studio'
        result=client.post(prefix+'/upload',files={'file':('sample.dxf',(tmp_path/'source.dxf').read_bytes())})
        assert result.status_code==200,result.text
        session=result.json(); prefix+='/'+session['session_id']
        assert 'svg' not in session  # opening never renders the expensive server SVG
        assert client.get(prefix+'/source').status_code==200
        body={'revision':session['revision'],'dxf':text(baseline)}
        assert client.post(prefix+'/baseline',json=body).status_code==200
        assert client.post(prefix+'/baseline',json=body).status_code==409
        body['dxf']=text(edited)
        empty=client.post(prefix+'/save',json={**body,'layout':'Layout1'})
        assert empty.status_code==422,empty.text
        assert '布局' in empty.json()['detail']
        result=client.post(prefix+'/save',json=body)
        assert result.status_code==200,result.text
        saved=ezdxf.readfile(result.json()['drawing_dxf'])
        assert saved.modelspace().query('LINE')[0].dxf.end.x==125
        assert len(saved.modelspace().query('ELLIPSE'))==1
        assert len(saved.modelspace().query('DIMENSION'))==1
        assert len(saved.layouts.get('Manufacturing').query('TEXT'))==1
        assert main.require_part('leaf')['geometry']['approval_status']=='pending'
        assert client.post(prefix+'/save',json=body).status_code==409
        # Reopening the saved version establishes a new baseline for another save.
        new_session=client.post('/api/parts/leaf/cad/studio/open',json={}).json()
        prefix='/api/parts/leaf/cad/studio/'+new_session['session_id']
        body={'revision':new_session['revision'],'dxf':text(saved)}
        assert client.post(prefix+'/baseline',json=body).status_code==200
        saved.modelspace().query('LINE')[0].dxf.end=(140,0,0)
        result=client.post(prefix+'/save',json={**body,'dxf':text(saved)})
        assert result.status_code==200,result.text
        assert ezdxf.readfile(result.json()['drawing_dxf']).modelspace().query('LINE')[0].dxf.end.x==140


def test_merge_keeps_unrepresented_entities_and_detects_unmapped_edits():
    original=ezdxf.new();line=original.modelspace().add_line((0,0),(10,0))
    original.modelspace().add_point((3,4))
    baseline=ezdxf.read(io.StringIO(text(original)))
    baseline.modelspace().delete_entity(baseline.modelspace().query('POINT')[0])
    edited=ezdxf.read(io.StringIO(text(baseline)))
    edited.modelspace().delete_entity(edited.modelspace().query('LINE')[0])
    assert merge_edits(original,baseline,edited)==1
    assert len(original.modelspace().query('LINE'))==0
    assert len(original.modelspace().query('POINT'))==1
