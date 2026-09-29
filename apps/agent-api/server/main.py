import json
import os
import shutil
from difflib import SequenceMatcher
from threading import Thread
from .cloud_context import enabled as cloud_enabled
from pathlib import Path

import pymupdf
import openpyxl
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, agent
from .db import FILES, ROOT, add_message, connect, init, now, project_bundle, row, rows, uid, unpack
from .drawing import export_drawing, export_process_pdf, export_process_xlsx, svg_preview
from .dwg_converter import available as dwg_available
from .domain import check_geometry, check_process, normalize_material
from .domain import complete_draft_geometry
from .memory import process_steps_from_xlsx, seed as seed_memory
from .mbom import validate_plan
from .cad_import import cad_to_pdf
from .workflow import (APPROVED, children_ready, drawing_approved,
                       pending_geometry, pending_process, process_approved,
                       reference_approved, require_generation_stage, invalidate_dependents)
from .manufacturing import geometry_blockers, prepare_manufacturing

app = FastAPI(title='工程解析平台', version='0.1.0')


@app.middleware('http')
async def refresh_entry_documents(request, call_next):
    response = await call_next(request)
    if request.url.path in {'/', '/index.html', '/cad-studio.html'}:
        response.headers['Cache-Control'] = 'no-cache'
    return response


class ChatRequest(BaseModel):
    project_id: str
    mode: str = Field(default='agent', pattern='^(agent|drawing|process)$')
    message: str = Field(min_length=1, max_length=5000)
    part_id: str | None = None
    action: str | None = Field(default=None, pattern='^(generate|ask)$')


class PartPatch(BaseModel):
    name: str | None = None
    drawing_no: str | None = None
    parent_id: str | None = None
    material: str | None = None
    kind: str | None = None
    quantity: float | None = Field(default=None, gt=0)
    geometry: dict | None = None
    specifications: dict | None = None


class NewPart(BaseModel):
    name: str
    drawing_no: str = ''
    parent_id: str | None = None
    material: str = ''
    kind: str = '零件'
    quantity: float = Field(default=1, gt=0)


class MbomItem(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    quantity: float = Field(gt=0)
    kind: str = '零件'
    material: str = ''
    drawing_no: str = ''


class MbomLinkItem(BaseModel):
    id: str
    parent_id: str | None = None
    child_id: str
    quantity: float = Field(gt=0)


class MbomPatch(BaseModel):
    parts: list[MbomItem]
    links: list[MbomLinkItem] | None = None


class NewMbomLink(BaseModel):
    parent_id: str | None = None
    child_id: str
    quantity: float = Field(default=1, gt=0)


class ProcessPatch(BaseModel):
    title: str = ''
    material: str = ''
    summary: str = ''
    steps: list[dict] = []
    review_items: list[str] = []


class ApprovalRequest(BaseModel):
    reference_id: str = ''
    note: str = ''


class GenerationRequest(BaseModel):
    instruction: str = Field(default='', max_length=5000)


@app.post('/api/parts/{part_id}/generate/{stage}')
def generate_part_stage(part_id: str, stage: str, body: GenerationRequest):
    if stage not in {'drawing', 'process'}:
        raise HTTPException(404, '生成阶段不存在')
    part = require_part(part_id)
    project = require_project(part['project_id'])
    try:
        require_generation_stage(project, part, process=stage == 'process')
        result = (agent._save_part_drawing if stage == 'drawing' else agent._save_process)(project, part, body.instruction)
    except ValueError as exc:
        add_message(project['id'], 'assistant', 'workflow', f"{part['name']} 生成未完成。原因：{exc}\n请处理以上问题后重试。", part_id)
        raise HTTPException(409, str(exc))
    except Exception as exc:
        add_message(project['id'], 'assistant', 'workflow', f"{part['name']} 生成失败。原因：{str(exc)[:300]}\n请检查模型连接或稍后重试；本次未完成生成。", part_id)
        raise HTTPException(502, f'专业 Agent 生成失败：{str(exc)[:300]}')
    from .result_summary import generation_summary
    add_message(project['id'], 'assistant', 'workflow', generation_summary(require_part(part_id), stage), part_id)
    return {'result': result, 'project': require_project(project['id'])}


def require_project(project_id):
    project = project_bundle(project_id)
    if not project:
        raise HTTPException(404, '项目不存在')
    return project


def require_part(part_id):
    with connect() as con:
        part = row(con, 'SELECT * FROM parts WHERE id=?', (part_id,))
    if not part:
        raise HTTPException(404, '零件不存在')
    return unpack(part)


def safe_file(path):
    p = Path(path)
    if cloud_enabled() and not p.resolve().is_relative_to(FILES.resolve()):
        raise HTTPException(403, '文件不属于当前云端工作区')
    if not p.is_file():
        raise HTTPException(404, '文件不存在')
    if cloud_enabled():
        from .cloud_uploads import signed_url
        from .cloud_storage import object_key
        from starlette.responses import RedirectResponse
        return RedirectResponse(signed_url(object_key(p)),status_code=302)
    return FileResponse(p, filename=p.name, content_disposition_type='inline')


def attach_process_files(project, part, process):
    process['pdf_path'] = str(export_process_pdf(project, part, process))
    process['xlsx_path'] = str(export_process_xlsx(project, part, process))
    return process


def seed_example():
    source = ROOT / 'sample' / '示例' / '输入' / '装配体.pdf'
    if not source.is_file():
        return
    sample_card = ROOT / 'sample' / '示例' / '输出' / '收卷轴工艺流程单.xlsx'
    reference_process = None
    if sample_card.is_file():
        sheet = openpyxl.load_workbook(sample_card, read_only=True, data_only=True).active
        steps = []
        for values in sheet.values:
            if isinstance(values[0], int):
                steps.append({'seq': values[0], 'operation': str(values[1] or ''),
                              'equipment': '', 'description': str(values[2] or ''),
                              'inspection': '按图纸及工艺要求检验', 'basis': '附件示例工艺卡',
                              'review_required': True})
        reference_process = {'title': '收卷轴工艺流程单（附件示例）', 'summary': '从附件工艺卡读取的参考流程',
                             'steps': steps, 'review_items': ['示例流程仅供参考，参数需工程师复核']}
    with connect() as con:
        existing_sample = row(con, 'SELECT analysis FROM projects WHERE id=?', ('sample-reel',))
        if existing_sample:
            analysis = json.loads(existing_sample['analysis'] or '{}')
            if reference_process and 'assembly_process' not in analysis:
                analysis['assembly_process'] = reference_process
                con.execute('UPDATE projects SET analysis=? WHERE id=?',
                            (json.dumps(analysis, ensure_ascii=False), 'sample-reel'))
            con.execute("UPDATE parts SET parent_id='axis-one' WHERE project_id='sample-reel' AND name='闷板1' AND parent_id IS NULL")
            con.execute("UPDATE parts SET parent_id='axis-two' WHERE project_id='sample-reel' AND name='闷板2' AND parent_id IS NULL")
            con.execute("UPDATE projects SET stage='MBOM待确认' WHERE id='sample-reel' AND stage='待解析'")
            return
        doc = pymupdf.open(source)
        source_text = doc[0].get_text(sort=True)
        analysis = {
            'assembly_name': '收卷轴', 'drawing_no': 'JX.LTJ-04-05-01', 'material': '45',
            'technical_requirements': [
                '去毛刺，锐边倒钝', '轴头和闷板、闷板和辊筒之间过盈配合，热压装配',
                '保留两端中心孔', '辊子静置不回转', '焊缝加工后不得有气孔、焊渣等缺陷',
                '加工后填写质检报告', '表面镀铬处理'],
            'dimensions': [{'label': s, 'value': s, 'evidence': '装配体 PDF 可提取标注'} for s in
                           ['1971.50', '1680 ±0.2', '1550', '⌀150 ±0.03', '⌀50 ±0.008', 'H7/s6']],
            'review_items': ['示例零件的完整尺寸以附件原始部件图为准', '型谱与父子关系需工程师核对'],
            'provenance': '用户提供的示例装配图与拆解部件图'
        }
        if reference_process:
            analysis['assembly_process'] = reference_process
        con.execute('INSERT INTO projects (id,name,drawing_no,source_path,source_text,analysis,created_at) VALUES (?,?,?,?,?,?,?)',
                    ('sample-reel', '收卷轴', 'JX.LTJ-04-05-01', str(source), source_text, json.dumps(analysis, ensure_ascii=False), now()))
        parts_dir = ROOT / 'sample' / '示例' / '输出' / '拆解的部件图'
        names = [
            ('收卷轴-轴头1.pdf', '轴头1', '部件', None),
            ('收卷轴-轴头2.pdf', '轴头2', '部件', None),
            ('收卷轴-辊筒.pdf', '辊筒', '零件', None),
            ('收卷轴-轴头1-轴.pdf', '轴头1-轴', '零件', 'axis-one'),
            ('收卷轴-轴头2-轴.pdf', '轴头2-轴', '零件', 'axis-two'),
            ('收卷轴-轴头-闷板1.pdf', '闷板1', '零件', 'axis-one'),
            ('收卷轴-轴头-闷板2.pdf', '闷板2', '零件', 'axis-two'),
        ]
        ids = {'轴头1': 'axis-one', '轴头2': 'axis-two'}
        for filename, name, kind, parent in names:
            if not (parts_dir / filename).is_file():
                continue
            part_id = ids.get(name, uid())
            con.execute('INSERT INTO parts (id,project_id,parent_id,name,drawing_no,kind,material,status,specifications,geometry,process,source_pdf,drawing_pdf,drawing_dxf,drawing_dwg,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (part_id, 'sample-reel', parent, name, '', kind, '45' if '轴' in name else '',
                         '示例参考', '{}', '{}', '{}', str(parts_dir / filename), '', '', '', now()))
    add_message('sample-reel', 'assistant', 'drawing', '已载入示例装配图与 7 张拆解部件图。选择部件后可生成新的可编辑草图。')
    with connect() as con:
        con.execute("UPDATE projects SET stage='MBOM待确认' WHERE id='sample-reel'")


