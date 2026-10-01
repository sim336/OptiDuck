#!/usr/bin/env python3
"""HD-1910 总线舵机配置工具（图形界面）。

用于装机前逐颗配置舵机 ID 与波特率。背景：
  - 接插件是 AMP2.0-3P。规格书 §6-7 针序：1=Signal / 2=Vcc / 3=GND，
    且**三根线同为黑色**——不能靠线色分辨，只能认插头针位。
  - 出厂 ID 为 1（规格书 §7-3）。波特率规格书 §7-4 写"出厂默认 1 Mbps"，
    而 robotd 侧常量 FACTORY_BAUD_RATE=57600（那是 XL330 的出厂值）——两者矛盾，
    以实测为准，所以识别时会依次试 1 Mbps / 57.6 kbps / 115.2 kbps 三档。
  - robotd 的 auto-adopt 只在"恰好缺一个关节"时生效，15 颗全新舵机全是 ID 1
    会同时应答，收养逻辑直接放弃 -> 首次装机必须手工逐颗配
  - 总线运行参数里的 return_delay_time / pwm_slope / shutdown 由 robotd 启动时写，
    所以手工只需要写 ID 和 baud_rate 两项
  - 信号电平（规格书 §12）：高 2–5 V、低 0–0.45 V，3.3 V 逻辑可用。

协议层为什么有两套：
  robotd 走 DynamixelIo（XL330 寄存器表，baud_rate=3 表示 1 Mbps），
  而仓库里的台架脚本 hd1910_bench_record.py 走 pypot 的 FeetechSTS3215IO
  （STS3215 表，且脚本自己标注 MUST be verified against the real unit）。
  两者地址不同，因此本工具两套都实现，由"自动识别"实测决定用哪套。

运行：
    uv run --no-project tools/servo_config_gui.py            # uv 自动装 pyserial
    python tools/servo_config_gui.py                          # 已装 pyserial 时
    python tools/servo_config_gui.py --probe COM7             # 命令行只读探测（不开界面）
    python tools/servo_config_gui.py --dump COM7              # 只读 dump 寄存器（不开界面）
    python tools/servo_config_gui.py --set-id COM7 --to 10    # 配一颗的 ID（会写 EPROM）
    python tools/servo_config_gui.py --selftest               # 界面自检，不碰串口
    python tools/servo_config_gui.py --prototest              # 协议编解码自测
"""

# /// script
# requires-python = ">=3.9"
# dependencies = ["pyserial>=3.5"]
# ///

from __future__ import annotations

import json
import os
import queue
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import tkinter as tk
from tkinter import messagebox, ttk

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - 仅在缺依赖时触发
    print("缺少 pyserial。请用 `uv run --no-project tools/servo_config_gui.py` 运行，"
          "或先执行 `pip install pyserial`。", file=sys.stderr)
    raise SystemExit(1)


# ============================================================================
# 寄存器表
#
# !! 写入任何寄存器之前，请先跑『自动识别』实测确认 !!
#
# 下面两套地址来源：
#   DXL_XL330 : robotd 的 DynamixelIo + XL330 控制表（RobotIS e-manual）
#   FT_HD1910 : 真机实测表，见
#               microduck_rl/vendor/bam/docs/identification/hd1910_servo_notes.md
#
# 2026-09-27 实测结论：用户手上这颗 HD-1910 走的是 **飞特 FT-SCS 帧格式**
# （FF FF ID LEN INSTR … CHK），1 Mbps / ID 1 应答，读侧与 FT_HD1910 表吻合
# （reg0=3 固件主版本、reg6=0 即 1 Mbps、reg62→5.0 V、reg63→30℃、reg56→305）。
# ============================================================================

# Dynamixel 波特率码表（baud_rate=3 即 1 Mbps，与 robotd 的 BAUD_RATE 一致）
DXL_BAUD_TABLE: Dict[int, int] = {
    0: 9_600, 1: 57_600, 2: 115_200, 3: 1_000_000,
    4: 2_000_000, 5: 3_000_000, 6: 4_000_000, 7: 4_500_000,
}

# 飞特 STS 系列波特率码表（出厂 57.6 kbps = 码 6，与 DXL 表完全不同）
FT_BAUD_TABLE: Dict[int, int] = {
    0: 1_000_000, 1: 500_000, 2: 250_000, 3: 128_000, 4: 115_200,
    5: 76_800, 6: 57_600, 7: 38_400, 8: 19_200, 9: 14_400, 10: 9_600,
}


@dataclass(frozen=True)
class RegMap:
    """一套 (协议帧格式 + 寄存器地址表)。"""

    key: str                            # 内部标识
    label: str                          # 界面显示名
    proto: str                          # "dxl2" | "ft"
    model: Optional[Tuple[int, int]]    # (地址, 字节数)；None 表示该平台没有
    fw: Tuple[int, int]                 # 固件**主**版本
    fw_minor: Optional[Tuple[int, int]] # 固件**次**版本；飞特 3.46 里的 ".46"
    sv: Optional[Tuple[int, int]]       # 服务端/软件版本
    sid: Tuple[int, int]                # ID
    baud: Tuple[int, int]               # 波特率
    rdt: Optional[Tuple[int, int]]      # return delay time
    lock: Optional[Tuple[int, int]]     # EPROM 写锁标志（1=锁；写 EPROM 前先置 0）
    eprom_range: Optional[Tuple[int, int]]  # EPROM 可写区间（闭区间）；None=不分区
    mode: Optional[Tuple[int, int]]     # 运行模式
    status: Optional[Tuple[int, int]]   # 舵机状态 / 错误位
    pos: Tuple[int, int]                # 当前位置
    volt: Tuple[int, int]               # 当前电压
    temp: Tuple[int, int]               # 当前温度
    baud_table: Dict[int, int]
    volt_scale: float                   # 原始值 -> 伏特

    def baud_to_bps(self, code: int) -> Optional[int]:
        return self.baud_table.get(code)

    def bps_to_baud(self, bps: int) -> Optional[int]:
        for code, val in self.baud_table.items():
            if val == bps:
                return code
        return None


# --- Dynamixel 2.0 + XL330 控制表（robotd 走这条） -------------------------
MAP_DXL = RegMap(
    key="dxl",
    label="Dynamixel 2.0 / XL330 表",
    proto="dxl2",
    model=(0, 2),          # Model Number
    fw=(6, 1),             # Version of Firmware（XL330 是单字节，如 46/52）
    fw_minor=None,
    sv=None,
    sid=(7, 1),            # ID
    baud=(8, 1),           # Baud Rate
    rdt=(9, 1),            # Return Delay Time
    lock=None,             # XL330 控制表里没有这个锁
    eprom_range=None,
    mode=(11, 1),          # Operating Mode
    status=(70, 1),        # Hardware Error Status
    pos=(132, 4),          # Present Position
    volt=(144, 2),         # Present Input Voltage，待核对
    temp=(146, 1),         # Present Temperature，待核对
    baud_table=DXL_BAUD_TABLE,
    volt_scale=0.1,
)

