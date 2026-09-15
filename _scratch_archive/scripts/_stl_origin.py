import trimesh, numpy as np

d = r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\assets"

for f, label in (("xl330.stl", "XL330"), ("hd1910.stl", "HD-1910")):
    m = trimesh.load(d + "\\" + f)
    lo, hi = m.bounds
    print(f"===== {label} =====")
    print(f"  bounds min: [{lo[0]:+.4f} {lo[1]:+.4f} {lo[2]:+.4f}]")
    print(f"  bounds max: [{hi[0]:+.4f} {hi[1]:+.4f} {hi[2]:+.4f}]")
    print(f"  size: [{hi[0]-lo[0]:.4f} {hi[1]-lo[1]:.4f} {hi[2]-lo[2]:.4f}]")
    center = (lo + hi) / 2
    print(f"  center: [{center[0]:+.4f} {center[1]:+.4f} {center[2]:+.4f}]")
    # 质心
    com = m.center_mass
    print(f"  center_mass: [{com[0]:+.4f} {com[1]:+.4f} {com[2]:+.4f}]")
    print()
