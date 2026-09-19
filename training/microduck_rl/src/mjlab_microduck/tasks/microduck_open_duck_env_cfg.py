"""Open Duck 平地行走任务（复用 velocity 配方：14D 纯关节动作 + 61D 观测）。

机器人换成 SolidWorks 导出的 Open Duck 模型（open_duck_walk.xml，由
make_open_duck.py 生成，INCLUDE_EDF=False）：
  - 14 个 STS3215 BAM 舵机（与 crazy_chick 相同的执行器参数），无 EDF
  - HOME 零位姿：腿伸直站立、头部水平（静态稳定性已验证：CoM 在脚支撑多边形内）
  - init_state 携带 XML 烘焙的直立 root 位姿（mjlab 默认 pos/rot 会覆写为躺平）

动作/观测契约：动作 14D 不变；观测在 velocity 策略族 61D 基础上追加 2D 步态
相位时钟（v8 方案3）→ **63D**（48 本体感知 + 13 命令块 + 2 时钟，时钟追加在
观测末尾）。OpenDuck 家族自此按 63D 契约训练/导出/部署；microduck 主族 61D
契约不受影响（两家族本就不能热交换——不同机器人）。sim2real：部署侧 runtime
需自 episode reset 起以 1.8 Hz 喂 (sin, cos) 时钟，ONNX 导出自动携带输入位。

坐标系适配（2026-09-02，10k 迭代训练失败根因）：mjlab/microduck 的
upright/bad_orientation/reset_yaw 都隐含 "HOME 时 body z == world up"（z-up）。
Open Duck 的 trunk_base 是 SolidWorks 任意坐标系：HOME 站立时局部 z 与竖直方向
夹角约 74°，导致标准 fell_over 一出生就触发、upright 恒为 0。本文件为 Open Duck
覆写这两处，用 "HOME 位姿下指向世界 +z 的 body 局部向量" 作为参考轴（对 microduck
退化为标准 z-up 公式），并禁用 reset_base 的 body 系 yaw 随机化（mjlab 用
quat_mul(default, euler_delta) 在 HOME 体坐标系里转 yaw，Open Duck 下等价于绕
倾斜轴转 → 部分 env 一出生就歪倒）。
"""
from __future__ import annotations

import math

import numpy as np
import torch

import mujoco as _mujoco

from mjlab.managers.scene_entity_config import SceneEntityCfg
from mjlab.managers import CurriculumTermCfg, ObservationTermCfg, RewardTermCfg
from mjlab.envs import ManagerBasedRlEnv
from mjlab.utils.lab_api.math import quat_apply_inverse, quat_mul, quat_from_euler_xyz

from mjlab_microduck.robot.crazy_chick.open_duck_walk_constants import (
    OPEN_DUCK_HOME_FRAME,
    OPEN_DUCK_WALK_ROBOT_CFG,
    OPEN_DUCK_WALK_XML,
)
from mjlab_microduck.tasks import mdp as microduck_mdp
from mjlab_microduck.tasks.microduck_velocity_env_cfg import (
    make_microduck_velocity_env_cfg,
)

_OPEN_DUCK_SCENE = SceneEntityCfg("robot")

# HOME 竖直轴：机器人处于 HOME 直立位姿时，"世界 +z" 在 root body 坐标系里的
# 单位向量（即 -projected_gravity_b 的期望值）。由 HOME quat 一次算好，运行时
# 只做 device 搬运。cos_tilt = (-projected_gravity_b) · up_home 就是机器人当前
# 姿态相对 HOME 直立的倾角余弦（yaw 无关，与 microduck 的 z-up 公式在
# up_home=(0,0,1) 时完全一致）。
_OPEN_DUCK_UP_HOME: tuple[float, float, float] = tuple(
    quat_apply_inverse(
        torch.tensor(list(OPEN_DUCK_HOME_FRAME.rot), dtype=torch.float32).unsqueeze(0),
        torch.tensor([[0.0, 0.0, 1.0]]),
    )
    .squeeze(0)
    .tolist()
)


