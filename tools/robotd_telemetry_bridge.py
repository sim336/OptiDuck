#!/usr/bin/env python3
"""robotd telemetry 桥：把 robotd 的 robot.state 流搬到 TCP 上，喂给数字孪生。

v0.4（2026-09-30）：**反向通道多一条 `loadPolicy`**（面板要"起身策略"开关）
  · 面板的「起身策略」开关要能单独关掉 `sitstand` 槽位。robotd 有现成的运行时方法
    `robot.loadPolicy {slot, path}`，"关掉某槽位"就是把 `path` 写成字面量串 `"none"`
    （`params::is_none_sentinel`，和 `[policy] <slot> = "none"` 同一套）。**只有
    `walk` 不能关** —— robotd 明确拒绝："它是所有其它槽位的兜底"
  · 于是这里不用碰 robotd 源码、也不用改 TOML：加一条白名单项即可
  · ⚠ robotd 拒这条的**唯一**情况是它自己**没加载策略**（`[policy] enabled = false`，
    也就是以 `--no-policy` 起）：实测回 `policies are disabled on this robot; set
    [policy] enabled = true first`。**注意不是 `robot.enable off`** —— 那条闸关的是
    "现在有没有在驱动机器人"（实测 `enable off` 之后再发 loadPolicy 照样 accepted），
    配置层的 enabled 没变
  · ⚠ 槽位切换是**在 home 位姿时才发生**的，ack 只说 accepted，真结果要看
    `robot.policies`

v0.3（2026-09-30）：**health 里把 `imu` 也带上来**（面板要能证明 IMU 是真值）
  · 背景：`FeetechIo` 的 IMU 走**独立串口**（`[bus] imu_port`，台架 `/dev/ttyUSB0`），
    `SerialCsv` 在主机侧跑 Mahony；但 `imu_port` 打不开时它**静默**退化成"假定直立静止"
    （main.rs 只打一行 error 日志）—— 之后 `quat` 恒 `[1,0,0,0]`，面板上看着一切正常
  · 而 `robot.health` 里本来就有权威判据 `imu: {ready, stale_blocks,
    consecutive_stale_blocks}`，以前被 `parse_health` 丢掉了。现在转发成
    `imu_ready` / `imu_stale` / `imu_stale_total`，面板据此把真值和假值分开显示

v0.2（2026-09-30）：
  · 新增**反向命令通道**（`--allow-write` 才打开，默认仍是纯只读桥）：客户端在同一
    条 TCP 上写一行 JSON 就是一条命令，桥用**第二条** unix 连接转发给 robotd 并回
    ack。白名单 + 量程校验 + 有限性校验，见下面「反向命令通道」一节
  · 客户端类 Client：两个线程都会往同一个 socket 写（帧与 ack），加锁避免交错

v0.1（2026-09-30）：robot.subscribe(robot.state) -> TCP 一行一帧 JSON（只读）

为什么需要它
-----------
`bench_mirror.py` 平时直接开 COM8 读舵机（只读镜像）。但真机由 robotd 驱动时
COM8 被 robotd 独占 —— 这是硬限制，不是配置问题，两个进程不能同时开同一个串口。

好在 robotd 已经把该给的数据推出来了：`robot.subscribe` 就是一条 50 Hz 的
`robot.state` 流（实测关节角、目标角、IMU、安全状态、循环频率）。只是它走的是
**unix socket + NDJSON**，Windows 侧连不上。这个桥只干一件事：

    robotd (unix socket, WSL) --NDJSON--> 本桥 --一帧一行 JSON--> TCP:8199
                                                          Windows 的 bench_mirror

用法（在 WSL 里跑，robotd 起在哪个 socket 上就指哪个）

    python3 /mnt/e/optiDuck/tools/robotd_telemetry_bridge.py \
        --socket /run/robotd.sock --listen 0.0.0.0:8199

Windows 侧（另一个终端）

    py tools\\bench_mirror.py --telemetry-host 127.0.0.1:8199 --viser-port 8081

连不通时先确认这几点：robotd 在跑（`robotctl health` 有回应）、socket 路径对、
WSL2 的 localhost 转发开着；实在不行用 WSL 的 IP（`hostname -I`）代替 127.0.0.1。

只读（默认）
------------
默认情况下桥只调用 `robot.subscribe` 和 `robot.health` 两个**纯读**的方法，不写任何
寄存器、不发任何运动指令、不开扭矩。TCP 只该 listen 在台架网段，不要往公网转发。

反向命令通道（`--allow-write` 才打开）
------------------------------------
孪生面板要"让真机也动一下"时，光有下行帧不够，得有往上的一条路。但 robotd 那条
订阅连接是**独占**的 —— 它订阅完之后就再也不读下一个请求（这是 robotd 的设计，
不是 bug），所以反过来发命令必须另开**第二条** unix 连接。

    bench_mirror --(TCP 命令行)--> 桥 --(第二条 unix 连接, 请求/应答)--> robotd

客户端往同一条 TCP 连接写一行 JSON 就是一条命令，桥回一行 `{"ack":true,...}`：

    {"cmd":"head","params":{"neck_pitch":0,"head_pitch":0.2,
                            "head_yaw":0.4,"head_roll":0}}
    {"cmd":"mouth","params":{"open":0.6}}
    {"cmd":"move","params":{"vx":0,"vy":0,"vyaw":0.5}}
    {"cmd":"pose","params":{"z":0,"roll":0,"pitch":0,"active":true}}
    {"cmd":"do","params":{"skill":"sit_toggle"}}
    {"cmd":"stop"} / {"cmd":"relax"} / {"cmd":"init"} / {"cmd":"enable","params":{"on":true}}
    {"cmd":"loadPolicy","params":{"slot":"sitstand","path":"none"}}   ← 关掉起身策略
    {"cmd":"ping"}                     ← 只回 pong，用来确认通道活着

应答：`{"ack":true,"cmd":"head","ok":true,"note":"accepted"}`。
`ok:false` 的 note 说明为什么没发出去（字段错、超限、robotd 拒绝、连不上 robotd）。

**这是能驱动真机的开关**，所以三道闸：① 桥必须带 `--allow-write` 启动（默认关）；
② 命令只在上面那张白名单里（不接受任意方法名，桥不是通用代理）；③ 数值要在物理
量程内且有限（NaN / 1e9 会被拒）。面板上还有一道 arm 开关与急停。

动了真机就意味着机器人可能摔 —— 台架试验请托住它、手放在电源开关上。

帧格式（一行一个 JSON 对象，键都短，50 Hz 下省带宽）

    t     robotd 自己的单调时间（秒）
    recv  桥收到这一帧的本地 perf_counter（用来算延迟/丢帧）
    j     [15] 实测关节角 rad，顺序与 duck-control JOINT_NAMES 一致
    tg    [15] 本拍下发的目标角 rad（已过 safety 限幅）
    quat  [w,x,y,z] 躯干->世界；robotd 没送 IMU 时是 null
    gyro  [3] rad/s，躯干系
    grav  [3] 世界"下"在躯干系里的表达（robotd 的 safety.gravity）
    hz/missed/policy/gain/fallen/limp  循环与安全状态
    bat/tmax/tmean/thot        robot.health 的电池与热（1 Hz，来自另一个方法）
    imu_ready                  robot.health 的 imu.ready —— **IMU 是不是真姿态**（v0.3）
    imu_stale/imu_stale_total  连续 / 累计的"读数没刷新"块数（v0.3）

为什么另起一个进程而不是改 robotd
--------------------------------
1) robotd 跑在 WSL 里，那份源码是带 FeetechIo 的分支，改它要重新编译、还要把
   Windows 仓库的改动同步过去；这个桥不用碰 robotd 一行代码。
2) robotd 是机器人上的守护进程，给它开一个常驻 TCP 监听口是**扩大攻击面**的决定，
   不该为了台架调试去改。桥是按需起、按需停的调试件。

测试（不需要 robotd）
--------------------
`--stdio` 模式从 stdin 读 robotd 的 NDJSON、往 stdout 写帧，用来在本机验证翻译层：

    py tools\\robotd_telemetry_bridge.py --stdio < sample_robot_state.ndjson

反向命令那张白名单与 ack 格式同理，`--cmd-stdio` 从 stdin 读命令行、往 stdout 写 ack：

    echo {"cmd":"head","params":{"neck_pitch":0,"head_pitch":0.2,"head_yaw":0.4,"head_roll":0}} | \\
        py tools\\robotd_telemetry_bridge.py --cmd-stdio
"""

