"""Versioned engineering representation and traceable, deterministic checks."""
from .manufacturing import number, prepare_manufacturing, geometry_blockers


def build_model(geometry, part):
    g = prepare_manufacturing(geometry)
    dimensions = []
    evidence = g.get('dimension_evidence') or {}
    if not isinstance(evidence, dict):
        evidence = {}
    fields = ['outer_diameter_mm', 'inner_diameter_mm', 'overall_length_mm', 'thickness_mm']
    fields += [f'segments.{i}.{k}' for i, _ in enumerate(g.get('segments') or [])
               for k in ('length_mm', 'diameter_mm')]
    fields += [f'machining_zones.{i}.length_mm' for i, _ in enumerate(g.get('machining_zones') or [])]
    from .manufacturing import dimension_value
    for field in fields:
        value = dimension_value(g, field)
        if value is None:
            continue
        source = evidence.get(field) or {}
        if not isinstance(source, dict):
            source = {}
        overridden = field.split('.')[0] in (g.get('user_overrides') or {})
        tolerance_field = {'outer_diameter_mm':'outer_tolerance','inner_diameter_mm':'inner_tolerance',
                           'overall_length_mm':'length_tolerance'}.get(field)
        dimensions.append({'id': field, 'value': value, 'unit': 'mm', 'state': 'part_delivery',
                           'tolerance': g.get(tolerance_field, '') if tolerance_field else '',
                           'source': source, 'basis': 'user' if overridden else 'model_extraction',
                           'verified': overridden,
                           'reference': field == 'outer_diameter_mm' and bool(g.get('outer_reference'))})
    plan = g['manufacturing']
    errors = geometry_blockers(g)
    errors += [f"{d['id']} 引用了非交付状态尺寸，请核对原料/装配后尺寸归属" for d in dimensions
               if not d['verified'] and d['source'].get('state') in ('stock', 'post_assembly')]
    warnings = [f"{d['id']} 缺少图纸定位依据" for d in dimensions
                if not d['verified'] and not d['source'].get('token_ids')]
    warnings += [a['warning'] for a in plan['allowances'] if a.get('warning')]
    zones = g.get('machining_zones') or []
    length = g.get('overall_length_mm')
    if number(length) and any(not number(z.get('length_mm')) or z['length_mm'] > length for z in zones):
        errors.append('端部加工区域超出零件长度或无有效长度')
    if g.get('shape_type') == 'tube' and number(g.get('outer_diameter_mm')) and number(g.get('inner_diameter_mm')):
        wall = (g['outer_diameter_mm'] - g['inner_diameter_mm'])/2
    else:
        wall = None
    return {'schema_version': '1.0', 'object_id': part.get('id'), 'units': 'mm',
            'delivery_state': plan['delivery_state'], 'geometry_type': g.get('shape_type'),
            'dimensions': dimensions, 'features': g.get('features') or [], 'datums': g.get('datums') or [],
            'states': {'stock': plan.get('stock_dimensions') or {}, 'part_delivery': {d['id']: d['value'] for d in dimensions},
                       'post_assembly': {}},
            'calculations': {'wall_thickness_mm': wall, 'allowances': plan['allowances']},
            'manufacturing_route': plan.get('core_route') or [],
            'validation': {'policy': 'minimum-engineering-v1', 'errors': list(dict.fromkeys(errors)),
                           'warnings': warnings, 'status': 'blocked' if errors else 'pending_human_review'}}