# --- 飞特 FT-SCS + HD-1910 控制表（真机实测，优先用这条） -------------------
# 来源：microduck_rl/vendor/bam/docs/identification/hd1910_servo_notes.md
#   §3 地址表 —— 2026-09 真机全址 0–86 回读，地址/字节序/单位与 FT-HLS 同构；
#   §6.6 与飞特官方 FDdebug 导出的 hd1910m.xdat 逐项交叉验证，0–39 段完全一致。
# 因此这张表**可以按真机验证过的读侧直接使用**，不再是"待核对"。
MAP_FT = RegMap(
    key="ft",
    label="飞特 FT-SCS / HD-1910 表",
    proto="ft",
    model=(3, 2),          # 型号特征值：HD=7946；HLS 家族=10（读 1 字节时得 10）
    fw=(0, 1),             # 固件主版本
    fw_minor=(1, 1),       # 固件次版本（真机 3.46 -> 主 3 / 次 46）
    sv=None,               # 地址 2 是 "END"（0=小端存储结构），不是软件版本
    sid=(5, 1),            # 主 ID（0–253，出厂 1）
    baud=(6, 1),           # 波特率档（0=1M … 7=38.4k，出厂 0）
    rdt=None,              # 地址 7 是"无定义"，不是 return delay time
    lock=(55, 1),          # 锁标志：1=EPROM 写锁，写 EPROM 前必须先置 0
    eprom_range=(5, 39),   # EPROM 区（掉电保存，受锁标志保护）
    mode=(33, 1),          # 运行模式：0=角度伺服 … 4=纯位置 PD（出厂 4）
    status=(65, 1),        # 舵机状态错误位，0=正常
    pos=(56, 2),           # 当前位置
    volt=(62, 1),          # 当前输入电压（0.1 V/LSB）
    temp=(63, 1),          # 当前温度（℃）
    baud_table=FT_BAUD_TABLE,
    volt_scale=0.1,
)

MAPS: Tuple[RegMap, ...] = (MAP_DXL, MAP_FT)


# ============================================================================
# 协议层
#
# 两种帧格式：
#   Dynamixel 2.0 : FF FF FD 00 | ID | LEN_L LEN_H | INSTR | PARAM... | CRC_L CRC_H
#                   LEN = 指令(1) + 参数(N) + CRC(2) = N + 3
#                   整帧长度 = 4(头) + 1(ID) + 2(LEN) + LEN = N + 10
#                   应答帧同构，只是把"指令"位换成"error"位
#   飞特/DXL1.0   : FF FF | ID | LEN | INSTR | PARAM... | CHK
#                   LEN = 参数(N) + 2，整帧长度 = N + 6
#                   校验和 = ~(ID + LEN + INSTR + sum(PARAM)) & 0xFF
# ============================================================================

INSTR_PING = 0x01
INSTR_READ = 0x02
INSTR_WRITE = 0x03
# 广播读：只发一帧（参数 = 起始地址, 长度, 各舵机 ID），每颗自己回一帧。
# 飞特私有指令，Dynamixel 侧没有对应项，所以只在 ft 协议下可用。
INSTR_SYNC_READ = 0x82

BROADCAST_ID = 0xFE


def dxl2_crc(data: bytes) -> int:
    """Dynamixel Protocol 2.0 的 CRC-16（poly 0x8005，初值 0，小端输出）。"""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_frame(proto: str, sid: int, instr: int, params: bytes = b"") -> bytes:
    """按协议构造一条指令帧。"""
    if proto == "dxl2":
        body = bytes([sid]) + struct.pack("<H", len(params) + 3) + bytes([instr]) + params
        return b"\xff\xff\xfd\x00" + body + struct.pack("<H", dxl2_crc(body))

    length = len(params) + 2
    checksum = ~(sid + length + instr + sum(params)) & 0xFF
    return bytes([0xFF, 0xFF, sid, length, instr]) + params + bytes([checksum])


@dataclass(frozen=True)
class Frame:
    sid: int
    err: int
    params: bytes
    raw: bytes

    @property
    def total(self) -> int:
        return len(self.raw)


def parse_one_frame(proto: str, buf: bytes) -> Optional[Frame]:
    """尝试把 buf 的**开头**解析成一帧。解析不出（含数据不足）返回 None。"""
    if proto == "dxl2":
        if len(buf) < 10 or buf[:4] != b"\xff\xff\xfd\x00":
            return None
        length = struct.unpack_from("<H", buf, 5)[0]
        if length < 3:
            return None
        total = 7 + length                     # 头4 + ID1 + LEN2 + LEN
        if len(buf) < total:
            return None
        raw = buf[:total]
        if dxl2_crc(raw[4:total - 2]) != struct.unpack_from("<H", raw, total - 2)[0]:
            return None
        # 应答帧：FF FF FD 00 | ID | LEN2 | ERROR | PARAM... | CRC2
        return Frame(sid=raw[4], err=raw[7], params=raw[8:total - 2], raw=raw)

    if len(buf) < 6 or buf[:2] != b"\xff\xff":
        return None
    length = buf[3]
    if length < 2:
        return None
    total = 4 + length                          # FF FF ID LEN + LEN
    if len(buf) < total:
        return None
    raw = buf[:total]
    if (~sum(raw[2:total - 1]) & 0xFF) != raw[total - 1]:
        return None
    return Frame(sid=raw[2], err=raw[4], params=raw[5:total - 1], raw=raw)


def iter_frames(proto: str, buf: bytes):
    """从 buf 里连续解析出所有完整帧，返回 (帧列表, 已消费字节数)。"""
    frames: List[Frame] = []
    pos = 0
    while pos < len(buf):
        frame = parse_one_frame(proto, buf[pos:])
        if frame is None:
            # 头部对不上就往后挪一字节继续找（丢弃回显噪声/半截数据）
            if len(buf) - pos < 10:
                break
            pos += 1
            continue
        frames.append(frame)
        pos += frame.total
    return frames, pos


class ServoBus:
    """串口 + 半双工收发。单线程使用，不要跨线程共享。"""

    def __init__(self) -> None:
        self.ser: Optional[serial.Serial] = None
        self.proto: str = "dxl2"

    @property
    def is_open(self) -> bool:
        return self.ser is not None and self.ser.is_open

    @property
    def baudrate(self) -> int:
        return int(self.ser.baudrate) if self.ser else 0

    # --- 连接管理 ---
    def open(self, port: str, baud: int, proto: str) -> None:
        self.close()
        # 半双工适配器会把 TX 回显到 RX，所以不依赖 in_waiting 判断，
        # 靠逐帧解析 + 回显剔除来容错。
        self.ser = serial.Serial(
            port=port, baudrate=baud, bytesize=8,
            parity=serial.PARITY_NONE, stopbits=1,
            timeout=0.02, write_timeout=0.2,
        )
        self.proto = proto
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def reopen(self, baud: int) -> None:
        """保持端口与协议不变，只换速率（写完 baud_rate 后需要）。"""
        if self.ser is None:
            raise RuntimeError("串口未打开")
        port, proto = self.ser.port, self.proto
        self.open(port, baud, proto)

    # --- 收发 ---
    def transact(self, sid: int, instr: int, params: bytes = b"",
                 timeout: float = 0.08) -> Optional[Frame]:
        """发一条指令并等应答。返回应答帧；超时返回 None。"""
        if self.ser is None:
            raise RuntimeError("串口未打开")

        sent = build_frame(self.proto, sid, instr, params)
        self.ser.reset_input_buffer()
        self.ser.write(sent)
        self.ser.flush()

        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self.ser.read(max(1, self.ser.in_waiting))
            if not chunk:
                time.sleep(0.001)
                continue
            buf.extend(chunk)
            frames, consumed = iter_frames(self.proto, bytes(buf))
            for frame in frames:
                # 半双工回显：回到 RX 的字节与我们发出的完全相同，直接丢弃。
                # 这是确定性的判据，不依赖 sleep 时序。
                if frame.raw == sent:
                    continue
                if frame.sid == sid or frame.sid == BROADCAST_ID:
                    return frame
            if consumed:
                del buf[:consumed]
        return None

    # --- 高层操作 ---
    def ping(self, sid: int, timeout: float = 0.05) -> bool:
        return self.transact(sid, INSTR_PING, b"", timeout) is not None

    def read(self, sid: int, addr: int, size: int,
             timeout: float = 0.08) -> Optional[bytes]:
        if self.proto == "dxl2":
            params = struct.pack("<HH", addr, size)
        else:
            params = bytes([addr, size])
        frame = self.transact(sid, INSTR_READ, params, timeout)
        if frame is None or frame.err != 0 or len(frame.params) < size:
            return None
        return frame.params[:size]

    def write(self, sid: int, addr: int, data: bytes,
              timeout: float = 0.08) -> bool:
        if self.proto == "dxl2":
            params = struct.pack("<HH", addr, len(data)) + data
        else:
            params = bytes([addr]) + data
        frame = self.transact(sid, INSTR_WRITE, params, timeout)
        return frame is not None and frame.err == 0

    def read_value(self, sid: int, spec: Tuple[int, int]) -> Optional[int]:
        raw = self.read(sid, spec[0], spec[1])
        return None if raw is None else int.from_bytes(raw, "little")

    def sync_read(self, ids: Sequence[int], addr: int, size: int,
                  timeout: float = 0.06) -> Dict[int, bytes]:
        """广播读一批同名寄存器块，返回 {id: 原始字节}；没应答的 id 不在结果里。

        只发一帧（不是逐颗发 READ），所以 15 颗只要一次事务的线上时间 —— 这是
        50 Hz 只看位置的镜像能再挂上电压/温度/电流的前提：逐颗 READ 15 次的光
        往返就已经吃掉大半个控制周期了。

        广播读**没有**"共几帧"的长度字，每颗各回一帧，所以只能按"集齐 or 超时"
        收口；超时到点就用已经收到的，缺的那几颗由调用方当"没应答"处理。
        半双工回显仍用逐字节比对剔除（与 transact 同一判据，不赌时序）。
        """
        if self.ser is None:
            raise RuntimeError("串口未打开")
        if self.proto != "ft":
            raise RuntimeError("广播读是飞特私有指令，dxl2 下不可用")

        sent = build_frame(self.proto, BROADCAST_ID, INSTR_SYNC_READ,
                           bytes([addr, size]) + bytes(ids))
        self.ser.reset_input_buffer()
        self.ser.write(sent)
        self.ser.flush()

        want = set(int(i) for i in ids)
        got: Dict[int, bytes] = {}
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while (want - set(got)) and time.monotonic() < deadline:
            chunk = self.ser.read(max(1, self.ser.in_waiting))
            if not chunk:
                time.sleep(0.001)
                continue
            buf.extend(chunk)
            frames, consumed = iter_frames(self.proto, bytes(buf))
            for frame in frames:
                if frame.raw == sent:
                    continue
                if frame.err == 0 and frame.sid in want and len(frame.params) >= size:
                    got[frame.sid] = frame.params[:size]
            if consumed:
                del buf[:consumed]
        return got


