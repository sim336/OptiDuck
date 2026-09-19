"""P1 smoke test: crazy_chick (STS3215 BAM M6) on the velocity task.

Registers a temporary task that swaps the robot entity to
CRAZY_CHICK_WALK_ROBOT_CFG (14 servos with STS3215 BAM + EDF motor at duty 0),
then delegates to the standard mjlab train CLI so the run uses the exact same
runner/logging path as the P0 smokes.

Usage:
    uv run python scripts/smoke_crazy_chick.py --agent.max_iterations 5 \
        --env.scene.num-envs 64
"""

from __future__ import annotations

import sys

from mjlab.tasks.registry import register_mjlab_task

from mjlab_microduck.robot.crazy_chick.crazy_chick_constants import (
    CRAZY_CHICK_WALK_ROBOT_CFG,
)
from mjlab_microduck.tasks import MicroduckOnPolicyRunner
from mjlab_microduck.tasks.microduck_velocity_env_cfg import (
    MicroduckRlCfg,
    make_microduck_velocity_env_cfg,
)

env_cfg = make_microduck_velocity_env_cfg()
# Robot swap: microduck walk model -> crazy_chick walk model (EDF + springs,
# STS3215 BAM on the 14 servo joints). EDF motor is site-bound, no joint, so
# the 14D joint_pos action space is unchanged and the EDF stays at duty 0.
env_cfg.scene.entities = {"robot": CRAZY_CHICK_WALK_ROBOT_CFG}

register_mjlab_task(
    task_id="Mjlab-Velocity-Flat-CrazyChick",
    env_cfg=env_cfg,
    play_env_cfg=env_cfg,
    rl_cfg=MicroduckRlCfg,
    runner_cls=MicroduckOnPolicyRunner,
)


def main() -> int | None:
    from mjlab.scripts.train import main as train_main

    return train_main()


if __name__ == "__main__":
    sys.exit(main())
