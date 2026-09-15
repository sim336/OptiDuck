# -*- coding: utf-8 -*-
"""修复 hd1910 舵机壳体相对 xl330 的几何中心偏移。

hd1910.stl 壳体在局部 x 方向不对称（[-22, +8.5]mm），而 xl330 对称（[-14.5, +14.5]mm）。
直接替换 mesh 后壳体向 -x 偏移约 6.75mm，与塑料支撑件重叠。
本脚本对每个 hd1910 geom 沿其局部 +x 方向补偿 (xl330_bbox_center - hd1910_bbox_center)，
使壳体中心回到 xl330 的位置。
"""
import xml.etree.ElementTree as ET
import numpy as np

XML = r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\robot_openmicroduck.xml"

# 包围盒中心差异（mesh 局部坐标，米）
# xl330 bbox center: (0, 0, -0.0075)
# hd1910 bbox center: (-0.00675, 0, -0.0075)
COMP = np.array([0.00675, 0.0, 0.0])  # 沿局部 +x 补偿


def quat_to_mat(q):
    """MuJoCo quat [w,x,y,z] -> 3x3 rotation matrix."""
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


ET.register_namespace("", "")
tree = ET.parse(XML)
root = tree.getroot()

n = 0
for geom in root.iter("geom"):
    if geom.get("mesh") != "hd1910":
        continue
    pos = np.array([float(v) for v in geom.get("pos", "0 0 0").split()])
    quat = np.array([float(v) for v in geom.get("quat", "1 0 0 0").split()])
    R = quat_to_mat(quat)
    shift = R @ COMP  # body 坐标下的补偿
    new_pos = pos + shift
    geom.set("pos", " ".join(f"{v:.10g}" for v in new_pos))
    n += 1
    print(f"  shift(mm)={np.array2string(shift*1000, precision=3)}, pos {pos} -> {new_pos}")

tree.write(XML, encoding="utf-8", xml_declaration=True)
print(f"\n已修正 {n} 个 hd1910 geom，保存到 {XML}")