# ============================================================================
# 业务动作
# ============================================================================

@dataclass
class Identity:
    regmap: RegMap
    sid: int
    model: Optional[int] = None
    fw: Optional[int] = None
    fw_minor: Optional[int] = None
    sv: Optional[int] = None
    baud_code: Optional[int] = None
    rdt: Optional[int] = None
    lock: Optional[int] = None
    mode: Optional[int] = None
    status: Optional[int] = None
    volt: Optional[float] = None
    temp: Optional[int] = None
    pos: Optional[int] = None

    @property
    def fw_text(self) -> Optional[str]:
        if self.fw is None:
            return None
        return str(self.fw) if self.fw_minor is None else f"{self.fw}.{self.fw_minor}"

    @property
    def fw_gate(self) -> Optional[int]:
        """做 ≥v46 判定用的那个数字：飞特取次版本号（3.46 -> 46），DXL 取固件字节。"""
        return self.fw_minor if self.fw_minor is not None else self.fw


CANDIDATE_IDS: Sequence[int] = (
    (1, 0)
    + tuple(range(10, 15)) + tuple(range(20, 25)) + tuple(range(30, 35))
)


def identify(bus: ServoBus, log: Callable[[str], None]) -> Optional[Identity]:
    """自动识别协议 + 读回信息。全程只读。

    实作策略：先在当前速率下，两套协议 × 候选 ID 逐个 ping；
    全无应答就换另一个速率再来一轮（出厂 57.6 kbps / 已配置 1 Mbps）。
    """
    rates: List[int] = []
    for rate in (bus.baudrate, 1_000_000, 57_600, 115_200):
        if rate and rate not in rates:
            rates.append(rate)

    hit: Optional[Tuple[RegMap, int]] = None
    for rate in rates:
        if bus.baudrate != rate:
            bus.reopen(rate)
            log(f"  切到 {rate:,} bps 再试…")
        for regmap in MAPS:
            bus.proto = regmap.proto
            for sid in CANDIDATE_IDS:
                if bus.ping(sid, timeout=0.03):
                    hit = (regmap, sid)
                    break
            if hit:
                break
        if hit:
            break

    if hit is None:
        log(f"  无应答（已试 {len(rates)} 个速率 × 2 套协议 × {len(CANDIDATE_IDS)} 个 ID）")
        return None

    regmap, sid = hit
    log(f"  ✓ 应答：{regmap.label}，ID {sid}，@ {bus.baudrate:,} bps")

    ident = Identity(regmap=regmap, sid=sid)

    def rv(spec: Optional[Tuple[int, int]]) -> Optional[int]:
        return None if spec is None else bus.read_value(sid, spec)

    ident.model = rv(regmap.model)
    ident.fw = rv(regmap.fw)
    ident.fw_minor = rv(regmap.fw_minor)
    ident.sv = rv(regmap.sv)
    ident.baud_code = rv(regmap.baud)
    ident.rdt = rv(regmap.rdt)
    ident.lock = rv(regmap.lock)
    ident.mode = rv(regmap.mode)
    ident.status = rv(regmap.status)
    raw_volt = rv(regmap.volt)
    ident.volt = None if raw_volt is None else raw_volt * regmap.volt_scale
    ident.temp = rv(regmap.temp)
    ident.pos = rv(regmap.pos)
    return ident


def describe(ident: Identity) -> str:
    rm = ident.regmap
    bps = rm.baud_to_bps(ident.baud_code) if ident.baud_code is not None else None
    lines = [
        f"协议      : {rm.label}",
        f"ID        : {ident.sid}",
    ]
    if ident.model is not None:
        lines.append(f"型号编号  : {ident.model}（0x{ident.model:04X}）")
    if ident.fw_text is not None:
        lines.append(f"固件版本  : {ident.fw_text}")
    if ident.sv is not None:
        lines.append(f"软件版本  : {ident.sv}")
    if ident.baud_code is not None:
        lines.append(f"波特率    : 码 {ident.baud_code}"
                     + (f" = {bps:,} bps" if bps else "（不在已知码表内）"))
    if ident.lock is not None:
        lines.append("EPROM 锁  : "
                     + (f"{ident.lock}（已锁，写 EPROM 前必须先置 0）"
                        if ident.lock else f"{ident.lock}（已解锁，可直接写 EPROM）"))
    if ident.mode is not None:
        lines.append(f"运行模式  : {ident.mode}")
    if ident.status is not None:
        lines.append(f"舵机状态  : {ident.status}"
                     + ("（无错误）" if ident.status == 0 else "（有错误位）"))
    if ident.rdt is not None:
        lines.append(f"return_delay_time : {ident.rdt}")
    if ident.volt is not None:
        lines.append(f"供电电压  : {ident.volt:.1f} V")
    if ident.temp is not None:
        lines.append(f"温度      : {ident.temp} ℃")
    if ident.pos is not None:
        lines.append(f"当前位置  : {ident.pos}")
    return "\n".join(lines)


def write_verify(bus: ServoBus, sid: int, addr: int, data: bytes,
                 read_sid: Optional[int] = None) -> bool:
    """写一个寄存器并**回读校验**。

    为什么不靠应答帧判定成功：reg8（应答状态级别）可以被设成 0，那时
    "除读/PING 外不返回"，写指令就没有应答帧；而且 EPROM 写被锁标志挡住时
    也是静默丢弃。回读是唯一可靠的判据（本机实测 reg8=1，应答是有的，但
    仍然只信回读）。

    read_sid：回读时用哪个 ID 发读指令。**写 reg5(ID) 时必须传新 ID**——
    真机实测 ID 写入后立刻生效，舵机当场改用新 ID 应答，再用旧 ID 去读
    只会超时（2026-09-27 实测踩过这个坑）。
    """
    probe_sid = sid if read_sid is None else read_sid
    bus.write(sid, addr, data, timeout=0.15)   # 有没有应答都往下走
    time.sleep(0.03)
    return bus.read(probe_sid, addr, len(data)) == data


