"""Static-hold test: send zero action (=> default/HOME joint targets) and watch trunk/foot height.
Decides physics-vs-training: if the robot collapses under zero action, the HOME pose / actuator
can't statically hold the body and no amount of RL training will fix it."""
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

robot = env.unwrapped.scene["robot"]
mj_model = env.unwrapped.sim.mj_model
body_names = [mj_model.body(i).name for i in range(mj_model.nbody)]
left_foot = body_names.index("robot/ankle_left")
right_foot = body_names.index("robot/ankle_right")
trunk = body_names.index("robot/trunk_base")
xpos = env.unwrapped.sim.data.xpos

num_actions = env.num_actions
print(f"num_actions={num_actions}, clip_actions={agent_cfg.clip_actions}")

obs, _ = env.reset()
lz0 = float(xpos[0, left_foot, 2])
rz0 = float(xpos[0, right_foot, 2])
tz0 = float(xpos[0, trunk, 2])
print(f"INITIAL: LfootZ={lz0:.4f} RfootZ={rz0:.4f} TrunkZ={tz0:.4f}")

rows = []
for step in range(200):
    action = torch.zeros(1, num_actions, device=device)
    obs, rew, dones, extras = env.step(action)
    lz = float(xpos[0, left_foot, 2])
    rz = float(xpos[0, right_foot, 2])
    tz = float(xpos[0, trunk, 2])
    rows.append((step, lz, rz, tz, float(dones.sum().item()) if hasattr(dones, "sum") else 0.0))

print("\nSTEP  LfootZ  RfootZ  TrunkZ")
for step, lz, rz, tz, d in rows[::10]:
    print(f"{step:4d}  {lz:6.4f}  {rz:6.4f}  {tz:6.4f}")

tz = np.array([r[3] for r in rows])
lz = np.array([r[1] for r in rows])
rz = np.array([r[2] for r in rows])
print(f"\ntrunk z: init={tz0:.4f}  min={tz.min():.4f}  max={tz.max():.4f}  final={tz[-1]:.4f}")
print(f"foot z  : init L={lz0:.4f} R={rz0:.4f}  min L={lz.min():.4f} R={rz.min():.4f}")
print(f"trunk drop from init: {tz0 - tz.min():.4f} m ({(tz0 - tz.min())*100:.1f} cm)")