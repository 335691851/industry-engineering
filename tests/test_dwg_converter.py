from pathlib import Path
import subprocess
import ezdxf
import pytest
from server import dwg_converter as converter


def test_geometry_gate_detects_text_vertices_and_missing_entities():
    doc=ezdxf.new(); m=doc.modelspace()
    m.add_text('辊筒 Φ150'); poly=m.add_lwpolyline([(0,0),(2,3)])
    before=converter.signature(doc)
    poly.set_points([(0,0),(3,3)])
    assert converter.signature(doc)!=before
    before=converter.signature(doc);m.query('TEXT')[0].dxf.text='辊筒 Φ160'
    assert converter.signature(doc)!=before


def test_bad_native_export_never_overwrites_previous_dwg(tmp_path,monkeypatch):
    source=tmp_path/'input.dxf';ezdxf.new().saveas(source)
    previous=source.with_suffix('.dwg');previous.write_bytes(b'keep-original')
    monkeypatch.setattr(converter,'executable',lambda _:Path('test'))
    def fake(name,args,folder):
        if name=='dxf2dwg':(Path(folder)/'output.dwg').write_bytes(b'not-a-dwg')
    monkeypatch.setattr(converter,'run',fake)
    with pytest.raises(converter.ConversionError):converter.convert_dwg(source)
    assert previous.read_bytes()==b'keep-original'


def test_empty_or_malformed_results_are_rejected(tmp_path):
    path=tmp_path/'empty.dxf';ezdxf.new().saveas(path)
    with pytest.raises(converter.ConversionError):converter.load_checked(path)
    path.write_text('broken')
    with pytest.raises(converter.ConversionError):converter.load_checked(path)


def test_native_timeout_becomes_actionable_error(tmp_path,monkeypatch):
    monkeypatch.setattr(converter,'executable',lambda _:Path('dwg2dxf'))
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('dwg2dxf',90)
    monkeypatch.setattr(subprocess,'run',timeout)
    with pytest.raises(converter.ConversionError,match='90'):
        converter.run('dwg2dxf',['-o','out.dxf','in.dwg'],tmp_path)
