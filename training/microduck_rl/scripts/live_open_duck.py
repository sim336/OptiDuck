#!/usr/bin/env python3
"""Open Duck 实时策略查看器（与官方 play 等效，供真实 WSL 终端使用）。

照抄 mjlab NativeMujocoViewer 核心链路（warp 仿真 + CPU MjModel 渲染同步 +
runner policy 推理），剥离 reward-figures / perturbation 等附加功能。
官方 `uv run play Mjlab-Velocity-Flat-OpenDuck --checkpoint-file ...` 功能更全，
本脚本仅在需要轻量/可控查看时使用（例如沙箱内窗口不可用时在真实终端手动跑）。

用法：
    python scripts/live_open_duck.py [checkpoint.pt] [num_envs]
默认取 logs/rsl_rl/velocity/ 下最新的 model_*.pt。
"""
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
NUM_ENVS = int(sys.argv[2]) if len(sys.argv) > 2 else 1

if CKPT is None:
    import glob
    runs = sorted(glob.glob("logs/rsl_rl/velocity/*_velocity/model_*.pt"))
    CKPT = runs[-1] if runs else None
assert CKPT, "no checkpoint given"
print(f"[live] checkpoint: {CKPT}")

device = "cpu"
cfg = make_open_duck_velocity_env_cfg(play=True)
cfg.scene.num_envs = NUM_ENVS
env = ManagerBasedRlEnv(cfg=cfg, device=device)
env = RslRlVecEnvWrapper(env, clip_actions=MicroduckRlCfg.clip_actions)

runner_cls = load_runner_cls("Mjlab-Velocity-Flat-OpenDuck") or MjlabOnPolicyRunner
runner = runner_cls(env, asdict(MicroduckRlCfg), device=device)
runner.load(CKPT, load_cfg={"actor": True}, strict=True, map_location=device)
policy = runner.get_inference_policy(device=device)

sim = env.unwrapped.sim
mjm = sim.mj_model
mjd = sim.mj_data if NUM_ENVS == 1 else mujoco.MjData(mjm)
assert mjm is not None and mjd is not None

viewer = mujoco.viewer.launch_passive(
    mjm, mjd, show_left_ui=False, show_right_ui=False,
)
if viewer is None:
    raise RuntimeError("Failed to launch MuJoCo viewer")

obs, _ = env.reset()
t_last = time.time()
step = 0
while viewer.is_running():
    with torch.no_grad():
        actions = policy(obs)
    obs, rew, dones, extras = env.step(actions)
    step += 1

    # copy env-0 state into the CPU MjData buffer for rendering
    sd = sim.data
    mjd.qpos[:] = sd.qpos[0].cpu().numpy()
    mjd.qvel[:] = sd.qvel[0].cpu().numpy()
    mjd.ctrl[:] = sd.ctrl[0].cpu().numpy()
    mujoco.mj_forward(mjm, mjd)
    viewer.sync()

    if step % 50 == 0:
        z = sd.qpos[0, 2].item()
        fell = extras.get("Episode_Termination", {}).get("fell_over", False)
        print(f"[live] step={step} trunk_z={z:.3f} fell={fell}")

    # ~30 FPS cap
    dt = 1.0 / 30.0 - (time.time() - t_last)
    if dt > 0:
        time.sleep(dt)
    t_last = time.time()

print("[live] viewer closed")
