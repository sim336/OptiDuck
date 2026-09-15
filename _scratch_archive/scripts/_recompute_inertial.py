# -*- coding: utf-8 -*-
"""基于当前 robot_openmicroduck.xml 的零件位置（含 _fix_servo_offset 修正后的
hd1910 舵机），用 STL 网格几何 + 平行轴定理精确重算每个 body 的质心、质量、惯性张量。

STL 坐标单位为米（onshape-to-robot 导出），全部用 SI 单位（kg, m, kg/m³）。

策略：
  - 打印件: STL volume × 树脂密度 1150 kg/m³，质心=网格质心，惯性=网格惯性
  - 舵机 hd1910: 21g 固定质量，用 hd1910.stl 几何（有效密度=0.021/volume）
  - "其他"质量(无 STL 的 PCB/电池/轴承等):
      other = 原版总质量 - 原版打印件(ABS 1050) - 原版舵机(XL330 18g)
      质心沿用原版 body 质心位置，作为点质量贡献惯性
  - 只算 class="visual" 的 geom（跳过 collision/self_collision，避免重复）
  - 非 watertight 网格用 convex_hull 回退
  - 若惯性张量非正定（全"其他"质量的 body），回退到原版×质量比
"""
import os
import xml.etree.ElementTree as ET
import numpy as np
import trimesh

ASSETS = r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\assets"
OPEN_XML = r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\robot_openmicroduck.xml"
WALK_XML = r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\robot_walk.xml"

RESIN_DENS = 1150.0   # kg/m³ (1.15 g/cm³)
ABS_DENS = 1050.0     # kg/m³ (1.05 g/cm³)
HD1910_MASS = 0.021   # kg (21g)
XL330_MASS = 0.018    # kg (18g)

_mesh_cache = {}


def load_mesh(name):
    if name in _mesh_cache:
        return _mesh_cache[name]
    p = os.path.join(ASSETS, f"{name}.stl")
    if not os.path.exists(p):
        _mesh_cache[name] = None
        return None
    m = trimesh.load(p, force='mesh')
    _mesh_cache[name] = m
    return m


def quat_to_mat(q):
    w, x, y, z = q
    return np.array([
        [1 - 2*(y*y+z*z), 2*(x*y-w*z),   2*(x*z+w*y)],
        [2*(x*y+w*z),     1-2*(x*x+z*z), 2*(y*z-w*x)],
        [2*(x*z-w*y),     2*(y*z+w*x),  1-2*(x*x+y*y)],
    ])


def geom_T(g):
    pos = np.array([float(v) for v in g.get("pos", "0 0 0").split()])
    quat = np.array([float(v) for v in g.get("quat", "1 0 0 0").split()])
    T = np.eye(4)
    T[:3, :3] = quat_to_mat(quat)
    T[:3, 3] = pos
    return T


def is_visual(g):
    cls = g.get("class", "")
    return "collision" not in cls


def part_props(mesh, T, density, fixed_mass=None):
    """返回 (mass_kg, com_m, I_com_kg_m2) 在 body 坐标系下（SI 单位）。"""
    m = mesh.copy()
    m.apply_transform(T)
    vol = abs(m.volume)  # m³
    # 非 watertight 用 convex_hull 回退
    if vol <= 0 or not m.is_watertight:
        m = m.convex_hull
        vol = abs(m.volume)
    if vol <= 0:
        return 0.0, np.zeros(3), np.zeros((3, 3))
    if fixed_mass is not None:
        mass = fixed_mass
        eff_dens = fixed_mass / vol
    else:
        mass = vol * density
        eff_dens = density
    com = np.array(m.center_mass).copy()
    # 关于质心的惯性张量：把网格平移到质心在原点
    mc = m.copy()
    mc.apply_translation(-com)
    try:
        I_com = np.array(mc.moment_inertia) * eff_dens  # kg·m²
    except Exception:
        I_com = np.zeros((3, 3))
    return mass, com, I_com


def parallel_axis_sum(parts):
    """parts: [(mass, com, I_com), ...] -> (M, C, I_about_C)"""
    parts = [p for p in parts if p[0] > 0]
    M = sum(p[0] for p in parts)
    if M <= 0:
        return 0.0, np.zeros(3), np.zeros((3, 3))
    C = sum(p[0] * p[1] for p in parts) / M
    I = np.zeros((3, 3))
    for mass, com, Ic in parts:
        r = com - C
        I += Ic + mass * (np.dot(r, r) * np.eye(3) - np.outer(r, r))
    return M, C, I


