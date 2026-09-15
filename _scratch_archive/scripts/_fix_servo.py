# -*- coding: utf-8 -*-
"""尝试修复 hd1910 网格封闭性。"""
import os
import trimesh

ASSETS = r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\assets"
sv = trimesh.load(os.path.join(ASSETS, "hd1910.stl"))
print(f"hd1910 watertight={sv.is_watertight} vol={sv.volume:.6f} "
      f"verts={len(sv.vertices)} faces={len(sv.faces)}")
trimesh.repair.fix_normals(sv)
n = trimesh.repair.fill_holes(sv)
print(f"fill_holes 修复边数={n}")
print(f"修复后 watertight={sv.is_watertight} vol={sv.volume:.6f} "
      f"verts={len(sv.vertices)} faces={len(sv.faces)}")
if not sv.is_watertight:
    print("boundary 边数:", len(sv.edges_unique[trimesh.grouping.group_rows(
        sv.edges_sorted, require_count=1)]))
