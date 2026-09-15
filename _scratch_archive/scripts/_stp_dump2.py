from steputils import p21

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

# find a CYLINDRICAL_SURFACE and dump its full raw structure
for ref, elist in ents.items():
    for etype, params in elist:
        if etype == "CYLINDRICAL_SURFACE":
            print("CYL ref:", ref)
            for i, p in enumerate(params):
                print(f"  param[{i}]: type={type(p).__name__} repr={repr(p)[:120]}")
            break
    else:
        continue
    break

# find an AXIS2_PLACEMENT_3D referenced by that cylinder
# dump one AXIS2_PLACEMENT_3D
for ref, elist in ents.items():
    for etype, params in elist:
        if etype == "AXIS2_PLACEMENT_3D":
            print("\nAXIS ref:", ref)
            for i, p in enumerate(params):
                print(f"  param[{i}]: type={type(p).__name__} repr={repr(p)[:120]}")
            break
    else:
        continue
    break

# CARTESIAN_POINT
for ref, elist in ents.items():
    for etype, params in elist:
        if etype == "CARTESIAN_POINT":
            print("\nPOINT ref:", ref)
            for i, p in enumerate(params):
                print(f"  param[{i}]: type={type(p).__name__} repr={repr(p)[:120]}")
            break
    else:
        continue
    break