def main():
    # 1. 解析 walk.xml，建立 body -> 原版参数（用于"其他"质量反推）
    wtree = ET.parse(WALK_XML)
    wroot = wtree.getroot()
    walk = {}
    for body in wroot.iter("body"):
        name = body.get("name", "?")
        inert = body.find("inertial")
        old_mass = float(inert.get("mass")) if inert is not None else 0.0  # kg
        old_pos = np.array([float(v) for v in inert.get("pos", "0 0 0").split()]) if inert is not None else np.zeros(3)  # m
        old_ia = inert.get("fullinertia", "").split() if inert is not None else None
        # 原版 visual 零件 ABS 体积 + 舵机数
        off_part = 0.0
        servo_n = 0
        for g in body.findall("geom"):
            if not is_visual(g):
                continue
            mesh = g.get("mesh")
            if not mesh:
                continue
            if mesh == "xl330":
                servo_n += 1
            else:
                m = load_mesh(mesh)
                if m is not None:
                    v = abs(m.volume) if m.is_watertight else abs(m.convex_hull.volume)
                    off_part += v * ABS_DENS
        other = max(0.0, old_mass - off_part - servo_n * XL330_MASS)
        walk[name] = {
            "old_mass": old_mass,
            "old_pos": old_pos,
            "old_ia": old_ia,
            "other": other,
        }

    # 2. 解析 open.xml，重算每个 body
    ET.register_namespace("", "")
    tree = ET.parse(OPEN_XML)
    root = tree.getroot()

    print(f"{'body':22s} {'mass_g':>8s} {'com_mm':>26s} | {'old_g':>8s} {'other_g':>7s}")
    print("-" * 85)
    total = 0.0
    for body in root.iter("body"):
        name = body.get("name", "?")
        w = walk.get(name, {"old_mass": 0.0, "old_pos": np.zeros(3), "old_ia": None, "other": 0.0})
        parts = []
        for g in body.findall("geom"):
            if not is_visual(g):
                continue
            mesh = g.get("mesh")
            if not mesh:
                continue
            m = load_mesh(mesh)
            if m is None:
                continue
            T = geom_T(g)
            if mesh == "hd1910":
                parts.append(part_props(m, T, RESIN_DENS, fixed_mass=HD1910_MASS))
            elif mesh == "xl330":
                parts.append(part_props(m, T, RESIN_DENS, fixed_mass=XL330_MASS))
            else:
                parts.append(part_props(m, T, RESIN_DENS))
        # "其他"质量作为点质量，质心沿用原版 body 质心
        other = w["other"]
        if other > 0:
            parts.append((other, w["old_pos"].copy(), np.zeros((3, 3))))

        M, C, I = parallel_axis_sum(parts)

        # 正定性检查：若非正定，回退到原版×质量比
        fallback = False
        try:
            eig = np.linalg.eigvalsh(I)
            if M <= 0 or np.any(eig <= 0) or np.any(~np.isfinite(eig)):
                fallback = True
        except Exception:
            fallback = True
        if fallback and w["old_mass"] > 0 and w.get("old_ia"):
            scale = M / w["old_mass"] if w["old_mass"] > 0 else 1.0
            old_ia = [float(x) * scale for x in w["old_ia"]]
            I = np.zeros((3, 3))
            I[0, 0], I[1, 1], I[2, 2] = old_ia[0], old_ia[1], old_ia[2]
            I[0, 1], I[0, 2], I[1, 2] = old_ia[3], old_ia[4], old_ia[5]
            I[1, 0], I[2, 0], I[2, 1] = old_ia[3], old_ia[4], old_ia[5]
            C = w["old_pos"].copy()

        # 更新 inertial（MuJoCo fullinertia: Ixx Iyy Izz Ixy Ixz Iyz）
        inert = body.find("inertial")
        if inert is None:
            inert = ET.SubElement(body, "inertial")
        inert.set("mass", f"{M:.10g}")
        inert.set("pos", " ".join(f"{v:.10g}" for v in C))
        ia = [I[0, 0], I[1, 1], I[2, 2], I[0, 1], I[0, 2], I[1, 2]]
        inert.set("fullinertia", " ".join(f"{v:.10g}" for v in ia))
        old_g = w["old_mass"] * 1000
        tag = " [fallback]" if fallback else ""
        print(f"{name:22s} {M*1000:8.2f} [{C[0]*1000:7.2f} {C[1]*1000:7.2f} {C[2]*1000:7.2f}] | {old_g:8.2f} {other*1000:7.2f}{tag}")
        total += M

    print("-" * 85)
    print(f"总质量: {total*1000:.1f} g ({total:.3f} kg)")
    tree.write(OPEN_XML, encoding="utf-8", xml_declaration=True)
    print(f"\n已保存: {OPEN_XML}")


if __name__ == "__main__":
    main()
