"""CAD export rendering lives in the native service; DB commits stay in Agent API."""
from pathlib import Path
import ezdxf
from ezdxf.addons.drawing import Frontend,RenderContext,config,layout,svg,pymupdf
from .native_rpc import native

@native('cad_artifacts')
def render_artifacts(source:Path,layout_name):
    from .dwg_converter import convert_dwg,ConversionError
    doc=ezdxf.readfile(source)
    options=config.Configuration(background_policy=config.BackgroundPolicy.WHITE,color_policy=config.ColorPolicy.BLACK)
    svg_backend=svg.SVGBackend()
    Frontend(RenderContext(doc),svg_backend,config=options).draw_layout(doc.layouts.get(layout_name))
    try: image=svg_backend.get_string(layout.Page(0,0))
    except ValueError as exc:
        raise ValueError('所选布局没有可输出的可见图元，请选择 Model 或含图形的布局后保存') from exc
    source.with_suffix('.svg').write_text(image,encoding='utf-8')
    backend=pymupdf.PyMuPdfBackend()
    Frontend(RenderContext(doc),backend,config=options).draw_layout(doc.layouts.get(layout_name))
    pdf=source.with_suffix('.pdf');pdf.write_bytes(backend.get_pdf_bytes(layout.Page(420,297)))
    warning='';dwg=None
    try: dwg=convert_dwg(source)
    except (OSError,ConversionError) as exc: warning=str(exc)
    return {'drawing_pdf':str(pdf),'drawing_dxf':str(source),'drawing_dwg':str(dwg) if dwg else '',
            'svg':str(source.with_suffix('.svg')),'dwg_warning':warning}