# ---- v11 指令坐标修正（2026-09-04，历史）：把"前进"映射到鼻子方向 ----
# 诊断：躯干 SolidWorks 任意系下 body+x（指令前进轴）与机器人"头/嘴"方向差 ~170°，
# 导致策略沿 body+x 直线走、视觉上却"斜着走/背对脸"。旋转整个模型/重训均无法改变
# 该相对关系（已实证：任意世界旋转下两轴相对夹角不变，因鼻子随 body 一起转）。
# 修法：在【指令层】把 lin_x>0 重新映射为"前进"。v11-v12 曾用用户指定的 hip_roll
# 关节轴线（body 系 [0.4544,0.8908] ≈ body+y 侧 63°）。
# ---- v13 覆盖（2026-09-05，用户实测后改回）：前进 = 模型 body +x ----
# 用户看 v12 训练/渲染视频判定：前进指令 ≈ 机器人 y 轴，观感错误 → 改为 x。
# 即 _OPEN_DUCK_FORWARD_BODY_DIR = (1,0) → ROT = 单位阵 → 指令不再旋转，+vx 直译为
# body+x 方向（世界 HOME ≈ +71.8°；注意头/嘴在 body -x 侧 -158.6°，接受"背着走"外观，
# 以与真机/用户坐标系对齐为准）。历史 hip_roll 求法保留在函数里备用，不再使用。
def _open_duck_forward_body_dir() -> tuple[float, float]:
    """历史 v12 求法（hip_roll 轴），v13 起未使用；保留作参考。"""
    _m = _mujoco.MjModel.from_xml_path(str(OPEN_DUCK_WALK_XML))
    _d = _mujoco.MjData(_m)
    _mujoco.mj_forward(_m, _d)
    tr = next(i for i in range(_m.nbody) if "trunk_base" in _m.body(i).name)
    Rt = np.asarray(_d.xmat[tr]).reshape(3, 3)
    ax_b = np.asarray(_m.jnt_axis[_m.joint("left_hip_roll").id])  # 轴在 body 系
    ax_w = Rt @ ax_b
    fwd_w = ax_w.copy()
    fwd_w[2] = 0.0
    fwd_w /= np.linalg.norm(fwd_w) + 1e-9
    fwd_b = Rt.T @ fwd_w
    fb = fwd_b[:2] / (np.linalg.norm(fwd_b[:2]) + 1e-9)
    return (float(fb[0]), float(fb[1]))


# v13（2026-09-05）：前进 = 模型 body +x → 单位旋转（无指令旋转）。
_OPEN_DUCK_FORWARD_BODY_DIR = (1.0, 0.0)
# R：2D 旋转，把"前进系"向量 → body 系（恒等 → +vx 指令 = body+x）
_OPEN_DUCK_CMD_ROT = np.array(
    [
        [_OPEN_DUCK_FORWARD_BODY_DIR[0], -_OPEN_DUCK_FORWARD_BODY_DIR[1]],
        [_OPEN_DUCK_FORWARD_BODY_DIR[1], _OPEN_DUCK_FORWARD_BODY_DIR[0]],
    ]
)


class OpenDuckNoseForwardCommand(microduck_mdp.VelocityCommandCommandOnly):
    """twist 命令项：每步把 vx,vy 旋转到"鼻子方向"系（前进=朝头）。"""

    def __init__(self, cfg, env: ManagerBasedRlEnv):
        super().__init__(cfg, env)
        self._rot = torch.tensor(_OPEN_DUCK_CMD_ROT, dtype=torch.float32, device=self.device)

    def _update_command(self) -> None:
        super()._update_command()
        self.vel_command_b[:, :2] = self.vel_command_b[:, :2] @ self._rot.T


class OpenDuckNoseForwardCommandCfg(microduck_mdp.VelocityCommandCommandOnlyCfg):
    def build(self, env: ManagerBasedRlEnv) -> "OpenDuckNoseForwardCommand":
        return OpenDuckNoseForwardCommand(self, env)


def open_duck_rotated_twist(env, command_name: str) -> torch.Tensor:
    """把 twist 指令的 vx,vy 旋转到"鼻子方向"系（wz 不变，2D 旋转不碰 yaw）。"""
    command = env.command_manager.get_command(command_name)
    assert command is not None
    rot = torch.tensor(_OPEN_DUCK_CMD_ROT, dtype=command.dtype, device=command.device)
    out = command.clone()
    out[:, :2] = out[:, :2] @ rot.T
    return out


def open_duck_twist_command_obs(env, command_name: str) -> torch.Tensor:
    """twist 观测项：返回旋转后的指令（策略看到"前进=鼻子"）。"""
    return open_duck_rotated_twist(env, command_name)


def open_duck_track_linear_velocity(
    env,
    std: float,
    command_name: str,
    asset_cfg: SceneEntityCfg = _OPEN_DUCK_SCENE,
) -> torch.Tensor:
    """track_linear_velocity 的鼻子系版：与 obs 用同一旋转，避免时序不一致。"""
    asset = env.scene[asset_cfg.name]
    command = env.command_manager.get_command(command_name)
    assert command is not None
    actual = asset.data.root_link_lin_vel_b
    rot = torch.tensor(_OPEN_DUCK_CMD_ROT, dtype=command.dtype, device=command.device)
    cmd_xy = command[:, :2] @ rot.T
    xy_error = torch.sum(torch.square(cmd_xy - actual[:, :2]), dim=1)
    z_error = torch.square(actual[:, 2])
    return torch.exp(-(xy_error + z_error) / std**2)