@app.on_event('startup')
def startup():
    init()
    if not cloud_enabled() and os.getenv('ENGINEERING_LOAD_SAMPLE_DATA') == '1':
        seed_memory()
        seed_example()
        init()


@app.get('/api/status')
def status():
    return {'ai_configured': bool(os.getenv('DEEPSEEK_API_KEY') or ai.os.getenv('DEEPSEEK_API_KEY')),
            'dwg_available': dwg_available(), 'vision_model': os.getenv('DEEPSEEK_VISION_MODEL', 'deepseek-flash'),
            'agent_framework': 'DeepAgents'}


@app.get('/api/deployment')
def deployment():
    return {'runtime': 'local', 'authentication': False}


@app.get('/api/projects')
def list_projects():
    with connect() as con:
        return rows(con, '''SELECT p.id,p.name,p.drawing_no,p.created_at,COUNT(t.id) AS part_count
                            FROM projects p LEFT JOIN parts t ON t.project_id=p.id
                            GROUP BY p.id ORDER BY p.created_at DESC''')


@app.delete('/api/business-data')
def delete_business_data():
    from .data_reset import clear_business_data
    return clear_business_data()


@app.get('/api/projects/{project_id}')
def get_project(project_id: str):
    return require_project(project_id)


@app.post('/api/projects/upload')
async def upload_project(file: UploadFile = File(...), name: str = Form(''), drawing_no: str = Form(...)):
    drawing_no = drawing_no.strip()[:80]
    if not drawing_no:
        raise HTTPException(422, '上传装配图时必须填写图号')
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.pdf', '.dwg', '.dxf'}:
        raise HTTPException(422, '装配图仅支持 PDF、DWG、DXF')
    payload = await file.read(30 * 1024 * 1024 + 1)
    if len(payload) > 30 * 1024 * 1024:
        raise HTTPException(413, 'PDF 不得超过 30 MB')
    if suffix == '.pdf' and not payload.startswith(b'%PDF'):
        raise HTTPException(422, '文件不是有效 PDF')
    if suffix == '.dwg' and not dwg_available():
        raise HTTPException(422, 'DWG 自动解析需安装 LibreDWG 转换工具。')
    project_id = uid()
    folder = FILES / project_id
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / f'assembly{suffix}'
    original.write_bytes(payload)
    path = original if suffix == '.pdf' else folder / 'assembly.pdf'
    try:
        if suffix != '.pdf':
            cad_to_pdf(original, path)
        doc = pymupdf.open(path)
        source_text = '\n'.join(page.get_text(sort=True) for page in doc[:3])[:14000]
    except Exception:
        original.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise HTTPException(422, '图纸无法解析，请检查格式及内容')
    name = (name.strip() or Path(file.filename or '新装配体').stem)[:80]
    upload_meta = {'manual_name': name, 'manual_drawing_no': drawing_no}
    if suffix != '.pdf':
        upload_meta['source_cad_path'] = str(original)
    with connect() as con:
        con.execute('INSERT INTO projects (id,name,drawing_no,source_path,source_text,analysis,created_at) VALUES (?,?,?,?,?,?,?)',
                    (project_id, name, drawing_no, str(path), source_text,
                     json.dumps(upload_meta, ensure_ascii=False), now()))
    return require_project(project_id)


