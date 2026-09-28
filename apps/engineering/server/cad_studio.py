"""MLightCAD bridge. Keep the original and merge only browser edits into it."""
import hashlib
import io
from pathlib import Path

import ezdxf
from ezdxf.addons import Importer
from ezdxf.addons.importer import IMPORT_ENTITIES
from ezdxf.lldxf.tagwriter import TagCollector
from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db
from .cad_editor import gated, part_record, session_folder, start_session, persist_document

router = APIRouter(prefix='/api/parts/{part_id}/cad/studio')


@router.post('/open')
def open_current(part_id: str):
    part = gated(part_id)
    path = part.get('drawing_dxf') or part.get('drawing_dwg')
    if not path or not Path(path).is_file():
        raise HTTPException(404, '请加载 DWG / DXF 图纸开始编辑')
    return start_session(part, Path(path), Path(path).name, studio=True)


@router.post('/upload')
async def upload(part_id: str, file: UploadFile = File(...)):
    part = gated(part_id)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ('.dwg', '.dxf'):
        raise HTTPException(422, '请上传 DWG 或 DXF')
    content = await file.read(30 * 1024 * 1024 + 1)
    if len(content) > 30 * 1024 * 1024:
        raise HTTPException(413, '文件不能超过 30 MB')
    folder = db.FILES / part['project_id'] / part_id / 'cad' / 'uploads'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (db.uid() + suffix)
    path.write_bytes(content)
    return start_session(part, path, Path(file.filename).name, studio=True)


@router.get('/{session_id}/source')
def source(part_id: str, session_id: str):
    from .main import safe_file
    return safe_file(session_folder(part_record(part_id), session_id) / 'source.dxf')


class StudioDocument(BaseModel):
    revision: str
    dxf: str = Field(max_length=60 * 1024 * 1024)
    layout: str = 'Model'


def read_dxf(text):
    try:
        doc = ezdxf.read(io.StringIO(text))
        if sum(len(s) for s in doc.layouts) > 100000:
            raise ValueError('实体数量超过限制')
        return doc
    except (ValueError, ezdxf.DXFError) as exc:
        raise HTTPException(422, 'DXF 导出无效：' + str(exc)) from exc


def checked_folder(part_id, session_id, revision):
    folder = session_folder(part_record(part_id), session_id)
    if hashlib.sha256((folder / 'source.dxf').read_bytes()).hexdigest() != revision:
        raise HTTPException(409, '源图纸版本已变化，请重新打开')
    return folder


@router.post('/{session_id}/baseline')
def baseline(part_id: str, session_id: str, body: StudioDocument):
    folder = checked_folder(part_id, session_id, body.revision)
    document = read_dxf(body.dxf)
    path = folder / 'baseline.dxf'
    if path.exists():
        raise HTTPException(409, '编辑基线已建立，请重新打开图纸')
    document.saveas(path)
    from .cloud_context import enabled
    if enabled():
        from .cloud_storage import publish
        publish(path)
    original = ezdxf.readfile(folder / 'source.dxf')
    visible = {e.dxf.handle for s in document.layouts for e in s}
    retained = sum(e.dxf.handle not in visible for s in original.layouts for e in s)
    return {'ready': True, 'preserved_entities': retained}


def signature(entity):
    # Rendering may materialize optional DXF defaults after the baseline export.
    # Compare their meaning, so a normal (0,0,1) or BYLAYER is not a block edit.
    defaults = {48: 1.0, 60: 0, 62: 256, 370: -1, 38: 0.0}
    normal = not entity.dxf.is_supported('extrusion') or tuple(entity.dxf.get('extrusion', (0, 0, 1))) == (0, 0, 1)
    return [(t.code, t.value) for t in TagCollector.dxftags(entity)
            if t.code not in (5, 330)
            and not (t.code in defaults and t.value == defaults[t.code])
            and not (normal and t.code in (210, 220, 230))]


