from steputils import p21
import numpy as np

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

def unq(p):
    if isinstance(p, (list, tuple)):
        return [unq(x) for x in p]
    if isinstance(p, p21.TypedParameter):
        return unq(p.value)
    if isinstance(p, p21.Reference):
        return ("REF", str(p))
    if isinstance(p, p21.Keyword):
        return ("KW", str(p))
    return p

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
axes = {}
for ref, elist in ents.items():
    for etype, params in elist:
        if etype == "CARTESIAN_POINT":
            c = [to_float(p) for p in params[1]]
            if all(v is not None for v in c):
                points[ref] = np.array(c, dtype=float)
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

def axis_geom(axref):
    if axref is None:
        return None, None
    if isinstance(axref, (list, tuple)):
        axref = ref_of(axref)
    if axref is None:
        return None, None
    loc, direc = axes.get(axref, (None, None))
    loc = ref_of(loc) if not isinstance(loc, str) else loc
    direc = ref_of(direc) if not isinstance(direc, str) else direc
    p = points.get(loc) if loc else None
    d = points.get(direc) if direc else None
    return p, d

# collect cylinders
cyls = []
for ref, elist in ents.items():
    for etype, params in elist:
        if etype == "CYLINDRICAL_SURFACE":
            r = to_float(params[2])
            axref = params[1] if len(params) > 1 else None
            p, d = axis_geom(axref)
            if r is not None and p is not None:
                cyls.append((r, p, d, ref))
print(f"cylinders resolved: {len(cyls)}")

# cluster by position (same axis line) - group by rounded location
from collections import defaultdict
bins = defaultdict(list)
for r, p, d, ref in cyls:
    key = (round(p[0], 3), round(p[1], 3), round(p[2], 3))
    bins[key].append((r, p, d, ref))

# print clusters with multiple radii (concentric features) — these are holes/bosses
print("\n=== position clusters with 2+ concentric cyl ===")
n = 0
for key, items in sorted(bins.items(), key=lambda kv: (kv[0][2], kv[0][1], kv[0][0])):
    rs = sorted(set(round(r, 3) for r, _, _, _ in items))
    if len(rs) >= 2:
        n += 1
        p0 = items[0][1]
        ds = set(tuple(round(v, 2) for v in items[0][3]) for _, _, d, _ in items if d is not None)
        print(f"  loc=({key[0]:+.3f},{key[1]:+.3f},{key[2]:+.3f}) r={rs} ndir={len(ds)}")
print(f"total multi-radius clusters: {n}")
