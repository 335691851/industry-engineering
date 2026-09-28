"""Non-destructive CAD editing: keep the original document, apply bounded operations."""
import hashlib
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, svg, pymupdf
from fastapi import APIRouter, File, UploadFile, HTTPException
from pydantic import BaseModel, Field

from . import db
from .dwg_converter import read_dwg, convert_dwg, ConversionError
from .workflow import require_generation_stage, invalidate_dependents, pending_geometry, pending_process

router = APIRouter(prefix='/api/parts/{part_id}/cad')
EDITABLE = {'LINE', 'CIRCLE', 'ARC', 'LWPOLYLINE', 'TEXT', 'MTEXT', 'DIMENSION'}


def part_record(part_id):
    with db.connect() as con:
        part = db.unpack(db.row(con, 'SELECT * FROM parts WHERE id=?', (part_id,)))
    if not part: raise HTTPException(404, '零件不存在')
    return part


def gated(part_id):
    part = part_record(part_id)
    try: require_generation_stage(db.project_bundle(part['project_id']), part)
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc
    return part


def session_folder(part, session_id):
    if not re.fullmatch(r'[a-f0-9]{12}', session_id): raise HTTPException(404, 'CAD 会话不存在')
    folder = db.FILES / part['project_id'] / part['id'] / 'cad' / session_id
    from .cloud_context import enabled
    if enabled():
        from .cloud_storage import restore_folder
        restore_folder(f"{part['project_id']}/{part['id']}/cad/{session_id}", ['source.dxf', 'meta.json', 'baseline.dxf'])
    if not (folder / 'source.dxf').is_file(): raise HTTPException(404, 'CAD 会话不存在')
    return folder


def read_document(path):
    if path.suffix.lower() == '.dwg':
        return read_dwg(path)
    return ezdxf.readfile(path)


def finite(value, positive=False):
    if isinstance(value, bool): raise ValueError('坐标必须是数字')
    result = float(value)
    if not math.isfinite(result) or abs(result) > 1e9 or (positive and result <= 0):
        raise ValueError('尺寸或坐标无效')
    return result


def point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2: raise ValueError('二维坐标应包含 X、Y')
    return tuple(finite(v) for v in value)


def apply_operations(doc, layout_name, operations):
    if layout_name not in doc.layouts.names(): raise ValueError('图纸空间不存在')
    space = doc.layouts.get(layout_name)
    handles = {e.dxf.handle: e for e in space}
    for op in operations:
        action = op.get('action'); values = op.get('values') or {}
        if action in ('update', 'delete'):
            entity = handles.get(op.get('handle'))
            if entity is None or not entity.is_alive: raise ValueError('实体不存在或不属于当前空间')
            kind = entity.dxftype()
            if kind not in EDITABLE: raise ValueError('此类实体保留在原图中，暂不支持修改')
            if action == 'delete': space.delete_entity(entity); continue
            if kind == 'LINE':
                for key in ('start', 'end'):
                    if key in values: setattr(entity.dxf, key, point(values[key]))
                if 'length' in values:
                    vector = entity.dxf.end-entity.dxf.start
                    if vector.magnitude < 1e-10: raise ValueError('零长度直线不能调整长度')
                    entity.dxf.end = entity.dxf.start + vector.normalize(finite(values['length'], True))
            elif kind in ('CIRCLE','ARC'):
                if 'center' in values: entity.dxf.center = point(values['center'])
                if 'radius' in values: entity.dxf.radius = finite(values['radius'], True)
                if kind == 'ARC':
                    for key in ('start_angle','end_angle'):
                        if key in values: setattr(entity.dxf,key,finite(values[key]))
            elif kind == 'LWPOLYLINE':
                if 'points' in values:
                    if not 2 <= len(values['points']) <= 2000: raise ValueError('顶点数量应为 2–2000')
                    old=list(entity.get_points('xyseb'))
                    if len(old) != len(values['points']): raise ValueError('编辑顶点时需保留原顶点数量')
                    entity.set_points([(*point(p),*old[i][2:]) for i,p in enumerate(values['points'])],format='xyseb')
                if 'width' in values or 'height' in values:
                    vertices=list(entity.get_points('xyseb'))
                    if any(v[4] for v in vertices): raise ValueError('含圆弧的多段线暂不支持拉伸宽高')
                    xmin=min(v[0] for v in vertices);ymin=min(v[1] for v in vertices)
                    width=max(v[0] for v in vertices)-xmin;height=max(v[1] for v in vertices)-ymin
                    sx=finite(values['width'],True)/width if 'width' in values and width>0 else 1
                    sy=finite(values['height'],True)/height if 'height' in values and height>0 else 1
                    entity.set_points([(xmin+(v[0]-xmin)*sx,ymin+(v[1]-ymin)*sy,*v[2:]) for v in vertices],format='xyseb')
            elif kind in ('TEXT','MTEXT'):
                if 'text' in values:
                    text=str(values['text'])[:2000]
                    if kind == 'TEXT': entity.dxf.text=text
                    else: entity.text=text
                if 'insert' in values: entity.dxf.insert=point(values['insert'])
                if 'height' in values: setattr(entity.dxf,'height' if kind == 'TEXT' else 'char_height',finite(values['height'],True))
            elif kind == 'DIMENSION':
                if 'text' in values: entity.dxf.text=str(values['text'])[:200]
                entity.render()
        elif action == 'add':
            kind=op.get('type'); attrs={'layer':'CAD_EDIT'}
            if 'CAD_EDIT' not in doc.layers: doc.layers.new('CAD_EDIT',dxfattribs={'color':7})
            if kind == 'TEXT': space.add_text(str(values.get('text',''))[:2000],dxfattribs={**attrs,'height':finite(values.get('height',3.5),True),'insert':point(values['insert'])})
            elif kind == 'LINE': space.add_line(point(values['start']),point(values['end']),dxfattribs=attrs)
            elif kind == 'CIRCLE': space.add_circle(point(values['center']),finite(values['radius'],True),dxfattribs=attrs)
            elif kind == 'DIMENSION':
                style={'dimtxt':finite(values.get('height',3.5),True),'dimasz':finite(values.get('height',3.5),True)*.8,'dimdec':3,'dimzin':8}
                space.add_linear_dim(base=point(values['base']),p1=point(values['p1']),p2=point(values['p2']),angle=finite(values.get('angle',0)),text=str(values.get('text','<>'))[:200],override=style,dxfattribs=attrs).render()
            elif kind == 'DIAMETER':
                space.add_diameter_dim(center=point(values['center']),radius=finite(values['radius'],True),angle=45,override={'dimtxt':finite(values.get('height',3.5),True)},dxfattribs=attrs).render()
            else: raise ValueError('不支持的新增实体')
        else: raise ValueError('不支持的编辑操作')
    return space


