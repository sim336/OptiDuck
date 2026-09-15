import cadquery as cq, trimesh, numpy as np
# 1. import STEP, take the 3 shell solids (top volumes, 20x34 plane)
w = cq.importers.importStep(r"E:\optiDuck\OpenMicroDuck\cad\HD-1910-C001-20260902.stp")
sol = w.val()
shells = sorted(sol.Solids(), key=lambda s: -s.Volume())[:3]
case = shells[0].union(shells[1]).union(shells[2])
bb = case.BoundingBox()
print("case bbox:", bb.xlen, bb.ylen, bb.zlen)
# 2. add output shaft along +z (STEP output axis), from STEP: HORN at z=+5ish
shaft = cq.Workplane("XY").circle(4.95/2).extrude(5).translate((0, 0, bb.zmax))
servo = case.union(shaft)
# 3. export STL (mm), then remap verts: new=(old_z, old_x, old_y) to match xl330 layout
stl_tmp = r"E:\optiDuck\_hd1910_tmp.stl"
cq.exporters.export(servo.val(), stl_tmp)
m = trimesh.load(stl_tmp)
v = m.vertices.copy()
m.vertices = np.column_stack([v[:,2], v[:,0], v[:,1]])   # (x,y,z)->(z,x,y)
m.vertices = m.vertices / 1000.0                          # mm -> m
out = r"src\mjlab_microduck\robot\microduck\assets\hd1910.stl"
m.export(out)
print("real servo mesh:", m.bounds[1]-m.bounds[0], "| verts", len(m.vertices))