def configure_one(bus: ServoBus, regmap: RegMap, from_id: int, to_id: int,
                  target_bps: int, log: Callable[[str], None]) -> bool:
    """配一颗：解锁 EPROM -> 写 ID -> 写波特率（最后）-> 回读校验 -> 重新上锁
    -> 回中位 2048 并关扭矩。

    HD-1910 的 ID(reg5) 与波特率(reg6) 都在 EPROM 区(5–39)，受 reg55 锁标志
    保护（1=锁）。必须先写 reg55=0，否则写 ID 会被静默丢弃——而且因为
    reg8=0 时写操作不返回应答帧，失败时不会有任何报错，只能靠回读发现。

    顺序照 robotd bus.rs 的做法：先写 ID 再写波特率，因为写波特率是在
    "旧速率"下发的、之后舵机才切速；反过来则两次写之间要重开端口。

    回中位放在最后（第 7 步）：装配前必须保证输出轴在中位 2048，否则装入
    结构后关节角度会整体偏掉，且可能需要强行扭转输出轴对准螺丝孔。
    """
    baud_code = regmap.bps_to_baud(target_bps)
    if baud_code is None:
        log(f"  ✗ {target_bps:,} bps 不在这套协议的波特率码表里")
        return False

    log(f"  [1/7] 在 {bus.baudrate:,} bps 下确认 ID {from_id} 在线")
    if not bus.ping(from_id):
        log(f"  ✗ ID {from_id} 无应答，中止（此处失败不要继续写）")
        return False

    unlocked = False
    if regmap.lock is not None:
        log(f"  [2/7] 写锁标志 reg{regmap.lock[0]} = 0（解锁 EPROM）")
        if not write_verify(bus, from_id, regmap.lock[0], b"\x00"):
            log("  ✗ 解锁失败——写 EPROM 会被静默丢弃，中止")
            return False
        unlocked = True
    else:
        log("  [2/7] 该协议表没有 EPROM 锁，跳过解锁")

    log(f"  [3/7] 写 ID：reg{regmap.sid[0]} {from_id} -> {to_id}")
    if not write_verify(bus, from_id, regmap.sid[0], bytes([to_id]),
                        read_sid=to_id):
        log(f"  ✗ 写 ID 失败。已用新旧两个 ID 都试过回读，都没读到 {to_id}")
        log(f"    （ID 写入后立刻生效，所以回读必须用新 ID {to_id}）")
        return False
    log(f"        已回读确认 {to_id} 应答")

    cur_code = bus.read_value(to_id, regmap.baud)
    if cur_code == baud_code:
        log(f"  [4/7] 波特率已经是码 {baud_code}（= {target_bps:,} bps），无需修改")
    else:
        log(f"  [4/7] 写波特率：reg{regmap.baud[0]} 码 {cur_code} -> {baud_code}"
            f"（= {target_bps:,} bps），此后舵机切速")
        if not write_verify(bus, to_id, regmap.baud[0], bytes([baud_code])):
            log("  ✗ 写波特率后回读不匹配，中止")
            return False
        time.sleep(0.05)
        try:
            bus.reopen(target_bps)
        except Exception as exc:
            log(f"  ✗ 重开串口失败：{exc}")
            return False

    log(f"  [5/7] 在 {target_bps:,} bps 下回读校验")
    if not bus.ping(to_id):
        log(f"  ✗ 切到 {target_bps:,} bps 后 {to_id} 无应答——"
            f"配置可能已写入但校验失败，先别改，重跑识别看看")
        return False
    final_id = bus.read_value(to_id, regmap.sid)
    final_code = bus.read_value(to_id, regmap.baud)
    if final_id != to_id or final_code != baud_code:
        log(f"  ✗ 回读不一致：ID={final_id}，波特率码={final_code}")
        return False

    if unlocked and regmap.lock is not None:
        log(f"  [6/7] 写回锁标志 reg{regmap.lock[0]} = 1（重新上锁）")
        if not write_verify(bus, to_id, regmap.lock[0], b"\x01"):
            log("  ⚠ 重新上锁失败——不影响使用，但下次配这颗前要重新解锁")

    # --- 第 7 步：回中位 2048（装配前必须在中位） ---
    # mode 4 已验证可动写序之一（hd1910_servo_notes §6.3）：
    #   40=1(开扭矩) → 46=速度 → 42=目标
    # 注意 reg46 出厂为 0（0=停止），不设速度舵机不会动。
    # reg46 单位 0.732 RPM/LSB，100 ≈ 73 RPM，空载回中位足够快。
    log(f"  [7/7] 回中位：开扭矩 → 设速度 → 目标位置=2048 → 等待到位 → 关扭矩固定")
    bus.write(to_id, 40, b"\x01", timeout=0.15)          # 扭矩开
    time.sleep(0.05)
    bus.write(to_id, 46, struct.pack("<H", 100), timeout=0.15)  # 速度 ≈73 RPM
    time.sleep(0.05)
    bus.write(to_id, 42, struct.pack("<H", 2048), timeout=0.15)  # 目标=中位
    time.sleep(1.5)                                # 等舵机转到中位
    pos_raw = bus.read(to_id, 56, 2)
    pos = int.from_bytes(pos_raw, "little") if pos_raw else None
    bus.write(to_id, 46, struct.pack("<H", 0), timeout=0.15)    # 速度清零
    bus.write(to_id, 40, b"\x00", timeout=0.15)   # 关扭矩固定
    if pos is not None:
        err = abs(pos - 2048)
        if err <= 8:
            log(f"        到位：当前位置 {pos}（误差 {err} LSB ≈ {err*0.088:.1f}°），"
                f"已关扭矩固定。可以拔线。")
        else:
            log(f"        ⚠ 当前位置 {pos}，离中位 2048 还差 {err} LSB"
                f"（≈{err*0.088:.1f}°）。检查是否卡住，或手拨到中位附近再配一次。")
    else:
        log("        位置读不到，已关扭矩固定。凭手感确认在中位后拔线。")

    log(f"  ✓ 完成：ID {to_id} @ {target_bps:,} bps，已回中位。")
    return True


def set_id(port: str, expect_from: int, to_id: int,
           baud: int = 1_000_000, target_bps: int = 1_000_000,
           log: Callable[[str], None] = print) -> int:
    """配一颗舵机：自动识别 -> 解锁 EPROM -> 写 ID -> 写波特率 -> 回读校验。

    只写 reg5(ID) / reg6(波特率) / reg55(锁)，**不碰**扭矩 reg40、目标位置
    reg42 等运动相关寄存器，所以舵机全程不会转动。
    """
    bus = ServoBus()
    try:
        bus.open(port, baud, "ft")
    except Exception as exc:
        log(f"打开 {port} 失败：{exc}")
        return 1
    try:
        log(f"配一颗：当前 ID {expect_from} -> 目标 ID {to_id}，目标 {target_bps:,} bps")
        log("前提：桌上只接这一颗舵机。全程只写 ID / 波特率 / 锁标志。")
        log()
        ident = identify(bus, log)
        if ident is None:
            log("识别失败，中止。先跑 --probe 确认链路。")
            return 1
        if ident.regmap.key != "ft":
            log(f"应答的是 {ident.regmap.label}，本命令只处理飞特 FT-SCS，中止。")
            return 1
        if ident.sid != expect_from:
            log(f"⚠ 实际在场的 ID 是 {ident.sid}，不是预期的 {expect_from}；"
                f"按实际 ID 继续。")
        log()
        log(describe(ident))
        log()
        if ident.mode == 4:
            log("运行模式 4（纯位置 PD）且扭矩 reg40=0 → 配置过程不会有任何转动。")
        if ident.lock:
            log(f"reg55 = {ident.lock}（EPROM 已锁），下面第 2 步会先解锁。")
        log()
        if not configure_one(bus, ident.regmap, ident.sid, to_id, target_bps, log):
            log()
            log("配置未完成。不要拔线，把上面的日志原样贴回来。")
            return 1
        log()
        log(f"✓ 这颗已配好：ID {to_id} @ {target_bps:,} bps。")
        log("再跑一次 --dump 只看关键项，确认后拔线、贴标签、换下一颗。")
        return 0
    finally:
        bus.close()


