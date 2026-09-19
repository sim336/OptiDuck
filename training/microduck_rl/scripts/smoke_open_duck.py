"""P1 smoke test: Open Duck (SolidWorks) on the velocity task.

Registers a temporary task that swaps the robot entity to
OPEN_DUCK_WALK_ROBOT_CFG (14 servos with STS3215 BAM, no EDF), then delegates
to the standard mjlab train CLI so the run uses the exact same runner/logging
path as the crazy_chick smokes.

Usage:
    uv run python scripts/smoke_open_duck.py Mjlab-Velocity-Flat-OpenDuck-Smoke \
        --agent.max_iterations 5 --env.scene.num-envs 64
"""

from __future__ import annotations

import sys

from mjlab.tasks.registry import register_mjlab_task

from mjlab_microduck.tasks import MicroduckOnPolicyRunner
from mjlab_microduck.tasks.microduck_velocity_env_cfg import MicroduckRlCfg
from mjlab_microduck.tasks.microduck_open_duck_env_cfg import (
    make_open_duck_velocity_env_cfg,
)

env_cfg = make_open_duck_velocity_env_cfg()
# Same 14D action / 61D obs contract as microduck's velocity task, robot swapped
# to the SolidWorks Open Duck (STS3215 BAM × 14, no EDF, HOME zero-pose standing).
register_mjlab_task(
    task_id="Mjlab-Velocity-Flat-OpenDuck-Smoke",
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