@app.post('/api/projects/{project_id}/analyze')
def analyze(project_id: str):
    project = require_project(project_id)
    if any(p['drawing_pdf'] or p['process'].get('pdf_path') or p['status'] == '已归档' for p in project['parts']):
        raise HTTPException(409, '已有生成成果；请新建项目解析，避免覆盖已编辑图纸和工艺')
    try:
        analysis, source_text = ai.analyze_assembly(Path(project['source_path']))
        plan = agent.refine_mbom(analysis, source_text)
    except Exception as exc:
        raise HTTPException(502, f'AI 解析失败：{str(exc)[:240]}')
    name = str(project['analysis'].get('manual_name') or analysis.get('assembly_name') or project['name'])[:80]
    if project['analysis'].get('source_cad_path'):
        analysis['source_cad_path'] = project['analysis']['source_cad_path']
    drawing_no = str(project['analysis'].get('manual_drawing_no') or analysis.get('drawing_no') or project['drawing_no'] or '')[:80]
    analysis['manual_name'] = name
    analysis['manual_drawing_no'] = drawing_no
    analysis['parts'] = plan['parts']
    analysis['links'] = plan['links']
    analysis['mbom_audit'] = plan.get('audit_note', '多级 MBOM 已通过拓扑校验。')
    if plan.get('audit_note') and '需人工复核' in plan['audit_note']:
        analysis.setdefault('review_items', []).append(plan['audit_note'])
    analysis.setdefault('review_items', []).append('MBOM 装配关系为工程草案；复用位置、每上级用量和材料需根据正式图纸复核。')
    with connect() as con:
        con.execute('DELETE FROM mbom_links WHERE project_id=?', (project_id,))
        con.execute('DELETE FROM parts WHERE project_id=?', (project_id,))
        con.execute('UPDATE projects SET name=?,drawing_no=?,source_text=?,analysis=? WHERE id=?',
                    (name, drawing_no, source_text, json.dumps(analysis, ensure_ascii=False), project_id))
        identifiers = {item['key']: uid() for item in plan['parts']}
        first_link = {}
        for link in plan['links']:
            first_link.setdefault(link['child_key'], link)
        for item in plan['parts']:
            part_name = str(item['name']).strip()[:80]
            part_id = identifiers[item['key']]
            link = first_link[item['key']]
            parent_id = identifiers.get(link['parent_key']) if link['parent_key'] else None
            source_pdf = ''  # Reference input is selected and approved by the engineer.
            con.execute('INSERT INTO parts (id,project_id,parent_id,name,drawing_no,kind,material,status,specifications,geometry,process,source_pdf,drawing_pdf,drawing_dxf,drawing_dwg,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (part_id, project_id, parent_id, part_name, str(item.get('drawing_no') or '')[:80],
                         str(item.get('kind') or '零件')[:20], str(item.get('material') or '')[:80],
                         '待审核', json.dumps({'evidence': item.get('evidence'), 'confidence': item.get('confidence'), 'mbom_key': item['key']}, ensure_ascii=False),
                         '{}', '{}', source_pdf, '', '', '', now()))
            con.execute('UPDATE parts SET quantity=? WHERE id=?', (float(link['quantity']), part_id))
        for link in plan['links']:
            con.execute('INSERT INTO mbom_links VALUES (?,?,?,?,?,?,?)',
                        (uid(), project_id, identifiers.get(link['parent_key']) if link['parent_key'] else None,
                         identifiers[link['child_key']], float(link['quantity']),
                         str(link.get('evidence') or '')[:500], str(link.get('confidence') or '待复核')[:20]))
        con.execute("UPDATE projects SET stage='MBOM待确认' WHERE id=?", (project_id,))
    add_message(project_id, 'assistant', 'agent',
                f'已建立多级 MBOM 草案：{len(plan["parts"])} 种零部件、{len(plan["links"])} 条装配使用关系。请逐级核对复用、用量和待复核项。')
    return require_project(project_id)


@app.post('/api/projects/{project_id}/parts')
def create_part(project_id: str, body: NewPart):
    require_project(project_id)
    if body.parent_id:
        parent = require_part(body.parent_id)
        if parent['project_id'] != project_id:
            raise HTTPException(422, '上级部件不属于此项目')
    part_id = uid()
    with connect() as con:
        con.execute('INSERT INTO parts (id,project_id,parent_id,name,drawing_no,kind,material,status,specifications,geometry,process,source_pdf,drawing_pdf,drawing_dxf,drawing_dwg,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (part_id, project_id, body.parent_id, body.name[:80], body.drawing_no[:80],
                     body.kind[:20], body.material[:80], '待审核', '{}', '{}', '{}', '', '', '', '', now()))
        con.execute('UPDATE parts SET quantity=? WHERE id=?', (body.quantity, part_id))
        con.execute('INSERT INTO mbom_links VALUES (?,?,?,?,?,?,?)',
                    (uid(), project_id, body.parent_id, part_id, body.quantity, '工程师手动添加', '用户指定'))
        invalidate_dependents(con, project_id, topology=True)
        if body.parent_id:
            con.execute("UPDATE parts SET kind='部件' WHERE id=?", (body.parent_id,))
    return require_part(part_id)


@app.delete('/api/parts/{part_id}')
def delete_part(part_id: str):
    part = require_part(part_id)
    if part['status'] == '已归档':
        raise HTTPException(409, '已归档对象不能从 MBOM 中直接删除')
    with connect() as con:
        child = row(con, 'SELECT id FROM mbom_links WHERE parent_id=? LIMIT 1', (part_id,))
        if child:
            raise HTTPException(409, '请先移除或调整下级零件')
        invalidate_dependents(con, part['project_id'], topology=True)
        con.execute('DELETE FROM parts WHERE id=?', (part_id,))
    return {'deleted': part_id}


