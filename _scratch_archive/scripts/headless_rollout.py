"""Headless rollout: per-step foot heights / trunk / terminations to diagnose hopping."""
from dataclasses import asdict

import numpy as np
import torch

from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import MjlabOnPolicyRunner, RslRlVecEnvWrapper
from mjlab.tasks.registry import load_env_cfg, load_rl_cfg, load_runner_cls
from mjlab.utils.torch import configure_torch_backends

configure_torch_backends()
task_id = "Mjlab-Velocity-Flat-MicroDuck"
device = "cuda:0"

env_cfg = load_env_cfg(task_id, play=True)
agent_cfg = load_rl_cfg(task_id)
env_cfg.scene.num_envs = 1

env = ManagerBasedRlEnv(cfg=env_cfg, device=device)
env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)

runner_cls = load_runner_cls(task_id) or MjlabOnPolicyRunner
runner = runner_cls(env, asdict(agent_cfg), device=device)
runner.load(
    r"logs\rsl_rl\velocity\2026-09-15_15-42-41_velocity_pure_official\model_3750.pt",
    load_cfg={"actor": True},
    strict=True,
    map_location=device,
)
policy = runner.get_inference_policy(device=device)

robot = env.unwrapped.scene["robot"]
mj_model = env.unwrapped.sim.mj_model
body_names = [mj_model.body(i).name for i in range(mj_model.nbody)]

left_foot = body_names.index("robot/ankle_left")
right_foot = body_names.index("robot/ankle_right")
trunk = body_names.index("robot/trunk_base")
print(f"ids: left_foot={left_foot} right_foot={right_foot} trunk={trunk}")

xpos = env.unwrapped.sim.data.xpos  # (1, N, 3)

obs, extras = env.reset()

# foot rest height = ankle z when standing on flat ground (roughly foot radius)
lz0 = float(xpos[0, left_foot, 2])
rz0 = float(xpos[0, right_foot, 2])
tz0 = float(xpos[0, trunk, 2])
print(f"\nINITIAL: LfootZ={lz0:.4f} RfootZ={rz0:.4f} TrunkZ={tz0:.4f}")

rows = []
ep_done = False
for step in range(400):
    with torch.no_grad():
        action = policy(obs)
    obs, rew, dones, extras = env.step(action)
    done = bool(dones[0].item()) if hasattr(dones, "__getitem__") else False
    lz = float(xpos[0, left_foot, 2])
    rz = float(xpos[0, right_foot, 2])
    tz = float(xpos[0, trunk, 2])
    rows.append((step, lz, rz, tz, done))
    if step == 0 and done:
        pass

print("\nSTEP  LfootZ  RfootZ  TrunkZ   done")
for step, lz, rz, tz, done in rows[:30]:
    print(f"{step:4d}  {lz:6.4f}  {rz:6.4f}  {tz:6.4f}   {done}")

lz = np.array([r[1] for r in rows])
rz = np.array([r[2] for r in rows])
tz = np.array([r[3] for r in rows])
donearr = np.array([r[4] for r in rows])

rest = min(lz.min(), rz.min())
air_l = lz - rest
air_r = rz - rest
both_air = ((air_l > 0.01) & (air_r > 0.01)).mean()
any_air = ((air_l > 0.01) | (air_r > 0.01)).mean()
print(f"\nfoot rest z ~ {rest:.4f}")
print(f"both feet airborne (>1cm) fraction: {both_air:.1%}")
print(f"any foot airborne  (>1cm) fraction: {any_air:.1%}")
print(f"trunk z range: {tz.min():.4f} .. {tz.max():.4f}")
print(f"terminated steps: {donearr.sum()} / {len(donearr)}")

# Steady-state sampling (already not using first 50 transient steps)
for lo in (50, 150, 250, 350):
    hi = lo + 10
    print(f"\n--- steps {lo}-{hi} ---")
    for step, l, r, t, done in rows[lo:hi]:
        print(f"{step:4d}  {l:6.4f}  {r:6.4f}  {t:6.4f}   {done}")
ss = slice(50, None)
print(f"\nsteady-state (step>=50): trunk mean={tz[ss].mean():.4f} min={tz[ss].min():.4f} max={tz[ss].max():.4f}")
print(f"steady-state: Lfoot mean={lz[ss].mean():.4f}  Rfoot mean={rz[ss].mean():.4f}")
# first step both feet airborne?
first_air_step = None
for s, l, r, t, d in rows:
    if l > rest + 0.01 and r > rest + 0.01:
        first_air_step = s
        break
print(f"first step where BOTH feet airborne: {first_air_step}")