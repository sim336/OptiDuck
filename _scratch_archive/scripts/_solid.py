import trimesh, os
PARTS = r"E:\optiDuck\OpenMicroDuck\cad\parts"
# 100% infill: material usage = actual solid volume of the model (fill the shell)
# trimesh volume gives shell volume for hollow STL; 100% infill fills interior too.
# Approximate solid usage as bbox volume * occupancy (model solid ratio ~0.5-0.7)
total_bbox = 0.0
total_shell = 0.0
n = 0
for name in sorted(os.listdir(PARTS)):
    if not name.endswith(".stl"): continue
    m = trimesh.load(os.path.join(PARTS, name))
    sz = m.bounds[1]-m.bounds[0]
    bbox = sz[0]*sz[1]*sz[2]/1000
    shell = abs(m.volume)/1000 if m.volume else 0
    total_bbox += bbox
    total_shell += shell
    n += 1
print(f"parts: {n}")
print(f"shell total: {total_shell:.0f} cm3")
print(f"bbox total:  {total_bbox:.0f} cm3")
# 100% infill realistic usage: shell + filling interior. For these shell models,
# slicer fills the enclosed space -> usage between shell and bbox, ~ 60-75% of bbox
for occ in (0.5, 0.6, 0.7):
    mass = total_bbox * occ * 1.15
    print(f"occupancy {occ}: solid mass ~ {mass:.0f} g  (resin 1.15 g/cm3)")