from __future__ import annotations

import argparse
import json
import math
import select
import socket
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

NUM_JOINTS = 15
DEFAULT_SOCKET = "/run/robotd.sock"
DEFAULT_LISTEN = "0.0.0.0:8199"

# robotd 的 robot.state 就是 50 Hz 控制循环的频率；要它就是全速率，不抽帧。
SUBSCRIBE_HZ = 50
# 电池/温度在 robotd 里本来就只有 ~1 Hz（慢寄存器单独一次事务），跟着它走。
HEALTH_PERIOD_S = 1.0
# 连 robotd 的读超时。到点没数据不算掉线，只是这一拍没有（顺便用来发 health 请求）。
READ_TIMEOUT_S = 0.5
# 桥"掉线"的定义：这么久没收到任何一帧。robotd 50 Hz，1 s = 50 帧没来。
STALE_AFTER_S = 1.0


# ============================================================================
# 翻译层：robotd 的 robot.state 一个对象 -> 一帧
# ============================================================================

def _f3(v) -> Optional[List[float]]:
    if not v or len(v) != 3:
        return None
    try:
        return [round(float(x), 6) for x in v]
    except (TypeError, ValueError):
        return None


def _f4(v) -> Optional[List[float]]:
    if not v or len(v) != 4:
        return None
    try:
        return [round(float(x), 6) for x in v]
    except (TypeError, ValueError):
        return None


