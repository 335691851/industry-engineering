import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from scripts.package_cloud_compute import package


@pytest.fixture(scope='module')
def compute(tmp_path_factory):
    target = package(tmp_path_factory.mktemp('cloud-compute'))
    sys.path.insert(0, str(target))
    spec = importlib.util.spec_from_file_location('cloud_compute', target / 'api/index.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.path.remove(str(target))


def test_cloud_reuses_local_rules_and_never_approves(compute):
    from server.engineering_model import build_model
    geometry = {'shape_type': 'tube', 'outer_diameter_mm': 100, 'inner_diameter_mm': 80,
                'overall_length_mm': 200, 'manufacturing': {'stock_dimensions': {'outer_diameter_mm': 104}}}
    assert compute.validate_payload({'geometry': geometry, 'part': {'id': 'tube'}}) == build_model(geometry, {'id': 'tube'})
    geometry['manufacturing']['stock_dimensions']['outer_diameter_mm'] = 90
    assert compute.validate_payload({'geometry': geometry})['validation']['status'] == 'blocked'


@pytest.mark.parametrize('geometry', [
    {'segments': 'wrong'}, {'cad_document': {'drawing_dxf': 'C:/file.dxf'}},
    {'outer_diameter_mm': float('nan')}, {'manufacturing': {'allowances': ['wrong']}},
])
def test_rejects_invalid_or_local_only_data(compute, geometry):
    with pytest.raises(ValueError):
        compute.validate_payload({'geometry': geometry})


def test_endpoint_fails_closed_and_accepts_authorized_input(compute, monkeypatch):
    def invoke(token, body):
        request = object.__new__(compute.handler)
        raw = json.dumps(body).encode()
        request.headers = {'Authorization': token, 'Content-Type': 'application/json', 'Content-Length': str(len(raw))}
        request.rfile = io.BytesIO(raw)
        result = []
        request.reply = lambda status, payload: result.append((status, payload))
        request.do_POST()
        return result[0]
    monkeypatch.delenv('ENGINEERING_COMPUTE_TOKEN', raising=False)
    assert invoke('', {})[0] == 503
    monkeypatch.setenv('ENGINEERING_COMPUTE_TOKEN', 'x' * 40)
    assert invoke('Bearer wrong', {})[0] == 401
    code, report = invoke('Bearer ' + 'x' * 40, {'geometry': {}})
    assert code == 200 and report['validation']['status'] == 'blocked'


def test_local_database_cannot_silently_become_ephemeral(monkeypatch):
    from server.runtime_policy import require_local_persistence
    monkeypatch.setenv('VERCEL', '1')
    with pytest.raises(RuntimeError):
        require_local_persistence()
