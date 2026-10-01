#!/usr/bin/env python3
"""台架只读实时镜像 / 状态看板：真机 15 颗 HD-1910 + IMU 实时映到浏览器 3D。

v0.22（2026-09-30）：**两个策略开关**（起因：打开策略后腿自己走了）
  · 现象（用户实测）：按「robot.enable（打开策略）」之后，**没给任何速度指令，双腿
    就开始迈**。不是某个开关坏了，而是几件事叠在一起：
    - 头/颈的关节是**由策略的 action 驱动**的（robotd 不单独驱动它们），所以"我只是
      想动头"也必须先把策略打开；
    - 策略一开，gait 网络就在跑。**零速度指令下腿也动了**（这是实测现象）；但到底是
      "这条策略本身零指令就走"还是"喂进去的 obs / 指令不对"（比如 obs 布局与训练端
      不一致、指令源不是零），**没有单独验证过** —— 要查得从 `robot.state` 里的
      目标角是否周期性摆动入手（telemetry 帧没有指令字段，看不到 twist）。
      另外 robotd 检测到坐姿时**会先自己用 `sitstand` 策略起身**，起身完接着走。
  · 改法：加两把闸，都在「真机指令」组里，都是勾选即发（`on_update`）
    - **① 策略总开关** = `robot.enable {on}`：关掉 → robotd 退回 hold 位姿，所有策略
      都不再驱关节。⚠ **不等于松扭矩**（reg40 仍是 1，关节还是硬的），要松按 ⑥
    - **② 起身策略开关** = `robot.loadPolicy {slot:"sitstand", path}`：关 → path 写
      字面量 `"none"`（`params::is_none_sentinel`，和 `[policy] sitstand = "none"`
      同一套）。robotd 允许关任何槽位，**只有 `walk` 例外**（它是所有槽位解析不到时
      的兜底，robotd 明确拒绝："the walking policy cannot be switched off"）
  · 桥侧：反向白名单加一条 `loadPolicy`（`robotd_telemetry_bridge.py` v0.4），
    否则这条会被桥当"不认识命令"拒掉
  · ⚠ 起身开关的前提是 **robotd 真的加载了策略**（② 别勾 `--no-policy`）。实测：
    `loadPolicy` 只在 `[policy] enabled = false` 时被拒（"policies are disabled on
    this robot"）；而 `robot.enable off` **不**触发这个拒绝 —— 关掉总开关之后再发
    `loadPolicy` 照样 accepted（那条闸只关"是否在驱动机器人"）
  · ⚠ 槽位切换是**在 home 位姿时才发生**的，ack 只说 accepted，真结果看状态行的
    `policy`
  · ⚠ **改完要重启桥**（`robotd_telemetry_bridge.py` 是另一个进程，白名单是启动时
    读的）；面板也要重启（v0.22）

v0.21（2026-09-30）：**把 ONNX Runtime 指给 robotd（策略因此才可能加载）**
  · 症状：不带 `--no-policy` 起 robotd，health 报
    `policy unavailable: ONNX Runtime not loadable (libonnxruntime.so): …`
  · 根因：`duck-control/src/policy.rs::ensure_runtime()` 用 `libloading` dlopen
    `libonnxruntime.so`（名字取自 `ORT_DYLIB_PATH`，没设就是裸名走系统搜索路径）。
    这台 WSL **没跑过 `scripts/setup-board.sh`**，`/usr/lib`、`/usr/local/lib` 下都没有
    它；实测那份在 `/home/liu/ort/onnxruntime-linux-x64-1.28.2/lib/`（**1.28.2**，
    满足 `ort` 2.0.0-rc.11 要的 ≥1.23），纯粹是没进搜索路径
  · 改法：② 的启动命令前加 `ORT_DYLIB_PATH=<那份 .so>`，并把它做成面板上的一个文本
    框（`ROBOTD_ORT_DEFAULT`，**留空 = 不设**，走系统搜索路径）
  · ⚠ **只修 dylib 还不够**：`[policy]` 的默认槽位解析到 `POLICY_DIR`
    （`/opt/robot/policies/current`），而这台机器上**该目录根本不存在** ⇒ 就算 dylib
    好了，官方默认路径的 `velstand.onnx` / `alpha_sitstand.onnx` / …也全是缺文件，
    一样 unhealthy。本机只有 RL 导出的 `microduck_rl/{walk,standup}_hd1910.onnx`
    （实测 obs `[1,61]`、actions `[1,14]`，正好对上 `OBS_LEN=61` / `ACTION_LEN=14`），
    所以在 `realbus-robotd.toml` 的 `[policy]` 里把槽位显式指过去、把没有文件的槽位
    用 `"none"` 关掉（`is_none_sentinel`，对 `kick_left/kick_right/roulade` 也有效：
    `resolved_skills_with` 末尾 `retain` 掉 `resolved_path()` 为 None 的）
  · 顺带纠正 ② 里那段过期内联注释（"起 robotd 不会主动开扭矩" —— v0.20 已证伪）

v0.20（2026-09-30）：**"电机掰不动"的真凶查明，并落地成一键按钮** —— 新增 ⑥ + `tools/ft_regs.py`
  · 现象：`robot.relax` 报 `torque off`、15 颗舵机全部 ACK，关节**还是掰不动**
  · **真根因（单变量探针实测，不是推断）**：HD-1910 的固件在收到**目标位置写入（reg42）**
    时会把扭矩开关 reg40 自动置 1。对停着的总线做对照：
      写 reg40=0 → 读回 0（15/15）；什么都不写等 2 s → 还是 0（不是它自己跳）；
      **只写一次 reg42=当前位置 → 读回 1（15/15）**；只写 reg46=速度 → 还是 0。
    而 robotd 的控制环**每拍都在写 reg42**（`FeetechIo::apply` 的
    `sync_write_goal_position`），因为它在 "holding the pose found at startup"。
    ⇒ `robot.relax` 把 reg40 写 0 之后，**下一拍（20 ms 后）就被舵机自己翻回 1**。
    带 `--no-policy` 也一样：日志从 "policy unavailable" 变成 "policy disabled"，
    但机器人照样 hold 位姿、照样写 reg42，reg40 照样回到 1
  · 因此**纠正两条旧结论**：
    1. 不是（只是）`feetech.rs::set_torque` 只发不等的问题 —— 那只是让整件事看不见
       （`write_torque_enable()` 没有读回校验），不是根因
    2. "起 robotd 不碰扭矩 / 停在 `Bringup::Limp` 所以关节是松的" **是错的** ——
       Limp 只是"没在驱策略"，位姿照样 hold、reg42 照样写、扭矩照样上
       （老话"要松就断电 5 s 或按 relax"里的 **relax 这条路在 robotd 跑着时不成立**）
  · 新增工具 `tools/ft_regs.py`：`--read`（默认）/ `--off` / `--on` / `--port` / `--ids` /
    `--brief`。趁 robotd 不在时直写 reg40 并**读回校验** —— robotd 从不读回，这是唯一
    能确认"真的写进去了"的办法
  · 新增 ⑥ 按钮：**停 robotd + 桥 → 写 reg40=0 → 读回**。⚠ 写完**不要马上按 ②**：
    robotd 一起来 reg40 就回到 1。要一边看孪生一边掰关节：按 ④ 把总线收回 Windows、
    再按 ③′ 换回**串口源**（串口源只读 reg56/IMU，不写 reg42，扭矩不会回来）
  · 同步改掉 ② 那段说明、⑤ 的注释、以及「关节实测限位」里"用 robot.relax 松扭矩"的措辞

v0.19（2026-09-30）：**"数据源停了，表格还在印旧值"** —— 这是上一轮漏掉的一条"显示≠真实"
  · 现象（实测）：按 ① 把 COM8/COM6 交给 WSL 之后，主循环按设计不再读串口，`states`
    里留着让开前最后一帧的 raw/电压/温度/电流。概览确实写了"15 颗缺失"，可**表格照
    常一行行印着电压温度电流**，跑起来还是"49 Hz"，看不出它已经彻底断流了 ——
    我上一轮就是被这个数骗了一下（以为是真值，其实是挂走前的残影）
  · 改法：记下"数据源从哪一刻起不再给新值"（`bus_down_since`），表格首行加醒目提示
    （**已停 N s，下表整列都是历史值**），每行前缀 `⏳`、状态列写"⏳ N s 前的读数"，
    电压/温度/电流三列不再印（那三列最容易让人以为是实时读数）
  · 覆盖三种断流：串口让给 WSL（`bus_released`）、串口/USB 掉线（`bus_down`）、
    桥掉线（telemetry `not ok`）

v0.18（2026-09-30）：**"面板数据必须是真值"** —— 把几处"只是为了显示"的地方改掉，
  并把 robotd 的 IMU 到底是不是真值这件事**变成面板上能看见的判据**
  · **先纠一个前提（上一轮我给错了）**：robotd 的 IMU **不是**挂在舵机总线上、
    也**不是** ID 200 —— 那是官方原始树 `imu_to_dxl` 板的方案。这台改造成 HD-1910
    的树上，IMU 走**独立串口**：`[bus] imu_port`（台架 `/dev/ttyUSB0`），代码是
    `BusIo::Feetech` + `duck_control::SerialCsv::open(imu_port)`，四元数在**主机**用
    Mahony 融合（`imu_csv.rs` 的 `Attitude`）。台架 params 早就配好了。
    ⇒ **不需要"修复"，它本来就在单独从串口读**。
  · **但有一个真陷阱**：`imu_port` 打不开时 robotd **静默**退化成"假定直立静止"
    （main.rs 只打一行 `cannot open the IMU; the trunk will be assumed upright`），
    此后 `quat` 恒 `[1,0,0,0]`、`gyro` 恒 0 —— 面板上是一个**看着完全正常**的直立
    姿态，你根本看不出它没数据。（`usbipd` 重挂会换号：`ttyUSB0` → `ttyUSB1`。）
    修法：`robot.health` 里本来就有 `imu: {ready, stale_blocks, …}`，以前被桥丢掉了；
    现在桥（v0.3）转发成 `imu_ready/imu_stale`，面板据此显示**真 / 假**，并在
    `ready=false` 或 quat 冻结 >25 帧时告警。同时本机也自己数 quat 冻结帧数（双判据）
  · **"显示 ≠ 真实"逐条修**：
    1. 舵机状态表「角度」列以前直接放 `smoother.get()`（开了平滑就是显示量），
       现在这一列放**真值**，3D 用的那份只在有差别时附成 `→3D …`，并加表头提示
       （录制 CSV 与这一列是真值，只有 3D 用平滑值）
    2. 录制 CSV 的 `roll/pitch/yaw` 三列以前装的是 `base_rpy`（含 yaw 归零 / EMA /
       修正滑块 —— 显示变换），列名却像真姿态。现在拆成 `imu_roll/pitch/yaw`
       （**真姿态**，由 `quat` 换算）与 `base_roll/pitch/yaw`（**3D 实际用的那份**）
    3. 基座姿态面板的 `imu_md` 以前只报 `base_rpy`；现在**真姿态与 3D 用的姿态并列**，
       不一致时明说"叠了归零/EMA/滑块，要真值就勾『基座用原值』"
    4. 「基座姿态」「逐关节标定」两格各加一段声明：**本页 MOUNT / sign / 零位 / 归零
       / EMA 只影响这一页的 3D 显示，不下发 robotd、不写寄存器**；并说明 telemetry
       源下关节角是 robotd 的 `cal_angle` 原样透传
    5. telemetry 源的概览新增一行 **IMU（robotd 的姿态源）**：`imu.ready` 状态 +
       冻结/未刷新告警 —— 以前这一行只报电压电流，"IMU 是假的"根本无处可见
  · 配套：桥 `robotd_telemetry_bridge.py` 升 v0.3（`parse_health` 转发 `imu`）
  · **加单实例保护**（今天实测又撞到两个进程都开 COM8+8081）：按「数据源 + viser
    端口」抢一个锁文件，抢不到就退出并报出占用者 —— 否则你重启的是新进程、浏览器
    看的是旧进程，"改了没生效"根本查不出来

v0.17（2026-09-30）：**「点 ② 之后关节就是硬的」查清了** —— 收两处面板侧的错
  · 现象（用户实测，可重复）：勾着 `--no-policy`，只要点 ② 起 robotd + 桥，关节就动不了
  · 查证（robotd 源码，未改）：
    1. 扭矩**只有**两处会开：`robot.init`（`PowerRequest::Init`）和 enable 触发的自动
       bring-up（要 `snapshot.enabled` 且 `controller.is_some()`）；`--no-policy` 时
       后者不成立 ⇒ **robotd 启动本身不会开扭矩**
    2. 扭矩**只有**一处会关：`cut_torque_before_poweroff`，而它只在 `robot.shutdown` /
       电池空那条「坐下再断电」的路上被调用。**SIGTERM（`pkill`）不走那条路** ——
       `shutdown()` 之后只是 `state.shutdown.store(true)` + `control.join()`，控制环
       退出前一句话都不写总线 ⇒ **pkill 不会切扭矩**
    3. 结论：扭矩是舵机 SRAM 里的 reg40，robotd 既不主动开也不主动关（它只在自己
       `init`/`enable` 时开）。上一轮按过 `robot.init`／`robot.enable`，扭矩就一直留着，
       之后每次起 robotd 关节都还是硬的 —— **这才是「点 ② 就硬」的机制**，跟 ② 无关
  · 面板侧两个真 bug（这次一起修）：
    - ② **不杀旧 robotd**：旧的还占着 `--socket`，新起的 bind 拿到 AddrInUse 直接退出，
      面板连上的其实一直是**最早那一个**（带当时那套参数，可能已加载策略/已 enable）⇒
      「② 的勾选没生效」的另一种解释。现在 ② 先把旧进程停掉，并把它的完整命令行打出来
    - ⑤ 的注释写着"robotd 收到 SIGTERM 会优雅退出：切断扭矩、按 SHUTDOWN_SIT 坐下再
      断电" —— **错的**（那是 `robot.shutdown` 的路）。已改成实情 + 怎么真的松扭矩
  · 顺带把两处会误导的文案改口径：「起 robotd 不会让真机动（扭矩没开）」和状态行里的
    「robotd 报 limp（扭矩没开）」都补上"robotd 的看法 ≠ 舵机 reg40 的实情"

v0.16（2026-09-30）：**桥的 ack 也落日志**（「按了没反应」现在能事后查）
  · 起因：用户一次会话里按了 500+ 次真机指令，事后只能从日志看到**发出去了什么**
    （`[真机] 续发速度…`、`[真机] 下发嘴…`），看不到**桥回了什么** —— ack 只显示在
    面板状态行的"最近一条"里，翻不了历史
  · 改法：`TelemetryClient.poll()` 收到 ack 时打一行
    `[真机] 桥 ack <cmd> ok` 或 `[真机] 桥 ack <cmd> !! 被拒：<note>`（ok:false 的
    note 就是原因：桥只读 / 字段错 / 超量程 / robotd 拒绝）
  · 配套判据（这次实测顺出来的）：日志里**没有** `[真机] !! …没发出去` ⇒ 命令都进了
    socket；而 `move` 心跳的续发门槛是 `move_on and tele and rw_arm.value` ⇒ 有续发
    就等于**当时 arm 是勾着的**。合起来 ⇒「没反应」发生在 robotd 侧，不在面板/桥

v0.15（2026-09-30）：**修「观测到的行程」被脏读数污染** —— 量程外的位置不再当角度用
  · 症状（用户实测，v0.13 的限位表）：`head_yaw` 观测下限 `−2800.3°`、`right_hip_yaw`
    `−2883.8°`、`right_hip_pitch` `−2792.4°`，还有 `left_hip_pitch` 下限 `−199.4°`
  · 反推（公式 `raw = (unit⁻¹(angle − OFFSET)/SIGN)`）得到读到的 reg56 分别是
    **33194(0x81AA) / 33102(0x814E) / 33730(0x83C2)**（都**BIT15 置起**）和 **4185** ——
    单圈 12 bit 编码器（中位 2048、满量程 4095）根本给不出这些值 ⇒ 这几帧的总线事务坏了
  · 根因：`decode_block()` 把位置**无条件**按无符号 u16 收下，脏字节直接算进角度；
    而「观测行程」是裸 min/max，**一帧脏值就永久污染**（要按「清空观测窗」才消）
  · 危险面：v0.13 的 `LIM_MIN_SPAN` 只挡"两侧太窄"，**不挡"太宽"** ⇒ 那时按
    「把观测行程记为限位」会把上面这些垃圾整批写进 bench_joint_limits.json
  · 改法（修在解码边界，顺带护住 3D 与 CSV 录制）：`decode_block()` 发现位置不在
    `0..4095` 就返回 `None`；调用方把 `None` 并按"这一颗这一拍没读到"处理 ——
    保留上一帧位姿、计入「缺失」（不再把垃圾喂给下游）
  · 再补一道兜底：观测窗只收有限且 `|angle| ≤ 2π` 的样本（防 telemetry 源或别的
    路径绕圈）。**没加**"帧间跳变 >π 就丢"那条：解码这道已经堵住量程外，而那条会在
    换姿势/断流之后误伤第一个真实样本
  · 已核：清掉脏值后剩下的极值都能反推回合法 raw（`neck_pitch −130.8°` → 3574、
    `right_knee +120.9°` → 672、`left_knee −112.8°` → 3077），是真的走到过，不是坏值

v0.14（2026-09-30）：**修"IMU 口被关掉的那一瞬间整个页面崩掉"**
  · 症状：串口源跑了 59 s（2900 帧）后整页死掉，浏览器一片空白，日志尾部是一个
    `AttributeError: 'NoneType' object has no attribute 'hEvent'`，栈在
    `ImuStream.poll()` → `serialwin32.read()` 的 `win32.ResetEvent(self._overlapped_read.hEvent)`
  · 根因（pyserial 的 `close()` 不是原子的，`serialwin32.py`）：`read()` 开头只检查
    `is_open`，紧接着就用 `self._overlapped_read.hEvent`；而 `close()` 是**先**
    `_close()`（内部把 `_overlapped_read` 置 **None**）、**后**才 `is_open = False`。
    面板回调线程调 `close()` 与主循环进 `read()` 撞上，就读到"`is_open` 还 True、
    `_overlapped_read` 已 None" → `AttributeError`。它**不在 OSError 家族**里，
    `poll()` 原来只抓 `(OSError, SerialException)`，于是这一拍把整个进程带走
  · 为什么总线没跟着崩：总线那条路（`bus.sync_read`）本来就 `except Exception` 兜着，
    崩的只有 IMU 这一路
  · 改法一：`ImuStream.poll()` 补抓 `AttributeError`（当"这一拍没读到"处理：基座保持
    上一帧，概览里照常报 IMU 无数据），页面不再因为一个口被关就整体死掉
  · 改法二：①（attach）里 `bus_released[0] / imu_released[0]` 改成**先立旗子再 close** ——
    主循环的判断是"看到旗子就不读这个口"，反序会在中间留一段它照样去读已关端口的窗口
  · 两条合起来才把洞封住：旗子先立减少"重新发起一次读"的概率，补抓 AttributeError
    兜住"已经读进去了才被关"的那一半
  · 触发场景（复现路径）：串口源下按了 ①/③ 这类会 `close()` 端口的按钮

v0.13（2026-09-30）：**限位重做 —— XML 的 `range` 不再当限位用** + 「观测行程」一键记
  · 症状（用户实测）：真机停在正常姿态，限位表却把很多关节标成「⚠ 超限」
  · 实测证据（从孪生日志的 reg56 逐颗回算本页角度）：静止时 `left_hip_pitch` 本页算出
    `−179.8°`、`left_knee` `−92.4°`，而 XML 写的是 `±90°` ⇒ 每帧都"超限"；同一份标定
    下 `neck_pitch` `+25.3°`、`head_pitch` `+17.3°` 却都落在 XML 范围内
  · 根因：XML 的 `range` 是 MuJoCo 模型的**设计值**，跟本页显示角（reg56 → 本页
    SIGN/OFFSET → rad）**口径不保证对齐**，尤其腿这一路。拿它判超限就是每帧误报；
    拿它夹指令会把合法指令整段挡掉（v0.11 就这么夹的）
  · 改成三个来源，可信度从高到低：**实测**（推死点记的角）＞ **观测**（跑动中学到的
    min/max）＞ XML（设计值 —— **只当滑条量程兜底，不判超限、不夹指令**）
  · `real_lim`：每一侧可信限位，`None` = 这一侧没有 ⇒ 不判也不夹。`Command.clamp()`
    改成接受 `None`（半开区间），`jnt_range` 退居"头/颈滑条量程"一个用途
  · 新增「**把观测行程记为限位**」：主循环每拍记 14 颗的 min/max，跑一会儿（或让策略
    带一段）按一下，整批按 ±3°（`OBS_MARGIN_RAD`）写进去 —— 和显示角同口径，不会误报。
    省掉逐颗推死点（14 次手 + 先松扭矩）。配套「清空观测窗」
  · 表里新增「观测到的行程」列；余量只按**可信**的那一侧算，两侧都没有就写
    「—（无可信限位，不判）」；`src`（实测/观测）随数值落盘，重启后仍分得清来路
  · 已知未尽：桥那侧的下发校验仍按 XML 量程硬编码（只影响头/颈的下发）

v0.12（2026-09-30）：**「真机指令」按了没反应的问题** —— 状态行改成活的自检 + 加换回串口源
  · 症状（用户实测）：切到 telemetry 源后，按「急停：松扭矩」、拖头/颈滑块，真机一点
    不动，面板也看不出为什么
  · 根因一（面板说谎）：`_estop()` 发完命令就把状态行改写成「急停：robot.relax（松扭矩，
    会塌）」—— 看着像成功了，其实桥可能回的是 `ok:false`（只读桥 / robotd 拒了）。真正的
    ack 在同一个折叠组最下面那格里，折叠着根本看不见
  · 根因二（默认值互相打架）：② 起桥时「桥带 `--allow-write`」默认**不勾** ⇒ 桥是只读的，
    「真机指令」这一组按什么都只会被拒；② 的「`--no-policy`」默认**勾着** ⇒ robotd 不加载
    策略，而头/颈正是在策略的 action 里驱动的 ⇒ 逐关节驱动必然不动
  · 改法一：状态行不再由某个 handler 写一句就定死 —— `rw_state` 改成每 0.5 s 从**活状态**
    重算（`_rw_status()`）：源 / arm / 最近一条 ack / robotd 的 limp·policy·fallen，并直接
    写出**接下来该按什么**（"robotd 报 limp：先按 robot.init"、"policy=held：robotd 起的时候
    别带 --no-policy"、"桥 ack ok:false：桥是只读的"）
  · 改法二：② 起桥时若没勾 `--allow-write`，回显里**明写**"桥将以只读方式启动，真机指令
    那组按了不会动"，不再让人自己去猜
  · 改法三：新增 **「③′ 换回串口源重启」**（`--port` / `--imu-port`）—— 原来的 ③ 是**单向**
    的：切到 telemetry 之后按 ④ 收回总线、⑤ 停桥，页面就卡在 telemetry 源上没数据、也没有
    回去的路。换回前先**探一次串口能不能开**，开不了（还挂在 WSL 里）就拒绝并提示先按 ④，
    免得 execv 把页面弄死
  · 已知未尽：桥那侧的 `--allow-write` 仍是它自己的闸，本页不会替你加

v0.11（2026-09-30）：**实物实测的关节限位** + 基座姿态「原值直通」开关
  · 新增「关节实测限位」面板：让关节自由（断电，或 `robot.relax` 松扭矩）→ 用手把某颗
    关节慢慢推到机械死点 → 选关节按「记下限 / 记上限」。记完自动落盘
    tools/bench_joint_limits.json，下次启动读回。两侧至少拉开 1.15°（`LIM_MIN_SPAN`）
    才算一对有效行程，否则当成"同一处点了两次"当场拒掉
  · 有实测值的关节，`jnt_range`（合成指令夹取）与头/颈滑条量程就用实测值替掉 XML 的
    `range`；**没记的那一侧 / 没记的关节仍用 XML**。为什么不直接用 XML：XML 的 range 是
    **设计值**（±90°、±22° 这类整数），不等于这台实物真能走到哪儿
  · 限位表逐行给「实测下限 / 上限 / 当前角 / 距最近限位」，超限标 ⚠ —— 只读告警，
    **不动 3D**（3D 的职责是如实反映真机，夹住了就看不见真机到底在哪儿）
  · 新增「基座用原值」开关：绕过 yaw 启动归零、up 向量 EMA、三个修正滑块，基座直接用
    IMU 的四元数（telemetry 源下这就是 robotd 原值；串口源下是本页融合结果）。
    核对仿真与真机绝对姿态时开它
  · 已知未尽：桥那侧的下发校验仍按 XML 量程硬编码；实测限位若比 XML **宽**，超出的
    那一段桥会拒。实测一般比 XML 窄（机械死点落在模型行程之内），正常不冲突

v0.10（2026-09-30）：**修 telemetry 源的双重标定** —— 同一姿态下两源的关节角终于对得上
  · 症状：telemetry 源显示的关节角和串口源对不上（15 个关节全部翻号、再偏一次），
    一眼能看出"和真实源不一样"，于是怀疑标定没生效
  · 根因：robotd 经桥送来的 `j` / `tg` **已经是它自己按 sign/offset 标定过的关节角**；
    本脚本在入口 `rad_to_raw()` 折回伪 raw 之后，下游又走了一遍 `raw_to_angle()`
    （SIGN / OFFSET），等于把标定叠了两层。算术对账（同一个姿态的 left_hip_pitch）：
    串口 raw 3964 → 本页算出 −3.1410 rad；robotd 报 −3.1416 rad（折回伪 raw = 0）；
    页面却显示 +2.939 —— 两源对"真角度"的认识本来就一致，是这层叠加把号翻了
  · 修法：`ServoState` 加 `cal_angle` / `cal_target`（含义是"已经是标定过的角"），
    telemetry 的 `_apply()` 直接填 robotd 的弧度；新增 `joint_angle()` / `joint_target()`
    两个取值口，3D 实线、目标幽灵（橙）、状态表、状态打印、录制 rad 列、分析对照、
    合成指令基准全部改走它。**串口源一行没动**：`cal_*` 为 None 时仍按 raw → SIGN/OFFSET
  · 故意**不用**"反解 `raw = rad_to_raw((rad − OFFSET)/SIGN)` 再算回来"的取巧写法：
    raw 只有 12 bit ⇒ 解码窗口被 OFFSET 平移（right_hip_yaw 的 OFFSET 是 −2.697、
    head_yaw 是 −1.097），超出窗口的角会绕 2π —— 是埋雷
  · 两处**仍会不一样**、别再当 bug 查：telemetry 源的 `raw` 列是 `4096*(rad+π)/2π`
    （"robotd 的角折回 raw"），不是那颗舵机的物理计数，所以两源的 raw 列、以及终端那张
    左右腿"和 ≈ 4096"的对称表必然不同；比两源一致不一致，要看 rad 列
  · 验证（2026-09-30，无硬件）：假桥按 50 Hz 灌"15 个关节全是 +0.7 rad"，终端与状态表
    一律显示 **+0.700**（修前会显示 −0.700 + 各自 OFFSET，逐个不同）；串口源重启后逐行
    与修前**完全一致**（neck_pitch +0.437 / head_yaw −0.026 …），确认串口链路没被动到
  · 旁证：18:55 那次 telemetry 里 head 四关节的角（+0.437 / +0.298 / −0.026 / −0.008）
    与 18:58 串口源**逐位相同** —— robotd 报的确实是本页标定口径的关节角，不是巧合
  · 已知未尽（没改，怕越界）：telemetry 源录出来的 CSV，`_tgt` 列是伪 raw，拿它走
    「CSV 回放」会被按本页标定再解一次。要回放 telemetry 录的数据，得先给 CSV 加个
    "数据源"标记

v0.2（2026-09-30）：
  · 目标幽灵层：橙色半透明副本，由舵机目标位置 reg42 驱动，跟实测反馈并排看跟随误差
  · 数据录制：一帧一行 CSV（15 关节 raw/角度/目标/电压/温度/电流 + IMU + 时间戳）
  · 标定值一键落盘 bench_mirror_calib.json，下次启动自动读回
  · 告警历史（概览面板）+ 舵机总线掉线自动重连（1 s 一次，不用重启脚本）

v0.3（2026-09-30）：**分析模式** —— 指令 → 仿真执行 → 与真机做差异分析 → 出优化建议
  · 青色「仿真层」：同一份 XML 的执行器模型（position actuator + mj_step），由指令驱动
  · 四种指令：单关节阶跃 / 单关节正弦 / 预定义姿态序列 / 回放录制 CSV 的目标轨迹
  · 差异分析：逐关节 RMSE、峰值误差、稳态误差、互相关滞后、执行器饱和率
  · 报告落盘：analysis_<时间>.md（逐关节指标）+ analysis_<时间>.csv（逐帧明细）
  · 系统辨识：粗网格搜 kp / damping / frictionloss，**只出建议、不写 XML**

v0.4（2026-09-30）：**robotd telemetry 数据源** —— robotd 驱真机时也能镜像
  · 新增 `--telemetry-host 主机:端口`：数据不再来自 COM8，而是 robotd 推出来的
    `robot.state`（15 关节实测角 + 目标角 + IMU + 安全状态），经
    `tools/robotd_telemetry_bridge.py` 从 WSL 的 unix socket 搬到 TCP
  · 与 `--port` 互斥：一个数据源一个进程，别指望同时开（COM8 是独占的）
  · telemetry 源没有逐颗的电压/温度/电流（robotd 只推整包均值与最热关节），
    那几列显示 `--`、录制里留空，概览里给整包的电池电压与最热关节温度
  · 分析模式的「CSV 回放」因此有了第二种参考：robotd 驱真机时录的 CSV

v0.9（2026-09-30）：**修 ① 的现实问题：attach 前得先把 WSL 叫醒、并且自己先让出串口**
  · 现场按 ① 报的是 `usbipd: error: The selected WSL distribution is not running`
    —— usbipd 5.3 要求发行版**正在运行**，而 WSL2 闲置一分钟左右就自己停了。现在
    attach 前先 `wsl.exe -d <发行版> -- exec true` 叫醒（面板上留一条「唤醒 WSL」），
    万一唤醒后又被判定没在跑，再唤醒一次重试，仍失败就给一句人话提示
  · attach 要把设备从 Windows 摘走，而串口源模式下**本页正开着 COM8 / COM6**，不放开的话
    attach 一样会失败（放开前实测 4-2 报 `Device busy (exported)`）。现在 ① 会先
    `bus.close()` + `imu.close()` 让开两条口，并置 `bus_released` / `imu_released` 标志：
    主循环不再读、也不按 1 s 一次重开那个口（重开等于跟 attach 抢设备）。数据要按
    ②③ 切到 telemetry 源才回来；想撤回就 ④ + 重启本页串口模式
  · 失败回显按原因分岔：`not running` / `in use` / 需要管理员，各给一句对应的话，
    不再只丢一段 usbipd 原文
  · 新增「② robotd 不加载策略（`--no-policy`）」勾选框，**默认勾上**。两种起法
    启动后都不动真机（robotd 停在 `Bringup::Limp`、扭矩没开，只有显式的
    `robotd init` / `robot.enable` 才驱动关节），差别只在策略加载了没：勾着时
    `robot.policies` 报 enabled=false，走路/技能不可用、只剩头颈嘴这类关节直控。
    台架纯观测就保持勾上；要试策略时取消勾选再按 ②
  · 修 ② 的 `rc=15 · 0.2 s ·（无输出）`：`pkill -f` 的模式把自己这条 `bash -lc` 也
    匹配上了（② 的 inner 串里含展开后的真桥路径）。改用锚定 cmdline 开头的
    `BRIDGE_PROC_RE`（`^python3.*robotd_telemetry_bridge`）—— 真桥 argv[0] 是
    `python3`、wrapper 是 `bash`，锚定之后 wrapper 永远匹配不上。② 与 ⑤ 都已统一
  · 修 ② 的 `nohup: unrecognized option '--params'`（自杀死修掉后才露出来）：② 的
    inner 串里**不能出现 `$`**。`wsl.exe -d … -- bash -lc "<inner>"` 不是原样转交 ——
    wsl.exe 会把命令重拼成一行先过一层外壳做参数展开，于是 `BIN=$(command -v robotd
    || echo …)` 里的 `$(…)` 在外层就被算掉、`nohup $BIN` 里的 `$BIN` 在外层展开成空
    （外层没有 BIN），bash 最终收到 `nohup  --params …`。实测给 `$BIN` 的 `$` 加一层
    反斜杠转义就恢复正常，证明是外层在展开。现在直接用手填的绝对路径（`binp` 已过
    `^/[A-Za-z0-9._/-]+$`），robotd 本来也不在 WSL 的 PATH 里，`command -v` 永远走兜底
  · 教训：**验证这类 wsl 命令不能用「写个 .sh 再 bash 它」的干跑**（文件内容不会被
    外层展开，测不出这个坑），得走面板真实路径

v0.8（2026-09-30）：**联调命令升级为「一键启动」** —— 不用再去终端照抄命令
  · 「联调命令（robotd + 桥）」组里加了 5 个按钮，对应上面那四条命令：
      ① 把两条总线挂到 WSL（`usbipd attach`）  ④ 把总线收回 Windows（`usbipd detach`）
      ② 在 WSL 起 robotd + 桥（`nohup`，日志落 `/tmp/{robotd,bridge}.log`）
      ⑤ 停掉 WSL 里的 robotd + 桥（`pkill`）
      ③ 本页换成 telemetry 源**同端口重启**（`os.execv`，浏览器只闪一下）
  · 三道闸，缺一不可：面板要勾 **「⚠ 允许一键启动」**；所有文本参数过正则白名单
    （只认 `/路径`、`host:port`、`数字-数字`、`[A-Za-z0-9._-]` 这几种形状，防注入）；
    ③ 在换源前**先探桥端口通不通**，不通就拒绝执行、只提示 —— 免得把页面弄死
  · 命令全在线程里跑，50 Hz 主循环一秒都不等；结果（退出码 / 耗时 / 输出尾部 14 行）
    在按钮下面那格 2 Hz 回显。usbipd 直连失败（非管理员）才用
    `Start-Process -Verb RunAs` 提一次权，并明确回报"需要管理员"
  · 桥要不要 `--allow-write` 由面板上一个勾决定（不加也能看，只是「真机指令」那组
    一律被拒）。参数（发行版 / BUSID / robotd 路径 / socket / listen）都在组里可改，
    默认值是这台机器上已实测确认的
  · 两个坑实测踩过并已修：`pkill -f robotd_telemetry_bridge.py` 会**把 wrapper 自己
    这条 bash 也杀掉**（它的 cmdline 里就含这段模式，rc=15、后面的命令全不执行），
    改成 `robotd_telemetry_bridge[.]py` 让 wrapper 匹配不上；`wsl.exe` 的 stderr 是
    UTF-16LE，原来按 UTF-8 解整行乱码，现在按 NUL 密度判编码并把 `wsl:` 告警行滤掉。
    **方括号那招后来发现不够**（v0.9）：② 的 inner 串里还要展开真桥路径，wrapper 的
    cmdline 里于是出现带真点的 `robotd_telemetry_bridge.py`，又被模式匹配到 —— 症状
    与原来一模一样（rc=15、0.2 s、无输出）。现在统一用锚定开头的 `BRIDGE_PROC_RE`

v0.7（2026-09-30）：
  · 面板新增折叠组 **「联调命令（robotd + 桥）」**：把"怎么从串口源切到 telemetry 源"
    那几条命令（usbipd attach / WSL 起 robotd / WSL 起桥 / Windows 换源）直接写在
    页面上，不用再去翻文档或聊天记录。组里第一行会按当前数据源说明**这一页现在
    在用什么**（串口源 / telemetry 源），以及「真机指令」那组因此可不可用
  · 命令措辞与 docstring 里那段保持一致（含 `--allow-write` 那句"不加也能看，
    只是面板那组会被拒"）

v0.6（2026-09-30）：**分析模式重做：真机也动能对比 + 面板分两组**
  · 面板分成两组，后果不同所以分开摆、各自标注清楚：
      - **「真机指令（会动真机）」** —— 头/颈/嘴逐关节直控（`robot.head` /
        `robot.mouth`）、速度指令（`robot.move`）、`robot.init` / `enable`，以及两条
        急停（`robot.stop` 零速 / `robot.relax` 松扭矩）。命令经桥的反向通道下发。
        三道闸：桥要 `--allow-write`、面板要勾 arm、逐条命令走白名单 + 量程校验；
        急停**不受 arm 约束**（只减权限的方向任何时候都要按得动）。
        串口源时整组置灰（反向通道只建在 telemetry 源上）。
      - **「只跑仿真（指令只喂仿真）」** —— 阶跃/正弦/姿态序列/CSV 回放 + 新增的
        「在线对比」。这一组一个命令都不发。
  · 新增**在线对比**（推荐）：仿真逐帧吃**真机当拍目标**（telemetry 的 `targets`），
    真机由别人（robotd 策略 / 游戏杆 / 上面那组面板）驱动。两层之差就是**纯模型
    失配**，本脚本不需要激励真机；最多 60 s 自动收尾，可手停、可导出报告。
  · 速度指令带 **500 ms 心跳**（robotd 的 deadman）：勾「行走中」时面板按 5 Hz 续发；
    取消勾选 / 急停 / 关 arm 都立刻发一条零速，不等它过期。
  · 反向通道的 ack 与状态帧共用桥的那条 TCP（收到就单独归到 `TelemetryClient.ack`，
    绝不喂给 `_apply`）；面板 2 Hz 刷「最近一条 ack」，桥没开 `--allow-write` 时
    会明确显示"被拒：桥是只读的"，而不是静默无事发生。

    ⚠ 这一版起，本脚本**不再全程只读** —— 「真机指令」那组是会驱动真机的开关。
      默认不 arm、串口源置灰；不碰那一组就还是原来那个只读镜像。

v0.5（2026-09-30）：**工具面板 + 青色层改回镜像**
  · 页面顶部新增「工具」面板（默认展开）：录制 / 目标幽灵 / 仿真层 / IMU 跟随四个
    开关集中在这里，一眼看到现在有什么能开、各自是开是关；原来散在各自折叠面板里
    的副本已删掉，避免两处状态打架
  · 青色「仿真层」的语义改对了：**不跑分析时它直接镜像真机姿态**（两层本该重合，
    这是用来验证 XML 模型与装出来的实机是同一个姿态）；只有按了「开始分析」、
    分析正在跑的那一段，青色才按仿真自己走 —— 那时两层分开才是有含义的预测偏差。
    原先只在 `analysis.cmd is not None` 时才推位置，平时停在零位/最后一帧，看起来
    就是"青色跟实线对不上"
  · 显示与运行解耦：青色可见性只由「工具」里的开关决定，勾不勾「分析模式」不再
    影响看不看得见；点「开始分析」会自动把青色亮出来

要对真机做有意义的差异分析，得先有一份**真机真的动过**的参考 —— 三条路：

  ① **CSV 回放 + 串口源**：拿一份录制，命令取 `*_tgt` 列（真机当时收到的目标），
     真机参考取 `*_rad` 列（reg56 实测）。同一串目标喂给仿真，就得到"同一指令下
     模型 vs 真机"的逐帧对比。
  ② **CSV 回放 + telemetry 源**：录制是在 robotd 驱动真机时录的（见 v0.4），
     命令/参考仍是 `*_tgt` / `*_rad`，区别是那份数据来自 robotd 自己的观测，
     没有第二个进程碰过串口。
  ③ **在线对比（v0.6，推荐）**：勾上就直接用**真机当拍目标**喂仿真，真机由 robotd
     的策略 / 游戏杆驱着走；两层之差是纯模型失配，本脚本一条命令都不用发。

  ⚠ COM8 是独占的：robotd（WSL）拿着串口时，本脚本用 `--port COM8` 读不到舵机。
    要"一边 robotd 驱真机、一边这里看反馈"，走 telemetry 桥（v0.4），不要试图
    同时开串口。合成指令（阶跃/正弦/姿态序列）只能看仿真行为，报告里会明确标注
    "真机没有被激励"。

只读是**默认**：镜像链路只发 PING / READ / 广播读(0x82)，不写任何寄存器、不开扭矩。
唯一的写路径是 v0.6 的「真机指令」面板（要 telemetry 源 + 桥带 `--allow-write` +
面板勾 arm，见那一段的说明），不碰它就还是纯只读镜像，也不需要 robotd、不需要策略。

链路：
    COM8 (CH343, 自动方向半双工) @ 1 Mbps
      └─ 广播 SYNC_READ(0x82) 一次取回 15 颗的 56..70 共 15 字节
           ├─ 位置 56 (2B 小端, 0..4095, 中位 2048)
           │    angle = sign*(2*pi*raw/4096 - pi) + offset   <- 与 robotd bus.rs 同一公式
           ├─ 电压 62 (0.1 V/LSB)、温度 63 (℃)
           └─ 电流 69 (6.5 mA/LSB)、负载 60 (0.1%/LSB, BIT10 方向位)
                └─ MuJoCo qpos -> mjviser -> http://127.0.0.1:<端口>

    COM6 (CH340) @ 115200  <- STM32 桥转发的 LSM6DSV16x CSV
      └─ ts_ms, ax_mg, ay_mg, az_mg, gx_mdps, gy_mdps, gz_mdps,
         qw_e4, qx_e4, qy_e4, qz_e4
           └─ 后四列是芯片 SFLP 直出的游戏旋转矢量（×1e4 定点，标量在前）。
                乘 MOUNT⁻¹ 搬到躯干系就是姿态本身，主机侧不再做任何融合 ——
                yaw 由芯片的动态自标定稳住，不用再吃陀螺零偏的漂移。
              · 老固件只有前 7 列，或 SFLP 还没吐出第一帧时（四列全 0），
                自动退回主机 Mahony 互补滤波，界面照常可用。
           └─ gravity(躯干系) -> 基座姿态

    IMU 是**可选**的：打不开就用面板滑块手工摆基座，3D 照常跟真机关节走。

    ---- 另一条数据源（v0.4）：robotd telemetry ----
    robotd (WSL, unix socket)
      └─ robot.subscribe -> robot.state @ 50 Hz（实测角/目标角/IMU/安全状态）
           └─ tools/robotd_telemetry_bridge.py（WSL 里跑）--一行一帧 JSON--> TCP
                └─ 本脚本 --telemetry-host 主机:端口（不再开 COM8 / COM6）
    源里的角是**弧度**、而且**已经是 robotd 标定过的关节角**（v0.10），所以取角走
    `joint_angle()` 直接透传，不再套本页的 SIGN / OFFSET（套了就是标定做两遍）。
    折回的 `raw = 4096*(rad+pi)/(2*pi)` 只是伪计数，供 raw 列显示与录制用，两源的
    raw 列本来就不该相同。逐颗的电压/温度/电流在这个源里不存在（robotd 只推整包
    均值与最热关节），表里显示 `--`、录制留空。

浏览器左侧面板：
    · 概览        —— 两个数据源的状态、帧率、缺了哪几颗、告警历史
    · 工具        —— 所有能在页面上开关的功能集中在这一格（默认展开）：
                     录制 / 目标幽灵 / 仿真层（青） / 基座跟随 IMU。开关只此一份，
                     各面板里只剩各自的设置项
    · 目标幽灵    —— 橙色半透明副本，由舵机**目标位置** reg42 驱动；不透明实线由
                     实测反馈 reg56 驱动。两层一摆就能看出跟随误差（卡滞、扭矩不足）
    · 录制        —— 一帧一行 CSV：15 颗的 raw/角度/目标/电压/温度/电流 + IMU
                     四元数/gravity/欧拉角 + 时间戳，默认落 E:\\optiDuck\\logs
    · 基座姿态    —— 跟随 IMU（默认）或手工滑块；「姿态归零」把当前朝向定为 0
    · 舵机状态    —— 15 行表格：原始值/角度/电压/温度/电流/负载，超限标 ⚠
    · 显示        —— 数值平滑与死区的开关，用来压抖动
    · 逐关节标定  —— 反向 + 零位，拖了立刻生效；「打印标定快照」打回终端，
                     「保存标定到 JSON」落盘到脚本旁的 bench_mirror_calib.json，
                     下次启动自动读回
    · 关节限位（v0.13）—— 三个来源：**实测**（松扭矩推死点按「记下限/记上限」）、
                     **观测**（跑动中学 min/max，按「把观测行程记为限位」一键整批记）、
                     XML（设计值，只当滑条量程兜底）。只有可信限位才判超限、才夹指令；
                     表里另有「观测到的行程」列。落盘 bench_joint_limits.json
    · 嘴 / 两点标定 —— 嘴在 XML 里没有关节，3D 看不到，只能两点标定
    · 真机指令    —— **会动真机**（v0.6）。头/颈/嘴逐关节直控 + 嘴开合 + 速度指令
                     + init/enable + 两条急停。要 telemetry 源 + 勾 arm + 桥带
                     --allow-write；串口源时整组置灰，急停永远可按。面板底部那条
                     ack 显示桥最后怎么回的（含"桥是只读的"这种拒绝理由）
    · 只跑仿真    —— **一个命令都不发**（v0.6 由原「分析」改名）。阶跃 / 正弦 /
                     姿态序列 / CSV 回放 四种合成指令只喂青色模型；另有「在线对比」
                     直接用真机当拍目标喂仿真。逐关节差异表 + 报告导出 + 系统辨识
                     （只出建议，不写 XML）。青色仿真层**平时镜像真机、只有分析在跑
                     时按仿真走**（见 v0.5）
    · 联调命令    —— **页面上的操作手册 + 一键启动**（v0.7 写命令，v0.8 加按钮，
                     v0.9 修 ①）：从串口源切到 telemetry 源的 usbipd attach / WSL 起
                     robotd / WSL 起桥 / Windows 换源四条命令写在页面上照抄即可；第一行
                     按当前数据源说明这一页现在在用什么。底下 6 个按钮就是这几条命令的
                     一键版（①挂 USB 到 WSL ②WSL 起 robotd+桥 ③本页换源重启
                     ③′**换回串口源**重启 ④收回总线 ⑤停 robotd+桥 / 桥可选
                     `--allow-write` / robotd 可选 `--no-policy` 且**默认勾上**），
                     要勾「⚠ 允许一键启动」
                     且参数过白名单才会执行，结果在按钮下面回显；③ 前会先探桥端口，
                     不通用不会换源（免得把页面弄死）。③ 在无 robotd/桥时会按设计拒绝。
                     ① 会先唤醒 WSL 发行版再 attach（usbipd 要求它在运行），并先让开
                     本页占着的 COM8 与 COM6（设备被 Windows 占着时 attach 报
                     `Device busy (exported)`），此后本页不再读串口 / IMU、也不重开它们，
                     数据要按 ②③ 切到 telemetry 源才回来。
                     **② 起 robotd = 关节一定变硬**（v0.20 纠正）：Limp 只是
                     「没在驱策略」，robotd 照样 hold 启动位姿、**每拍写一次目标位置
                     reg42**，而 HD-1910 收到目标位置写入就把扭矩开关 reg40 置 1
                     （单变量实测：只写 reg42 → 15/15 立刻变 1）。要松关节：停 robotd
                     后按 ⑥；由此 **`robot.relax` 在 robotd 跑着时也不成立**（它写 0
                     的下一拍就被翻回来，看着生效、关节还是硬的）

串口掉了（USB 松了 / 适配器复位）不会让脚本退出：异常被兜住记一条告警，然后按
1 s 一次的节奏重开端口，重开成功自动继续。

为什么用这个公式：robotd 在 bus.rs 里读 Dynamixel `present_position` 就是这么换算的
（`(2.0 * PI * position / 4096.0) - PI`），飞特与 Dynamixel 的编码器量程同构
（都是 0-4095、中位 2048），所以两边可以共用同一个关节空间。raw 2048 -> 0 rad。

运行（真机接线后；先把 usbipd 上挂着的两个设备 detach 回 Windows）：
    <microduck_rl>/.venv/Scripts/python.exe tools/bench_mirror.py
    ... tools/bench_mirror.py --port COM8 --imu-port COM6 --baud 1000000
    ... tools/bench_mirror.py --no-imu          # 只舵机，基座用手工滑块
    ... tools/bench_mirror.py --no-ghost        # 不做目标幽灵层，启动快一点

robotd 驱真机时的用法（两个终端，WSL 一个、Windows 一个）：
    WSL> python3 /mnt/e/optiDuck/tools/robotd_telemetry_bridge.py \
             --socket /run/robotd.sock --listen 0.0.0.0:8199
    Win> py tools/bench_mirror.py --telemetry-host 127.0.0.1:8199 --viser-port 8081

  要让面板「真机指令」那组真能驱动真机，桥必须带 --allow-write 启动（默认是纯只读
  桥，命令一律被拒、面板上会明说"桥是只读的"）：
    WSL> python3 /mnt/e/optiDuck/tools/robotd_telemetry_bridge.py \
             --socket /run/robotd.sock --listen 0.0.0.0:8199 --allow-write

分析模式的典型用法（一个真机命令都不用发）：
    1. 「录制」勾上，让 robotd 驱动真机做一段动作，录一份 CSV（含 *_tgt / *_rad）
    2. 取消录制 → 勾上「分析模式」→ 指令类型选「CSV 回放」→ 填那份 CSV 的路径
    3. 「开始分析」：同一串目标喂给仿真，跑完逐关节差异表就出来了
    4. 「导出报告」出 md + csv；「拟合执行器参数」出 kp / damping / frictionloss 建议

  或者更省事：真机正被 robotd / 游戏杆驱着动的时候，直接勾「只跑仿真」里的
  「在线对比」—— 仿真逐帧吃真机当拍目标，两层之差就是纯模型失配。

真机指令的用法（**这一组会动真机**；台架上先托住它、手放在电源开关上）：
    1. WSL 那边用 --allow-write 起桥（见上）
    2. 面板「真机指令」→ 勾「⚠ arm」→ 看面板底部的 ack（ping 那条会说明桥能不能写）
    3. 头/颈用滑块（或勾「拖动即下发」）、嘴用滑块 + 「下发嘴一次」；
       行走要先勾「行走中」（有 500 ms 心跳，本面板 5 Hz 续发）
    4. 出问题按「急停：零速」；真塌了按「急停：松扭矩」
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Dict, List, Optional, Sequence, Tuple

import mujoco
import numpy as np
import serial
import viser
import viser.transforms as vtf
from mjviser import ViserMujocoScene
from mjviser.conversions import get_body_name, is_fixed_body, merge_geoms

XML = r"E:\optiDuck\microduck_rl\src\mjlab_microduck\robot\microduck\robot_openmicroduck.xml"

# 复用已经真机验证过的 FT-SCS 帧编解码（同目录的配置工具）
_GUI = r"e:\optiDuck\tools\servo_config_gui.py"
_spec = importlib.util.spec_from_file_location("scg", _GUI)
scg = importlib.util.module_from_spec(_spec)
sys.modules["scg"] = scg
_spec.loader.exec_module(scg)

NUM_JOINTS = 15
POS_REG = scg.MAP_FT.pos          # (56, 2) 当前位置

# ---- 一次广播读覆盖的寄存器块 56..70 ----
# 布局照抄 duck-control/src/feetech.rs 的 READ_ADDR/READ_LEN 与那段注释：
#   [0:2] 位置 · [2:4] 速度 · [4:6] 负载 · [6] 电压 · [7] 温度 · [8] 保留
#   [9] 状态 · [10] 移动标志 · [11:13] 目标位置 · [13:15] 电流
# 一次事务拿到位置 + 电压 + 温度 + 电流，是不逐颗 READ 15 次的前提。
BLOCK_ADDR = 56
BLOCK_LEN = 15
OFF_POS, OFF_SPEED, OFF_LOAD, OFF_VOLT = 0, 2, 4, 6
OFF_TEMP, OFF_STATUS, OFF_MOVING, OFF_TARGET, OFF_CURRENT = 7, 9, 10, 11, 13

RPM_PER_COUNT = 0.732                      # 速度 58：0.732 RPM/LSB
RAD_S_PER_COUNT = RPM_PER_COUNT * 2.0 * math.pi / 60.0
MA_PER_COUNT = 6.5                         # 电流 69：6.5 mA/LSB
VOLT_PER_COUNT = 0.1                       # 电压 62：0.1 V/LSB
LOAD_PER_COUNT = 0.1                       # 负载 60：0.1 %/LSB

# 超限标红用的门限。6.6 V 来自 robotd 的 BATTERY_EMPTY_V：低于它 robotd 会判定
# 电池空并切扭矩，所以这是台架上值得一眼看到的线。
VOLT_LO, VOLT_HI = 6.6, 8.6
TEMP_WARN = 55.0                           # ℃，HD-1910 内部温度
CURR_WARN_MA = 1500.0                      # 空载 20~26 mA，堵转/较劲时才上百 mA

# 关节顺序与 duck-control/src/model.rs 的 JOINT_IDS / JOINT_NAMES 一致：
#   左腿 20..24，头/颈/嘴 30..34，右腿 10..14
#
# 注意 33 / 34：出厂装配时 ID 33 驱动嘴、ID 34 驱动 head_roll，与 robotd 的
# model.rs 相反（那里 MOUTH_INDEX = 9 → JOINT_IDS[9] = 34）。阶段 C4 已用配置工具
# 把两颗舵机的 ID 互换，现在实机是 **ID 33 = head_roll、ID 34 = 嘴**，与 robotd 一致。
# 标定值不用改：换的是 ID，不是舵机本身，head_roll / 嘴各自的舵盘装配没变。
JOINT_TABLE: Tuple[Tuple[int, Optional[str]], ...] = (
    (20, "left_hip_yaw"), (21, "left_hip_roll"), (22, "left_hip_pitch"),
    (23, "left_knee"), (24, "left_ankle"),
    (30, "neck_pitch"), (31, "head_pitch"), (32, "head_yaw"), (33, "head_roll"),
    (34, None),
    (10, "right_hip_yaw"), (11, "right_hip_roll"), (12, "right_hip_pitch"),
    (13, "right_knee"), (14, "right_ankle"),
)
JOINT_IDS: Tuple[int, ...] = tuple(sid for sid, _ in JOINT_TABLE)
# 关节名 -> 舵机 ID（嘴不在其中：XML 里没有它对应的关节）
SID_OF: Dict[str, int] = {n: sid for sid, n in JOINT_TABLE if n is not None}

# 嘴（ID 34）：实机上有这颗舵机，但 OpenMicroDuck 的 MuJoCo XML 里**没有**对应的关节
# —— 全文件只有 14 个 hinge joint，`jaw` 是刚性挂在 head_roll 那个 body 上的。
# 所以嘴没法在 3D 里显示，也就没法用"看 3D"的方式标定。它的标定值是给 robotd 用的：
# MOUTH_CLOSED = −5°、MOUTH_OPEN = +30°（model.rs），要用得上就必须先把
# raw 映射到同一个角度约定。做法：把嘴用手掰到全闭 / 全开，记下读数表里的 raw，
# 两个点就能定出 sign 和 offset。
MOUTH_ID: int = 34
MOUTH_NAME: str = "mouth"
MOUTH_CLOSED_RAD: float = math.radians(-5.0)
MOUTH_OPEN_RAD: float = math.radians(30.0)

# ---- 落盘位置 ----
# 标定 JSON 放在脚本旁边：面板上调好的值「保存标定」写这里，下次启动自动读回来，
# 省得每次重启都要重新对实物拖一遍滑块。文件不在就用下面 CALIB 里的实测值。
CALIB_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "bench_mirror_calib.json")
# 「关节实测限位」落的盘（v0.11）。XML 的 `range` 是模型设计值，不是这台实物真能走到的
# 行程；面板上推到死点记下来的那一组写这里，下次启动读回，有实测值的关节就用它。
LIMITS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "bench_joint_limits.json")
# 记实测限位时，两侧至少要拉开这么宽（rad ≈ 1.15°）才算一对有效行程 ——
# 低于它就当成"同一处点了两次"，当场拒掉，免得存下一对宽度为零的限位。
LIM_MIN_SPAN = 0.02
# 「把观测行程记为限位」时，两侧各往外放宽这么多（rad ≈ 2.9°）—— 观测到的极值只是
# 这几天恰好走到过的地方，不是死点，留一点余量免得上来就判"超限"。
OBS_MARGIN_RAD = 0.05
# 录制的 CSV 默认落这里（目录不存在会自动建）。
LOG_DIR = r"E:\optiDuck\logs"

# 「目标幽灵」：同一份 XML 再渲染一层半透明副本，由舵机**目标位置** reg42 驱动；
# 不透明的实线模型仍由实测反馈 reg56 驱动。两者分开就能一眼看出"雷达下发的目标"
# 和"舵机实际到的位置"差多少（跟随误差、卡滞、扭矩不足都会在这里露出来）。
GHOST_RGB: Tuple[int, int, int] = (255, 140, 0)
GHOST_OPACITY = 0.35

# 「仿真预测」层：分析模式下由 SimTwin 的 mj_step 结果驱动，青色。跟实线（真机实测）
# 摆在一起，一处关节差异就是一条"模型没标对"的线索。
SIM_RGB: Tuple[int, int, int] = (0, 200, 220)
SIM_OPACITY = 0.55

# 逐关节标定：angle = sign * (2*pi*raw/4096 - pi) + offset
#
# 2026-09-29 在浏览器面板里对实物逐关节调定，值如下。这是**实机装配的实测结果**，
# 不要用"理论上应该是什么"去改它：装配时舵盘装在哪一齿、舵机朝哪边装，都只由实物决定。
# （先前从 symmetry.py 的 left=-right 约定推出"sign 全取 +1"是自洽的，但实机舵盘
# 装配并非理想的镜像零位，实测是**15 个全部取 -1**，以实测为准。）
#
# 2026-09-29 二次复调（33/34 换过来之后）：两个 hip_yaw 的方向反了，已改成 -1 并
# 重解零位；right_hip_roll 的零位也跟着微调。这一版由面板「打印标定快照」打出，
# 原样落在 E:\Temp\bench_mirror.out.log。
#
# head_roll 的标定是在这颗舵机还叫 ID 34 的时候对实物调的；C4 只换了 ID（改成 33），
# 舵机和舵盘没动，所以这组值仍然有效，但换 ID 之后没有重新核过，下次上电留意一下。
CALIB: Dict[str, Tuple[float, float]] = {
    "left_hip_yaw": (-1.0, -0.2620),
    "left_hip_roll": (-1.0, +0.5030),
    "left_hip_pitch": (-1.0, -0.2020),
    "left_knee": (-1.0, -0.3920),
    "left_ankle": (-1.0, +0.5030),
    "neck_pitch": (-1.0, +0.0580),
    "head_pitch": (-1.0, +0.1230),
    "head_yaw": (-1.0, -1.0970),
    "head_roll": (-1.0, +0.4430),
    "right_hip_yaw": (-1.0, -2.6970),
    "right_hip_roll": (-1.0, +0.2480),
    "right_hip_pitch": (-1.0, -0.1370),
    "right_knee": (-1.0, +0.0000),
    "right_ankle": (-1.0, +0.3130),
    # 嘴：2026-09-29 用面板「嘴 / 两点标定」标定（掰到全闭/全开各记一次 raw）。
    # 回代：开 → +30.0°（offset 是解开端点解的，故开必然准），闭 → −6.1°。
    # 那 1.1° 是手掰端点和 robotd 标称角度的差，不是标定误差：encoder 的
    # 2π/4096 rad/count 增益是固定的，只有 sign 和 offset 两个自由度，
    # 两点刚好把它定死，改不了增益。35° 的行程里差 1.1° 可以不管。
    "mouth": (-1.0, +0.4991),
}

# XML 里有对应关节的名字（14 个，"嘴"不在其中）
JOINT_NAMES_MJ: Tuple[str, ...] = tuple(n for _, n in JOINT_TABLE if n is not None)
# 面板上要标定的全部名字 = 14 个关节 + 嘴
CALIB_NAMES: Tuple[str, ...] = JOINT_NAMES_MJ + (MOUTH_NAME,)
SIGN: Dict[str, float] = {n: CALIB[n][0] for n in CALIB_NAMES}
OFFSET: Dict[str, float] = {n: CALIB[n][1] for n in CALIB_NAMES}

# 基座欧拉角（rad，xyz 内旋）初值：用户对着实物调好的那一组（"快照"按钮打出来的）。
# 只在 **IMU 没接上** 时用；IMU 接上时这三个滑块变成"修正偏移"，初值归零。
BASE_RPY_INIT: Tuple[float, float, float] = (-0.0100, -1.4100, -0.1300)
BASE_RPY: List[float] = list(BASE_RPY_INIT)


def load_saved_calib(path: str = CALIB_JSON) -> str:
    """启动时把上一次「保存标定」落盘的 JSON 读回来盖到 CALIB / BASE_RPY 上。

    文件不在（第一次跑）就什么都不做，用文件里那组实测值。读坏了也只是打印一行告警，
    不让它挡住建界面 —— 标定值是可以随时在面板上重调的。
    """
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] 标定文件 {path} 读不了（{exc}），改用文件内实测值", file=sys.stderr)
        return ""
    changed = 0
    for name, pair in (blob.get("calib") or {}).items():
        if name in CALIB and len(pair) == 2:
            CALIB[name] = (float(pair[0]), float(pair[1]))
            changed += 1
    rpy = blob.get("base_rpy")
    if rpy and len(rpy) == 3:
        BASE_RPY[:] = [float(v) for v in rpy]
    return f"{path}（{changed} 项标定 + 基座姿态）"


def save_calib(path: str, calib: Dict[str, Tuple[float, float]],
               base_rpy: Sequence[float]) -> None:
    """把面板上的标定值写成 JSON。原子写：先写 .tmp 再替换，避免写一半掉电。"""
    blob = {
        "note": "bench_mirror.py 面板落盘的标定值；启动时自动读回，删掉即回到文件内实测值",
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "calib": {n: [round(float(calib[n][0]), 1), round(float(calib[n][1]), 6)]
                  for n in CALIB_NAMES},
        "base_rpy": [round(float(v), 6) for v in base_rpy],
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(blob, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_meas_limits(path: str = LIMITS_JSON) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """读回「关节实测/观测限位」：{关节: {"lo": rad, "hi": rad, "src": "实测"|"观测"}}。

    可能只有一侧。文件不在（没记过）就返回空表 —— 全部关节都不判超限、不夹指令（XML
    的 `range` 只当滑条量程兜底）。读坏了只打印一行告警，不挡建界面（限位随时能重记）。
    """
    if not os.path.exists(path):
        return {}, ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
    except Exception as exc:                          # noqa: BLE001
        print(f"[!] 限位文件 {path} 读不了（{exc}），改用 XML 的 range", file=sys.stderr)
        return {}, ""
    out: Dict[str, Dict[str, Any]] = {}
    for name, rec in (blob.get("limits") or {}).items():
        if name not in JOINT_NAMES_MJ or not isinstance(rec, dict):
            continue
        side: Dict[str, Any] = {}
        for k in ("lo", "hi"):
            v = rec.get(k)
            if isinstance(v, (int, float)):
                side[k] = float(v)
        if side:
            # src 是「实测」还是「观测」—— 只影响面板上显示成什么，不影响判/夹
            if isinstance(rec.get("src"), str) and rec["src"] in ("实测", "观测"):
                side["src"] = rec["src"]
            out[name] = side
    n_obs = sum(1 for r in out.values() if r.get("src") == "观测")
    return out, f"{path}（{len(out)} 颗有可信限位，其中观测 {n_obs} 颗）"


def save_meas_limits(path: str, limits: Dict[str, Dict[str, Any]]) -> None:
    """把实测 / 观测限位写成 JSON。原子写，与 save_calib 同一套。只存记过的关节/侧。

    `src`（"实测" / "观测"）随数值一起存：两者都是可信限位，但来路不同，面板上要能
    分开显示 —— 观测值不是机械死点，只是"这台实物真的走到过的地方"。
    """
    blob = {
        "note": "实物实测 / 观测的关节限位（rad，本页标定口径；src = 实测|观测）。"
                "bench_mirror.py 启动时读回；只有这里的值才判超限、才夹指令，"
                "XML 的 range 仅当滑条量程兜底",
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "limits": {n: {k: (v if k == "src" else round(float(v), 5))
                       for k, v in sorted(rec.items())}
                   for n, rec in sorted(limits.items())},
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(blob, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ---- IMU 常量：与 duck-control/src/imu_csv.rs 逐项对齐 ----
# MOUNT：sensor -> trunk 的旋转（实测标定，imu_mount.py solve 的输出）。
MOUNT: Tuple[float, float, float, float] = (0.723884, -0.019342, -0.688977, 0.030481)
# 芯片直出四元数是 sensor->world，要的是 trunk->world，即 q ⊗ MOUNT⁻¹。
MOUNT_INV: Tuple[float, float, float, float] = (MOUNT[0], -MOUNT[1], -MOUNT[2], -MOUNT[3])
# 陀螺零偏（mdps，模块静止实测）；融合没有积分项去估计它，只能减掉。
IMU_BIAS_MDPS: Tuple[float, float, float] = (-590.0, 43.0, 750.0)
IMU_KP = 1.0                               # 互补滤波增益，1/s
ACCEL_TRUSTED_G: Tuple[float, float] = (0.75, 1.25)
G0 = 9.80665
MG_TO_M_S2 = G0 * 1e-3
MDPS_TO_RAD_S = math.pi / 180000.0


# ============================================================================
# 四元数：与 duck-control/src/imu.rs 同一套约定（标量在前）
# ============================================================================

def q_mul(a, b):
    return [
        a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
        a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
        a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
        a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
    ]


def q_unit(q):
    n = math.sqrt(sum(x * x for x in q))
    return [x / n for x in q] if n > 0.5 else [1.0, 0.0, 0.0, 0.0]


def q_cross_terms(q, v):
    x, y, z = q[1], q[2], q[3]
    t = [(y * v[2] - z * v[1]) * 2.0,
         (z * v[0] - x * v[2]) * 2.0,
         (x * v[1] - y * v[0]) * 2.0]
    c = [y * t[2] - z * t[1],
         z * t[0] - x * t[2],
         x * t[1] - y * t[0]]
    return t, c


def q_rotate(q, v):
    """q · v · q⁻¹（躯干向量 -> 世界向量）。"""
    t, c = q_cross_terms(q, v)
    return [v[0] + q[0] * t[0] + c[0],
            v[1] + q[0] * t[1] + c[1],
            v[2] + q[0] * t[2] + c[2]]


def q_rotate_inverse(q, v):
    """q⁻¹ · v · q（世界向量 -> 躯干向量）。"""
    t, c = q_cross_terms(q, v)
    return [v[0] - q[0] * t[0] + c[0],
            v[1] - q[0] * t[1] + c[1],
            v[2] - q[0] * t[2] + c[2]]


def v_cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def v_norm(v):
    m = math.sqrt(sum(x * x for x in v))
    return [x / m for x in v] if m > 1e-9 else [0.0, 0.0, 1.0]


def q_from_axes(x, y, z):
    """由旋转矩阵的三列（躯干三轴在世界里的方向）反解四元数。"""
    m = [[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]]
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        q = [0.25 * s,
             (m[2][1] - m[1][2]) / s,
             (m[0][2] - m[2][0]) / s,
             (m[1][0] - m[0][1]) / s]
    else:
        i = max(range(3), key=lambda k: m[k][k])
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(1.0 + m[i][i] - m[j][j] - m[k][k]) * 2.0
        xyz = [0.0, 0.0, 0.0]
        xyz[i] = 0.25 * s
        xyz[j] = (m[j][i] + m[i][j]) / s
        xyz[k] = (m[k][i] + m[i][k]) / s
        q = [(m[k][j] - m[j][k]) / s] + xyz
    q = q_unit(q)
    return [-x for x in q] if q[0] < 0.0 else q


def q_from_up_yaw(up, yaw):
    """躯干 -> 世界：躯干 +Z 指向 up，朝向由 yaw 定（重力给不了偏航，只能自己给）。"""
    z = v_norm(up)
    y = v_cross(z, (math.cos(yaw), math.sin(yaw), 0.0))
    if math.sqrt(sum(c * c for c in y)) < 1e-6:
        y = (0.0, 1.0, 0.0)
    y = v_norm(y)
    return q_from_axes(v_cross(y, z), y, z)


def euler_xyz(q):
    """躯干 -> 世界的 roll/pitch/yaw（xyz 内旋），只为在面板上显示。"""
    w, x, y, z = q
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    sin_pitch = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = math.asin(sin_pitch)
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return roll, pitch, yaw


# ============================================================================
# 舵机：一帧一次广播读，解码成状态
# ============================================================================

@dataclass
class ServoState:
    sid: int
    raw: int               # 位置原始值 0..4095（中位 2048）
    speed_rpm: float
    load_pct: float
    volt: float
    temp: float
    status: int
    moving: int
    target: int
    current_ma: float
    # 电压/温度/电流是不是**这一帧真的读到的**。串口源每帧都读得到（True）；
    # robotd telemetry 源只推关节角与目标角，那三样根本没上线（False）。
    # 有它才能把"0.0 V"和"没读到"分开 —— 否则 telemetry 模式下 15 颗会被当成
    # 15 颗电池全空，面板直接刷一屏假告警。
    live: bool = True
    # telemetry 源专用：robotd 送来的弧度**本身就是标定过的关节角**，串口源没有这层。
    # 有值 = 直接用它（下游走 joint_angle() / joint_target()），绝不能再叠本页的
    # SIGN / OFFSET —— 那是给"物理 raw"用的，叠上去等于标定做两遍（15 个关节翻号）。
    # None = 串口源，按 raw → SIGN/OFFSET 算。
    cal_angle: Optional[float] = None
    cal_target: Optional[float] = None


def sign_magnitude(u: int, bits: int = 15) -> float:
    """符号-幅值解码：高位的方向位 + 低位幅值（feetech.rs sign_magnitude_u16 同款）。

    位置/速度/电流用 BIT15，负载用 BIT10 配 11 位幅值。
    """
    mag = u & ((1 << bits) - 1)
    return -float(mag) if (u >> bits) & 1 else float(mag)


def decode_block(sid: int, b: bytes) -> Optional[ServoState]:
    u16 = lambda o: b[o] | (b[o + 1] << 8)
    # 位置照旧按 0..4095 无符号用（实机验过：中位 2048、量程满 4095）。
    # BIT15 那一位在单圈内不会置起；真置起了说明舵机在报多圈/异常。
    # **量程外的值不能当角度用**（v0.15）：单圈 12 bit 编码器给不出 4095 以上的
    # 位置，读到就是这一帧的总线事务坏了。2026-09-30 实测到的三个脏值
    # （0x81AA / 0x814E / 0x83C2，BIT15 置起）会把 head_yaw / right_hip_yaw /
    # right_hip_pitch 算成 −2800° 上下，4185 会把 left_hip_pitch 算成 −199.4°，
    # 一帧就永久污染「观测到的行程」那一列（那是裸 min/max）。所以这里返回 None，
    # 由调用方按"这一颗这一拍没读到"处理：保留上一帧、计入缺失。
    raw = u16(OFF_POS)
    if not (0 <= raw <= 4095):
        return None
    return ServoState(
        sid=sid,
        raw=raw,
        speed_rpm=sign_magnitude(u16(OFF_SPEED)) * RPM_PER_COUNT,
        load_pct=sign_magnitude(u16(OFF_LOAD), 10) * LOAD_PER_COUNT,
        volt=b[OFF_VOLT] * VOLT_PER_COUNT,
        temp=float(b[OFF_TEMP]),
        status=b[OFF_STATUS],
        moving=b[OFF_MOVING],
        target=u16(OFF_TARGET),
        current_ma=sign_magnitude(u16(OFF_CURRENT)) * MA_PER_COUNT,
    )


def unit_angle(raw: int) -> float:
    """未标定的原始换算，与 robotd bus.rs 同一公式：raw 2048 -> 0 rad。"""
    return 2.0 * math.pi * raw / 4096.0 - math.pi


def rad_to_raw(rad: float) -> int:
    """unit_angle() 的反函数：弧度 -> raw（0..4095）。

    robotd telemetry 源上来的是弧度，而本脚本的显示 / 幽灵层 / 录制列都按 raw 排布，
    所以在入口处折回 raw，让两条路的数据形状一样。

    **但它只是伪计数**：robotd 的弧度是标定过的角，不是那颗舵机的物理 raw，所以
    "折回来再按本页标定算角"会把标定做两遍（v0.10 修掉的 bug）。取角一律走
    `joint_angle()` / `joint_target()`。
    """
    return int(round(4096.0 * (rad + math.pi) / (2.0 * math.pi))) % 4096


def raw_to_angle(raw: int, name: str) -> float:
    return SIGN[name] * unit_angle(raw) + OFFSET[name]


def joint_angle(st: ServoState, name: str) -> float:
    """这颗舵机当前的关节角（rad）—— 两个数据源**取角只走这一个口**。

    telemetry 源里 robotd 报的弧度已经是标定过的关节角（`cal_angle` 有值），直接用它；
    再套一遍本页 SIGN / OFFSET 就是把标定做两遍（15 个关节全部翻号 + 再偏一次）。
    串口源没有这一层，仍按物理 raw → 本页标定算。
    """
    return st.cal_angle if st.cal_angle is not None else raw_to_angle(st.raw, name)


def joint_target(st: ServoState, name: str) -> float:
    """这颗舵机这一拍收到的目标角（rad），口径与 joint_angle() 完全相同。"""
    return (st.cal_target if st.cal_target is not None
            else raw_to_angle(st.target, name))


# ============================================================================
# 数值平滑：EMA + 死区，用来压抖动（默认很轻，可以关掉）
# ============================================================================

class Smoother:
    """逐名字的 EMA + 死区。

    alpha = 0 时完全不滤波（原值直通）；死区是"小于这个变化量就当没动"，
    对着静止的机器人能把末位抖动吃掉，又不拖慢真实运动。
    """

    def __init__(self) -> None:
        self._value: Dict[str, float] = {}

    def get(self, name: str) -> Optional[float]:
        """当前平滑值（面板显示用）；还没收到过就返回 None。"""
        return self._value.get(name)

    def update(self, name: str, target: float, alpha: float, deadband: float) -> float:
        prev = self._value.get(name)
        if prev is None:
            self._value[name] = target
            return target
        if deadband > 0.0 and abs(target - prev) < deadband:
            target = prev
        value = target if alpha <= 0.0 else prev + alpha * (target - prev)
        self._value[name] = value
        return value


# ============================================================================
# IMU：COM6 的 CSV 流 + Mahony 互补滤波（ports imu_csv.rs）
# ============================================================================

class Attitude:
    """当前姿态的容器：quat 是 躯干->世界（标量在前），yaw 是它的偏航分量。

    常态下由 update() 靠陀螺积分 + 加速度计反馈往前推（Mahony）。桥的新固件
    把芯片算好的四元数送上来时，ImuStream 直接写进 quat/yaw，不再跑滤波 ——
    两条路填的是同一个量，下游（基座姿态、归零、面板）不必分开对待。
    """

    def __init__(self, bias_mdps: Sequence[float]) -> None:
        self.quat: List[float] = [1.0, 0.0, 0.0, 0.0]
        # 零偏是在**传感器系**里静置测出来的，而 update() 拿到的是已经转到躯干系
        # 的角速度，所以这里先把零偏一起转过去再减。少这一步就是拿躯干系去减传
        # 感器系的量，凭空多出一个 |b − R·b| ≈ 1.3 °/s 的偏航漂移 —— 那是模型
        # 会自己转圈的直接原因。
        self.bias = q_rotate(MOUNT, [b * MDPS_TO_RAD_S for b in bias_mdps])
        self.yaw = 0.0

    def update(self, accel: Sequence[float], gyro: Sequence[float], dt: float):
        gyro = [gyro[i] - self.bias[i] for i in range(3)]
        rate = list(gyro)

        mag = math.sqrt(sum(a * a for a in accel))
        if ACCEL_TRUSTED_G[0] <= (mag / G0) <= ACCEL_TRUSTED_G[1]:
            # 加速度计读的是重力的反作用，两者在躯干系里都指"上"：
            # 测量值 vs 估计值，叉积既是误差轴也是（小角下）误差角。
            measured = [a / mag for a in accel]
            estimated = q_rotate_inverse(self.quat, (0.0, 0.0, 1.0))
            error = v_cross(measured, estimated)
            for i in range(3):
                rate[i] += IMU_KP * error[i]
        # 注意：加速度计幅值不在带内就不修正（机器人在加速/落地时它指的不是重力）。

        if dt > 0.0:
            delta = q_mul(self.quat, [0.0, rate[0] * 0.5 * dt,
                                      rate[1] * 0.5 * dt, rate[2] * 0.5 * dt])
            self.quat = q_unit([self.quat[i] + delta[i] for i in range(4)])
            # 偏航没有绝对参考（6 轴，无磁力计），只能靠陀螺积出来 —— 会漂，
            # 所以面板上有「姿态归零」，临场把当前朝向定成 0。
            self.yaw += gyro[2] * dt

        gravity = v_norm(q_rotate_inverse(self.quat, (0.0, 0.0, -1.0)))
        return gravity, gyro


class ImuStream:
    """COM6 上那根线的读取端：STM32 桥以 ≈35 Hz 打 CSV。

    行格式：
        ts_ms, ax_mg, ay_mg, az_mg, gx_mdps, gy_mdps, gz_mdps
        [, qw_e4, qx_e4, qy_e4, qz_e4]
    前 7 列与 imu_csv.rs 的 parse 完全一致；后 4 列是桥的新固件附上的芯片
    SFLP 四元数（×1e4 定点）。四列全 0 = 芯片还没吐出第一帧，此时退回主机
    互补滤波。字段数对不上 / 解析不出 / 有 NaN 的行一律丢弃。
    """

    def __init__(self, port: str, baud: int = 115200) -> None:
        # timeout=0：调用方在 50 Hz 环里轮询，绝不能为等一行数据把这一拍拍住。
        self.ser = serial.Serial(port, baud, timeout=0)
        self.attitude = Attitude(IMU_BIAS_MDPS)
        self.pending = bytearray()
        self.last_t_ms: Optional[float] = None
        self.accel: Optional[List[float]] = None
        self.gyro: Optional[List[float]] = None
        self.gravity: List[float] = [0.0, 0.0, -1.0]
        self.hw_quat = False           # 当前姿态是不是芯片直出（False = 主机滤波）
        self.good = 0
        self.bad = 0
        self.hz = 0.0
        self._t_report = time.perf_counter()

    @property
    def ok(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def close(self) -> None:
        if self.ok:
            self.ser.close()

    def _one(self, line: bytes) -> bool:
        text = line.strip().decode("ascii", "replace")
        if not text:
            return False
        fields = [f.strip() for f in text.split(",")]
        if len(fields) not in (7, 11):
            self.bad += 1
            return False
        try:
            v = [float(f) for f in fields]
        except ValueError:
            self.bad += 1
            return False
        if not all(math.isfinite(x) for x in v):
            self.bad += 1
            return False

        accel_s = [v[1] * MG_TO_M_S2, v[2] * MG_TO_M_S2, v[3] * MG_TO_M_S2]
        gyro_s = [v[4] * MDPS_TO_RAD_S, v[5] * MDPS_TO_RAD_S, v[6] * MDPS_TO_RAD_S]
        # 传感器系 -> 躯干系（芯片是侧躺装的，不转这一下姿态整个是歪的）
        accel = q_rotate(MOUNT, accel_s)
        gyro = q_rotate(MOUNT, gyro_s)

        dt = 0.0
        if self.last_t_ms is not None:
            dt = (v[0] - self.last_t_ms) * 1e-3
            if not (0.0 < dt < 0.5):       # 桥重启 / 断流：不猜 dt，只做修正
                dt = 0.0
        self.last_t_ms = v[0]

        self.accel, self.gyro = accel, gyro

        # 芯片直出的四元数。全 0 是"还没出数据"的哨兵；模长离谱说明这一行不
        # 可信，宁可退回滤波也不要用错姿态去驱动模型。
        q_sensor: Optional[List[float]] = None
        if len(v) == 11 and any(v[7:11]):
            candidate = [x * 1e-4 for x in v[7:11]]
            if abs(math.sqrt(sum(x * x for x in candidate)) - 1.0) < 0.05:
                q_sensor = candidate

        if q_sensor is not None:
            self.hw_quat = True
            # sensor->world 变 trunk->world，就是右乘 MOUNT⁻¹。之后就到此为止：
            # 姿态本身已经是芯片算好的，主机不再积分、不再修正、不再去零偏。
            self.attitude.quat = q_unit(q_mul(q_sensor, list(MOUNT_INV)))
            self.attitude.yaw = euler_xyz(self.attitude.quat)[2]
            self.gravity = v_norm(q_rotate_inverse(self.attitude.quat,
                                                   (0.0, 0.0, -1.0)))
        else:
            self.hw_quat = False
            self.gravity, _ = self.attitude.update(accel, gyro, dt)

        self.good += 1
        return True

    def poll(self) -> bool:
        """非阻塞：把已经到的字节全部解析掉，返回这一拍有没有拿到新样本。"""
        try:
            chunk = self.ser.read(max(1, self.ser.in_waiting))
        except (OSError, serial.SerialException, AttributeError):
            # AttributeError：端口被 close 之后 pyserial 会走到
            # `win32.ResetEvent(self._overlapped_read.hEvent)`，而 hEvent 已是 None。
            # 它不在 OSError 家族里，不抓的话这一拍会把**整个页面打死**
            # （2026-09-30 实测：跑 59 s 后整页崩掉，见文件头 v0.14）。
            # 当成"这一拍没读到"：基座保持上一帧，概览里会报 IMU 无数据。
            return False
        if chunk:
            self.pending.extend(chunk)
        if len(self.pending) > 8192:       # 一直没换行的垃圾，别把缓冲堆爆
            self.pending.clear()

        updated = False
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            if self._one(line):
                updated = True
        return updated

    def stats(self) -> Tuple[float, float]:
        """返回 (Hz, 出错行占比)；顺带把统计窗口清零，只算最近这一段。"""
        now = time.perf_counter()
        span = now - self._t_report
        if span <= 0.0:
            return 0.0, 0.0
        hz = self.good / span
        bad_ratio = self.bad / float(self.good + self.bad) if (self.good + self.bad) else 0.0
        self.good = 0
        self.bad = 0
        self._t_report = now
        self.hz = hz
        return hz, bad_ratio


# ============================================================================
# robotd telemetry 源：不开串口，数据从 robotd 推的 robot.state 来（v0.4）
# ============================================================================

class TelemetryClient:
    """robotd telemetry 桥的客户端：一条 TCP 连接，一行一帧 JSON。

    它一个人顶掉两样东西 —— 串口总线（ServoBus）和 IMU（ImuStream）—— 所以两边要的
    接口它都得有：主循环里 `states = tele.states` 拿舵机，`tele.poll()` /
    `tele.attitude` / `tele.gravity` 拿姿态。数据来自谁，主循环不用分叉。

    与串口源的两点差别：
      · 角是**弧度**，而且**已经是标定过的关节角**：`_apply()` 原样存进
        `ServoState.cal_angle` / `cal_target`，下游走 `joint_angle()` 直接透传，
        绝不再套本页的 SIGN / OFFSET（v0.10 前就是在这里叠了两层，15 个关节全翻号）。
        折回的 raw 只用于 raw 列与录制，跟串口源的物理 raw 不是一回事。
      · 逐颗的电压/温度/电流，robotd 的 robot.state 里没有（它只推整包均值与最热
        关节），所以 ServoState.live=False，界面显示 `--`、录制留空。

    只读为主：平时只 recv。`send_cmd()` 是 v0.6 加的反向命令口，能不能真发出去由
    桥那一侧的 `--allow-write` 决定（没开的话桥回一条 ok:false 的 ack，真机不动）。
    """

    def __init__(self, host: str, port: int, timeout: float = 2.0) -> None:
        self.conn: Optional[socket.socket] = socket.create_connection(
            (host, port), timeout=timeout)
        # timeout=0：调用方在 50 Hz 环里轮询，绝不能为等一帧把这一拍拍住。
        self.conn.settimeout(0.0)
        self.host, self.port = host, port
        self.pending = bytearray()

        self.frame: Optional[dict] = None
        self.states: Dict[int, ServoState] = {}
        self.missing: List[int] = []
        self.last_rx = time.perf_counter()
        self.down = False
        self.note = ""                     # 最近一次出问题的原因，给人看
        self.good = 0
        self.bad = 0
        self.hz = 0.0
        self._t_report = time.perf_counter()

        # ---- 桥的 IMU 那一面：属性名与 ImuStream 一致，主循环同一套代码读 ----
        self.attitude = Attitude(IMU_BIAS_MDPS)
        self.accel: Optional[List[float]] = None
        self.gyro: Optional[List[float]] = None
        self.gravity: List[float] = [0.0, 0.0, -1.0]
        # robotd 送过来的就是姿态本身，没有"芯片直出 / 主机滤波"之分，按直出显示
        self.hw_quat = True
        # 源里没有原始加速度计读数（只有姿态），面板据此改写那一行，别拿恒 1.000 g 骗人
        self.has_raw_accel = False

        # ---- 概览要的整包量（来自 robot.health，约 1 Hz） ----
        self.bat: Optional[float] = None
        self.tmax: Optional[float] = None
        self.thot = ""
        self.policy = ""
        self.loop_hz = 0.0
        self.missed = 0
        self.fallen = False
        self.limp = False
        self.gain: Optional[int] = None

        # ---- robotd 的 IMU 健康（v0.18）：证明姿态是**真值**而不是"假定直立" ----
        # robot.health 的 imu.ready / stale 走桥转发上来（桥 v0.3）。None = 还没收到
        # health（老桥不转发这几个键时也会一直是 None）。
        self.imu_ready: Optional[bool] = None
        self.imu_stale = 0                        # 连续"读数没刷新"块数
        self.imu_stale_total = 0
        # 冻结检测：quat 连续多少帧一模一样。robotd 的 imu_port 打不开时 quat 恒
        # [1,0,0,0]，health 的 ready 也会是 false；两个判据互为佐证。
        self._last_quat: Optional[List[float]] = None
        self.imu_frozen = 0

        # ---- 反向命令（v0.6）：桥回的 ack 走同一条 TCP，所以这里要收下来 ----
        self.ack: Optional[dict] = None           # 最近一条 ack
        self.acks: Deque[Tuple[float, dict]] = deque(maxlen=8)
        self.cmd_sent = 0
        self.cmd_err = ""                         # 本机发不出去（socket 层）的原因

    @property
    def ok(self) -> bool:
        return self.conn is not None and not self.down

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
            self.conn = None

    def poll(self) -> bool:
        """非阻塞：把已经到的字节解析掉，返回这一拍有没有拿到新帧。"""
        if self.conn is None:
            return False
        try:
            chunk = self.conn.recv(1 << 16)
        except (BlockingIOError, InterruptedError):
            return False
        except OSError as exc:
            self.down = True
            self.note = f"{type(exc).__name__}: {exc}"
            return False
        if not chunk:
            self.down = True
            self.note = "桥断了（recv 返回空）"
            return False
        self.pending.extend(chunk)
        if len(self.pending) > (1 << 20):
            # 一直没换行的垃圾：断不了句就别把内存堆爆
            self.pending.clear()
            self.bad += 1
            return False

        updated = False
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            if self._one(line):
                updated = True
        return updated

    def _one(self, line: bytes) -> bool:
        if not line.strip():
            return False
        try:
            frame = json.loads(line)
        except ValueError:
            self.bad += 1
            return False
        if not isinstance(frame, dict):
            self.bad += 1
            return False
        # 反向命令的 ack 与状态帧走同一条 TCP：它不是一帧状态，绝不能喂给 _apply
        # —— 那份 JSON 里没有 j/tg，_apply 会把 states 清成空表，界面看起来就是
        # "15 颗全掉线"。
        if "ack" in frame:
            self.ack = frame
            self.acks.append((time.perf_counter(), frame))
            # v0.16：ack 也落日志。面板状态行只显示**最近一条**，事后来问"我按了半天
            # 为什么没反应"时，日志里一条都没有可查的 —— 记一条，ok/原因都带上。
            _ok = "ok" if frame.get("ok") else "!! 被拒"
            _note = str(frame.get("note") or "")
            print(f"[真机] 桥 ack {frame.get('cmd')} {_ok}"
                  + (f"：{_note}" if _note else ""), flush=True)
            return False
        self.frame = frame
        self.last_rx = time.perf_counter()
        self.down = False
        self.good += 1
        self._apply(frame)
        return True

    def _apply(self, f: dict) -> None:
        """一帧 -> 舵机表 + 姿态 + 概览量。"""
        js = f.get("j") or []
        tgs = f.get("tg") or []
        out: Dict[int, ServoState] = {}
        missing: List[int] = []
        # JOINT_TABLE 的顺序就是 duck-control JOINT_NAMES 的顺序（左腿/颈头嘴/右腿），
        # robot.state 的 joints/targets 也按这个顺序，所以是按下标一一对应的。
        for i, (sid, _name) in enumerate(JOINT_TABLE):
            if i >= len(js):
                missing.append(sid)
                continue
            rad_now = float(js[i])
            rad_tgt = float(tgs[i]) if i < len(tgs) else rad_now
            raw = rad_to_raw(rad_now)
            target = rad_to_raw(rad_tgt)
            out[sid] = ServoState(
                sid=sid, raw=raw, speed_rpm=0.0, load_pct=0.0, volt=0.0, temp=0.0,
                status=0, moving=0, target=target, current_ma=0.0, live=False,
                # robotd 的弧度已经是标定过的关节角：原样存下来给下游用（joint_angle /
                # joint_target）。上面折出来的 raw / target 只是"角折回 raw"的伪计数，
                # 仅供显示与录制 —— 别拿它反算角，那样会再叠一遍本页标定。
                cal_angle=rad_now, cal_target=rad_tgt)
        self.states = out
        self.missing = missing

        # IMU：robotd 的 quat 就是"躯干->世界"，与本脚本 Attitude 的约定一致 ——
        # 直接写进去，不过 MOUNT、不积分、不去零偏（MOUNT 已经在 robotd 里用过了）。
        quat = f.get("quat")
        if quat and len(quat) == 4:
            q_new = [float(x) for x in quat]
            # 冻结检测（v0.18）：robotd 的 imu_port 打不开时 quat 恒 [1,0,0,0]，姿态看着
            # 正常其实没有数据。连续多少帧逐位相同就记多少。
            if self._last_quat is not None and q_new == self._last_quat:
                self.imu_frozen += 1
            else:
                self.imu_frozen = 0
            self._last_quat = q_new

            self.attitude.quat = q_new
            self.attitude.yaw = euler_xyz(self.attitude.quat)[2]
            grav = f.get("grav")
            if grav and len(grav) == 3:
                self.gravity = [float(x) for x in grav]
            else:
                # robotd 没送 gravity 就用姿态自己推：世界"下"在躯干系里的表达
                self.gravity = v_norm(
                    q_rotate_inverse(self.attitude.quat, (0.0, 0.0, -1.0)))
            gyro = f.get("gyro") or []
            self.gyro = [float(x) for x in gyro] if len(gyro) == 3 else None
            # accel 从 gravity 反推（加速度计读的是重力的反作用，躯干系里指"上"）。
            # 它只为了让"有没有 IMU 数据"这个判断和面板那条式子成立；
            # has_raw_accel=False 让面板改写"无原始加速度"，不假装量到了 |a|。
            self.accel = [-g * G0 for g in self.gravity]

        # robotd 报的 IMU 健康（桥 v0.3 才转发；老桥不给就留 None，面板照实说"未知"）
        if "imu_ready" in f:
            self.imu_ready = bool(f["imu_ready"])
        if "imu_stale" in f:
            self.imu_stale = int(f["imu_stale"])
        if "imu_stale_total" in f:
            self.imu_stale_total = int(f["imu_stale_total"])

        self.policy = str(f.get("policy") or "")
        self.loop_hz = float(f.get("hz") or 0.0)
        self.missed = int(f.get("missed") or 0)
        self.fallen = bool(f.get("fallen"))
        self.limp = bool(f.get("limp"))
        if "gain" in f:
            self.gain = int(f["gain"])
        if "bat" in f:
            self.bat = float(f["bat"])
        if "tmax" in f:
            self.tmax = float(f["tmax"])
        if "thot" in f:
            self.thot = str(f["thot"])

    def send_cmd(self, cmd: str, params: Optional[dict] = None) -> bool:
        """往桥写一条命令行（一行 JSON）。返回"本机发出去了没有"。

        真正的成败在桥回的 ack 里（`ack` 属性、`acks` 历史），这里只管 socket。
        桥没开 `--allow-write` 时也会回一条 ok:false 的 ack —— 这是故意的：面板
        能明确显示"桥拒了"，而不是静默什么都不发生。
        """
        if self.conn is None:
            self.cmd_err = "连接已关"
            return False
        msg: Dict[str, object] = {"cmd": cmd}
        if params:
            msg["params"] = params
        try:
            self.conn.sendall((json.dumps(msg, separators=(",", ":")) + "\n").encode())
        except (BlockingIOError, InterruptedError):
            self.cmd_err = "本机发送缓冲满（链路堵住了）"
            return False
        except OSError as exc:
            self.cmd_err = f"{type(exc).__name__}: {exc}"
            return False
        self.cmd_sent += 1
        self.cmd_err = ""
        return True

    def stats(self) -> Tuple[float, float]:
        """(Hz, 坏帧占比)，与 ImuStream.stats() 同签名同语义（顺带清窗）。"""
        now = time.perf_counter()
        span = now - self._t_report
        if span <= 0.0:
            return 0.0, 0.0
        hz = self.good / span
        bad_ratio = self.bad / float(self.good + self.bad) if (self.good + self.bad) else 0.0
        self.good = 0
        self.bad = 0
        self._t_report = now
        self.hz = hz
        return hz, bad_ratio


# ============================================================================
# 终端表格
# ============================================================================

def dump_table(states: Dict[int, ServoState], title: str) -> None:
    """把 15 颗的读数打出来。左腿和右腿并排，方便看镜像关系。

    腿部的"和"一列：把真机摆成左右对称的姿态时，左右同名关节应满足
    raw_L + raw_R = 4096（镜像装配）。差得远说明当时姿态本来就不对称，
    或者该关节的舵盘装偏了。
    """
    print(f"--- {title} ---"
          + ("" if all(s.live for s in states.values()) or not states
             else "  （telemetry 源：3 列无逐颗电压/温度/电流）"))
    left = [(sid, n) for sid, n in JOINT_TABLE if 20 <= sid < 30]
    right = [(sid, n) for sid, n in JOINT_TABLE if 10 <= sid < 20]
    for (lsid, lname), (rsid, rname) in zip(left, right):
        ls, rs = states.get(lsid), states.get(rsid)
        ltxt = "  --  " if ls is None else f"{ls.raw:>4}"
        rtxt = "  --  " if rs is None else f"{rs.raw:>4}"
        ssum = "" if (ls is None or rs is None) else f"  和 {ls.raw + rs.raw:>4}"
        sym = ""
        if ls is not None and rs is not None and abs(ls.raw + rs.raw - 4096) < 60:
            sym = "  ≈对称"
        volt = _volt_tail(ls)
        print(f"  {str(lname):<16}{ltxt}  |  {str(rname):<16}{rtxt}{ssum}{sym}{volt}")
    for sid, name in JOINT_TABLE:
        if not (30 <= sid < 35):
            continue
        st = states.get(sid)
        if st is None:
            continue
        label = MOUTH_NAME if name is None else name
        angle = joint_angle(st, label)
        tail = "  (XML 无关节, 3D 不显示)" if name is None else ""
        print(f"  ID {sid:>3} {label:<16}{st.raw:>4}  {angle:+.3f} rad  "
              f"{_volt_tail(st).strip()}{tail}")


def _volt_tail(st: Optional[ServoState]) -> str:
    """逐颗的电压/温度/电流尾巴。telemetry 源里没有这三样，就写 `--` 而不是 0
    （写 0 会被电压告警当成 15 颗电池全空）。"""
    if st is None:
        return ""
    if not st.live:
        return "  -- / -- / --"
    return f"  {st.volt:.1f}V {st.temp:.0f}℃ {st.current_ma:+.0f}mA"


def fmt_servo_row(name: str, sid: int, st: Optional[ServoState],
                  angle: Optional[float] = None,
                  disp: Optional[float] = None,
                  stale_s: float = 0.0) -> str:
    """面板里舵机状态表的一行（markdown 表格用）。

    `angle` 是**真实关节角**（串口源 = 本页标定后的实测角；telemetry 源 = robotd 的
    `cal_angle` 原值），`disp` 是"送进 3D 的那一份"——只有开了平滑（EMA/死区）才会
    和 `angle` 不一样（v0.18）。以前这一列直接显示平滑值、又不加区分，等于把显示量
    当真值摆出来。

    `stale_s > 0` 表示**这份数据源已经 `stale_s` 秒没有给出新值了**（v0.19）：`st` 是
    断流前最后一次读到的数，不能当"当前值"印出来 —— 整行前面加 `⏳` 并在状态列写明
    多久之前。串口让给 WSL、桥掉线、USB 松脱都走这条路。
    """
    if st is None:
        return f"| {name} | {sid} | — | — | — | — | — | ❌ 无应答 |"
    if angle is None:
        ang = "—"
    else:
        ang = f"{math.degrees(angle):+.1f}°"
        if disp is not None and abs(disp - angle) > math.radians(0.05):
            ang += f" →3D {math.degrees(disp):+.1f}°"
    if stale_s > 0.0:
        # 断流之后这一行整行都是历史值。原始 raw 照留（那是断流前真读到的），
        # 但状态列必须说明它不是当前的。
        held = f"⏳ {stale_s:.0f} s 前的读数"
        return (f"| ⏳ {name} | {sid} | {st.raw} | {ang} | — | — | — | {held} |")
    if not st.live:
        # telemetry 源：只有位置和目标，逐颗的电压/温度/电流/移动标志都没有。
        return (f"| {name} | {sid} | {st.raw} | {ang} | — | — | — |"
                " （telemetry 源） |")
    flags = []
    if not (VOLT_LO <= st.volt <= VOLT_HI):
        flags.append("电压")
    if st.temp >= TEMP_WARN:
        flags.append("温度")
    if abs(st.current_ma) >= CURR_WARN_MA:
        flags.append("电流")
    mark = f"⚠ {'/'.join(flags)}" if flags else ("动" if st.moving else "")
    return (f"| {name} | {sid} | {st.raw} | {ang} | {st.volt:.1f} V | "
            f"{st.temp:.0f} ℃ | {st.current_ma:+.0f} mA | {mark} |")


def _imu_truth_md(tele) -> str:
    """telemetry 源下，robotd 的姿态到底是不是真值 —— 一句话讲清（v0.18）。

    为什么需要这一句：`FeetechIo` 的 IMU 走**独立串口**（`[bus] imu_port`），
    `SerialCsv::open` 失败时 robotd **不报错**，只是静默地用"假定直立静止"顶上
    （main.rs 的 error 日志在 WSL 的 /tmp/robotd.log 里，面板平时看不到）。此后
    `quat` 恒 `[1,0,0,0]`、`gyro` 恒 0 —— 面板上是一个**看着完全正常**的直立姿态。
    所以这里不复述"有 IMU"，而是给判据：robotd 自己报的 `imu.ready`，加上本机看到
    的 quat 冻结帧数。
    """
    parts: List[str] = []
    if tele.imu_ready is True:
        parts.append("✅ robotd `imu.ready` 已就绪（独立串口 + 主机 Mahony）")
    elif tele.imu_ready is False:
        parts.append("❌ robotd `imu.ready` **未就绪** —— 姿态很可能是"
                     "「假定直立静止」的假值：查 `[bus] imu_port` 与 `/dev/ttyUSB*`"
                     "（掉线重接会换号）")
    else:
        parts.append("· robotd `imu.ready` 未知（桥没转发这一项：桥要 v0.3 起）")
    if tele.imu_frozen > 25:
        parts.append(f"⚠ quat 已连续 {tele.imu_frozen} 帧逐位不变 —— 冻结")
    if tele.imu_stale > 0:
        parts.append(f"⚠ robotd 报连续 {tele.imu_stale} 块读数没刷新")
    if tele.imu_stale_total > 0:
        parts.append(f"（累计未刷新 {tele.imu_stale_total} 块）")
    return "**IMU（robotd 的姿态源）** " + " · ".join(parts)


# ============================================================================
# 副本层：同一份 XML 的附加半透明网格层（目标幽灵 / 仿真预测共用）
# ============================================================================

_MESH_CACHE: Dict[int, List[Tuple[int, str, np.ndarray, np.ndarray]]] = {}


def body_meshes(model: mujoco.MjModel) -> List[Tuple[int, str, np.ndarray, np.ndarray]]:
    """把所有非固定 body 的几何合并成**局部**网格：(bid, name, verts, faces)。

    merge_geoms 合并 15 个 body 要 ~1.9 s，而台架上要建好几层副本（目标幽灵、
    仿真预测），所以按模型实例缓存，只算一次、各层共用同一批顶点。
    """
    cached = _MESH_CACHE.get(id(model))
    if cached is not None:
        return cached
    body_geoms: Dict[int, List[int]] = {}
    for gid in range(model.ngeom):
        bid = int(model.geom_bodyid[gid])
        if is_fixed_body(model, bid) or model.geom_rgba[gid, 3] == 0:
            continue
        body_geoms.setdefault(bid, []).append(gid)
    out: List[Tuple[int, str, np.ndarray, np.ndarray]] = []
    for bid, gids in body_geoms.items():
        mesh = merge_geoms(model, gids)
        out.append((bid, get_body_name(model, bid),
                    np.asarray(mesh.vertices, dtype=np.float32),
                    np.asarray(mesh.faces, dtype=np.int32)))
    _MESH_CACHE[id(model)] = out
    return out


class BodyLayer:
    """一层由 qpos 驱动的半透明副本。

    mjviser 的 ViserMujocoScene 只认一份 mjdata —— 它把每个 body 的几何合并成一条
    batched handle，一帧写一遍变换，同一场景里塞不进第二份姿态。所以这里走 viser
    原生 API 自建：几何用 body_meshes() 的**局部**网格（与实线模型逐顶点一致），
    各建一条 batched handle，路径挂在 prefix 下，与实线模型的 /bodies/ 互不冲突。

    颜色与不透明度走 batched_colors / batched_opacities（纯色），所以副本不带纹理，
    一眼就能和实线模型分开。副本只驱动关节角：base 位姿来自调用方给的 qpos。
    """

    def __init__(self, server: viser.ViserServer, model: mujoco.MjModel,
                 qadr: Dict[str, int], meshes: List[Tuple[int, str, np.ndarray, np.ndarray]],
                 prefix: str, rgb: Tuple[int, int, int], opacity: float) -> None:
        self.model = model
        self.qadr = qadr
        self.server = server
        self.data = mujoco.MjData(model)
        self.prefix = prefix
        # 实线场景的 camera tracking 会把所有 body 平移 -xpos[tracked]，副本要跟它
        # 用同一个偏移，否则会错开一个躯干的位置。tracked body 的取法与 mjviser 一致：
        # nbody 里第一个非固定 body。
        self.tracked = next((b for b in range(model.nbody)
                             if not is_fixed_body(model, b)), None)
        self.handles: List[Tuple[object, int]] = []
        with server.atomic():
            for bid, name, verts, faces in meshes:
                handle = server.scene.add_batched_meshes_simple(
                    f"{prefix}/{bid}_{name}", verts, faces,
                    batched_wxyzs=np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32),
                    batched_positions=np.zeros((1, 3), dtype=np.float32),
                    batched_colors=np.array([rgb], dtype=np.uint8),
                    batched_opacities=np.array([opacity], dtype=np.float32),
                    lod="off", cast_shadow=False, receive_shadow=False)
                self.handles.append((handle, bid))

    @property
    def body_count(self) -> int:
        return len(self.handles)

    def set_visible(self, visible: bool) -> None:
        for handle, _ in self.handles:
            handle.visible = visible

    def set_style(self, rgb: Tuple[int, int, int], opacity: float) -> None:
        for handle, _ in self.handles:
            handle.batched_colors = np.array([rgb], dtype=np.uint8)
            handle.batched_opacities = np.array([opacity], dtype=np.float32)

    def update(self, base_qpos: np.ndarray, targets: Dict[str, float],
               scene_offset: np.ndarray) -> None:
        """把 qpos 摆成 base_qpos（含基座 7 项）再盖上 targets 里的关节角，推世界变换。"""
        self.data.qpos[:] = base_qpos
        for name, angle in targets.items():
            self.data.qpos[self.qadr[name]] = angle
        # 只算运动学就够（副本不跑物理、不看接触），比 mj_forward 省。
        mujoco.mj_kinematics(self.model, self.data)
        quat = vtf.SO3.from_matrix(self.data.xmat.reshape(-1, 3, 3)).wxyz
        with self.server.atomic():
            for handle, bid in self.handles:
                handle.batched_positions = np.asarray(
                    self.data.xpos[bid] + scene_offset, dtype=np.float32)[None]
                handle.batched_wxyzs = np.asarray(quat[bid], dtype=np.float32)[None]


# ============================================================================
# 数据录制：一帧一行 CSV
# ============================================================================

def _csv_header() -> List[str]:
    """列名与 record_row 必须一一对应，所以两者挨着写、共用同一套循环。"""
    head = ["t_s", "wall", "frame"]
    for sid, name in JOINT_TABLE:
        label = MOUTH_NAME if name is None else name
        head += [f"{label}_raw", f"{label}_rad", f"{label}_tgt",
                 f"{label}_volt", f"{label}_temp", f"{label}_ma"]
    head += ["imu_w", "imu_x", "imu_y", "imu_z", "grav_x", "grav_y", "grav_z",
             # v0.18：`imu_*` 是 **IMU 报的真姿态**（与 quat 同一份数据换算而来）；
             # `base_*` 才是**3D 基座实际用的**那份（含 yaw 归零 / EMA / 修正滑块）。
             # 以前只有一列叫 roll/pitch/yaw，装的却是 base_*，拿去做分析会对不上真值。
             "imu_roll", "imu_pitch", "imu_yaw",
             "base_roll", "base_pitch", "base_yaw", "imu_hw"]
    return head


CSV_HEAD: List[str] = _csv_header()


def record_row(t_rel: float, states: Dict[int, ServoState], frame: int,
               quat: Optional[Sequence[float]], gravity: Optional[Sequence[float]],
               imu_rpy: Optional[Sequence[float]],
               base_rpy: Optional[Sequence[float]], hw_quat: bool) -> List[object]:
    row: List[object] = [f"{t_rel:.4f}",
                         datetime.now().isoformat(timespec="milliseconds"), frame]
    for sid, name in JOINT_TABLE:
        label = MOUTH_NAME if name is None else name
        st = states.get(sid)
        if st is None:
            row += ["", "", "", "", "", ""]
            continue
        if not st.live:
            # telemetry 源：电压/温度/电流三列留空，别写成 0 —— 0 V 会被当成电池空。
            row += [st.raw, f"{joint_angle(st, label):.5f}", st.target, "", "", ""]
            continue
        row += [st.raw, f"{joint_angle(st, label):.5f}", st.target,
                f"{st.volt:.2f}", f"{st.temp:.0f}", f"{st.current_ma:.1f}"]
    if quat is not None:
        row += [f"{v:.6f}" for v in quat]
    else:
        row += ["", "", "", ""]
    if gravity is not None:
        row += [f"{v:.6f}" for v in gravity]
    else:
        row += ["", "", ""]
    for rpy in (imu_rpy, base_rpy):
        if rpy is not None:
            row += [f"{v:.6f}" for v in rpy]
        else:
            row += ["", "", ""]
    row.append(1 if hw_quat else 0)
    return row


class Recorder:
    """一帧一行地写 CSV。只管写，不管录什么（列由 record_row 决定）。"""

    def __init__(self) -> None:
        self._fh = None
        self._writer: object = None
        self.path = ""
        self.rows = 0
        self.t0 = 0.0

    @property
    def on(self) -> bool:
        return self._fh is not None

    def start(self, path: str) -> str:
        self.stop()
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._fh = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(CSV_HEAD)
        self.path = path
        self.rows = 0
        self.t0 = time.perf_counter()
        return path

    def write(self, row: Sequence[object]) -> None:
        if self._writer is None:
            return
        self._writer.writerow(row)
        self.rows += 1
        # 每 100 行落一次盘：50 Hz 下就是 2 s 一刷，掉电最多丢 2 s，写盘又不会拖帧。
        if self.rows % 100 == 0:
            self._fh.flush()

    def stop(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:                          # noqa: BLE001
                pass
        self._fh = None
        self._writer = None


def solve_mouth(sample: Dict[str, int], status, sign_box: Dict[str, object],
                off_slider: Dict[str, object]) -> None:
    """用两个端点 raw 反算嘴的 sign/offset 并写回面板滑块。"""
    if "closed" not in sample or "open" not in sample:
        status.content = (
            f"已记录：闭 = {sample.get('closed', '?')}，开 = {sample.get('open', '?')}。"
            "还差一个端点")
        return
    rc, ro = sample["closed"], sample["open"]
    if rc == ro:
        status.content = "闭和开读到的 raw 一样？确认嘴有被掰动，再各点一次"
        return
    sign = -1.0 if (ro < rc) else 1.0
    # 用"开"这个点解 offset：sign * unit_angle(raw_open) + offset = MOUTH_OPEN_RAD
    offset = MOUTH_OPEN_RAD - sign * unit_angle(ro)
    # 零位滑块范围只有 ±π，超出就折掉 2π 的整数倍（对转动关节是同一个姿态）
    offset = (offset + math.pi) % (2.0 * math.pi) - math.pi
    sign_box[MOUTH_NAME].value = sign < 0
    off_slider[MOUTH_NAME].value = offset
    status.content = (
        f"解出 mouth: sign = {sign:+.1f}, offset = {offset:+.4f} rad "
        f"（闭 → {math.degrees(sign * unit_angle(rc) + offset):+.1f}°）")
    print(f'    "{MOUTH_NAME}": ({sign:+.1f}, {offset:+.4f}),', flush=True)


# ============================================================================
# 运动指令发生器：一段 50 Hz 的 14 关节目标序列
# ============================================================================

CTRL_HZ = 50.0                     # 与 robotd 的控制频率一致
CTRL_DT = 1.0 / CTRL_HZ

# 在线对比会话的帧数上限（60 s）。在线模式没有"跑完了"这件事，曲线是别人驱动的，
# 所以给个上限：撞到了就收尾并说明，免得 hist 无限涨、报告里的稳态误差变成"最后
# 一秒的均值"却看着像全程。
LIVE_MAX_FRAMES = int(60 * CTRL_HZ)

# 真机上能被**逐关节**驱动的那几个（robotd 的 robot.head 是关节空间直控）。
# 腿的 10 个关节没有逐关节接口，只能靠策略/技能整机地动，或者走仿真。
HEAD_JOINTS: Tuple[str, ...] = ("neck_pitch", "head_pitch", "head_yaw", "head_roll")


# ============================================================================
# 联调命令的一键执行（v0.8）
# ============================================================================
# 面板上那组按钮会在**本机**起进程：usbipd attach/detach（要管理员）、wsl 里起
# robotd 与桥、以及把本页自己换成 telemetry 源重启。三条都是"改了机器状态"的操作，
# 所以面板上有 gate 开关，命令也全部先校验再拼（不接受任意字符串 —— 这是给台架用的
# 按钮，不是通用远程执行口）。
WSL_DISTRO_DEFAULT = "Ubuntu"
ROBOTD_BIN_DEFAULT = "/home/liu/optiDuck/microduck/target/release/robotd"
# 记忆里的值：robotd 的 socket 与容器卡在 /tmp/robotd-c5.sock，参数文件在 E:\Temp
ROBOTD_SOCKET_DEFAULT = "/tmp/robotd-c5.sock"
ROBOTD_PARAMS_DEFAULT = "/mnt/e/Temp/realbus-robotd.toml"
# v0.21：ONNX Runtime 的动态库。WSL 里没跑过 `scripts/setup-board.sh`，所以
# `/usr/lib`、`/usr/local/lib` 都没有 `libonnxruntime.so`，`ort` 的 `ensure_runtime()`
# 一 dlopen 就失败 → 机器人 health 报 `policy unavailable: ONNX Runtime not loadable`。
# 本机那份解压在 /home/liu/ort（1.28.2，满足 `ort` 要求的 ≥1.23），只是没进搜索路径，
# 所以 ② 起 robotd 时用 `ORT_DYLIB_PATH=` 指过去即可（`ort` 读这个变量优先）。
ROBOTD_ORT_DEFAULT = "/home/liu/ort/onnxruntime-linux-x64-1.28.2/lib/libonnxruntime.so"
# v0.22：起身策略那份 .onnx。要和 `E:\Temp\realbus-robotd.toml` 的 `[policy] sitstand`
# 保持一致 —— 面板的「起身策略」开关就是往这个槽位 `robot.loadPolicy` 写路径，
# 而 robotd 只收**绝对路径**（它自己的工作目录不是调用者的）。
SITSTAND_POLICY_PATH = "/mnt/e/optiDuck/microduck_rl/standup_hd1910.onnx"
BRIDGE_LISTEN_DEFAULT = "0.0.0.0:8199"
# 只在 127.0.0.1 上听（ViserServer 的 host 就是它），所以这些按钮没有网络暴露面
DEFAULT_BRIDGE_PORT = 8199

# `pkill -f` / `pgrep -f` 认桥用的模式。**必须锚定到 cmdline 开头**（`^python3`）。
#
# 为什么不能用 `robotd_telemetry_bridge[.]py` 这种"用字符类躲开自己"的老写法：那个
# 技巧只对 ⑤ 成立（它的 cmdline 里只有带方括号的 pattern）。② 的 inner 串里还要把
# 真桥路径展开进 `nohup python3 {bridge} ...`，于是 wrapper（`bash -lc "..."`）自己的
# cmdline 里就出现了真的 `robotd_telemetry_bridge.py`，被那个正则匹配到 → pkill 把
# 自己这条 bash 杀掉，表现为 `rc=15 · 0.2 s ·（无输出）`（2026-09-30 实测复现）。
#
# 锚定开头就根治了：真桥的 argv[0] 是 `python3`（nohup exec 之后就是它），wrapper 的
# argv[0] 是 `bash`，`^python3` 永远匹配不上 wrapper —— 而且 ② 里到处都有的真路径
# 也不再是威胁，因为正则要求从 cmdline 第 0 个字符就开始匹配。
BRIDGE_PROC_RE = "^python3.*robotd_telemetry_bridge"


def _wsl_path(win_path: str) -> str:
    """`E:\\optiDuck\\tools\\x.py` -> `/mnt/e/optiDuck/tools/x.py`（拿不到盘符就原样回）。"""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", win_path)
    if not m:
        return win_path
    return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")


def _usbipd_exe() -> str:
    """usbipd 装完会写进 PATH，但本进程未必继承了新 PATH，所以按路径兜一下。"""
    for cand in (shutil.which("usbipd"),
                 r"C:\Program Files\usbipd-win\usbipd.exe"):
        if cand and os.path.exists(cand):
            return cand
    return "usbipd"


def _usbipd_rows() -> List[Tuple[str, str, str]]:
    """`usbipd list` -> [(BUSID, VID:PID, 设备名 + 状态)]，解析不了就回空表。"""
    try:
        cp = subprocess.run([_usbipd_exe(), "list"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    rows: List[Tuple[str, str, str]] = []
    for line in (cp.stdout or "").splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) >= 3 and re.match(r"^\d+-\d+$", parts[0]) \
                and re.match(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$", parts[1]):
            rows.append((parts[0], parts[1], " ".join(parts[2:])))
    return rows


def _pick_busid(rows: List[Tuple[str, str, str]], want_servo: bool) -> str:
    """按 VID:PID 认总线：CH343(1a86:55d*) 是舵机，CH340/341(1a86:7523/5523) 是 IMU。"""
    if want_servo:
        for busid, vid, _ in rows:
            if vid.lower().startswith("1a86:55"):
                return busid
    else:
        for busid, vid, _ in rows:
            if vid.lower() in ("1a86:7523", "1a86:5523"):
                return busid
    return ""


@dataclass
class Command:
    """一段 50 Hz 的关节目标序列（外加回放时的逐帧真机实测参考）。"""

    kind: str
    desc: str
    values: List[Dict[str, float]]                      # 每帧 14 关节目标角 (rad)
    real_ref: Optional[List[Optional[Dict[str, float]]]] = None

    @property
    def frames(self) -> int:
        return len(self.values)

    @property
    def duration_s(self) -> float:
        return len(self.values) * CTRL_DT

    def clamp(self, ranges: Dict[str, Tuple[Optional[float], Optional[float]]]) -> None:
        """按限位夹一下，免得指令把关节顶到限位外面。

        每一侧都可以是 `None` = **这一侧没有可信的限位，不夹**（v0.13）。为什么不是
        "没有就用 XML 兜底"：XML 的 `range` 是模型设计值，跟本页显示角的口径不一定
        对齐（实测：静止时左 hip_pitch 本页算出 −179.8°，XML 写的是 ±90°），拿它夹
        会把本来合法的指令全挡在门外。
        """
        for pose in self.values:
            for name, (lo, hi) in ranges.items():
                if name not in pose:
                    continue
                if lo is not None:
                    pose[name] = max(lo, pose[name])
                if hi is not None:
                    pose[name] = min(hi, pose[name])


def _minjerk(a: float, b: float, tau: float) -> float:
    """最小加加速度插值（两端速度/加速度都为 0），比线性更像"修订好的轨迹"。"""
    s = tau * tau * tau * (10.0 + tau * (-15.0 + 6.0 * tau))
    return a + (b - a) * s


def _blend(pose_a: Dict[str, float], pose_b: Dict[str, float],
           tau: float) -> Dict[str, float]:
    return {n: _minjerk(pose_a[n], pose_b[n], tau) for n in pose_a}


def make_step(base: Dict[str, float], joint: str, delta: float, hold_s: float,
              pre_s: float = 0.5) -> Command:
    """单关节阶跃：先保持当前姿态 pre_s，然后一步跳到 base+delta 并驻留 hold_s。

    位置执行器收到的是纯阶跃，所以这一段就是最干净的阶跃响应——稳态误差、建立时间、
    超调、饱和率都能一眼读出来。
    """
    target = dict(base)
    target[joint] = base[joint] + delta
    values = [dict(base) for _ in range(max(1, int(round(pre_s * CTRL_HZ))))]
    values += [dict(target) for _ in range(max(1, int(round(hold_s * CTRL_HZ))))]
    return Command("step", f"{joint} 阶跃 {delta:+.2f} rad，驻留 {hold_s:.1f} s", values)


def make_sine(base: Dict[str, float], joint: str, amp: float, freq_hz: float,
              duration_s: float, pre_s: float = 0.3) -> Command:
    """单关节小幅正弦：在 base 附近 ±amp 来回，用来扫频看带宽与相位滞后。"""
    values = [dict(base) for _ in range(max(1, int(round(pre_s * CTRL_HZ))))]
    for k in range(max(1, int(round(duration_s * CTRL_HZ)))):
        pose = dict(base)
        pose[joint] = base[joint] + amp * math.sin(2.0 * math.pi * freq_hz * k * CTRL_DT)
        values.append(pose)
    return Command("sine", f"{joint} 正弦 ±{amp:.2f} rad @ {freq_hz:.2f} Hz，"
                           f"{duration_s:.1f} s", values)


# 预定义姿态序列：名字 -> [(相对当前姿态的增量, 到位时间 s, 保持时间 s), ...]
# 增量是在 MuJoCo 关节系里写的，符号已按实机装配（左右腿镜像）核对过：
# 例如左 hip_pitch 取正、右 hip_pitch 取负，两只脚才会一起抬起来。
POSE_SEQUENCES: Dict[str, List[Tuple[Dict[str, float], float, float]]] = {
    "蹲起": [
        ({}, 0.0, 0.30),
        ({"left_hip_pitch": +0.35, "left_knee": -0.70, "left_ankle": +0.35,
          "right_hip_pitch": -0.35, "right_knee": +0.70, "right_ankle": -0.35},
         0.60, 0.60),
        ({}, 0.60, 0.40),
    ],
    "点头": [
        ({}, 0.0, 0.20),
        ({"neck_pitch": -0.30, "head_pitch": -0.30}, 0.40, 0.40),
        ({}, 0.40, 0.30),
    ],
    "摆头": [
        ({}, 0.0, 0.20),
        ({"head_yaw": +0.60}, 0.40, 0.50),
        ({"head_yaw": -0.60}, 0.80, 0.50),
        ({}, 0.40, 0.30),
    ],
    "抬左腿": [
        ({}, 0.0, 0.20),
        ({"left_hip_pitch": +0.45, "left_knee": -0.30, "left_ankle": +0.15}, 0.50, 0.60),
        ({}, 0.50, 0.30),
    ],
    "抬右腿": [
        ({}, 0.0, 0.20),
        ({"right_hip_pitch": -0.45, "right_knee": +0.30, "right_ankle": -0.15}, 0.50, 0.60),
        ({}, 0.50, 0.30),
    ],
}


def make_sequence(base: Dict[str, float], name: str) -> Command:
    """预定义姿态序列：段与段之间用最小加加速度插值，每段后面驻留一段。"""
    segments = POSE_SEQUENCES[name]
    values: List[Dict[str, float]] = []
    pose = dict(base)
    for delta, move_s, hold_s in segments:
        target = {n: base[n] + delta.get(n, 0.0) for n in base}
        n_move = int(round(move_s * CTRL_HZ))
        for k in range(n_move):
            values.append(_blend(pose, target, (k + 1) / max(1, n_move)))
        pose = target
        values += [dict(target) for _ in range(int(round(hold_s * CTRL_HZ)))]
    if not values:
        values = [dict(base)]
    return Command("pose", f"姿态序列「{name}」（{len(segments)} 段）", values)


def make_csv_replay(path: str, limit_s: float = 30.0) -> Command:
    """回放一份录制好的 CSV。

    命令取 `*_tgt` 列（reg42 目标位置，即真机当时收到的指令），真机参考取 `*_rad`
    列（reg56 实测）。把同一串目标喂给仿真，就得到"同一指令下模型 vs 真机"的
    逐帧对比 —— 这是本功能里最有信息量的一种，而且全程不需要真机动一下。
    """
    with open(path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        need = [f"{n}_{suf}" for n in JOINT_NAMES_MJ for suf in ("tgt", "rad")]
        missing = [c for c in need if c not in cols]
        if missing:
            raise ValueError(f"CSV 里缺列：{missing[:6]}"
                             + (f" 等 {len(missing)} 列" if len(missing) > 6 else ""))
        values: List[Dict[str, float]] = []
        refs: List[Optional[Dict[str, float]]] = []
        cap = int(max(1.0, limit_s) * CTRL_HZ)
        for row in reader:
            if len(values) >= cap:
                break
            tgt: Dict[str, float] = {}
            ref: Dict[str, float] = {}
            for n in JOINT_NAMES_MJ:
                try:
                    tgt[n] = raw_to_angle(int(float(row[f"{n}_tgt"])), n)
                    ref[n] = float(row[f"{n}_rad"])
                except (TypeError, ValueError):
                    tgt = {}
                    break
            if not tgt:
                continue                                   # 缺答的帧整行跳过
            values.append(tgt)
            refs.append(ref)
    if not values:
        raise ValueError("CSV 里没有可用的帧（列全空？）")
    return Command("csv", f"回放 {os.path.basename(path)}（{len(values)} 帧）",
                   values, refs)


# ============================================================================
# 仿真孪生：把指令喂给 MuJoCo 的 position 执行器并 mj_step
# ============================================================================

class SimTwin:
    """指令 → 仿真 → 模型预测的关节轨迹。

    台架上的仿真前提是**基座钉住**：每个子步都把基座 qpos/qvel 复位成真机当前的
    位姿 —— 等价于把机器人吊在空中、躯干姿态跟真机一致、腿悬空不接地。这样关节上
    的力矩只来自执行器、重力与关节自身的 damping/frictionloss，差异分析看到的就
    是这几项的失配，而不是"接触力猜不准"。

    一个主循环帧（50 Hz）推进 model.opt.timestep × N 个子步 = 20 ms，与真机控制
    周期一致；子步之间 ctrl 不变（零阶保持）。
    """

    def __init__(self, model: mujoco.MjModel, qadr: Dict[str, int],
                 base_adr: Optional[int]) -> None:
        self.model = model
        self.qadr = qadr
        self.base_adr = base_adr
        self.data = mujoco.MjData(model)
        self.names: Tuple[str, ...] = JOINT_NAMES_MJ
        self.act: Dict[str, int] = {}
        self.dof: Dict[str, int] = {}
        for n in self.names:
            aid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, n)
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n)
            if aid < 0 or jid < 0:
                raise ValueError(f"XML 里缺 {n} 的 actuator / joint")
            self.act[n] = int(aid)
            self.dof[n] = int(model.jnt_dofadr[jid])
        # 子步数：让一个控制周期刚好是整数个子步（timestep 0.002 -> 10 步）
        self.sub = max(1, int(round(CTRL_DT / float(model.opt.timestep))))
        self.force_limit = {n: float(abs(model.actuator_forcerange[self.act[n], 1]))
                            for n in self.names}
        self._ctrl = {n: 0.0 for n in self.names}

    def set_gravity(self, on: bool) -> None:
        self.model.opt.gravity[:] = (0.0, 0.0, -G0) if on else (0.0, 0.0, 0.0)

    def reset(self, qpos0: np.ndarray) -> None:
        self.data.qpos[:] = qpos0
        self.data.qvel[:] = 0.0
        for n in self.names:
            self._ctrl[n] = float(qpos0[self.qadr[n]])
            self.data.ctrl[self.act[n]] = self._ctrl[n]
        mujoco.mj_forward(self.model, self.data)

    def set_targets(self, targets: Dict[str, float]) -> None:
        for n in self.names:
            if n in targets:
                self._ctrl[n] = float(targets[n])
            self.data.ctrl[self.act[n]] = self._ctrl[n]

    def advance(self, base_qpos: np.ndarray) -> None:
        """推进一个 50 Hz 控制周期；每个子步都先钉住基座、再 mj_step。"""
        if self.base_adr is not None:
            for _ in range(self.sub):
                self.data.qpos[self.base_adr:self.base_adr + 7] = \
                    base_qpos[self.base_adr:self.base_adr + 7]
                self.data.qvel[self.base_adr:self.base_adr + 6] = 0.0
                mujoco.mj_step(self.model, self.data)
        else:
            for _ in range(self.sub):
                mujoco.mj_step(self.model, self.data)

    def angles(self) -> Dict[str, float]:
        return {n: float(self.data.qpos[self.qadr[n]]) for n in self.names}

    def saturated(self) -> Dict[str, bool]:
        """本帧哪些关节的执行器出力顶到了 forcerange（位置误差大到推不动）。"""
        return {n: abs(float(self.data.actuator_force[self.act[n]]))
                >= 0.98 * self.force_limit[n] for n in self.names}

    def run_series(self, values: Sequence[Dict[str, float]],
                   base_qpos: np.ndarray) -> Dict[str, np.ndarray]:
        """批量跑一串目标（系统辨识用，不渲染、不刷面板）。"""
        out = {n: np.empty(len(values), dtype=float) for n in self.names}
        self.reset(base_qpos)
        for k, tg in enumerate(values):
            self.set_targets(tg)
            self.advance(base_qpos)
            for n in self.names:
                out[n][k] = self.data.qpos[self.qadr[n]]
        return out


# ============================================================================
# 分析会话：指令 → 仿真 → 与真机逐帧对照
# ============================================================================

class Analysis:
    """一次「下发指令 → 仿真执行 → 与真机比对」的会话。

    两种模式：
      · **合成指令**（`cmd` 非空）：指令是自己造的（阶跃/正弦/姿态序列/CSV 回放），
        有确定的帧数，跑到头自动结束。真机那侧读到什么就是什么。
      · **在线对比**（v0.6，`live=True`，`cmd is None`）：指令不是造的，是**真机
        当时的每一拍目标**（telemetry 帧里的 `tg`）。仿真吃同一串目标，于是两层分开
        的那一段就是纯模型失配 —— 这时候"真机真的动过"是别人（robotd / 面板的真机
        指令）给的，不用本脚本激励。长度不固定，到 LIVE_MAX_FRAMES 自动收尾或者手停。
    """

    def __init__(self) -> None:
        self.cmd: Optional[Command] = None
        self.live = False
        self.k = 0
        self.cmd_hist: List[Dict[str, float]] = []
        self.sim_hist: List[Dict[str, float]] = []
        self.real_hist: List[Optional[Dict[str, float]]] = []
        self.sat_hist: List[Dict[str, bool]] = []
        self.qpos0: Optional[np.ndarray] = None
        self.done = False
        self.hit_cap = False                    # 在线模式撞到帧数上限（不是人停的）

    @property
    def running(self) -> bool:
        return not self.done and (self.cmd is not None or self.live)

    @property
    def desc(self) -> str:
        return self.cmd.desc if self.cmd is not None else "在线对比（仿真吃真机目标）"

    @property
    def kind(self) -> str:
        return self.cmd.kind if self.cmd is not None else "live"

    @property
    def progress(self) -> float:
        if self.cmd is None:
            return min(1.0, self.k / float(LIVE_MAX_FRAMES)) if self.live else 0.0
        if self.cmd.frames == 0:
            return 0.0
        return min(1.0, self.k / self.cmd.frames)

    def _reset(self, qpos0: np.ndarray) -> None:
        self.k = 0
        self.cmd_hist, self.sim_hist, self.real_hist, self.sat_hist = [], [], [], []
        self.qpos0 = np.array(qpos0, copy=True)
        self.done = False
        self.hit_cap = False

    def start(self, cmd: Command, qpos0: np.ndarray) -> None:
        """qpos0 = 开始那一刻真机的 qpos：仿真的初值与整个会话里钉住的基座都用它。"""
        self.cmd = cmd
        self.live = False
        self._reset(qpos0)

    def start_live(self, qpos0: np.ndarray) -> None:
        self.cmd = None
        self.live = True
        self._reset(qpos0)

    def next_targets(self) -> Optional[Dict[str, float]]:
        """合成指令模式下这一拍该喂给仿真的目标；在线模式返回 None（由主循环喂真机目标）。"""
        if not self.running or self.cmd is None:
            return None
        return self.cmd.values[min(self.k, self.cmd.frames - 1)]

    def record(self, sim: Dict[str, float], real: Optional[Dict[str, float]],
               sat: Dict[str, bool], cmd: Optional[Dict[str, float]] = None) -> None:
        if self.done:
            return
        if self.cmd is not None:
            self.cmd_hist.append(dict(self.cmd.values[self.k]))
        else:
            # 在线模式：这一拍真机收到的目标就是"指令"
            self.cmd_hist.append(dict(cmd or {}))
        self.sim_hist.append(dict(sim))
        # 回放模式用 CSV 里的真机实录；其余用现场读到的反馈
        if self.cmd is not None and self.cmd.real_ref is not None:
            self.real_hist.append(self.cmd.real_ref[min(self.k,
                                                       len(self.cmd.real_ref) - 1)])
        else:
            self.real_hist.append(dict(real) if real else None)
        self.sat_hist.append(dict(sat))
        self.k += 1
        if self.cmd is not None and self.k >= self.cmd.frames:
            self.done = True
        elif self.cmd is None and self.k >= LIVE_MAX_FRAMES:
            self.done = True
            self.hit_cap = True

    def stop(self) -> None:
        self.done = True


# ============================================================================
# 差异分析：逐关节指标 + 报告
# ============================================================================

def _lag_frames(sim: np.ndarray, real: np.ndarray, max_lag: int = 15) -> int:
    """互相关峰值的帧偏移。返回正数表示**真机滞后于仿真**。"""
    n = len(sim)
    if n < 6:
        return 0
    a = sim - sim.mean()
    b = real - real.mean()
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0
    best, best_v = 0, -np.inf
    for d in range(-max_lag, max_lag + 1):
        if d >= 0:
            x, y = a[d:], b[:n - d]
        else:
            x, y = a[:n + d], b[-d:]
        if len(x) < 4:
            continue
        v = float(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-12))
        if v > best_v:
            best_v, best = v, -d
    return best


def compare_series(a: Analysis) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    """把一次会话摊成逐关节指标。真机没被激励的关节只报仿真行程，不报误差。"""
    rows: List[Dict[str, object]] = []
    n = len(a.sim_hist)
    for name in JOINT_NAMES_MJ:
        cmd = np.array([h[name] for h in a.cmd_hist], dtype=float)
        sim = np.array([h[name] for h in a.sim_hist], dtype=float)
        real = np.array([np.nan if h is None else h[name] for h in a.real_hist],
                        dtype=float)
        ok = ~np.isnan(real)
        row: Dict[str, object] = {
            "joint": name,
            "travel_cmd": float(cmd.max() - cmd.min()) if n else 0.0,
            "travel_sim": float(sim.max() - sim.min()) if n else 0.0,
            "travel_real": float(np.nanmax(real) - np.nanmin(real)) if ok.any() else 0.0,
            "sat_pct": (100.0 * sum(1 for h in a.sat_hist if h.get(name)) / n) if n else 0.0,
            "excited": False,
        }
        if ok.sum() >= 4 and float(row["travel_real"]) > 0.02:
            err = sim[ok] - real[ok]
            row["rmse"] = float(np.sqrt(np.mean(err * err)))
            row["max_err"] = float(np.max(np.abs(err)))
            tail = err[-min(25, len(err)):]
            row["ss_err"] = float(np.mean(tail))
            row["lag_ms"] = _lag_frames(sim[ok], real[ok]) * CTRL_DT * 1000.0
            row["excited"] = True
        rows.append(row)

    excited = [r for r in rows if r["excited"]]
    summary: Dict[str, object] = {
        "frames": n,
        "duration_s": n * CTRL_DT,
        "excited": len(excited),
        "rmse_mean": (float(np.mean([float(r["rmse"]) for r in excited]))
                      if excited else None),
        "rmse_worst": (max(excited, key=lambda r: float(r["rmse"]))["joint"]
                       if excited else None),
        "sat_joints": [r["joint"] for r in rows if float(r["sat_pct"]) > 5.0],
        "max_sat_pct": max([float(r["sat_pct"]) for r in rows], default=0.0),
    }
    return rows, summary


def format_report(a: Analysis, rows: List[Dict[str, object]],
                  summary: Dict[str, object], gravity_on: bool) -> str:
    head = [
        f"# 孪生差异分析报告",
        "",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 指令：{a.kind} · {a.desc}",
        f"- 长度：{summary['frames']} 帧 / {summary['duration_s']:.1f} s"
        f"（{CTRL_HZ:.0f} Hz）"
        + ("；撞到 60 s 上限自动收尾" if a.hit_cap else ""),
    ]
    if a.live:
        head.append("- 在线对比：仿真逐帧吃的是**真机当拍目标**（telemetry 的 "
                    "targets），所以两层之差就是纯模型失配")
    head += [
        f"- 仿真条件：基座钉住（跟随真机姿态）、重力"
        f"{'开' if gravity_on else '关'}、腿悬空不接地",
        "",
        "| 关节 | 指令行程 | 仿真行程 | 真机行程 | RMSE | 峰值误差 | 稳态误差 | "
        "真机滞后 | 仿真饱和 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    body = []
    for r in rows:
        if r["excited"]:
            body.append(
                f"| {r['joint']} | {float(r['travel_cmd']):.3f} | "
                f"{float(r['travel_sim']):.3f} | {float(r['travel_real']):.3f} | "
                f"{float(r['rmse']):.4f} | {float(r['max_err']):.4f} | "
                f"{float(r['ss_err']):+.4f} | {float(r['lag_ms']):+.0f} ms | "
                f"{float(r['sat_pct']):.0f}% |")
        else:
            body.append(
                f"| {r['joint']} | {float(r['travel_cmd']):.3f} | "
                f"{float(r['travel_sim']):.3f} | — | — | — | — | — | "
                f"{float(r['sat_pct']):.0f}% |")
    tail = ["", "（rad；RMSE/峰值误差按真机被激励的帧算，行程 < 0.02 rad 的关节"
                "只列仿真行为。饱和 = 执行器出力顶到 forcerange。）", ""]
    if summary["excited"] == 0:
        tail += [
            "> ⚠ **真机没有被激励**：所有关节的实测行程都小于 0.02 rad。",
            "> 这一段指令只喂给了仿真，真机从头到尾没动。要有意义的差异分析，得让",
            "> 真机也动起来，三条路任选：",
            "> ① **面板「真机指令」**（要 telemetry 源 + 桥带 `--allow-write`）：勾上",
            ">    arm，用滑块拧 `robot.head` / `robot.mouth`，或给一点 `robot.move`；",
            "> ② **在线对比**：勾「在线对比」，再看真机被①或 robotd 自己的策略/游戏杆",
            ">    驱动 —— 仿真逐帧吃真机目标，两层之差就是纯模型失配（推荐）；",
            "> ③ **CSV 回放**：喂一段真机**真的动过**的录制，不碰硬件。",
            "",
        ]
    else:
        tail += [
            f"- 真机被激励的关节：{summary['excited']} 个；"
            f"平均 RMSE {float(summary['rmse_mean']):.4f} rad，"
            f"最大的是 `{summary['rmse_worst']}`。",
            "- RMSE 大不一定是「模型错」：先看真机有没有抖/被手挡住/电池欠压"
            "（舵机软了），再看仿真饱和率。",
            "- 稳态误差（末帧均值）偏一边 → 更像关节上的常值阻力（frictionloss）/"
            "标定零位差；RMSE 大而稳态对得上 → 更像 kp、damping、armature 量级不对。",
            "- 真机滞后明显（几十 ms）→ 真机自己的控制周期/指令链延迟，"
            "不是仿真参数问题。",
            "",
        ]
    if summary["sat_joints"]:
        tail += [f"> ⚠ 仿真里这些关节的执行器长时间饱和："
                 f"{', '.join(summary['sat_joints'])} —— 位置误差已超出 "
                 f"forcerange/kp ≈ 1.75 rad，模型在这些关节上推不动。", ""]
    return "\n".join(head + body + tail)


def format_metric_table(rows: List[Dict[str, object]],
                        summary: Dict[str, object]) -> str:
    """面板上的短表（只列有动静的关节），完整版在导出的 Markdown 里。"""
    out = ["| 关节 | RMSE | 峰值 | 稳态 | 滞后 | 饱和 |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r["excited"]:
            out.append(
                f"| {r['joint']} | {float(r['rmse']):.4f} | {float(r['max_err']):.4f} "
                f"| {float(r['ss_err']):+.4f} | {float(r['lag_ms']):+.0f} ms "
                f"| {float(r['sat_pct']):.0f}% |")
        elif float(r["travel_sim"]) > 0.02 or float(r["sat_pct"]) > 0.0:
            out.append(f"| {r['joint']} | — | — | — | — | {float(r['sat_pct']):.0f}% |")
    if summary["excited"] == 0:
        out += ["", "真机没被激励（实测行程全 < 0.02 rad），现在只看到仿真行为。"]
    return "\n".join(out)


def write_report(a: Analysis, rows: List[Dict[str, object]],
                 summary: Dict[str, object], gravity_on: bool,
                 out_dir: str = LOG_DIR) -> Tuple[str, str]:
    """把报告写成 Markdown + 逐帧明细 CSV，返回两个路径。"""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = os.path.join(out_dir, f"analysis_{stamp}.md")
    csv_path = os.path.join(out_dir, f"analysis_{stamp}.csv")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(format_report(a, rows, summary, gravity_on))
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "joint", "cmd_rad", "sim_rad", "real_rad", "err_rad", "sat"])
        for k in range(len(a.sim_hist)):
            real = a.real_hist[k]
            sat = a.sat_hist[k]
            for name in JOINT_NAMES_MJ:
                c = a.cmd_hist[k][name]
                s = a.sim_hist[k][name]
                r = real[name] if real else None
                w.writerow([f"{k * CTRL_DT:.4f}", name, f"{c:.5f}", f"{s:.5f}",
                            "" if r is None else f"{r:.5f}",
                            "" if r is None else f"{s - r:.5f}",
                            1 if sat.get(name) else 0])
    return md_path, csv_path


# ============================================================================
# 系统辨识：粗网格搜执行器参数，只出建议、不写 XML
# ============================================================================

SYSID_KP_SCALES = (0.5, 0.75, 1.0, 1.5, 2.0)
SYSID_DAMP_SCALES = (0.5, 1.0, 2.0)
SYSID_FRICTION = (0.0, 0.0048, 0.02)
SYSID_MAX_FRAMES = 300


def sysid_suggest(xml_path: str, qadr: Dict[str, int], base_adr: Optional[int],
                  a: Analysis, rows: List[Dict[str, object]], gravity_on: bool,
                  log=print) -> str:
    """对「仿真 vs 真机」的 RMSE 做粗网格搜索，给出 kp / damping / frictionloss 的建议值。

    只改候选模型（原地复制一份 XML 生成的 MjModel 数组），**不动**镜像用的模型，
    也绝不写回 XML —— 参数要不要采纳由人决定。
    """
    if not a.cmd_hist or a.qpos0 is None:
        return "还没有跑过分析会话，先「开始分析」或「在线对比」。"
    excited = [r["joint"] for r in rows if r["excited"]]
    if not excited:
        return ("真机没有被激励，没法做系统辨识。让真机真的动起来再来：面板「真机指令」"
                "拧头/嘴、或 robotd 驱动真机走一段，配合「在线对比」；也可以「CSV 回放」"
                "喂一段真机真的动过的录制。")

    n = min(len(a.cmd_hist), SYSID_MAX_FRAMES, len(a.real_hist))
    values = a.cmd_hist[:n]
    ref: Dict[str, np.ndarray] = {}
    idx: Dict[str, np.ndarray] = {}
    for name in excited:
        arr = np.array([np.nan if a.real_hist[k] is None else a.real_hist[k][name]
                        for k in range(n)], dtype=float)
        ok = ~np.isnan(arr)
        if ok.sum() < 8:
            continue
        ref[name] = arr[ok]
        idx[name] = ok
    if not ref:
        return "真机参考帧太少（<8 帧），没法拟合。"

    probe = mujoco.MjModel.from_xml_path(xml_path)
    base_kp, base_damp, dof = {}, {}, {}
    for name in JOINT_NAMES_MJ:
        aid = mujoco.mj_name2id(probe, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        jid = mujoco.mj_name2id(probe, mujoco.mjtObj.mjOBJ_JOINT, name)
        base_kp[name] = float(probe.actuator_gainprm[aid, 0])
        dof[name] = int(probe.jnt_dofadr[jid])
        base_damp[name] = float(probe.dof_damping[dof[name]])
    base_fric = float(probe.dof_frictionloss[dof[JOINT_NAMES_MJ[0]]])

    def rmse_of(ks: float, ds: float, fr: float):
        m = mujoco.MjModel.from_xml_path(xml_path)
        for name in JOINT_NAMES_MJ:
            aid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            m.dof_damping[dof[name]] = base_damp[name] * ds
            m.dof_frictionloss[dof[name]] = fr
            m.actuator_gainprm[aid, 0] = base_kp[name] * ks
            m.actuator_biasprm[aid, 1] = -base_kp[name] * ks
        m.opt.gravity[:] = (0.0, 0.0, -G0) if gravity_on else (0.0, 0.0, 0.0)
        sim = SimTwin(m, qadr, base_adr)
        out = sim.run_series(values, a.qpos0)
        total, per = 0.0, {}
        for name, r in ref.items():
            e = out[name][idx[name]] - r
            v = float(np.sqrt(np.mean(e * e)))
            per[name] = v
            total += v * v
        return math.sqrt(total / len(ref)), per

    base_rmse, base_per = rmse_of(1.0, 1.0, base_fric)
    results = []
    for ks in SYSID_KP_SCALES:
        for ds in SYSID_DAMP_SCALES:
            for fr in SYSID_FRICTION:
                if ks == 1.0 and ds == 1.0 and abs(fr - base_fric) < 1e-9:
                    results.append((base_rmse, ks, ds, fr, base_per))
                    continue
                v, per = rmse_of(ks, ds, fr)
                results.append((v, ks, ds, fr, per))
                log(f"[sysid] kp×{ks} damp×{ds} fric {fr}: RMSE {v:.4f}")
    results.sort(key=lambda r: r[0])
    best = results[0]

    out = [f"# 系统辨识建议（{datetime.now().isoformat(timespec='seconds')}）", "",
           f"- 用到的关节：{', '.join(ref)}（{n} 帧 / {n * CTRL_DT:.1f} s）",
           f"- 当前 XML 参数：kp {base_kp[JOINT_NAMES_MJ[0]]:.3f} · "
           f"damping {base_damp[JOINT_NAMES_MJ[0]]:.4f} · frictionloss {base_fric:.4f}",
           f"- 当前 RMSE：**{base_rmse:.4f} rad**（"
           + "，".join(f"{k} {v:.4f}" for k, v in sorted(base_per.items())) + "）",
           "",
           "| 排名 | kp 倍数 | damping 倍数 | frictionloss | RMSE | 相对当前 |",
           "|---|---|---|---|---|---|"]
    for i, (v, ks, ds, fr, _) in enumerate(results[:5], 1):
        gain = (base_rmse - v) / base_rmse * 100.0 if base_rmse > 1e-9 else 0.0
        out.append(f"| {i} | ×{ks:.2f} | ×{ds:.2f} | {fr:.4f} | {v:.4f} | "
                   f"{gain:+.1f}% |")
    _, ks, ds, fr, per = best
    out += ["",
            f"**建议**：`<position kp=\"{base_kp[JOINT_NAMES_MJ[0]] * ks:.3f}\" "
            f"kv=\"0.0\"/>`、`<joint damping=\"{base_damp[JOINT_NAMES_MJ[0]] * ds:.4f}\" "
            f"frictionloss=\"{fr:.4f}\"/>`（对照当前 "
            f"{base_kp[JOINT_NAMES_MJ[0]]:.3f} / {base_damp[JOINT_NAMES_MJ[0]]:.4f} / "
            f"{base_fric:.4f}）",
            "",
            "逐关节 RMSE（当前 → 建议）："
            + "，".join(f"{k} {base_per.get(k, float('nan')):.4f} → "
                        f"{per.get(k, float('nan')):.4f}" for k in ref),
            "",
            "> 这是粗网格（kp ×5 档 / damping ×3 档 / frictionloss 3 档）在**台架条件**"
            "（基座钉住、腿悬空）下拟合出来的。三点提醒：",
            "> 1. 网格点之间的差别小于测量噪声时，说明现有参数已经够用，不要为了"
            "数字好看去改；",
            "> 2. 真机的滞后里含指令链延迟，网格里没有这一项，它会系统性放大 kp 的"
            "建议值；",
            "> 3. **不自动写 XML**。要采纳请人工改，并在改完后跑一次同样的指令复核。",
            ""]
    text = "\n".join(out)
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR,
                        f"sysid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    log(f"[sysid] 报告 → {path}")
    return text + f"\n\n（已写入 `{path}`）"


# ============================================================================
# 主程序
# ============================================================================

_SINGLETON = None


def acquire_single_instance(args) -> object:
    """同一份数据源 + 同一个 viser 端口，只允许跑一个实例（v0.18）。

    为什么加这个：今天实测撞到过**两个** `bench_mirror.py` 同时在跑（一个 venv 的
    python、一个 uv 的 python），都开着 `COM8` + `8081`。后果是"改了代码没生效"——
    浏览器打开 8081 看到的可能是**旧那个进程**的画面；而且两个进程抢同一个串口，
    读还会互相打断。所以启动时先抢一个按 **viser 端口**命名的锁文件：
    抢不到就直接退出并报出是哪个进程占着，而不是默默跑起来让人对着旧画面发呆。
    """
    # 锁的粒度就是 **viser 端口**：那是真正"只能有一份"的资源（浏览器按端口找页面，
    # 第二个进程会被 viser 悄悄顶到 8082，而你还是看 8081 → 看到的是旧进程）。
    # v0.18 初版把数据源也编进键里，结果是"串口源 8081"和"telemetry 源 8081"互不
    # 认识、能同时起来 —— 那正是要防的情况，所以改成只看端口。
    key = f"viser:{args.viser_port}"
    src = f"{args.telemetry_host or args.port}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:12]
    path = os.path.join(tempfile.gettempdir(), f"bench_mirror_{digest}.lock")
    try:
        fh = open(path, "a+")
    except OSError as exc:                            # 锁文件都建不了：不拦，只提醒
        print(f"[!] 单实例锁建不了（{path}）：{exc}", file=sys.stderr)
        return True
    # 注意：Windows 上 `a+` 打开的指针在**文件末尾**，而 `msvcrt.locking` 锁的是
    # "从当前指针起"的 n 个字节 —— 不先 seek(0) 的话两份进程会各锁各的字节、
    # 谁都不拦谁（v0.18 实测踩到过：第二个实例照样起来了）。
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = ""
        try:
            fh.seek(0)
            holder = (fh.read(200) or "").strip()
        except OSError:
            pass
        print(f"[!] 已经在跑了：viser 端口 {args.viser_port} 已被另一个 bench_mirror 占用"
              + (f"（{holder}）" if holder else "") + "\n"
              f"    现在这份想用数据源 `{src}`，但同一个页面上只能有一个 —— 先关掉那个，"
              "或者换 `--viser-port`。\n"
              "    提示：`Get-CimInstance Win32_Process -Filter \"Name like '%python%'\"`",
              file=sys.stderr)
        return None
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()} src={src}")
        fh.flush()
    except OSError:
        pass
    return fh


def main() -> int:
    ap = argparse.ArgumentParser(
        description="台架只读实时镜像 / 状态看板：真机 15 颗 HD-1910 + IMU 实时映到浏览器 3D")
    ap.add_argument("--port", default=None,
                    help="舵机总线（CH343）；与 --telemetry-host 互斥，给了一个就用那个")
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--telemetry-host", default=None,
                    help="robotd telemetry 桥的 主机:端口（如 127.0.0.1:8199）。"
                         "给了它就不开 COM8/COM6 —— robotd 拿着串口时用这条")
    ap.add_argument("--imu-port", default="COM6", help="IMU 桥（CH340 / STM32）")
    ap.add_argument("--no-imu", action="store_true", help="不接 IMU，基座用手工滑块")
    ap.add_argument("--no-ghost", action="store_true",
                    help="不做「目标幽灵」层（少一层网格、启动快一点）")
    ap.add_argument("--viser-port", type=int, default=8080)
    ap.add_argument("--show", type=int, default=3, help="前 N 帧打印逐关节原始值")
    ap.add_argument("--frames", type=int, default=0, help="跑够 N 帧就退出（0=不限）")
    ap.add_argument("--hz", type=float, default=50.0,
                    help="推送浏览器的刷新率上限（0=不限）。50 与 robotd 控制频率一致")
    args = ap.parse_args()

    # 数据源三选一：串口（默认 COM8）、telemetry 桥、或都不给（只看面板）。两个都给
    # 是配置错误而不是"优先级问题"—— COM8 是独占的，同时开就是骗自己。
    if args.port and args.telemetry_host:
        print("[!] --port 与 --telemetry-host 只能给一个（COM8 是独占的：robotd 拿着"
              "串口时本脚本读不到舵机，要走 telemetry 桥）", file=sys.stderr)
        return 2
    if args.port is None and args.telemetry_host is None:
        args.port = "COM8"                 # 老用法（不带参数）仍然是串口源

    # 单实例：同一份数据源 + 同一个 viser 端口只允许跑一个（见 acquire_single_instance）
    global _SINGLETON
    _SINGLETON = acquire_single_instance(args)
    if _SINGLETON is None:
        return 3

    # 上次「保存标定」落盘的 JSON 先读回来：它在 SIGN/OFFSET 之外，必须在建面板之前
    # 盖到 CALIB/BASE_RPY 上，滑块的初值才是上次调好的那一组。
    restored = load_saved_calib()
    if restored:
        # SIGN/OFFSET 是 import 时从 CALIB 展开的，CALIB 被改了就得重新展开一次。
        for name in CALIB_NAMES:
            SIGN[name], OFFSET[name] = CALIB[name]
        print(f"已读回标定：{restored}", flush=True)

    # 实测限位也要在建面板之前读回来：头/颈滑条的量程就是按它（或 XML）定的
    meas_limits, lim_note = load_meas_limits()
    if lim_note:
        print(f"已读回实测限位：{lim_note}", flush=True)

    model = mujoco.MjModel.from_xml_path(XML)
    data = mujoco.MjData(model)

    # 关节名 -> qpos 下标；XML 里没有的关节（嘴）记 None
    qadr: Dict[str, int] = {}
    for _, name in JOINT_TABLE:
        if name is None:
            continue
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if jid < 0:
            print(f"[!] XML 里找不到关节 {name}", file=sys.stderr)
            return 1
        qadr[name] = int(model.jnt_qposadr[jid])

    # 躯干 freejoint：台架镜像不跑物理，基座位姿由 IMU（或面板滑块）给
    base_jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "trunk_base_freejoint")
    base_adr = int(model.jnt_qposadr[base_jid]) if base_jid >= 0 else None

    # ---- telemetry 模式下先连桥：它一个人顶掉 IMU + 总线两样 ----
    # 连接失败就直接退出，**不静默回退到滑块** —— 用户是特意指明"数据从 robotd 来"的，
    # 悄悄退化成空面板比报错更误导。
    tele: Optional[TelemetryClient] = None
    if args.telemetry_host is not None:
        t_host, _, t_port_s = args.telemetry_host.rpartition(":")
        try:
            tele = TelemetryClient(t_host or "127.0.0.1", int(t_port_s or "8199"))
        except Exception as exc:                     # noqa: BLE001
            print(f"[!] telemetry 桥 {args.telemetry_host} 连不上：{exc}", file=sys.stderr)
            print("    WSL 那边先起："
                  "`python3 tools/robotd_telemetry_bridge.py --listen 0.0.0.0:8199`",
                  file=sys.stderr)
            return 1
        print(f"telemetry 桥 {args.telemetry_host} 已连上（只读；COM8 归 robotd）", flush=True)

    # ---- IMU 先开：它决定了面板上基座那三个滑块的语义 ----
    imu: Optional[ImuStream] = None
    imu_error = ""
    if tele is not None:
        # telemetry 模式：姿态也从桥来。TelemetryClient 的 IMU 面与 ImuStream 同名同义，
        # 主循环那段 `imu.poll()/imu.attitude/imu.gravity` 一个字都不用改。
        imu = tele                                   # type: ignore[assignment]
    elif not args.no_imu:
        try:
            imu = ImuStream(args.imu_port)
            print(f"IMU {args.imu_port} @ 115200 已打开（只读）", flush=True)
        except Exception as exc:                     # noqa: BLE001 — 接不上不该挡住建界面
            imu_error = str(exc)
            imu = None
            print(f"[!] IMU {args.imu_port} 打不开：{exc}", file=sys.stderr)
            print("    基座姿态改用手工滑块；要接 IMU 先 `usbipd detach --busid 4-2`",
                  file=sys.stderr)

    server = viser.ViserServer(host="127.0.0.1", port=args.viser_port)
    scene = ViserMujocoScene(server, model, num_envs=1)
    # 地面：这个 XML 是纯机器人模型，没有 floor geom，3D 里原本是空的。这里直接补一层
    # 无限网格（参数照抄 mjviser scene.py::_add_fixed_geometry 里对 PLANE geom 的画法，
    # 所以观感与"XML 里真有地面"一致）。
    server.scene.add_grid("/ground", infinite_grid=True, fade_distance=50.0,
                          shadow_opacity=0.2, plane_opacity=0.4,
                          position=(0.0, 0.0, 0.0))
    # 8080 被占用时 viser 会自动跳到下一个空闲端口，所以回报实际端口而不是请求的
    print(f"viser: http://127.0.0.1:{server.get_port()}  (浏览器打开)", flush=True)

    # ---- 附加网格层：目标幽灵（橙）由 reg42 驱动，仿真预测（青）由 SimTwin 驱动 ----
    # 两层的网格来自同一份 body_meshes() 缓存，merge_geoms 只跑一次。
    meshes = body_meshes(model)
    ghost: Optional[BodyLayer] = None
    if not args.no_ghost:
        ghost = BodyLayer(server, model, qadr, meshes, "/ghost_bodies",
                          GHOST_RGB, GHOST_OPACITY)
        print(f"目标幽灵层：{ghost.body_count} 个 body（由 reg42 目标位置驱动）", flush=True)

    # ---- 概览 ----
    with server.gui.add_folder("概览"):
        conn_md = server.gui.add_markdown("启动中…")
        bus_md = server.gui.add_markdown("等待第一帧…")
        alarms_md = server.gui.add_markdown("暂无告警")

    # ---- 工具：所有"能在页面上开关的功能"集中在这一格，展开着，一眼看到状态 ----
    # 这些开关原先散在各自的折叠面板里（幽灵 / 录制 / 分析 / 基座），要么得翻两层才
    # 找得到，要么根本不在这页上。放最上面一份，删掉各自的副本，避免两处状态打架。
    with server.gui.add_folder("工具"):
        rec_on = server.gui.add_checkbox("● 录制到 CSV", initial_value=False)
        ghost_on = server.gui.add_checkbox("显示目标幽灵（橙）",
                                           initial_value=ghost is not None)
        sim_vis = server.gui.add_checkbox("显示仿真层（青）", initial_value=False)
        follow_imu = server.gui.add_checkbox("基座跟随 IMU",
                                             initial_value=imu is not None)
        base_raw = server.gui.add_checkbox("基座用原值（不归零 / 不平滑 / 不叠修正滑块）",
                                           initial_value=False)
        server.gui.add_markdown(
            "**青（仿真）与实线（实测）本来就应该重合** —— 不跑分析时青色直接镜像"
            "真机姿态，用来验证 XML 模型与装出来的实机是不是一个姿态；只有按了"
            "「开始分析」，青色才按仿真自己走、与实线分开（分开的那一段才是有含义的"
            "预测偏差）。\n\n"
            "勾上「基座用原值」后，3D 基座直接用 IMU 的四元数 —— telemetry 源下就是 "
            "robotd 报的原值，不再做启动 yaw 归零、up 向量 EMA、也不再叠「基座姿态」"
            "那三个修正滑块。想核对仿真与真机的**绝对**姿态时开它。\n\n"
            "其余设置还在各面板里：`录制` 改路径、`目标幽灵` 改颜色/透明度、"
            "`分析` 选指令、`基座姿态` 手工修正、`逐关节标定` 拧零位、"
            "`关节实测限位` 记实物行程。")

    # ---- 双模型 ----
    with server.gui.add_folder("目标幽灵", expand_by_default=False):
        server.gui.add_markdown(
            "橙色半透明 = **目标姿态**（舵机 reg42 目标位置）；"
            "不透明实线 = **实测姿态**（reg56 反馈）。\n\n"
            "两者分开就看得到跟随误差：卡滞、扭矩不足、目标跳变都会在这里露出来。"
            + ("" if ghost is not None else "\n\n（本次用 `--no-ghost` 启动，没有这一层）"))
        ghost_op = server.gui.add_slider("幽灵不透明度", 0.05, 1.0, 0.05, GHOST_OPACITY)
        ghost_rgb = server.gui.add_rgb("幽灵颜色", initial_value=GHOST_RGB)

    # ---- 录制 ----
    rec_default = os.path.join(
        LOG_DIR, f"bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    recorder = Recorder()
    with server.gui.add_folder("录制", expand_by_default=False):
        server.gui.add_markdown(
            "一帧一行：15 颗的 raw/角度/目标/电压/温度/电流 + IMU 四元数/gravity + "
            "**真姿态欧拉角（`imu_*`）与 3D 基座实际用的姿态（`base_*`，两份分开列）** "
            "+ 时间戳。50 Hz 下约 1 MB / 20 s。")
        rec_path = server.gui.add_text("CSV 路径", initial_value=rec_default)
        # 录制开关（rec_on）在顶部的「工具」面板里 —— 这里只留路径和状态，开关不重复
        rec_md = server.gui.add_markdown("未录制")

    # ---- 基座姿态 ----
    # IMU 接上时三个滑块是"修正量"（初值 0）；没接上就是绝对姿态（初值 = 昨天对着
    # 实物调好的那一组）。这样同一套控件两种语义，不用两套面板。
    base_init = (0.0, 0.0, 0.0) if imu is not None else tuple(BASE_RPY)
    with server.gui.add_folder("基座姿态"):
        base_md = server.gui.add_markdown(
            "IMU 已接入：3D 基座跟着真机倾。" if imu is not None else
            f"IMU 未接入（{imu_error or '--no-imu'}）：手工把 3D 摆成和真机同向")
        # v0.18：把这句说清楚 —— 这一格里的滑块/归零/EMA 全是**显示用**的，改不动 robotd
        base_truth_md = server.gui.add_markdown(
            "⚠ **本格的滑块 / 姿态归零 / up 向量 EMA 只影响这一页的 3D 显示**，"
            "不会下发给 robotd，也不改任何寄存器。板上真正跑策略用的姿态由 robotd "
            "自己那份融合给出（telemetry 源下就是它报的 `quat`）。想看没有显示变换的"
            "绝对真值，勾「工具」里的**基座用原值**。")
        # 「基座跟随 IMU」（follow_imu）在顶部的「工具」面板里
        roll_h = server.gui.add_slider("roll (rad)", -math.pi, math.pi, 0.01, base_init[0])
        pitch_h = server.gui.add_slider("pitch (rad)", -math.pi / 2, math.pi / 2, 0.01,
                                        base_init[1])
        yaw_h = server.gui.add_slider("yaw (rad)", -math.pi, math.pi, 0.01, base_init[2])
        zero_btn = server.gui.add_button("姿态归零（把当前朝向定为 0）")
        imu_md = server.gui.add_markdown("gravity = —")

    # ---- 舵机状态 ----
    with server.gui.add_folder("舵机状态"):
        health_md = server.gui.add_markdown("等待第一帧…")
        table_md = server.gui.add_markdown("等待第一帧…")

    # ---- 显示：平滑 ----
    with server.gui.add_folder("显示", expand_by_default=False):
        server.gui.add_markdown(
            "数值平滑：0 = 原值直通。抖动明显时先把死区调到 ~0.002 rad，"
            "还抖再给一点 alpha。改完不影响标定。")
        smooth_h = server.gui.add_slider("关节 EMA alpha", 0.0, 1.0, 0.05, 0.0)
        dead_h = server.gui.add_slider("关节死区 (rad)", 0.0, 0.02, 0.001, 0.0)
        grav_h = server.gui.add_slider("IMU gravity EMA alpha", 0.0, 1.0, 0.05, 0.35)

    # ---- 逐关节标定 ----
    sign_box: Dict[str, object] = {}
    off_slider: Dict[str, object] = {}
    with server.gui.add_folder("逐关节标定", expand_by_default=False):
        server.gui.add_markdown(
            "方向反了就勾『反向』；整体差固定角度就调『零位』。"
            "最后一项 mouth 是嘴：XML 无关节、3D 里看不到，只存标定值")
        # v0.18：这里的关系必须讲明白，否则容易以为"在面板上拧一下就把机器人标定好了"
        server.gui.add_markdown(
            "⚠ **本页的 sign/零位（还有 IMU 的 MOUNT/零偏）只用于这一页把 raw 换算成"
            "关节角去驱动 3D**，不会下发给 robotd、也不写舵机寄存器。\n\n"
            "· **telemetry 源**：关节角是 robotd 已经标定好的 `cal_angle`，本页**原样"
            "透传**（这里拧滑块只改 3D 显示，看不出「标定」效果）\n"
            "· **串口源**：本页自己按下面的 sign/零位换算 —— 这是本页的口径，robotd "
            "驱动真机时用的是它自己那份标定，两者不共享\n"
            "· 面板上的 IMU `MOUNT` / 零偏与 robotd 的 `imu_csv.rs` 取值**一致**"
            "（同一套实测标定），所以串口源下本页算出的姿态和 robotd 是同一份答案")
        for name in CALIB_NAMES:
            sign, offset = CALIB[name]
            sign_box[name] = server.gui.add_checkbox(f"{name} 反向", sign < 0)
            off_slider[name] = server.gui.add_slider(
                f"{name} 零位 (rad)", -math.pi, math.pi, 0.005, offset)

    dump_btn = server.gui.add_button("打印标定快照")
    save_btn = server.gui.add_button("保存标定到 JSON（下次启动自动读回）")
    save_md = server.gui.add_markdown("")

    @save_btn.on_click
    def _save(_event) -> None:
        """面板上的值落盘。注意基座姿态只在 IMU 未接入时才写 —— IMU 接入时那三个
        滑块是"修正量"，把它们当成绝对姿态存下去会把没接 IMU 时的默认朝向带歪。"""
        calib = {name: (-1.0 if sign_box[name].value else 1.0, off_slider[name].value)
                 for name in CALIB_NAMES}
        rpy = (roll_h.value, pitch_h.value, yaw_h.value) if imu is None else BASE_RPY
        try:
            save_calib(CALIB_JSON, calib, rpy)
        except Exception as exc:                      # noqa: BLE001
            save_md.content = f"❌ 写不了 {CALIB_JSON}：{exc}"
            return
        save_md.content = f"✅ 已写入 `{CALIB_JSON}`" + (
            "" if imu is None else "（IMU 已接入，基座姿态沿用上次的绝对值）")
        print(f"[标定] 已落盘 {CALIB_JSON}", flush=True)

    @dump_btn.on_click
    def _dump(_event) -> None:
        """把当前面板上的值打成可直接粘回本文件的代码。"""
        out = ["CALIB = {"]
        for name in CALIB_NAMES:
            sign = -1.0 if sign_box[name].value else 1.0
            out.append(f'    "{name}": ({sign:+.1f}, {off_slider[name].value:+.4f}),')
        out.append("}")
        out.append(f"BASE_RPY = [{roll_h.value:+.4f}, {pitch_h.value:+.4f}, "
                   f"{yaw_h.value:+.4f}]")
        print("\n".join(out), flush=True)

    # ---- 嘴的两点标定：3D 里看不到，用两个端点 raw 反算 sign/offset ----
    with server.gui.add_folder("嘴 / 两点标定", expand_by_default=False):
        server.gui.add_markdown(
            "把嘴掰到**全闭**点一次『闭』、再掰到**全开**点一次『开』。"
            "标定约定：闭 = −5°、开 = +30°（与 robotd 一致）。")
        capture_closed = server.gui.add_button("记下当前为「全闭」")
        capture_open = server.gui.add_button("记下当前为「全开」")
        mouth_status = server.gui.add_markdown("尚未记录端点")
    mouth_sample: Dict[str, int] = {}

    # 最近一帧的读数（主循环更新，按钮回调也读它，所以先建好再注册回调）
    states: Dict[int, ServoState] = {}

    @capture_closed.on_click
    def _cap_closed(_event) -> None:
        st = states.get(MOUTH_ID)
        if st is None:
            mouth_status.content = f"没读到嘴（ID {MOUTH_ID} 缺失），稍后再点"
            return
        mouth_sample["closed"] = st.raw
        solve_mouth(mouth_sample, mouth_status, sign_box, off_slider)

    @capture_open.on_click
    def _cap_open(_event) -> None:
        st = states.get(MOUTH_ID)
        if st is None:
            mouth_status.content = f"没读到嘴（ID {MOUTH_ID} 缺失），稍后再点"
            return
        mouth_sample["open"] = st.raw
        solve_mouth(mouth_sample, mouth_status, sign_box, off_slider)

    yaw_zero = [0.0]

    @zero_btn.on_click
    def _zero(_event) -> None:
        yaw_zero[0] = imu.attitude.yaw if imu is not None else 0.0
        roll_h.value = 0.0
        pitch_h.value = 0.0
        yaw_h.value = 0.0
        print(f"[姿态归零] yaw 零点 = {yaw_zero[0]:+.3f} rad", flush=True)

    # ---- 幽灵层：只改颜色/透明度/可见性，不走主循环 ----
    def _ghost_style(_event=None) -> None:
        if ghost is not None:
            ghost.set_style(tuple(int(c) for c in ghost_rgb.value),
                            float(ghost_op.value))

    @ghost_on.on_update
    def _ghost_vis(_event) -> None:
        if ghost is not None:
            ghost.set_visible(ghost_on.value)

    ghost_op.on_update(_ghost_style)
    ghost_rgb.on_update(_ghost_style)

    # ---- 录制开关 ----
    @rec_on.on_update
    def _rec_toggle(_event) -> None:
        if rec_on.value:
            try:
                path = recorder.start(rec_path.value.strip())
            except Exception as exc:                  # noqa: BLE001 — 路径写错不该崩界面
                rec_on.value = False
                rec_md.content = f"❌ 开不了文件：{exc}"
                print(f"[!] 录制失败：{exc}", file=sys.stderr)
                return
            rec_md.content = f"● 录制中 → `{path}`"
            print(f"[录制] 开始 → {path}", flush=True)
        else:
            rows = recorder.rows
            recorder.stop()
            rec_md.content = (f"已停止，{rows} 行 → `{recorder.path}`" if rows
                              else "未录制")
            print(f"[录制] 停止，共 {rows} 行", flush=True)

    # ========================================================================
    # 分析模式：指令 → 仿真执行 → 与真机逐帧对照 → 出报告 / 参数建议
    # ========================================================================
    sim_twin = SimTwin(model, qadr, base_adr)
    sim_layer = BodyLayer(server, model, qadr, meshes, "/sim_bodies",
                          SIM_RGB, SIM_OPACITY)
    sim_layer.set_visible(False)
    analysis = Analysis()
    # 面板上的分析状态要在按钮回调与主循环之间共享，用 list 当可变盒子
    an_announced = [True]                     # 完成提示只打一次
    sim_shown = [False]                       # 仿真层是否正在推位置
    # ---- 限位（v0.13 重做）：分三个来源，**只有可信的来源才判超限、才夹指令** ----
    # 为什么重做：XML 的 `range` 是 MuJoCo 模型的设计值，跟本页显示角的口径**不一定
    # 对齐**。实测证据（2026-09-30 用户报"很多关节默认正常却显示超限"，当场从孪生日志
    # 的 reg56 逐颗回算）：真机静止时 left_hip_pitch 本页算出 −179.8°、left_knee
    # −92.4°，而 XML 写的是 ±90° —— 每帧都"超限"；同一份标定下 neck_pitch +25.3°、
    # head_pitch +17.3° 却都在 XML 范围内。所以腿这一路的 XML 数字**不能当限位用**。
    #
    # 三个来源，可信度从高到低：
    #   实测（推到机械死点记下来的角，面板记）> 观测（跑动中学到的 min/max，一键记）
    #   > XML（设计值 —— **只拿来做滑条量程的兜底，不判超限、不夹指令**）
    # `jnt_range`  头/颈滑条的量程（恒有值，缺的一侧用 XML 兜底，否则滑条没有量程）
    # `real_lim`   每一侧**可信**的限位；None = 这一侧没有，不判也不夹
    jnt_range: Dict[str, Tuple[float, float]] = {}
    real_lim: Dict[str, List[Optional[float]]] = {n: [None, None] for n in JOINT_NAMES_MJ}
    xml_range: Dict[str, Tuple[float, float]] = {}
    lim_src: Dict[str, str] = {}
    for name in JOINT_NAMES_MJ:
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        lo, hi = model.jnt_range[jid]
        xml_range[name] = (float(lo), float(hi))
        jnt_range[name] = (float(lo), float(hi))
        lim_src[name] = "XML（设计值）"

    # 观测行程：主循环每拍记一次 min/max。攒一会儿按面板上的按钮就能得到"在这台实物
    # 上、本页口径下真的走到过哪儿" —— 不用逐颗推死点，也不会像 XML 那样和显示角打架。
    # 开机即开始学；换姿势 / 换装配后按「清空观测窗」重来。
    obs_seen: Dict[str, List[float]] = {n: [math.inf, -math.inf] for n in JOINT_NAMES_MJ}

    def _lim_apply(name: str) -> None:
        """重算这一颗的限位（实测 / 观测 记在 meas_limits 里，XML 只是兜底）。"""
        rec = meas_limits.get(name) or {}
        lo = rec.get("lo")
        hi = rec.get("hi")
        lo = None if lo is None else float(lo)
        hi = None if hi is None else float(hi)
        if lo is not None and hi is not None and lo >= hi:
            print(f"[!] {name} 的限位不合法（lo {lo:+.3f} ≥ hi {hi:+.3f}），这颗不判也不夹",
                  flush=True)
            lo = hi = None
        real_lim[name] = [lo, hi]
        # 滑条量程：可信的一侧用可信值，另一侧退回 XML（只是为了滑条能拖，不代表限位）
        xlo, xhi = xml_range[name]
        jnt_range[name] = (lo if lo is not None else xlo, hi if hi is not None else xhi)
        lim_src[name] = (rec.get("src") or "实测") if (lo is not None or hi is not None) \
            else "XML（设计值）"

    for _name in list(meas_limits):
        _lim_apply(_name)

    def _limits_table() -> str:
        """限位面板的表：这一页按的行程 + 当前角 + 距最近限位还有多少。

        超限只在这里标 ⚠（只读告警），**不去夹 3D** —— 3D 的职责是如实反映真机，
        夹住了就看不见真机到底跑到哪儿去了。
        """
        rows = ["| 关节 | 限位下限 | 限位上限 | 当前角 | 余量 | 来源 | 观测到的行程 |",
                "|---|---|---|---|---|---|---|"]
        for name in JOINT_NAMES_MJ:
            lo, hi = jnt_range[name]          # 滑条量程（可能含 XML 兜底）
            rlo, rhi = real_lim[name]         # 可信的那一侧，None = 没有
            st = states.get(SID_OF[name])
            cur = None if st is None else joint_angle(st, name)
            lo_txt = f"{math.degrees(lo):+.1f}°" + ("" if rlo is not None else "*")
            hi_txt = f"{math.degrees(hi):+.1f}°" + ("" if rhi is not None else "*")

            # 观测行程：这一页口径下、这台实物真的走到过哪儿
            olo, ohi = obs_seen[name]
            obs_txt = ("还没学到" if not math.isfinite(olo)
                       else f"{math.degrees(olo):+.1f} ~ {math.degrees(ohi):+.1f}°")

            if cur is None:
                rows.append(f"| {name} | {lo_txt} | {hi_txt} | — | — |"
                            f" {lim_src[name]} | {obs_txt} |")
                continue

            # 余量只按**可信的**那一侧判：XML 兜底的那侧不判超限（口径不保证一致）
            over: List[str] = []
            mar = math.inf
            if rlo is not None:
                if cur < rlo:
                    over.append(f"⚠ 低于下限 {math.degrees(rlo - cur):.1f}°")
                mar = min(mar, cur - rlo)
            if rhi is not None:
                if cur > rhi:
                    over.append(f"⚠ 高于上限 {math.degrees(cur - rhi):.1f}°")
                mar = min(mar, rhi - cur)
            if over:
                mar_txt = " ".join(over)
            elif math.isinf(mar):
                mar_txt = "—（无可信限位，不判）"
            else:
                mar_txt = f"{math.degrees(mar):+.1f}°"
            rows.append(f"| {name} | {lo_txt} | {hi_txt} |"
                        f" {math.degrees(cur):+.1f}° | {mar_txt} |"
                        f" {lim_src[name]} | {obs_txt} |")
        rows += ["", "`*` = 这一侧没有实测 / 观测值，显示的是 **XML 的设计值** —— 它跟本页"
                     "显示角的口径不保证一致（实测：静止时 `left_hip_pitch` 本页算 "
                     "`−179.8°`，XML 写的是 `±90.0°`），所以 **不拿它判超限、也不拿它夹"
                     "指令**。可信限位只有两个来源：手动「记下限/上限」（推死点）和"
                     "「把观测行程记为限位」。",
                     "", "`—` = 这一帧没读到。"]
        return "\n".join(rows)

    with server.gui.add_folder("关节实测限位", expand_by_default=False):
        server.gui.add_markdown(
            "**先说清楚为什么以前「默认姿势却显示超限」**（v0.13 修）：XML 里的 `range` 是"
            "MuJoCo 模型的**设计值**，跟本页显示角的口径不保证对齐 —— 实测证据：真机静止"
            "时 `left_hip_pitch` 本页算 `−179.8°`、`left_knee` `−92.4°`，而 XML 写的是 "
            "`±90°`；同一份标定下 `neck_pitch` `+25.3°`、`head_pitch` `+17.3°` 却都在范围内。"
            "所以现在 **XML 只拿来当滑条量程的兜底，不判超限、也不夹指令**（表里带 `*` 的"
            "那一侧就是它）。\n\n"
            "**可信限位有两个来源**，按可信度排：\n"
            "1. **实测**（推死点，最权威）：**先让关节自由** —— 断电，或按「联调命令」里的 "
            "⑥（停 robotd 后直写 reg40=0；⚠ **别用 `robot.relax`**：robotd 跑着时它 "
            "写的 0 下一拍就被 reg42 翻回来，v0.20 实测）—— 带扭矩硬推会顶坏舵机 / "
            "烧驱动。然后用**手**把目标关节**慢慢**推到一端的"
            "死点，凭手感停 → 选好这颗关节按「记下限」；反方向再来一次按「记上限」。"
            "落盘 `tools/bench_joint_limits.json`，合成指令的夹取立刻按新值；"
            "头/颈滑条量程下次启动生效。\n"
            "2. **观测**（一键，最省事）：这一页每拍都在记每颗关节的 min/max，跑一会儿"
            "（或让策略带着动一段）后按「**把观测行程记为限位**」，整批按 ±3° 放宽写进去。"
            "不是死点，但**和显示角同口径**，判超限不会误报。\n\n"
            "两侧都记完才两侧都判；只记了一侧就只判那一侧。任何一侧都可以按"
            "「清除这颗的实测限位」退回不判。张嘴是 `0–1` 开合，不走这套弧度限位。\n\n"
            "⚠ 桥那侧的下发校验仍按 XML 量程硬编码：实测 / 观测限位若比 XML **宽**，"
            "超出的那一段会被桥拒（只影响头/颈的下发，腿没有逐关节下发接口）。")
        lim_joint = server.gui.add_dropdown("关节", JOINT_NAMES_MJ,
                                            initial_value=JOINT_NAMES_MJ[0])
        lim_rec_lo = server.gui.add_button("记当前角为「下限」（负方向死点）")
        lim_rec_hi = server.gui.add_button("记当前角为「上限」（正方向死点）")
        lim_clear = server.gui.add_button("清除这颗的实测限位")
        lim_learn = server.gui.add_button("把观测行程记为限位（整批，两侧各放宽 3°）")
        lim_obs_reset = server.gui.add_button("清空观测窗（换姿势/换装配后重学）")
        lim_md = server.gui.add_markdown(_limits_table())

    def _lim_record(side: str) -> None:
        name = lim_joint.value
        st = states.get(SID_OF[name])
        if st is None:
            lim_md.content = f"没读到 {name}（ID {SID_OF[name]}），稍后再点"
            return
        angle = joint_angle(st, name)
        rec = meas_limits.get(name) or {}
        other = "hi" if side == "lo" else "lo"
        if other in rec:
            # 新记的这一侧要跟已记的那一侧拉开一段，否则等于同一处记了两遍
            span = angle - rec[other] if side == "hi" else rec[other] - angle
            if span < LIM_MIN_SPAN:
                lim_md.content = (
                    f"❌ {name}：这一侧 {math.degrees(angle):+.1f}° 与已记的"
                    f"「{'上限' if side == 'lo' else '下限'}」"
                    f"{math.degrees(rec[other]):+.1f}° 只差 {math.degrees(span):+.1f}°，"
                    f"不到 {math.degrees(LIM_MIN_SPAN):.1f}° —— 是不是同一处点了两次？"
                    f"要重记先「清除这颗的实测限位」")
                return
        meas_limits.setdefault(name, {})[side] = angle
        meas_limits[name]["src"] = "实测"      # 手动推死点记的，来源标死在文件里
        _lim_apply(name)
        try:
            save_meas_limits(LIMITS_JSON, meas_limits)
        except Exception as exc:                      # noqa: BLE001
            lim_md.content = (f"⚠ 记下了（{name} "
                              f"{'下限' if side == 'lo' else '上限'} = "
                              f"{math.degrees(angle):+.1f}°）但写不了 {LIMITS_JSON}：{exc}")
            return
        print(f"[限位] {name} {'下限' if side == 'lo' else '上限'} = "
              f"{math.degrees(angle):+.2f}°（{angle:+.5f} rad）→ {LIMITS_JSON}", flush=True)
        lim_md.content = _limits_table()

    def _lim_clear(_event) -> None:
        name = lim_joint.value
        if meas_limits.pop(name, None) is None:
            lim_md.content = f"{name} 本来就没有实测限位（用的是 XML）"
            return
        _lim_apply(name)
        try:
            save_meas_limits(LIMITS_JSON, meas_limits)
        except Exception as exc:                      # noqa: BLE001
            lim_md.content = f"⚠ 清掉了但写不了 {LIMITS_JSON}：{exc}"
            return
        print(f"[限位] 清除 {name} 的实测限位，退回 XML", flush=True)
        lim_md.content = _limits_table()

    def _lim_learn(_event) -> None:
        """把观测窗里的 min/max 整批记为限位（两侧各放宽 `OBS_MARGIN_RAD`）。

        为什么要这一条：逐颗推死点要动 14 次手（还得先松扭矩），而"这一页口径下这台
        实物真的走到过哪儿"跑一会儿就有了 —— 口径和显示角一致，所以判超限不会像 XML
        那样误报。代价是它不是机械死点，只是个区间，所以两侧放宽 3°。
        """
        got, skipped = [], []
        for name in JOINT_NAMES_MJ:
            olo, ohi = obs_seen[name]
            if not math.isfinite(olo) or (ohi - olo) < LIM_MIN_SPAN:
                skipped.append(name)
                continue
            meas_limits[name] = {"lo": olo - OBS_MARGIN_RAD,
                                 "hi": ohi + OBS_MARGIN_RAD, "src": "观测"}
            _lim_apply(name)
            got.append(name)
        if not got:
            lim_md.content = ("观测窗里还没有可用的行程 —— 让关节动一动（跑策略 / 手动掰）"
                              "或者先按「清空观测窗」重学")
            return
        try:
            save_meas_limits(LIMITS_JSON, meas_limits)
        except Exception as exc:                      # noqa: BLE001
            lim_md.content = f"⚠ 记下了 {len(got)} 颗但写不了 {LIMITS_JSON}：{exc}"
            return
        print(f"[限位] 按观测行程记下 {len(got)} 颗（放宽 {math.degrees(OBS_MARGIN_RAD):.1f}°）"
              f"→ {LIMITS_JSON}", flush=True)
        lim_md.content = _limits_table()
        if skipped:
            print(f"[限位] 观测窗里没数据的：{'、'.join(skipped)}", flush=True)

    def _lim_obs_reset(_event) -> None:
        """清空观测窗：换姿势 / 换装配之后旧极值会污染新行程。"""
        for name in JOINT_NAMES_MJ:
            obs_seen[name] = [math.inf, -math.inf]
        lim_md.content = _limits_table()
        print("[限位] 观测窗已清空，重新开始学", flush=True)

    lim_rec_lo.on_click(lambda _e: _lim_record("lo"))
    lim_rec_hi.on_click(lambda _e: _lim_record("hi"))
    lim_clear.on_click(_lim_clear)
    lim_learn.on_click(_lim_learn)
    lim_obs_reset.on_click(_lim_obs_reset)

    # ---- 真机指令（会动真机）：反向通道 + arm + 急停 ----
    # 这一组与下面「只跑仿真」分开摆，因为它们的后果完全不同：这里的按钮下去，机器人
    # 会真的动。arm 开关是给人看的第二道闸（桥那侧还有 --allow-write 一道）。
    rw_widgets: List[object] = []                 # 非 telemetry 源时统一置灰

    def _slide_range(lo_hi: Tuple[float, float]) -> Tuple[float, float, float]:
        """XML 的关节行程 -> 滑块的 (lo, hi, step)。向外取整到 3 位小数，与桥的量程
        校验用的是同一套数（桥那边是硬编码的同一组数，来源就是这个 XML）。"""
        lo, hi = lo_hi
        return (math.floor(lo * 1000) / 1000, math.ceil(hi * 1000) / 1000, 0.01)

    with server.gui.add_folder("真机指令（会动真机）", expand_by_default=False):
        rw_intro = server.gui.add_markdown(
            "**这一组的按钮真的会驱动真机** —— 命令经桥反向下发给 robotd。三个前提：\n\n"
            "① 本脚本用 `--telemetry-host` 启动（反向通道只在 telemetry 源上有，"
            "串口源那条路没地方发）；\n"
            "② WSL 那边的桥带 `--allow-write` 启动（默认是纯只读桥，命令一律被拒）；\n"
            "③ 这里勾上 arm。\n\n"
            "真机能被**逐关节**驱动的只有头/颈/嘴这 4+1 个（`robot.head` 是关节空间"
            "直控）；腿的 10 个关节没有逐关节接口 —— 它们只能靠策略/技能整机地动。"
            "想看逐关节波形，用下面「只跑仿真」那组。\n\n"
            "速度指令 `robot.move` 有 **500 ms 心跳**（robotd 的 deadman：速度过期就"
            "归零，机器人站住不动）。所以「行走中」勾着时本面板按 5 Hz 续发；取消勾选"
            "或按急停就停。\n\n"
            "真机动起来就可能摔 —— 台架上托住它，手放在电源开关上。")
        rw_arm = server.gui.add_checkbox("⚠ arm：允许驱动真机", initial_value=False)
        # 这一行**不是**一次性的提示：主循环每 0.5 s 用 `_rw_status()` 从活状态重算一遍，
        # 把「源 / arm / 最近一条桥 ack / robotd 的 limp·policy」拼成"现在为什么不动、
        # 下一步按什么"。以前是 handler 发完命令写一句就定死，看着像成功了其实可能被拒。
        rw_state = server.gui.add_markdown("正在读状态…")
        server.gui.add_markdown("**头 / 颈**（rad，关节空间）")
        head_sl: Dict[str, object] = {}
        for hname in HEAD_JOINTS:
            hlo, hhi, hstep = _slide_range(jnt_range[hname])
            head_sl[hname] = server.gui.add_slider(f"{hname} (rad)", hlo, hhi, hstep, 0.0)
            rw_widgets.append(head_sl[hname])
        head_live = server.gui.add_checkbox("拖动即下发（只喂仿真时别勾）",
                                            initial_value=False)
        head_send = server.gui.add_button("下发头/颈一次")
        head_home = server.gui.add_button("头/颈回中位（全 0）")
        rw_widgets += [head_live, head_send, head_home]
        server.gui.add_markdown("**嘴**（0 闭 → 1 开）")
        mouth_open = server.gui.add_slider("mouth open", 0.0, 1.0, 0.02, 0.0)
        mouth_send = server.gui.add_button("下发嘴一次")
        rw_widgets += [mouth_open, mouth_send]
        server.gui.add_markdown("**走**（速度指令；有 500 ms 心跳，勾着才续发）")
        mv_vx = server.gui.add_slider("vx 前进 (m/s)", -0.5, 0.5, 0.01, 0.0)
        mv_vy = server.gui.add_slider("vy 左移 (m/s)", -0.4, 0.4, 0.01, 0.0)
        mv_vyaw = server.gui.add_slider("vyaw 左转 (rad/s)", -2.0, 2.0, 0.05, 0.0)
        move_on = server.gui.add_checkbox("● 行走中（5 Hz 续发速度）", initial_value=False)
        rw_widgets += [mv_vx, mv_vy, mv_vyaw, move_on]
        server.gui.add_markdown("**上电 / 策略**")
        init_btn = server.gui.add_button("robot.init（上电并 ramp 到 home）")
        enable_btn = server.gui.add_button("robot.enable（打开策略；头要动就得有策略在跑）")
        rw_widgets += [init_btn, enable_btn]
        server.gui.add_markdown(
            "**策略开关**（v0.22；两个都是 `on_update` 的开关，勾上/取消就发一条）\n\n"
            "① **总开关** = `robot.enable {on}`。关掉后 robotd 退回 hold 位姿、"
            "**所有策略都不驱关节** —— 但注意**关节仍然是硬的**（reg40 还是 1，只是"
            "没人下目标了），真想松手还得按「联调命令」里的 ⑥。\n\n"
            "② **起身策略** = `robot.loadPolicy {slot:\"sitstand\", path}`。"
            "关 → path 写 `\"none\"`（robotd 允许关任何槽位，**只有 `walk` 例外**："
            "它是所有槽位解析不到的兜底）。于是它不会再自己从坐姿起身、也就不会起身后"
            "接着迈腿。\n\n"
            "⚠ 起身开关要 robotd **真的加载了策略**才有效 —— 也就是 ② 别勾"
            "「robotd 不加载策略（`--no-policy`）」。**这与上面①总开关无关**："
            "总开关关的只是「现在有没有在驱动机器人」，配置层的 `[policy] enabled` "
            "没动（实测 `robot.enable off` 之后再发 `loadPolicy` 照样 accepted；"
            "而 `--no-policy` 起的话会被拒：“policies are disabled on this robot”）。\n\n"
            "⚠ 槽位切换是**在 home 位姿时才发生**的，ack 只回 accepted，真结果"
            "看上面那行 `policy`。\n\n"
            "⚠ 开（给权限）要 arm；关（收权限）不 arm 也发。")
        pol_on = server.gui.add_checkbox("① 策略总开关（robot.enable）",
                                        initial_value=False)
        sitstand_on = server.gui.add_checkbox("② 起身策略（sitstand 槽位可加载）",
                                             initial_value=True)
        rw_widgets += [pol_on, sitstand_on]
        server.gui.add_markdown("**急停**（任何时候都能按，不受 arm 影响）")
        estop_btn = server.gui.add_button("急停：零速（robot.stop，仍站立）")
        relax_btn = server.gui.add_button(
            "急停：松扭矩（robot.relax，**会塌**；⚠ robotd 在跑就不管用 → 用 ⑥）")
        rw_ack = server.gui.add_markdown("")

    with server.gui.add_folder("只跑仿真（指令只喂仿真）", expand_by_default=False):
        server.gui.add_markdown(
            "**这一组的指令只喂给青色模型，真机一个关节都不会被本组驱动。**"
            "报告里会明确写「真机没有被激励」。要让真机也动：上面那组「真机指令」，"
            "或者用 robotd 自己的策略/游戏杆驱它，再用下面的「在线对比」。\n\n"
            "① **阶跃 / 正弦 / 姿态序列** —— 合成指令，看仿真自己的行为（验证 XML "
            "参数、看执行器饱和）。没有真机参考时只有仿真这一半。\n"
            "② **CSV 回放** —— 喂一段录制：命令取 `*_tgt` 列（真机当时收到的目标），"
            "真机参考取 `*_rad` 列。同一串目标喂给仿真，就得到『同一指令下，模型 vs "
            "真机』的逐帧对比。\n"
            "③ **在线对比**（推荐）—— 仿真逐帧吃**真机当拍目标**（telemetry 的 "
            "`targets`），真机由别人驱动；两层分开的那一段就是**纯模型失配**，"
            "不需要本组激励真机。\n\n"
            "仿真条件：基座钉住（跟随真机姿态）、腿悬空不接地。会话全程 50 Hz，"
            "与真机控制周期一致。")
        analysis_on = server.gui.add_checkbox("分析模式（允许下发仿真指令）",
                                              initial_value=False)
        live_on = server.gui.add_checkbox("在线对比：仿真吃真机目标", initial_value=False)
        live_md = server.gui.add_markdown("")
        # 「显示仿真层（青）」（sim_vis）在顶部的「工具」面板里，和这里解耦：
        # 显示是显示、跑不跑是跑不跑，青色平时该一直跟真机重合。
        grav_on = server.gui.add_checkbox("仿真加重力", initial_value=True)
        cmd_kind = server.gui.add_dropdown("指令类型",
                                           ("阶跃", "正弦", "姿态序列", "CSV 回放"),
                                           initial_value="阶跃")
        cmd_joint = server.gui.add_dropdown("关节", JOINT_NAMES_MJ,
                                           initial_value="left_knee")
        step_delta = server.gui.add_slider("阶跃增量 (rad)", -1.0, 1.0, 0.05, 0.30)
        step_hold = server.gui.add_slider("阶跃驻留 (s)", 0.5, 10.0, 0.5, 3.0)
        sine_amp = server.gui.add_slider("正弦幅值 (rad)", 0.02, 0.60, 0.01, 0.20)
        sine_freq = server.gui.add_slider("正弦频率 (Hz)", 0.2, 5.0, 0.1, 1.0)
        sine_dur = server.gui.add_slider("正弦时长 (s)", 1.0, 10.0, 0.5, 4.0)
        seq_name = server.gui.add_dropdown("姿态序列", tuple(POSE_SEQUENCES),
                                           initial_value="蹲起")
        csv_path = server.gui.add_text("回放 CSV 路径", initial_value="")
        start_btn = server.gui.add_button("开始分析")
        stop_btn = server.gui.add_button("停止")
        an_state = server.gui.add_markdown("未开始")
        an_table = server.gui.add_markdown("")
        export_btn = server.gui.add_button("导出报告（md + csv）")
        sysid_btn = server.gui.add_button("拟合执行器参数（只出建议，不写 XML）")
        an_extra = server.gui.add_markdown("")

    # ---- 联调命令：把"怎么从串口源切到 telemetry 源"写在页面上 ----
    # 这几条命令原先只在 docstring / 聊天记录里，现场要照抄时不好找。放页面上，
    # 并且第一行先说清**这一页现在在用什么源**（决定「真机指令」那组可不可用）。
    with server.gui.add_folder("联调命令（robotd + 桥）", expand_by_default=False):
        _src_now = (
            "**当前数据源：telemetry 桥**（`--telemetry-host`）—— robotd 正驱着真机，"
            "上面「真机指令」那组可用。**要回到串口源**：先按 ④ 把总线收回 Windows"
            "（还挂在 WSL 里的话串口开不了），再按 ③′。"
            if tele is not None else
            "**当前数据源：串口 COM8**（`--port`）—— 本进程自己开串口读舵机，robotd 没在"
            "跑，所以上面「真机指令」那组是灰的。要让 robotd 驱真机、同时还能在这里看，"
            "按下面 ①②③ 切到 telemetry 源。")
        server.gui.add_markdown(
            _src_now + "\n\n"
            "**下面的按钮就是下面这几条命令的一键版** —— 它们只在这台机器上跑（viser "
            "只 listen 在 `127.0.0.1`，没有网络暴露面），但要先勾「⚠ 允许一键启动」，"
            "因为按下去会改 USB 归属、在 WSL 里起进程、或者把本页重启。执行结果在按钮"
            "下面那格里回显（含退出码与输出尾部）。\n\n"
            "**① Windows（管理员 PowerShell）：把两条总线交给 WSL**\n"
            "- `usbipd list` —— 先记下 CH343（舵机 / COM8）那一行的 BUSID\n"
            "- `usbipd attach --wsl Ubuntu --busid 上面那个BUSID` —— 舵机总线\n"
            "- `usbipd attach --wsl Ubuntu --busid 4-2` —— IMU 桥（CH340 / COM6）\n"
            "（要换回串口源就反过来 `usbipd detach --busid ...` 把设备拿回 Windows）\n"
            "按钮 ① 会先把 WSL 叫醒（usbipd 要求发行版**正在运行**，WSL2 闲置自己会停）"
            "并让开本页占着的 COM8 与 COM6（attach 要把设备从 Windows 摘走，本页开着"
            "串口它就摘不动：实测报 `Device busy (exported)`）—— 让开之后本页就没有"
            "舵机与 IMU 数据了，属于预期，按 ②③ 切到 telemetry 源就回来\n\n"
            "**② WSL：robotd + 桥**\n"
            "- `robotd --params /mnt/e/Temp/realbus-robotd.toml --socket "
            "/tmp/robotd-c5.sock`（想只观测就再加 `--no-policy`，见上）\n"
            "- `python3 /mnt/e/optiDuck/tools/robotd_telemetry_bridge.py --socket "
            "/tmp/robotd-c5.sock --listen 0.0.0.0:8199 --allow-write`\n"
            "- 桥**不加** `--allow-write` 也能看，只是「真机指令」那组一律被拒（ack 会说"
            "「桥是只读的」）\n"
            "- ⚠ **起 robotd = 关节变硬**（v0.20 纠正）：robotd 停在 `Bringup::Limp` 说的只是"
            "「没在驱策略」，它照样 hold 启动位姿、**每拍写一次目标位置 reg42**；而 HD-1910 "
            "的固件收到目标位置写入就会把扭矩开关 reg40 置 1（单变量实测：只写一次 reg42 → "
            "15/15 立刻变 1）。所以要松关节必须让 robotd 停着 —— 按下面的 ⑥\n"
            "- ⚠ 由此**「急停：松扭矩」（`robot.relax`）在 robotd 跑着时不成立**：它把 "
            "reg40 写 0，下一拍（20 ms 后）就被 reg42 翻回来，看着生效、关节还是硬的。"
            "真松按 ⑥；要一边看孪生一边掰关节：按 ④ 把总线收回 Windows、再按 ③′ 换回"
            "**串口源**（串口源只读 reg56/IMU、不写 reg42，扭矩不会回来）\n"
            "- ② 会**先把在跑的 robotd 停掉**再起新的（否则旧的那个继续占着 `--socket`，"
            "新的 bind 失败直接退出，面板连上的还是旧参数那一个）\n\n"
            "**③ Windows：本页换成 telemetry 源**（COM8 是独占的，先把串口模式那个进程"
            "关掉）\n"
            "- `e:\\optiDuck\\microduck_rl\\.venv\\Scripts\\python.exe "
            "e:\\optiDuck\\tools\\bench_mirror.py --telemetry-host 127.0.0.1:8199 "
            "--viser-port 8081`\n\n"
            "连不通先查这三样：robotd 在跑没（`robotctl health`）、桥的 `--socket` 与"
            "robotd 的 `--socket` 是不是同一个、WSL2 的 localhost 转发开着没（不行就用 "
            "`hostname -I` 的 IP 替 127.0.0.1）。")

        lc_gate = server.gui.add_checkbox(
            "⚠ 允许一键启动（会改 USB 归属 / 在 WSL 起进程 / 重启本页）",
            initial_value=False)
        lc_b1 = server.gui.add_button("① 把两条总线挂到 WSL（usbipd attach）")
        lc_b4 = server.gui.add_button("④ 把总线收回 Windows（切回串口源）")
        lc_aw = server.gui.add_checkbox("桥带 --allow-write（允许本页驱动真机）",
                                        initial_value=False)
        lc_np = server.gui.add_checkbox(
            "② robotd 不加载策略（--no-policy，台架纯观测；勾着时走路/技能不可用）",
            initial_value=True)
        lc_b2 = server.gui.add_button("② 在 WSL 起 robotd + 桥")
        lc_b5 = server.gui.add_button("⑤ 停掉 WSL 里的 robotd + 桥")
        lc_b3 = server.gui.add_button("③ 本页换成 telemetry 源重启")
        # 换回串口源（v0.12）：③ 是**单向**的，原来切到 telemetry 之后按 ④⑤ 收摊，
        # 页面就卡在 telemetry 源上没数据、也没有回去的路。这条是 ③ 的逆操作。
        lc_b3s = server.gui.add_button("③′ 换回串口源重启（--port，反向）")
        # ⑥（v0.20）：真·松扭矩。**必须先停 robotd** —— 它每拍写 reg42，而 HD-1910 收到
        # 目标位置写入就把 reg40 置回 1，所以只要 robotd 活着，写多少遍 reg40=0 都没用
        # （实测 20 ms 内翻回，`robot.relax` 就是这么"成功"了却掰不动的）。
        lc_ftport = server.gui.add_text("⑥ 舵机串口（WSL 侧）",
                                        initial_value="/dev/ttyACM0")
        lc_b6 = server.gui.add_button(
            "⑥ 真·松扭矩（停 robotd + 桥 → 直写 reg40=0 → 读回）")
        server.gui.add_markdown("悬停这行看说明：① ④ 改 USB 归属（要管理员，会弹 UAC）；"
                                "② ⑤ 在 WSL 里起/停 robotd 与桥；③ 用**同一个端口**把本页"
                                "换成 telemetry 源（先确认桥活着，否则不换）；③′ 是 ③ 的"
                                "**逆操作**（换回 `--port` 串口源，换前先探串口开不开）；"
                                "⑥ 停 robotd + 桥后直写 `reg40=0` 并读回 —— **这是唯一能"
                                "真正把关节弄松的按钮**，按完别急着按 ②（robotd 一起来"
                                "reg40 就翻回硬态）")
        server.gui.add_markdown(
            "**② 的两个新参数（v0.21）**：`ORT_DYLIB_PATH` 指到那份 "
            "`libonnxruntime.so`（WSL 里没装到 `/usr/lib`，不指就报 "
            "`policy unavailable: ONNX Runtime not loadable`）；要真跑策略还得去掉 "
            "「② robotd 不加载策略」那个勾 —— 但注意**策略槽位的文件得先就位**"
            "（见 `realbus-robotd.toml` 的 `[policy]`），否则官方默认路径 "
            "`/opt/robot/policies/current/*.onnx` 一个都不存在，同样是 unhealthy。")
        lc_out = server.gui.add_markdown("还没执行过。")
        server.gui.add_markdown("**下面这些值照你的机器改**（默认是这台机器上已知的）")
        lc_distro = server.gui.add_text("WSL 发行版", initial_value=WSL_DISTRO_DEFAULT)
        lc_busid_s = server.gui.add_text("舵机总线 BUSID（留空=自动认 CH343）",
                                         initial_value="")
        lc_busid_i = server.gui.add_text("IMU 总线 BUSID（留空=自动认 CH340）",
                                         initial_value="4-2")
        # ③′ 要用的两个口名：telemetry 源下 args.port 是 None，所以这里给默认值兜底
        lc_port = server.gui.add_text("③′ 舵机串口", initial_value=(args.port or "COM8"))
        lc_imu = server.gui.add_text("③′ IMU 口", initial_value=(args.imu_port or "COM6"))
        lc_bin = server.gui.add_text("robotd 可执行文件", initial_value=ROBOTD_BIN_DEFAULT)
        lc_params = server.gui.add_text("robotd --params", initial_value=ROBOTD_PARAMS_DEFAULT)
        lc_socket = server.gui.add_text("robotd --socket", initial_value=ROBOTD_SOCKET_DEFAULT)
        # v0.21：留空 = 不给 ORT_DYLIB_PATH（`ort` 走系统搜索路径）。
        lc_ort = server.gui.add_text("ORT_DYLIB_PATH（留空=不设）",
                                     initial_value=ROBOTD_ORT_DEFAULT)
        lc_listen = server.gui.add_text("桥 --listen", initial_value=BRIDGE_LISTEN_DEFAULT)

    # ========================================================================
    # 一键启动的 handler（v0.8）：三条命令都在线程里跑，50 Hz 主循环绝不等它们
    # ========================================================================
    # 1) usbipd attach/detach：本进程多半不是管理员，直连失败（明确报 administrator/
    #    拒绝访问）时再用 Start-Process -Verb RunAs 提一次；RunAs 接不了管道，所以让它
    #    把输出写文件、我们再读回来，结果照样能显示在面板上。
    # 2) WSL 那边起 robotd + 桥：`wsl.exe -d <发行版> -- bash -lc ...`，nohup 挂后台，
    #    日志落 /tmp/{robotd,bridge}.log；起之前先 pkill 掉旧的桥，免得抢 8199。
    # 3) 本页换源：先探 8199 通不通，通了才 os.execv 换参数重启（同 PID、同端口，
    #    所以浏览器只会闪一下）。不通用 execv 是**不会**做的 —— 那等于把页面弄死。
    lc_jobs: List[Dict[str, object]] = []
    # ① 把串口交给 WSL 之后置 True：主循环不再读、也不重开 COM8（重开会把设备从 WSL
    # 抢回来、attach 就失败）。恢复数据要按 ③ 切到 telemetry 源。
    bus_released = [False]
    # 同理，IMU 那条口（COM6）也得让开：本页开着它时 attach 4-2 实测报
    # `Device busy (exported)` —— usbipd 摘不走正被 Windows 程序占着的设备。
    imu_released = [False]

    def _lc_note(label: str, cmd: str, rc: int, out: str, err: str,
                 sec: float = 0.0) -> None:
        lc_jobs.append({"label": label, "cmd": cmd, "rc": rc, "out": (out or "").strip(),
                        "err": (err or "").strip(), "sec": sec})
        del lc_jobs[:-6]                              # 只留最近 6 条，别涨

    def _wsl_wake(distro: str) -> bool:
        """usbipd attach 要求发行版**正在运行**（WSL2 空闲一分钟左右就把它停掉），
        所以 attach 前先喊一声把它拉起来，否则必然报 "distribution is not running"。"""
        argv = ["wsl.exe", "-d", distro, "--", "exec", "true"]
        try:
            cp = subprocess.run(argv, capture_output=True, timeout=30)
            rc = cp.returncode
            err = _lc_text(cp.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            rc, err = -1, str(exc)
        ok = rc == 0
        _lc_note("唤醒 WSL", " ".join(argv), rc, "已唤醒" if ok else "",
                 "" if ok else (err or "发行版起不来（`wsl.exe -l -v` 看看）"))
        return ok

    def _lc_text(blob: bytes) -> str:
        """wsl.exe 的 stderr 是 UTF-16LE（那句话是 localhost 代理的告警），直接按 UTF-8
        解会整行乱码；这里按 NUL 密度猜一下编码，再把 wsl 自己的告警行滤掉。

        `usbipd attach` 更麻烦：它把 WSL 那边的 UTF-16LE stderr **原样**混进自己的
        UTF-8 输出，整块猜编码就两头不讨好（实测 attach 成功那行下面跟着一串乱码）。
        这种残渣解出来一定带私用区字符（U+E000–U+F8FF）或替换符（U+FFFD），按这个
        特征逐行丢掉即可 —— usbipd 与桥的输出都是英文，正常不会出现这两种字符。
        """
        if not blob:
            return ""
        if blob.count(b"\x00") > len(blob) // 8:
            text = blob.decode("utf-16-le", "replace")
        else:
            text = blob.decode("utf-8", "replace")
        keep = [ln for ln in text.splitlines()
                if not ln.lstrip("\ufeff").startswith("wsl:")
                and not any(0xE000 <= ord(c) <= 0xF8FF or c == "\ufffd" for c in ln)]
        return "\n".join(keep)

    def _lc_bg(label: str, argv: List[str], timeout: float = 60.0) -> None:
        """线程里跑一条命令，结果进 lc_jobs（面板 2 Hz 刷）。"""
        shown = " ".join(argv)

        def work() -> None:
            t0 = time.perf_counter()
            try:
                cp = subprocess.run(argv, capture_output=True, timeout=timeout)
                _lc_note(label, shown, cp.returncode, _lc_text(cp.stdout),
                         _lc_text(cp.stderr), time.perf_counter() - t0)
            except subprocess.TimeoutExpired:
                _lc_note(label, shown, -1, "", f"超时 {timeout:.0f} s（命令还挂着，"
                         "去终端看看）", time.perf_counter() - t0)
            except OSError as exc:                    # 没装 / 路径不对
                _lc_note(label, shown, -1, "", str(exc), time.perf_counter() - t0)

        threading.Thread(target=work, daemon=True).start()

    def _lc_gate() -> bool:
        """三道闸里最后一道：按钮本身也要过 gate，不勾就什么都不会发生。"""
        if lc_gate.value:
            return True
        _lc_note("被拦下", "—", -1, "",
                 "先勾「⚠ 允许一键启动」：这一组会改 USB 归属、在 WSL 起进程、"
                 "或者把本页重启。")
        return False

    def _lc_sane(text: str, what: str, pattern: str) -> Optional[str]:
        """按钮里的文本会拼进命令行，所以只收"看起来就是那个东西"的值。"""
        v = text.strip()
        if not re.match(pattern, v):
            _lc_note(f"{what} 不合法", "—", -1, "", f"{v!r} 不匹配 {pattern}")
            return None
        return v

    def _usbipd_worker(busid: str, distro: str, action: str) -> None:
        exe = _usbipd_exe()
        argv = ([exe, "attach", "--wsl", distro, "--busid", busid] if action == "attach"
                else [exe, "detach", "--busid", busid])
        shown = " ".join(argv)
        t0 = time.perf_counter()

        def attempt() -> "subprocess.CompletedProcess[bytes]":
            return subprocess.run(argv, capture_output=True, timeout=60)

        if action == "attach":
            _wsl_wake(distro)          # 发行版没在跑的话 attach 一定报 not running
        try:
            cp = attempt()
        except (OSError, subprocess.SubprocessError) as exc:
            _lc_note(f"{action} {busid}", shown, -1, "", str(exc))
            return
        blob = f"{_lc_text(cp.stdout)}\n{_lc_text(cp.stderr)}"
        # WSL2 空闲会自己停：attach 前刚唤醒也可能在几秒内又被判"没在跑"，重试一次
        if cp.returncode != 0 and action == "attach" \
                and "not running" in blob.lower():
            if _wsl_wake(distro):
                try:
                    cp = attempt()
                except (OSError, subprocess.SubprocessError) as exc:
                    _lc_note(f"{action} {busid}", shown, -1, "", str(exc))
                    return
                blob = f"{_lc_text(cp.stdout)}\n{_lc_text(cp.stderr)}"
        need_admin = cp.returncode != 0 and any(
            k in blob.lower() for k in ("administrator", "elevat", "access is denied",
                                        "拒绝访问", "管理员"))
        if not need_admin:
            hint = ""
            if cp.returncode != 0 and action == "attach":
                low = blob.lower()
                if "not running" in low:
                    hint = ("\n（WSL 发行版没在跑，唤醒也没成功：先在终端跑一次 "
                            "`wsl.exe -d " + distro + " -- true` 或开着 WSL 窗口再按 ①）")
                elif ("in use" in low or "being used" in low or "busy" in low
                        or "占用" in blob):
                    hint = ("\n（设备正被 Windows 这边占着，usbipd 摘不走："
                            "报的是 `Device busy (exported)` 的话，多半是还有别的"
                            "终端 / 程序开着这个 COM 口，先关掉；本页若是串口源，① "
                            "已经先让开 COM8 + COM6 了）")
            _lc_note(f"{action} {busid}", shown, cp.returncode,
                     _lc_text(cp.stdout), _lc_text(cp.stderr) + hint,
                     time.perf_counter() - t0)
            return
        # 提权重试：RunAs 起的新进程接不回管道，让它把输出写文件、这里再读
        log = os.path.join(tempfile.gettempdir(), f"usbipd_{action}_{busid}.log")
        script = os.path.join(tempfile.gettempdir(), f"usbipd_{action}_elevated.ps1")
        try:
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(f"& '{exe}' {' '.join(argv[1:])} *> '{log}'\n")
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Start-Process powershell -Verb RunAs -Wait "
                            f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass',"
                            f"'-File','{script}'"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            _lc_note(f"{action} {busid}（提权）", shown, -1, "",
                     f"提权那步没起来：{exc}（也可以自己在管理员 PowerShell 里跑这条）")
            return
        try:
            with open(log, "r", encoding="utf-8", errors="replace") as fh:
                out2 = fh.read()
        except OSError:
            out2 = ""
        _lc_note(f"{action} {busid}（已提权）", shown,
                 0 if out2.strip() else -1, out2,
                 "" if out2.strip() else "UAC 被取消 / 提权窗口没写出东西；实在不行就"
                 "自己开一个管理员 PowerShell 跑这条", time.perf_counter() - t0)

    def _lc_two_busids(action: str) -> None:
        """① 与 ④ 共用：先读 usbipd list，按 VID:PID 认两条总线（也可手工填 BUSID）。"""
        rows = _usbipd_rows()
        table = "\n".join(f"{b}  {v}  {d}" for b, v, d in rows) or "（没读到设备）"
        if action == "detach" and rows:
            # 收回：只动"挂在 WSL 上"的那些（State 里带 Attached），不乱拔别的
            attached = [b for b, _v, d in rows if "attach" in d.lower()]
            if not attached:
                _lc_note("④ 收回总线", f"{_usbipd_exe()} list", -1, table,
                         "没有任何设备挂在 WSL 上（State 不是 Attached）")
                return
            for b in attached:
                threading.Thread(target=_usbipd_worker, args=(b, "", "detach"),
                                 daemon=True).start()
            return
        wants = [("舵机", lc_busid_s.value.strip() or _pick_busid(rows, True)),
                 ("IMU", lc_busid_i.value.strip() or _pick_busid(rows, False))]
        if bus is not None and not bus_released[0]:
            # attach 要把设备从 Windows 摘走，本页占着 COM8 会让它失败：先让开。
            # 让开之后主循环不再重开这个口（否则会跟 attach 抢设备），舵机数据要按 ③
            # 切到 telemetry 源才回来。
            # 旗子必须**先立**再关：主循环是"看到旗子就不读这个口"，反过来（先关后立）
            # 中间那一小段它照样会去 read 已关闭的端口（2026-09-30 的崩因）。
            bus_released[0] = True
            try:
                bus.close()
            except Exception:                             # noqa: BLE001
                pass
            _lc_note("让出串口给 WSL", f"{args.port} 已关闭", 0, "",
                     "本页不再读串口、也不重开它；按 ②③ 切到 telemetry 源后数据才回来"
                     "（想撤回就按 ④，再重启本页的串口模式）")
        if imu is not None and isinstance(imu, ImuStream) and not imu_released[0]:
            # COM6 不放的话 attach 4-2 会报 `Device busy (exported)`（实测踩过）。
            # 同 ①：旗子先立再关，免得主循环对着刚关掉的口 read。
            imu_released[0] = True
            try:
                imu.close()
            except Exception:                             # noqa: BLE001
                pass
            _lc_note("让出 IMU 口给 WSL", f"{args.imu_port} 已关闭", 0, "",
                     "基座姿态停在最后一帧；按 ③ 切到 telemetry 源后 IMU 也回来")
        for what, busid in wants:
            if not re.match(r"^\d+-\d+$", busid or ""):
                _lc_note(f"attach {what}", "—", -1, table,
                         f"认不出 {what} 总线（BUSID={busid!r}）：要么在上面那个 "
                         "BUSID 框里手工填，要么先 `usbipd list` 看看设备在不在")
                continue
            threading.Thread(target=_usbipd_worker, args=(busid, distro0(), "attach"),
                             daemon=True).start()

    def distro0() -> str:
        v = lc_distro.value.strip()
        return v if re.match(r"^[A-Za-z0-9._-]+$", v) else WSL_DISTRO_DEFAULT

    @lc_b1.on_click
    def _(_ev=None) -> None:
        if _lc_gate():
            _lc_two_busids("attach")

    @lc_b4.on_click
    def _(_ev=None) -> None:
        if _lc_gate():
            _lc_two_busids("detach")

    @lc_b2.on_click
    def _(_ev=None) -> None:
        if not _lc_gate():
            return
        distro = distro0()
        sock = _lc_sane(lc_socket.value, "socket", r"^/[A-Za-z0-9._/-]+$")
        params = _lc_sane(lc_params.value, "params", r"^/[A-Za-z0-9._/-]+$")
        listen = _lc_sane(lc_listen.value, "listen", r"^[0-9.]+:\d{2,5}$")
        binp = _lc_sane(lc_bin.value, "robotd 路径", r"^/[A-Za-z0-9._/-]+$")
        if None in (sock, params, listen, binp):
            return
        # v0.21：ORT_DYLIB_PATH 允许**留空**（= 不给这个变量，`ort` 走系统搜索路径），
        # 所以不能直接用 `_lc_sane`（它的正则要求以 `/` 开头、空串必挂）。
        ortv = lc_ort.value.strip()
        if ortv and not re.match(r"^/[A-Za-z0-9._/-]+$", ortv):
            _lc_note("ORT_DYLIB_PATH 不合法", "—", -1, "",
                     f"{ortv!r} 必须是以 / 开头的绝对路径，或者留空")
            return
        bridge = _wsl_path(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "robotd_telemetry_bridge.py"))
        aw = " --allow-write" if lc_aw.value else ""
        # `--no-policy` = 不加载 ONNX 策略，差别只在策略加载了没：带上它
        # robot.policies 报 enabled=false，「真机指令」里只剩头/颈/嘴这类关节直控可用，
        # 走路与技能全不可用。
        #
        # ⚠ **v0.20 纠正**：起 robotd 一定会让关节变硬（`Limp` 只是"没在驱策略"，robotd
        # 照样 hold 启动位姿、**每拍写一次 reg42**，而 HD-1910 收到目标位置写入就把扭矩
        # 开关 reg40 置 1；单变量实测：只写 reg42 → 15/15 立刻变 1）。所以"起 robotd
        # 不会主动开扭矩""Limp 所以关节是松的"都是错的；要松就得停 robotd 后按 ⑥。
        npol = " --no-policy" if lc_np.value else ""
        # v0.21：把 ONNX Runtime 指给 robotd。留空就不加这个前缀。
        ortenv = f"ORT_DYLIB_PATH={ortv} " if ortv else ""
        # 模式见 BRIDGE_PROC_RE 的注释：必须锚定 cmdline 开头，否则 ② 里下面那句
        # `nohup python3 {bridge} ...` 展开出的真路径会让 wrapper 匹配到自己的模式。
        kill_bridge = f"pkill -f '{BRIDGE_PROC_RE}' || true"
        # 下面这个 inner 串里**一个 `$` 都不能出现**。
        #
        # `wsl.exe -d … -- bash -lc "<inner>"` 并不是把 inner 原样交给 bash：wsl.exe 会把
        # 命令重新拼成一行、先过一层外壳，那层壳会做一次参数展开。原来那句
        # `BIN=$(command -v robotd || echo …); nohup $BIN …` 因此两头都错 —— `$(…)` 在外层
        # 就被算掉了，`$BIN` 在外层展开成**空**（外层根本没有 BIN），bash 最终拿到的是
        # `nohup  --params …`，报出来的是 `nohup: unrecognized option '--params'`
        # （2026-09-30 实测；用 `\$BIN` 转义就恢复正常，证明是外层在展开）。
        #
        # 所以直接用面板里那个绝对路径（`binp` 已过 `^/[A-Za-z0-9._/-]+$` 校验）。反正
        # robotd 不在 WSL 的 PATH 里，`command -v` 永远走兜底分支，省掉它反而更清楚。
        # v0.17：**先把在跑的 robotd 也停掉**。以前这里只杀桥，旧 robotd 还占着
        # `--socket`，新起的那个会在 bind 处拿到 AddrInUse 直接退出（robotd 只有在
        # 连得上、但被拒时才当"陈旧的 socket"删掉重绑）。于是面板连上的其实一直是
        # **最早那个** robotd —— 它带着当时那套参数（可能加载了策略、已经 enable、
        # 扭矩开着），`--no-policy` 之类新勾的东西根本没生效，用户看到的就是
        # 「点了 ② 机器人还是硬的 / 还是按老样子在跑」。先停掉，② 才真的是"重启"。
        # 顺带把旧进程的完整命令行打出来 —— 这行就是"上一次到底带的什么参数"的证据。
        inner = (f"{kill_bridge}; sleep 0.3; "
                 f"echo '--- 停掉的旧 robotd（没有就是空）---'; "
                 f"pgrep -a -x robotd || echo '(没有在跑的 robotd)'; "
                 f"pkill -x robotd || true; sleep 0.5; "
                 f"{ortenv}nohup {binp} --params {params} --socket {sock}{npol} "
                 f"> /tmp/robotd.log 2>&1 & sleep 2; "
                 f"nohup python3 {bridge} --socket {sock} --listen {listen}{aw} "
                 f"> /tmp/bridge.log 2>&1 & sleep 2; "
                 f"echo '--- robotd 进程 ---'; pgrep -a -x robotd || echo '(没起来)'; "
                 f"echo '--- 桥进程 ---'; "
                 f"pgrep -a -f '{BRIDGE_PROC_RE}' || echo '(没起来)'; "
                 f"echo '--- robotd.log 尾部 ---'; tail -n 5 /tmp/robotd.log "
                 f"2>/dev/null || true; "
                 f"echo '--- bridge.log 尾部 ---'; tail -n 5 /tmp/bridge.log "
                 f"2>/dev/null || true")
        # 桥不带 --allow-write 时它是**只读**的：「真机指令」那一组按什么都会被桥回
        # ok:false。以前这件事只在桥的 stdout 里、没人看得到，用户按了没反应只能猜
        # （2026-09-30 实测就是这么撞上的）。标签上直接写明。
        ro = "" if lc_aw.value else " · ⚠ 桥只读（真机指令按了不会动：勾上上面那个框再按 ②）"
        _lc_bg(f"② 起 robotd + 桥（{distro}）{ro}",
               ["wsl.exe", "-d", distro, "--", "bash", "-lc", inner], timeout=90)

    @lc_b5.on_click
    def _(_ev=None) -> None:
        if not _lc_gate():
            return
        _lc_bg("⑤ 停 robotd + 桥",
               ["wsl.exe", "-d", distro0(), "--", "bash", "-lc",
                f"pkill -f '{BRIDGE_PROC_RE}' || true; "
                "pkill -x robotd || true; "
                # v0.17 更正：robotd 收到 SIGTERM **只**是把控制环停下来退出
                # （main.rs 的 shutdown() → `state.shutdown.store(true)` →
                # `control.join()` → 删 socket），**不会切扭矩** —— 切扭矩只有
                # `robot.shutdown` / 电池空 那条"坐下再断电"的路会做
                # （`cut_torque_before_poweroff`）。所以按 ⑤ 之后关节**可能还是硬的**：
                # 舵机的 reg40 在 SRAM 里，robotd 走了没人再碰它。
                # v0.20 再更正一次"怎么松"：**「急停：松扭矩」在 robotd 跑着时不管用**
                # （它把 reg40 写 0，下一拍就被同步的目标位置 reg42 翻回来）。既然 ⑤ 已经
                # 把 robotd 停了，接着按 ⑥（直写 reg40=0 + 读回校验）就是最后一步。
                "sleep 1; "
                "echo '--- 剩余 robotd ---'; pgrep -a -x robotd || echo '(无)'; "
                "echo '--- 剩余桥 ---'; "
                f"pgrep -a -f '{BRIDGE_PROC_RE}' || echo '(无)'"], timeout=40)

    @lc_b6.on_click
    def _(_ev=None) -> None:
        """⑥ 真·松扭矩（v0.20）。

        为什么必须先停 robotd：HD-1910 的固件收到**目标位置 reg42** 写入就把扭矩开关
        reg40 置 1，而 robotd 的控制环每拍都在 `sync_write_goal_position`（它在 hold
        启动位姿）。所以只要 robotd 活着，写多少遍 reg40=0 都会在 20 ms 内被翻回来 ——
        `robot.relax` 就是这么「报成功」了却依然掰不动的。停掉它，再直写并**读回校验**
        才是真的松（这一步 robotd 从来不做：它写扭矩开关只发不等、没有读回）。

        桥也一起停：robotd 都没了，桥只会每秒重连一次刷日志。按完**别急着按 ②**，
        一起 robotd reg40 就回 1；要一边看孪生一边掰关节就走 ④ → ③′（串口源）。
        """
        if not _lc_gate():
            return
        distro = distro0()
        port = _lc_sane(lc_ftport.value, "⑥ 舵机串口", r"^/[A-Za-z0-9._/-]+$")
        if port is None:
            return
        tool = _wsl_path(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "ft_regs.py"))
        inner = (f"pkill -f '{BRIDGE_PROC_RE}' || true; "
                 f"pkill -x robotd || true; sleep 1.2; "
                 f"python3 {tool} --port {port} --off --brief; "
                 "echo; "
                 "echo '⚠ 别马上按 ②：robotd 一起来就 hold 位姿、每拍写 reg42，"
                 "reg40 会翻回 1'; "
                 "echo '想一边看孪生一边掰关节 → 先按 ④ 收回总线，再按 ③′ 换回串口源'")
        _lc_bg(f"⑥ 真·松扭矩（{distro}）",
               ["wsl.exe", "-d", distro, "--", "bash", "-lc", inner], timeout=90)

    @lc_b3.on_click
    def _(_ev=None) -> None:
        if not _lc_gate():
            return
        listen = _lc_sane(lc_listen.value, "listen", r"^[0-9.]+:\d{2,5}$")
        if listen is None:
            return
        port = int(listen.rsplit(":", 1)[-1])
        # 换源前先探桥：桥没活着就 execv，等于把页面弄死且回不来
        probe = socket.socket()
        probe.settimeout(1.5)
        try:
            probe.connect(("127.0.0.1", port))
        except OSError as exc:
            _lc_note("③ 换源前探测", f"tcp 127.0.0.1:{port}", -1, "",
                     f"连不上桥（{exc}）：先按 ② 把 robotd + 桥 起起来再换源")
            return
        finally:
            probe.close()
        if recorder.on:                       # execv 会跳过 finally，先把录制收尾
            recorder.stop()
        if bus is not None:
            bus.close()                       # 串口让出去，免得新进程抢不到
        argv = [sys.executable, os.path.abspath(__file__),
                "--telemetry-host", f"127.0.0.1:{port}",
                "--viser-port", str(server.get_port())]
        if args.no_ghost:
            argv.append("--no-ghost")
        print(f"[联调] 换源重启：{' '.join(argv)}", flush=True)
        sys.stdout.flush()
        try:
            os.execv(sys.executable, argv)    # 同 PID / 同端口，浏览器闪一下就连回来
        except OSError as exc:
            _lc_note("③ 换源重启失败", " ".join(argv), -1, "",
                     f"{exc}（旧进程已经收了串口，重新跑一次本脚本吧）")

    @lc_b3s.on_click
    def _(_ev=None) -> None:
        """③ 的逆操作：本页换回 `--port` 串口源（v0.12）。

        ③ 把页面切到 telemetry 源之后，如果又按 ④ 收回总线、⑤ 停桥，页面就停在
        telemetry 源上：没有数据，也没有回去的路（只能去终端重敲命令）。这条补上那段。

        换回前**先探一次串口能不能开** —— WSL 还攥着这个口的时候新进程开不了它，
        那种情况下 execv 等于把页面弄死且回不来，所以探到打不开就拒绝并说明。
        """
        if not _lc_gate():
            return
        port = _lc_sane(lc_port.value, "③′ 舵机串口", r"^COM\d{1,3}$")
        if port is None:
            return
        imup: Optional[str] = None
        if not args.no_imu:
            imup = _lc_sane(lc_imu.value, "③′ IMU 口", r"^COM\d{1,3}$")
            if imup is None:
                return
        # 探路：开一下立刻关（不影响后续 execv 接手）。COM 口是独占的，WSL 那边还
        # attach 着的话这里会报 PermissionError / 拒绝访问。
        try:
            probe = serial.Serial(port, args.baud, timeout=0.2)
            probe.close()
        except Exception as exc:                      # noqa: BLE001
            low = str(exc).lower()
            hint = ("还挂在 WSL 里？先按 ④ 把总线收回 Windows 再换回"
                    if ("permission" in low or "拒绝访问" in str(exc) or "access" in low)
                    else "看看设备在不在（`usbipd list`）、有没有别的程序占着这个口")
            _lc_note("③′ 换回串口源前探测", f"{port}@{args.baud}", -1, "",
                     f"打不开 {port}：{exc}。{hint}")
            return
        if recorder.on:                       # execv 会跳过 finally，先把录制收尾
            recorder.stop()
        argv = [sys.executable, os.path.abspath(__file__),
                "--port", port, "--baud", str(args.baud),
                "--viser-port", str(server.get_port())]
        if imup:
            argv += ["--imu-port", imup]
        if args.no_ghost:
            argv.append("--no-ghost")
        print(f"[联调] 换回串口源重启：{' '.join(argv)}", flush=True)
        sys.stdout.flush()
        try:
            os.execv(sys.executable, argv)
        except OSError as exc:
            _lc_note("③′ 换回串口源失败", " ".join(argv), -1, "",
                     f"{exc}（页面还在 telemetry 源上，可以再按一次）")

    def _base_pose_now() -> Dict[str, float]:
        """开始那一刻真机的 14 关节角 —— 合成指令都以它为基准（增量式）。"""
        pose: Dict[str, float] = {}
        for sid, name in JOINT_TABLE:
            st = states.get(sid)
            if name is not None and st is not None:
                pose[name] = joint_angle(st, name)
        return {n: pose.get(n, 0.0) for n in JOINT_NAMES_MJ}

    def _build_command() -> Command:
        base = _base_pose_now()
        kind = cmd_kind.value
        if kind == "阶跃":
            return make_step(base, cmd_joint.value, step_delta.value, step_hold.value)
        if kind == "正弦":
            return make_sine(base, cmd_joint.value, sine_amp.value, sine_freq.value,
                             sine_dur.value)
        if kind == "姿态序列":
            return make_sequence(base, seq_name.value)
        path = csv_path.value.strip().strip("`")
        if not path:
            raise ValueError("「CSV 回放」要先填路径（面板「录制」里录的那份）")
        return make_csv_replay(path)

    def _apply_sim_vis(_event=None) -> None:
        # 只由「工具」里那个显示开关决定 —— 勾不勾「分析模式」不该影响看不看得见青色。
        # 青色不跑分析时本来就跟实线重合，所以默认不显示（重合着看反而糊）。
        show = bool(sim_vis.value)
        sim_layer.set_visible(show)
        sim_shown[0] = show

    sim_vis.on_update(_apply_sim_vis)

    # ========================================================================
    # 真机指令的 handler：反向通道（只有 telemetry 源才有这条往上走的路）
    # ========================================================================
    # 串口源时整组置灰、连 arm 都点不动 —— 本脚本走串口时根本没连 robotd，没有"发给
    # 谁"这件事。置灰不是装饰：不给"按了没反应，是脚本坏了还是机器人不理我"的错觉。
    if tele is None:
        rw_intro.content += ("\n\n**当前是串口源（`--port`）：这一组不可用** —— 反向通道"
                             "只建在 telemetry 源上。要用它请改用 `--telemetry-host`。")
        rw_arm.disabled = True
        for _w in rw_widgets:
            _w.disabled = True

    head_pushed = [0.0]                  # 上一次"拖动即下发"的时刻（限 20 Hz）
    move_last = [0.0]                    # 上一次续发速度的时刻（robotd 的心跳是 500 ms）
    rw_last = [""]                       # 本机最近一次动作（"已发出 `head`，等桥 ack"）

    def _rw_status() -> str:
        """状态行：把"现在为什么不动、下一步按什么"拼出来。每 0.5 s 重算一次。

        这不是日志，是**自检**：合成的一条都不写死 —— 源是不是 telemetry、arm 勾没勾、
        桥最近一条 ack 说了什么、robotd 报的 limp / policy / fallen 是什么，全从活状态读。
        「按了没反应」这类问题，答案就在这几行里（2026-09-30 用户报的那次，是桥只读 +
        robotd 没策略两件事叠在一起，而旧版面板只会显示一句看着像成功的话）。
        """
        if tele is None:
            return ("**串口源：这一组不可用** —— 反向通道只建在 telemetry 源上"
                    "（本进程没连 robotd，没有「发给谁」这件事）。要驱动真机：先按"
                    "「联调命令」里的 ①②③ 切到 telemetry 源。")

        lines = [f"**{'✅ 已 arm' if rw_arm.value else '⛔ 未 arm'}** · 已发 "
                 f"{tele.cmd_sent} 条命令"
                 + (f" · {rw_last[0]}" if rw_last[0] else "")]

        # 最近一条 ack：桥到底接受了没有。桥是只读的时 ack 会写明，这里直接点出来。
        if tele.acks:
            t_ack, a = tele.acks[-1]
            ok = bool(a.get("ok"))
            note = str(a.get("note") or "")
            lines.append(f"{'✅' if ok else '❌'} 桥 ack `{a.get('cmd')}`"
                         f"（{now - t_ack:.1f} s 前）：{note}")
            if not ok and "只读" in note:
                lines.append("→ 桥是**只读**起的：先在「联调命令」里勾上"
                             "「桥带 `--allow-write`」，再按 ② 重启 robotd + 桥")
        elif tele.cmd_sent:
            lines.append("… 已发出，等桥回 ack")
        else:
            lines.append("还没发过命令（arm 一下会自动 ping 一条，用来确认桥能不能写）")

        # robotd 的活状态：这一栏才回答"为什么按了不动"
        why = []
        if tele.limp:
            why.append("robotd 报 **limp** —— 这是 **robotd 自己**的 bringup 状态（它没在"
                       "驱动），**不等于舵机扭矩真的松了**：扭矩是舵机 SRAM 里的 reg40，"
                       "robotd 启动不碰、被 pkill 也不切（v0.17 查证）⇒ 上一轮按过 "
                       "`robot.init`／`robot.enable` 的话关节会是硬的。要真松掉：断电 5 s，"
                       "或按「急停：松扭矩（`robot.relax`）」（桥要带 `--allow-write`）。"
                       "要让 robotd 驱动关节，先按 `robot.init` 上电")
        # policy 是 robotd 那一拍实际在跑的策略名；没加载策略时是 "held"
        if tele.policy and tele.policy not in ("walk", "stand"):
            why.append(f"policy = `{tele.policy}`（**没有策略在跑**）—— 头/颈是在策略的 "
                       "action 里驱动的：robotd 起的时候别带 `--no-policy`，"
                       "或按 `robot.enable` 把策略打开")
        if tele.fallen:
            why.append("robotd 报 **fallen（倒了）** —— 安全状态会压住大部分命令")
        if why:
            lines.append("\n".join("⚠ " + w for w in why))
        return "\n\n".join(lines)

    def _rw_send(cmd: str, params: Optional[dict] = None, force: bool = False,
                 note: str = "") -> bool:
        """一条命令交给桥。force=True 只给急停/归零这种**只减权限**的动作用。

        这里只管"发出去没有"；成不成功要看桥回的 ack（poll 收下来，`_rw_status()` 显示）。
        桥没开 `--allow-write` 时会回一条 ok:false 的 ack —— 那是好事，面板能明说
        "桥拒了"，而不是静默无事发生。
        """
        if tele is None:
            rw_last[0] = "串口源：没有反向通道，命令没发"
            return False
        if not force and not rw_arm.value:
            rw_last[0] = "未 arm：这一组不会发出任何命令"
            return False
        if not tele.send_cmd(cmd, params):
            rw_last[0] = f"❌ 本机没发出去：{tele.cmd_err}"
            print(f"[真机] !! {cmd} 没发出去：{tele.cmd_err}", file=sys.stderr, flush=True)
            return False
        # 只写到"发出去了"，**不写"成功了"** —— 成败由 ack 决定，下一拍状态行会摊开
        rw_last[0] = f"已发出 `{cmd}`，等桥 ack"
        if note:
            print(f"[真机] {note}", flush=True)
        return True

    def _head_params() -> Dict[str, float]:
        return {n: float(head_sl[n].value) for n in HEAD_JOINTS}

    def _send_head() -> None:
        _rw_send("head", _head_params(), note=f"下发头/颈 {_head_params()}")

    def _head_slider(_event=None) -> None:
        """「拖动即下发」：滑块一动就把当前四个角发下去，限 20 Hz（拖动会连发很多次）。"""
        if not head_live.value or not rw_arm.value or tele is None:
            return
        now = time.perf_counter()
        if now - head_pushed[0] < 0.05:
            return
        head_pushed[0] = now
        _send_head()

    for _hn in HEAD_JOINTS:
        head_sl[_hn].on_update(_head_slider)

    @head_send.on_click
    def _head_send_click(_event) -> None:
        _send_head()

    @head_home.on_click
    def _head_home_click(_event) -> None:
        for _hn in HEAD_JOINTS:
            head_sl[_hn].value = 0.0
        _rw_send("head", {n: 0.0 for n in HEAD_JOINTS}, note="头/颈回中位（全 0）")

    @mouth_send.on_click
    def _mouth_send_click(_event) -> None:
        _rw_send("mouth", {"open": float(mouth_open.value)},
                 note=f"下发嘴 open={float(mouth_open.value):.2f}")

    def _twist(note: str = "续发速度指令") -> None:
        _rw_send("move", {"vx": float(mv_vx.value), "vy": float(mv_vy.value),
                          "vyaw": float(mv_vyaw.value)}, note=note)

    def _stop_twist(note: str = "零速") -> None:
        # force：把速度归零是**只减权限**的方向，不该因为"手滑把 arm 取消了"就发不出去
        _rw_send("move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}, force=True, note=note)

    @move_on.on_update
    def _move_toggle(_event=None) -> None:
        if move_on.value:
            if tele is None or not rw_arm.value:
                move_on.value = False
                rw_last[0] = ("未 arm：先把 arm 勾上再开行走" if tele is not None
                              else "串口源：没有反向通道")
                return
            move_last[0] = time.perf_counter()
            _twist("开始行走（500 ms 心跳，5 Hz 续发）")
        else:
            _stop_twist("停止行走，发零速")

    def _estop(cmd: str, note: str) -> None:
        """急停：**不受 arm 约束**（只减权限），顺手把"会持续发东西"的开关全放下。"""
        move_on.value = False
        head_live.value = False
        params = {"on": False} if cmd == "enable" else None
        # 这里**只记"发了哪一条"**，不写"急停成功" —— 以前写的是 `急停：robot.relax
        # （松扭矩，会塌）`，桥明明回 ok:false 也照样这么显示，看着像成功了（v0.12 修）
        _rw_send(cmd, params, force=True, note=f"急停：{note}")

    @estop_btn.on_click
    def _estop_stop(_event) -> None:
        _estop("stop", "robot.stop（零速，仍站立）")

    @relax_btn.on_click
    def _estop_relax(_event) -> None:
        # v0.20：这条**在 robotd 跑着时基本无效** —— HD-1910 收到目标位置 reg42 写入就把
        # reg40 置 1，而 robotd 每拍都在 `sync_write_goal_position`（hold 位姿），所以 relax
        # 写的 0 在 20 ms 内被翻回来。真松要按「联调命令」里的 ⑥（停 robotd 后直写 + 读回）。
        _estop("relax", "robot.relax（松扭矩，会塌）⚠ robotd 跑着时无效，真松请用 ⑥")

    @init_btn.on_click
    def _init_click(_event) -> None:
        # init 会读当前位置、写目标、写增益、开扭矩、ramp 到 home —— 真机会站起来
        _rw_send("init", None, note="robot.init（上电 + ramp 到 home）")

    @enable_btn.on_click
    def _enable_click(_event) -> None:
        _rw_send("enable", {"on": True}, note="robot.enable on（打开策略）")
        pol_on.value = True

    # ---- v0.22：两个策略开关 ----
    #
    # 为什么需要它们：`enable_btn` 打开策略之后**腿就会动** —— 头/颈也是靠策略的
    # action 驱动的，所以"只想动头"也必须开策略；而策略一开，gait 网络就在跑，
    # 腿在**零速度指令**下也动了（现象实测；"策略本身零指令就走"还是"obs/指令喂错"
    # 未验证，别当成结论）；再加上 robotd 检测到坐姿会**先用 sitstand 策略自己起身**、
    # 起身完接着走。于是要两把闸：一把关掉全部策略，一把单独关掉"起身"。

    @pol_on.on_update
    def _pol_on_change(_event=None) -> None:
        """策略总开关 = `robot.enable {on}`。

        开 = 给权限（要 arm）；关 = 收权限（不 arm 也发，`force=True`，和急停一个道理）。
        关掉只是"没人再驱关节"（robotd 退回 hold 位姿），**关节仍然是硬的**。
        """
        if pol_on.value:
            if not rw_arm.value:
                pol_on.value = False
                rw_last[0] = "未 arm：策略总开关没有打开（先勾上面「⚠ arm」）"
                return
            _rw_send("enable", {"on": True}, note="策略总开关 → 开（robot.enable on）")
        else:
            _rw_send("enable", {"on": False}, force=True,
                     note="策略总开关 → 关（robot.enable off：不再驱策略）")

    @sitstand_on.on_update
    def _sitstand_change(_event=None) -> None:
        """起身策略开关 = `robot.loadPolicy {slot:"sitstand", path}`。

        关 = path 写 `"none"`（robotd 允许关任何槽位，**只有 walk 例外** —— 它是
        所有槽位解析不到时的兜底）；开 = 指回那份 standup .onnx。
        关是收权限（不 arm 也发）；开是给权限（要 arm）。

        ⚠ robotd 在策略总开关关着时会拒这条；槽位切换在 **home 位姿**时才发生，
        所以 ack 只回 accepted，真结果看状态行里的 `policy`。
        """
        want = bool(sitstand_on.value)
        if want and not rw_arm.value:
            sitstand_on.value = False
            rw_last[0] = "未 arm：起身策略没有改动（先勾上面「⚠ arm」）"
            return
        sent = _rw_send("loadPolicy",
                        {"slot": "sitstand",
                         "path": SITSTAND_POLICY_PATH if want else "none"},
                        force=not want,
                        note=("起身策略 → 装载 " + SITSTAND_POLICY_PATH if want
                              else "起身策略 → 关掉（sitstand = none）"))
        # 加在 `_rw_send` 之后（它会覆写 rw_last[0]）。robotd 唯一会拒这条的情况是它自己
        # 没加载策略（`--no-policy`），先在面板上说清原因，省得对着英文 refusal 猜。
        if sent and want and lc_np.value:
            rw_last[0] += " · ⚠ ② 勾着 `--no-policy` 起的 robotd 没读槽位，这条会被拒"

    @rw_arm.on_update
    def _arm_change(_event=None) -> None:
        if rw_arm.value:
            # arm 只代表"我允许"，不代表"桥允许"。先 ping 一条把桥的 ack 逼出来 ——
            # 桥没加 --allow-write 时 ping 回的 note 里会写明，面板上一眼看到，
            # 不用等到真发一条命令才发现被拒。
            if tele is not None:
                tele.send_cmd("ping")
            rw_last[0] = "已 arm，ping 了一条探桥能不能写"
            print("[真机] arm 已打开 —— 真机指令组生效", flush=True)
        else:
            move_on.value = False
            head_live.value = False
            _stop_twist("解除 arm，发零速")
            rw_last[0] = ""
            print("[真机] arm 已关闭", flush=True)

    # ---- 在线对比（只跑仿真那一组里的开关；仿真吃真机目标，不动真机）----
    @live_on.on_update
    def _live_toggle(_event=None) -> None:
        if live_on.value:
            if tele is None:
                live_on.value = False
                live_md.content = "❌ 在线对比要 telemetry 源（串口源里没有真机目标流）"
                return
            if analysis.running:
                analysis.stop()                   # 合成指令与在线对比只能用一份记录
            sim_vis.value = True
            _apply_sim_vis()
            sim_twin.set_gravity(grav_on.value)
            sim_twin.reset(data.qpos)
            analysis.start_live(data.qpos)
            an_announced[0] = False
            an_table.content = ""
            an_extra.content = ""
            live_md.content = (
                "● **在线对比中**：仿真逐帧吃真机当拍目标（telemetry 的 `targets`）。"
                "真机由别人驱动 —— 真机不动的话这里两层也不会分开。\n\n"
                f"最多 {LIVE_MAX_FRAMES / CTRL_HZ:.0f} s 自动收尾，也可以取消勾选或按"
                "「停止」；跑完按「导出报告（md + csv）」")
            print("[分析] 在线对比开始：仿真吃真机目标流（本脚本不发任何命令）", flush=True)
        elif analysis.live and analysis.running:
            analysis.stop()
            live_md.content = f"已停止（{len(analysis.sim_hist)} 帧，可导出报告）"
            print("[分析] 在线对比停止", flush=True)

    @start_btn.on_click
    def _an_start(_event) -> None:
        if not analysis_on.value:
            an_state.content = "先把「分析模式」勾上"
            return
        if live_on.value:
            # 合成指令与在线对比共用一个 sim_twin 和一份记录，同时开会互相踩
            live_on.value = False
            live_md.content = "已让位给合成指令（在线对比与合成指令不能同时跑）"
        try:
            cmd = _build_command()
        except Exception as exc:                      # noqa: BLE001 — 路径写错不该崩界面
            an_state.content = f"❌ 指令构造失败：{exc}"
            return
        # 只按**可信**限位夹（实测 / 观测）。XML 的设计值不参与 —— 口径不一致时它会把
        # 本来合法的指令整段挡掉（v0.13）
        cmd.clamp(real_lim)
        sim_vis.value = True                          # 跑分析就把青色亮出来，否则看不到
        _apply_sim_vis()
        sim_twin.set_gravity(grav_on.value)
        sim_twin.reset(data.qpos)                     # 从真机当前姿态起步
        analysis.start(cmd, data.qpos)
        an_announced[0] = False
        an_table.content = ""
        an_extra.content = ""
        an_state.content = (f"● 运行中：{cmd.desc}\n\n共 {cmd.frames} 帧 / "
                            f"{cmd.duration_s:.1f} s，按 {CTRL_HZ:.0f} Hz 实时推进")
        print(f"[分析] 开始 {cmd.kind}：{cmd.desc}", flush=True)

    @stop_btn.on_click
    def _an_stop(_event) -> None:
        if analysis.running:
            analysis.stop()
            an_state.content = f"已手动停止（{len(analysis.sim_hist)} 帧）"
            if analysis.live:
                live_on.value = False        # 开关跟着停，免得看着像还在跑
                live_md.content = (f"已手动停止（{len(analysis.sim_hist)} 帧，"
                                   "可导出报告）")
            print("[分析] 手动停止", flush=True)

    @export_btn.on_click
    def _an_export(_event) -> None:
        if not analysis.sim_hist:
            an_extra.content = "还没有数据，先跑一次分析"
            return
        rows_a, summ_a = compare_series(analysis)
        try:
            md_p, csv_p = write_report(analysis, rows_a, summ_a, grav_on.value)
        except Exception as exc:                      # noqa: BLE001
            an_extra.content = f"❌ 写报告失败：{exc}"
            return
        an_extra.content = f"✅ 报告 `{md_p}`\n\n逐帧明细 `{csv_p}`"
        print(f"[分析] 报告 → {md_p}\n       明细 → {csv_p}", flush=True)

    @sysid_btn.on_click
    def _an_sysid(_event) -> None:
        if not analysis.sim_hist:
            an_extra.content = "还没有数据，先跑一次分析"
            return
        rows_a, _ = compare_series(analysis)
        an_extra.content = "拟合中…（终端有逐点进度，会卡住主循环 1~2 s）"
        try:
            an_extra.content = sysid_suggest(XML, qadr, base_adr, analysis, rows_a,
                                             grav_on.value)
        except Exception as exc:                      # noqa: BLE001
            an_extra.content = f"❌ 拟合失败：{exc}"

    # telemetry 模式下整块跳过：COM8 在 robotd 手里，本脚本一个串口都不开。
    bus: Optional[scg.ServoBus] = None
    if tele is None:
        bus = scg.ServoBus()
        try:
            bus.open(args.port, args.baud, "ft")
        except Exception as exc:                      # noqa: BLE001
            print(f"[!] 舵机总线 {args.port} 打不开：{exc}", file=sys.stderr)
            print("    接在 WSL 上时先 `usbipd detach --busid 1-1` 把设备还给 Windows；"
                  "或者走 telemetry 桥：--telemetry-host 127.0.0.1:8199",
                  file=sys.stderr)
            return 1
        bus.proto = "ft"
        print(f"串口 {args.port} @ {args.baud:,} bps / FT-SCS 已打开，只读镜像开始", flush=True)
    else:
        print("telemetry 源：不开串口（COM8 归 robotd），只读镜像开始", flush=True)

    smoother = Smoother()
    up_smooth: List[float] = [0.0, 0.0, 1.0]
    frame = 0
    # 计时一律用 perf_counter：Windows + Python 3.12 的 monotonic() 走 GetTickCount64，
    # 分辨率只有 15.625 ms，本帧耗时会一律读成 0.0。
    t0 = time.perf_counter()
    misses: Dict[int, int] = {sid: 0 for sid in JOINT_IDS}
    # 总线自愈：[0] 当前是否掉线 / 上次重连尝试时刻 / 最近一次异常文本。
    bus_down: List[bool] = [False]
    bus_retry: List[float] = [0.0]
    bus_exc: List[str] = [""]
    # v0.19：数据源从哪一刻起不再给新值。用来把"表里还印着的那堆数字"如实标成
    # **陈旧**而不是"当前值" —— ① 把 COM8/COM6 让给 WSL 之后，主循环不再读串口，
    # `states` 会一直留着让开前最后一帧的 raw/电压/温度/电流，表格照印不误，
    # 只看表格根本看不出它已经停了（实测就撞到过：面板显示 49 Hz、15 颗"缺失"，
    # 表里电压温度电流却还是一条条排着，像在读）。
    bus_down_since: List[float] = [0.0]
    # 告警历史：只记"新出现的"告警，同一类持续存在不会每 0.5 s 刷一条。
    alarms: Deque[Tuple[float, str]] = deque(maxlen=12)
    last_warn: set = set()

    def _alarm(msg: str) -> None:
        alarms.append((time.perf_counter(), msg))
        print(f"[告警] {msg}", flush=True)

    try:
        while True:
            t_start = time.perf_counter()

            # 面板 -> 全局标定表（每帧同步一次，滑块一拖 3D 立即跟着变）
            for name in CALIB_NAMES:
                SIGN[name] = -1.0 if sign_box[name].value else 1.0
                OFFSET[name] = off_slider[name].value

            # ---- 舵机：一次广播读拿全 15 颗 ----
            # 串口掉了（USB 松了、适配器复位）时 pyserial 会抛异常而不是返回空 —— 整个
            # 循环兜住它：先记一笔告警，然后按 1 s 一次的节奏重开端口，重开成功就继续跑，
            # 不用重启脚本。
            t_bus = time.perf_counter()
            if tele is not None:
                # 桥把整帧翻好了：states/missing 直接拿。这里只 poll + 报告，不重连 ——
                # 重连是桥那一侧的活（1 s 一次连 robotd），这边断了就等它自己回来。
                tele.poll()
                states = tele.states
                missing = tele.missing
                bus_down[0] = not tele.ok
                if not tele.ok:
                    bus_exc[0] = tele.note or "桥断了"
                bus_ms = (time.perf_counter() - t_bus) * 1000.0
            else:
                if bus_released[0]:
                    # ① 已经把串口交给 WSL 了：这里既不再读、也不重开 —— 重开会把设备从
                    # WSL 那边抢回来，attach 就会失败。数据要按 ③ 切到 telemetry 源才有。
                    blocks = {}
                    bus_down[0] = True
                    bus_exc[0] = "串口已让给 WSL（按 ③ 切到 telemetry 源才有数据）"
                else:
                    try:
                        blocks = bus.sync_read(JOINT_IDS, BLOCK_ADDR, BLOCK_LEN,
                                               timeout=0.04)
                        if bus_down[0]:
                            bus_down[0] = False
                            print(f"[总线] {args.port} 恢复正常", flush=True)
                    except Exception as exc:                  # noqa: BLE001
                        blocks = {}
                        bus_down[0] = True
                        bus_exc[0] = f"{type(exc).__name__}: {exc}"
                bus_ms = (time.perf_counter() - t_bus) * 1000.0
                if bus_down[0] and not bus_released[0] \
                        and time.perf_counter() - bus_retry[0] > 1.0:
                    bus_retry[0] = time.perf_counter()
                    try:
                        bus.close()
                        bus.open(args.port, args.baud, "ft")
                        bus.proto = "ft"
                        bus_down[0] = False
                        print(f"[总线] {args.port} 重连成功", flush=True)
                    except Exception:                         # noqa: BLE001
                        pass                                  # 再等 1 s；USB 没回来的话别刷屏
                missing = []
                for sid in JOINT_IDS:
                    st = decode_block(sid, blocks[sid]) if sid in blocks else None
                    if st is None:
                        # 没读到、或读到了量程外的脏位置（见 decode_block）：
                        # 两种都算这一颗这一拍缺 —— 保留上一帧位姿，别把垃圾喂给
                        # 3D / 录制 / 观测窗。
                        missing.append(sid)
                        misses[sid] += 1
                    else:
                        states[sid] = st

            # ---- 关节角 -> qpos（含平滑） ----
            # v0.19：先记下"从哪一刻起没有新值"，下面表格据此把残留值标成陈旧。
            if bus_down[0] or bus_released[0]:
                if bus_down_since[0] == 0.0:
                    bus_down_since[0] = time.perf_counter()
            else:
                bus_down_since[0] = 0.0
            stale_s = (time.perf_counter() - bus_down_since[0]
                       if bus_down_since[0] else 0.0)

            alpha, deadband = smooth_h.value, dead_h.value
            for sid, name in JOINT_TABLE:
                st = states.get(sid)
                if name is None or st is None:
                    continue
                angle = smoother.update(name, joint_angle(st, name), alpha, deadband)
                data.qpos[qadr[name]] = angle

            # ---- IMU -> 基座姿态 ----
            imu_new = imu.poll() if (imu is not None and not imu_released[0]) else False
            imu_ok = imu is not None and imu.accel is not None
            base_rpy: Tuple[float, float, float] = (0.0, 0.0, 0.0)
            if base_adr is not None:
                data.qpos[base_adr:base_adr + 3] = (0.0, 0.0, 0.15)
                if imu_ok and follow_imu.value and base_raw.value:
                    # 原值直通：不归零 yaw、不做 up 向量 EMA、也不叠三个修正滑块。
                    # telemetry 源下这就是 robotd 报的四元数原样；串口源下是本页融合的
                    # 结果（仍然是"没有再加工"的那一份）。
                    q_base = [float(v) for v in imu.attitude.quat]
                    data.qpos[base_adr + 3:base_adr + 7] = q_base
                    base_rpy = euler_xyz(q_base)
                elif imu_ok and follow_imu.value:
                    # 躯干 +Z 在世界系里的方向（q_from_up_yaw 要的就是这个）。
                    # 注意：**不能**写成 -gravity。gravity 是世界"下"在躯干系里的表达
                    # R⁻¹·(0,0,−1)，取反得到 R⁻¹·(0,0,1)，即"世界向上在躯干系里"；
                    # 而这里要的是 R·(0,0,1)，差一个转置。两者只在直立时相等，一旦倾斜
                    # 俯仰和横滚的横向分量就整体反号 —— 表现为模型往反方向倒。
                    up = q_rotate(imu.attitude.quat, (0.0, 0.0, 1.0))
                    # 先给 up 做一次 EMA，IMU 噪声直接进基座的话整个模型都会抖
                    a = grav_h.value
                    if a > 0.0:
                        up_smooth = [up_smooth[i] + a * (up[i] - up_smooth[i])
                                     for i in range(3)]
                        up = v_norm(up_smooth)
                    else:
                        up_smooth = list(up)
                    yaw = imu.attitude.yaw - yaw_zero[0]
                    q_imu = q_from_up_yaw(up, yaw)
                    # 三个滑块在 IMU 模式下是修正量，按世界轴前置乘上去
                    q_off = np.zeros(4)
                    mujoco.mju_euler2Quat(q_off, np.array([roll_h.value, pitch_h.value,
                                                           yaw_h.value]), "xyz")
                    q_base = q_mul(list(q_off), q_imu)
                    data.qpos[base_adr + 3:base_adr + 7] = q_base
                    base_rpy = euler_xyz(q_base)
                else:
                    quat = np.zeros(4)
                    base_rpy = (roll_h.value, pitch_h.value, yaw_h.value)
                    mujoco.mju_euler2Quat(quat, np.array(base_rpy), "xyz")
                    data.qpos[base_adr + 3:base_adr + 7] = quat

            mujoco.mj_forward(model, data)
            scene.update_from_mjdata(data)

            # ---- 真机这一拍收到的目标角（rad）----
            # 幽灵层（橙）和在线对比（青）用的是同一份数：目标指的是"发给舵机的指令"，
            # 与实线那层的实测反馈（reg56）配一对，才看得出跟随误差/模型失配。
            real_tg: Dict[str, float] = {}
            real_now: Dict[str, float] = {}
            for sid, name in JOINT_TABLE:
                st = states.get(sid)
                if name is None or st is None:
                    continue
                real_tg[name] = joint_target(st, name)
                real_now[name] = joint_angle(st, name)

            # ---- 速度指令的 500 ms 心跳 ----
            # robotd 的 deadman 到点会把速度归零（机器人站住），所以「行走中」勾着时按
            # 5 Hz 续发。取消勾选 / 急停 / 关 arm 都会立刻发一条零速，不等心跳过期。
            if (move_on.value and tele is not None and rw_arm.value
                    and time.perf_counter() - move_last[0] >= 0.2):
                move_last[0] = time.perf_counter()
                _twist("续发速度（500 ms 心跳）")

            # ---- 目标幽灵：与实线模型同一个基座，关节角换成舵机目标位置 ----
            if ghost is not None and ghost_on.value:
                # 实线场景开了 camera tracking 时会整体平移 -xpos[tracked]，幽灵要跟
                # 同一个偏移，否则两层会错开一个躯干的位置。tracked 取自实线 data。
                if scene.camera_tracking_enabled and ghost.tracked is not None:
                    scene_offset = -data.xpos[ghost.tracked].copy()
                else:
                    scene_offset = np.zeros(3)
                ghost.update(data.qpos, real_tg, scene_offset)

            # ---- 分析：指令喂给仿真，推进一个控制周期，并与真机同一帧对照 ----
            # 仿真每个子步都把基座钉在真机当前姿态上（台架条件：吊着、腿悬空）。
            # 两种模式的"指令"来源不同：合成指令来自面板，在线对比来自真机当拍目标。
            if analysis.running:
                # 在线对比完全依赖真机目标流：桥断线的那几拍 real_tg 是空的，这时候
                # 别喂空目标、也别记帧 —— 空字典进 cmd_hist 会让逐关节表在导出时读不到
                # 键直接抛 KeyError，而"记了一堆空帧"本身也是假的。
                tg_ok = (not analysis.live
                         or all(n in real_tg for n in JOINT_NAMES_MJ))
                if tg_ok:
                    sim_twin.set_targets(real_tg if analysis.live
                                         else (analysis.next_targets() or {}))
                    sim_twin.advance(data.qpos)
                    analysis.record(sim_twin.angles(), real_now, sim_twin.saturated(),
                                    cmd=real_tg)
                if not analysis.running and not an_announced[0]:
                    an_announced[0] = True
                    extra = "（撞到上限自动收尾）" if analysis.hit_cap else ""
                    print(f"[分析] 完成{extra}，{len(analysis.sim_hist)} 帧；"
                          f"「导出报告」出 md + csv", flush=True)

            # 仿真层（青）：平时就是真机姿态的镜像 —— 两层本该重合，这是用来验证"XML 模型
            # 与装出来的实机是同一个姿态"的。只有分析正在跑的那一段，青色才按仿真
            # 自己走（那时两层分开才是有含义的预测偏差）。跑完立刻回到镜像。
            if sim_shown[0]:
                if scene.camera_tracking_enabled and sim_layer.tracked is not None:
                    sim_off = -data.xpos[sim_layer.tracked].copy()
                else:
                    sim_off = np.zeros(3)
                if analysis.running:
                    sim_layer.update(sim_twin.data.qpos, {}, sim_off)
                else:
                    sim_layer.update(data.qpos, {}, sim_off)

            # ---- 录制 ----
            if recorder.on:
                recorder.write(record_row(
                    time.perf_counter() - recorder.t0, states, frame,
                    imu.attitude.quat if imu_ok else None,
                    imu.gravity if imu_ok else None,
                    # v0.18：真姿态与"3D 用的那份"分两列写。真姿态从 quat 换算，
                    # 与 imu_w..grav_z 是同一份数据的两种表达；base_rpy 含显示变换。
                    euler_xyz(imu.attitude.quat) if imu_ok else None,
                    base_rpy if imu_ok else None,
                    imu.hw_quat if imu is not None else False))

            frame += 1
            if frame <= args.show:
                dump_table(states, f"第 {frame} 帧")

            # ---- 面板刷新：2 Hz 就够，GUI 更新也是网络消息 ----
            if frame % 25 == 0:
                now = time.perf_counter()
                elapsed = now - t0
                hz = 25.0 / elapsed if elapsed > 0 else 0.0
                t0 = now
                imu_hz, imu_bad = imu.stats() if imu is not None else (0.0, 0.0)
                print(f"[{frame:>5} 帧] 本帧 {(now - t_start) * 1000:.1f} ms"
                      f"（{'桥' if tele is not None else '总线'} {bus_ms:.1f}）  {hz:.1f} Hz  "
                      f"IMU {imu_hz:.1f} Hz 缺失 {missing or '无'}", flush=True)
                dump_table(states, "当前")

                # 本帧的健康指标：既用来拼看板，也用来判告警。只统计 live 的读数 ——
                # telemetry 源逐颗电压/温度/电流没上线，把 0.0 当读数会刷一屏假"电池空"。
                volts = [s.volt for s in states.values() if s.live]
                temps = [s.temp for s in states.values() if s.live]
                currents = [abs(s.current_ma) for s in states.values() if s.live]

                # 告警：同一类（按文本）只在"刚出现"时记一次，持续存在不刷屏。
                current: set = set()
                if bus_down[0]:
                    current.add(f"{'telemetry 桥' if tele is not None else '舵机总线'}掉线："
                                f"{bus_exc[0]}")
                if missing:
                    current.add(f"缺答 {len(missing)} 颗：{missing}")
                if imu is not None and not imu_ok:
                    current.add("IMU 无数据")
                if volts:
                    if min(volts) < VOLT_LO:
                        current.add(f"最低电压 {min(volts):.1f} V < {VOLT_LO} V"
                                    "（robotd 会判电池空）")
                    if max(volts) - min(volts) > 0.5:
                        current.add(f"电压离散度 {max(volts) - min(volts):.1f} V，查接线")
                if temps and max(temps) >= TEMP_WARN:
                    current.add(f"最高温度 {max(temps):.0f} ℃ ≥ {TEMP_WARN:.0f} ℃")
                if currents and max(currents) >= CURR_WARN_MA:
                    current.add(f"最大电流 {max(currents):.0f} mA")
                if tele is not None:
                    # telemetry 源特有：整包电压/最热/环频/倒地，都是 robotd 报的
                    if tele.bat is not None and tele.bat < VOLT_LO:
                        current.add(f"电池 {tele.bat:.1f} V < {VOLT_LO} V（robotd 判空）")
                    if tele.tmax is not None and tele.tmax >= TEMP_WARN:
                        current.add(f"最热 {tele.thot or '—'} {tele.tmax:.0f} ℃ ≥ "
                                    f"{TEMP_WARN:.0f} ℃")
                    if tele.fallen:
                        current.add("robotd 报 fallen：机器人倒地（策略已停/跛行）")
                    if tele.limp:
                        current.add("robotd 报 limp：扭矩已松")
                    # v0.18：robotd 的姿态是不是真值 —— 假姿态比没有姿态更危险，因为
                    # 它长得跟真的一模一样（见 _imu_truth_md 的说明）
                    if tele.imu_ready is False:
                        current.add("robotd 报 IMU **未就绪**：姿态可能是"
                                    "「假定直立静止」的假值（查 [bus] imu_port）")
                    elif tele.imu_frozen > 25:
                        current.add(f"robotd 姿态冻结：quat 连续 {tele.imu_frozen} 帧不变")
                    if 0.0 < tele.loop_hz < 40.0:
                        current.add(f"robotd 控制环 {tele.loop_hz:.0f} Hz < 40")
                for msg in sorted(current - last_warn):
                    _alarm(msg)
                last_warn = current

                if tele is not None:
                    conn_md.content = (
                        f"**源** telemetry `{args.telemetry_host}` "
                        + ("✅ · 本机刷新 " if tele.ok else "❌ " + (tele.note or "掉线") + " · ")
                        + f"{hz:.1f} Hz\n\n"
                        f"**舵机** {'✅' if not missing else '⚠ 缺 ' + str(missing)} "
                        f"ROBOTD 推的 15 颗关节角 / 目标角\n"
                        f"（逐颗电压 · 温度 · 电流没上线，表里显示 `—`）\n\n"
                        + _imu_truth_md(tele)
                        + f"\n\n**robotd** 策略 `{tele.policy or '—'}` · 环 {tele.loop_hz:.0f} Hz"
                        f" · missed {tele.missed}"
                        + (f" · gain {tele.gain}" if tele.gain is not None else "")
                        + (f"\n\n**电池** {tele.bat:.2f} V · **最热** "
                           f"{tele.thot or '—'} {tele.tmax:.0f} ℃"
                           if tele.bat is not None else ""))
                else:
                    conn_md.content = (
                        f"**舵机** {'✅' if not missing else '⚠ 缺 ' + str(missing)} "
                        f"`{args.port}` @ {args.baud:,} · {hz:.1f} Hz\n\n"
                        f"**IMU** " + (
                            f"✅ `{args.imu_port}` · {imu_hz:.1f} Hz"
                            + (f" · 丢弃行 {imu_bad * 100:.0f}%" if imu_bad > 0.01 else "")
                            + (" · 姿态：芯片 SFLP 直出" if imu.hw_quat
                               else " · 姿态：主机 Mahony（四元数列还是空的）")
                            if imu is not None else
                            f"❌ 未接入（{imu_error or '--no-imu'}）· 基座用手工滑块"))

                bus_md.content = (f"应答 {len(states)}/{NUM_JOINTS} · "
                                  f"本帧 {(now - t_start) * 1000:.1f} ms"
                                  + (f"（桥 {bus_ms:.1f} ms 收一帧 JSON）"
                                     if tele is not None
                                     else f"（总线 {bus_ms:.1f} ms 读 15 颗 × 15 字节）")
                                  + ("\n\n**当前告警**\n\n"
                                     + "\n\n".join(f"⚠ {m}" for m in sorted(current))
                                     if current else ""))

                # 年龄是 `now - t`（_alarm 存的是发生那一刻的 perf_counter）。写反过
                # 来会显示「-3 s 前」这种负数（v0.12 顺手修；telemetry 源下每帧都在
                # 刷 robotd 状态，肉眼很容易撞见）
                alarms_md.content = "暂无告警" if not alarms else "\n\n".join(
                    f"`{now - t:.0f} s 前` {m}" for t, m in reversed(alarms))

                if recorder.on:
                    rec_md.content = (f"● 录制中 → `{recorder.path}`\n\n"
                                      f"已写 {recorder.rows} 行（约 {hz:.0f} 行/s）")
                elif recorder.rows:
                    rec_md.content = f"已停止，{recorder.rows} 行 → `{recorder.path}`"

                # 分析状态：跑的时候看进度，跑完就把逐关节差异表摊开
                if analysis.running or analysis.sim_hist:
                    if analysis.running:
                        an_state.content = (f"● 运行中 {analysis.progress * 100:.0f}%："
                                            f"{analysis.desc}")
                        if analysis.live:
                            live_md.content = (
                                f"● **在线对比中**：仿真逐帧吃真机当拍目标"
                                f"（已 {analysis.k} 帧，上限 {LIVE_MAX_FRAMES}）。"
                                "真机由别人驱动 —— 真机不动，两层就不会分开。")
                    else:
                        rows_a, summ_a = compare_series(analysis)
                        extra = ("" if not summ_a["sat_joints"] else
                                 "；⚠ 饱和关节 " + "、".join(summ_a["sat_joints"]))
                        tail = ("\n\n（撞到上限自动收尾，可再勾一次继续）"
                                if analysis.hit_cap else "")
                        an_state.content = (
                            f"✅ 完成：{analysis.desc}\n\n"
                            f"{summ_a['frames']} 帧 / "
                            f"{float(summ_a['duration_s']):.1f} s；真机被激励 "
                            f"{summ_a['excited']} 个关节" + extra + tail)
                        an_table.content = format_metric_table(rows_a, summ_a)
                        if analysis.live:
                            live_md.content = (
                                f"✅ 在线对比完成（{summ_a['frames']} 帧，真机被激励 "
                                f"{summ_a['excited']} 个关节）。按「导出报告（md + csv）」"
                                "出逐关节 RMSE / 滞后 / 饱和率")
                            if live_on.value:
                                live_on.value = False      # 跑完把开关放下，免得看着像还在跑

                # 反向命令的状态：桥的 ack 是异步回来的，2 Hz 刷一次就够看
                if tele is not None:
                    if tele.acks:
                        t_ack, a = tele.acks[-1]
                        note = str(a.get("note") or "")
                        if a.get("ok"):
                            rw_ack.content = (f"✅ `{a.get('cmd')}` · {note}"
                                              f"（{now - t_ack:.1f} s 前，本机已发 "
                                              f"{tele.cmd_sent} 条）")
                        else:
                            hint = ("\n\n桥是**只读**的：WSL 那边要带 `--allow-write` "
                                    "重启桥，真机才会动" if "只读" in note else "")
                            rw_ack.content = (f"❌ `{a.get('cmd')}` 被拒：{note}"
                                              f"{hint}")
                    elif tele.cmd_sent:
                        rw_ack.content = f"已发 {tele.cmd_sent} 条，等桥回 ack…"
                    else:
                        rw_ack.content = ("还没发过命令（arm 一下会 ping 一条，用来确认"
                                          "桥是不是 `--allow-write`）")

                # 「真机指令」的状态行：每 0.5 s 从活状态重算（v0.12）。放在 `if tele`
                # 外面 —— 串口源时它要显示"这一组不可用"，那句话也是自检的一部分。
                rw_state.content = _rw_status()

                # 一键启动的结果回显：命令都在线程里跑，这里 2 Hz 把最近几条摊开。
                # 每条都带退出码与耗时，输出只留尾部 14 行（wsl/usbipd 废话挺多）。
                if lc_jobs:
                    blocks = []
                    for j in reversed(lc_jobs[-4:]):
                        try:
                            rc = int(j["rc"])            # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            rc = -1
                        head = (f"{'✅' if rc == 0 else '❌'} **{j['label']}**"
                                f" · rc={rc} · {float(j['sec']):.1f} s")
                        body = str(j["out"]) or str(j["err"]) or "（无输出）"
                        tail = body.splitlines()[-14:]
                        blocks.append(head + "\n\n" + "\n\n".join(
                            f"`{ln}`" for ln in tail))
                    lc_out.content = ("\n\n---\n\n".join(blocks)
                                      + "\n\n---\n\n"
                                      f"最近的命令：`{lc_jobs[-1]['cmd']}`")

                rows = ["| 关节 | ID | raw | 角度 | 电压 | 温度 | 电流 | 状态 |",
                        "|---|---|---|---|---|---|---|---|"]
                # v0.18：**这一列显示真实角**，3D 用的平滑值只在有差别时才附在右边
                # （`→3D …`）。以前这一列直接放 `smoother.get()`，开了平滑之后表里
                # 的数是"显示量"，却没有任何标记 —— 那是"为了显示而显示"。
                _sm = (smooth_h.value, dead_h.value)
                if stale_s > 0.0:
                    rows.append("")
                    rows.append(f"⚠⚠ **数据源已停 {stale_s:.0f} s，下表整列都是断流前的"
                                f"历史值，不是当前状态**（{bus_exc[0] or '总线掉线'}）")
                    rows.append("")
                elif _sm[0] > 0.0 or _sm[1] > 0.0:
                    rows.append("")
                    rows.append(f"⚠ 3D 用的是平滑后的值（EMA α={_sm[0]:.2f}，"
                                f"死区 {_sm[1]:.3f} rad）；表中「角度」列与录制 CSV 都是"
                                f"真值，`→3D …` 才是送进模型的那一份")
                    rows.append("")
                for sid, name in JOINT_TABLE:
                    if name is None:
                        _st = states.get(sid)
                        rows.append(fmt_servo_row(
                            MOUTH_NAME, sid, _st,
                            joint_angle(_st, MOUTH_NAME) if _st is not None else None,
                            None, stale_s))
                    else:
                        _st = states.get(sid)
                        rows.append(fmt_servo_row(
                            name, sid, _st,
                            joint_angle(_st, name) if _st is not None else None,
                            smoother.get(name) if _st is not None else None,
                            stale_s))
                dead = sorted(sid for sid, n in misses.items() if n > 0)
                if dead:
                    rows.append("")
                    rows.append("累计缺答过的 ID：" + ", ".join(str(s) for s in dead))
                table_md.content = "\n".join(rows)

                # 观测窗（v0.13）：每拍把 14 颗的 min/max 收一次。攒下来的就是"这一页
                # 口径下这台实物真的走到过的行程"，按面板上的按钮一键记成限位。
                for _n in JOINT_NAMES_MJ:
                    _st = states.get(SID_OF[_n])
                    if _st is None:
                        continue
                    _a = joint_angle(_st, _n)
                    # 兜底过滤（v0.15）：解码那条路已经把量程外的 raw 挡掉了，这里再
                    # 挡一道非有限/绕圈的值 —— 这一列会被直接写进限位文件，宁可漏记。
                    if not math.isfinite(_a) or abs(_a) > 2.0 * math.pi:
                        continue
                    _w = obs_seen[_n]
                    if _a < _w[0]:
                        _w[0] = _a
                    if _a > _w[1]:
                        _w[1] = _a

                lim_md.content = _limits_table()

                if not imu_ok:
                    imu_md.content = "gravity = —（无 IMU 数据）"
                else:
                    # telemetry 源只有姿态、没有原始加速度计读数，那条 |a| 就别假报恒 1g
                    acc_txt = ("无原始加速度（telemetry 源只有姿态）"
                               if not getattr(imu, "has_raw_accel", True)
                               else f"|a|={math.sqrt(sum(a * a for a in imu.accel)) / G0:.3f} g")
                    # v0.18：**把"IMU 报的真姿态"和"3D 基座实际用的姿态"分开显示**。
                    # 以前只显示 base_rpy，那是经过 yaw 归零 / EMA / 修正滑块之后的**显示用**
                    # 变换 —— 和真值是两回事。现在两个都摊开，并且明说 3D 用的是哪一个。
                    raw_rpy = euler_xyz(imu.attitude.quat)
                    d_max = max(abs(raw_rpy[i] - base_rpy[i]) for i in range(3))
                    if tele is not None:
                        src = ("robotd（独立串口 `imu_port` + 主机 Mahony）"
                               if tele.imu_ready is not False else
                               "robotd **未就绪** —— 这是「假定直立」的假姿态")
                    else:
                        src = ("芯片 SFLP 直出" if imu.hw_quat
                               else "本页主机 Mahony（芯片四元数列还是空的）")
                    imu_md.content = (
                        f"gravity(躯干系) = [{imu.gravity[0]:+.3f}, {imu.gravity[1]:+.3f}, "
                        f"{imu.gravity[2]:+.3f}]  {acc_txt}\n\n"
                        f"**源**：{src}\n\n"
                        f"**IMU 报的真姿态**（roll/pitch/yaw）="
                        f" {math.degrees(raw_rpy[0]):+.1f}° / {math.degrees(raw_rpy[1]):+.1f}°"
                        f" / {math.degrees(raw_rpy[2]):+.1f}°\n\n"
                        f"**3D 基座实际用的**（roll/pitch/yaw）="
                        f" {math.degrees(base_rpy[0]):+.1f}° / {math.degrees(base_rpy[1]):+.1f}°"
                        f" / {math.degrees(base_rpy[2]):+.1f}°"
                        + (("　⚠ 与真值不同 —— 因为叠了 yaw 归零 / up 向量 EMA / 修正滑块；"
                            "要绝对真值就勾「基座用原值」")
                           if d_max > math.radians(0.5) else "　（与真值一致）")
                        + (f"\n\nyaw 零点 {math.degrees(yaw_zero[0]):+.1f}°"
                           + ("（yaw 由陀螺积分，会漂）" if not imu.hw_quat else "")
                           if not base_raw.value else
                           "\n\n**基座原值直通**：yaw 未归零、未做 up 向量 EMA、"
                           "未叠修正滑块")
                        if follow_imu.value else "")

            if args.frames and frame >= args.frames:
                print(f"到达帧数上限 {args.frames}，退出", flush=True)
                break

            if args.hz > 0:
                delay = t_start + 1.0 / args.hz - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
    except KeyboardInterrupt:
        print("停止")
    finally:
        if recorder.on:
            print(f"[录制] 收尾，共 {recorder.rows} 行 → {recorder.path}", flush=True)
        recorder.stop()
        if bus is not None:
            bus.close()
        if imu is not None:
            imu.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())