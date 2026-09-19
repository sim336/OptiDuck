"""Jitter analysis for the standup model: quantify standing micro-oscillation.

Loads the converged standup checkpoint, drives the Standing/sitting starts to a
clean stand, then records a steady-state window of per-joint qpos / qvel / ctrl
(target) / torque (qfrc_actuator) at the control rate. FFT per joint to find the
dominant frequency + amplitude, so we can tell whether the standing jitter is:
  - torque ripple at a specific freq / on specific joints (overactive policy),
  - a low-freq limit cycle (sensor-noise / PD reaction), or
  - distributed broadband flutter.

Pushes and DR are disabled so the window reflects pure steady-state hold.
"""
from dataclasses import asdict

import numpy as np
import torch

from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import MjlabOnPolicyRunner, RslRlVecEnvWrapper
from mjlab.tasks.registry import load_env_cfg, load_rl_cfg, load_runner_cls
from mjlab.utils.torch import configure_torch_backends

configure_torch_backends()
task_id = "Mjlab-StandUp-Flat-MicroDuck"
device = "cuda:0"

MODEL = r"logs\rsl_rl\microduck_stand\2026-09-16_00-42-47_standup_prebom_resume_v2\model_21999.pt"

env_cfg = load_env_cfg(task_id, play=True)
env_cfg.scene.num_envs = 1

# Step a) control timing introspect later from env.unwrapped.sim.dt + cfg.decimation

# Disable pushes + DR so the steady-state window is a clean hold.
if "push_robot" in env_cfg.events:
    env_cfg.events["push_robot"].interval_range_s = (1e6, 1e6)
if "randomize_com" in env_cfg.events:
    env_cfg.events["randomize_com"].params["ranges"] = (0.0, 0.0)
if "randomize_mass_inertia" in env_cfg.events:
    env_cfg.events["randomize_mass_inertia"].params["alpha_range"] = (0.0, 0.0)

agent_cfg = load_rl_cfg(task_id)

env = ManagerBasedRlEnv(cfg=env_cfg, device=device)
env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)

runner_cls = load_runner_cls(task_id) or MjlabOnPolicyRunner
runner = runner_cls(env, asdict(agent_cfg), device=device)
runner.load(MODEL, load_cfg={"actor": True}, strict=True, map_location=device)
policy = runner.get_inference_policy(device=device)

sim = env.unwrapped.sim
mj_model = sim.mj_model
robot = env.unwrapped.scene["robot"]
dt = 0.005  # reported "Physics step-size 0.005" (warp Simulation hides .dt)
decimation = int(getattr(env_cfg, "decimation", 4))
fs = 1.0 / (dt * decimation)
print(f"sim.dt={dt}  decimation={decimation}  control_fs={fs:.1f} Hz")

rj = robot.data
print("robot.data samples:", {k: tuple(getattr(rj, k).shape) if hasattr(getattr(rj, k), 'shape') else type(getattr(rj, k)).__name__
                               for k in ('joint_pos','joint_vel','joint_pos_target','qfrc_actuator')})
NJ = int(rj.joint_pos.shape[1])

# joint names: map model joint dofadr -> name within robot dof range
dof2name = {}
for i in range(100):
    try:
        nm = mj_model.joint(i).name
    except Exception:
        break
    a = int(np.asarray(mj_model.joint(i).dofadr).reshape(-1)[0])
    if nm.startswith("robot/") and a not in dof2name:
        dof2name[a] = nm
doff = min(dof2name) if dof2name else 6
# drop passive/free joints; take the first NJ articulated names in dof order
_sorted = sorted((k, v) for k, v in dof2name.items() if "passive" not in v and "free" not in v)
joint_names = [nm for _, nm in _sorted[:NJ]]
print("robot dof base:", doff, "  joints:", joint_names)

body_names = [mj_model.body(i).name for i in range(mj_model.nbody)]
trunk = body_names.index("robot/trunk_base")

def snapshot():
    qp = rj.joint_pos[0].detach().cpu().numpy().copy()
    qv = rj.joint_vel[0].detach().cpu().numpy().copy()
    ctrl = rj.joint_pos_target[0].detach().cpu().numpy().copy()
    tau = rj.qfrc_actuator[0].detach().cpu().numpy().copy()
    return qp, qv, ctrl, tau

# qpos freejoint order: 7 quaternion (tx,ty,tz, qw qx qy qz)
def trunk_z():
    return float(sim.data.xpos[0, trunk, 2].cpu())

def trunk_tilt_deg():
    # z-axis tilt from the body quaternion (qpos[3:7] w,x,y,z)
    q = sim.data.qpos[0, 3:7].cpu().numpy()
    qw, qx, qy, qz = q[0], q[1], q[2], q[3]
    # rotation matrix z-axis (world) from quat
    zx = 2 * (qx * qz + qw * qy)
    zy = 2 * (qy * qz - qw * qx)
    zz = 1 - 2 * (qx * qx + qy * qy)
    tilt = np.degrees(np.arccos(np.clip(zz, -1.0, 1.0)))
    return tilt

# ---- roll out episodes, capture a standing steady-state window per episode ----
N_EPISODES = 6
WINDOW_STEPS = int(2.5 * fs)   # 2.5 s steady-state window
RISE_Z = 0.108                 # considered "standing"
# Zero-command mode: null out the head/body command obs slots (actor obs layout
# 51:55 head_command, 55:61 body_command) so the frozen policy holds the nominal
# stand with no commanded motion. Isolates true balance jitter from the
# policy's active tracking of the alive-range random commands.
ZERO_CMD = True


