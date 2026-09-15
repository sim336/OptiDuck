import cadquery as cq, trimesh, numpy as np
w = cq.importers.importStep(r"E:\optiDuck\OpenMicroDuck\cad\HD-1910-C001-20260902.stp")
sol = w.val()
shells = sorted(sol.Solids(), key=lambda s: -s.Volume())[:3]
case = cq.Workplane(obj=shells[0])
for s in shells[1:]:
    case = case.union(cq.Workplane(obj=s))
bb = case.val().BoundingBox()
print("case bbox:", round(bb.xlen,1), round(bb.ylen,1), round(bb.zlen,1))
shaft = cq.Workplane("XY").circle(4.95/2).extrude(5).translate((0,0,bb.zmax))
servo = case.union(shaft)
stl_tmp = r"E:\optiDuck\_hd1910_tmp.stl"
cq.exporters.export(servo.val(), stl_tmp)
m = trimesh.load(stl_tmp)
v = m.vertices.copy()
m.vertices = np.column_stack([v[:,2], v[:,0], v[:,1]])   # (x,y,z)->(z,x,y)
m.vertices = m.vertices / 1000.0
out = r"src\mjlab_microduck\robot\microduck\assets\hd1910.stl"
m.export(out)
print("real servo:", m.bounds[1]-m.bounds[0], "| verts", len(m.vertices))