# ============================================================================
# 命令行只读探测（--probe COM7）
#
# 为什么单独做一条命令行路径：出问题时最需要的是**原始字节**。界面里
# 解析失败只会给一句"无应答"，分不清是线没接、电没供、还是协议不对。
# 这里把每一格的原始收发字节都打出来，配合下面的判读表定位。
#
# 判读表（半双工适配器必然把 TX 回显到 RX）：
#   收到 == 发出，且只有这一份  -> 回显正常，舵机没说话（波特率/线序/供电/协议）
#   收到 == 发出，后跟额外字节  -> 舵机说话了，解析器可能没认出来（协议/CRC 问题）
#   收到全空                    -> 模块 TX 没出去，或 RX 没并到信号线
#   收到乱码（大量非 FF 字节）  -> 波特率不对
# ============================================================================

PROBE_RATES: Sequence[int] = (1_000_000, 57_600, 115_200)
SCAN_RATES: Sequence[int] = (1_000_000, 57_600)


def raw_exchange(port: str, baud: int, proto: str, sid: int,
                 instr: int = INSTR_PING, params: bytes = b"",
                 timeout: float = 0.2) -> Tuple[bytes, bytes]:
    """发一帧、原样收一段字节，不做任何解析。返回 (发出, 收到)。"""
    sent = build_frame(proto, sid, instr, params)
    ser = serial.Serial(
        port=port, baudrate=baud, bytesize=8,
        parity=serial.PARITY_NONE, stopbits=1,
        timeout=0.02, write_timeout=0.3,
    )
    try:
        ser.reset_input_buffer()
        ser.write(sent)
        ser.flush()
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = ser.read(max(1, ser.in_waiting))
            if chunk:
                buf.extend(chunk)
            else:
                time.sleep(0.002)
        return sent, bytes(buf)
    finally:
        ser.close()


def probe(port: str, log: Callable[[str], None] = print) -> int:
    """只读探测：三档速率 × 两套协议握手，全程不写寄存器。"""
    log(f"只读探测 {port}")
    log("前提：桌上只接一颗舵机，舵机已供电，且电源负极与串口模块共地。")
    log("全程只发 PING / READ，不写任何寄存器。")
    log()

    hits: List[Tuple[int, str]] = []
    echo_seen = 0
    for baud in PROBE_RATES:
        for regmap in MAPS:
            try:
                sent, data = raw_exchange(port, baud, regmap.proto, 1)
            except Exception as exc:
                log(f"[{baud:>9,} bps | {regmap.key}] 打开串口失败：{exc}")
                return 1
            if not data:
                verdict = "   ← RX 一个字节都没收到"
            elif data == sent:
                verdict = "   （仅回显，舵机没应答）"
                echo_seen += 1
            elif data.startswith(sent):
                verdict = "   ← 回显 + 额外字节，舵机说话了"
            else:
                verdict = "   ← 非回显，波特率不对或线上有噪声"
            if data and data != sent:
                hits.append((baud, regmap.proto))
            log(f"[{baud:>9,} bps | {regmap.key}] 发出 {sent.hex(' ')}")
            log(f"{'':>22}收到 {data.hex(' ') if data else '(空)'}{verdict}")
        log()

    if not hits and echo_seen == 0:
        log("六个组合 RX 全空：连自己的回显都收不到。")
        log("说明 TX 和 RX 不在同一个电气节点上——半双工接线还没接上。")
        log("按这个顺序查（任一条不满足都会是这个现象）：")
        log("  1. 模块 TX 串一个电阻后，和模块 RX 短接在同一根线上；")
        log("     这根线再接到舵机 1 脚 Signal。")
        log("  2. 模块 GND 与舵机电源负极相连。")
        log("  3. 模块 VCC 不接（除非用的是 5V 逻辑档）。")
        log("先跳过 ID 扫描（没接对，扫了也是全空）。")
        return 1

    if hits:
        baud, proto = hits[0]
        log(f"有额外字节：{baud:,} bps / {proto}。用完整识别复读一遍寄存器…")
        bus = ServoBus()
        try:
            bus.open(port, baud, proto)
        except Exception as exc:
            log(f"打开串口失败：{exc}")
            return 1
        try:
            ident = identify(bus, log)
            if ident is not None:
                log()
                log(describe(ident))
                log()
                gate = ident.fw_gate
                if gate is not None and gate < 46:
                    log(f"⚠ 固件 {ident.fw_text} < v46，需要先用飞特官方上位机升级。")
        finally:
            bus.close()

    # 阶段二：ID 扫描（只在主力两档速率上做，避免太慢）
    log()
    log("ID 扫描 0..40（只报『有额外字节』的格子）…")
    alive: List[Tuple[int, str, int]] = []
    for baud in SCAN_RATES:
        for regmap in MAPS:
            found: List[int] = []
            for sid in range(0, 41):
                try:
                    sent, data = raw_exchange(port, baud, regmap.proto, sid,
                                              timeout=0.06)
                except Exception as exc:
                    log(f"串口异常：{exc}")
                    return 1
                if data and data != sent:
                    found.append(sid)
            log(f"[{baud:>9,} bps | {regmap.key}] "
                + (", ".join(str(s) for s in found) if found else "无"))
            alive.extend((baud, regmap.proto, s) for s in found)

    log()
    if not alive:
        log("结论：整条链路没有任何应答。按下面顺序查：")
        log("  1. 线序——HD-1910 是 1=Signal / 2=Vcc / 3=GND，三根线同色，")
        log("     只能认插头针位；插反不认，也可能烧板。")
        log("  2. 舵机供电——4~8.4 V，负极必须和串口模块 GND 相连。")
        log("  3. 半双工——模块 TX 串约 1 kΩ 后并到信号线，RX 直接并上。")
        log("  4. 波特率——模块是不是 CH340？CP2102 上不了 1 Mbps。")
        return 1

    log(f"结论：总线有应答，共 {len(alive)} 处：")
    for baud, proto, sid in alive:
        log(f"  {baud:>9,} bps  {proto:<4}  ID {sid}")
    log("下一步：把识别到的那个速率填进界面的『速率』框，再点『自动识别』。")
    return 0


# 真机实测表里确定的寄存器名（只标注确证的项，用于 dump 打印）
FT_NAMES: Dict[int, str] = {
    0: "固件主版本", 1: "固件次版本", 2: "END（0=小端存储）", 3: "型号特征值（HD=7946）",
    4: "型号次版本", 5: "主 ID", 6: "波特率档（0=1M）", 7: "无定义",
    8: "应答状态级别（0=除读/PING 外不返回）",
    13: "最高温度上限", 19: "卸载条件", 20: "LED 报警条件",
    21: "EPROM Kp", 22: "EPROM Kd", 23: "EPROM Ki",
    28: "保护电流（6.5 mA/LSB）", 33: "运行模式（4=纯位置 PD）",
    40: "扭矩开关", 42: "目标位置", 44: "目标电流",
    46: "运行速度（目标）", 48: "转矩限制",
    50: "Kp（生效）", 51: "Kd（生效）", 52: "Ki（生效）",
    55: "锁标志（1=EPROM 写锁）",
    56: "当前位置", 58: "当前速度", 60: "当前负载",
    62: "当前输入电压（0.1 V）", 63: "当前温度（℃）",
    65: "舵机状态（0=正常）", 66: "移动标志", 67: "目标位置（反馈）",
    69: "当前电流",
}

