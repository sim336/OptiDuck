"""疯狂小鸡 jump 任务族（阶段 1：平地行走 + EDF 允许）。

在 microduck velocity 配方之上做**最小增量**（microduck 全能力保留）：
  - 机器人换为 crazy_chick walk 模型（STS3215 BAM 14 舵机 + EDF motor）
  - 动作 14→15：新增 `edf_duty ∈ [0,1]`（site 绑定 motor ctrl，`edf_exit`）
  - 观测 61→62：新增上帧 `edf_cmd`（供策略学习动作平滑，EDF 不抖）

动作/观测契约（jump 策略族内部一致，P4 运行时按策略元数据识别）：
  动作 15D = [14 joint_pos] + [1 edf_duty]
  观测 62D = [base_ang_vel(3), projected_gravity(3), joint_pos(14),
              joint_vel(14), actions_joint(14), edf_cmd_last(1),
              twist(3), head_pose(4), body_pose(6)]

阶段 1 目标：复用 velocity 走姿配方，策略学会正常行走（EDF 允许使用但不作
强制要求）；阶段 2/3（jump_launch / jump_full）在此文件基础上扩展奖励与课程。
"""

from __future__ import annotations

from mjlab.envs.mdp.actions import JointPositionActionCfg, SiteEffortActionCfg
from mjlab.managers.observation_manager import ObservationTermCfg
from mjlab.tasks.velocity import mdp

from mjlab_microduck.robot.crazy_chick.crazy_chick_constants import (
    CRAZY_CHICK_WALK_ROBOT_CFG,
)
from mjlab_microduck.tasks.microduck_velocity_env_cfg import (
    make_microduck_velocity_env_cfg,
)

# EDF 推力满档 ~1 N（40mm 风道，仿真已按悬空加速度标定 gear="0 0 1"）；
# duty ∈ [0,1] 由 tanh 动作经 scale/offset 映射（见下）。
EDF_FULL_THRUST_N = 1.0


def _set_edf_action_and_obs(cfg) -> None:
    """注入 15D 动作 / 62D 观测的 jump 契约。"""
    # --- 动作：14 舵机 + 1 EDF duty ---
    joint_pos_action = cfg.actions["joint_pos"]
    assert isinstance(joint_pos_action, JointPositionActionCfg)
    # 只控制 14 个伺服（edf_thrust 是 site 绑定 motor、无 joint，本就不会命中；
    # 显式排除防止未来模型加入 EDF 关节时动作维意外扩张）。
    joint_pos_action.actuator_names = (r"^(?!edf_).*",)

    # EDF duty：policy 输出 tanh ∈ [-1,1] → duty = 0.5·a + 0.5 ∈ [0,1]。
    # SiteEffortAction 直接写 site 绑定 motor 的 ctrl（= 推力乘子，gear 已含方向）。
    cfg.actions["edf_duty"] = SiteEffortActionCfg(
        entity_name="robot",
        actuator_names=("edf_exit",),
        scale=0.5,
        offset=0.5,
    )

    # --- 观测：last_action 拆分为 14 关节动作 + 1 上帧 edf_cmd ---
    # mjlab 的 last_action 默认拼接所有 action term（15D）；契约要求分成两段，
    # 且 edf_cmd_last 插在 actions 之后、command 之前（62D 布局见模块 docstring）。
    for grp in ("actor", "critic"):
        terms = cfg.observations[grp].terms
        new_terms = {}
        for name, term in terms.items():
            new_terms[name] = term
            if name == "actions":
                new_terms["actions"] = ObservationTermCfg(
                    func=mdp.last_action,
                    params={"action_name": "joint_pos"},
                )
                new_terms["edf_cmd_last"] = ObservationTermCfg(
                    func=mdp.last_action,
                    params={"action_name": "edf_duty"},
                )
        cfg.observations[grp].terms = new_terms


def make_microduck_jump_env_cfg(
    play: bool = False,
    rough: bool = False,
):
    """Jump 阶段 1（walking）环境：velocity 配方 + EDF 动作/观测契约。

    rough 参数保留以对齐 velocity 的接口，阶段 1 实际只用平地。
    """
    cfg = make_microduck_velocity_env_cfg(play=play, rough=rough)

    # Robot: crazy_chick walk（STS3215 BAM + EDF + 膝/踝弹簧）。
    cfg.scene.entities = {"robot": CRAZY_CHICK_WALK_ROBOT_CFG}

    _set_edf_action_and_obs(cfg)

    return cfg