@app.patch('/api/parts/{part_id}')
def patch_part(part_id: str, body: PartPatch):
    part = require_part(part_id)
    updates = body.model_dump(exclude_unset=True)
    updates = {key: value for key, value in updates.items() if value != part.get(key)}
    if not updates:
        return part
    if 'specifications' in updates:
        specs = dict(updates['specifications'] or {})
        specs.pop('reference_approval', None)
        if (part.get('specifications') or {}).get('reference_approval'):
            specs['reference_approval'] = part['specifications']['reference_approval']
        updates['specifications'] = specs
    if any(k in updates for k in ('material', 'drawing_no', 'name', 'kind')) and 'geometry' not in updates:
        updates['geometry'] = part['geometry']
    if 'geometry' in updates:
        incoming = dict(updates['geometry'] or {})
        incoming.pop('cad_document', None)  # Only CAD save endpoints may create this server-owned reference.
        overrides = dict((part.get('geometry') or {}).get('user_overrides') or {})
        for key, value in incoming.items():
            if key not in {'approval_status', 'approved_at', 'user_overrides', 'drawing_validation', 'drawing_standard', 'cad_document'} and value != part['geometry'].get(key):
                overrides[key] = value
        incoming['user_overrides'] = overrides
        updates['geometry'] = pending_geometry(check_geometry(prepare_manufacturing(incoming)))
        updates['process'] = pending_process(part.get('process'))
        updates['process'].pop('pdf_path', None)
        updates['process'].pop('xlsx_path', None)
    primary_link = None
    if 'parent_id' in updates or 'quantity' in updates:
        project = require_project(part['project_id'])
        primary_link = next((l for l in project['mbom_links'] if l['child_id'] == part_id), None)
        if primary_link:
            candidate_links = [dict(l) for l in project['mbom_links']]
            match = next(l for l in candidate_links if l['id'] == primary_link['id'])
            match['parent_id'] = updates.get('parent_id', match['parent_id'])
            match['quantity'] = updates.get('quantity', match['quantity'])
            try:
                validate_plan({'parts': [{'key': p['id'], 'kind': p['kind']} for p in project['parts']],
                               'links': [{'parent_key': l['parent_id'], 'child_key': l['child_id'],
                                          'quantity': l['quantity']} for l in candidate_links]})
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc))
    allowed = {'name','drawing_no','parent_id','material','kind','quantity','geometry','specifications','process'}
    if primary_link and 'specifications' in updates:
        updates['specifications'].pop('reference_approval', None)
    with connect() as con:
        invalidate_dependents(con, part['project_id'], part_id, topology=bool(primary_link))
        if primary_link:
            con.execute('UPDATE mbom_links SET parent_id=?,quantity=? WHERE id=?',
                        (updates.get('parent_id', primary_link['parent_id']),
                         updates.get('quantity', primary_link['quantity']), primary_link['id']))
        if 'geometry' in updates:
            con.execute("UPDATE parts SET drawing_pdf='',drawing_dxf='',drawing_dwg='' WHERE id=?", (part_id,))
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key in {'geometry','specifications','process'}:
                value = json.dumps(value, ensure_ascii=False)
            con.execute(f'UPDATE parts SET {key}=? WHERE id=?', (value, part_id))
        con.execute('UPDATE parts SET status=?,updated_at=? WHERE id=?',
                    ('图纸待审核' if 'geometry' in updates else '待审核', now(), part_id))
    part = require_part(part_id)
    if 'geometry' in updates:
        paths = export_drawing(part)
        part['geometry']['dwg_warning']=paths.get('dwg_warning','')
        with connect() as con:
            saved = con.execute('UPDATE parts SET drawing_pdf=?,drawing_dxf=?,drawing_dwg=?,geometry=? WHERE id=? AND updated_at=?',
                        (paths['drawing_pdf'], paths['drawing_dxf'], paths['drawing_dwg'], json.dumps(part['geometry'],ensure_ascii=False), part_id, part['updated_at']))
            if saved.rowcount != 1:
                raise HTTPException(409, '参数在导出期间发生变化，请刷新后重试')
    return require_part(part_id)


@app.post('/api/parts/{part_id}/save')
def save_part(part_id: str):
    part = require_part(part_id)
    if not reference_approved(part) or not drawing_approved(part) or not process_approved(part):
        raise HTTPException(409, '请先依次审核通过相似图纸、生成图纸和工艺流程单')
    with connect() as con:
        con.execute('UPDATE parts SET status=?,updated_at=? WHERE id=?', ('已归档', now(), part_id))
    return require_part(part_id)


@app.post('/api/projects/{project_id}/archive')
def archive_project(project_id: str):
    project = require_project(project_id)
    if not project['parts'] or not project['analysis'].get('assembly_process', {}).get('pdf_path'):
        raise HTTPException(409, '请先生成全部草案与装配工艺')
    incomplete = [p['name'] for p in project['parts'] if not drawing_approved(p) or not process_approved(p)]
    if incomplete:
        raise HTTPException(409, '以下对象尚未审核通过图纸或工艺：' + '、'.join(incomplete[:5]))
    if project['analysis'].get('assembly_process', {}).get('approval_status') != APPROVED:
        raise HTTPException(409, '请先审核通过装配体工艺流程单')
    with connect() as con:
        con.execute("UPDATE parts SET status='已归档',updated_at=? WHERE project_id=?", (now(), project_id))
        con.execute("UPDATE projects SET stage='已归档' WHERE id=?", (project_id,))
    add_message(project_id, 'assistant', 'workflow', '已保存全部零部件图纸、工艺流程和装配工艺到零件管理。')
    return require_project(project_id)


@app.patch('/api/parts/{part_id}/process')
def patch_part_process(part_id: str, body: ProcessPatch):
    part = require_part(part_id)
    project = require_project(part['project_id'])
    if not drawing_approved(part):
        raise HTTPException(409, '请先审核通过生成图纸')
    try:
        require_generation_stage(project, part, process=True)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    is_assembly = any(l['parent_id'] == part_id for l in project['mbom_links'])
    process = pending_process(check_process(body.model_dump(), project, is_assembly, part))
    process = attach_process_files(project, part, process)
    with connect() as con:
        con.execute('UPDATE parts SET process=?,status=?,updated_at=? WHERE id=?',
                    (json.dumps(process, ensure_ascii=False), '工艺待审核', now(), part_id))
        invalidate_dependents(con, part['project_id'], part_id)
    return require_part(part_id)


@app.patch('/api/projects/{project_id}/process')
def patch_assembly_process(project_id: str, body: ProcessPatch):
    project = require_project(project_id)
    ready, children = children_ready(project, None)
    if not ready:
        raise HTTPException(409, '请先审核通过直接下级工艺：' + '、'.join(p['name'] for p in children if not process_approved(p)))
    try:
        require_generation_stage(project, process=True)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    process = pending_process(check_process(body.model_dump(), project, True))
    process = attach_process_files(project, None, process)
    project['analysis']['assembly_process'] = process
    with connect() as con:
        con.execute('UPDATE projects SET analysis=? WHERE id=?',
                    (json.dumps(project['analysis'], ensure_ascii=False), project_id))
    return require_project(project_id)


