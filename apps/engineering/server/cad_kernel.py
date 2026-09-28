"""OpenCascade/build123d adapter: numerical input only, no model-written code."""
from functools import lru_cache
import json


@lru_cache(maxsize=64)
def _validate(payload):
    g = json.loads(payload)
    try:
        from build123d import Cylinder, Pos, Align
    except ImportError:
        return {'engine': 'build123d', 'status': 'unavailable', 'reason': 'Install requirements-engineering.txt'}
    try:
        shape = g.get('shape_type')
        if shape in ('tube', 'plate'):
            length = g.get('overall_length_mm') if shape == 'tube' else g.get('thickness_mm')
            solid = Cylinder(g['outer_diameter_mm']/2, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
            if g.get('inner_diameter_mm', 0) > 0:
                solid -= Cylinder(g['inner_diameter_mm']/2, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
        elif shape == 'rotational':
            solid = None
            z = 0
            for span in g['segments']:
                segment = Pos(0, 0, z)*Cylinder(span['diameter_mm']/2, span['length_mm'],
                                              align=(Align.CENTER, Align.CENTER, Align.MIN))
                solid = segment if solid is None else solid+segment
                z += span['length_mm']
        else:
            return {'engine': 'build123d', 'status': 'unsupported'}
        box = solid.bounding_box().size
        return {'engine': 'build123d/OpenCascade', 'status': 'valid' if solid.is_valid and solid.volume > 0 else 'invalid',
                'volume_mm3': solid.volume, 'bounding_box_mm': [box.X, box.Y, box.Z],
                'scope': 'base solid only; textual chamfers, threads, grooves are not modeled'}
    except Exception as exc:
        return {'engine': 'build123d', 'status': 'invalid', 'reason': type(exc).__name__}


def validate_solid(geometry):
    keys = ('shape_type', 'overall_length_mm', 'thickness_mm', 'outer_diameter_mm', 'inner_diameter_mm', 'segments')
    return _validate(json.dumps({k: geometry.get(k) for k in keys}, sort_keys=True))
