#!/usr/bin/env python3
"""FT-SCS 舵机寄存器直读/直写 —— 主要是「真·松扭矩」。

为什么需要它（2026-09-30 实测）
--------------------------------
`robot.relax` 报 `torque off`、15 颗舵机全部 ACK，但关节**还是掰不动**。
绕开 robotd 直接读总线，15 颗的 reg40 实测全是 1 —— 那条"已松扭矩"的结论是假的。

**真根因（探针实测）：HD-1910 的固件在收到「目标位置」写入时会自动上扭矩。**
对停着的总线做单变量实验：

    写 reg40=0            -> 读回 0（15/15）
    什么都不写等 2 s       -> 读回 0（不是它自己跳）
    只写 reg42=当前位置    -> 读回 1（15/15）   <-- 就是这一步
    只写 reg46=速度        -> 读回 0（不是所有写都触发）

而 robotd 的控制环**每拍都在写 reg42**（`FeetechIo::apply` 的
`sync_write_goal_position`），因为它在"holding the pose found at startup"。
于是 `robot.relax` 把 reg40 写 0 之后，**下一拍（20 ms 后）就被舵机自己翻回 1**。
带 `--no-policy` 也一样：日志从 "policy unavailable" 变成 "policy disabled"，
但机器人仍然 hold 位姿、仍然写 reg42，reg40 照样回到 1。

次要发现：`duck-control/src/feetech.rs::set_torque` 走 rustypot 的
`write_torque_enable()`，**只发不等、没有读回校验**（同文件 `write_live()`
写 reg50/51/52 是写+读回校验的）。这不是根因，但它让整个问题一直看不见。

所以这个工具做两件 robotd 做不到的事：**趁 robotd 不在时**写 reg40，
然后**读回校验**。

几件必须知道的事
----------------
* **写完 reg40=0 之后不要马上重启 robotd**。它一起来就 hold 位姿、每拍写
  reg42，reg40 在 20 ms 内被翻回 1（见上）。想"松着看数字孪生"就用面板的
  **串口源**（只读 reg56/IMU，不写 reg42），不要用 telemetry 源。
* reg40 在舵机的 **SRAM** 里：掉电即清零，`pkill` 也不切它。所以停掉 robotd
  那一刻关节还是硬的，得靠这个工具写 0。
* 总线同一时刻只能有一个主：跑之前先在 WSL 里 `pkill -x robotd` 让出
  `/dev/ttyACM0`，不然两边抢串口。
* 只写 reg40（扭矩开关）。**不碰** reg42（目标位置，一写就上锁）、也不碰
  reg50/51/52 那组增益：那三个是训练锚点（32/40/0），改了下一次上电生效会
  直接把动力学搞错位（站漂、抖、摔）。

用法
----
    # 只读（默认动作）：打一张表，看 reg40 到底是不是 0
    python3 /mnt/e/optiDuck/tools/ft_regs.py

    # 真·松扭矩：逐颗写 reg40=0 再读回校验
    python3 /mnt/e/optiDuck/tools/ft_regs.py --off

    # 反向：写 reg40=1（一般不需要，robotd 的 init 会自己开）
    python3 /mnt/e/optiDuck/tools/ft_regs.py --on

    # 换端口 / 只动其中几颗
    python3 ft_regs.py --port COM8 --ids 33,34 --off

    # 给面板的一键按钮用：只打结论，不打表
    python3 ft_regs.py --off --brief

退出码：0 全部成功；1 有舵机没应答或读回与写入不一致；2 打不开串口。
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Dict, List, Optional

try:
    import serial
except ImportError:                                  # pragma: no cover
    print("缺 pyserial：WSL 里 `pip3 install pyserial`，Windows 上 `pip install pyserial`",
          file=sys.stderr)
    raise SystemExit(2)


# 台架接线：CH343 USB-TTL 桥，1 Mbps（WSL 里是 /dev/ttyACM0，Windows 上一般是某个 COM 口）
PORT_DEFAULT = "/dev/ttyACM0"
BAUD_DEFAULT = 1_000_000

# 总线上的 15 颗 HD-1910。顺序是"左腿 → 头颈嘴 → 右腿"，和 robotd 的 JOINT_IDS 一致。
IDS_DEFAULT: List[int] = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14]

NAMES: Dict[int, str] = {
    10: "right_hip_pitch", 11: "right_hip_roll", 12: "right_hip_yaw",
    13: "right_knee", 14: "right_ankle",
    20: "left_hip_pitch", 21: "left_hip_roll", 22: "left_hip_yaw",
    23: "left_knee", 24: "left_ankle",
    30: "head_yaw", 31: "head_pitch", 32: "neck",
    33: "mouth_upper", 34: "mouth",
}

# FT-SCS 指令
PING, READ, WRITE = 0x01, 0x02, 0x03

# 只看这几个寄存器。reg40 是本工具的主角；其余三个是"这颗舵机活着吗"的旁证，
# 顺便在表里显示，不用为它们单独跑一次。
REG_TORQUE = 40       # 1 B，扭矩开关（0=松 / 1=硬）
REG_SPEED = 46        # 2 B，运行速度（回中位时写 100 用的就是它）
REG_POSITION = 56     # 2 B，当前位置（raw，0..4095 一圈）
REG_VOLTAGE = 62      # 1 B，0.1 V/LSB
REG_TEMPERATURE = 63  # 1 B，°C

# 一次事务的读超时。舵机 1 Mbps 上一帧往返远小于 1 ms，60 ms 已经很宽容了。
TIMEOUT = 0.06


def _build(sid: int, instr: int, params: bytes = b"") -> bytes:
    """FT-SCS 帧：FF FF ID LEN INSTR PARAMS... CHECKSUM，CHECKSUM 是 ID..PARAMS 的按位取反。"""
    length = len(params) + 2
    body = bytes([sid, length, instr]) + params
    return b"\xff\xff" + body + bytes([(~sum(body)) & 0xFF])


def _read_frame(ser, timeout: float = TIMEOUT) -> Optional[bytes]:
    """从字节流里切出**一个校验通过**的应答帧，超时回 None。

    比"读几个字节然后赌对齐"稳：应答可能带前导回显、也可能上一轮残留半帧，
    所以扫 `FF FF` 头、按 LEN 取整帧、再核校验和，坏的直接丢、继续等下一帧。
    """
    deadline = time.perf_counter() + timeout
    buf = bytearray()
    while time.perf_counter() < deadline:
        chunk = ser.read(1)
        if not chunk:
            continue
        buf += chunk
        head = buf.find(b"\xff\xff")
        if head < 0:
            del buf[:-1]                 # 没有帧头，只留最后 1 字节防止跨读丢半个头
            continue
        if head:
            del buf[:head]
        if len(buf) < 4:
            continue
        total = 4 + buf[3]               # LEN = PARAMS + 2（INSTR + ERR）
        if len(buf) < total:
            continue
        frame = bytes(buf[:total])
        del buf[:total]
        if ((~sum(frame[2:total - 1])) & 0xFF) != frame[total - 1]:
            continue                     # 校验不过：不是给我们的帧
        return frame
    return None


def _rd(ser, sid: int, addr: int, size: int) -> Optional[int]:
    """读寄存器；没应答 / 校验坏 / ERR 非零都回 None（而不是编个 0 出来）。"""
    ser.reset_input_buffer()
    ser.write(_build(sid, READ, bytes([addr, size])))
    frame = _read_frame(ser)
    if frame is None or frame[2] != sid or frame[4] != 0:
        return None
    return int.from_bytes(frame[5:5 + size], "little")


def _wr(ser, sid: int, addr: int, data: bytes) -> bool:
    """写寄存器；只要收到该 ID 的、ERR=0 的应答就算成功（真成功以调用方的读回为准）。"""
    ser.reset_input_buffer()
    ser.write(_build(sid, WRITE, bytes([addr]) + data))
    frame = _read_frame(ser)
    return frame is not None and frame[2] == sid and frame[4] == 0


def _ping(ser, sid: int) -> bool:
    ser.reset_input_buffer()
    ser.write(_build(sid, PING))
    frame = _read_frame(ser)
    return frame is not None and frame[2] == sid


def _parse_ids(text: str) -> List[int]:
    out: List[int] = []
    for piece in text.replace("，", ",").split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.append(int(piece, 0))
        except ValueError:
            raise SystemExit(f"--ids 里 {piece!r} 不是数字")
    if not out:
        raise SystemExit("--ids 是空的")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="FT-SCS 舵机 reg40（扭矩）直读/直写，绕开 robotd 的 write-only 路径",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="跑之前先在 WSL 里 `pkill -x robotd`，让总线空出来。")
    ap.add_argument("--port", default=PORT_DEFAULT,
                    help=f"串口（WSL 里 /dev/ttyACM0，Windows 上 COMx），默认 {PORT_DEFAULT}")
    ap.add_argument("--baud", type=int, default=BAUD_DEFAULT,
                    help=f"波特率，默认 {BAUD_DEFAULT}")
    ap.add_argument("--ids", default=",".join(str(i) for i in IDS_DEFAULT),
                    help="要处理的舵机 ID，逗号分隔，默认总线上的 15 颗")
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--read", action="store_true", help="只读并打表（默认动作）")
    action.add_argument("--off", action="store_true", help="写 reg40=0：真·松扭矩")
    action.add_argument("--on", action="store_true", help="写 reg40=1：开扭矩")
    ap.add_argument("--brief", action="store_true",
                    help="只打结论行，不打表（给面板的一键按钮用）")
    args = ap.parse_args(argv)

    ids = _parse_ids(args.ids)
    write_value: Optional[int] = 0 if args.off else (1 if args.on else None)
    label = "真·松扭矩（reg40=0）" if args.off else (
        "开扭矩（reg40=1）" if args.on else "只读")

    print(f"== FT-SCS {label} ==")
    print(f"端口 {args.port} @ {args.baud} · 目标 {len(ids)} 颗")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.02)
    except Exception as exc:                          # noqa: BLE001
        print(f"!! 打不开 {args.port}：{exc}")
        print("   （WSL 里多半是 robotd / 别的进程还占着口：先 `pkill -x robotd`）")
        return 2

    try:
        alive = [sid for sid in ids if _ping(ser, sid)]
        missing = [sid for sid in ids if sid not in alive]
        print(f"总线应答 {len(alive)}/{len(ids)}"
              + (f" · 没应答：{missing}" if missing else ""))

        if write_value is not None and alive:
            print(f"逐颗写 reg40={write_value} …")
            for sid in alive:
                _wr(ser, sid, REG_TORQUE, bytes([write_value]))
            time.sleep(0.2)                            # 让它落进 SRAM 再读回

        if not args.brief:
            hdr = (f"{'ID':>3} {'名字':<16} {'扭矩':>4} {'速度':>6} "
                   f"{'位置':>6} {'电压':>6} {'温度':>4}")
            print()
            print(hdr)
            print("-" * len(hdr))
            for sid in alive:
                torque = _rd(ser, sid, REG_TORQUE, 1)
                speed = _rd(ser, sid, REG_SPEED, 2)
                pos = _rd(ser, sid, REG_POSITION, 2)
                volt = _rd(ser, sid, REG_VOLTAGE, 1)
                temp = _rd(ser, sid, REG_TEMPERATURE, 1)
                print(f"{sid:>3} {NAMES.get(sid, '?'):<16} {str(torque):>4} "
                      f"{str(speed):>6} {str(pos):>6} "
                      f"{('%.1f V' % (volt / 10)) if volt is not None else '—':>6} "
                      f"{str(temp):>4}")

        # ---- 结论：这一行是给面板 tail 用的，异常必须显式列出来 ----
        readback = {sid: _rd(ser, sid, REG_TORQUE, 1) for sid in alive}
        if write_value is None:
            hard = [sid for sid, v in readback.items() if v == 1]
            soft = [sid for sid, v in readback.items() if v == 0]
            print()
            if soft:
                print(f"reg40=0（已松）：{len(soft)} 颗 {soft}")
            if hard:
                print(f"reg40=1（还硬着）：{len(hard)} 颗 {hard}")
            bad = [sid for sid, v in readback.items() if v is None]
            if bad:
                print(f"读不到 reg40：{len(bad)} 颗 {bad}")
            ok = not hard and not bad and len(alive) == len(ids)
            print(f"结论：{'15 颗全部已松 ✅' if ok else '还有问题 ❌'}"
                  + ("" if ok else "（看上面那几行）"))
            return 0 if ok else 1

        good = [sid for sid, v in readback.items() if v == write_value]
        bad = [sid for sid, v in readback.items() if v != write_value]
        print()
        print(f"写 reg40={write_value} 后读回一致：{len(good)}/{len(alive)}")
        if bad:
            for sid in bad:
                print(f"  !! {sid} {NAMES.get(sid, '?')} 读回 "
                      f"{readback[sid]!r}（想要 {write_value}）")
        if missing:
            print(f"  !! 没应答：{missing}")
        ok = not bad and not missing
        if args.off:
            print("结论：" + ("15 颗全部已松 ✅ 关节现在应该能掰动"
                            if ok else "有没松掉的 ❌ 见上面"))
        else:
            print("结论：" + ("全部写入并读回一致 ✅" if ok else "有不一致的 ❌ 见上面"))
        return 0 if ok else 1
    finally:
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())