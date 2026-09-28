"""Deterministic mechanical-drawing profile used by every exporter.

ISO publications are the normative design basis.  This module contains the
small, implementation-level profile used by the application; it is not a copy
of the standards and does not replace an authorised standards library.
"""
from __future__ import annotations

from dataclasses import dataclass
import re



PRIMARY_STANDARD = {
    "code": "ISO 128-1:2020",
    "title": "Technical product documentation — General principles of representation",
    "url": "https://www.iso.org/standard/65296.html",
}

SUPPORTING_STANDARDS = (
    ("ISO 128-2:2022", "线型、指引线与参考线", "https://www.iso.org/standard/83355.html"),
    ("ISO 128-3:2022", "视图、剖视与断面", "https://www.iso.org/standard/83356.html"),
    ("ISO 129-1:2018+Amd 1:2020", "尺寸与公差标注", "https://www.iso.org/standard/64007.html"),
    ("ISO 5455:1979", "工程图比例及标识", "https://www.iso.org/standard/11500.html"),
    ("ISO 5456-2:1996", "正投影表示法", "https://www.iso.org/standard/11502.html"),
    ("ISO 5457:1999+Amd 1:2010", "图纸幅面与布局", "https://www.iso.org/standard/29017.html"),
    ("ISO 7200:2004", "标题栏与文档字段", "https://www.iso.org/standard/35446.html"),
)


@dataclass(frozen=True)
class DrawingProfile:
    sheet_name: str = "A3"
    sheet_width_mm: float = 420.0
    sheet_height_mm: float = 297.0
    binding_margin_mm: float = 20.0
    other_margin_mm: float = 10.0
    thick_line_mm: float = 0.7
    thin_line_mm: float = 0.35
    text_small_mm: float = 2.5
    text_normal_mm: float = 3.5
    text_title_mm: float = 5.0
    arrow_mm: float = 3.5
    extension_gap_mm: float = 1.0
    extension_overshoot_mm: float = 2.0
    projection: str = "第一角投影"
    dimension_unit: str = "mm"


PROFILE = DrawingProfile()

# Preferred ratios from the conventional ISO scale series used by this system.
PREFERRED_SCALES = (10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01)


def choose_scale(real_width_mm: float, real_height_mm: float,
                 available_width_mm: float, available_height_mm: float) -> tuple[float, str]:
    """Choose the largest preferred scale that fits the allocated drawing field."""
    if min(real_width_mm, real_height_mm, available_width_mm, available_height_mm) <= 0:
        return 1.0, "1:1"
    limit = min(available_width_mm / real_width_mm, available_height_mm / real_height_mm)
    candidates = list(PREFERRED_SCALES)
    while candidates[-1] > limit:
        candidates.extend([candidates[-1] * .5, candidates[-1] * .2, candidates[-1] * .1])
    ratio = next(item for item in candidates if item <= limit)
    if ratio >= 1:
        label = f"{ratio:g}:1"
    else:
        label = f"1:{1 / ratio:g}"
    return ratio, label


def format_number(value: float) -> str:
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def dimensional_text(value: float, tolerance: str = "", prefix: str = "") -> str:
    # Model explanations belong in review notes, never on a dimension line.
    suffix = str(tolerance or '').strip().replace('−', '-').replace('＋', '+')
    suffix = re.sub(r'^[ΦφØø]\s*' + re.escape(format_number(value)) + r'\s*', '', suffix)
    suffix = re.split(r'[（(]', suffix, maxsplit=1)[0].strip()
    numeric = r'(?:±\s*\d+(?:\.\d+)?|[+-]\s*\d+(?:\.\d+)?\s*/\s*[+-]?\s*\d+(?:\.\d+)?)'
    fit = r'[A-Za-z]{1,2}\d{1,2}'
    if not re.fullmatch(rf'(?:{numeric}|{fit})', suffix):
        suffix = ''
    return f"{prefix}{format_number(value)}{suffix}"


def drawing_standard_payload() -> dict:
    return {
        "primary": PRIMARY_STANDARD,
        "supporting": [
            {"code": code, "purpose": purpose, "url": url}
            for code, purpose, url in SUPPORTING_STANDARDS
        ],
        "profile": {
            "sheet": PROFILE.sheet_name,
            "sheet_size_mm": [PROFILE.sheet_width_mm, PROFILE.sheet_height_mm],
            "line_widths_mm": {"visible": PROFILE.thick_line_mm, "thin": PROFILE.thin_line_mm},
            "text_heights_mm": [PROFILE.text_small_mm, PROFILE.text_normal_mm, PROFILE.text_title_mm],
            "projection": PROFILE.projection,
            "unit": PROFILE.dimension_unit,
        },
    }


def validate_geometry(part: dict) -> list[str]:
    """Return deterministic blockers for creating a production-review drawing."""
    from .manufacturing import geometry_blockers
    issues = geometry_blockers(part.get('geometry') or {})
    if not part.get('drawing_no'):
        issues.append('标题栏图号缺失')
    if not part.get('material'):
        issues.append('标题栏材料缺失')
    return issues