@app.post('/api/parts/{part_id}/resources')
async def upload_part_resource(part_id: str, file: UploadFile = File(...)):
    part = require_part(part_id)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.pdf', '.dwg', '.dxf'}:
        raise HTTPException(422, '资源仅支持 PDF、DWG、DXF')
    payload = await file.read(30 * 1024 * 1024 + 1)
    if len(payload) > 30 * 1024 * 1024:
        raise HTTPException(413, '文件不得超过 30 MB')
    if suffix == '.pdf' and not payload.startswith(b'%PDF'):
        raise HTTPException(422, '文件不是有效 PDF')
    resource_id = uid()
    folder = FILES / part['project_id'] / part_id / 'resources'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{resource_id}{suffix}'
    path.write_bytes(payload)
    with connect() as con:
        con.execute('INSERT INTO resources VALUES (?,?,?,?,?,?)',
                    (resource_id, part_id, Path(file.filename or '资源').name[:120], suffix[1:].upper(), str(path), now()))
        if suffix == '.pdf':
            invalidate_dependents(con, part['project_id'], part_id)
            specifications = dict(part.get('specifications') or {})
            specifications['reference_approval'] = {'status': 'pending', 'reference_id': f'resource:{resource_id}'}
            stale_process = pending_process(part.get('process'))
            stale_process.pop('pdf_path', None)
            stale_process.pop('xlsx_path', None)
            con.execute('''UPDATE parts SET source_pdf=?,specifications=?,geometry=?,process=?,drawing_pdf='',
                           drawing_dxf='',drawing_dwg='',status=?,updated_at=? WHERE id=?''',
                        (str(path), json.dumps(specifications, ensure_ascii=False),
                         json.dumps(pending_geometry(part.get('geometry')), ensure_ascii=False),
                         json.dumps(stale_process, ensure_ascii=False),
                         '参考图待审核', now(), part_id))
    return {'id': resource_id, 'name': Path(file.filename or '资源').name, 'kind': suffix[1:].upper()}


@app.get('/api/resources/{resource_id}')
def resource_file(resource_id: str):
    with connect() as con:
        resource = row(con, 'SELECT * FROM resources WHERE id=?', (resource_id,))
    if not resource:
        raise HTTPException(404, '资源不存在')
    return safe_file(resource['file_path'])


@app.get('/api/library')
def library():
    with connect() as con:
        return [unpack(p) for p in rows(con, 'SELECT * FROM parts ORDER BY updated_at DESC')]


@app.get('/api/parts/{part_id}/engineering-model')
def engineering_model_report(part_id: str):
    from .engineering_model import build_model
    from .cad_kernel import validate_solid
    with connect() as con:
        found = row(con, 'SELECT * FROM parts WHERE id=?', (part_id,))
    if not found:
        raise HTTPException(404, '零部件不存在')
    part = unpack(found)
    model = build_model(part.get('geometry') or {}, part)
    model['cad_kernel'] = validate_solid(part.get('geometry') or {})
    return model


@app.get('/api/parts/{part_id}/similar-drawings')
def similar_drawings(part_id: str):
    """Search the enterprise library for drawing evidence without silently using the assembly drawing."""
    target = require_part(part_id)
    target_name = target['name'].strip().lower()
    target_material = normalize_material(target.get('material') or '').lower()
    target_series = str(target.get('specifications', {}).get('series') or '').strip().lower()
    candidates = []
    with connect() as con:
        library_parts = [unpack(item) for item in rows(con, 'SELECT * FROM parts WHERE id<>? ORDER BY updated_at DESC', (part_id,))]
        memories = rows(con, "SELECT id,title,content,source FROM engineering_memory WHERE category='drawing_example' AND file_path<>''")
        pdf_resources = rows(con, """SELECT r.id,r.part_id,r.name,p.name AS part_name,p.material,p.kind,p.specifications
                                      FROM resources r JOIN parts p ON p.id=r.part_id
                                      WHERE r.kind='PDF'""")

    def score(name: str, material: str = '', kind: str = '', series: str = ''):
        value = SequenceMatcher(None, target_name, (name or '').strip().lower()).ratio()
        if material and normalize_material(material).lower() == target_material:
            value += 0.12
        if kind and kind == target.get('kind'):
            value += 0.08
        if target_series and series and target_series == series.strip().lower():
            value += 0.15
        return min(value, 1.0)

    def same_name_family(name: str):
        candidate = (name or '').strip().lower()
        return bool(candidate) and (target_name in candidate or candidate in target_name or
                                    SequenceMatcher(None, target_name, candidate).ratio() >= 0.34)

    for item in library_parts:
        file_kind = 'source' if item.get('source_pdf') else 'pdf' if item.get('drawing_pdf') else ''
        if not file_kind or not same_name_family(item['name']):
            continue
        item_score = score(item['name'], item.get('material', ''), item.get('kind', ''), str(item.get('specifications', {}).get('series') or ''))
        if item_score < 0.18:
            continue
        candidates.append({'id': f"part:{item['id']}:{file_kind}", 'name': item['name'],
                           'drawing_no': item.get('drawing_no') or '图号待确认', 'material': item.get('material') or '待确认',
                           'kind': item.get('kind') or '零件', 'score': round(item_score, 3),
                           'source': '零件库', 'url': f"/api/parts/{item['id']}/{'source' if file_kind == 'source' else 'files/pdf'}"})
    for item in pdf_resources:
        is_current_upload = item['part_id'] == part_id
        if not is_current_upload and not same_name_family(item['part_name']):
            continue
        specs = json.loads(item.get('specifications') or '{}')
        item_score = 1.0 if is_current_upload else score(item['part_name'], item.get('material', ''), item.get('kind', ''), str(specs.get('series') or ''))
        if item_score >= 0.18:
            candidates.append({'id': f"resource:{item['id']}", 'name': item['part_name'], 'drawing_no': item['name'],
                               'material': item.get('material') or '待确认', 'kind': item.get('kind') or '零件',
                               'score': round(item_score, 3), 'source': '用户上传' if is_current_upload else '零件资源', 'url': f"/api/resources/{item['id']}"})
    for item in memories:
        content = json.loads(item.get('content') or '{}')
        name = str(content.get('part_name') or item['title'])
        if not same_name_family(name):
            continue
        item_score = score(name)
        if item_score >= 0.18:
            candidates.append({'id': f"memory:{item['id']}", 'name': name, 'drawing_no': item['title'],
                               'material': '历史资料', 'kind': '参考图', 'score': round(item_score, 3),
                               'source': item.get('source') or '工程记忆', 'url': f"/api/memory/{item['id']}/file"})
    candidates.sort(key=lambda item: (-item['score'], item['name']))
    return candidates[:8]


def resolve_reference_file(reference_id: str, target_part_id: str):
    """Resolve only references returned by similar_drawings; never accept a raw path."""
    if not reference_id:
        return '', '无相似图纸，工程师确认继续'
    tokens = reference_id.split(':')
    with connect() as con:
        if tokens[0] == 'resource' and len(tokens) == 2:
            item = row(con, 'SELECT file_path,name FROM resources WHERE id=?', (tokens[1],))
            if item:
                return item['file_path'], item['name']
        if tokens[0] == 'memory' and len(tokens) == 2:
            item = row(con, "SELECT file_path,title FROM engineering_memory WHERE id=? AND category='drawing_example'", (tokens[1],))
            if item and item['file_path']:
                return item['file_path'], item['title']
        if tokens[0] == 'part' and len(tokens) == 3:
            item = row(con, 'SELECT id,name,source_pdf,drawing_pdf FROM parts WHERE id=?', (tokens[1],))
            if item and item['id'] != target_part_id:
                path = item['source_pdf'] if tokens[2] == 'source' else item['drawing_pdf']
                if path:
                    return path, item['name']
    raise HTTPException(422, '相似图纸引用不存在或不允许使用')


