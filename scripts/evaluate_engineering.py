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


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('actual')
    parser.add_argument('--case', default='tests/fixtures/tube_case.json')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = evaluate(json.loads(Path(args.actual).read_text(encoding='utf-8-sig')),
                      json.loads(Path(args.case).read_text(encoding='utf-8')))
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'matched': report['matched'], 'total': report['total']}))
