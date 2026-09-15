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
points, dirs, axes = {}, {}, {}
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
            axes[ref] = (params[1] if len(params) > 1 else None,
                         params[2] if len(params) > 2 else None)

def ref_of(p):
    if isinstance(p, p21.Reference):
        return str(p)
    return None

def resolve_axis(axref):
    axname = ref_of(axref)
    if not axname:
        return None, None
    loc, direc = axes.get(axname, (None, None))
    p = points.get(ref_of(loc)) if loc is not None else None
    d = dirs.get(ref_of(direc)) if direc is not None else None
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
            p, d = resolve_axis(params[1])
            if r is not None and p is not None:
                cyls.append({"r": r, "p": p, "d": d})

# overall point bounds
pts = np.array(list(points.values()))
print("=== assembly bounds (mm) ===")
print("  min:", pts.min(0))
print("  max:", pts.max(0))
print("  size:", pts.max(0) - pts.min(0))

# output-face mounting holes: r~0.8, direction ~+z (0,0,1)
print("\n=== r≈0.8 holes with dir +z (output-face mount holes) ===")
for c in cyls:
    if abs(c["r"] - 0.8) < 0.01 and c["d"] is not None and abs(c["d"][2]) > 0.99:
        p = c["p"]
        rad = np.hypot(p[0], p[1])
        ang = np.degrees(np.arctan2(p[1], p[0]))
        print(f"  pos=({p[0]:+.3f},{p[1]:+.3f},{p[2]:+.3f}) R={rad:.3f} ang={ang:+.1f}°  d={c['d']}")

print("\n=== r≈0.8 holes with dir -y (side?) ===")
for c in cyls:
    if abs(c["r"] - 0.8) < 0.01 and c["d"] is not None and abs(c["d"][1]) > 0.99:
        p = c["p"]
        print(f"  pos=({p[0]:+.3f},{p[1]:+.3f},{p[2]:+.3f}) d={c['d']}")

print("\n=== r≈0.85 holes (z=0 plane?) ===")
for c in cyls:
    if abs(c["r"] - 0.85) < 0.01 and c["d"] is not None:
        p = c["p"]
        print(f"  pos=({p[0]:+.3f},{p[1]:+.3f},{p[2]:+.3f}) d={c['d']}")

# output shaft features along z
print("\n=== shaft-axis concentric features (x=0,y=0) ===")
for c in cyls:
    p = c["p"]
    if abs(p[0]) < 0.01 and abs(p[1]) < 0.01 and c["d"] is not None and abs(c["d"][2]) > 0.9:
        print(f"  z={p[2]:+.3f} r={c['r']:.3f} (OD {2*c['r']:.2f})")
