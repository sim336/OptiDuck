# -*- coding: utf-8 -*-
"""监控 MicroDuck 训练效果。解析最新 velocity run 的 tfevents，
输出关键指标，并写一份状态摘要到 monitor_state.txt（供计划任务日志/告警用）。

用法： python _monitor_train.py [--interval-s 默认输出到 stdout]
由 Windows 计划任务定期调用。退出码：
  0 = 正常
  2 = 训练进程未运行（可能已停止/崩溃）
"""
import glob
import os
import sys
import time

import psutil

LOGS = r"e:\optiDuck\microduck_rl\logs\rsl_rl\velocity"
OUT_TXT = r"e:\optiDuck\monitor_state.txt"


def running():
    for p in psutil.process_iter(["name"]):
        n = p.info.get("name", "").lower()
        if n.startswith("python"):
            try:
                cmd = " ".join(p.cmdline()).lower()
            except Exception:
                continue
            if "train" in cmd or "mjlab" in cmd:
                return True
    return False


def latest_run_dir():
    dirs = [d for d in glob.glob(os.path.join(LOGS, "2026-*_velocity"))
            if os.path.isdir(d)]
    if not dirs:
        return None
    return max(dirs, key=os.path.getmtime)


def last_scalar(acc, tag):
    try:
        events = acc.Scalars(tag)
        if not events:
            return None
        return events[-1].value
    except Exception:
        return None


def main():
    from tensorboard.backend.event_processing import event_accumulator

    alive = running()
    run_dir = latest_run_dir()

    if not run_dir:
        print(f"[{time.strftime('%H:%M:%S')}] 未找到训练运行目录: {LOGS}")
        sys.exit(2 if alive else 0)

    try:
        acc = event_accumulator.EventAccumulator(
            run_dir, size_guidance={"scalars": 1000000})
        acc.Reload()
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] 解析 tfevents 失败: {e}")
        sys.exit(0)  # 文件可能正在写入，非致命

    def g(tag):
        return last_scalar(acc, tag)

    iter_now = len(acc.Tags().get("scalars", [])) and (
        len(acc.Scalars("Train/mean_episode_length")) or 0)
    status = "运行中" if alive else "已停止/崩溃"
    mr = g("Train/mean_reward")
    mel = g("Train/mean_episode_length")
    landed = g("Episode_Reward/upright")
    vel = g("Episode_Reward/track_linear_velocity")
    angvel = g("Episode_Reward/track_angular_velocity")
    ar = g("Episode_Reward/action_rate_l2")
    angmom = g("Episode_Reward/angular_momentum")
    nan = g("Episode_Termination/nan_state")
    fell = g("Episode_Termination/fell_over")

    lines = []
    lines.append("=" * 46)
    lines.append(f"训练监控  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"状态        : {status}")
    lines.append(f"运行目录    : {os.path.basename(run_dir)}")
    lines.append("-" * 46)
    lines.append(f"迭代轮数     : {iter_now}")
    lines.append(f"平均回报     : {mr:.4f}" if mr is not None else "平均回报     : N/A")
    lines.append(f"平均回合长   : {mel:.2f}" if mel is not None else "平均回合长   : N/A")
    lines.append(f"直立奖励     : {landed:.4f}" if landed is not None else "直立奖励     : N/A")
    lines.append(f"线速度跟踪   : {vel:.4f}" if vel is not None else "线速度跟踪   : N/A")
    lines.append(f"角速度跟踪   : {angvel:.4f}" if angvel is not None else "角速度跟踪   : N/A")
    lines.append("-" * 46)
    # 关键惩罚项（应为 <=0，若有 >0 说明符号被翻转为奖励，需告警）
    lines.append(f"action_rate 惩罚: {ar:.4f}" if ar is not None else "action_rate 惩罚: N/A")
    lines.append(f"angular_momentum: {angmom:.4f}" if angmom is not None else "angular_momentum: N/A")
    lines.append(f"NaN 终止        : {nan}" if nan is not None else "NaN 终止        : N/A")
    lines.append(f"摔倒次数/回合  : {fell}" if fell is not None else "摔倒次数/回合  : N/A")
    lines.append("=" * 46)

    msg = "\n".join(lines)
    print(msg)

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(msg + "\n")

    # 告警检查
    warns = []
    if not alive:
        warns.append("训练进程未在运行！")
    if ar is not None and ar > 0:
        warns.append("WARNING: action_rate 惩罚为正值（奖励符号异常）")
    if angmom is not None and angmom < -1000:  # 过大的洪动能惩罚
        warns.append("注意: angular_momentum 惩罚偏大")
    if nan is not None and nan != 0:
        warns.append("WARNING: 检测到 NaN 状态终止")
    for w in warns:
        print(f"[ALERT] {w}")

    exit_code = 2 if (not alive) else 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()