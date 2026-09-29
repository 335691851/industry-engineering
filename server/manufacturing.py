"""Manufacturing intent and allowance calculations, without invented finish dimensions.

Allowances are process proposals, never ISO dimensional tolerances. Values must
carry a source/rationale and remain drafts until the drawing approval gate.
"""
from copy import deepcopy
from math import isfinite


def number(value, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isfinite(value) and (value >= 0 if allow_zero else value > 0)


def dimension_value(geometry, field):
    keys = field.split('.')
    current = geometry
    try:
        for key in keys:
            current = current[int(key)] if isinstance(current, list) else current[key]
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    return current


def prepare_manufacturing(geometry):
    result = deepcopy(geometry)
    plan = deepcopy(result.get('manufacturing') or {})
    plan.setdefault('delivery_state', '本零部件制造完成并检验合格；装入上级前的交付状态')
    plan.setdefault('blank_type', '')
    plan.setdefault('core_route', [])
    # Derive stock removal from known state dimensions, never from a product name.
    supplied = list(plan.get('allowances') or [])
    present = {a.get('dimension') for a in supplied}
    stock_dimensions = plan.get('stock_dimensions') or {}
    for field, kind in [('outer_diameter_mm', 'external'), ('inner_diameter_mm', 'bore'),
                        ('overall_length_mm', 'axial'), ('thickness_mm', 'axial')]:
        finish, blank = result.get(field), stock_dimensions.get(field)
        if field not in present and number(finish) and number(blank, allow_zero=kind == 'bore'):
            delta = finish - blank if kind == 'bore' else blank - finish
            supplied.append({'dimension': field, 'label': field, 'kind': kind, 'faces': 2,
                             'per_side_mm': delta/2, 'basis': '尺寸状态差值推算',
                             'reason': '按原料与本件交付尺寸差计算；轴向默认双端对称分配，待审核'})
    allowances = []
    for source in supplied:
        item = dict(source)
        field = str(item.get('dimension', ''))
        nominal = dimension_value(result, field)
        stock = item.get('per_side_mm')
        faces = item.get('faces', 2)
        kind = item.get('kind')
        # Diameter stock is a radial, per-side value.  Models sometimes report
        # ``faces=1`` because the drawing shows one radius; the blank diameter
        # still changes by two radial allowances.  Normalize that semantic
        # representation here instead of rejecting an otherwise traceable
        # calculation.  Values outside the schema remain hard errors.
        if kind in {'external', 'bore'} and faces == 1:
            item['input_faces'] = 1
            faces = 2
            item['faces'] = 2
            item['normalization'] = '直径单边余量按两侧径向计入毛坯直径'
        item['finished_mm'] = nominal
        item['blank_mm'] = None
        item['calculation'] = ''
        item['review_required'] = True
        item['error'] = ''
        item['warning'] = ''
        allowed = field in {'outer_diameter_mm', 'inner_diameter_mm', 'overall_length_mm', 'thickness_mm'} or (
            field.startswith('segments.') and field.endswith(('.length_mm', '.diameter_mm')))
        if not allowed or not number(nominal, allow_zero=kind == 'bore'):
            item['error'] = '缺少该要素的成品尺寸，不能计算毛坯尺寸'
        elif stock is None:
            item['warning'] = '加工余量尚未确定；本次审核不确认该项毛坯尺寸，工艺生成必须保留待复核提示'
        elif not number(stock, allow_zero=True) or faces not in (1, 2) or isinstance(faces, bool):
            item['error'] = '单边余量必须为非负有限数值，加工面数只能为 1 或 2'
        elif kind not in {'external', 'bore', 'axial'}:
            item['error'] = '余量方向必须为 external / bore / axial'
        elif (field == 'inner_diameter_mm') != (kind == 'bore') or (
                field.endswith(('length_mm', 'thickness_mm')) != (kind == 'axial')) or (
                kind in {'external', 'bore'} and faces != 2):
            item['error'] = '直径为两侧径向余量，长度/厚度为轴向余量，方向与尺寸不符'
        elif not item.get('basis') or not item.get('reason'):
            item['error'] = '余量需要来源和工艺理由（材料、毛坯及后续加工）'
        else:
            delta = stock * faces
            blank = nominal - delta if kind == 'bore' else nominal + delta
            if blank < 0:
                item['error'] = '内孔留量超过成品孔径'
            else:
                item['blank_mm'] = round(blank, 4)
                item['calculation'] = f'{nominal:g} {"-" if kind == "bore" else "+"} {faces} × {stock:g} = {blank:g} mm'
                stated_blank = (plan.get('stock_dimensions') or {}).get(field)
                if number(stated_blank, allow_zero=kind == 'bore') and abs(stated_blank - blank) > .001:
                    item['error'] = f'余量计算毛坯 {blank:g} mm 与来料尺寸 {stated_blank:g} mm 不一致，请核对交付状态及余量'
        allowances.append(item)
    plan['allowances'] = allowances
    result['manufacturing'] = plan
    return result


def geometry_blockers(geometry):
    if geometry.get('cad_document'):
        cad = geometry['cad_document']
        return [] if cad.get('drawing_dxf') and cad.get('drawing_pdf') else ['CAD 版本缺少图纸文件']
    errors = []
    shape = geometry.get('shape_type')
    if shape in {'tube', 'plate'}:
        od, bore = geometry.get('outer_diameter_mm'), geometry.get('inner_diameter_mm')
        length = geometry.get('overall_length_mm' if shape == 'tube' else 'thickness_mm')
        if not (number(od) and number(bore, allow_zero=True) and number(length) and od > bore):
            errors.append('外径、内径和长度/厚度未构成有效几何')
        elif shape == 'tube' and bore == 0:
            errors.append('空心筒体必须有大于零的内径')
    elif shape in {'rotational', None}:
        spans = geometry.get('segments') or []
        if not spans or any(not number(s.get('length_mm')) or not number(s.get('diameter_mm')) for s in spans):
            errors.append('回转体分段尺寸不完整')
        elif geometry.get('overall_length_mm') is not None:
            total = geometry['overall_length_mm']
            if not number(total) or abs(sum(s['length_mm'] for s in spans) - total) > .05:
                errors.append('轴向尺寸链不闭合；请核对尺寸或缺失段')
    else:
        errors.append('当前绘图器不支持该几何类型，需补充专业 CAD 图纸')
    length = geometry.get('overall_length_mm')
    if number(length) and any(not number(z.get('length_mm')) or z['length_mm'] > length
                              for z in geometry.get('machining_zones') or []):
        errors.append('端部加工区域超出零件长度或无有效长度')
    sources = geometry.get('dimension_evidence') or {}
    if isinstance(sources, dict):
        for field, source in sources.items():
            if isinstance(source, dict) and source.get('checks') and field.split('.')[0] not in (geometry.get('user_overrides') or {}):
                errors.append(f'{field} 图像识别证据存在矛盾或缺失，请核对并保存确认参数')
            if dimension_value(geometry, field) is not None and not field.startswith(('manufacturing.', 'stock_dimensions.')) and isinstance(source, dict) and source.get('state') in ('stock', 'post_assembly') and field.split('.')[0] not in (geometry.get('user_overrides') or {}):
                errors.append(f'{field} 引用了非交付状态尺寸')
    errors += [a['error'] for a in prepare_manufacturing(geometry)['manufacturing']['allowances'] if a['error']]
    return list(dict.fromkeys(errors))


def manufacturing_summary(geometry):
    plan = prepare_manufacturing(geometry)['manufacturing']
    lines = [f"交付状态：{plan['delivery_state']}"]
    if plan.get('blank_type'):
        lines.append(f"毛坯：{plan['blank_type']}")
    if plan.get('core_route'):
        lines.append('核心路线：' + ' → '.join(plan['core_route']))
    labels = {'outer_diameter_mm':'外圆径向', 'inner_diameter_mm':'内孔径向', 'overall_length_mm':'长度轴向', 'thickness_mm':'厚度轴向'}
    for item in plan['allowances']:
        lines.append(f"{item.get('label') or labels.get(item['dimension'], item['dimension'])}：{item['calculation'] or item['error']}；依据：{item.get('basis', '')}；{item.get('reason', '')}")
    return lines
