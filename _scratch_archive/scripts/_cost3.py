# -*- coding: utf-8 -*-
import trimesh, os
PARTS = r"E:\optiDuck\OpenMicroDuck\cad\parts"
DENS = 1.15  # resin g/cm3 (avg)
# 分组: (文件, 数量, 填充) ; group in {"T":韧性,"N":普通,"F":柔性}
G = {}
for f in ["hip_l","hip_l1","upper_leg_left","upper_leg_right","leg","leg2","ankle_left","ankle_right1",
          "foot_left","foot_right","upper_leg_rigidity_plate","upper_leg_rigidity_plate1",
          "trunk_base","trunk_base1","motor_support","power_support","banana_pcb_locker",
          "bearing_roll","bearing_roll1","neck_pitch","neck","neck1","yaw2roll","yaw2roll1","yaw_roll_motion"]:
    G[f]=("T",0.35)
for f in ["bottom_head_shell","top_head_shell","left_shell","right_shell","face_part","noenoeil","jaw"]:
    G[f]=("N",0.25)
for f in ["sole_left","sole_right","jaw_soft","soft_mouth_top"]:
    G[f]=("F",0.40)

tot = {"T":0.0, "N":0.0, "F":0.0}
vol_tot = {"T":0.0, "N":0.0, "F":0.0}
for name in sorted(os.listdir(PARTS)):
    if not name.endswith(".stl"): continue
    stem = name[:-4]
    if stem not in G: continue
    grp, fill = G[stem]
    qty = 2 if any(x in stem for x in ["_left","_right","_1","hip","leg","ankle","foot","sole","trunk_base","rigidity","bearing","yaw2roll","neck"]) else 1
    # 简单数量规则：成对件
    if stem in ["motor_support","power_support","banana_pcb_locker","neck_pitch","yaw_roll_motion","bottom_head_shell","top_head_shell","face_part","jaw","jaw_soft","soft_mouth_top","left_shell","right_shell"]:
        qty = 1
    m = trimesh.load(os.path.join(PARTS, name))
    sz = m.bounds[1]-m.bounds[0]
    bbox_cm3 = sz[0]*sz[1]*sz[2]/1000
    vol_cm3 = abs(m.volume)/1000 if m.volume else 0
    # 实际用量取 bbox*fill 和 壳体积*2 的平均（折中）
    use_cm3 = (bbox_cm3*fill + vol_cm3*1.8)/2
    mass = use_cm3 * DENS * qty
    tot[grp] += mass
    vol_tot[grp] += use_cm3*qty

PR = {"T":180, "N":115, "F":0}  # 元/kg
print("== 分组用量与成本 ==")
for g in ["T","N","F"]:
    name = {"T":"高韧性树脂 180/kg","N":"普通树脂 115/kg","F":"柔性(不可树脂)"}[g]
    print(f"{name:22s}: {tot[g]:6.0f} g -> {tot[g]/1000*PR[g]:6.0f} 元" if PR[g] else f"{name:22s}: {tot[g]:6.0f} g -> 另做(FDM TPU/橡胶)")
print("\n== 方案对比 ==")
t,n = tot["T"], tot["N"]
print(f"A 全高韧性树脂: {(t+n)/1000*180:.0f} 元")
print(f"B 混合(承力韧性+外观普通): {t/1000*180:.0f} + {n/1000*115:.0f} = {(t*180+n*115)/1000:.0f} 元  <== 推荐")
print(f"C 全普通树脂: {(t+n)/1000*115:.0f} 元 (承力件会断,不可取)")
print(f"\n柔性件(鞋底/软嘴)需另做: 约 {tot['F']/1000:.1f} kg TPU/橡胶 -> ~30-80 元")