def open_duck_bad_orientation(
    env,
    limit_angle: float,
    asset_cfg: SceneEntityCfg = _OPEN_DUCK_SCENE,
) -> torch.Tensor:
    """倾角超限即终止（HOME 竖直轴版 bad_orientation）。

    取代 mjlab.envs.mdp.terminations.bad_orientation（其用 projected_gravity_b
    的 z 分量，等价于假设 HOME 时 body z == world up，对 Open Duck 直立即判倒）。
    """
    asset = env.scene[asset_cfg.name]
    up_home = torch.tensor(_OPEN_DUCK_UP_HOME, device=env.device)
    cos_tilt = (-asset.data.projected_gravity_b * up_home).sum(dim=-1)
    return torch.acos(cos_tilt.clamp(-1.0, 1.0)).abs() > limit_angle


def open_duck_upright(
    env,
    std: float,
    asset_cfg: SceneEntityCfg = _OPEN_DUCK_SCENE,
) -> torch.Tensor:
    """直立奖励（HOME 竖直轴版，z-up 泛化）。

    取代 mjlab velocity 模板的 upright：原来把 body z 与 world up 的偏差平方和
    计入 exp 惩罚，Open Duck 下恒为 ~0。这里用 HOME 竖直轴作为参考：
    sin²(tilt) = 1 - (up_cur·up_home)²，站立时 ≈0 → 奖励 ≈1。
    """
    asset = env.scene[asset_cfg.name]
    up_home = torch.tensor(_OPEN_DUCK_UP_HOME, device=env.device)
    up_cur = -asset.data.projected_gravity_b  # 世界 up 在 body 系（单位向量）
    sin2_tilt = (1.0 - ((up_cur * up_home).sum(dim=-1) ** 2)).clamp(min=0.0)
    return torch.exp(-sin2_tilt / std**2)


def open_duck_reset_world_yaw(
    env: ManagerBasedRlEnv,
    env_ids: torch.Tensor | None,
    pose_range: dict[str, tuple[float, float]],
    velocity_range: dict[str, tuple[float, float]] | None = None,
    asset_cfg: SceneEntityCfg = _OPEN_DUCK_SCENE,
) -> None:
    """Open Duck 版 reset_base：yaw 绕【世界 +z】随机旋转（左乘），其余同 mjlab。

    mjlab 的 reset_root_state_uniform 把 pose_range 的 yaw 作为 HOME 体坐标系增量
    （quat_mul(q_home, q_delta)），Open Duck 的 HOME 是 SolidWorks 任意倾斜系 →
    等价绕倾斜轴转 → 一出生就歪倒（v12 起被迫 yaw=(0,0)）。本函数用
    q_new = quat_mul(quat_yaw_world(θ), q_home) 绕世界竖轴转，保留 HOME 倾斜姿态，
    实现"出生朝向全随机"（v13b，2026-09-05）。位置/速度与 microduck 基线的
    reset_base 完全一致（z 抖动 + env_origins + 零初速）。
    """
    if env_ids is None:
        env_ids = torch.arange(env.num_envs, device=env.device, dtype=torch.int)
    asset = env.scene[asset_cfg.name]
    root = asset.data.default_root_state[env_ids].clone()

    z_lo, z_hi = pose_range.get("z", (0.0, 0.005))
    y_lo, y_hi = pose_range.get("yaw", (-math.pi, math.pi))
    z = torch.rand(len(env_ids), device=env.device) * (z_hi - z_lo) + z_lo
    theta = torch.rand(len(env_ids), device=env.device) * (y_hi - y_lo) + y_lo

    positions = root[:, 0:3].clone()
    positions[:, 2] += z
    positions = positions + env.scene.env_origins[env_ids]

    q_yaw = quat_from_euler_xyz(
        torch.zeros_like(theta), torch.zeros_like(theta), theta
    )  # (w,x,y,z) 纯世界 z 旋转
    orientations = quat_mul(q_yaw, root[:, 3:7])

    velocities = root[:, 7:13].clone()
    if velocity_range:
        rl = [
            velocity_range.get(k, (0.0, 0.0))
            for k in ["x", "y", "z", "roll", "pitch", "yaw"]
        ]
        rs = torch.tensor(rl, device=env.device)
        vs = torch.rand(len(env_ids), 6, device=env.device) * (rs[:, 1] - rs[:, 0]) + rs[:, 0]
        velocities = velocities + vs

    asset.write_root_link_pose_to_sim(
        torch.cat([positions, orientations], dim=-1), env_ids=env_ids
    )
    asset.write_root_link_velocity_to_sim(velocities, env_ids=env_ids)