def snapshot(doc, layout_name):
    space=doc.layouts.get(layout_name)
    backend=svg.SVGBackend()
    Frontend(RenderContext(doc),backend,config=config.Configuration(background_policy=config.BackgroundPolicy.WHITE,color_policy=config.ColorPolicy.BLACK)).draw_layout(space)
    image=backend.get_string(layout.Page(0,0),settings=layout.Settings(output_coordinate_space=2000))
    root=ET.fromstring(image); viewbox=[float(n) for n in root.get('viewBox','0 0 2000 1200').split()]
    matrix=getattr(backend,'transformation_matrix',None)
    if matrix is None: raise ValueError('图纸没有可显示的二维实体')
    origin=matrix.transform((0,0,0)); ax=matrix.transform((1,0,0)); ay=matrix.transform((0,1,0))
    entities=[]; cache=bbox.Cache()
    for entity in list(space)[:5000]:
        kind=entity.dxftype(); fields={}; snaps=[]
        xy=lambda p:[float(p[0]),float(p[1])]
        if kind=='LINE': fields={'start':xy(entity.dxf.start),'end':xy(entity.dxf.end),'length':(entity.dxf.end-entity.dxf.start).magnitude}; snaps=[fields['start'],fields['end']]
        elif kind in ('CIRCLE','ARC'):
            fields={'center':xy(entity.dxf.center),'radius':entity.dxf.radius};snaps=[fields['center']]
            if kind=='ARC': fields.update(start_angle=entity.dxf.start_angle,end_angle=entity.dxf.end_angle)
        elif kind=='LWPOLYLINE':
            fields={'points':[list(p) for p in entity.get_points('xy')]};snaps=fields['points']
            if snaps and not entity.has_arc:
                fields.update(width=max(p[0] for p in snaps)-min(p[0] for p in snaps),height=max(p[1] for p in snaps)-min(p[1] for p in snaps))
        elif kind in ('TEXT','MTEXT'): fields={'insert':xy(entity.dxf.insert),'text':entity.dxf.text if kind=='TEXT' else entity.text,'height':entity.dxf.height if kind=='TEXT' else entity.dxf.char_height}
        elif kind=='DIMENSION': fields={'text':entity.dxf.get('text','<>')}
        try:
            box=bbox.extents([entity],cache=cache)
            if not box.has_data: continue
            a=matrix.transform(box.extmin); b=matrix.transform(box.extmax)
            rect=[min(a.x,b.x),min(a.y,b.y),abs(a.x-b.x),abs(a.y-b.y)]
        except (ValueError,TypeError,AttributeError): continue
        entities.append({'handle':entity.dxf.handle,'type':kind,'layer':entity.dxf.layer,'editable':kind in EDITABLE,'fields':fields,'box':rect,'snaps':snaps})
    return {'svg':image,'viewbox':viewbox,'transform':[ax.x-origin.x,ay.y-origin.y,origin.x,origin.y],
            'entities':entities,'layouts':list(doc.layouts.names()),'layout':layout_name,'units':int(doc.units),
            'total_entities':len(space),'selection_truncated':len(space)>5000}


