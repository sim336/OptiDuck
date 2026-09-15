import cadquery as cq
w = cq.importers.importStep(r"E:\optiDuck\OpenMicroDuck\cad\HD-1910-C001-20260902.stp")
sol = w.val()
# list solids by volume, find shell bodies (big 3)
vols = []
for i, s in enumerate(sol.Solids()):
    v = s.Volume()
    vols.append((v, i, s))
vols.sort(reverse=True)
print("top 6 solids by volume (mm3):")
for v, i, s in vols[:6]:
    bb = s.BoundingBox()
    print(f"  vol={v:10.0f} size=({bb.xlen:.1f},{bb.ylen:.1f},{bb.zlen:.1f})")
