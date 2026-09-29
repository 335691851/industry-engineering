"""Offline case scoring, deliberately never imported by production generation."""
import argparse
import json
from pathlib import Path


def evaluate(actual, case):
    actual = actual.get('geometry', actual)
    rows = []
    for key, expected in case['expected'].items():
        value = actual.get(key)
        passed = (isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value-expected) < .001
                  if type(expected) in (float, int) else value == expected)
        rows.append({'field': key, 'expected': expected, 'actual': value, 'passed': passed})
    for zone in case['zones']:
        passed = any(z.get('end') == zone['end'] and z.get('length_mm') == zone['length_mm']
                     for z in actual.get('machining_zones', []))
        rows.append({'field': 'machining_zone.'+zone['end'], 'expected': zone['length_mm'], 'passed': passed})
    return {'case_id': case['case_id'], 'passed': all(r['passed'] for r in rows),
            'matched': sum(r['passed'] for r in rows), 'total': len(rows), 'checks': rows,
            'limitations': 'Only annotated numeric/semantic baseline; drawing visual review and route review required separately.'}


def evaluate_project(actual, case):
    """Score a project bundle against user supplied business acceptance data.

    The case file is test data only.  Production prompts never import it.
    """
    parts = {item.get('name'): item for item in actual.get('parts', [])}
    by_id = {item.get('id'): item.get('name') for item in actual.get('parts', [])}
    rows = []
    for expected in case.get('mbom', {}).get('parts', []):
        item = parts.get(expected['name'])
        parent = by_id.get(item.get('parent_id')) if item else None
        passed = bool(item and item.get('kind') == expected['kind'] and parent == expected.get('parent'))
        rows.append({'field': 'mbom.' + expected['name'], 'expected': expected,
                     'actual': {'kind': item.get('kind'), 'parent': parent} if item else None,
                     'passed': passed})
    process = (actual.get('analysis') or {}).get('assembly_process') or {}
    operations = [str(step.get('operation') or '') for step in process.get('steps', [])]
    for operation in case.get('assembly_process', {}).get('required_operations', []):
        rows.append({'field': 'assembly_process.' + operation, 'expected': operation,
                     'actual': operations, 'passed': any(operation in value for value in operations)})
    for name, geometry_case in case.get('drawing_cases', {}).items():
        item = parts.get(name) or {}
        report = evaluate(item.get('geometry') or {}, geometry_case)
        for check in report['checks']:
            rows.append({**check, 'field': f'drawing.{name}.' + check['field']})
    return {'case_id': case['case_id'], 'passed': bool(rows) and all(r['passed'] for r in rows),
            'matched': sum(r['passed'] for r in rows), 'total': len(rows), 'checks': rows,
            'limitations': 'Business baseline scoring does not replace human approval or standards compliance gates.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('actual')
    parser.add_argument('--case', default='tests/fixtures/tube_case.json')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    actual = json.loads(Path(args.actual).read_text(encoding='utf-8-sig'))
    case = json.loads(Path(args.case).read_text(encoding='utf-8'))
    report = evaluate_project(actual, case) if 'mbom' in case else evaluate(actual, case)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'matched': report['matched'], 'total': report['total']}))
