"""Minimal OpenCascade adapter: numerical input only, no model-written code.

The production gate only needs primitive topology validation.  Calling the
no-VTK OCP binding directly avoids shipping build123d's notebook, scientific
and machine-learning dependency stack in every engineering deployment.
"""
from functools import lru_cache
import json
import math


@lru_cache(maxsize=64)
def _validate(payload):
    g = json.loads(payload)
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Trsf, gp_Vec
    except ImportError:
        return {'engine': 'OpenCascade', 'status': 'unavailable', 'reason': 'Install requirements-engineering.txt'}

    def cylinder(diameter, length, z=0):
        result = BRepPrimAPI_MakeCylinder(float(diameter) / 2, float(length)).Shape()
        if z:
            transform = gp_Trsf()
            transform.SetTranslation(gp_Vec(0, 0, float(z)))
            result = BRepBuilderAPI_Transform(result, transform, True).Shape()
        return result
    try:
        shape = g.get('shape_type')
        if shape in ('tube', 'plate'):
            length = g.get('overall_length_mm') if shape == 'tube' else g.get('thickness_mm')
            outer = float(g['outer_diameter_mm'])
            inner = float(g.get('inner_diameter_mm') or 0)
            solid = cylinder(outer, length)
            if g.get('inner_diameter_mm', 0) > 0:
                operation = BRepAlgoAPI_Cut(solid, cylinder(inner, length))
                operation.Build()
                if not operation.IsDone():
                    raise ValueError('OpenCascade cut failed')
                solid = operation.Shape()
            volume = math.pi * (outer * outer - inner * inner) * float(length) / 4
            bounds = [outer, outer, float(length)]
        elif shape == 'rotational':
            solid = None
            z = 0
            volume = 0
            largest = 0
            for span in g['segments']:
                diameter, length = float(span['diameter_mm']), float(span['length_mm'])
                segment = cylinder(diameter, length, z)
                if solid is None:
                    solid = segment
                else:
                    operation = BRepAlgoAPI_Fuse(solid, segment)
                    operation.Build()
                    if not operation.IsDone():
                        raise ValueError('OpenCascade fuse failed')
                    solid = operation.Shape()
                volume += math.pi * diameter * diameter * length / 4
                largest = max(largest, diameter)
                z += length
            bounds = [largest, largest, z]
        else:
            return {'engine': 'OpenCascade', 'status': 'unsupported'}
        valid = solid is not None and BRepCheck_Analyzer(solid).IsValid() and volume > 0
        return {'engine': 'OpenCascade/OCP', 'status': 'valid' if valid else 'invalid',
                'volume_mm3': volume, 'bounding_box_mm': bounds,
                'scope': 'base solid only; textual chamfers, threads, grooves are not modeled'}
    except Exception as exc:
        return {'engine': 'OpenCascade/OCP', 'status': 'invalid', 'reason': type(exc).__name__}


def validate_solid(geometry):
    keys = ('shape_type', 'overall_length_mm', 'thickness_mm', 'outer_diameter_mm', 'inner_diameter_mm', 'segments')
    return _validate(json.dumps({k: geometry.get(k) for k in keys}, sort_keys=True))
