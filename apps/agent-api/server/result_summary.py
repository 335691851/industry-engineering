"""Business summaries composed from persisted results, not model claims."""
from .manufacturing import geometry_blockers, prepare_manufacturing


def generation_summary(part, stage):
    lines = [f"{part['name']} · {'图纸' if stage == 'drawing' else '工艺流程单'}草案已生成"]
    if stage == 'drawing':
        g = prepare_manufacturing(part.get('geometry') or {})
        values = [f'{label} {g[key]} mm' for key,label in [('outer_diameter_mm','外径'),('inner_diameter_mm','内径'),('overall_length_mm','总长'),('thickness_mm','厚度')] if g.get(key) is not None]
        if g.get('segments'): values.append(f"轮廓 {len(g['segments'])} 段")
        lines += ['核心结果：'+('；'.join(values) or '几何信息待补充'), '材料：'+(part.get('material') or '待确认')]
        if g.get('dwg_warning'): lines.append('DWG 导出：'+str(g['dwg_warning']))
        for a in g['manufacturing']['allowances']:
            if a.get('calculation'): lines.append('放量：'+a['calculation'])
        blockers = geometry_blockers(g)
        if blockers: lines += ['审核阻塞：'] + ['• '+x for x in blockers]
        reviews = list(g.get('review_items') or []) + [a['warning'] for a in g['manufacturing']['allowances'] if a.get('warning')]
        lines.append('下一步：'+('先核对阻塞项，在尺寸编辑中保存后重新生成。' if blockers else '核对图纸及放量，审核通过后进入工艺。'))
    else:
        p = part.get('process') or {}; steps=p.get('steps') or []
        lines.append(f'核心路线（{len(steps)} 道）：'+' → '.join(s.get('operation','待确定') for s in steps))
        if p.get('summary'): lines.append('交付要求：'+p['summary'])
        for s in steps:
            if s.get('inspection'): lines.append(f"检验 · {s.get('operation','')}：{s['inspection']}")
        reviews = p.get('review_items') or []
        lines.append('下一步：核对工序、基准及检验要求，审核通过后完成当前对象。')
    if reviews: lines += [f'待复核（{len(reviews)} 项）：'] + ['• '+str(x) for x in reviews[:6]] + (['其余问题请查看右侧编辑与校核报告。'] if len(reviews)>6 else [])
    return '\n'.join(lines)
