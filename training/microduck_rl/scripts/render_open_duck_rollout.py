#!/usr/bin/env python3
"""Open Duck 策略 rollout → 离屏渲染 PNG 帧（供浏览器播放）。

在【真实 WSL 终端】运行（沙箱内 GL 被拦）。用法：
    python scripts/render_open_duck_rollout.py [checkpoint.pt] [seconds]
默认 checkpoint = logs/rsl_rl/open_duck_zup_fix/ 下最新的 model_*.pt，
输出到 renders/open_duck_latest/（每帧 PNG + index.html 播放页）。
渲染后端：MUJOCO_GL=egl（WSL GPU 离屏）；失败可试 MUJOCO_GL=glfw。
"""
import glob
import os
import sys
import time
from dataclasses import asdict

import torch
import mujoco

from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import MjlabOnPolicyRunner, RslRlVecEnvWrapper
from mjlab.tasks.registry import load_runner_cls

from mjlab_microduck.tasks.microduck_open_duck_env_cfg import (
    make_open_duck_velocity_env_cfg,
)
from mjlab_microduck.tasks.microduck_velocity_env_cfg import MicroduckRlCfg

CKPT = sys.argv[1] if len(sys.argv) > 1 else None
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

if CKPT is None:
    runs = sorted(glob.glob("logs/rsl_rl/open_duck_zup_fix/*_velocity/model_*.pt"))
    CKPT = runs[-1] if runs else None
assert CKPT, "no checkpoint found"
print(f"[render] checkpoint: {CKPT}")

OUT = os.path.abspath("renders/open_duck_latest")
os.makedirs(OUT, exist_ok=True)
for f in glob.glob(os.path.join(OUT, "*.png")):
    os.remove(f)

device = "cpu"
cfg = make_open_duck_velocity_env_cfg(play=True)
cfg.scene.num_envs = 1
env = ManagerBasedRlEnv(cfg=cfg, device=device)
env = RslRlVecEnvWrapper(env, clip_actions=MicroduckRlCfg.clip_actions)

runner_cls = load_runner_cls("Mjlab-Velocity-Flat-OpenDuck") or MjlabOnPolicyRunner
runner = runner_cls(env, asdict(MicroduckRlCfg), device=device)
runner.load(CKPT, load_cfg={"actor": True}, strict=True, map_location=device)
policy = runner.get_inference_policy(device=device)

sim = env.unwrapped.sim
mjm = sim.mj_model
mjd = sim.mj_data

renderer = mujoco.Renderer(mjm, height=540, width=960)
cam = mujoco.MjvCamera()
cam.type = mujoco.mjtCamera.mjCAMERA_FREE
cam.azimuth, cam.elevation, cam.distance = 45.0, -20.0, 1.5
cam.lookat[:] = (0.0, 0.0, 0.25)

FRAME_EVERY = 2  # 每 2 步存 1 帧 → 25 fps 播放（50 fps 仿真）
step = 0
n_steps = int(SECONDS / 0.02)
t0 = time.time()
obs, _ = env.reset()
while step < n_steps:
    with torch.no_grad():
        actions = policy(obs)
    obs, rew, dones, extras = env.step(actions)
    sd = sim.data
    mjd.qpos[:] = sd.qpos[0].cpu().numpy()
    mjd.qvel[:] = sd.qvel[0].cpu().numpy()
    mjd.ctrl[:] = sd.ctrl[0].cpu().numpy()
    mujoco.mj_forward(mjm, mjd)
    if step % FRAME_EVERY == 0:
        renderer.update_scene(mjd, cam)
        img = renderer.render()
        from PIL import Image
        Image.fromarray(img).save(os.path.join(OUT, f"f{step // FRAME_EVERY:05d}.png"))
    step += 1
renderer.close()
nframes = step // FRAME_EVERY
html = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<title>Open Duck 实际运动 rollout</title>
<style>body{margin:0;background:#000;color:#ccc;font-family:sans-serif}
canvas{display:block;max-width:100vw;max-height:94vh;margin:auto}
#bar{position:fixed;top:0;left:0;right:0;background:#111a;padding:6px 12px;font-size:13px}</style></head><body>
<div id=bar>Open Duck 实际运动 · checkpoint %s · 拖动查看 / 空格暂停</div>
<canvas id=cv width=960 height=540></canvas>
<script>
const N=%d, FPS=25, cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const imgs=[]; let i=0, playing=true, first=true;
for(let k=0;k<N;k++){const im=new Image();im.src='f'+String(k).padStart(5,'0')+'.png';imgs.push(im);}
function draw(){const im=imgs[i]; if(im.complete&&im.naturalWidth){ctx.drawImage(im,0,0,cv.width,cv.height);first=false;}}
let last=0;
function loop(ts){if(!first&&playing&&ts-last>=1000/FPS){draw();i=(i+1)%%N;last=ts;}requestAnimationFrame(loop);}
setTimeout(draw,200); requestAnimationFrame(loop);
addEventListener('keydown',e=>{if(e.code==='Space'){playing=!playing;e.preventDefault();}});
cv.addEventListener('click',()=>{playing=!playing;});
</script></body></html>""" % (
    os.path.basename(CKPT), nframes
)
with open(os.path.join(OUT, "index.html"), "w") as f:
    f.write(html)
print(f"[render] done {step} steps ({nframes} frames) in {time.time()-t0:.1f}s -> {OUT}")