def merge_edits(original, baseline_doc, edited):
    """Compare two exports from the same serializer, preserving unrepresented entities."""
    if set(baseline_doc.layouts.names()) != set(edited.layouts.names()):
        raise ValueError('暂不支持增删布局，请保留原布局后保存')
    changed_dimensions = set()
    # Layout blocks are handled below; dimension graphics travel with their entity.
    for block in baseline_doc.blocks:
        if block.name.upper().startswith(('*MODEL_SPACE', '*PAPER_SPACE')): continue
        other = edited.blocks.get(block.name)
        if other is None or [signature(e) for e in block] != [signature(e) for e in other]:
            if block.name.upper().startswith('*D'):
                changed_dimensions.add(block.name)
                continue
            raise ValueError('检测到块定义修改，请先炸开需要编辑的块再修改实体')
    for table_name in ('styles', 'dimstyles', 'linetypes'):
        old_table, new_table = getattr(baseline_doc, table_name), getattr(edited, table_name)
        for record in old_table:
            other = new_table.get(record.dxf.name) if record.dxf.name in new_table else None
            if other is None or signature(record) != signature(other):
                raise ValueError('暂不支持覆盖已有字体、标注或线型样式定义，请新建样式后应用到实体')
    importer = Importer(edited, original)
    changes = 0
    for space in baseline_doc.layouts:
        before = {e.dxf.handle: e for e in space}
        after_space = edited.layouts.get(space.name)
        after = {e.dxf.handle: e for e in after_space}
        if space.name not in original.layouts.names():
            if before or after: raise ValueError('布局不能映射回原图纸')
            continue
        target = original.layouts.get(space.name)
        existing = {e.dxf.handle: e for e in target}
        for handle in before.keys() | after.keys():
            old, new = before.get(handle), after.get(handle)
            if old is not None and new is not None and signature(old) == signature(new):
                if new.dxftype() != 'DIMENSION' or new.dxf.get('geometry', '') not in changed_dimensions: continue
            if old is not None:
                source_entity = existing.get(handle)
                if source_entity is None or source_entity.dxftype() != old.dxftype():
                    raise ValueError('编辑实体无法映射回原图纸，已阻止覆盖')
                target.delete_entity(source_entity)
            if new is not None:
                if new.dxftype() not in IMPORT_ENTITIES:
                    raise ValueError('暂不支持保存此类实体修改：' + new.dxftype())
                importer.import_entities([new], target)
            changes += 1
    importer.finalize()
    if any(layer.dxf.name not in edited.layers for layer in baseline_doc.layers):
        raise ValueError('暂不支持删除原图图层，请关闭或冻结图层')
    for layer in edited.layers:
        previous = baseline_doc.layers.get(layer.dxf.name) if layer.dxf.name in baseline_doc.layers else None
        if layer.dxf.name not in original.layers:
            original.layers.new(layer.dxf.name)
        target = original.layers.get(layer.dxf.name)
        for key in ('color', 'true_color', 'flags', 'lineweight', 'plot', 'linetype'):
            value = layer.dxf.get(key)
            if previous is not None and value == previous.dxf.get(key): continue
            if key == 'linetype' and value and value not in original.linetypes:
                importer.import_table('linetypes', [value]); importer.finalize()
            if value is None: target.dxf.discard(key)
            else: setattr(target.dxf, key, value)
    return changes


@router.post('/{session_id}/save')
def save(part_id: str, session_id: str, body: StudioDocument):
    part = gated(part_id)
    folder = checked_folder(part_id, session_id, body.revision)
    if not (folder / 'baseline.dxf').is_file():
        raise HTTPException(409, '尚未完成编辑基线加载')
    edited = read_dxf(body.dxf)
    original = ezdxf.readfile(folder / 'source.dxf')
    try:
        changes = merge_edits(original, ezdxf.readfile(folder / 'baseline.dxf'), edited)
        if body.layout not in original.layouts.names(): raise ValueError('请选择有效布局')
    except (ValueError, ezdxf.DXFError) as exc:
        raise HTTPException(422, str(exc)) from exc
    saved = persist_document(part, folder, original, body.layout, [{'editor': 'MLightCAD 1.7.1', 'changed_entities': changes}])
    return saved
