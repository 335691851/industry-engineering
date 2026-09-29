"""Build-time native runtime gate; no user drawings or API credentials needed."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import pymupdf
import ezdxf
from rapidocr_onnxruntime import RapidOCR


def check():
    from server.cad_kernel import validate_solid
    solid = validate_solid({'shape_type':'tube','outer_diameter_mm':20,
                            'inner_diameter_mm':10,'overall_length_mm':30})
    assert solid['status'] == 'valid' and solid['bounding_box_mm'] == [20,20,30], solid
    engine = RapidOCR()
    assert engine is not None
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)
        doc = ezdxf.new()
        doc.modelspace().add_circle((0, 0), 10)
        doc.saveas(path / 'probe.dxf')
        from server.cad_import import cad_to_pdf
        cad_to_pdf(path / 'probe.dxf', path / 'probe.pdf')
        with pymupdf.open(path / 'probe.pdf') as pdf:
            assert len(pdf) == 1 and pdf[0].get_pixmap().width > 0
    print('Native gate passed: ONNX OCR initialization, OpenCascade solid, DXF -> PDF rendering.')


if __name__ == '__main__': check()
