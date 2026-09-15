# -*- coding: utf-8 -*-
"""渲染 OpenMicroDuck 模型多视角图，用于检查舵机装配重叠。"""
import os
import numpy as np
import mujoco

import sys
XML = sys.argv[1] if len(sys.argv) > 1 else r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\robot_openmicroduck.xml"
OUT = sys.argv[2] if len(sys.argv) > 2 else r"e:\optiDuck\_render_ov"

os.makedirs(OUT, exist_ok=True)
m = mujoco.MjModel.from_xml_path(XML)
d = mujoco.MjData(m)
mujoco.mj_forward(m, d)
W = m.vis.global_.offwidth
H = m.vis.global_.offheight
renderer = mujoco.Renderer(m, H, W)

views = [
    ("front", 90, -20, 0.55), ("back", -90, -20, 0.55), ("left", 0, -20, 0.55),
    ("right", 180, -20, 0.55), ("iso", 45, -25, 0.55), ("top", 0, -80, 0.55),
]
# 特写：头部、躯干舵机、腿部
closeups = [
    ("head", 90, -10, 0.30, (0.02, 0.0, 0.09)),
    ("trunk", 100, -15, 0.30, (0.0, 0.0, 0.02)),
    ("legL", 45, -25, 0.30, (0.02, 0.02, -0.03)),
    ("legR", 45, -25, 0.30, (-0.02, 0.02, -0.03)),
]

from PIL import Image

def save(tag, cam):
    renderer.update_scene(d, camera=cam)
    img = renderer.render()
    path = os.path.join(OUT, f"{tag}.png")
    Image.fromarray(img).save(path)
    print("saved", path)

for tag, az, el, dist in views:
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.azimuth, cam.elevation, cam.distance = az, el, dist
    cam.lookat = np.array([0.0, 0.0, 0.02])
    save(f"view_{tag}", cam)

for tag, az, el, dist, look in closeups:
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.azimuth, cam.elevation, cam.distance = az, el, dist
    cam.lookat = np.array(look)
    save(f"close_{tag}", cam)