def start_session(part, path, filename, studio=False):
    session_id=db.uid(); folder=db.FILES/part['project_id']/part['id']/'cad'/session_id
    folder.mkdir(parents=True,exist_ok=True)
    try:
        doc=read_document(path)
        if len(doc.modelspace()) > 100000: raise ValueError('图纸实体过多，请拆分到 10 万个实体以内')
        doc.saveas(folder/'source.dxf')
        meta={'part_revision':part['updated_at'],'filename':filename}
        (folder/'meta.json').write_text(json.dumps(meta),encoding='utf-8')
        result=({'layouts':list(doc.layouts.names()),'units':int(doc.units)} if studio else snapshot(doc,'Model'))
        result.update(conversion_warning=('DWG 已经 LibreDWG 转为 DXF，请对照原件复核复杂标注、填充及布局。' if path.suffix.lower()=='.dwg' else ''),session_id=session_id,filename=filename,revision=hashlib.sha256((folder/'source.dxf').read_bytes()).hexdigest())
        from .cloud_context import enabled
        if enabled():
            from .cloud_storage import publish_folder
            publish_folder(folder)
        return result
    except HTTPException: raise
    except Exception as exc: raise HTTPException(422,'CAD 解析失败：'+str(exc)) from exc


@router.post('/open')
def open_generated(part_id:str):
    part=gated(part_id); path=part.get('drawing_dxf') or part.get('drawing_dwg')
    if not path or not Path(path).is_file(): raise HTTPException(404,'当前对象没有 CAD 文件，请上传 DWG / DXF')
    return start_session(part,Path(path),Path(path).name)


@router.post('/upload')
async def upload(part_id:str,file:UploadFile=File(...)):
    part=gated(part_id); suffix=Path(file.filename or '').suffix.lower()
    if suffix not in ('.dwg','.dxf'): raise HTTPException(422,'请上传 DWG 或 DXF')
    data=await file.read(30*1024*1024+1)
    if len(data)>30*1024*1024: raise HTTPException(413,'图纸不能超过 30 MB')
    folder=db.FILES/part['project_id']/part['id']/'cad'/'uploads';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(db.uid()+suffix);path.write_bytes(data)
    return start_session(part,path,Path(file.filename).name)


class EditRequest(BaseModel):
    revision:str
    layout:str='Model'
    operations:list[dict]=Field(default_factory=list,max_length=1000)


def edited(part,session_id,body):
    folder=session_folder(part,session_id); source=folder/'source.dxf'
    if hashlib.sha256(source.read_bytes()).hexdigest()!=body.revision: raise HTTPException(409,'CAD 版本已变化，请重新加载')
    doc=ezdxf.readfile(source)
    try: apply_operations(doc,body.layout,body.operations)
    except (ValueError,TypeError,KeyError,ezdxf.DXFError) as exc: raise HTTPException(422,str(exc)) from exc
    return folder,doc


@router.post('/{session_id}/preview')
def preview(part_id:str,session_id:str,body:EditRequest):
    folder,doc=edited(part_record(part_id),session_id,body)
    return {**snapshot(doc,body.layout),'session_id':session_id,'revision':body.revision}


@router.post('/{session_id}/save')
def save(part_id:str,session_id:str,body:EditRequest):
    part=gated(part_id); folder,doc=edited(part,session_id,body)
    return persist_document(part,folder,doc,body.layout,body.operations)


def persist_document(part,folder,doc,layout_name,operations):
    part_id=part['id']
    meta=json.loads((folder/'meta.json').read_text(encoding='utf-8'))
    if part['updated_at']!=meta['part_revision']: raise HTTPException(409,'对象在编辑期间已变更，请重新打开 CAD')
    target=folder/'versions'/db.uid();target.mkdir(parents=True)
    dxf=target/'drawing.dxf';doc.saveas(dxf)
    from .cad_artifacts import render_artifacts
    try:
        artifacts=render_artifacts(dxf,layout_name)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    paths={key:artifacts[key] for key in ('drawing_dxf','drawing_pdf','drawing_dwg')}
    warning=artifacts.get('dwg_warning','')
    dwg=paths['drawing_dwg']
    geometry=pending_geometry(part['geometry'])
    geometry['dwg_warning']=warning
    geometry['cad_document']={**paths,'svg':artifacts['svg'],'layout':layout_name,'units':int(doc.units),
                              'filename':meta['filename'],'edited_at':db.now(),'operations':operations,
                              'note':'CAD 为当前图纸依据。实体编辑不自动重算参数化模型；工艺生成须优先读取本 CAD 图纸。'}
    process=pending_process(part['process']);process.pop('pdf_path',None);process.pop('xlsx_path',None)
    with db.connect() as con:
        saved=con.execute('UPDATE parts SET geometry=?,process=?,drawing_pdf=?,drawing_dxf=?,drawing_dwg=?,status=?,updated_at=? WHERE id=? AND updated_at=?',
                         (json.dumps(geometry,ensure_ascii=False),json.dumps(process,ensure_ascii=False),paths['drawing_pdf'],paths['drawing_dxf'],paths['drawing_dwg'],'图纸待审核',db.now(),part_id,part['updated_at']))
        if saved.rowcount!=1: raise HTTPException(409,'对象已变化，本次版本没有覆盖当前成果')
        invalidate_dependents(con,part['project_id'],part_id)
    return {'saved':True,**paths,'dwg_available':bool(dwg)}
