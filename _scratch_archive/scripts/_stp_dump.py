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
print("total instances:", len(ents))
from collections import Counter
c = Counter()
for ref, elist in ents.items():
    for name, params in elist:
        c[name] += 1
print(dict(c))
# dump first CARTESIAN_POINT, AXIS2_PLACEMENT_3D, CYLINDRICAL_SURFACE
for target in ("CARTESIAN_POINT", "AXIS2_PLACEMENT_3D", "CYLINDRICAL_SURFACE"):
    for ref, elist in ents.items():
        hit = [e for e in elist if e[0] == target]
        if hit:
            print(f"\n--- {target} {ref}:")
            for p in hit[0][1]:
                print("   ", type(p).__name__, repr(p)[:200])
            break