def open_duck_stance_slip(
    env,
    sensor_name: str,
    command_name: str,
    command_threshold: float = 0.01,
    asset_cfg: SceneEntityCfg = _OPEN_DUCK_SCENE,
) -> torch.Tensor:
    """支撑脚滑移惩罚（线性成本 + 直线行走门控）。

    取代 mjlab feet_slip 的平方成本 Σv²·in_contact：平方成本在 0.17 m/s
    滑移处梯度/成本都极小（-0.5 权重下每步仅 ~0.01 罚分，比 action_rate_l2
    的 -0.77/步 小 70 倍），roller-skate 滑步是奖励的理性最优解——v1 方案
    （foot_slip ×5）跑了 500 迭代所有步态指标纹丝不动（2026-09-03 实验）。
    本项改为线性成本 Σ|v_xy|·in_contact：低速滑移处梯度恒定，对 0.1~0.2 m/s
    的"慢滑"敏感得多；并按 linear/(linear+angular) 缩放——直线行走全额
    惩罚，原地转向几乎豁免（保留 pivot 能力，规避 microduck 当年 -1.0
    "太限制"的问题）。日志键 Metrics/slip_velocity_mean 与 feet_slip 一致。
    """
    asset = env.scene[asset_cfg.name]
    contact_sensor = env.scene[sensor_name]
    command = env.command_manager.get_command(command_name)
    assert command is not None
    linear_norm = torch.norm(command[:, :2], dim=1)
    angular_norm = torch.abs(command[:, 2])
    total = linear_norm + angular_norm
    active = (total > command_threshold).float()
    straight_scale = linear_norm / total.clamp(min=1e-6)  # 1.0=纯直线, 0.0=纯转向
    in_contact = (contact_sensor.data.found > 0).float()  # [B, N]
    foot_vel_xy = asset.data.site_lin_vel_w[:, asset_cfg.site_ids, :2]  # [B, N, 2]
    vel_xy_norm = torch.norm(foot_vel_xy, dim=-1)  # [B, N]
    cost = torch.sum(vel_xy_norm * in_contact, dim=1) * active * straight_scale
    num_in_contact = torch.sum(in_contact)
    mean_slip_vel = torch.sum(vel_xy_norm * in_contact) / torch.clamp(
        num_in_contact, min=1
    )
    env.extras["log"]["Metrics/slip_velocity_mean"] = mean_slip_vel
    return cost


# ---- v8 步态相位时钟参数（2026-09-03，方案3；v8c/v10 调频史见下）----
# f=1.8 Hz（v10 回退值，2026-09-03）：v8c 的 0.8Hz 实验判定失败——降频后
# match_mean 从 0.95 只恢复到 1.04（1300 迭代），action std 0.21 探索不足，
# 策略无法重学慢拍节奏；而 1.8Hz 是策略实证学过的节奏（match 1.50、两脚
# 50/50 交替、步数 41:40）。步幅天花板（舵机速度×步频）另行走渐进降频课程
# （1.8→1.5→1.2，每次从已合规状态出发），不在冷恢复期硬切。
# duty margin c=0.2：每脚期望支撑占周期 56.4%、摆动 43.6%（≈0.24s，落在
# base air_time 奖励窗 [0.125, 0.300] 内）；|sin|<c 双支撑窗 ~12.8%，无跑跳要求。
# 0.10 m 步长 ↔ ~0.36 m/s（步幅受舵机速度墙限制的实际值见实验记录 v8b/v8c）。
_OPEN_DUCK_GAIT_FREQ_HZ = 1.8
_OPEN_DUCK_GAIT_DUTY_C = 0.2


def open_duck_gait_phase(env) -> torch.Tensor:
    """步态时钟相位角 ang = 2π·f·t，t = 本回合步数 × step_dt（复位归零）。

    用 episode_length_buf 派生：确定性、全 env 同步、obs 与 reward 在同一步内
    取同一相位（二者都调 open_duck_gait_phase，天然一致）。
    """
    t = env.episode_length_buf.to(torch.float32) * env.step_dt
    return 2.0 * math.pi * _OPEN_DUCK_GAIT_FREQ_HZ * t


def open_duck_gait_expected_contact(
    sin_phase: torch.Tensor, duty_c: float
) -> torch.Tensor:
    """期望接触 mask [B,2]（列 0/1 = 接触传感器前两脚，反相落脚调度）。

    列0 期望支撑：sin > -c；列1 期望支撑：sin < c。
      - sin > +c：仅列0支撑；sin < -c：仅列1支撑（单支撑反相段）
      - |sin| < c：双脚都期望支撑（双支撑窗，~12.8% 周期）
    列的物理左右语义无关紧要：调度只要求两列反相（对 2026-09-03 的
    left/right 重命名鲁棒——命名只换标签，不换物理）。
    """
    col0 = sin_phase > -duty_c
    col1 = sin_phase < duty_c
    return torch.stack((col0, col1), dim=-1)


def open_duck_gait_clock(env) -> torch.Tensor:
    """步态相位时钟观测 (sin, cos) [B,2]——追加到 actor/critic 观测末尾。

    无噪声、无延迟（确定性输入）；obs_normalization 会把它归一到自身幅度。
    策略据此能推断当前相位与变化率，从而"预判"下一步该抬哪只脚。
    """
    ang = open_duck_gait_phase(env)
    return torch.stack((torch.sin(ang), torch.cos(ang)), dim=-1)