def roll_one_episode(step0):
    """Run one episode; return (start_kind, [(t, qpos, qvel, ctrl, tau, z, tilt),...]) standing window."""
    obs, extras = env.reset()
    # determine start kind from trunk z + tilt
    z0 = trunk_z(); t0 = trunk_tilt_deg()
    if t0 > 60:
        kind = "face_down" if z0 < 0.09 else "face_up"
    elif z0 > 0.105:
        kind = "standing"
    else:
        kind = "sitting"

    history = []        # full episode for grain
    window = []         # selected steady-state window
    stood_at = None
    t_idx = 0
    stable = 0
    done = False
    while not done and t_idx < int(6.0 * fs):
        obs_in = obs.clone()
        if ZERO_CMD:
            obs_in[0, 51:61] = 0.0
        with torch.no_grad():
            action = policy(obs_in)
        obs, rew, dones, extras = env.step(action)
        done = bool(dones[0].item())
        z = trunk_z(); tilt = trunk_tilt_deg()
        qp, qv, ctrl, tau = snapshot()
        history.append((t_idx, qp, qv, ctrl, tau, z, tilt))
        t_idx += 1
        # standing detected: near STAND_Z and roughly upright, hold for a bit
        if z > RISE_Z and tilt < 15:
            if stood_at is None:
                stood_at = t_idx
            stable += 1
        else:
            stable = 0
        if stable >= WINDOW_STEPS:
            break

    if stood_at is None:
        return kind, None, None, None

    # window = last WINDOW_STEPS of history (fully standing)
    win = history[-WINDOW_STEPS:]
    return kind, win, stood_at, t_idx


print("\n==== JITTER ANALYSIS ====\n")
all_windows = []
for ep in range(N_EPISODES):
    kind, win, stood_at, total = roll_one_episode(ep)
    if win is None:
        print(f"ep{ep}: start={kind} -> never stood, skipped")
        continue
    t = np.array([r[0] for r in win])
    qp = np.stack([r[1] for r in win])         # (W,14)
    qv = np.stack([r[2] for r in win])         # (W,14)
    tau = np.stack([r[4] for r in win])        # (W,14)
    z = np.array([r[5] for r in win])
    tilt = np.array([r[6] for r in win])
    print(f"ep{ep}: start={kind:10s} stood@step~{stood_at}  window {len(win)} ctrl-steps "
          f"z={z.mean():.4f}±{z.std()*1000:.2f}mm  tilt={tilt.mean():.1f}°±{tilt.std():.1f}")
    all_windows.append((kind, qp, qv, tau, z, tilt))


if not all_windows:
    print("No standing window captured!")
    raise SystemExit

# ---- FFT each joint over pooled standing windows ----
def rms(x):
    return float(np.sqrt(np.mean(x ** 2)))

print("\n--- per-joint steady-state jitter (pooled standing windows) ---")
print(f"{'#':>2} {'joint':<26} {'|q| rms(mmrad)':>15} {'pk-pk(mmrad)':>13} "
      f"{'|tau|rms(Nm)':>12} {'domFreq(Hz)':>11} {'pkAmp(mmrad)':>12}")
pool_qp = np.concatenate([w[1] for w in all_windows], axis=0)
pool_qv = np.concatenate([w[2] for w in all_windows], axis=0)
pool_tau = np.concatenate([w[3] for w in all_windows], axis=0)
W, NJ = pool_qp.shape

for j in range(NJ):
    sig = pool_qp[:, j] - pool_qp[:, j].mean()
    pkpk = float(np.ptp(sig))
    scale = 1000.0  # rad -> mrad
    # FFT
    n = len(sig)
    winf = np.hanning(n)
    S = np.abs(np.fft.rfft((sig) * winf)) / np.sum(winf)
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    # exclude DC (0 Hz)
    mask = freqs > 0.3
    if not mask.any():
        dom_f, dom_a = float("nan"), 0.0
    else:
        k = np.argmax(S[mask])
        dom_f = float(freqs[mask][k])
        dom_a = float(S[mask][k] * scale)
    tr = rms(pool_tau[:, j])
    print(f"{j:2d} {joint_names[j]:<26} {rms(pool_qp[:,j]-pool_qp[:,j].mean())*scale:15.3f} "
          f"{pkpk*scale:13.3f} {tr:12.4f} {dom_f:11.2f} {dom_a:12.3f}")

# Whole-body torque ripple + freq of largest contributor
tot_tau_rms = rms(pool_tau)
print(f"\ntotal torque ripple rms: {tot_tau_rms:.4f} Nm")

# Which joints dominate the qpos oscillation energy?
var_per_joint = pool_qp.var(axis=0)
order = np.argsort(-var_per_joint)
print("\ntop joints by oscillation energy (pos variance):")
for j in order[:6]:
    print(f"  {joint_names[j]}  var={var_per_joint[j]:.2e} rad^2  "
          f"pulse(qvel rms)={rms(pool_qv[:,j]):.4f} rad/s")

# Save raw for later plotting
np.savez(r"E:\Temp\jitter_stand_21999.npz",
         dt=fs, joint_names=np.array(joint_names, dtype=object),
         qp=pool_qp, qv=pool_qv, tau=pool_tau)
print("\nsaved raw steady-state series -> E:\\Temp\\jitter_stand_21999.npz")