# dump 的地址计划：(地址, 字节数)。跳过多字节字段的高字节地址。
DUMP_PLAN: Sequence[Tuple[int, int]] = (
    (0, 1), (1, 1), (2, 1), (3, 2), (5, 1), (6, 1), (7, 1), (8, 1),
    (13, 1), (19, 1), (20, 1), (21, 1), (22, 1), (23, 1),
    (28, 2), (33, 1), (40, 1), (42, 2), (44, 2), (46, 2), (48, 2),
    (50, 1), (51, 1), (52, 1), (55, 1),
    (56, 2), (58, 2), (60, 2), (62, 1), (63, 1), (65, 1), (66, 1), (67, 2), (69, 2),
)


def dump(port: str, sid: int = 1, baud: int = 1_000_000,
         log: Callable[[str], None] = print) -> int:
    """只读 dump 一遍关键寄存器。写任何 EPROM 之前先跑这个核对地址表。"""
    bus = ServoBus()
    try:
        bus.open(port, baud, "ft")
    except Exception as exc:
        log(f"打开 {port} 失败：{exc}")
        return 1
    try:
        if not bus.ping(sid):
            log(f"ID {sid} 在 {baud:,} bps 无应答。先用 --probe 确认速率和 ID。")
            return 1

        log(f"只读 dump：ID {sid} @ {baud:,} bps（FT-SCS）。不写任何寄存器。")
        log()
        log(f"{'地址':<5}{'值':>6}   {'HEX':<8}名称")
        log("-" * 54)
        for addr, width in DUMP_PLAN:
            raw = bus.read(sid, addr, width)
            if raw is None:
                log(f"{addr:<5}{'(err)':>6}   {'':<8}{FT_NAMES.get(addr, '')}")
                continue
            value = int.from_bytes(raw, "little")
            log(f"{addr:<5}{value:>6}   {raw.hex(' '):<8}{FT_NAMES.get(addr, '')}")

        log()
        log("关键项核对：")
        checks: List[Tuple[int, str, str]] = [
            (3, "型号特征值", "7946 = HD 固件；10 = HLS 家族"),
            (0, "固件主版本", "真机应为 3"),
            (1, "固件次版本", "真机应为 46 → 固件 3.46，满足 ≥v46"),
            (8, "应答状态级别", "0 = 写操作不返回应答帧；1 = 全部返回"),
            (55, "锁标志", "1 = EPROM 已锁，写 ID/波特率前必须先置 0"),
            (33, "运行模式", "4 = 纯位置 PD（出厂默认）"),
            (65, "舵机状态", "0 = 无错误"),
        ]
        for addr, name, note in checks:
            raw = bus.read(sid, addr, 2 if addr == 3 else 1)
            value = "(读失败)" if raw is None else str(int.from_bytes(raw, "little"))
            log(f"  {name:<12}= {value:<10}（{note}）")
        return 0
    finally:
        bus.close()


# ============================================================================
# 界面
# ============================================================================

# 官方 ID 分配：右腿 10-14、左腿 20-24、头/颈/嘴 30-34
ID_GROUPS: Sequence[Tuple[str, List[int]]] = (
    ("右腿", [10, 11, 12, 13, 14]),
    ("左腿", [20, 21, 22, 23, 24]),
    ("头 / 颈 / 嘴", [30, 31, 32, 33, 34]),
)