def _angles(v) -> List[float]:
    """关节数组 -> 定长 15 的浮点表。长度不对就丢（宁可报空，不要错位）。"""
    if not isinstance(v, (list, tuple)) or len(v) != NUM_JOINTS:
        return []
    try:
        return [round(float(x), 6) for x in v]
    except (TypeError, ValueError):
        return []


def translate(params: dict, t_recv: float) -> dict:
    """robot.state 的 params -> 一帧。纯函数，好测。"""
    safety = params.get("safety") or {}
    loop = params.get("loop") or {}
    imu = params.get("imu") or {}
    return {
        "t": round(float(params.get("t") or 0.0), 4),
        "recv": round(t_recv, 4),
        "policy": str(params.get("policy") or ""),
        "hz": round(float(loop.get("hz") or 0.0), 2),
        "missed": int(loop.get("missed") or 0),
        "fallen": bool(safety.get("fallen")),
        "limp": bool(safety.get("limp")),
        "gain": int(safety.get("gain") or 0),
        "quat": _f4(imu.get("quat")),
        "gyro": _f3(imu.get("gyro")),
        "grav": _f3(safety.get("gravity")),
        "j": _angles(params.get("joints")),
        "tg": _angles(params.get("targets")),
    }


def parse_health(result: dict) -> Dict[str, object]:
    """robot.health 的 result -> 几个扁平键，附在之后每一帧上。

    v0.3：**把 `imu` 也带出来**。`robot.health` 里有 `imu: {ready, stale_blocks,
    consecutive_stale_blocks}`，而它正是"robotd 到底有没有拿到真姿态"的唯一权威判据 ——
    `FeetechIo` 在 `imu_port` 打不开时会**静默**退化成"假定直立静止"（main.rs 只打一行
    `cannot open the IMU; the trunk will be assumed upright and still`），此后 `quat` 恒
    `[1,0,0,0]`、`gyro` 恒 0，面板看到的是一个**看起来很正常**的直立姿态。把 ready
    与 stale 转发上来，面板才有办法把"真值"和"假值"分开显示，而不是只有一条假数据。
    """
    out: Dict[str, object] = {}
    bat = result.get("battery") or {}
    motors = result.get("motors") or {}
    imu = result.get("imu") or {}
    if isinstance(bat, dict) and "volts" in bat:
        out["bat"] = round(float(bat["volts"]), 2)
    if isinstance(motors, dict):
        if "max_c" in motors:
            out["tmax"] = float(motors["max_c"])
        if "mean_c" in motors:
            out["tmean"] = float(motors["mean_c"])
        if "hottest" in motors:
            out["thot"] = str(motors["hottest"])
    if isinstance(imu, dict) and "ready" in imu:
        out["imu_ready"] = bool(imu["ready"])
        out["imu_stale"] = int(imu.get("consecutive_stale_blocks") or 0)
        out["imu_stale_total"] = int(imu.get("stale_blocks") or 0)
    return out


# ============================================================================
# 反向命令：白名单 + 量程校验（写路径的所有门都在这一个函数里）
# ============================================================================

