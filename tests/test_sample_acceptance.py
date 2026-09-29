import hashlib
import json
from pathlib import Path

import openpyxl
import pymupdf
import pytest

from scripts.evaluate_engineering import evaluate_project
from server import db, main


ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads((ROOT / 'tests/fixtures/assembly_case.json').read_text(encoding='utf-8'))
SAMPLE = ROOT / 'sample/示例'


def test_business_acceptance_evaluator_covers_hierarchy_drawing_and_process():
    parts = []
    ids = {item['name']: str(index) for index, item in enumerate(CASE['mbom']['parts'])}
    for expected in CASE['mbom']['parts']:
        geometry = CASE['drawing_cases']['辊筒']['expected'] if expected['name'] == '辊筒' else {}
        if expected['name'] == '辊筒':
            geometry = {**geometry, 'machining_zones': CASE['drawing_cases']['辊筒']['zones']}
        parts.append({'id': ids[expected['name']], 'name': expected['name'], 'kind': expected['kind'],
                      'parent_id': ids.get(expected['parent']), 'geometry': geometry})
    actual = {'parts': parts, 'analysis': {'assembly_process': {'steps': [
        {'operation': operation} for operation in CASE['assembly_process']['required_operations']]}}}
    report = evaluate_project(actual, CASE)
    assert report['passed'] and report['matched'] == report['total'] == 25


@pytest.mark.skipif(not (SAMPLE / '输入/装配体.pdf').is_file(), reason='用户样例未安装')
def test_installed_user_sample_matches_acceptance_manifest_and_is_readable():
    source = SAMPLE / '输入/装配体.pdf'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == CASE['source_sha256']
    with pymupdf.open(source) as document:
        assert len(document) == 1 and document[0].rect.width > document[0].rect.height
        text = document[0].get_text()
        assert all(value in text for value in ('1971.50', '1550', '150'))
    drawings = list((SAMPLE / '输出/拆解的部件图').glob('*.pdf'))
    assert len(drawings) == 7
    assert all(len(pymupdf.open(path)) >= 1 for path in drawings)
    workbook = openpyxl.load_workbook(SAMPLE / '输出/收卷轴工艺流程单.xlsx', read_only=True, data_only=True)
    try:
        operations = [str(row[1] or '') for row in workbook.active.iter_rows(values_only=True)
                      if row and isinstance(row[0], int)]
    finally:
        workbook.close()
    assert all(any(required in value for value in operations)
               for required in CASE['assembly_process']['required_operations'])


@pytest.mark.skipif(not (SAMPLE / '输入/装配体.pdf').is_file(), reason='用户样例未安装')
def test_sample_seed_matches_business_mbom(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB', tmp_path / 'db.sqlite')
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(db, 'FILES', tmp_path / 'files')
    db.init()
    main.seed_example()
    project = db.project_bundle('sample-reel')
    report = evaluate_project(project, {**CASE, 'drawing_cases': {}, 'assembly_process': {}})
    assert report['passed'], report['checks']