def open_duck_gait_contact(
    env,
    sensor_name: str,
    command_name: str,
    command_threshold: float = 0.01,
    duty_c: float = _OPEN_DUCK_GAIT_DUTY_C,
) -> torch.Tensor:
    """落脚调度匹配奖励（v8 方案3 核心）：实际接触 == 相位调度期望。

    每脚匹配 +1（共 0..2），weight 由 gait_contact_weight 课程
    0 → 0.5(iter 500) → 1.0(iter 1000) ramp（对齐"技能发现后再上强度"）。
    零指令（standing_envs curriculum 的 env）门控为 0：站立不受时钟骚扰。
    静止双脚撑地时期望匹配 ≈1.13/步，按调度交替迈步 ≈2/步 → 迈步严格占优，
    不存在"站着刷分"的 JACKPOT（每个正项对每个稳定状态自审，见 AGENTS.md）。
    """
    contact_sensor = env.scene[sensor_name]
    found = contact_sensor.data.found > 0  # [B, 2] 列序 = 传感器匹配序
    ang = open_duck_gait_phase(env)
    expected = open_duck_gait_expected_contact(torch.sin(ang), duty_c)
    match = (found == expected).float().sum(dim=1)  # [B]
    command = env.command_manager.get_command(command_name)
    assert command is not None
    total = torch.norm(command[:, :2], dim=1) + torch.abs(command[:, 2])
    active = (total > command_threshold).float()
    env.extras["log"]["Metrics/gait_contact_match_mean"] = (
        (match * active).sum() / torch.clamp(active.sum(), min=1.0)
    )
    return match * active


def open_duck_limit_proximity(
    env: ManagerBasedRlEnv,
    asset_cfg: SceneEntityCfg = _OPEN_DUCK_SCENE,
    margin_abs: float = 0.15,
) -> torch.Tensor:
    """限位邻近惩罚（qpos 侧，二次方）——消灭"贴限停车"拐杖。

    标准 dof_pos_limits 只在限位最后 7.5% 触发，v4 策略在 hip_yaw/roll 上
    99% 时间贴限外摆（宽站姿白嫖横向稳定）只交 ~0.17/步 保护费。本项从离
    硬限位 margin_abs 以内开始罚（二次方增长），把"贴限停车"变成持续出血，
    逼策略用主动平衡而非机械限位维持站姿。只作用于腿关节（与 pose 奖励
    相同的正则，排除 neck/head/passive）。
    """
    asset = env.scene[asset_cfg.name]
    qpos = asset.data.joint_pos[:, asset_cfg.joint_ids]  # [B, N]
    limits = asset.data.joint_pos_limits[:, asset_cfg.joint_ids, :]  # [B, N, 2]
    lo = limits[..., 0] + margin_abs
    hi = limits[..., 1] - margin_abs
    over_lo = (lo - qpos).clamp(min=0.0)
    over_hi = (qpos - hi).clamp(min=0.0)
    return torch.sum(over_lo.square() + over_hi.square(), dim=1)