# 短名 -> (robotd 方法, {字段: (下限, 上限) 或 None(字符串/布尔)})
#
# 只收这一张表里的方法名。桥**不是**通用代理：随手能发 `robot.rebootMotors`
# 或以后新增的任何方法的调试口，就是给机器人留了一个没有认证的遥控器。
CMD_TABLE: Dict[str, Tuple[str, Dict[str, Optional[Tuple[float, float]]]]] = {
    "head": ("robot.head", {"neck_pitch": (-1.571, 1.048),
                            "head_pitch": (-1.571, 1.571),
                            "head_yaw": (-2.968, 2.968),
                            "head_roll": (-0.437, 0.437)}),
    "mouth": ("robot.mouth", {"open": (0.0, 1.0)}),
    "move": ("robot.move", {"vx": (-0.6, 0.6), "vy": (-0.4, 0.4),
                            "vyaw": (-2.5, 2.5)}),
    # 训练范围就是这么小（z −0.025..+0.010 m、roll/pitch ±0.26 rad），超出去
    # robotd 不会拦、策略会拿到没见过的输入 —— 所以在桥上就拦掉。
    "pose": ("robot.pose", {"z": (-0.025, 0.010), "roll": (-0.26, 0.26),
                            "pitch": (-0.26, 0.26), "active": None}),
    "do": ("robot.do", {"skill": None}),
    "enable": ("robot.enable", {"on": None}),
    # v0.4：把某个槽位换成别的 .onnx，或写 "none" 关掉它（`walk` 除外，robotd 拒绝）。
    # 「起身策略」开关就是这条：path="none" 关、path=<那份 standup .onnx> 开。
    "loadPolicy": ("robot.loadPolicy", {"slot": None, "path": None}),
    "stop": ("robot.stop", {}),
    "init": ("robot.init", {}),
    "relax": ("robot.relax", {}),
}
MAX_SKILL_NAME = 32
# robotd `Slot::names()` 的全集。写死在这里而不是"把字符串透传"：槽位名打错时
# robotd 会拒（回 note），但先在桥上拦掉，面板看到的原因更短。
POLICY_SLOTS = ("walk", "stand", "sitstand", "ground_pick",
                "kick_left", "kick_right", "roulade")


def build_command(cmd: str, params: dict) -> Tuple[str, dict]:
    """客户端的一行 -> (robotd 方法, 校验过的 params)。不合法抛 ValueError。"""
    entry = CMD_TABLE.get(str(cmd))
    if entry is None:
        raise ValueError(f"不认识命令 {cmd!r}（只收 {'/'.join(CMD_TABLE)}）")
    method, fields = entry
    if not isinstance(params, dict):
        raise ValueError("params 必须是对象")
    unknown = set(params) - set(fields)
    if unknown:
        # robotd 那边是 deny_unknown_fields，多给一个键会整条被拒 —— 在这里先说是哪个
        raise ValueError(f"多余的字段 {sorted(unknown)}（只认 {sorted(fields)}）")
    out: dict = {}
    for key, lim in fields.items():
        if key not in params:
            raise ValueError(f"缺字段 {key}")
        v = params[key]
        if lim is None:
            if key == "skill":
                if not isinstance(v, str) or not v or len(v) > MAX_SKILL_NAME:
                    raise ValueError("skill 必须是非空短字符串")
                out[key] = v
            elif key == "active":
                if not isinstance(v, bool):
                    raise ValueError("active 必须是 true/false")
                out[key] = v
            elif key == "slot":         # loadPolicy.slot
                if v not in POLICY_SLOTS:
                    raise ValueError(f"slot 必须是 {list(POLICY_SLOTS)} 之一")
                out[key] = v
            elif key == "path":         # loadPolicy.path
                if not isinstance(v, str) or not (v == "none" or v.startswith("/")):
                    raise ValueError('path 必须是绝对路径，或 "none"（关掉该槽位）')
                out[key] = v
            else:                       # enable.on
                if not isinstance(v, bool):
                    raise ValueError("on 必须是 true/false")
                out[key] = v
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{key} 必须是数字")
        fv = float(v)
        if not math.isfinite(fv):
            raise ValueError(f"{key} 不是有限数（{v}）")
        lo, hi = lim
        if not (lo <= fv <= hi):
            raise ValueError(f"{key}={fv} 超出量程 [{lo}, {hi}]")
        out[key] = fv
    return method, out


