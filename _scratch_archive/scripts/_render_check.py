import mujoco
import numpy as np
from PIL import Image

def save(img, path):
    Image.fromarray(img).save(path)
    print("  saved", path)

jobs = [
    (r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\robot_openmicroduck.xml", "openmicroduck"),
    (r"e:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\robot_walk.xml", "walk_orig"),
]

for xml, tag in jobs:
    m = mujoco.MjModel.from_xml_path(xml)
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    print(f"===== {tag} nbody={m.nbody} ngeom={m.ngeom} =====")
    H = m.vis.global_.offheight
    W = m.vis.global_.offwidth
    print(f"  framebuffer {W}x{H}")
    renderer = mujoco.Renderer(m, H, W)
    # 多种视角：前、后、侧、俯、斜
    cams = {}
    for i in range(m.ncam):
        cams[m.camera(i).name] = i
    print("  cameras:", list(cams.keys()))
    views = []
    # 用现有相机
    for cname in cams:
        views.append(cname)
    # 再加一个自由视角
    for i, vname in enumerate(views):
        renderer.update_scene(d, camera=vname)
        img = renderer.render()
        save(img, rf"e:\optiDuck\_render_{tag}_{i}_{vname}.png")
    # 自定义自由视角（从侧面看舵机）
    for az, el, dist, lab in [(90, -15, 0.6, "front"), (-90, -15, 0.6, "back"), (0, -15, 0.6, "side"), (0, -60, 0.6, "top"), (45, -25, 0.6, "iso")]:
        cam = mujoco.MjvCamera()
        cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        cam.azimuth = az
        cam.elevation = el
        cam.distance = dist
        cam.lookat = np.array([0.0, 0.0, 0.05])
        renderer.update_scene(d, camera=cam)
        img = renderer.render()
        save(img, rf"e:\optiDuck\_render_{tag}_{lab}.png")
    renderer.close()
