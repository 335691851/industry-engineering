"""Normalize an engineering MBOM as reusable parts plus parent/child usages."""
import re


def candidates_to_plan(analysis):
    """Convert legacy vision candidates; ambiguous relationships remain review items."""
    items = [x for x in analysis.get('parts', []) if isinstance(x, dict) and str(x.get('name', '')).strip()][:50]
    parts, keys_by_name = [], {}
    for index, item in enumerate(items):
        key = str(item.get('key') or item.get('node_key') or f'item_{index}')[:80]
        if any(p['key'] == key for p in parts):
            key = f'item_{index}'
        name = str(item['name']).strip()[:80]
        parts.append({'key': key, 'name': name, 'kind': str(item.get('kind') or '零件'),
                      'drawing_no': str(item.get('drawing_no') or ''),
                      'material': str(item.get('material') or ''),
                      'evidence': str(item.get('evidence') or ''),
                      'confidence': str(item.get('confidence') or '待复核')})
        keys_by_name.setdefault(name, []).append(key)
    if isinstance(analysis.get('links'), list) and analysis['links']:
        model_links = []
        for link in analysis['links'][:150]:
            if isinstance(link, dict):
                model_links.append({'parent_key': link.get('parent_key') or None,
                                    'child_key': link.get('child_key'),
                                    'quantity': link.get('quantity') or 1,
                                    'evidence': str(link.get('evidence') or ''),
                                    'confidence': str(link.get('confidence') or '待复核')})
        candidate = {'parts': parts, 'links': model_links}
        try:
            return validate_plan(candidate)
        except (ValueError, TypeError):
            pass
    links = []
    for index, item in enumerate(items):
        parent_key = item.get('parent_key')
        if parent_key and not any(p['key'] == parent_key for p in parts):
            parent_key = None
        if not parent_key:
            parent_name = str(item.get('parent_name') or '').strip()
            matches = keys_by_name.get(parent_name, [])
            if len(matches) == 1:
                parent_key = matches[0]
            elif not matches:
                # A part named “轴头1-轴” is normally a child of “轴头1”.
                prefixes = [p for p in parts if item['name'].startswith(p['name'] + '-')]
                if prefixes:
                    parent_key = max(prefixes, key=lambda p: len(p['name']))['key']
        links.append({'parent_key': parent_key, 'child_key': parts[index]['key'],
                      'quantity': item.get('quantity') or 1,
                      'evidence': str(item.get('evidence') or ''),
                      'confidence': str(item.get('confidence') or '待复核')})
    return {'parts': parts, 'links': links}


def validate_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError('MBOM 必须是对象')
    parts = plan.get('parts') or []
    links = plan.get('links') or []
    if not isinstance(parts, list) or not isinstance(links, list) or not parts or len(parts) > 50 or len(links) > 150:
        raise ValueError('MBOM 零部件或装配关系数量无效')
    keys = [str(p.get('key') or '') for p in parts]
    if len(set(keys)) != len(keys) or not all(keys):
        raise ValueError('零部件 key 必须唯一且非空')
    key_set = set(keys)
    used, seen_edges = set(), set()
    children = {key: [] for key in keys}
    roots = []
    for link in links:
        child = str(link.get('child_key') or '')
        parent = link.get('parent_key') or None
        pair = (parent, child)
        if child not in key_set or (parent is not None and parent not in key_set) or parent == child:
            raise ValueError('装配关系引用了无效零部件')
        if pair in seen_edges:
            raise ValueError('同一上级不能重复引用相同零件；请调整用量')
        seen_edges.add(pair)
        quantity = float(link.get('quantity') or 0)
        if quantity <= 0 or quantity > 100000:
            raise ValueError('每上级用量无效')
        used.add(child)
        if parent is None:
            roots.append(child)
        else:
            children[parent].append(child)
    if used != key_set or not roots:
        raise ValueError('每个零部件都必须从装配体可达')
    visited, visiting = set(), set()
    def visit(key):
        if key in visiting:
            raise ValueError('MBOM 不能形成循环')
        if key in visited:
            return
        visiting.add(key)
        for child in children[key]:
            visit(child)
        visiting.remove(key)
        visited.add(key)
    for root in roots:
        visit(root)
    if visited != key_set:
        raise ValueError('存在与装配体不连通的零部件')
    for p in parts:
        p['kind'] = '部件' if children[p['key']] else (p.get('kind') or '零件')
    return plan
