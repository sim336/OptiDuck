import numpy as np
from steputils import p21

def load_entities(path):
    sf = p21.readfile(path)
    ents = {}
    for ds in sf.data:
        for inst in ds.instances.values():
            if isinstance(inst, p21.ComplexEntityInstance):
                for ent in inst.entities:
                    ents[inst.ref] = (ent.name, list(ent.params))
            elif inst.entity:
                ents[inst.ref] = (inst.entity.name, list(inst.entity.params))
    return ents

def unq(p):
    """unwrap parameter to plain python: keep Reference/Keyword/numbers/floats, recurse lists/tuples"""
    if isinstance(p, (list, tuple)):
        return [unq(x) for x in p]
    if isinstance(p, p21.TypedParameter):
        return unq(p.value)
    if isinstance(p, p21.Reference):
        return ("REF", str(p))
    if isinstance(p, p21.Keyword):
        return ("KW", str(p))
    if isinstance(p, p21.Enumeration):
        return ("ENUM", str(p))
    return p

def to_float(x):
    while isinstance(x, (list, tuple)):
        if len(x) == 1:
            x = x[0]
        elif x and x[0] == "REF":
            return None
        elif len(x) == 3 and all(isinstance(v, (int, float)) for v in x):
            return None
        else:
            for v in x:
                f = to_float(v)
                if f is not None:
                    return f
            return None
    try:
        return float(x)
    except Exception:
        return None

def analyze(path, label):
    ents = load_entities(path)
    points = {}
    axes = {}
    cyls = []
    for name, (etype, params) in ents.items():
        if etype == "CARTESIAN_POINT":
            coords = [to_float(x) for x in params[1:4]]
            if all(c is not None for c in coords):
                points[name] = coords
        elif etype == "AXIS2_PLACEMENT_3D":
            loc = unq(params[1]) if len(params) > 1 else None
            direc = unq(params[2]) if len(params) > 2 else None
            axes[name] = (loc, direc)
        elif etype == "CYLINDRICAL_SURFACE":
            r = to_float(params[1])
            axref = unq(params[2]) if len(params) > 2 else None
            cyls.append((name, r, axref))
    print(f"===== {label}: points={len(points)} axes={len(axes)} cyl={len(cyls)} =====")
    def resolve_axis(ax):
        # ax is either ('REF','#xx') or ('TYPED','AXIS2_PLACEMENT_3D',[loc,dir])
        loc, direc = None, None
        if ax and ax[0] == "REF":
            loc, direc = axes.get(ax[1], (None, None))
        elif ax and ax[0] == "TYPED":
            loc, direc = ax[2][0], ax[2][1]
        def getp(v):
            if v and v[0] == "REF":
                return points.get(v[1])
            if v and v[0] == "TYPED" and v[1] == "CARTESIAN_POINT":
                return v[2]
            return None
        return getp(loc), getp(direc)
    res = []
    for cname, r, ax in cyls:
        p, d = resolve_axis(ax)
        res.append((cname, r, p, d))
    res.sort(key=lambda t: (tuple(t[2] if t[2] else (0,0,0)), t[1]))
    from collections import Counter
    rcount = Counter(round(x[1], 3) for x in res if x[1] is not None)
    print("  radius histogram:", dict(sorted(rcount.items())))
    for cname, r, p, d in res:
        print(f"  {cname} r={r:.4f} loc={p} dir={d}")
    return res

analyze(r"e:\optiDuck\OpenMicroDuck\cad\HD-1910-C001-20260902.stp", "HD-1910")
