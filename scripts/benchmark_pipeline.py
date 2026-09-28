"""Run a real provider against a user-selected reference; expected data stays offline."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import ai
from server.domain import complete_draft_geometry
from server.evidence import extract
from server.drawing_iso import draw_pdf_iso, svg_preview_iso
from server.cad_kernel import validate_solid
from scripts.evaluate_engineering import evaluate


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference', required=True)
    parser.add_argument('--assembly', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--case', required=True)
    args = parser.parse_args()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    part = {'id':'benchmark', 'name':args.name, 'drawing_no':'', 'geometry':{}, 'specifications':{},
            'source_pdf':args.reference, 'material':''}
    project = {'source_path':args.assembly, 'analysis':{}, 'messages':[], 'parts':[part], 'mbom_links':[]}
    evidence = extract(Path(args.reference))
    (output/'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    draft = ai.draft_part(project, part, '依据本件参考图生成独立交付图纸，保留尺寸、公差、加工范围及原料状态的可追溯依据。')
    geometry = complete_draft_geometry(draft, part)
    part.update(geometry=geometry, material=draft.get('material',''))
    (output/'actual.json').write_text(json.dumps(geometry, ensure_ascii=False, indent=2), encoding='utf-8')
    draw_pdf_iso(part, output/'drawing.pdf')
    (output/'drawing.svg').write_text(svg_preview_iso(part), encoding='utf-8')
    report = evaluate(geometry, json.loads(Path(args.case).read_text(encoding='utf-8')))
    report['kernel'] = validate_solid(geometry)
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'matched':report['matched'],'total':report['total'],'passed':report['passed']}))
