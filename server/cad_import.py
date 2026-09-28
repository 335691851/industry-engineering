"""Read CAD with ezdxf/LibreDWG and make a PDF evidence view for the vision model."""

from pathlib import Path
import os

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf

from .dwg_converter import read_dwg


from .native_rpc import native


@native("cad_pdf")
def cad_to_pdf(source: Path, output: Path):
    if source.suffix.lower() == '.dwg':
        doc = read_dwg(source)
    else:
        doc = ezdxf.readfile(source)
    backend = pymupdf.PyMuPdfBackend()
    white = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
    Frontend(RenderContext(doc), backend, config=white).draw_layout(doc.modelspace())
    output.write_bytes(backend.get_pdf_bytes(layout.Page(0, 0)))
    return output
