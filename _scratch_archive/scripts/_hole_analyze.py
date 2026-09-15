import trimesh, numpy as np, os
from shapely.geometry import Polygon

d = r"src\mjlab_microduck\robot\microduck\assets"

def slice_polys(m, axis, n=60):
    """沿 axis 均匀切片，返回 (plane_val, [poly, ...]) 列表"""
    lo, hi = m.bounds
    out = []
    for i in range(n):
        v = lo[axis] + (hi[axis]-lo[axis])*(i+0.5)/n
        origin = np.zeros(3); origin[axis] = v
        normal = np.zeros(3); normal[axis] = 1
        sec = m.section(plane_origin=origin, plane_normal=normal)
        if sec is None: continue
        try:
            polys = sec.polygons_full
        except Exception:
            continue
        real = [p for p in polys if p is not None and not p.is_empty]
        if real: out.append((v, real))
    return out

def find_cylindrical_holes(m, axis, min_radius=0.001, max_radius=0.008, tol=0.0004):
    """在 axis 方向的切片中找圆孔。返回 [(axis_val, center2d, radius)]"""
    holes = []
    for v, polys in slice_polys(m, axis):
        other = [i for i in range(3) if i != axis]
        for poly in polys:
            for interior in poly.interiors:
                coords = np.array(interior.coords)
                if len(coords) < 10: continue
                c = coords.mean(0)
                r = float(np.mean(np.linalg.norm(coords - c, axis=1)))
                if min_radius <= r <= max_radius:
                    holes.append((v, c, r))
    return holes

def cluster_holes(holes, tol=0.0015):
    """把不同切片的同一孔聚成一簇，输出 (center3d, radius, z_extent)"""
    if not holes: return []
    clusters = []
    for v, c, r in holes:
        placed = False
        for cl in clusters:
            c3 = cl["center"]
            other_dist = np.linalg.norm(c - c3[:2])
            if other_dist < tol:
                cl["center"][2] = v  # axis 坐标取平均即可
                cl["radius"].append(r)
                cl["axis_vals"].append(v)
                cl["center"][2] = np.mean(cl["axis_vals"])
                placed = True
                break
        if not placed:
            clusters.append({"center": np.array([c[0], c[1], v]), "radius": [r], "axis_vals": [v]})
    res = []
    for cl in clusters:
        res.append((cl["center"], float(np.mean(cl["radius"])), (min(cl["axis_vals"]), max(cl["axis_vals"]))))
    return res

for f, label in (("hd1910.stl","HD-1910"), ("xl330.stl","XL330")):
    p = os.path.join(d, f)
    m = trimesh.load(p)
    print(f"===== {label} bounds: {m.bounds} =====")
    for axis in range(3):
        holes = find_cylindrical_holes(m, axis)
        cls = cluster_holes(holes)
        if not cls:
            print(f"  {'xyz'[axis]}向: 未检出圆形通孔")
            continue
        print(f"  {'xyz'[axis]}向检出 {len(cls)} 个圆柱孔:")
        for c, r, (vmin, vmax) in cls:
            print(f"    中心({c[0]:+.4f},{c[1]:+.4f},{c[2]:+.4f}) r={r:.4f} 轴向范围[{vmin:.4f},{vmax:.4f}]")