@app.post('/api/parts/{part_id}/approve/{stage}')
def approve_part_stage(part_id: str, stage: str, body: ApprovalRequest):
    if stage not in {'reference', 'drawing', 'process'}:
        raise HTTPException(404, '审核阶段不存在')
    part = require_part(part_id)
    project = require_project(part['project_id'])
    if project['stage'] not in {'MBOM已确认', '草案待审核', '已归档'}:
        raise HTTPException(409, '请先审核通过 MBOM')
    timestamp = now()
    ready, _ = children_ready(project, part_id)
    if not ready:
        raise HTTPException(409, '直接下级成果已变更，请先重新完成下级审核')
    # Retrying an approval must not invalidate already reviewed downstream work.
    reference = (part.get('specifications') or {}).get('reference_approval') or {}
    if ((stage == 'reference' and reference_approved(part) and reference.get('reference_id') == body.reference_id)
            or (stage == 'drawing' and drawing_approved(part))
            or (stage == 'process' and process_approved(part))):
        return project
    if stage == 'reference':
        ready, children = children_ready(project, part_id)
        if not ready:
            raise HTTPException(409, '请先审核通过直接下级工艺：' + '、'.join(p['name'] for p in children if not process_approved(p)))
        approved_reference_id = body.reference_id
        if not approved_reference_id and part.get('source_pdf'):
            # A blank retry must not discard an already uploaded/selected
            # engineering reference.  Explicit removal is a separate edit.
            path = part['source_pdf']
            approved_reference_id = reference.get('reference_id') or ''
            label = reference.get('label') or Path(path).name
        else:
            path, label = resolve_reference_file(approved_reference_id, part_id)
        specifications = dict(part.get('specifications') or {})
        specifications['reference_approval'] = {
            'status': APPROVED, 'approved_at': timestamp, 'reference_id': approved_reference_id,
            'label': label, 'note': body.note[:300]
        }
        geometry = pending_geometry(part.get('geometry'))
        process = pending_process(part.get('process'))
        process.pop('pdf_path', None)
        process.pop('xlsx_path', None)
        with connect() as con:
            con.execute('''UPDATE parts SET source_pdf=?,specifications=?,geometry=?,process=?,status=?,updated_at=?
                           WHERE id=?''',
                        (path, json.dumps(specifications, ensure_ascii=False),
                         json.dumps(geometry, ensure_ascii=False), json.dumps(process, ensure_ascii=False),
                         '参考图已确认', timestamp, part_id))
            con.execute("UPDATE parts SET drawing_pdf='',drawing_dxf='',drawing_dwg='' WHERE id=?", (part_id,))
    elif stage == 'drawing':
        if not reference_approved(part):
            raise HTTPException(409, '请先审核通过相似图纸阶段')
        if not part.get('drawing_pdf'):
            raise HTTPException(409, '请先生成图纸草案')
        blockers = geometry_blockers(part.get('geometry') or {})
        if blockers:
            raise HTTPException(409, '图纸不能通过审核：' + '；'.join(blockers))
        geometry = dict(part.get('geometry') or {})
        geometry.update({'approval_status': APPROVED, 'approved_at': timestamp})
        process = pending_process(part.get('process'))
        process.pop('pdf_path', None)
        process.pop('xlsx_path', None)
        approved_part = dict(part, geometry=geometry)
        paths = export_drawing(approved_part)
        geometry['dwg_warning']=paths.get('dwg_warning','')
        with connect() as con:
            saved = con.execute('UPDATE parts SET geometry=?,process=?,status=?,updated_at=?,drawing_pdf=?,drawing_dxf=?,drawing_dwg=? WHERE id=? AND updated_at=?',
                        (json.dumps(geometry, ensure_ascii=False), json.dumps(process, ensure_ascii=False),
                         '图纸已确认', timestamp, paths['drawing_pdf'], paths['drawing_dxf'], paths['drawing_dwg'], part_id, part['updated_at']))
            if saved.rowcount != 1:
                raise HTTPException(409, '审核期间图纸已变更，请刷新后重新审核')
    else:
        if not drawing_approved(part):
            raise HTTPException(409, '请先审核通过生成图纸阶段')
        if not (part.get('process') or {}).get('steps'):
            raise HTTPException(409, '工艺没有有效工序，不能审核')
        if not (part.get('process') or {}).get('pdf_path'):
            raise HTTPException(409, '请先生成工艺流程单草案')
        process = dict(part.get('process') or {})
        process.update({'approval_status': APPROVED, 'approved_at': timestamp})
        process.pop('artifact_id', None)
        process = attach_process_files(project, part, process)
        with connect() as con:
            saved = con.execute('UPDATE parts SET process=?,status=?,updated_at=? WHERE id=? AND updated_at=?',
                        (json.dumps(process, ensure_ascii=False), '已完成', timestamp, part_id, part['updated_at']))
            if saved.rowcount != 1:
                raise HTTPException(409, '审核期间工艺已变更，请刷新后重新审核')
    with connect() as con:
        invalidate_dependents(con, part['project_id'], part_id)
    add_message(part['project_id'], 'assistant', 'workflow',
                {'reference': '相似图纸输入已审核通过，可进入生成图纸。',
                 'drawing': '生成图纸已审核通过，可进入工艺流程单。',
                 'process': '工艺流程单已审核通过，当前对象已完成。'}[stage], part_id)
    resume_agent_workflow(part['project_id'])
    return require_project(part['project_id'])


@app.post('/api/projects/{project_id}/approve-process')
def approve_project_process(project_id: str):
    project = require_project(project_id)
    ready, children = children_ready(project, None)
    if not ready:
        raise HTTPException(409, '请先审核通过全部直接下级工艺：' + '、'.join(p['name'] for p in children if not process_approved(p)))
    process = dict(project['analysis'].get('assembly_process') or {})
    if process.get('approval_status') == APPROVED:
        return project
    if not process.get('pdf_path'):
        raise HTTPException(409, '请先生成装配工艺流程单')
    process.update({'approval_status': APPROVED, 'approved_at': now()})
    process.pop('artifact_id', None)
    process = attach_process_files(project, None, process)
    project['analysis']['assembly_process'] = process
    with connect() as con:
        con.execute('UPDATE projects SET analysis=?,stage=? WHERE id=?',
                    (json.dumps(project['analysis'], ensure_ascii=False), '草案待审核', project_id))
    add_message(project_id, 'assistant', 'workflow', '装配工艺流程单已审核通过，可保存全部成果。')
    resume_agent_workflow(project_id)
    return require_project(project_id)