def make_open_duck_velocity_env_cfg(
    play: bool = False,
    rough: bool = False,
):
    """Open Duck 平地行走：velocity 配方 + Open Duck 机器人（14D/61D 契约，无 EDF）。"""
    cfg = make_microduck_velocity_env_cfg(play=play, rough=rough)

    # Robot: SolidWorks Open Duck（STS3215 BAM × 14，无 EDF，HOME 零位姿站立）。
    cfg.scene.entities = {"robot": OPEN_DUCK_WALK_ROBOT_CFG}

    # reset_base 语义是 default_root_state + pose_range 叠加。microduck 用
    # z=(0.12,0.13) 把 freejoint 抬到脚触地高度；但 Open Duck 的 XML trunk_base
    # pos 已含转换器 shift（qpos0 即脚触地），必须把 z 归零，否则机器人悬空
    # ~12 cm 自由落体 → 第 1 步必倒（10k 迭代训练失败根因，2026-09-02）。
    cfg.events["reset_base"].params["pose_range"]["z"] = (0.0, 0.005)
    # v13b（2026-09-05）：mjlab 的 reset yaw 是 HOME 体坐标系里的旋转
    # （quat_mul(default, delta)），microduck HOME z≈world up 时等价于绕世界 z；
    # Open Duck trunk 倾斜，body 系 yaw 会把机器人甩成任意 3D 姿态（实测部分 env
    # 一出生 tilt>90°）。v12-v13a 被迫 yaw=(0,0) 固定出生朝向；v13b 起用自定义
    # open_duck_reset_world_yaw：绕【世界 +z】左乘旋转，出生朝向全随机（与 body
    # 相对指令正交训练：防朝向过拟合 + 为将来 heading/世界系指令铺路）。
    cfg.events["reset_base"].func = open_duck_reset_world_yaw
    cfg.events["reset_base"].params["pose_range"]["yaw"] = (-math.pi, math.pi)

    # z-up 适配：fell_over 终止与 upright 奖励改用 HOME 竖直轴参考。
    cfg.terminations["fell_over"].func = open_duck_bad_orientation
    cfg.rewards["upright"].func = open_duck_upright

    # ---- 步态强化 v2（2026-09-03，方案 B）：支撑脚防滑，直击滑步根因 ----
    # v1（foot_slip ×5 + foot_swing_height 强化）跑 500 迭代（2500→3085）指标
    # 纹丝不动，根因是奖励质量失衡：feet_slip 平方成本在 0.17 m/s 滑移处每步
    # 仅 ~0.01 罚分，比 action_rate_l2（-0.77/步）小 70 倍 → 滑步是理性最优解。
    # 且抬脚本来就没问题（peak_height 0.046 > 目标 0.03，悬空 ~0.22 s），
    # 病根只有"支撑脚不钉地、踩着滑"（slip_velocity 0.168 ≈ 指令速度 42%）。
    # v2：换自定义线性成本防滑项 open_duck_stance_slip（见上），权重 -2.0：
    # 消滑收益从 ~0.011/步 提到 ~0.5/步（与 action_rate 同级），真正改变最优解；
    # 直线行走全额惩罚、原地转向按 straight_scale 豁免。
    # 保留 v1 的 foot_swing_height/foot_clearance 设置不变（对指标无影响，
    # 本次唯一增量是 v2，便于 A/B 归因）。
    cfg.rewards["foot_slip"].func = open_duck_stance_slip
    # v3（2026-09-03）：-2.0 实测不足（440 迭代 slip 0.165→0.173 不降反升，
    # 策略硬吃 -0.07/步 罚分）→ 提到 -8.0：直线行走时罚分 ~-0.3~-0.8/步，
    # 与 action_rate_l2（-0.77/步）同级，抹掉滑步局部盆地；门控保留 pivot 豁免。
    cfg.rewards["foot_slip"].weight = -8.0
    cfg.rewards["foot_slip"].params["command_threshold"] = 0.01
    # v11 清理（2026-09-04）：删除 foot_swing_height/foot_clearance 的 OpenDuck 覆写
    # （v1 加倍/改目标）——深析 peak_height 0.047 恒高于目标 0.03，两项永远满足、
    # 零贡献（纯死重），还原 microduck 基线。

    # ---- v5 贴限惩罚（保留）：limit_proximity 直击"宽站姿贴限拐杖" ----
    # v11 清理（2026-09-04）：step_length 奖励已删除——v8b 加倍(1.5→3.0)判定无效、
    # 深析步幅 0.008-0.04 仍小（舵机速度墙，microduck 亦无此项），纯死重；
    # limit_proximity 保留（释放了 hip_pitch/knee 贴限 99%→0-3%，有实效）。
    cfg.rewards["limit_proximity"] = RewardTermCfg(
        func=open_duck_limit_proximity,
        weight=-2.0,
        params={
            "asset_cfg": SceneEntityCfg(
                "robot",
                joint_names=(r"^(?!passive_|.*neck.*|.*head.*).*",),
            ),
            "margin_abs": 0.15,
        },
    )

    # ---- 步态强化 v8（2026-09-03）：步态相位时钟（方案3）----
    # 病根复盘：v1–v7 所有步态项约束的都是【统计量】（步长均值、累计悬空时间、
    # 滑移、贴限），没有任何一项约束【迈步的时序/相位】→ "宽站姿 + 谁需要动
    # 谁动"的准静态碎挪（用户观察到的"左右腿各动各的"）在旧奖励下完全合法且
    # 最省力。v8 用外部时钟把"何时抬哪只脚"写进观测与奖励，见上方函数注释。
    #
    # 与相位时钟冲突的旧项处理：
    #   - v6 gait_symmetry【停用】：时钟本身强制左右反相、等时长悬空，时间维度
    #     度量已被"右脚高频点地凑悬空时间"骗过（v6 判定），保留只会在瞬态产生
    #     对抗噪声、污染 credit 分配。
    #   - v7 step_symmetry【停用】：转向时内外脚步长本应不同（差速转弯），该项
    #     会惩罚合法转向步态（与 turn-in-place 15% 桶对抗）；"两脚等长充分跨步"
    #     由时钟调度 + step_length 直接保证，无需统计量近似。
    #   - 保留（v11 清理后）：stance_slip -8.0（支撑脚要钉地，slip 0.079 实证有效）、
    #     limit_proximity（禁宽站拐杖，释放髋/膝贴限）；已删除 foot_swing_height/
    #     foot_clearance 覆写（永远满足的死重）与 step_length（无效）——与时钟正交：
    #     时钟管"何时"，stance_slip/limit_proximity 管"落得住/不贴死"。
    cfg.rewards["gait_contact"] = RewardTermCfg(
        func=open_duck_gait_contact,
        weight=0.0,  # 由下方 gait_contact_weight 课程 ramp
        params={
            "sensor_name": "feet_ground_contact",
            "command_name": "twist",
            "command_threshold": 0.01,
            "duty_c": _OPEN_DUCK_GAIT_DUTY_C,
        },
    )
    cfg.curriculum["gait_contact_weight"] = CurriculumTermCfg(
        func=microduck_mdp.reward_weight,
        params={
            "reward_name": "gait_contact",
            "weight_stages": [
                {"step": 0, "weight": 0.0},
                {"step": 500 * 24, "weight": 0.5},
                {"step": 1000 * 24, "weight": 1.0},
            ],
        },
    )

    # ---- v11 指令坐标修正：obs + track 奖励双入口把 twist 旋转到"鼻子方向"系 ----
    # 时序无关方案：命令缓冲保持原样，obs 与 track_linear 用同一个 R 旋转后使用 →
    # 策略看到"前进=鼻子"、被奖励朝鼻子走，全链路一致，无需重训。
    # （stand/turn-in-place 的 gating 用命令模长，旋转保模长 → 不受影响。）
    from copy import deepcopy as _deepcopy
    for _grp in ("actor", "critic"):
        if "commands" in cfg.observations[_grp].terms:
            _t = _deepcopy(cfg.observations[_grp].terms["commands"])
            _t.func = open_duck_twist_command_obs
            cfg.observations[_grp].terms["commands"] = _t
    cfg.rewards["track_linear_velocity"].func = open_duck_track_linear_velocity
    # 追踪强化（v11，2026-09-04）：track_linear 2.0→4.0——实证当前策略对指令追踪很弱
    # （track reward ~0.5/2.0，横向/反向指令下位移方向几乎不跟），导致"指令=鼻子方向"
    # 的修正无法在观感上体现。翻倍权重逼策略真正跟随指令（朝脸走），配合指令旋转生效。
    cfg.rewards["track_linear_velocity"].weight = 4.0
    # 追踪强化 v11b（2026-09-05）：track_angular 2.0→3.0——error_vel_yaw 持续偏高
    # （~0.7+），转向几乎不跟指令；与线性同步加强。
    cfg.rewards["track_angular_velocity"].weight = 3.0

    # ---- v11b 指令范围收窄（2026-09-05）：匹配物理可达包络，压 tracking 误差 ----
    # Metrics/twist/error_vel_xy 0.96、error_vel_yaw 0.7+ 的结构性根因：指令范围
    # （lin ±0.4 / ang ±1.0）远超 OpenDuck 能力（舵机速度墙 → 实际可达 ~0.2-0.3 m/s、
    # ~0.3-0.4 rad/s）。收窄到可达包络：不可达指令消失 → 误差结构性下降、梯度更干净。
    # microduck 当年亦因"ang ±2.0 超出能力"降到 ±1.0（同哲学）。
    _twist = cfg.commands["twist"]
    _twist.ranges.lin_vel_x = (-0.25, 0.25)
    _twist.ranges.lin_vel_y = (-0.15, 0.15)
    _twist.ranges.ang_vel_z = (-0.4, 0.4)
    # turn-in-place 桶自动随 ang 范围缩为 [0.4·0.4, 0.4] = [0.16, 0.4]

    # ---- v13c heading 指令（2026-09-05，用户指定"加上 heading 指令"）----
    # mjlab 原生 heading_command：is_heading_env 的 env 每 episode 抽一个世界系
    # 目标朝向 heading_target∈[-π,π]，每步 wz = clip(K·heading_error) 由控制器给；
    # heading_error = wrap(target - robot.data.heading_w)，heading_w = body+x 的
    # 世界方位角（Entity.forward_vec_b=(1,0,0)，与 v13 前进轴一致、与世界 yaw 随机
    # reset 配套）→ 训练"朝指定世界方向走"。linear vx/vy 仍照常抽样。obs 维度不变
    # （twist 仍 3D：策略看到自动 wz）；与 standing(后置清零)/turn-in-place(独立
    # mask 重叠 ~7.5% 让位 heading) 兼容。heading 50% 与 body 相对指令 50% 混合。
    # v16 消融（2026-09-05）：禁用 heading 指令，验证 err_yaw 偏高是否源于 v13c。
    # 保留 v13b 世界朝向随机（纯收益 DR）。heading 开关保留可回退（改回 True + 恢复 range）。
    _twist.heading_command = False
    _twist.ranges.heading = None  # 禁用 heading 时须置 None（mjlab 校验，否则报错）
    _twist.rel_heading_envs = 0.5  # 保留（禁用时不生效；改回 True 时复用）
    _twist.heading_control_stiffness = 1.0  # K·err 裁剪到 ±0.4 rad/s

    # ---- v8 观测：步态相位时钟 (sin, cos) 追加到 actor/critic 末尾 ----
    # OpenDuck 家族观测契约 61D → 63D（48 本体感知 + 13 命令 + 2 时钟）。
    # ⚠️ microduck 主族 61D 契约不动（AGENTS.md 热交换不变量只约束 microduck
    # 家族；OpenDuck 是不同机器人，本就无法与其热交换，但沿用同一 runtime 时
    # 部署侧需喂同频时钟）。追加在命令块之后，故 63D 布局 = [...13D 命令, sin, cos]。
    for group in ("actor", "critic"):
        cfg.observations[group].terms["gait_clock"] = ObservationTermCfg(
            func=open_duck_gait_clock,
            scale=1.0,
        )

    # ---- v14 步态解锁干预（2026-09-05，用户批准 5 项组合；均单变量、可回退）----
    # 根因（model_11250 深分析，非猜测）："稳而不走"局部最优——腿关节 1.8Hz 振幅仅
    # 0.01-0.11 rad（伺服可达 ~0.36 rad，用了不到 1/7），而每步 action_rate_l2 税
    # -1.08 是最大单项惩罚且随步幅急剧增大；track_linear std=√0.1≈0.32 使"站立零速"
    # 也拿 ~2.1/4.0 追踪分。=> 当前奖励面下"站稳+原地碎挪" 严格优于 "真走"。
    # 以下 5 项重排该奖励面，让迈步的净收益转正。

    # 1) action_rate 税：封顶 -1.0 → -0.3。base 配方 1500 iter 就封顶 -1.0，对
    #    步态技能仍处发现期的 Open Duck 过早/过狠（AGENTS.md：技能发现期别上税）。
    #    （注：2026-09-05 后 kp_fw 已从 9.5 修复为 32 —— 9.5 是 MuJoCo position
    #    kp 被误用于 BAM 固件 P 增益的单位错配，软到策略只能碎挪；见
    #    crazy_chick_constants.py。降税释放动作的初衷不变。）
    cfg.curriculum["action_rate_weight"] = CurriculumTermCfg(
        func=microduck_mdp.reward_weight,
        params={
            "reward_name": "action_rate_l2",
            "weight_stages": [
                {"step": 0,          "weight": -0.1},
                {"step": 500 * 24,   "weight": -0.15},
                {"step": 1000 * 24,  "weight": -0.2},
                {"step": 1500 * 24,  "weight": -0.25},
                {"step": 2000 * 24,  "weight": -0.3},
            ],
        },
    )

    # 2) track_linear std：√0.1 → √0.05（0.316 → 0.224）。与 4) 指令收窄配套：
    #    令"站立零速"的追踪分显著下降，把迈步的边际收益拉正。
    cfg.rewards["track_linear_velocity"].params["std"] = math.sqrt(0.05)

    # 3) standing_envs：0.25 → 0.10。base 2000 iter 涨到 25% 零指令站立，把"静止"
    #    强化过强。降到 10%，保留零指令 idle 训练但不占主导。
    cfg.curriculum["standing_envs"] = CurriculumTermCfg(
        func=microduck_mdp.standing_envs_curriculum,
        params={
            "command_name": "twist",
            "standing_stages": [
                {"step": 0,         "rel_standing_envs": 0.02},
                {"step": 500 * 24,  "rel_standing_envs": 0.05},
                {"step": 1000 * 24, "rel_standing_envs": 0.08},
                {"step": 1500 * 24, "rel_standing_envs": 0.10},
            ],
        },
    )

    # 4) 指令范围收窄：lin_x ±0.25 → ±0.12、lin_y ±0.15 → ±0.10。软舵机实际可达
    #    ~0.07-0.1 m/s，±0.25 超出可达包络 → err_xy 结构性虚高（v11b 同哲学）。
    _twist.ranges.lin_vel_x = (-0.12, 0.12)
    _twist.ranges.lin_vel_y = (-0.10, 0.10)

    # 5) head_pose_range 收回：pitch 终值 1.10 → 0.70 rad。head 占体重 ~38%，
    #    极幅头部追踪奖励会反噬行走（AGENTS.md 警告）；neck_pitch 实测 100% 贴下限。
    #    yaw/roll 按 base 比例（1.10:1.40:0.31）等比缩放到 pitch=0.70。
    cfg.curriculum["head_pose_range"] = CurriculumTermCfg(
        func=microduck_mdp.pose_command_range_curriculum,
        params={
            "command_name": "head_pose",
            "range_stages": [
                {"step": 0,         "ranges": ((-0.032, 0.032), (-0.032, 0.032), (-0.045, 0.045),  (-0.010, 0.010))},
                {"step": 500 * 24,  "ranges": ((-0.11, 0.11),   (-0.11, 0.11),   (-0.13, 0.13),    (-0.030, 0.030))},
                {"step": 1000 * 24, "ranges": ((-0.25, 0.25),   (-0.25, 0.25),   (-0.31, 0.31),    (-0.070, 0.070))},
                {"step": 1500 * 24, "ranges": ((-0.46, 0.46),   (-0.46, 0.46),   (-0.58, 0.58),    (-0.13, 0.13))},
                {"step": 2000 * 24, "ranges": ((-0.70, 0.70),   (-0.70, 0.70),   (-0.89, 0.89),    (-0.20, 0.20))},
            ],
        },
    )

    return cfg
