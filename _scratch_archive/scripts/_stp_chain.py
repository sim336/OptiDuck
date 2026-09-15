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

ents = load_entities(r"e:\optiDuck\OpenMicroDuck\cad\HD-1910-C001-20260902.stp")
# find the CYLINDRICAL_SURFACE that is at origin with radius 3.0 (output shaft)
# first dump a cylinder and follow its axis chain manually
for ref, elist in ents.items():
    for etype, params in elist:
        if etype == "CYLINDRICAL_SURFACE" and len(params) > 2 and isinstance(params[2], float) and abs(params[2]-3.0) < 1e-9:
            axref = params[1]
            print("CYL", ref, "axis=", repr(axref), "r=", params[2])
            if isinstance(axref, p21.Reference):
                axname = str(axref)
                for e2 in ents.get(axname, []):
                    print("  AXIS entity:", e2[0], [repr(p)[:60] for p in e2[1]])
                    if e2[0] == "AXIS2_PLACEMENT_3D":
                        loc = e2[1][1]; direc = e2[1][2]
                        for nm, val in (("loc", loc), ("dir", direc)):
                            if isinstance(val, p21.Reference):
                                pname = str(val)
                                pe = ents.get(pname, [])
                                print(f"    {nm} ref {pname}: {[(e[0], repr(e[1])[:120]) for e in pe]}")
            break
    else:
        continue
    break