class CmdLink:
    """到 robotd 的**第二条**连接：只做请求/应答，不订阅。

    与 RobotdLink 分开是必须的：robotd 一旦收到 robot.subscribe 就不再读那条
    连接的后续请求了（它把连接让给了状态流），所以命令走另一条。

    按需连接：第一条命令来了才连（也才需要 robotd 在跑）。断了下次命令再重连。
    """

    def __init__(self, path: str, timeout: float = 1.5) -> None:
        self.path = path
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.buf = bytearray()
        self._lock = threading.Lock()           # 客户端线程可能不止一个
        self.next_id = 1
        self.sent = 0
        self.failed = 0

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
            self.buf.clear()

    def _connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.path)
        self.sock = s
        self.buf.clear()

    def _read_reply(self, want_id: int) -> Tuple[bool, str]:
        """读到 want_id 的应答（别的行顺手丢掉）。返回 (ok, note)。"""
        assert self.sock is not None
        deadline = time.perf_counter() + self.timeout
        while time.perf_counter() < deadline:
            while b"\n" in self.buf:
                raw, _, rest = self.buf.partition(b"\n")
                self.buf = bytearray(rest)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict) or msg.get("id") != want_id:
                    continue
                err = msg.get("error")
                if err:
                    return False, f"robotd 拒绝：{err}"
                res = msg.get("result")
                if isinstance(res, dict) and "accepted" in res:
                    if res.get("accepted"):
                        return True, str(res.get("reason") or "accepted")
                    return False, f"robotd 未接受：{res.get('reason') or '未给理由'}"
                return True, "ok"
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as exc:
                self.close()
                return False, f"读 robotd 应答失败：{exc}"
            if not chunk:
                self.close()
                return False, "robotd 关掉了命令连接"
            self.buf.extend(chunk)
        return True, "已发出（robotd 没在 1.5 s 内应答，按已接受处理）"

    def call(self, method: str, params: dict) -> Tuple[bool, str]:
        """发一条请求并等应答。返回 (ok, note)。绝不抛异常 —— 调用方是网络线程。"""
        with self._lock:
            if self.sock is None:
                try:
                    self._connect()
                except OSError as exc:
                    self.failed += 1
                    return False, f"连不上 robotd {self.path}：{exc}"
                except AttributeError as exc:
                    # Windows 的 Python 没有 socket.AF_UNIX。桥正经跑在 WSL 里，这里
                    # 只为 `--cmd-stdio` 在本机验白名单时别把进程崩掉。
                    self.failed += 1
                    return False, f"本机不支持 unix socket（{exc}）：只能验命令格式"
            assert self.sock is not None
            mid = self.next_id
            self.next_id += 1
            line = json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                               "params": params}, separators=(",", ":")) + "\n"
            try:
                self.sock.sendall(line.encode())
            except OSError as exc:
                self.close()
                self.failed += 1
                return False, f"发给 robotd 失败：{exc}"
            self.sent += 1
            ok, note = self._read_reply(mid)
            if not ok:
                self.failed += 1
            return ok, note


# ============================================================================
# robotd 那条连接
# ============================================================================

