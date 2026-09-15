import trimesh, numpy as np
from collections import defaultdict

d = r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\assets"

def find_cyl_centers(m, axis=2, n=40, rmin=0.001, rmax=0.010):
    """在 axis 向切面找圆（外圆轮廓），返回中心聚类"""
    lo, hi = m.bounds
    bins = defaultdict(list)
    for i in range(n):
        v = lo[axis] + (hi[axis] - lo[axis]) * (i + 0.5) / n
        origin = np.zeros(3); origin[axis] = v
        normal = np.zeros(3); normal[axis] = 1
        sec = m.section(plane_origin=origin, plane_normal=normal)
        if sec is None:
            continue
        try:
            polys = sec.polygons_full
        except Exception:
            continue
        for p in polys:
            if p is None or p.is_empty:
                continue
            # 检测多边形外轮廓（没有洞的圆盘）与带洞的圆环
            reps = [p] + list(p.interiors)
            for poly in reps:
                coords = np.array(poly.coords)
                if len(coords) < 16:
                    continue
                c = coords.mean(0)
                r = float(np.mean(np.linalg.norm(coords - c, axis=1)))
                if rmin <= r <= rmax:
                    # 用拟合圆更准确
                    cc, rr = fit_circle(coords)
                    if rr is not None and rmin <= rr <= rmax:
                        c, r = cc, rr
                    bins[(round(c[0], 3), round(c[1], 3))].append((v, r))
    return bins

def fit_circle(coords):
    """最小二乘拟合圆"""
    x, y = coords[:, 0], coords[:, 1]
    A = np.stack([x, y, np.ones_like(x)], 1)
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0] / 2, sol[1] / 2
    r = np.sqrt(sol[2] + cx * cx + cy * cy)
    # 残差检查
    resid = np.mean(np.abs(np.hypot(x - cx, y - cy) - r))
    if resid / max(r, 1e-6) > 0.05:
        return None, None
    return (cx, cy), r

for f, label in (("xl330.stl", "XL330"), ("hd1910.stl", "HD-1910")):
    m = trimesh.load(d + "\\" + f)
    print(f"===== {label} =====")
    for axis, an in [(2, "z"), (1, "y"), (0, "x")]:
        bins = find_cyl_centers(m, axis)
        if not bins:
            print(f"  {an}向: 无圆特征")
            continue
        print(f"  {an}向圆特征 (中心x,y -> 半径范围, 切片数):")
        for (cx, cy), lst in sorted(bins.items()):
            rs = [r for _, r in lst]
            vs = [v for v, _ in lst]
            print(f"    center=({cx:+.3f},{cy:+.3f}) r={min(rs):.3f}~{max(rs):.3f} n={len(lst)} axis[{min(vs):.3f},{max(vs):.3f}]")
