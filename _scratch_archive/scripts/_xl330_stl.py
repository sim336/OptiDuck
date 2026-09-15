import trimesh, numpy as np

m = trimesh.load(r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\assets\xl330.stl")
print("=== XL330 STL ===")
print("bounds:", m.bounds)
print("verts:", len(m.vertices), "faces:", len(m.faces))

# 顶点/面数多不多？看是否适合做切面分析
# 简单检查：外壳包围盒尺寸
size = m.bounds[1] - m.bounds[0]
print("size (mm):", size)

# 尝试切面分析圆形孔
def find_circles(axis, n=40, rmin=0.0005, rmax=0.006, tol=0.0003):
    from shapely.geometry import Polygon
    lo, hi = m.bounds[:, axis]
    out = []
    for i in range(n):
        v = lo + (hi - lo) * (i + 0.5) / n
        origin = np.zeros(3); origin[axis] = v
        normal = np.zeros(3); normal[axis] = 1
        sec = m.section(plane_origin=origin, plane_normal=normal)
        if sec is None: continue
        try:
            polys = sec.polygons_full
        except Exception:
            continue
        for p in polys:
            if p is None or p.is_empty: continue
            for interior in p.interiors:
                coords = np.array(interior.coords)
                if len(coords) < 12: continue
                c = coords.mean(0)
                r = float(np.mean(np.linalg.norm(coords - c, axis=1)))
                if rmin <= r <= rmax:
                    out.append((v, c, r))
    return out

for axis in range(3):
    holes = find_circles(axis)
    # 聚类
    from collections import defaultdict
    bins = defaultdict(list)
    for v, c, r in holes:
        key = (round(c[0], 3), round(c[1], 3))
        bins[key].append((v, r))
    if not bins:
        print(f"  {'xyz'[axis]}向: 无小圆孔")
        continue
    print(f"  {'xyz'[axis]}向小圆孔簇:")
    for (cx, cy), lst in sorted(bins.items()):
        vs = [x[0] for x in lst]
        rs = [x[1] for x in lst]
        print(f"    center=({cx:+.3f},{cy:+.3f}) z[{min(vs):.3f},{max(vs):.3f}] r={np.mean(rs):.4f} (OD {2*np.mean(rs):.3f}) n={len(lst)}")