class RobotdLink:
    """到 robotd unix socket 的连接：发 subscribe、收 robot.state / health 应答。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.sock: Optional[socket.socket] = None
        self.buf = bytearray()
        self.next_id = 1
        self.last_rx = 0.0
        self.frames = 0
        self.health: Dict[str, object] = {}
        self._health_due = time.perf_counter() + HEALTH_PERIOD_S
        self.note = ""                      # 最近一次失败的原因，给人看

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(READ_TIMEOUT_S)
        s.connect(self.path)
        self.sock = s
        self.buf.clear()
        self._send({"jsonrpc": "2.0", "id": self.next_id,
                    "method": "robot.subscribe", "params": {"hz": SUBSCRIBE_HZ}})
        self.next_id += 1
        # 连上就算"刚刚有数据"：第一帧通常 20 ms 内就到，别让 STALE 判定误报。
        self.last_rx = time.perf_counter()
        self._health_due = time.perf_counter() + HEALTH_PERIOD_S

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.buf.clear()

    @property
    def ok(self) -> bool:
        return self.sock is not None

    def _send(self, obj: dict) -> None:
        assert self.sock is not None
        self.sock.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def line(self) -> Optional[bytes]:
        """读一行。超时返回 None（顺便发 health 请求）；对端关闭抛 OSError。"""
        if self.sock is None:
            raise OSError("not connected")
        while b"\n" not in self.buf:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                self._maybe_ask_health()
                return None
            if not chunk:
                raise OSError("robotd closed the connection")
            self.buf.extend(chunk)
            if len(self.buf) > 1 << 20:      # 一直没换行的垃圾，别把缓冲堆爆
                raise OSError("no newline in 1 MiB")
        raw, _, rest = self.buf.partition(b"\n")
        self.buf = bytearray(rest)
        self.last_rx = time.perf_counter()
        return bytes(raw)

    def _maybe_ask_health(self) -> None:
        now = time.perf_counter()
        if now < self._health_due or self.sock is None:
            return
        self._health_due = now + HEALTH_PERIOD_S
        try:
            self._send({"jsonrpc": "2.0", "id": self.next_id,
                        "method": "robot.health", "params": {}})
            self.next_id += 1
        except OSError:
            pass


# ============================================================================
# 桥
# ============================================================================

class Client:
    """一个 TCP 客户端：帧往下推（pump 线程），命令行往上读（它自己的读线程）。

    两个线程都会往同一个 fd 写，所以每次写都过一把锁 —— 否则一帧 JSON 与一条
    ack 可能交错成两半，客户端解析出一堆坏帧。
    """

    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock = sock
        self.addr = addr
        self.lock = threading.Lock()
        self.dead = False

    def send(self, data: bytes) -> str:
        """'ok' / 'drop'（对端跟不上，丢这一帧）/ 'dead'（连接没了）。"""
        with self.lock:
            try:
                self.sock.sendall(data)
                return "ok"
            except (BlockingIOError, InterruptedError):
                return "drop"
            except OSError:
                self.dead = True
                return "dead"

    def close(self) -> None:
        self.dead = True
        try:
            self.sock.close()
        except OSError:
            pass


class Bridge:
    """把 robotd 的一帧翻成一行 JSON，广播给所有 TCP 客户端。

    订阅是**按需**的：有客户端才 subscribe、客户端走光就断开 robotd 连接。
    robotd 只在有订阅者时才组装 robot.state（它自己的设计），所以没人看的时候
    不该让它白白每拍分配一次 —— 桥连上就订阅会让这件事常态化。

    `allow_write=False`（默认）时桥是纯只读的：客户端发来的命令行一律回
    `ok:false`，一个字都不会转给 robotd。要驱动真机得显式 `--allow-write`。
    """

    def __init__(self, sock_path: str, listen: str, verbose: bool,
                 allow_write: bool = False) -> None:
        host, _, port = listen.rpartition(":")
        self.host = host or "0.0.0.0"
        self.port = int(port)
        self.link = RobotdLink(sock_path)
        self.verbose = verbose
        self.allow_write = allow_write
        self.cmd = CmdLink(sock_path)

        self._clients: List[Client] = []
        self._lock = threading.Lock()
        self._want = threading.Event()          # 有客户端 = 要订阅
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.sent = 0
        self.dropped = 0
        self.cmds = 0
        self._t_report = time.perf_counter()

    # ---- 对外 ----

    def serve_forever(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(4)
        print(f"桥已起：robotd {self.link.path} -> tcp://{self.host}:{self.port}",
              flush=True)
        print("  等 bench_mirror 连上来才会订阅 robotd（在此之前 robotd 不会"
              "为桥组装帧）", flush=True)
        print("  反向命令通道：" + ("**已打开**（客户端可以驱动真机）"
                                   if self.allow_write else
                                   "关闭（纯只读；要开加 --allow-write）"), flush=True)
        try:
            while True:
                conn, addr = srv.accept()
                conn.setblocking(False)         # 写不动就丢帧，绝不阻塞广播线程
                client = Client(conn, addr)
                with self._lock:
                    self._clients.append(client)
                    n = len(self._clients)
                print(f"[+] {addr[0]}:{addr[1]} 连上（{n} 个客户端）", flush=True)
                # 读线程：命令行 -> robotd 第二条连接 -> ack 回这个客户端
                threading.Thread(target=self._client_loop, args=(client,),
                                 name=f"client-{addr[1]}", daemon=True).start()
                if not self._want.is_set():
                    self._want.set()
                    if self._reader is None or not self._reader.is_alive():
                        self._reader = threading.Thread(
                            target=self._pump, name="robotd-pump", daemon=True)
                        self._reader.start()
        except KeyboardInterrupt:
            print("\n停止", flush=True)
        finally:
            self._stop.set()
            self._want.clear()
            self.link.close()
            self.cmd.close()
            with self._lock:
                for c in self._clients:
                    c.close()
                self._clients.clear()
            srv.close()

    # ---- 客户端 -> robotd（反向命令） ----

    def _client_loop(self, client: Client) -> None:
        """读这个客户端的命令行。socket 是非阻塞的，所以用 select 等（不占 CPU）。"""
        buf = bytearray()
        while not self._stop.is_set() and not client.dead:
            try:
                ready, _, _ = select.select([client.sock], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                chunk = client.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                break
            if not chunk:
                break                       # 客户端关了读半边：广播那边会收拾
            buf.extend(chunk)
            if len(buf) > (1 << 20):
                buf.clear()
                continue
            while b"\n" in buf:
                raw, _, rest = buf.partition(b"\n")
                buf = bytearray(rest)
                if raw.strip():
                    client.send(self._answer(raw).encode())
        client.dead = True

    def _answer(self, raw: bytes) -> str:
        """一行命令 -> 一行 ack。任何异常都变成 ok:false，不能把桥打挂。"""
        def reply(ok: bool, note: str, cmd: str = "") -> str:
            return json.dumps({"ack": True, "cmd": cmd, "ok": ok, "note": note},
                              separators=(",", ":"), ensure_ascii=False) + "\n"

        try:
            msg = json.loads(raw)
        except ValueError:
            return reply(False, "不是 JSON")
        if not isinstance(msg, dict):
            return reply(False, "命令行必须是 JSON 对象")
        cmd = str(msg.get("cmd") or "")
        if cmd == "ping":
            return reply(True, "pong（通道活着）" if self.allow_write
                         else "pong（通道开着，但桥是只读的：要动真机得加 --allow-write）",
                         cmd)
        if not self.allow_write:
            return reply(False, "桥是只读的（启动时没加 --allow-write）", cmd)
        try:
            method, params = build_command(cmd, msg.get("params") or {})
        except ValueError as exc:
            return reply(False, str(exc), cmd)
        ok, note = self.cmd.call(method, params)
        self.cmds += 1
        if self.verbose or not ok:
            print(f"[cmd] {cmd} -> {method} {'ok' if ok else 'NO'} · {note}",
                  file=sys.stderr, flush=True)
        return reply(ok, note, cmd)

    # ---- robotd -> 客户端 ----

    def _pump(self) -> None:
        """reader 线程：订阅 robotd，翻译，广播。robotd 断了就 1 s 一次重连。"""
        while self._want.is_set() and not self._stop.is_set():
            if not self.link.ok:
                try:
                    self.link.connect()
                    print(f"[robotd] 已连接 {self.link.path} 并订阅 robot.state "
                          f"@ {SUBSCRIBE_HZ} Hz", flush=True)
                except OSError as exc:
                    print(f"[robotd] 连不上 {self.link.path}：{exc}；1 s 后重试",
                          file=sys.stderr, flush=True)
                    time.sleep(1.0)
                    continue
            try:
                raw = self.link.line()
            except OSError as exc:
                print(f"[robotd] 链路断了：{exc}；1 s 后重连", file=sys.stderr, flush=True)
                self.link.close()
                time.sleep(1.0)
                continue
            if raw is None:
                if time.perf_counter() - self.link.last_rx > STALE_AFTER_S:
                    print(f"[robotd] {STALE_AFTER_S:.0f} s 没收到帧，按掉线处理",
                          file=sys.stderr, flush=True)
                    self.link.close()
                    time.sleep(1.0)
                continue
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue                        # 不是 JSON 的行（日志混进流里）直接丢
            self._handle(msg)

    def _handle(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "robot.state":
            self.link.frames += 1
            frame = translate(msg.get("params") or {}, time.perf_counter())
            frame.update(self.link.health)
            self._broadcast(json.dumps(frame, separators=(",", ":")) + "\n")
            self._report()
        elif method is None and "result" in msg:
            # health 的应答（subscribe 的应答没有内容，也不管）。
            result = msg.get("result")
            if isinstance(result, dict):
                got = parse_health(result)
                if got:
                    self.link.health.update(got)
        # 其余（robot.* 的其它通知）忽略：本桥只搬状态。

    def _broadcast(self, line: str) -> None:
        data = line.encode()
        dead: List[Client] = []
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            res = c.send(data)
            if res == "drop":
                self.dropped += 1               # 对端跟不上：丢这一帧，不影响别人
            elif res == "dead":
                dead.append(c)
        if dead:
            with self._lock:
                for c in dead:
                    if c in self._clients:
                        self._clients.remove(c)
                    c.close()
                left = len(self._clients)
            print(f"[-] {len(dead)} 个客户端断开（剩 {left} 个）", flush=True)
            if left == 0:
                # 没人看就退订：robotd 随之停止组装帧。
                self._want.clear()
                self.link.close()
                self.cmd.close()
                print("[robotd] 没有客户端了，已退订", flush=True)

    def _report(self) -> None:
        if not self.verbose:
            return
        now = time.perf_counter()
        span = now - self._t_report
        if span < 5.0:
            return
        print(f"[{self.link.frames} 帧] {self.link.frames / span:.1f} 帧/s"
              f"  丢 {self.dropped} 帧"
              + (f"  命令 {self.cmd.sent} 条（失败 {self.cmd.failed}）"
                 if self.allow_write else "")
              + (f"  电池 {self.link.health['bat']} V" if "bat" in self.link.health else ""),
              flush=True)
        self._t_report = now
        self.link.frames = 0
        self.dropped = 0


# ============================================================================
# stdio 模式：不碰 socket，用管道验证翻译层（本机就能测）
# ============================================================================

def run_stdio() -> int:
    """stdin 读 robotd 的 NDJSON -> stdout 写帧。health 应答就地吸收。"""
    health: Dict[str, object] = {}
    frames = 0
    for raw in sys.stdin.buffer:
        text = raw.strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except ValueError:
            continue
        method = msg.get("method")
        if method == "robot.state":
            frame = translate(msg.get("params") or {}, time.perf_counter())
            frame.update(health)
            sys.stdout.write(json.dumps(frame, separators=(",", ":")) + "\n")
            frames += 1
        elif method is None and isinstance(msg.get("result"), dict):
            health.update(parse_health(msg["result"]))
    sys.stdout.flush()
    print(f"[stdio] 输入结束，输出 {frames} 帧", file=sys.stderr)
    return 0


def run_cmd_stdio(sock_path: str, allow_write: bool = True) -> int:
    """stdin 读客户端命令行 -> stdout 写 ack。走的是真 `_answer`，只是 robotd 不在，
    所以通过校验的命令会回 `连不上 robotd` —— 用来验证白名单、量程校验与 ack 格式。

    `allow_write=False` 时（不加 `--allow-write`）测的是另一条路：闸门本身，
    也就是"桥是只读的"那条拒绝。
    """
    bridge = Bridge(sock_path, "127.0.0.1:9", False, allow_write=allow_write)
    n = 0
    for raw in sys.stdin.buffer:
        if raw.strip():
            sys.stdout.write(bridge._answer(raw.strip()))     # noqa: SLF001 — 就是它的冒烟口
            n += 1
    sys.stdout.flush()
    print(f"[cmd-stdio] 输入结束，应答 {n} 条（"
          f"反向通道 {'开' if allow_write else '关：一律拒'}）", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="robotd -> TCP telemetry 桥（默认只读；--allow-write 打开反向命令）")
    ap.add_argument("--socket", default=DEFAULT_SOCKET,
                    help=f"robotd 的 unix socket（默认 {DEFAULT_SOCKET}）")
    ap.add_argument("--listen", default=DEFAULT_LISTEN,
                    help=f"TCP 监听 地址:端口（默认 {DEFAULT_LISTEN}）")
    ap.add_argument("--allow-write", action="store_true",
                    help="打开反向命令通道：**客户端可以驱动真机**。默认关闭，"
                         "这时桥是纯只读的，命令行一律被拒")
    ap.add_argument("--stdio", action="store_true",
                    help="从 stdin 读 NDJSON、往 stdout 写帧（本机测翻译层，不用 robotd）")
    ap.add_argument("--cmd-stdio", action="store_true",
                    help="从 stdin 读命令行、往 stdout 写 ack（本机测白名单与校验；"
                         "不带 --allow-write 时测的是只读闸门）")
    ap.add_argument("--verbose", action="store_true", help="每 5 s 打一行速率")
    args = ap.parse_args()

    if args.stdio:
        return run_stdio()
    if args.cmd_stdio:
        return run_cmd_stdio(args.socket, args.allow_write)
    if args.allow_write:
        print("[!] 反向命令通道已打开 —— 这个桥现在能驱动真机。"
              "确认它没有暴露在公网，且机器人被托住。", file=sys.stderr, flush=True)
    Bridge(args.socket, args.listen, args.verbose,
           allow_write=args.allow_write).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())