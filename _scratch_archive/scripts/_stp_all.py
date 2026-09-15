from steputils import p21
import numpy as np
from collections import defaultdict

def load_entities(path):
    sf = p21.readfile(path)
    ents = {}
    for ds in sf.data:
        for inst in ds.instances.values():
            if isinstance(inst, p21.ComplexEntityInstance):
                for ent in inst.entities:
                    ents.setdefault(inst.ref, []).append((ent.name, list(ent.params)))
            elif inst.entity:
                ents[inst.ref] = [(inst.entity.name, list(inst.entity.params))]
    return ents

def to_float(x):
    if isinstance(x, p21.TypedParameter):
        return to_float(x.value)
    if isinstance(x, (list, tuple)):
        if len(x) == 1:
            return to_float(x[0])
        return None
    try:
        return float(x)
    except Exception:
        return None

ents = load_entities(r"e:\optiDuck\OpenMicroDuck\cad\HD-1910-C001-20260902.stp")
points = {}
dirs = {}
axes = {}
for ref, elist in ents.items():
    for etype, params in elist:
        if etype == "CARTESIAN_POINT":
            c = [to_float(p) for p in params[1]]
            if all(v is not None for v in c):
                points[ref] = np.array(c, dtype=float)
        elif etype == "DIRECTION":
            d = [to_float(p) for p in params[1]]
            if all(v is not None for v in d):
                dirs[ref] = np.array(d, dtype=float)
        elif etype == "AXIS2_PLACEMENT_3D":
            loc = params[1] if len(params) > 1 else None
            direc = params[2] if len(params) > 2 else None
            axes[ref] = (loc, direc)

def ref_of(p):
    if isinstance(p, p21.Reference):
        return str(p)
    if isinstance(p, (list, tuple)) and len(p) == 2 and p[0] == "REF":
        return p[1]
    return None

def resolve_axis(axref):
    """axref may be Reference or nested; returns (origin, direction_unit)"""
    if axref is None:
        return None, None
    if isinstance(axref, (list, tuple)) and not isinstance(axref, p21.Reference):
        axref = ref_of(axref)
    axname = str(axref)
    loc, direc = axes.get(axname, (None, None))
    if loc is None:
        return None, None
    locref = ref_of(loc)
    dirref = ref_of(direc)
    p = points.get(locref) if locref else None
    d = dirs.get(dirref) if dirref else None
    if p is None:
        return None, None
    if d is not None and np.linalg.norm(d) > 1e-9:
        d = d / np.linalg.norm(d)
    return p, d

cyls = []
for ref, elist in ents.items():
    for etype, params in elist:
        if etype == "CYLINDRICAL_SURFACE":
            r = to_float(params[2])
            axref = params[1] if len(params) > 1 else None
            p, d = resolve_axis(axref)
            if r is not None and p is not None:
                cyls.append({"r": r, "p": p, "d": d, "ref": ref})

print(f"resolved: {len(cyls)}")
# cluster by axis position
bins = defaultdict(list)
for c in cyls:
    key = (round(c["p"][0], 3), round(c["p"][1], 3), round(c["p"][2], 3))
    bins[key].append(c)

print("\n=== ALL clusters (loc -> radii, dir) ===")
for key in sorted(bins.keys()):
    items = bins[key]
    rs = sorted(set(round(c["r"], 3) for c in items))
    ds = set()
    for c in items:
        if c["d"] is not None:
            ds.add(tuple(round(v, 2) for v in c["d"]))
    print(f"  ({key[0]:+.3f},{key[1]:+.3f},{key[2]:+.3f}) r={rs} dirs={list(ds)}")
