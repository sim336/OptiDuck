"""疯狂小鸡（crazy_chick）仿真模型生成器。

从 microduck 的模型矩阵派生疯狂小鸡模型，改造点：
  1. 腹部 EDF 推力执行器（motor，沿 -Z 施力，推力线过质心）
  2. 膝/踝弹簧储能（joint stiffness + springref）
  3. 可选的全局缩放（scale 参数，配合 STL mesh scale）

变体矩阵与 microduck 一一对应（walk / allcollisions / rollers / backlash）：

    robot_walk.xml                            -> crazy_chick_walk.xml
    robot_allcollisions.xml                   -> crazy_chick_allcollisions.xml
    robot_allcollisions_rollers.xml           -> crazy_chick_allcollisions_rollers.xml
    robot_walk_backlash.xml                   -> crazy_chick_walk_backlash.xml
    robot_allcollisions_backlash.xml          -> crazy_chick_allcollisions_backlash.xml
    robot_allcollisions_rollers_backlash.xml  -> crazy_chick_allcollisions_rollers_backlash.xml

生成物放置于本目录，meshdir 复用 ../microduck/assets。

用法：
    python make_crazy_chick.py                 # 生成全部 6 个变体
    python make_crazy_chick.py --variant walk  # 只生成指定变体
    python make_crazy_chick.py --scale 1.6     # 缩放腿部（后续硬件迭代，暂未实现）
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_MICRODUCK_DIR = Path(__file__).parent.parent / "microduck"
_OUT_DIR = Path(__file__).parent

# 膝/踝弹簧参数（起跳储能）
#   stiffness: 关节弹性系数 [Nm/rad]（数值标定见 P2）
#   springref: 弹簧自由长度参考角 [rad]（取 HOME 位形，蓄力位下蹲时储能）
KNEE_SPRING = {"stiffness": 1.0, "springref": 0.0}
ANKLE_SPRING = {"stiffness": 1.0, "springref": 0.0}

# 变体矩阵：变体名 -> (源模型文件名, 派生模型文件名)
MODEL_VARIANTS: dict[str, tuple[str, str]] = {
    "walk": ("robot_walk.xml", "crazy_chick_walk.xml"),
    "allcollisions": ("robot_allcollisions.xml", "crazy_chick_allcollisions.xml"),
    "allcollisions_rollers": (
        "robot_allcollisions_rollers.xml",
        "crazy_chick_allcollisions_rollers.xml",
    ),
    "walk_backlash": ("robot_walk_backlash.xml", "crazy_chick_walk_backlash.xml"),
    "allcollisions_backlash": (
        "robot_allcollisions_backlash.xml",
        "crazy_chick_allcollisions_backlash.xml",
    ),
    "allcollisions_rollers_backlash": (
        "robot_allcollisions_rollers_backlash.xml",
        "crazy_chick_allcollisions_rollers_backlash.xml",
    ),
}


def build_xml(src: str) -> str:
    """把一份 microduck MJCF 派生成 crazy_chick MJCF（EDF + 弹簧注入）。"""

    # 1) 模型名与 meshdir
    src = re.sub(r'<mujoco model="microduck">', '<mujoco model="crazy_chick">', src, count=1)
    src = re.sub(r'meshdir="assets"', 'meshdir="../microduck/assets"', src, count=1)

    # 2) 膝/踝关节加弹簧（在关节行末尾追加 stiffness/springref）
    def _add_spring(match: re.Match) -> str:
        tag = match.group(0)
        # 避免重复注入
        if "springref" in tag:
            return tag
        # 选择对应弹簧参数（passive_*_backlash 不匹配，正则只命中 left/right knee/ankle）
        name = re.search(r'name="([^"]+)"', tag)
        params = KNEE_SPRING if name and name.group(1).endswith("_knee") else (
            ANKLE_SPRING if name and name.group(1).endswith("_ankle") else None
        )
        if params is None:
            return tag
        if not tag.rstrip().endswith("/>"):
            return tag
        return tag[:-2] + f' stiffness="{params["stiffness"]}" springref="{params["springref"]}" />'

    # 匹配关节定义行（含 name=、type=hinge、class=chosen_actuator）
    src = re.sub(
        r'<joint [^>]*name="(?:left|right)_(?:knee|ankle)"[^>]*/>',
        _add_spring,
        src,
    )

    # 3) EDF 执行器：在 <actuator> 内追加 motor（site 绑定，+Z 方向推力）
    #    gear 在 site 局部坐标系定义"施加在机身上的力"；EDF 向下喷气 → 机身受向上
    #    反作用力。site 无 quat 时继承 trunk_base 方向（局部 +z = 世界 +z 向上），
    #    故 gear="0 0 1" 才产生向上的推力（"0 0 -1" 会把机身压向地面，曾踩坑）。
    edf_actuator = (
        '  <motor name="edf_thrust" site="edf_exit" gear="0 0 1" '
        'ctrlrange="0 1" ctrllimited="true"/>\n'
    )
    # 幂等守卫：已存在 edf_thrust 则跳过，避免重复注入
    if 'name="edf_thrust"' not in src:
        src = re.sub(r"(</actuator>)", edf_actuator + r"\1", src, count=1)

    # 4) EDF 安装 site：挂在 trunk_base 下（出风口在质心正下方，力臂 r≈0）
    edf_site = (
        '      <!-- EDF 出风口 site（推力线过质心，力臂 r≈0） -->\n'
        '      <site group="3" name="edf_exit" pos="0 0 -0.045" size="0.02"/>\n'
    )
    # 插到 trunk_base 的 imu site 之后（幂等守卫：已存在则跳过）
    if 'name="edf_exit"' not in src:
        src = re.sub(r'(      <!-- Frame imu -->[^\n]*\n)', r"\1" + edf_site, src, count=1)

    return src


def generate(variant: str, scale: float) -> tuple[Path, Path]:
    """生成一个变体，返回 (源路径, 输出路径)。"""
    if variant not in MODEL_VARIANTS:
        raise ValueError(
            f"unknown variant '{variant}'; choose from {sorted(MODEL_VARIANTS)}"
        )
    src_name, out_name = MODEL_VARIANTS[variant]
    src_path = _MICRODUCK_DIR / src_name
    out_path = _OUT_DIR / out_name
    if not src_path.exists():
        raise FileNotFoundError(f"source model not found: {src_path}")

    xml = build_xml(src_path.read_text(encoding="utf-8"))
    out_path.write_text(xml, encoding="utf-8")
    return src_path, out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        default="all",
        help="变体名（walk/allcollisions/allcollisions_rollers/walk_backlash/"
             "allcollisions_backlash/allcollisions_rollers_backlash），默认 all",
    )
    parser.add_argument("--scale", type=float, default=1.0, help="全局缩放（默认 1.0，暂未实现）")
    args = parser.parse_args()

    variants = sorted(MODEL_VARIANTS) if args.variant == "all" else [args.variant]
    for variant in variants:
        src_path, out_path = generate(variant, args.scale)
        print(f"[crazy_chick] {src_path.name} -> {out_path.name}")
    print(f"  done: {len(variants)} variant(s), scale={args.scale}")
    print(f"  EDF motor added: edf_thrust (site=edf_exit, gear=+Z 向上)")
    print(f"  knee/ankle springs: stiffness+springref injected")


if __name__ == "__main__":
    main()