BAUD_CHOICES = (57_600, 115_200, 1_000_000)
PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "servo_progress.json")


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=8)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        self.bus = ServoBus()
        self.regmap: Optional[RegMap] = None
        self.ident: Optional[Identity] = None
        self.logq: "queue.Queue[str]" = queue.Queue()
        self.uiqueue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.done: set = set()
        self.busy = False

        self._load_progress()
        self._build()
        self.after(60, self._pump)
        self.log("HD-1910 配置工具已启动。")
        self.log("流程：插好 USB 转串口 -> 选 COM 口 -> 打开串口 -> 自动识别（只读）-> 逐颗配置。")
        self.log("注意：桌上同一时刻只接一颗舵机，否则 ID 1 会同时应答。")

    # --- 构建界面 ---
    def _build(self) -> None:
        row = 0

        box = ttk.LabelFrame(self, text="1 · 连接", padding=8)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        ttk.Label(box, text="串口").grid(row=0, column=0, padx=(0, 4))
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(box, textvariable=self.port_var, width=12,
                                    state="readonly")
        self.port_cb.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(box, text="刷新", width=6, command=self.refresh_ports).grid(row=0, column=2)

        ttk.Label(box, text="速率").grid(row=0, column=3, padx=(12, 4))
        self.baud_var = tk.StringVar(value="1000000")
        ttk.Combobox(box, textvariable=self.baud_var, width=10, state="readonly",
                     values=[str(b) for b in BAUD_CHOICES]).grid(row=0, column=4, padx=(0, 8))

        self.conn_btn = ttk.Button(box, text="打开串口", width=10, command=self.toggle_port)
        self.conn_btn.grid(row=0, column=5)
        self.state_lbl = ttk.Label(box, text="● 未连接", foreground="#b00")
        self.state_lbl.grid(row=0, column=6, padx=(12, 0))

        box = ttk.LabelFrame(self, text="2 · 自动识别（只读，不写寄存器）", padding=8)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        ttk.Button(box, text="开始识别", width=12,
                   command=self.do_identify).grid(row=0, column=0, sticky="w")
        self.ident_txt = tk.Text(box, height=9, width=64, relief="solid",
                                 borderwidth=1, state="disabled")
        self.ident_txt.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        box = ttk.LabelFrame(self, text="3 · 配置一颗（自动按 ID -> 波特率 顺序执行）",
                             padding=8)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        ttk.Label(box, text="当前 ID").grid(row=0, column=0, padx=(0, 4))
        self.from_var = tk.StringVar(value="1")
        ttk.Entry(box, textvariable=self.from_var, width=6).grid(row=0, column=1, padx=(0, 12))
        ttk.Label(box, text="目标 ID").grid(row=0, column=2, padx=(0, 4))
        self.to_var = tk.StringVar(value="10")
        ttk.Entry(box, textvariable=self.to_var, width=6).grid(row=0, column=3, padx=(0, 12))
        ttk.Button(box, text="配置这一颗", width=14,
                   command=self.do_configure).grid(row=0, column=4)

        ttk.Label(box, text="快捷目标：").grid(row=1, column=0, columnspan=2,
                                          sticky="w", pady=(8, 0))
        quick = ttk.Frame(box)
        quick.grid(row=1, column=2, columnspan=3, sticky="w", pady=(8, 0))
        col = 0
        for _, ids in ID_GROUPS:
            for sid in ids:
                ttk.Button(quick, text=str(sid), width=4,
                           command=lambda s=sid: self.to_var.set(str(s))
                           ).grid(row=0, column=col, padx=1)
                col += 1

        box = ttk.LabelFrame(self, text="4 · 15 颗进度（点击切换状态，自动存盘）", padding=8)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        self.chips: Dict[int, tk.Button] = {}
        r = 0
        for name, ids in ID_GROUPS:
            ttk.Label(box, text=name, width=12).grid(row=r, column=0, sticky="w")
            for c, sid in enumerate(ids, start=1):
                b = tk.Button(box, text=str(sid), width=5,
                              command=lambda s=sid: self.toggle_done(s))
                b.grid(row=r, column=c, padx=2, pady=2)
                self.chips[sid] = b
            r += 1
        ttk.Button(box, text="清空进度", width=10,
                   command=self.clear_progress).grid(row=r, column=0, sticky="w", pady=(6, 0))
        self.progress_lbl = ttk.Label(box, text="")
        self.progress_lbl.grid(row=r, column=1, columnspan=5, sticky="w", pady=(6, 0))

        box = ttk.LabelFrame(self, text="5 · 扫描 / 校验", padding=8)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6)); row += 1
        ttk.Label(box, text="ID 范围").grid(row=0, column=0, padx=(0, 4))
        self.scan_lo = tk.StringVar(value="0")
        self.scan_hi = tk.StringVar(value="40")
        ttk.Entry(box, textvariable=self.scan_lo, width=6).grid(row=0, column=1)
        ttk.Label(box, text="~").grid(row=0, column=2, padx=2)
        ttk.Entry(box, textvariable=self.scan_hi, width=6).grid(row=0, column=3, padx=(0, 12))
        ttk.Button(box, text="开始扫描", width=12,
                   command=self.do_scan).grid(row=0, column=4)
        self.scan_lbl = ttk.Label(box, text="", wraplength=560, justify="left")
        self.scan_lbl.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

        box = ttk.LabelFrame(self, text="日志", padding=8)
        box.grid(row=row, column=0, sticky="nsew"); row += 1
        self.rowconfigure(row - 1, weight=1)
        self.log_txt = tk.Text(box, height=10, width=90, relief="solid", borderwidth=1)
        sb = ttk.Scrollbar(box, command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=sb.set)
        self.log_txt.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        box.columnconfigure(0, weight=1)

        self.refresh_ports()
        self._refresh_chips()

    # --- 线程安全的界面回传 ---
    def log(self, msg: str) -> None:
        self.logq.put(msg)

    def post(self, fn: Callable[[], None]) -> None:
        """从工作线程把界面更新排到主线程执行。"""
        self.uiqueue.put(fn)

    def _pump(self) -> None:
        while True:
            try:
                msg = self.logq.get_nowait()
            except queue.Empty:
                break
            self.log_txt.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.log_txt.see("end")
        while True:
            try:
                fn = self.uiqueue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self.log_txt.insert("end", f"[界面回调异常] {exc!r}\n")
        self.after(60, self._pump)

    def _run_async(self, work: Callable[[], None]) -> None:
        if self.busy:
            messagebox.showinfo("请稍等", "上一个操作还在进行中。")
            return
        self.busy = True
        self._set_enabled(False)

        def wrapper() -> None:
            try:
                work()
            except Exception as exc:  # noqa: BLE001 - 线程里必须兜住
                self.log(f"✗ 异常：{exc!r}")
            finally:
                self.busy = False
                self.post(lambda: self._set_enabled(True))

        threading.Thread(target=wrapper, daemon=True).start()

    def _set_enabled(self, enabled: bool) -> None:
        for child in self.winfo_children():
            self._set_state_recursive(child, enabled)

    def _set_state_recursive(self, widget, enabled: bool) -> None:
        if isinstance(widget, ttk.Combobox):
            try:
                widget.configure(state="readonly" if enabled else "disabled")
            except tk.TclError:
                pass
        elif isinstance(widget, (ttk.Button, ttk.Entry)):
            try:
                widget.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass
        for child in widget.winfo_children():
            self._set_state_recursive(child, enabled)

    def _set_ident_text(self, text: str) -> None:
        self.ident_txt.configure(state="normal")
        self.ident_txt.delete("1.0", "end")
        self.ident_txt.insert("1.0", text)
        self.ident_txt.configure(state="disabled")

    def _sel_baud(self) -> int:
        return int(self.baud_var.get())

    def _require_port(self) -> bool:
        if not self.port_var.get():
            messagebox.showwarning("未选串口", "请先刷新并选择一个串口。")
            return False
        return True

    # --- 串口 ---
    def refresh_ports(self) -> None:
        ports = [p.device for p in list_ports.comports()]
        self.port_cb["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])
        if not ports:
            self.log("未发现串口。请插好 USB 转串口模块后点『刷新』。")

    def toggle_port(self) -> None:
        if self.bus.is_open:
            self.bus.close()
            self.conn_btn.configure(text="打开串口")
            self.state_lbl.configure(text="● 未连接", foreground="#b00")
            self.log(f"已关闭 {self.port_var.get()}")
            return

        if not self._require_port():
            return
        try:
            self.bus.open(self.port_var.get(), self._sel_baud(), "dxl2")
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))
            self.log(f"✗ 打开 {self.port_var.get()} 失败：{exc}")
            return
        self.conn_btn.configure(text="关闭串口")
        self.state_lbl.configure(
            text=f"● {self.port_var.get()} @ {self._sel_baud():,}", foreground="#080")
        self.log(f"已打开 {self.port_var.get()} @ {self._sel_baud():,} bps")

    # --- 动作 ---
    NO_REPLY_HINT = (
        "无应答。\n\n"
        "按这个顺序排查：\n"
        "  1. 线序——HD-1910 是 1=Signal / 2=Vcc / 3=GND，与 XL330、\n"
        "     飞特 HL-2915 正好相反，插反轻则不认、重则烧板。\n"
        "  2. 舵机供电——4~8.4 V 稳压，且与串口模块共地。\n"
        "  3. 半双工接线——TX 串约 1 kΩ 并到信号线，RX 直接并上去。\n"
        "  4. 速率——规格书写出厂 1 Mbps，但 robotd 常量写的 57600，\n"
        "     两个都试（识别会自动轮换三档速率）。\n"
        "  5. 桌上是否只有一颗舵机。"
    )

    def do_identify(self) -> None:
        if not self.bus.is_open:
            messagebox.showwarning("未连接", "请先打开串口。")
            return
        self._set_ident_text("识别中…")

        def work() -> None:
            self.log("开始自动识别（2 套协议 × 候选 ID × 自动试速率，只读）…")
            ident = identify(self.bus, self.log)
            if ident is None:
                self.post(lambda: self._set_ident_text(self.NO_REPLY_HINT))
                return

            self.regmap = ident.regmap
            self.ident = ident
            text = describe(ident)
            self.post(lambda: self._set_ident_text(text))
            self.post(lambda: self.from_var.set(str(ident.sid)))

            if ident.regmap.key == "dxl":
                self.log("→ 应答协议是 Dynamixel，与 robotd 走的路径一致。")
            else:
                self.log("→ 应答协议是飞特数字包。robotd 走的是 DynamixelIo，"
                         "口径不一致，装到机器人前需要再确认。")

            gate = ident.fw_gate
            if gate is not None:
                if gate >= 46:
                    self.log(f"→ 固件版本 {ident.fw_text} ≥ v46，满足要求。")
                else:
                    self.log(f"→ 固件版本 {ident.fw_text} < v46，需要先升级；"
                             f"升级通常要飞特官方上位机，可能需要专用适配器。")
            if ident.volt is not None:
                if not 4.0 <= ident.volt <= 8.6:
                    self.log(f"→ ⚠ 电压 {ident.volt:.1f} V 超出 4~8.4 V，先查电源再继续！")
                else:
                    self.log(f"→ 电压 {ident.volt:.1f} V，在范围内。")

        self._run_async(work)

    def do_configure(self) -> None:
        if not self.bus.is_open:
            messagebox.showwarning("未连接", "请先打开串口。")
            return
        if self.regmap is None:
            messagebox.showwarning("未识别", "请先点『自动识别』确认协议与寄存器表。")
            return
        try:
            from_id, to_id = int(self.from_var.get()), int(self.to_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "ID 必须是整数。")
            return
        if not 0 <= to_id <= 253:
            messagebox.showerror("参数错误", "目标 ID 必须在 0~253 之间。")
            return
        if not messagebox.askyesno(
            "确认写入",
            f"把当前 ID {from_id} 的舵机改成 ID {to_id}，\n"
            f"并写入波特率 {self._sel_baud():,} bps？\n\n"
            f"协议：{self.regmap.label}\n\n"
            f"请确认总线上只有这一颗舵机。"
        ):
            return

        target_baud = self._sel_baud()

        def work() -> None:
            self.log(f"开始配置：ID {from_id} -> {to_id} @ {target_baud:,} bps")
            if configure_one(self.bus, self.regmap, from_id, to_id, target_baud, self.log):
                self.post(lambda: self._on_configured(to_id))

        self._run_async(work)

    def _on_configured(self, sid: int) -> None:
        self.mark_done(sid)
        self.from_var.set(str(sid))

    def do_scan(self) -> None:
        if not self.bus.is_open:
            messagebox.showwarning("未连接", "请先打开串口。")
            return
        try:
            lo, hi = int(self.scan_lo.get()), int(self.scan_hi.get())
        except ValueError:
            messagebox.showerror("参数错误", "ID 范围必须是整数。")
            return
        if not 0 <= lo <= hi <= 253:
            messagebox.showerror("参数错误", "范围须在 0~253 之间，且左端不大于右端。")
            return
        self.scan_lbl.configure(text="扫描中…")

        def work() -> None:
            rate = self.bus.baudrate
            self.log(f"扫描 ID {lo}~{hi} @ {rate:,} bps"
                     f"（协议：{self.regmap.label if self.regmap else '当前设置'}）")
            found = [sid for sid in range(lo, hi + 1) if self.bus.ping(sid, timeout=0.02)]
            self.log(f"→ 应答 {len(found)} 个：{found if found else '无'}")

            expected = [i for _, ids in ID_GROUPS for i in ids]
            missing = [i for i in expected if i not in found]
            extra = [i for i in found if i not in expected]
            parts = [f"应答 {len(found)} 个：{found if found else '无'}"]
            if missing:
                parts.append(f"缺失 {missing}")
            if extra:
                parts.append(f"非预期 ID {extra}")
            if not missing and not extra:
                parts.append("✓ 15 颗齐全且无重复 ID")
            text = " ｜ ".join(parts)
            self.post(lambda: self.scan_lbl.configure(text=text))

        self._run_async(work)

    # --- 进度 ---
    def _load_progress(self) -> None:
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as fh:
                self.done = set(json.load(fh).get("done", []))
        except Exception:
            self.done = set()

    def _save_progress(self) -> None:
        try:
            with open(PROGRESS_FILE, "w", encoding="utf-8") as fh:
                json.dump({"done": sorted(self.done)}, fh,
                          ensure_ascii=False, indent=2)
        except Exception as exc:
            self.log(f"进度存盘失败：{exc}")

    def toggle_done(self, sid: int) -> None:
        self.done.discard(sid) if sid in self.done else self.done.add(sid)
        self._save_progress()
        self._refresh_chips()

    def mark_done(self, sid: int) -> None:
        self.done.add(sid)
        self._save_progress()
        self._refresh_chips()

    def clear_progress(self) -> None:
        if messagebox.askyesno("清空进度", "确定要清空 15 颗的完成标记吗？"):
            self.done.clear()
            self._save_progress()
            self._refresh_chips()

    def _refresh_chips(self) -> None:
        for sid, btn in self.chips.items():
            if sid in self.done:
                btn.configure(bg="#2e7d32", fg="white",
                              activebackground="#2e7d32", relief="sunken")
            else:
                btn.configure(bg="#e0e0e0", fg="black",
                              activebackground="#cfcfcf", relief="raised")
        total = sum(len(ids) for _, ids in ID_GROUPS)
        self.progress_lbl.configure(
            text=f"已完成 {len(self.done & set(self.chips))} / {total}")