@app.get('/api/memory')
def list_memory():
    with connect() as con:
        items = rows(con, 'SELECT id,category,title,source,file_path,created_at,content FROM engineering_memory ORDER BY category,title')
    for item in items:
        content = json.loads(item.pop('content'))
        item['step_count'] = len(content.get('steps', []))
        item['has_file'] = bool(item.pop('file_path'))
    return items


@app.post('/api/memory')
async def upload_memory(file: UploadFile = File(...), category: str = Form(...)):
    if category not in {'drawing_example', 'process_example'}:
        raise HTTPException(422, '知识类型无效')
    name = Path(file.filename or '').name
    suffix = Path(name).suffix.lower()
    expected = '.pdf' if category == 'drawing_example' else '.xlsx'
    if suffix != expected:
        raise HTTPException(422, f'此类知识只支持 {expected} 文件')
    payload = await file.read(10 * 1024 * 1024 + 1)
    if len(payload) > 10 * 1024 * 1024:
        raise HTTPException(413, '单份知识文件不得超过 10 MB')
    if not payload.startswith(b'%PDF' if suffix == '.pdf' else b'PK'):
        raise HTTPException(422, '文件格式无效')
    memory_id = uid()
    folder = FILES / 'memory'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{memory_id}{suffix}'
    path.write_bytes(payload)
    try:
        if suffix == '.pdf':
            doc = pymupdf.open(path)
            if len(doc) == 0:
                raise ValueError('空 PDF')
            content = {'part_name': Path(name).stem, 'note': '企业上传的历史零部件图，尺寸需核对'}
        else:
            steps = process_steps_from_xlsx(path)
            if not steps:
                raise ValueError('未识别到序号、工序和说明三列')
            content = {'steps': steps}
    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(422, '文件无法解析，工艺 Excel 须包含序号、工序、说明三列')
    with connect() as con:
        con.execute('INSERT INTO engineering_memory VALUES (?,?,?,?,?,?,?)',
                    (memory_id, category, Path(name).stem[:80], json.dumps(content, ensure_ascii=False),
                     '企业上传', str(path), now()))
    return {'id': memory_id, 'title': Path(name).stem[:80], 'category': category,
            'step_count': len(content.get('steps', []))}


@app.get('/api/memory/{memory_id}/file')
def memory_file(memory_id: str):
    with connect() as con:
        item = row(con, 'SELECT file_path FROM engineering_memory WHERE id=?', (memory_id,))
    if not item or not item['file_path']:
        raise HTTPException(404, '知识文件不存在')
    return safe_file(item['file_path'])


@app.post('/api/chat')
def chat(body: ChatRequest):
    require_project(body.project_id)
    part = require_part(body.part_id) if body.part_id else None
    if part and part['project_id'] != body.project_id:
        raise HTTPException(422, '部件不属于此项目')
    add_message(body.project_id, 'user', 'agent', body.message, body.part_id or '')
    try:
        result = agent.run_chat(body.project_id, body.part_id, body.message)
        answer = result['answer']
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f'生成失败：{str(exc)[:240]}')
    add_message(body.project_id, 'assistant', 'agent', answer, body.part_id or '')
    return {'answer': answer, 'events': result['events'], 'project': require_project(body.project_id)}


@app.get('/api/projects/{project_id}/source')
def project_source(project_id: str):
    return safe_file(require_project(project_id)['source_path'])


@app.get('/api/projects/{project_id}/cad-source')
def project_cad_source(project_id: str):
    path = require_project(project_id)['analysis'].get('source_cad_path')
    if not path:
        raise HTTPException(404, '无 CAD 原始文件')
    return safe_file(path)


@app.get('/api/parts/{part_id}/source')
def part_source(part_id: str):
    return safe_file(require_part(part_id)['source_pdf'])


@app.get('/api/parts/{part_id}/preview.svg')
def preview_svg(part_id: str):
    part = require_part(part_id)
    cad = part['geometry'].get('cad_document')
    if cad:
        return safe_file(cad['svg'])
    return Response(svg_preview(part), media_type='image/svg+xml')


@app.get('/api/parts/{part_id}/files/{file_kind}')
def part_file(part_id: str, file_kind: str):
    if file_kind not in {'pdf', 'dxf', 'dwg', 'process', 'process-xlsx'}:
        raise HTTPException(404, '文件类型不存在')
    part = require_part(part_id)
    path = part['process'].get('pdf_path') if file_kind == 'process' else part['process'].get('xlsx_path') if file_kind == 'process-xlsx' else part.get('drawing_' + file_kind)
    if not path:
        raise HTTPException(404, '文件尚未生成')
    return safe_file(path)


@app.get('/api/projects/{project_id}/process.pdf')
def assembly_process_pdf(project_id: str):
    process = require_project(project_id)['analysis'].get('assembly_process') or {}
    path = process.get('pdf_path')
    if not path:
        raise HTTPException(404, '装配工艺流程单尚未生成')
    return safe_file(path)


@app.get('/api/projects/{project_id}/process.xlsx')
def assembly_process_xlsx(project_id: str):
    process = require_project(project_id)['analysis'].get('assembly_process') or {}
    path = process.get('xlsx_path')
    if not path:
        raise HTTPException(404, '装配工艺 Excel 尚未生成')
    return safe_file(path)


