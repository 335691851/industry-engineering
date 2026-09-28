"""Explicit human approval gates between engineering workflow stages."""
from copy import deepcopy


APPROVED = 'approved'
PENDING = 'pending'


def reference_approved(part):
    return (part.get('specifications') or {}).get('reference_approval', {}).get('status') == APPROVED


def drawing_approved(part):
    return (part.get('geometry') or {}).get('approval_status') == APPROVED


def process_approved(part):
    return (part.get('process') or {}).get('approval_status') == APPROVED


def pending_geometry(value):
    result = deepcopy(value or {})
    result['approval_status'] = PENDING
    result.pop('approved_at', None)
    return result


def pending_process(value):
    result = deepcopy(value or {})
    result.pop('artifact_id', None)
    result['approval_status'] = PENDING
    result.pop('approved_at', None)
    return result


def children_ready(project, parent_id):
    child_ids = {link['child_id'] for link in project.get('mbom_links', [])
                 if (link.get('parent_id') or None) == (parent_id or None)}
    children = [part for part in project.get('parts', []) if part['id'] in child_ids]
    return bool(len(children) == len(child_ids)) and all(
        reference_approved(part) and drawing_approved(part) and process_approved(part)
        for part in children), children


def require_generation_stage(project, part=None, process=False):
    if project.get('stage') not in {'MBOM已确认', '草案待审核', '已归档'}:
        raise ValueError('请先审核 MBOM，之后才能生成下游内容')
    ready, children = children_ready(project, part['id'] if part else None)
    if not ready:
        raise ValueError('请先完成并审核直接下级的图纸与工艺：' + '、'.join(p['name'] for p in children))
    if part and not reference_approved(part):
        raise ValueError('请先审核参考输入；没有相似图时可选择无参考图继续')
    if process and part and not drawing_approved(part):
        raise ValueError('请先审核通过当前图纸，再生成工艺')


def invalidate_dependents(con, project_id, part_id=None, topology=False):
    """Invalidate every ancestor of a reused child, inside the caller transaction.

    The edited part itself is handled by the caller. Topology changes invalidate
    the whole project and return to MBOM review. Historical files remain on disk
    but current resource links are cleared so stale output cannot be consumed.
    """
    import json
    from .db import now, unpack
    links = list(con.execute('SELECT parent_id,child_id FROM mbom_links WHERE project_id=?', (project_id,)))
    affected = set()
    frontier = {part_id} if part_id else set()
    while frontier:
        parents = {r['parent_id'] for r in links if r['child_id'] in frontier and r['parent_id']}
        frontier = parents - affected
        affected.update(frontier)
    timestamp = now()
    for record in con.execute('SELECT * FROM parts WHERE project_id=?', (project_id,)).fetchall():
        part = unpack(dict(record))
        if not topology and part['id'] not in affected:
            continue
        geometry = pending_geometry(part['geometry'])
        process = pending_process(part['process'])
        process.pop('pdf_path', None); process.pop('xlsx_path', None)
        specs = part['specifications']
        if topology:
            specs.pop('reference_approval', None)
        con.execute("UPDATE parts SET geometry=?,process=?,specifications=?,drawing_pdf='',drawing_dxf='',drawing_dwg='',status=?,updated_at=? WHERE id=?",
                    (json.dumps(geometry, ensure_ascii=False), json.dumps(process, ensure_ascii=False),
                     json.dumps(specs, ensure_ascii=False), '上游已变更，待重新生成', timestamp, part['id']))
    row = con.execute('SELECT analysis FROM projects WHERE id=?', (project_id,)).fetchone()
    if row:
        analysis = json.loads(row['analysis'])
        if analysis.get('assembly_process'):
            process = pending_process(analysis['assembly_process'])
            process.pop('pdf_path', None); process.pop('xlsx_path', None)
            analysis['assembly_process'] = process
        if topology:
            analysis.pop('mbom_approved_at', None)
        con.execute('UPDATE projects SET analysis=?,stage=CASE WHEN ? THEN ? WHEN stage=\'已归档\' THEN ? ELSE stage END WHERE id=?',
                    (json.dumps(analysis, ensure_ascii=False), topology, 'MBOM待确认', '草案待审核', project_id))


def workflow_state(project, part):
    from .manufacturing import geometry_blockers, prepare_manufacturing
    ready, children = children_ready(project, part['id'])
    mbom = project.get('stage') in {'MBOM已确认', '草案待审核', '已归档'}
    return {'mbom_approved': mbom, 'children_ready': ready,
            'waiting_children': [p['name'] for p in children if not (reference_approved(p) and drawing_approved(p) and process_approved(p))],
            'can_generate_drawing': mbom and ready and reference_approved(part),
            'can_generate_process': mbom and ready and reference_approved(part) and drawing_approved(part),
            'drawing_blockers': geometry_blockers(part.get('geometry') or {}),
            'drawing_warnings': [a['warning'] for a in prepare_manufacturing(part.get('geometry') or {})['manufacturing']['allowances'] if a.get('warning')],
            'reference_approved': reference_approved(part), 'drawing_approved': drawing_approved(part),
            'process_approved': process_approved(part)}