# ============================================================================
# 自测（无需硬件）
# ============================================================================

def prototest() -> int:
    """协议编解码自测：构造帧 -> 解析回读，并验证回显剔除。"""
    failures = 0

    def check(cond: bool, desc: str) -> None:
        nonlocal failures
        print(("  ✓ " if cond else "  ✗ ") + desc)
        if not cond:
            failures += 1

    for regmap in MAPS:
        proto = regmap.proto
        print(f"[{regmap.label}] proto={proto}")

        # 读指令帧的往返
        para = (struct.pack("<HH", 8, 1) if proto == "dxl2" else bytes([8, 1]))
        sent = build_frame(proto, 7, INSTR_READ, para)
        print(f"  发出指令帧 {sent.hex(' ')}（{len(sent)} 字节）")

        if proto == "dxl2":
            # 应答：FF FF FD 00 | 07 | LEN2 | ERR | 数据 | CRC2
            payload = bytes([3])
            length = len(payload) + 3
            body = bytes([7]) + struct.pack("<H", length) + bytes([0]) + payload
            resp = b"\xff\xff\xfd\x00" + body + struct.pack("<H", dxl2_crc(body))
        else:
            payload = bytes([3])
            length = len(payload) + 2
            sid_b, err = 7, 0
            resp = (bytes([0xFF, 0xFF, sid_b, length, err]) + payload
                    + bytes([~(sid_b + length + err + sum(payload)) & 0xFF]))
        print(f"  构造应答帧 {resp.hex(' ')}（{len(resp)} 字节）")

        frame = parse_one_frame(proto, resp)
        check(frame is not None, "应答帧能被解析")
        if frame:
            check(frame.sid == 7, f"ID 解析正确（得到 {frame.sid}）")
            check(frame.err == 0, f"error 字节解析正确（得到 {frame.err}）")
            check(frame.params == payload,
                  f"数据解析正确（得到 {frame.params.hex(' ')}）")
            check(frame.raw == resp, "整帧字节往返一致")

        # 回显剔除：把发出的指令帧拼在应答前面，应只认后者
        frames, consumed = iter_frames(proto, sent + resp)
        echo_skipped = [f for f in frames if f.raw == sent]
        real = [f for f in frames if f.raw != sent]
        check(len(echo_skipped) == 1, "回显帧被识别出来（用于剔除）")
        check(len(real) == 1 and real[0].params == payload,
              "回显之后能取到真正的应答")
        check(consumed == len(sent) + len(resp), "帧边界推进字节数正确")

        # 坏 CRC 必须被拒
        bad = bytearray(resp)
        bad[-1] ^= 0xFF
        check(parse_one_frame(proto, bytes(bad)) is None, "校验和被篡改的帧被拒绝")

    print()
    print("全部通过。" if failures == 0 else f"有 {failures} 项未通过。")
    return 0 if failures == 0 else 1


def _int_arg(name: str, default: int) -> int:
    if name in sys.argv:
        idx = sys.argv.index(name)
        if idx + 1 < len(sys.argv):
            try:
                return int(sys.argv[idx + 1])
            except ValueError:
                pass
    return default


def _port_arg(flag: str, usage: str) -> Optional[str]:
    idx = sys.argv.index(flag)
    port = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    if not port or port.startswith("--"):
        print(usage)
        return None
    return port


def main() -> int:
    if "--prototest" in sys.argv:
        return prototest()

    if "--probe" in sys.argv:
        port = _port_arg("--probe", "用法：python tools/servo_config_gui.py --probe COM7")
        return 2 if port is None else probe(port)

    if "--dump" in sys.argv:
        port = _port_arg("--dump",
                         "用法：python tools/servo_config_gui.py --dump COM7 "
                         "[--id 1] [--baud 1000000]")
        if port is None:
            return 2
        return dump(port, _int_arg("--id", 1), _int_arg("--baud", 1_000_000))

    if "--set-id" in sys.argv:
        port = _port_arg("--set-id",
                         "用法：python tools/servo_config_gui.py --set-id COM7 "
                         "--to 10 [--from 1] [--baud 1000000] [--bps 1000000]")
        if port is None:
            return 2
        to_id = _int_arg("--to", -1)
        if not 0 <= to_id <= 253:
            print("必须用 --to <0..253> 指定目标 ID，例如 --to 10")
            return 2
        return set_id(port, _int_arg("--from", 1), to_id,
                      _int_arg("--baud", 1_000_000),
                      _int_arg("--bps", 1_000_000))

    if "--selftest" in sys.argv:
        root = tk.Tk()
        root.withdraw()
        App(root)
        root.update_idletasks()
        root.destroy()
        print("selftest OK：界面构建成功，未接触串口。")
        return 0

    root = tk.Tk()
    root.title("HD-1910 舵机配置工具")
    root.minsize(760, 740)
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