@app.patch('/api/projects/{project_id}/mbom')
def update_mbom(project_id: str, body: MbomPatch):
    project = require_project(project_id)
    existing = {p['id'] for p in project['parts']}
    provided = {p.id for p in body.parts}
    if existing != provided or len(body.parts) != len(provided):
        raise HTTPException(422, 'MBOM 必须包含当前项目的全部零部件且不能重复')
    links = body.links if body.links is not None else [
        MbomLinkItem(id=uid(), parent_id=p.parent_id, child_id=p.id, quantity=p.quantity)
        for p in body.parts]
    if body.links is not None and (len({x.id for x in links}) != len(links) or
                                   {x.id for x in links} != {x['id'] for x in project['mbom_links']}):
        raise HTTPException(422, 'MBOM 装配关系已变化，请刷新后重试')
    try:
        checked = validate_plan({'parts': [{'key': p.id, 'kind': p.kind} for p in body.parts],
                                 'links': [{'parent_key': l.parent_id, 'child_key': l.child_id,
                                            'quantity': l.quantity} for l in links]})
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    checked_kind = {p['key']: p['kind'] for p in checked['parts']}
    with connect() as con:
        invalidate_dependents(con, project_id, topology=True)
        for p in body.parts:
            con.execute('''UPDATE parts SET name=?,kind=?,material=?,drawing_no=?,updated_at=? WHERE id=?''',
                        (p.name[:80], checked_kind[p.id][:20], p.material[:80], p.drawing_no[:80], now(), p.id))
        if body.links is None:
            con.execute('DELETE FROM mbom_links WHERE project_id=?', (project_id,))
            for link in links:
                con.execute('INSERT INTO mbom_links VALUES (?,?,?,?,?,?,?)',
                            (link.id, project_id, link.parent_id, link.child_id, link.quantity,
                             '工程师确认', '已确认'))
        else:
            for link in links:
                con.execute('''UPDATE mbom_links
                               SET parent_id=?,child_id=?,quantity=?,evidence='工程师确认',confidence='已确认'
                               WHERE id=? AND project_id=?''',
                            (link.parent_id, link.child_id, link.quantity, link.id, project_id))
        for p in body.parts:
            primary = next(l for l in links if l.child_id == p.id)
            con.execute('UPDATE parts SET parent_id=?,quantity=? WHERE id=?',
                        (primary.parent_id, primary.quantity, p.id))
            current = unpack(row(con, 'SELECT * FROM parts WHERE id=?', (p.id,)))
            specifications = dict(current.get('specifications') or {})
            specifications['reference_approval'] = {'status': 'pending'}
            con.execute('UPDATE parts SET specifications=?,geometry=?,process=?,status=?,updated_at=? WHERE id=?',
                        (json.dumps(specifications, ensure_ascii=False),
                         json.dumps(pending_geometry(current.get('geometry')), ensure_ascii=False),
                         json.dumps(pending_process(current.get('process')), ensure_ascii=False),
                         '参考图待审核', now(), p.id))
        con.execute("UPDATE projects SET stage='MBOM已确认' WHERE id=?", (project_id,))
    add_message(project_id, 'assistant', 'workflow', 'MBOM 已审核通过。请从最下级零件开始，依次审核相似图纸、生成图纸和工艺流程单。')
    resume_agent_workflow(project_id)
    return require_project(project_id)


@app.post('/api/projects/{project_id}/mbom-links')
def add_mbom_link(project_id: str, body: NewMbomLink):
    project = require_project(project_id)
    if body.child_id not in {p['id'] for p in project['parts']}:
        raise HTTPException(422, '零部件不属于当前项目')
    candidate = project['mbom_links'] + [{'id': uid(), 'parent_id': body.parent_id,
                                          'child_id': body.child_id, 'quantity': body.quantity}]
    try:
        validate_plan({'parts': [{'key': p['id'], 'kind': p['kind']} for p in project['parts']],
                       'links': [{'parent_key': l['parent_id'], 'child_key': l['child_id'],
                                  'quantity': l['quantity']} for l in candidate]})
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    link = candidate[-1]
    with connect() as con:
        con.execute('INSERT INTO mbom_links VALUES (?,?,?,?,?,?,?)',
                    (link['id'], project_id, link['parent_id'], link['child_id'], link['quantity'],
                     '工程师手动复用', '用户指定'))
        invalidate_dependents(con, project_id, topology=True)
        if link['parent_id']:
            con.execute("UPDATE parts SET kind='部件' WHERE id=?", (link['parent_id'],))
    return require_project(project_id)


@app.delete('/api/mbom-links/{link_id}')
def remove_mbom_link(link_id: str):
    with connect() as con:
        link = row(con, 'SELECT * FROM mbom_links WHERE id=?', (link_id,))
        if not link:
            raise HTTPException(404, '装配关系不存在')
        child = row(con, 'SELECT * FROM parts WHERE id=?', (link['child_id'],))
        if child['status'] == '已归档':
            raise HTTPException(409, '已归档零件不能直接移除')
        other = row(con, 'SELECT id,parent_id,quantity FROM mbom_links WHERE child_id=? AND id<>? ORDER BY rowid LIMIT 1',
                    (link['child_id'], link_id))
        dependent = row(con, 'SELECT id FROM mbom_links WHERE parent_id=? LIMIT 1', (link['child_id'],))
        if not other and dependent:
            raise HTTPException(409, '请先移除下级装配关系')
        invalidate_dependents(con, link['project_id'], topology=True)
        con.execute('DELETE FROM mbom_links WHERE id=?', (link_id,))
        if other:
            con.execute('UPDATE parts SET parent_id=?,quantity=? WHERE id=?',
                        (other['parent_id'], other['quantity'], link['child_id']))
        else:
            con.execute('DELETE FROM parts WHERE id=?', (link['child_id'],))
    return {'deleted': link_id}


@app.post('/api/projects/{project_id}/generate-all')
def generate_all(project_id: str):
    project = require_project(project_id)
    if project['stage'] == '待解析':
        raise HTTPException(409, '请先解析装配图形成 MBOM 草案')
    with connect() as con:
        existing = row(con, "SELECT * FROM jobs WHERE project_id=? AND status IN ('等待中','运行中','等待审核') ORDER BY created_at DESC LIMIT 1", (project_id,))
        if existing:
            existing['errors'] = json.loads(existing['errors'] or '[]')
            return existing
        job_id = uid()
        total = 2 + len(project['parts']) * 3
        con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',
                    (job_id, project_id, '等待中', total, 0, '编排 Agent 正在建立全项目计划', '[]', now(), now()))
    dispatch_workflow(job_id, project_id)
    return {'id': job_id, 'status': '等待中', 'total': total, 'completed': 0,
            'current': '编排 Agent 正在建立全项目计划', 'errors': []}


def resume_agent_workflow(project_id):
    """Wake the workflow orchestrator after the engineer passes an approval gate."""
    with connect() as con:
        job = row(con, "SELECT * FROM jobs WHERE project_id=? AND status='等待审核' ORDER BY created_at DESC LIMIT 1", (project_id,))
        if not job:
            return
        con.execute("UPDATE jobs SET status='等待中',current=?,updated_at=? WHERE id=?",
                    ('审核已通过，编排 Agent 准备继续', now(), job['id']))
    dispatch_workflow(job['id'], project_id)


def dispatch_workflow(job_id, project_id):
    if cloud_enabled():
        from .cloud_tasks import enqueue_workflow
        enqueue_workflow(job_id, project_id)
    else:
        Thread(target=agent.run_batch_workflow, args=(job_id, project_id), daemon=True).start()


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    with connect() as con:
        job = row(con, 'SELECT * FROM jobs WHERE id=?', (job_id,))
    if not job:
        raise HTTPException(404, '任务不存在')
    job['errors'] = json.loads(job['errors'])
    return job


from .cad_editor import router as cad_router
app.include_router(cad_router)
from .cad_studio import router as cad_studio_router
app.include_router(cad_studio_router)

DIST = ROOT / 'dist'
if DIST.is_dir():
    app.mount('/', StaticFiles(directory=DIST, html=True), name='web')
