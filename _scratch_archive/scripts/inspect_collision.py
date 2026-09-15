"""Dump geom collision types (contype/conaffinity) to explain ground contact."""
import torch
from mjlab.tasks.registry import load_env_cfg
from mjlab.envs import ManagerBasedRlEnv
from mjlab.utils.torch import configure_torch_backends

configure_torch_backends()
env_cfg = load_env_cfg("Mjlab-Velocity-Flat-MicroDuck", play=True)
env_cfg.scene.num_envs = 1
env = ManagerBasedRlEnv(cfg=env_cfg, device="cuda:0")
mj = env.unwrapped.sim.mj_model

print(f"\n=== ngeom={mj.ngeom} ===")
# Build body-name prefix map
print("\n--- geoms with contype!=0 or conaffinity!=0 ---")
for i in range(mj.ngeom):
    ct = mj.geom_contype[i]
    ca = mj.geom_conaffinity[i]
    if ct == 0 and ca == 0:
        continue
    bid = mj.geom_bodyid[i]
    bname = mj.body(bid).name
    gname = mj.geom(i).name
    print(f"geom[{i:3d}] body={bname:24s} name={gname:22s} contype={ct} conaffinity={ca} group={mj.geom_group[i]}")

print("\n--- ALL geoms incl. zero-cont (class=visual) ---")
for i in range(mj.ngeom):
    ct = mj.geom_contype[i]
    ca = mj.geom_conaffinity[i]
    bid = mj.geom_bodyid[i]
    bname = mj.body(bid).name
    gname = mj.geom(i).name
    if gname in ("", "0"):
        gname = f"(id{i})"
    print(f"geom[{i:3d}] body={bname:24s} name={gname:22s} contype={ct} conaffinity={ca}")