执行

# Radxa ZERO 3W 零基础开发教程：模型部署、硬件调试与软件开发

> 承接《[Radxa_ZERO_3W_零基础部署调试教程.md](./Radxa_ZERO_3W_零基础部署调试教程.md)》。
> 那份教程教你**把软件装上**，这份教程教你**自己造软件**：训练一个模型、把它部署上板、
> 调试舵机和传感器、改一行代码并在一分钟内看到它跑在鸭子上。
>
> 全部命令都来自本仓库（`e:\optiDuck\joyandai\microduck`、`microduck_rl`）的真实脚本和文档，
> 标注了来源文件，可直接跳过去看原文。

---

## 怎么读这份教程

这份教程假设你已经走完部署教程（板子能 SSH、`robotctl 0.13.0` 已装、7 个守护进程在跑）。
如果你的板子还没装好，先回去把部署教程 §7、§8 做完。

它分四块，可以跳着读：

| 想做什么                               | 读哪节            |
| -------------------------------------- | ----------------- |
| 想先搞清楚"鸭子系统到底怎么长在一起的" | §1               |
| 想训练/部署一个自己的策略或检测模型    | §2（全链路）     |
| 想调舵机、IMU、ToF、摄像头             | §3               |
| 想改 Rust 代码并推到板子上跑           | §4               |
| 想把上面串起来做一个完整小功能         | §5（端到端演练） |

**三块代码、三个仓库**，先记在脑子里：

| 仓库                      | 本地路径                              | 干什么                                                                                         |
| ------------------------- | ------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `microduck`             | `e:\optiDuck\joyandai\microduck`    | **上板软件栈**：7 个 Rust daemon、`robotctl`/`duckctl`、板级脚本。你的开发主要在这里 |
| `microduck_rl`          | `e:\optiDuck\joyandai\microduck_rl` | **强化学习训练**：MuJoCo + PPO 训走路/站立/翻跟头，导出 ONNX                             |
| `duck_detector`（外部） | 在 GitHub/HF 上                       | **检测器训练**：YOLO 模型训练 + ONNX→RKNN 转换（不在本地仓库里）                        |

还有一个"仓库"不是代码：**Hugging Face Hub**。策略（`microduck-*`）、检测器
（`microduck-duck-detector`）都以"文件 + 一个 `manifest.json`"的形式发布在 HF 上，
板子通过 `updaterd` 从 HF 拉取。**模型和策略的更新不经过 daemon 发版**——这是全篇最重要的概念之一。

---

## 🎯 0. 路线图：从"会部署"到"会开发"

先看整张图，心里有数再逐节拆。三条链路，本文各占一节：

```text
                        ┌────────────────────────────────────────────────┐
   模型链路 (§2)        │                                                │
   ┌────────────┐  ┌──────┴──────┐  ┌───────────┐  ┌───────────────────┐ │
   │ 训练       │  │ 导出 ONNX   │  │ 转 RKNN   │  │ 发布到 HF Hub     │ │
   │ microduck_ │ →│ scripts/    │ →│ (检测器)  │→ │ policies /        │ │
   │ rl (PPO)   │  │ export.py   │  │ to_rknn.py│  │ duck-detector     │ │
   └────────────┘  └─────────────┘  └───────────┘  └─────────┬─────────┘ │
                                                          │ updaterd     │
   硬件链路 (§3)                                           ▼             │
   ┌────────────────────────┐        ┌────────────────────────────────┐  │
   │ robotctl health/monitor│        │ /opt/robot/policies/current    │  │
   │ 舵机·IMU·ToF·摄像头    │────────│ /opt/robot/detector/current    │  │
   └────────────────────────┘        └──────────┬─────────────────────┘  │
                                                │ robotd 50 Hz 循环      │
   代码链路 (§4)                                │                        │
   ┌──────────┐  ┌──────────┐  ┌──────────────┐ │                        │
   │ 改代码   │ →│ 交叉编译 │ →│ dev-push.sh  │─│ 打包+签名+apply+健康门  │
   └──────────┘  └──────────┘  └──────────────┘ └────────────────────────┘
```

一句话版本：

- **模型 = 被动数据**（`.onnx` / `.rknn` + `manifest.json`），走 **HF Hub → updaterd** 通道，
  更新**不重启 daemon 之外的任何东西**，随时可以回退；
- **代码 = 主动逻辑**（Rust 二进制 + 板级脚本），走 **dev-push / release** 通道，
  每次替换都要过**签名校验 + 健康门 + 自动回滚**；
- **硬件 = 你调试的对象**，全部以 `robotctl health` / `monitor` 为观测入口。

> 💡 **部署教程讲"装"，这里讲"造"。** 你在部署教程里见过的 `policies/current → seed-v5`、
> `detector/current → seed-duck-v1` 这两个符号链接，就是模型链路的终点——读完 §2 你会知道
> 它们是怎么被填上的、以及如何填上你自己的。

---

## 1. 先建立全局观：这个系统是怎么长在一起的

### 1.1 一张图认识 7 个守护进程

部署教程只让你"装上了"，没讲它们各自是什么。这里补上（来自
`docs/design/architecture.md`）：

```text
  手柄(padd)    手机(btd)    你/SSH(robotctl)   远端(mediad)      GitHub release
     │ BLE         │ BLE          │ ssh             │ WebRTC          │ https
     ▼             ▼              ▼                 ▼                 ▼
  ┌─────┐      ┌─────┐      ┌─────────┐       ┌─────────┐        ┌─────────┐
  │padd │      │ btd │      │ robotctl│       │ mediad  │        │ updaterd│
  └──┬──┘      └──┬──┘      └────┬────┘       └────┬────┘        └────┬────┘
     │            │   JSON-RPC 2.0 · 一行的 NDJSON · unix socket      │
     ▼            ▼            ▼                  ▼                   ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  robotd（50 Hz 控制循环，唯一能碰电机的人）   configd（Wi-Fi/名字/配对）│
  │  tofd（ToF 8×8 深度矩阵，只发不收）                                  │
  └──────────────────────────────────────────────────────────────────────┘
                          │ 一条 Dynamixel UART（/dev/ttyS2）
                          ▼
              15 个舵机 + IMU（id 200，你的鸭子为 HD1910）
```

**三个必须记住的硬规则**（后面调试全靠它们）：

1. **`robotd` 是唯一能命令电机的人。** 其他人只能发"意图"（走多快、看哪里、站起来），安全性由 `robotd` 裁决。所以舵机总线出问题，先怀疑 `robotd`。
2. **`configd` / `updaterd` / `btd` 不依赖 `robotd`。** 机器人控制循环挂了，你还能改 Wi-Fi、更新、回滚——这是"永不砖"的根基。
3. **服务和数据分两条路：** 命令/状态走 unix socket JSON-RPC（控制面），视频/音频帧**绝不**走 socket（数据面）。所以"摄像头画面卡了"和"电机不动了"永远是两个问题。

### 1.2 两个必须分清的词：`slot` 与 `skill`、`policy` 与 `detector`

| 词                           | 意思                                                                                                                              | 例子                                               |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| **slot（槽位）**       | 机器人**默认**跑什么：7 个固定槽位 `walk`/`stand`/`sitstand`/`ground_pick`/`kick_left`/`kick_right`/`roulade` | `walk` 槽装走路步态                              |
| **skill（技能）**      | 机器人**被要求时**跑什么：一次性的动作，跑完自己回去                                                                        | 鞠躬`polite-bow`、前滚翻 `roulade`             |
| **policy（策略）**     | 从 RL 训练出的`obs[1,61] → actions[1,14]` 的 ONNX 模型                                                                         | `walk.onnx`                                      |
| **detector（检测器）** | 从图像训练出的目标检测模型（YOLO），输入 320×320 帧，输出 2100 个候选框                                                          | `duck_detect.rknn`（NPU）/ `.onnx`（CPU 兜底） |

```text
robotctl policy list   # 看 7 个槽位 + 已装的 skills
robotctl policy add polite-bow <HF repo>   # 加一个技能
robotctl robot do polite-bow               # 让它跑一下
```

> 💡 **`slot` 由配置指向某个模型文件，`skill` 由名字被调用。** 一个策略文件放对地方（槽位/技能），就是"部署"的全部。

### 1.3 三条更新通道：什么走哪条，为什么

| 更新内容                      | 通道                                                                                    | 门槛                           | 失败后果                 |
| ----------------------------- | --------------------------------------------------------------------------------------- | ------------------------------ | ------------------------ |
| **策略 / 检测器模型**   | HF Hub →`updaterd`（`robotctl policy update` / `robotctl duck-detector update`） | 只需`manifest.json` 字段合法 | 拒绝安装，现有模型不动   |
| **daemon 代码（发版）** | GitHub release →`robotctl update apply daemon`                                       | 签名 + 健康门                  | 自动回滚到上一个 release |
| **你的开发代码**        | `dev-push.sh`（用开发密钥签名）→ `robotctl update apply --from`                    | 开发板 + dev key               | 同上，自动回滚           |

**为什么模型单独一条通道？** 因为"换一个策略"和"换一个 daemon"是两种风险等级：
策略文件是数据，`robotd` 每次加载前都会校验形状（`obs_len`/`action_len`/`model_api`/`robot.model`），
错了顶多拒绝加载；而 daemon 二进制是代码，错了可能让板子失联，所以必须过签名 + 健康门 + 回滚。
记住这个分层，后面所有操作都不迷路。

### 1.4 你手上的三板斧（先记住，后面反复用）

```bash
robotctl version          # 每个 daemon 实际跑的版本 vs 已安装版本（先跑这个，别信记忆）
robotctl health           # 硬件 + 软件一份报告；不健康时退出码非 0（可接脚本）
robotctl monitor          # 实时：指令 vs 实际、IMU 重力、循环频率、电量和温度
```

`robotctl monitor` 是调试主力，先记几个键：`q` 退出、`t` 打开 ToF 矩阵、`c` 打开摄像头、
`d` 关掉右侧机器人小图、`p` 打开手柄输入流。终端不够宽时按 `d` 关掉机器人图。

---

## 2. 模型部署：从训练到上板（全链路）

> 目标：这一节结束，你能把一个**自己训出来的模型**（或仓库里已有的模型）放到鸭子上跑起来，
> 并知道每一步卡住时去哪查。全链路五段：

```text
① 训练 (microduck_rl)  →  ② 导出 ONNX  →  ③ 彩排 (CPU)  →  ⑤ 上板+验证
   uv run train            export.py         infer_policy /      robotctl policy /
                                               policy-rehearsal    duck-detector

④ 发布到 HF 是**可选分叉**，插在 ③ 和 ⑤ 之间：
   · 自己板子上用        → 跳过 ④，ONNX 拷到板子直接占槽位（local 来源）
   · 要叫名字/绑手柄/分享 → 先 ④（uv run publish），再装成技能
```

策略和检测器是**两条平行的链**（训练仓不同、转换不同、上板命令不同），分开讲，最后汇总一张对照表。（uv一个包及命令管理器，可以给你的每个命令单独创造一个环境进行运行，速度快、兼容好）

### 2.1 训练一个走路策略（最短路径）

训练仓是 `microduck_rl`，基于 mjlab（MuJoCo + Warp）跑 PPO（rsl_rl）。

mjlab：一个物理机器学习模型，用来分配及观测每次**PyTorch**的结果。一次训练迭代流程如下

1. > **mjlab 重置并并行运行数千个仿真环境**
   > 基于 MuJoCo Warp，在 GPU 上批量步进物理世界。
   >
2. > **策略网络（PyTorch）接收观测，输出动作**
   > 观测是 PyTorch 张量，策略网络也是 PyTorch `nn.Module`，两者共享 GPU 内存，零拷贝。
   >
3. > **mjlab 把动作送入物理引擎，计算新观测和奖励**
   > 奖励、终止条件、课程学习、域随机化都由 mjlab 定义。
   >
4. > **RSL-RL 等算法层用 PyTorch 计算损失和梯度**
   > mjlab 收集 rollout 数据，RSL-RL 调用 PyTorch 的 `autograd` 和 `optimizer` 更新策略参数。
   >
5. > **循环直到策略收敛**
   > mjlab 负责“跑环境”，PyTorch 负责“学参数”。
   >

**你的第一目标是跑通冒烟测试**，不是训出好策略——冒烟测试 1–2 分钟，能拦下约 95% 的配置错误
（这是 `microduck_rl/AGENTS.md` 的原话：*Never launch a long run without one*）。

```bash
cd e:\optiDuck\joyandai\microduck_rl

# 0) 先同步依赖（uv 管理，装好 uv 后）
uv sync

# 1) 冒烟测试（必须先跑；64 envs × 5 iterations，1–2 分钟）
uv run train Mjlab-Velocity-Flat-MicroDuck --env.scene.num-envs 64 --agent.max_iterations 5

# 2) 正式训练（需要 CUDA GPU；8–12 GB 显存用 1024 envs，≥16 GB 用 4096）
uv run train Mjlab-Velocity-Flat-MicroDuck \
  --env.scene.num-envs 1024 \
  --agent.max_iterations 15000

# 3) 续训（从某个 checkpoint 接着跑）
uv run train Mjlab-Velocity-Flat-MicroDuck \
  --env.scene.num-envs 1024 \
  --agent.load-checkpoint logs/rsl_rl/velocity/<run>/model_15000.pt \
  --agent.resume True \
  --agent.max_iterations 30000
```

来源：`docs/training_config_velocity_hd1910.md` §10 训练命令、`microduck_rl/AGENTS.md` Commands。

**训练产物**：`logs/rsl_rl/velocity/<timestamp>/model_<iter>.pt`；
曲线在 wandb 项目 `mjlab_microduck`（本地跑是离线 run：`wandb/offline-run-*`）。

#### 训练前必须懂的三件事（否则训出来的东西上不了板）

**① 观测是 61 维，全系统共享，别动它。** 布局固定：`48 维本体感受 + 13 维命令块 [twist(3), head_pose(4), body_pose(6)]`，顺序固定。新任务只改命令块的含义，**不能删槽位**
（AGENTS.md Invariants 第一条）。全零命令 = 站立——这是部署时的空闲状态，必须显式训练过。

**② 关节布局是 14 个舵机**，`ctrl idx = joint idx`：

```text
0–4   左腿：hip_yaw, hip_roll, hip_pitch, knee, ankle
5–8   脖子/头：neck_pitch, head_pitch, head_yaw, head_roll
9–13  右腿
```

**MDP** 是 **Markov Decision Process** 的缩写，中文叫  **马尔可夫决策过程** 。它是强化学习里描述“智能体如何在一系列状态下做决策”的数学框架。

写 MDP 时永远用 `_servo_joint_ids` / `_servo_joint_pos` 辅助函数取关节下标，
**不要硬编码**（换带被动关节的模型时下标会错位）。

**③ 执行器是 BAM 模型**（电压控制的作动器，摩擦力由执行器自己算）。电机参数按型号分开存：
`microduck_rl/vendor/bam/bam/params/hd1910/*.json`（你的鸭子）/ `hls2909`（另一款）。
训练、彩排命令都要带对应 `--motor hd1910`（§2.3）。`dof_frictionloss` 在 BAM 下是零——
给关节摩擦加 DR **（Domain Randomization，域随机化）**要缩放执行器的 `friction_scale`，否则是静默的无效操作。
**为什么按型号分开？因为换执行器 = 重训策略**：官方 XL330 的 ONNX 是给 XL330 的 BAM M6
执行器模型训的，直接换到 HD1910 上不能 bit-exact 复用（OpenMicroDuck `docs/servo.md` §1），
电机型号写在训练/彩排命令里（`--motor hd1910`），从源头就锁对执行器参数。

#### 训练纪律（少走几个月的弯路）

- **奖励符号：** `microduck_mdp` 里自取负的惩罚项（`*_penalty` / `*_l1` 返回 ≤ 0）要用**正权重**，
  否则双重取负变成"奖励违规"。铁律：每个 `Episode_Reward/<penalty>` 在 wandb 里必须 ≤ 0。
- **预算参考：** 简单一次性动作 ≈ 1000 iterations（4096 envs）；走路等复杂步态 4000–6000。
- **失败先测再改：** 别凭感觉调奖励，先 headless 评估 checkpoint，很多"失败"只是 checkpoint 太早。

> 💡 **当前板子实况：** 板子上 `policies/current → seed-v5` 是官方策略集。你训练的第一步
> 不是替换它，而是先在 PC 上跑通"冒烟 → 导出 → 彩排"，等 §2.3 的彩排通过再谈上板。

### 2.2 导出 ONNX（归一化会被烤进模型）

训练完的 checkpoint 不能直接用，必须走**官方导出器**——它会把观测归一化
（`obs_normalization=True`）**烘焙进 ONNX**。手工转换会丢掉归一化，在仿真里玩看不出问题
（play 时环境会再归一化一次），上了真机才露馅。这是 AGENTS.md 明确警告过的坑。

```bash
# 方式 A：从 wandb run 导出
uv run scripts/export.py Mjlab-Velocity-Flat-MicroDuck --wandb-run-path <entity/project/run_id>

# 方式 B：从本地 checkpoint 导出（最常用）
uv run scripts/export.py Mjlab-Velocity-Flat-MicroDuck \
  --onnx-file walk_hd1910.onnx --checkpoint <iter>
```

例如需要导出以下目录模型：E:\optiDuck\microduck_rl\logs\rsl_rl\velocity\2026-09-15_20-44-58_prebom_resume

实际命令为：

```Shell
# 1) 切到训练仓库根目录（导出器按相对路径找 logs/）
cd E:\optiDuck\microduck_rl

# 2) 导出 model_7750.pt → walk_hd1910.onnx（归一化自动烤进 ONNX）
uv run scripts/export.py Mjlab-Velocity-Flat-MicroDuck `
  --checkpoint-file "E:\optiDuck\microduck_rl\logs\rsl_rl\velocity\2026-09-15_20-44-58_prebom_resume\model_7750.pt" `
  --onnx-file walk_hd1910.onnx

# 3) 确认产物（应显示 walk_hd1910.onnx，约几十 MB）
Get-ChildItem walk_hd1910.onnx | Select-Object Name, Length, LastWriteTime
```

3D演示模型的命令如下：

```Shell
cd E:\optiDuck\microduck_rl

uv run play Mjlab-Velocity-Flat-MicroDuck `
  --checkpoint-file "E:\optiDuck\microduck_rl\logs\rsl_rl\velocity\2026-09-15_20-44-58_prebom_resume\model_7750.pt" `
  --num-envs 1
```

导出实现（`microduck_rl/src/mjlab_microduck/export.py` L251-L269）的核心动作：
`runner.export_policy_to_onnx` 只导出 actor（critic 不上板），把观测归一化的均值/方差写成
ONNX 常量，并把 `model_api`、`obs_len`、`action_len` 等写进 metadata。

**产出契约**（也是板子校验的契约，见 `docs/recurrent-policies.md` 与 `docs/policy-manifest.md`）：

|                               | 输入                                | 输出                                      |
| ----------------------------- | ----------------------------------- | ----------------------------------------- |
| 前馈模型                      | `obs [1, 61]`                     | `actions [1, 14]`                       |
| LSTM 模型（`model_api: 2`） | `obs [1, 61]`, `h_in`, `c_in` | `actions [1, 14]`, `h_out`, `c_out` |

所有张量 float32、按名字匹配（与顺序无关）。**LSTM 导出必须标 `model_api: 2`**，
老 daemon 会拒绝安装；前馈是 API 1。

### 2.3 CPU 彩排：上真机前的最后一次检查

彩排解决两个不同的问题，用的是两个不同的工具（**以下命令是 linux 环境**）：

| 彩排          | 在哪跑                               | 回答什么问题                         | 工具                 |
| ------------- | ------------------------------------ | ------------------------------------ | -------------------- |
| ① 训练仓彩排 | 开发机 MuJoCo 仿真                   | 策略动作合不合理？能不能走/站/起身？ | `infer_policy.py`  |
| ② 上板仓彩排 | **板子上**、生产 Rust 推理路径 | 板子跑不跑得动？延迟够不够 50 Hz？   | `policy-rehearsal` |

**两者都要做，顺序不能换**：先在训练仓看动作（行为对不对），再去上板仓测延迟（性能够不够）。
下面分开讲。大部分人会栽在②的一个认知上，先看②。

#### 2.3.1 训练仓彩排：看动作对不对（仿真）

在训练仓 `microduck_rl` 里，用 MuJoCo + 真实执行器参数（默认已是 hd1910 电机模型）把策略跑一遍：

```bash
# 训练仓（microduck_rl）目录下
uv run scripts/infer_policy.py --walking walk_hd1910.onnx --motor hd1910
```

它会弹出一个 3D 查看器，你就能用方向键驱动它，直观确认策略行为正常。
这一步**只验证动作合理性，测不了延迟**——仿真机比板子快，仿真时间不代表板上时间。

> 💡 前一步 §2.2 的"3D 演示"也是类似的目的（`uv run play ...`），但它加载的是 checkpoint，
> 而这里加载的是**导出后的 ONNX**——观测归一化已经烘焙进去，行为应当一致。以 ONNX 的结果为准。

#### 2.3.2 上板仓彩排：测延迟（板子跑不跑得动）

**⚠️ 摆正认知：这条彩排必须在板子上跑，不是在开发机上。**
官方文档（`docs/recurrent-policies.md` §Offline rehearsal）写得很明确：
"Run the built example **on the Radxa** to establish onboard timing;
development-host timings do not establish the robot's timing budget."
开发机的 CPU 比板子快得多，你拿开发机的 p50 去判断"能不能跑 50Hz"，
等于拿 i9 的帧率给手机做性能预算。**板子的耗时才是真预算。**

彩排目标：确认策略在 **50 Hz 控制环**（每步预算 20 ms）下，推理延迟不超标。
注意这个时间**只算 actor 推理**，不含传感器读取、电机写入和调度开销。

完整步骤如下：

**第 1 步：确认模型是官方导出器导出的**（§2.2 已做）

`uv run scripts/export.py` 把观测归一化**烘焙进 ONNX**，并写入 `model_api`/`obs_len`/
`action_len` 等 metadata。手工转换丢归一化，仿真里看不出问题，上了真机才露馅。
上板前先看一眼维数契约（在开发机的 Python REPL 或 venv 里跑）：

```python
import onnx
m = onnx.load("standup_hd1910.onnx")
print([(i.name, [d.dim_value for d in i.type.tensor_type.shape.dim]) for i in m.graph.input])
# 期望: [('obs', [1, 61])]
print([(o.name, [d.dim_value for d in o.type.tensor_type.shape.dim]) for o in m.graph.output])
# 期望: [('actions', [1, 14])]
```

前馈模型是 `obs[1,61] → actions[1,14]`；LSTM 还要多出 `h_in/c_in → h_out/c_out`，
且 metadata 必须标 `model_api: 2`，否则老 daemon 会拒绝安装（§2.2 产出契约）。

**第 2 步：把模型拷到板子**

这一步用 `scp` 传文件是**正常的**——它只是把待测模型送进板子，**不等于上板部署**。
真正上板要等彩排通过之后，走 §2.4/§2.5 的发布 + `robotctl policy` 通道。

```bash
# 在开发机（Windows PowerShell）上执行
scp e:\optiDuck\microduck_rl\standup_hd1910.onnx radxa@192.168.31.30:/home/radxa/

# 传完先核对 MD5，防止传输损坏（坏模型会给出完全不同的彩排结果）
# 开发机: Get-FileHash standup_hd1910.onnx -Algorithm MD5
# 板子上: md5sum /home/radxa/standup_hd1910.onnx
```

**第 3 步：确认板子彩排前置条件（Rust + ONNX Runtime）**

彩排要在板子上**现场编译** `duck-control`，需要两样东西，缺哪个装哪个：

```bash
# ① Rust 工具链在不在
cargo --version

# ② ONNX Runtime 动态库在不在（duck-control 通过 dlopen 加载它，不打包）
#    §7 的 setup-board.sh 装的是 1.28.0
find / -name "libonnxruntime*" 2>/dev/null
```

- **缺 Rust**（`command not found`）→ 往下看"装 Rust"小节
- **缺 ORT**（find 无输出）→ 跑 `setup-board.sh`（或单独装 ORT 1.28.0）

**→ 装 Rust（板子上没有 cargo 时执行）**

用 rustup 装到用户目录，**不需要 sudo**：

```bash
# ① 国内网络先配镜像，再装（官方源在国外，直连大概率超时；换源能省非常多时间）
export RUSTUP_DIST_SERVER=https://rsproxy.cn
export RUSTUP_UPDATE_ROOT=https://rsproxy.cn/rustup

# ② 安装（--profile minimal 只装 rustc/cargo，够编译用）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal

# ③ 让当前终端生效（rustup 装完不会自动加载环境变量）
source "$HOME/.cargo/env"

# ④ 验证
cargo --version
rustc --version
```

> 💡 **镜像没配就装**：rustup 默认从 `static.rust-lang.org` 拉工具链，在板子上（尤其国内网络）经常卡死或超时。
> 上面的 `rsproxy.cn` 是字节跳动的 Rust 镜像，配完基本秒下。如果装完 cargo 还是慢（拉 crates 依赖时），
> 再配 crates 镜像：`~/.cargo/config.toml` 里写 `[source.crates-io] replace-with = 'rsproxy'` + 对应 registry 地址（教程 §10 有完整配置示例）。

彩排**不碰电机总线**，所以不需要电机上电、不需要接舵机——板子空转也能测。

**第 4 步：把上板仓代码弄到板子**

彩排的 `cargo run` 必须在上板仓目录内执行（那里才有 `Cargo.toml`），而板子上默认**没有**上板仓代码——
新手最容易在这里卡住：在 `~` 下跑 `cargo run` 报 `error: could not find Cargo.toml in /home/radxa`。
所以先把上板仓源码弄到板子上：

```bash
# ① 在板子上，下载并解压上板仓（用 codeload 下载 tar.gz）
#    GitHub 直连（github.com）在板子上经常超时，codeload 是官方内容分发域名，稳得多
cd /home/radxa
curl -L -o microduck.tar.gz https://codeload.github.com/pollen-robotics/microduck/tar.gz/refs/heads/main
tar -xzf microduck.tar.gz
ls   # 应看到 microduck-main 目录

# ② 先配 crates 镜像再编译（板子上拉 crates 依赖，不配镜像会非常慢甚至失败）
mkdir -p ~/.cargo
cat > ~/.cargo/config.toml <<'EOF'
[source.crates-io]
replace-with = "rsproxy"

[source.rsproxy]
registry = "sparse+https://rsproxy.cn/index/"

[registries.rsproxy]
index = "sparse+https://rsproxy.cn/index/"

[net]
git-fetch-with-cli = true
EOF
```

> ⚠️ **复制粘贴反引号陷阱（真实踩坑）**：本文档的 URL 在界面里渲染成代码样式（两边带反引号 `` ` ``）。
> 从对话/文档**复制 heredoc 时，这些反引号会被一起复制进文件**，导致 cargo 解析 URL 失败。
> 症状：`cat ~/.cargo/config.toml` 看到 `registry = "sparse+`https://...`"` 两边带反引号。
> 修复：`sed -i 's/`//g' ~/.cargo/config.toml`（或干脆用下面的 scp 方案重写）。
> **最稳妥的做法**：不在板子上粘贴含 URL 的命令，而是从开发机生成配置文件再 scp 上去：

```powershell
# 在开发机（Windows PowerShell）生成干净配置，scp 到板子覆盖
# （本地建一个 cargo-config.toml，内容就是上面 heredoc 里的 6 行，确保 URL 无反引号）
scp cargo-config.toml radxa@192.168.31.30:/home/radxa/.cargo/config.toml
# 上板后验证：cat ~/.cargo/config.toml 应看到 registry = "sparse+https://rsproxy.cn/index/" 无反引号
```

> `sparse+` 是 rsproxy 官方推荐的增量 index 格式，比整包 git 拉 index 快很多，板子必用。

> 💡 **为什么用官方 `pollen-robotics/microduck`**：板子的更新器（`updater.toml`）配的就是这个仓库、
> 板上 daemon 也是它的代码。彩排的意义就是测**和板上相同代码**的推理路径，所以必须用同一个仓库。
> 注意：本机 `e:\optiDuck\joyandai\microduck` 这个目录**也只是官方仓库的一份 clone**（remote 指向
> `pollen-robotics/microduck`，本地无私有提交）——GitHub 上并不存在独立的"JoyandAI 版上板仓"。
> 国产化（HD1910 电机参数等）都在训练仓 `microduck_rl`、已烤进 ONNX，上板仓保持纯官方即可。

**第 5 步：在板子上编译并运行彩排**

```bash
# 在板子上、上板仓目录内执行（第 4 步解压出的 microduck-main）
cd /home/radxa/microduck-main
export ORT_DYLIB_PATH=/usr/local/lib/libonnxruntime.so   # 第 3 步 find 到的实际路径
cargo run --release -p duck-control --example policy-rehearsal -- \
  /home/radxa/standup_hd1910.onnx > rehearsal.json

# 查看结果
cat rehearsal.json
```

首次编译要拉依赖 + 编译 release，会花几分钟；之后的 `cargo run` 增量编译就快很多。
**板子上网不便时**：可以在开发机用 `cargo build --release` 交叉编译出二进制拷过去，
但 ORT 动态库必须是板子上的（架构一致），且**时间必须在板子上实测**——交叉编译只是省编译时间，不能省实测。

**第 6 步：解读结果，判断能否上板**

```json
{
  "steps": 1000,
  "latency_ms": {"p50": 2.1, "p95": 3.4, "p99": 5.2, "max": 9.8},
  "over_20_ms": 0,
  "actions": [[...], [...], ...]
}
```

判定标准：

| 指标            | 要求                                     | 说明                                                      |
| --------------- | ---------------------------------------- | --------------------------------------------------------- |
| `p95`/`p99` | 明显低于 20 ms                           | 50 Hz 控制环每步预算 20 ms，尾部延迟太高会周期超时        |
| `over_20_ms`  | 最好是 0                                 | 超过 20 ms 的次数；非 0 意味着有控制步会被拖过预算        |
| `max`         | 越低越好（首推可能偏高属正常，预热已做） | 峰值延迟，决定最坏情况                                    |
| `actions`     | 与前馈推理逐项对比（可选）               | 用带 obs 的 trace 对比 PyTorch/Python ORT，校验推理一致性 |

只有板子上测出的 `p95/p99/max` 都稳在 20 ms 内、`over_20_ms` 为 0，策略才算通过彩排，
可以进入 §2.4 发布。**不要**用开发机的测量结果替代——那只能证明模型结构没问题，
证明不了板子跑得动。

> ⚠️ **彩排 ≠ 上板**：彩排只是"用生产推理路径测延迟"，它**不写任何板子配置、
> 不占策略槽位、不需要电机**。真正把策略装进板子（`robotctl policy add`/`load`）是
> 彩排通过之后、发布完成之后的事（§2.5）。别把这两步搞混——彩排阶段就把模型
> `robotctl policy load` 进槽位，属于跳过流程的误操作。

### 2.4 发布到 HF Hub：一个模型变成一个"仓库"

> 📌 **先分流：发布不是必经之路**。如果模型只在自己的板子上用，可以跳过本节，
> 直接走 §2.5 的 `sudo robotctl policy load <槽位> /home/radxa/xxx.onnx`（本地文件占槽，
> 持久生效）。走发布的意义：叫名字触发（`robot do`）、绑手柄按钮、分享给别人、
> 有 manifest 校验和版本管理。两条路见 §2.5 末尾的对比。

**发布前置：HF 登录（一次性的）。** `uv run publish` 走 `huggingface_hub` 上传，
token 从两个地方找，**没有就不让你发**（报 `no cached HF token. Run hf auth login.`）：

> ⚠️ **本节的登录、发布命令都在开发机上执行**（发布模型的是开发机）。板子**不需要**
> HF token——它只通过 `robotctl policy add` 拉取**公开**策略仓，匿名即可；
> 在板子上跑 `hf auth login` 会报 `hf: command not found`（板子没装这套工具，也不需要）。

**先创建 token（第一次用 HF 时，网页操作，只需一次）：**

1. 打开 [https://huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)（需已注册/登录 HF 账号），
   点 **Create new Access Token**
2. **Token type**：保持默认的 **Fine-grained**（不用动；创建后不可改）
3. **Token name**：随便起个名字（如 `microduck-publish`），方便以后辨认
4. **Presets**（预设按钮）：点 **⬆ Write** —— 这正是发布需要的权限集（对你名下仓库的
   读+写，覆盖 `create_repo` + `upload_folder`）。**不要选 Read-Only**（上传 401/403），
   也不必选 Full Access（权限过大没必要）；Custom 手动勾细项是给特殊场景的，发布用不上
5. 点 **Create token**，弹出 `hf_xxxxx` 后**立刻复制**——**只显示这一次**，关掉就再也
   看不到了，丢了只能重新创建

**方式 A（推荐）：huggingface_hub CLI 登录，token 缓存到本机**

`hf` 不是系统全局命令，它装在训练仓的 venv 里——**必须通过 `uv run` 调用**（在训练仓
`microduck_rl` 目录下执行；直接敲 `hf auth login` 会报 command not found）：

```powershell
# 开发机（Windows PowerShell），先 cd 到训练仓目录
cd E:\optiDuck\microduck_rl
uv run hf auth login
# 按提示：粘贴 hf_xxx token → 回车；"Add token as git credential?" 选 n 也行
```

**方式 B：临时用环境变量（不写盘，仅当前终端生效）**

```powershell
$env:HF_TOKEN = "hf_xxx"     # PowerShell；Linux 用 export HF_TOKEN=hf_xxx
```

**怎么确认 token 已经就位（发布前先验证，避免白跑）：**

```powershell
# 同样在 microduck_rl 目录下
uv run python -c "from huggingface_hub import whoami; print(whoami()['name'])"
# 打印出你的 HF 用户名 = 登录成功；报错 = token 没配上
```

**token 权限要求**：`publish` 要在你名下发仓库（`create_repo`）并上传文件
（`upload_folder`），必须用 **Write 权限**的 token（创建页 Presets 点 **⬆ Write** 即可；
只读 token 上传会 401/403）。`--repo <user>/microduck-<name>` 里的 `<user>` 就是
登录账号的 HF 用户名，publish 会**自动从 token 推断**——所以先登录、用户名填自己的。

> 💡 如果你之后用 `uv run train ... --hf-jobs` 在 HF 上跑训练，那是另一个
> **带 Jobs 权限**的 fine-grained token，与本地发布不是一回事（`hf_jobs.py` 里
> token 鉴权通过但缺 Jobs 权限会明确报 "token-scope problem, not billing"）。

**manifest.json 是什么、为什么需要它**：板子**不认裸的 `.onnx`**。安装策略时，
`robotctl policy add` 会检查模型旁边一个 `manifest.json`——模型的"身份证"，写明：
给谁用的（`robot.model: microduck`，写错直接拒装）、输入输出维度（`obs_len: 61` /
`action_len: 14`，和板子对不上就拒装）、什么类型（episodic/perpetual）、跑几秒、
训练来源（便于溯源）。所以"发布" = **policy.onnx + manifest.json + README 三个文件
一起**上 HF Hub，板子拉取后验 manifest 通过才装。

**你不需要手写 manifest**——训练仓的 `publish` 命令根据参数**自动生成**它，并做
上传前体检（61→14 维度校验 + NaN 冒烟测试），不合格不会传（`microduck_rl/AGENTS.md`）。
权重来源有两种（**下述两种来源只是作为解释，已在2.2导出ONNX执行过**）：

```bash
# 来源 A：从 wandb 训练 run 现场导出（归一化烤进模型，最安全的路径）
uv run publish --task <TASK_ID> --wandb-run-path <entity/project/run_id> \
  --checkpoint N --repo <user>/microduck-<name> \
  --kind episodic --duration-s 4.0

# 来源 B：用已经导出好的 ONNX（§2.2 导出的就是这种）
uv run publish --onnx <文件>.onnx --repo <user>/microduck-<name> --kind episodic --duration-s 6.0
```

**`kind` 决定它装到哪（官方契约，别自己猜）**——映射写在
`microduck_rl/src/mjlab_microduck/publish/manifest.py` 的 `install_commands()` 里，
publish 结束时会直接把第一行安装命令打印给你：

| 发布时给的 `kind` | 板子上装成 | 官方生成的安装命令 |
| --- | --- | --- |
| `episodic` | **技能**（跑完 `duration_s` 自己结束） | `sudo robotctl policy add <name> <repo>` → `robotctl robot do <name>` |
| `perpetual --unwind-s N` | **"按住"型技能**（跑完由 daemon 送 `idle` 回来） | `sudo robotctl policy add <name> <repo> --hold <秒>` → `robot do <name>` |
| `perpetual`（不给 `unwind-s`） | **槽位步态**（一直跑，直到你叫停） | `sudo robotctl policy load <槽位> <repo>` |

两个容易搞错的点：

- **`--slot` 只是"显示用"**（`Display-only; drives the install hint`）：它只决定上面打印哪条
  安装提示，**不会**替你锁死槽位。真锁槽位的是板子上那条 `policy load`。
- **同名仓库已有 `policy.onnx` 时**要加 `--force` 才能覆盖——一个仓库只放一个 `.onnx`，
  想换个名字就是新建一个仓库（`cli.py` 原话：`a second name is a new repo`）。

**实操示例：把 standup_hd1910.onnx 发布成"叫名字就起身"的技能**（开发机、训练仓目录下；
写成单行避免多行续行符的复制问题）：

```powershell
cd E:\optiDuck\microduck_rl
uv run publish --onnx standup_hd1910.onnx --repo <你的HF用户名>/microduck-standup --kind episodic --duration-s 6.0 --entry-pose sitting --no-private
```

| 参数                                  | 作用                                                                            |
| ------------------------------------- | ------------------------------------------------------------------------------- |
| `--onnx`                            | 已导出的模型；publish 会先校验 61→14 维度 + 冒烟测试再传                       |
| `--repo <用户名>/microduck-standup` | 发到你 HF 账号下；**技能名 = `microduck-` 后面的部分**（`standup`）   |
| `--kind episodic`                   | 一次性技能：跑完`duration_s` 自己结束——"叫一下执行一次"                     |
| `--duration-s 6.0`                  | 与训练 episode 长度一致（standup env 的`EPISODE_LENGTH_S = 6.0`），够完成起身 |
| `--entry-pose sitting`              | 告诉板子这个策略从坐姿起步（默认 standing，起身模型必须改）                     |
| `--no-private`                      | **关键**：默认建私仓，而板子是匿名拉取**公开**仓——私仓板子拉不到  |

> 💡 不放心可先加 `--dry-run`：不联网，把三个文件写到本地 `publish-<name>/` 目录，
> 先看看自动生成的 manifest.json 长什么样再真发。

**另一种装法：不发技能，改走槽位（同一份 ONNX，换个 `kind`）**。如果你希望起身模型像
"基础功能"一样常驻（塞进 `stand` 槽，起身后一直维持站立），而不是"叫一次跑一次"：

```powershell
uv run publish --onnx standup_hd1910.onnx --repo <你的HF用户名>/microduck-standup-stand --kind perpetual --slot stand --entry-pose sitting --no-private
```

装法相应变成 `sudo robotctl policy load stand <你的HF用户名>/microduck-standup-stand`
（对照上面那张表第三行）。两条路选哪条：

| | episodic 技能（上面第一个示例） | perpetual 占槽位 |
| --- | --- | --- |
| 触发 | `robotctl robot do standup` 叫一次 | 默认就跑，一直维持 |
| 绑手柄 | 可以（`pad bind`） | 不行（槽位是默认态） |
| 结束行为 | 6 秒后 daemon 交回槽位策略 | 一直跑，策略自己闭环 |
| 适合 | "叫名字起身"、演示 | 让鸭子默认就站着 |

发布成功后，板子侧安装（§2.5 详述）：

```bash
sudo robotctl policy add standup <你的HF用户名>/microduck-standup
# 之后 robotctl robot do standup 即触发起身
```

`publish` 只允许发布**常命令的 episodic/perpetual** 策略；phase/posture 那种由 daemon 驱动的
策略（地面捡物、官方坐↔站槽位）属于官方策略集，不走这条普通发布路。注意自训起身模型
以 **episodic 技能**发布（`robot do standup` 触发）与官方的 posture 槽位机制是两条路，
互不影响。两点差异提前知道：① 官方坐↔站是 `scripted`（可中断 episodic，daemon 能在起身
中途打断），发布版 episodic **不可中断**——对起身无碍；② episodic 的结束语义是"策略自己
回到安全姿势"，起身策略 6 秒后稳定站立（站立 = 安全姿势），之后 daemon 恢复槽位控制，
由 stand 槽位策略接管维持站立。

**`manifest.json` 长什么样**（完整字段见 `docs/policy-manifest.md`，一个典型单策略仓库）：

```json
{
  "schema_version": 2,
  "model_api": 1,
  "obs_len": 61,
  "action_len": 14,
  "robot": { "model": "microduck", "hw_rev": 1, "servos": "hd1910", "control_hz": 50 },
  "name": "polite-bow",
  "kind": "episodic",
  "duration_s": 4.0,
  "chain": false,
  "entry_pose": "standing",
  "description": "Bows from a two-foot stand and comes back up.",
  "command": { "encoding": "constant", "idle": [0, 0, 0],
               "twist": "unused (zeros)", "head": "unused (zeros)", "body": "unused (zeros)" },
  "training": { "task_id": "Mjlab-PoliteBow-Flat-MicroDuck", "repo": "pollen-robotics/microduck_rl",
                "commit": "0bf9897", "branch": "bow", "dirty": false,
                "run": "pollen-robotics/mjlab_microduck/abc123", "checkpoint": 3000,
                "exported": "2026-09-02T14:05:00Z" }
}
```

> 💡 `robot.model`（`microduck`）才是**校验字段**，写错直接拒绝安装；`robot.servos`
> 只是**展示字段**（官方例子写 `xl330`，你的鸭子写 `hd1910`），不校验但写对才能溯源。
> 官方 `policy-manifest.md` 里的例子、以及 `robotctl policy add` 的校验规则都基于 `robot.model`。

**两个最关键的概念字段**（`kind` 和 `command.encoding`，板子靠它们决定怎么用你的模型）：

| `kind`      | 谁结束这个策略                      | 变成什么                            |
| ------------- | ----------------------------------- | ----------------------------------- |
| `episodic`  | 自己跑`duration_s` 秒后回安全姿势 | 一次性技能（`robot do` 触发）     |
| `perpetual` | 一直跑直到被告知停下                | 槽位步态（`policy load walk …`） |
| `scripted`  | 可被中断的一次性                    | 记录用，daemon 自己驱动             |

| `command.encoding` | daemon 喂给它的命令                    | 谁能用它                                               |
| -------------------- | -------------------------------------- | ------------------------------------------------------ |
| 缺省 /`constant`   | 固定 twist，回程`idle`               | 所有一次性技能                                         |
| `phase`            | `[cos 2πφ, sin 2πφ, 0]` 随时间转 | 地面捡物（daemon 驱动，**拒绝** `policy add`） |
| `posture_flag`     | 一个槽位携带 sit/stand                 | 坐↔站（同上，**拒绝** `policy add`）          |

> 💡 一个**常命令的 `episodic`** 策略，加上 `duration_s`，就是"按名字就能调用的技能"——
> 这是社区发布策略最常用的形态。phase/posture 策略不能做成技能，装进槽位由 daemon 驱动。

### 2.5 策略上板：五条命令全场景

板子上所有策略操作都走 `robotctl policy …`（只读命令不需要 `sudo`，**改状态的要 `sudo`**）：

```bash
robotctl policy list            # 看 7 个槽位 + 已装技能（两栏表格）
robotctl policy check           # 官方策略集：已装 vs 最新 vs 仓库还提供什么（只读）
sudo robotctl policy update     # 更新到最新（--version v1 可回退到指定版本）
```

实测输出长这样（`origin` 列就是 §2.5 末尾那张来源表里的 official/community/local）：

```text
$ robotctl policy list
mode: walk

   SLOT         ORIGIN     POLICY
   walk         official   velstand.onnx
   stand        -          (none)
   sitstand     official   alpha_sitstand.onnx
   ground_pick  official   alpha_ground_pick.onnx
   kick_left    official   ball_kick_left.onnx
   kick_right   official   ball_kick_right.onnx
   roulade      official   roulade.onnx

   SKILL        RUNS FOR  POLICY
   roulade           1 s  roulade.onnx
   sit_toggle          —  driven by the robot itself
```

> 💡 **`stand` 槽默认就是空的**（`origin = -`、`policy = (none)`）：走路模式下"站着"由
> `walk` 槽的 `velstand.onnx` 兜底。所以往 `stand` 槽装自己的策略是**覆盖一个空槽**，
> 语义干净；也正因如此，`policy load stand <源>` 失败后这一行会**保持 `(none)`**，
> 是判断"有没有真的生效"最直接的一眼。

**用自己的文件占槽位（"基础功能"路线，不发布、不用 HF）**：

```bash
sudo robotctl policy load walk /home/radxa/my_walking.onnx
```

> ️ **`<源>` 的判定规则（踩过坑）**：`robotctl` 先看**这个路径在板子上存在吗**——
> 存在就当本地文件（local 来源）；**不存在就当成 `org/name` 仓库**去 HF 拉，
> 于是报 `error: network error: /path/xxx.onnx is not an org/name repo`
> （`robotctl/src/main.rs`：*"A path if it exists here; otherwise `org/name`"*）。
> 所以上面这条命令**必须先真的把文件 scp 到 `/home/radxa/`**，否则一定报错。
> 例如板上已有 `standup_hd1910.onnx`，正确的写法就是：
> `sudo robotctl policy load stand /home/radxa/standup_hd1910.onnx`

走路时会先回 home 姿势、加载、再驱动；坐着时则什么都不动——**换的不是正在跑的那个网络**。
`sudo robotctl policy reset walk` 放回原样。

> ️ **这条不是"临时试试"**：`policy load` 会**写入 `/etc/robot/robotd.toml` 持久生效**，
> 重启后仍指向该文件（实测确认）。所以文件**必须一直留在板子上**——文件丢了，槽位会在
> 启动时回落到官方策略并在 `robotctl health` 里报出来。

**报错 `the robot accepted the change but had not made it after 20s` 怎么读**（本机实测）

**这不是失败**，是"**命令层成功、应用层尚未完成**"：`robotctl` 把请求交给 `robotd`，
`robotd` 收下了，但 20 秒内没做完切换，于是先这么回你，并提示你自己去看结果。要分两半看：

| 半 | 状态 | 怎么验证 |
| --- | --- | --- |
| **配置写入** | ✅ 已成功 | `awk '/^\[policy\]/{f=1;next} /^\[/{f=0} f' /etc/robot/robotd.toml` —— 能看到 `stand = "/home/radxa/xxx.onnx"` |
| **live 切换** |  未生效 | `robotctl policy list` —— 该槽仍显示 `(none)`、origin `-` |

最常见的原因是 **`robotd` 卡在电机自检**（舵机没上电 / 总线没接），日志会一直刷：

```bash
echo radxa | sudo -S journalctl -u robotd -n 30 --no-pager | tail -20
# ERROR robotd: motor register check failed; waiting, is servo power on?
# error=bus transaction failed: read return_delay_time on 20: Operation timed out
# （attempt=N 每 ~45 秒 +30，N 很大说明已经卡了很久，基本就是没接舵机）
```

`Operation timed out` 后面的 `on 20` 是**正在探测的舵机 ID**（20 = 左腿髋关节）。
设备节点在不在跟舵机答不答应是两回事，先确认节点存在：

```bash
ls -la /dev/ttyS2      # 期望 crw-rw---- root dialout（存在 ≠ 舵机在线）
```

> ⚠️ **别忽略"配置已写入"这一半**：`robotd` 是**先写 `robotd.toml`、再排队切换**的
> （`docs/design/policy-channel-design.md` §3 原话：*"robotd writes the key, not the
> caller"*，理由就是让这个覆盖跨重启存活）。所以即使这里报 20 秒超时，**这份覆盖重启后
> 依然在**——硬件恢复正常后开机，该槽就会加载你的模型。想撤销：
> `sudo robotctl policy reset <槽位>`（只撤这一个）或 `sudo robotctl policy reset`（七个全回官方）。

**槽位有三种来源，"官方槽位"不等于"只能装官方的"**（`docs/design/policy-channel-design.md` §2）：

| 来源 | 从哪来 | 自动更新 | `policy reset` 回到它 |
| --- | --- | --- | --- |
| **official** | `policies` 组件（`pollen-robotics/*`） | ✅ | ✅ |
| **community** | 任何别的 HF 仓库 | ❌ |  |
| **local** | **板子上的本地路径** | ❌ | ❌ |

来源由 HF org 判定（`pollen-robotics/*` 才算官方），不是槽位锁死的。所以自训模型
**可以占槽位**（走 local 来源），不是只能当技能。但有两条约束要知道：

- **`walk` / `stand` 是 perpetual gait 槽**（一直跑）→ 自训步态/站姿模型直接适配。
  自训起身模型走"基础功能"路线就装 `stand` 槽：
  `sudo robotctl policy load stand /home/radxa/standup_hd1910.onnx`
  （本会话实测：配置能写入 `robotd.toml`；当时仅因电机总线超时未完成 live 切换）
- **`sitstand` / `ground_pick` 是 daemon 驱动的特殊槽**：daemon 会给它喂
  `posture_flag`（sit/stand 标志）或 `phase`（相位）编码，**按 encoding 校验而不是按名字**
  （`docs/policy-manifest.md` §The two axes）。普通训练的 constant 编码模型装这两个槽
  有可能被拒或行为异常——起身模型建议走上面 `stand` 槽，或走下面的技能路线。

**装别人的（社区）策略**：

```bash
robotctl policy search microduck
sudo robotctl policy load walk RemiFabre/microduck-flamingo-cycle
```

**加一个技能并触发（"技能"路线，衔接 §2.4 发布的 standup）**：

```bash
# 官方示例：装 polite-bow 技能并触发
sudo robotctl policy add polite-bow fffiloni/microduck-polite-bow-b1d864
robotctl robot do polite-bow

# §2.4 发布的起身模型，对应的完整链路：
sudo robotctl policy add standup QingMuLYL/microduck-standup
robotctl robot do standup        # "叫名字就起身"——名字 = 仓库名去掉 microduck- 前缀
```

`robot do` 需要机器人正在驱动（手柄 Start 后）。技能长度来自 manifest 的 `duration_s`。
两种路线怎么选：**自己板子日常用 → 占槽位**（`policy load`，本地文件、简单直接）；
**要叫名字触发/绑手柄/分享给别人 → 走发布 + 技能**。区别只在触发机制，模型本身没有
"技能/基础功能"属性——同一份 onnx 两条路都通（前提是装的槽位与它的 kind/encoding 匹配，
见上文 `walk`/`stand` vs `sitstand` 的说明）。

**把技能绑到手柄按钮**：

```bash
robotctl pad bindings            # 看当前绑定
sudo robotctl pad bind x polite-bow
```

写入 `[pad] x = "polite-bow"` 一行，`padd` 一秒内生效，不重启。

**校验规则（被拒绝的常见原因）**：`policy add` 在下载前就检查 `obs_len`（必须 61）、
`action_len`（14）、`model_api`（≤ daemon 的）、`robot.model`（`microduck`），
以及非 `constant` 编码——任何一项不符直接拒绝，**不会动你现有的策略**。

> 📖 代码侧实现：`policy.fetch` 由 `updaterd` 处理，`robot.setSkill` / `robot.policies` /
> `robot.do` 是 `duck-ipc-proto` 的现成方法名——`spaces/policy-shop/app.py` 里的"一键安装"
> 就是按这个顺序调这四个调用（详见 §4.5）。

### 2.6 检测器模型：另一条链（YOLO → RKNN）

策略是"自己训 + 导出 + 发布"，检测器链路的**转换步骤不同**——它要变成 NPU 能跑的 `.rknn`：

```text
① 训练 (duck_detector 仓)  →  ② 转 RKNN (scripts/to_rknn.py)  →  ③ 发布 HF Hub  →  ④ 上板
    yolo11n 320×320          INT8 量化 → duck_detector.rknn        同名仓发两个文件      robotctl
                             同时留 duck_detector.onnx (CPU 兜底)                        duck-detector
```

**为什么两个文件？** 板子优先跑 NPU 的 `.rknn`；NPU 不可用时回退 CPU 的 `.onnx`
（`deploy/robotd.toml` L348-358 写了这个选择逻辑）。

**上板命令**（跟策略同构，根不同、文件清单固定）：

```bash
robotctl duck-detector check           # 已装 vs 仓库最新（只读）
sudo robotctl duck-detector update     # 更新（重启 mediad，画面会断一下）
# 检测器跑不跑还受配置控制：sudo robotctl configure 里 [duck_detector] enabled
```

**首次上板机制**：`scripts/seed-detector.sh` 由 release 的 postinstall 钩子跑，
在**空板子**上按 pin 安装（`DETECTOR_REPO` / `DETECTOR_VERSION` / `DETECTOR_FILES`，脚本 L32-47），
装到 `/opt/robot/detector/releases/seed-duck-v1/` 后原子切换 `current` 符号链接
（先写 `current.new` 再 rename，脚本 L66-126）。**它绝不碰不是自己装的东西。**

**板子目录结构**（`/opt/robot` 下三条 `current` 链，是模型链路的"安装现场"）：

```text
/opt/robot/detector/current      → releases/seed-duck-v1/   # 检测器（你的板子现在是这个）
/opt/robot/policies/current      → releases/seed-v5/        # 官方策略集（含 manifest.json）
/opt/robot/daemon/current        → releases/<version>/      # daemon 二进制（§4 的主角）
```

### 2.7 NPU 推理链路：模型在板子上到底怎么跑

检测器的运行时绑定在 `duck-detect` crate（`docs/project/npu-bringup.md`）：

- **`librknnrt.so` 是 `dlopen` 动态加载的，不是链接的**（`duck-detect/src/rknn.rs`）。
  因为它是 vendor blob、不在任何 Debian 套件里，链接它就没法在 CI 交叉编译；
- **运行时反量化**：INT8 量化模型的输出带 scale 和 zero point，
  `rknn_outputs_get` 在请求时会转成 float——解码层拿到的直接是 float，不用自己算 scale；
- **版本约束**：`librknnrt.so`（板子上是 **v2.3.2**，由 `scripts/setup-npu.sh` 装）**不能低于
  转换模型用的 rknn-toolkit2 版本**，否则 `rknn_init` 失败且无解释（`Cargo.toml` L34-42
  有这条约束的 pin）。

**验证工具 `duck-bench`**（不在 release 里，是测量工具，走 scp；`duck-detect/src/bin/duck-bench.rs`）：

```bash
cargo board --bins -p duck-detect     # 交叉编译（§4.3 会讲 cargo board 是什么）
scp target/aarch64-unknown-linux-gnu/release/duck-bench radxa@<robot>:/var/tmp/
scp <the>.rknn radxa@<robot>:/var/tmp/duck.rknn
scp -r datasets/raw/<a-session> radxa@<robot>:/var/tmp/frames

/var/tmp/duck-bench --model /var/tmp/duck.rknn --frames /var/tmp/frames
```

它按顺序回答三个问题：**能跑吗 → 还认鸭子吗 → 花多少钱**（延迟分位数 + 本进程 CPU）。

**量化模型的第一个坑——阈值：** 量化模型的分数在**自己的尺度**上，float 模型的 0.5
不是它的 0.5。一跑检测不到东西，先试 `--threshold 0.2`，别急着怀疑转换坏了。

**实测数字**（Radxa ZERO 3 上，2 Hz 节奏，来自 npu-bringup 文档）：

| 项目                          | 数值              |
| ----------------------------- | ----------------- |
| driver / runtime              | 0.9.8 / 2.3.2     |
| 延迟 p50 / p95                | 25.7 ms / 58.4 ms |
| 每帧 CPU（含 letterbox 缩放） | 20.7 ms           |
| 满载结束时 SoC 温度           | 63 °C            |

> 💡 **letterbox（1280×720 → 320×320 缩放）在 CPU 上跑**，CPU 数字不是纯 NPU 的代价。
> 用 NPU 的意义是**不占用 robotd 的 50 Hz 循环**，这个目标要在测量后确认。

### 2.8 两条模型链路对照表

|            | 策略（policy）                                           | 检测器（detector）                          |
| ---------- | -------------------------------------------------------- | ------------------------------------------- |
| 训练仓     | `microduck_rl`（PPO/MuJoCo）                           | `duck_detector`（YOLO，外部仓）           |
| 转换       | 不需要（CPU 直接跑 ONNX）                                | `to_rknn.py` INT8 量化（NPU 跑 RKNN）     |
| 板载格式   | `.onnx`                                                | `.rknn` + `.onnx`（CPU 兜底）           |
| 发布地     | `<user>/microduck-<name>` 或官方策略集                 | `pollen-robotics/microduck-duck-detector` |
| 上板命令   | `robotctl policy add/load/update`                      | `robotctl duck-detector update`           |
| 加载校验   | `obs_len`/`action_len`/`model_api`/`robot.model` | 形状 + 阈值                                 |
| 谁在跑     | `robotd`（50 Hz 循环）                                 | `mediad`（摄像头侧）                      |
| 更新后重启 | 不重启（槽位重新加载）                                   | 重启`mediad`                              |

---

## 3. 硬件调试：从体检到动起来

> ⚠️ **本机现状（2026-09）：你手上还没有舵机、摄像头、ToF HAT。** 这一节按"硬件到手后的
> 完整流程"写，每步都标注**【现在能跑】**还是**【等硬件】**。等硬件到齐，直接照做即可；
> 现在就能跑的部分（总线体检、日志、排障习惯），建议立刻练手。
>
> 核心思想只有一个：**板上的一切硬件都由 7 个 daemon 包了一层接口，你永远从
> `robotctl` 这一侧观察和操作，不要绕过 daemon 直接摸硬件。** 绕过了，下一层就是
> "为什么它不理我"的无底洞。

### 3.0 硬件地图：这条鸭子身上有什么

> 📌 **本机实测基线（2026-09-17，SSH 上板逐项核实，非照抄官方文档）**：
> 系统是 **Radxa OS**（`/etc/os-release` = Debian GNU/Linux 12 bookworm；型号
> `Radxa ZERO 3` / `radxa,zero3w-aic8800ds2`），内核 **6.1.84-10-rk2410-nocsf**，
> 内存 2 GB（`free -m` 1978 MB），Wi-Fi/BT 芯片 **AIC8800**（不是 Armbian——官方
> `media-bringup.md` 里那些 Armbian 专属的坑在本机不存在，下文凡涉及都会区分）。
> 当前已启用 overlay：`rk3568-i2c4-m0`、`rk3568-npu-enable`、`rk3568-uart2-m0`
> （见 `/boot/extlinux/extlinux.conf` 的 `fdtoverlays` 行）。

```text
                    Radxa ZERO 3W（你的板子，Radxa OS / Debian 12）
   ┌──────────────────────────────────────────────────────────────┐
   │  /dev/ttyS2  ←── Dynamixel UART（protocol 2.0，唯一电机总线）│
   │  /dev/i2c-4  ←── HAT 上的 ToF（VL53L5/8CX，8×8 深度矩阵）    │
   │  CSI-2 MIPI  ←── 摄像头（22-pin 座；overlay 见 §3.5）        │
   │  I2S / codec ←── 音频（喇叭 + 麦克风，HAT 未接时只有 HDMI 声）│
   │  NPU ←─ /dev/dri/by-path/platform-fde40000.npu-render        │
   │       （实测指向 renderD129；renderD128 是 display，见下注） │
   └──────────────────────────────────────────────────────────────┘
             │ 一条总线上的两个"人"
             ▼
   15 × Dynamixel 协议舵机 + IMU 板（id 200，走 imu_to_dxl）
   左腿 20–24 · 右腿 10–14 · 头/脖子/嘴 30–34（robotd-design.md §1.1 官方布局）
   · 同一条 UART、同一个 protocol 2.0、同一次 sync_read 批量读回
   · 舵机具体型号由机器人配置决定：**你的鸭子是 HD1910**（官方鸭子是 XL330，同协议不同电机）
   · 机器人**没有第二根电机总线**，也没用 DXL 之外的协议（docs/design/robotd-design.md §1.1）
```

> ️ **NPU 设备节点以 by-path 为准，别记编号**。本机实测
> `/dev/dri/by-path/`：`platform-fde40000.npu-render → renderD129`（NPU）、
> `platform-display-subsystem-render → renderD128`（显示）、
> `platform-fde60000.gpu-render → renderD130`（GPU）。renderD 编号按驱动 probe
> 顺序分配，换内核/配置可能挪位；librknnrt 自己枚举找 NPU，稳定标识是 by-path。
> （早期排查笔记里"NPU=renderD128"的说法与本次实测不符，以 by-path 为准。）

**舵机 ID 布局是官方写死的**，不是随便编的号（`docs/design/robotd-design.md` L35-37）：

| ID     | 位置                                   | 数量 |
| ------ | -------------------------------------- | ---- |
| 200    | `imu_to_dxl`（IMU 板，伪装成"舵机"） | 1    |
| 20–24 | 左腿（hip_yaw → ankle 五关节）        | 5    |
| 10–14 | 右腿（同上）                           | 5    |
| 30–34 | 头 / 脖子 / 嘴                         | 5    |

为什么 IMU 能和舵机挂同一条线？因为 Dynamixel 是地址寻址的菊花链：每帧带目标 id，
主控广播、目标应答。IMU 板把自己伪装成一个 id=200 的"舵机"（协议层）挂进来，
`robotd` 一次 `sync_read` 就把 15 个舵机 + IMU 的数据全拿回来了（IMU 排在 id 向量第一个，
先于舵机爆回应）。这条链是**机器人唯一的运动感知来源**，所以排查顺序永远是：总线 → robotd → 上层。

**主控的接口是怎么分给鸭子的**（OpenMicroDuck `docs/main_controller.md` §4，ZERO 3W 的 40-pin 引出）：

| 接口                 | 40-pin 位置                 | 鸭子拿来做什么                                                                                            |
| -------------------- | --------------------------- | --------------------------------------------------------------------------------------------------------- |
| **UART2**      | 40-pin 引出                 | HAT 半双工换向后 → 舵机总线 +`imu_to_dxl`（id 200）                                                    |
| **I²C（本机实测 I²C4 M0）** | 40-pin 的 3 / 5 | HAT 上 codec、ToF。**本机 `fdtoverlays` 启的是 `rk3568-i2c4-m0` → `/dev/i2c-4`**（`i2cdetect -y 4`，实测总线在、无设备因 HAT 未接）。⚠️ OpenMicroDuck `main_controller.md` 原文写"I²C3 M0"，与本机 Radxa 设备树不符——以板子实测的 i2c-4 为准；原厂该控制器另一复用给了 USB-C PD，改到 M0 后失去 PD 协商，5 V 充电仍可用 |
| **MIPI CSI**   | 22-pin、0.5 mm 间距、4-lane 座 | **IMX219**（8MP，77° 裸模组；选购见 §3.0.2，overlay 见 §3.5） |
| **I²S**       | 40-pin                      | HAT 上 TLV320AIC3104 音频 codec（HAT 未接时 `cat /proc/asound/cards` 只有 `rockchip-hdmi` 一张卡，正常现象） |
| **Wi-Fi / BT** | 板载                        | `configd` / `btd`，不经过 HAT                                                                         |

> ️ **UART2 要防 `serial-getty` 抢端口**：系统可能默认在 UART2 开 `serial-getty`
> （登录终端），一个 `agetty` 占着 `/dev/ttyS2`，**所有舵机就全看不见了**。
> `scripts/setup-board.sh` 会 mask 这个 unit——**本机已实测处理到位**：
> `systemctl is-enabled serial-getty@ttyS2` = `masked`（指向 `/dev/null`，2026-09-15
> setup-board 时做的）。官方文档把它叫"Armbian 默认坑"，但机制与发行版无关，Radxa OS
> 一样要查。以后谁再"突然全不认舵机"，先 `sudo fuser -v /dev/ttyS2` 看是不是 agetty
> 又回来了；手工处理：`sudo systemctl mask serial-getty@ttyS2`（robotd-design.md §1.1）。

**总线的"运行参数"是启动时写进舵机 EEPROM 的**（robotd-design.md §1.1、§2.1）——不是默认值：

| 寄存器                | 值                             | 为什么                                                                          |
| --------------------- | ------------------------------ | ------------------------------------------------------------------------------- |
| `baud_rate`         | **3**（= 1 000 000 bps） | 50 Hz 环一拍要读写 16 个设备，波特率低于 1 Mbps 拍预算就爆                      |
| `return_delay_time` | 0                              | XL330 出厂 250 = 每设备 500 µs 换向，16 个设备约 8 ms = 拍预算的 40%，必须清零 |
| `pwm_slope`         | 255                            | 位置环斜率，防电流尖峰                                                          |
| `shutdown`          | 52                             | 错误锁存掩码：过载 + 过热 + 输入电压故障（谁触发谁锁住，必须 reboot 才清）      |

> 💡 理解**换向（turnaround）**：Dynamixel 是半双工总线，一个设备答完才能轮下一个。
> 每个设备默认回包前等 500 µs，15 个舵机 + IMU 串下来一拍就吃掉 8 ms（20 ms 拍的 40%）。
> 把 `return_delay_time` 清零，就是把这 8 ms 还回去——所以"init 慢"和"一拍 50 Hz"的账，
> 都在这几个寄存器里。

### 3.0.1 舵机选型：你的 HD1910 与官方 XL330（为什么能混着讲）

> 本节内容主要来自 OpenMicroDuck 仓库的 `docs/servo.md`（选型逻辑）、`docs/bom.md`（采购）
> 与 `docs/structure.md`（结构差异）。先记住一句话：
> **"总线说 Dynamixel 话" ≠ "电机是 ROBOTIS 原厂"。**

官方 Microduck 的 BOM 是 **ROBOTIS Dynamixel XL330-M288-T**（18 g、288.4:1 减速比、
堵转 0.60 N·m @ 6 V、Dynamixel Protocol 2.0）。你的鸭子换成 **飞特 Feetech HD-1910-C001**，
OpenMicroDuck 的 3D 结构就是**按这颗舵机优化**的（`docs/structure.md`：HD1910 拆掉副舵盘后是
**突起**的，XL330 是**凹陷**的，头部/躯干/胯/腿/脚都为此改过——**不要拿 XL330 的结构件硬装 HD1910**）。

两者**协议层兼容**：HD-1910 是半双工 8N1 的"飞特数字包"，总线上按 XL330 的协议/寄存器工作
（1 Mbps、`baud_rate=3`），所以 robotd 不用改一行代码。**但电机本身不是同一颗——
换型号必须重训策略**（§2 里所有训练/彩排命令带 `--motor hd1910`，就是这个原因）。

**HD-1910-C001 关键规格**（飞特规格书 A/0，2026-09-07，`OpenMicroDuck/hardware_spec/servo/`）：

| 项目              | HD-1910-C001                                          | 对照 XL330-M288-T                        |
| ----------------- | ----------------------------------------------------- | ---------------------------------------- |
| 重量 / 尺寸       | 21 ± 2 g / 20 × 34 × 23 mm（含舵盘）               | 18 g / 20 × 34 × 23 mm                 |
| 减速比            | **320 : 1**                                     | 288.4 : 1                                |
| 工作电压          | **4–8.4 V**（6 V 为标定点）                    | 3.7–6.0 V（推荐 5 V）                   |
| 堵转扭矩          | **12 kg·cm**（1.18 N·m）@ 6 V                 | 6.1 kg·cm（0.60 N·m）@ 6 V             |
| 额定扭矩          | **3 kg·cm** @ 6 V                              | 手册未给                                 |
| 空载转速          | 92 rpm @ 6 V                                          | 123 rpm @ 6 V                            |
| 电流 @ 6 V        | 待机 20 mA / 空载 ≤180 mA / 额定 690 mA / 堵转 1.6 A | 17 mA / — / — / 1.74 A                 |
| 编码器            | 12-bit 磁编码                                         | 12-bit 绝对式（AS5601）                  |
| 电机 / 齿轮       | 空心杯 / 金属齿                                       | 有芯 / 工程塑料                          |
| 协议              | 半双工 8N1，飞特数字包（总线按 XL330 协议跑）         | Dynamixel Protocol 2.0                   |
| 恒力模式          | 模式 2 恒流；默认模式 4（PD 位置）                    | Mode 0 电流 / Mode 5 电流限制位置        |
| ID 范围           | 0–253（出厂 ID 1）                                   | 0–252                                   |
| 针序（AMP2.0-3P） | **1=Signal / 2=Vcc / 3=GND**                    | JST 3P：**1=GND / 2=Vcc / 3=DATA** |
| 零售价（2026-09） | 预售**¥123**                                         | $23.90–$27.49 / ¥95–¥238             |

HD-1910 在 4.8 / 6 / 7.4 V 的完整电气点（规格书 §5）：空载 73 / 92 / 113 rpm；堵转 9 / 12 / 15 kg·cm；
额定 2.2 / 3 / 3.7 kg·cm；堵转电流 1.2 / 1.6 / 2 A。**Kt = 7.5 kg·cm/A**（力矩电流比，BAM 标定用）。

**四个必须记的差异**（对照表之外的坑）：

1. **针序相反，别直插**：HD-1910 是 **1=Signal / 3=GND**，与 XL330 和飞特 HL-2915
   （**1=GND / 3=Signal**）相反。做转接线/HAT 前先核对针序，插反轻则不认、重则烧舵机板。
   OpenMicroDuck 桌面调试阶段专门有一根 **PH2.0 转 5264 3P** 转接线（`docs/bom.md`），
   就是给"外置 URT2（5264 口）→ HD-1910（AMP2.0-3P 口）"用的。
2. **电压范围宽，但别超 8.4 V**：HD-1910 走 4–8.4 V，过压保护阈值 10 V。2S 锂电（7.4 V）
   直供在范围内；供电轨的 MOS / 连接器电流预算按 15 路额定电流（690 mA × 15 ≈ 10 A 峰值预算）算。
3. **扭矩几乎翻倍，结构是适配过的**：6 V 堵转 12 kg·cm ≈ XL330 的两倍，额定 3 kg·cm。
   OpenMicroDuck 的结构/装配件（`cad/`）已按 HD-1910 调好，别混用 XL330 零件。
4. **换型号 = 重训策略**：官方 9 个 ONNX 是给 XL330 的 BAM M6 执行器模型训的，不能当
   bit-exact 用到 HD-1910 上（servo.md §1 引言：*换执行器之后，官方 9 个 ONNX 不能当即插即用步态*）。
   你的训练/彩排命令统一带 `--motor hd1910`（§2.1 ③、§2.3），就是让 BAM 用 HD-1910 的参数
   （`microduck_rl/vendor/bam/bam/params/hd1910/*.json`）。

### 3.0.2 采购清单：硬件到手前先对一遍（OpenMicroDuck BOM）

OpenMicroDuck 把采购分成**两个阶段**（`docs/bom.md`）——**别等所有零件齐了再开工**：

- **桌面调试版**：主控和舵机驱动板**外置**，用电源适配器供电、舵机驱动板直接连 PC，
  先把控制和策略跑通（研发期主力，你现在正处在的阶段）；
- **整机集成版**：Zero Robot HAT + IMU 转接板 + 电池全部进机体内，装好模型软件成整机。

**共用件**（两个版本都要）：

| 器件            | 型号              | 规格                                         | 数量 | 备注                |
| --------------- | ----------------- | -------------------------------------------- | ---- | ------------------- |
| 舵机            | 飞特 HD-1910-C001 | 恒力空心杯舵机，4–8.4 V，堵转 12 kg·cm     | 15   | 换型号必须重训策略  |
| 主控            | Radxa ZERO 3W     | RK3566，2 GB RAM，带 WiFi，无 eMMC，带排针版 | 1    | 系统装在 MicroSD 上 |
| SD 卡           | MicroSD / TF      | 64 GB，A2                                    | 1    | 无 eMMC 时必需      |
| 摄像头          | Sony IMX219（Pi Cam v2 规格） | MIPI FPC 排线，**800 万像素**（3280×2464），77° 标准镜头裸模组 | 1    | CSI 接口，选购细节见下注 |
| 麦克风 / 扬声器 | —                | —                                           | —   | 料号未定            |

紧固五金（M2/M2.5 自攻螺丝各规格共约 131 颗、轴承 6700K × 3 与 ET2216 × 1）和 3D 打印结构件
（约 40 个零件，推荐 PLA，脚垫可试 TPU-95）详见 `bom.md` 原文，这里不重复。

> 🔩 **轴承怎么买（2026-09-17 量过 CAD 后给的结论，数量有一处存疑，先看再下单）**
>
> | 型号 | 规格（内径 × 外径 × 厚） | `bom.md` 写 | CAD 模型实测 | 用途 |
> | --- | --- | --- | --- | --- |
> | **6700K** | **10 × 15 × 3 mm** | 3 | **3** ✅ 对得上 | 左右脚踝等 |
> | **ET2216** | **16 × 22 × 4 mm** | 1 | **11** ❌ 对不上 | 各关节（髋 / 膝 / 颈 / 躯干等） |
>
> - **尺寸已双重确认**：把训练模型 `robot_openmicroduck_groundcontact.xml` 里的两个轴承网格
>   解出来量了包围盒——`..._default` = 外径 15 × 厚 3（对应 6700K），`..._22x16x4` = 外径 22 × 厚 4
>   （对应 ET2216），与 `bom.md` 的尺寸逐字吻合。
> - ⚠️ **6700K 认准"厚 3"**：市面上最常见的"6700"标准件是 **10×15×4（厚 4）**，而本设计用的是
>   **10×15×3 薄款**。买成 4 mm 会顶到装配面、盖不上盖子——下单时认清 **内10 外15 厚3**。
> - ❗ **ET2216 数量存疑，别直接按 1 个买**：`bom.md` 写 1 个，但 CAD 模型里 16×22×4
>   轴承出现了 **11 次**（躯干 ×2、yaw2roll、bearing_roll、左右髋、左右膝、neck_pitch、
>   yaw_roll_motion ×2），基本是**每个关节一个**。两种可能：`bom.md` 漏写（应为 11），或作者有意
>   换成了别的方案而模型没改。**下单前务必看装配图 / 问作者确认**，宁可按 11 个备料（这种薄壁
>   轴承单价低、多买不心疼），也别只买 1 个装到一半卡住。
> - 复核方法：模型文件
>   `microduck_rl/src/mjlab_microduck/robot/microduck/robot_openmicroduck_groundcontact.xml`
>   里搜 `seeed_bearing`，数 `class="visual"` 的行数即每种轴承的个数。

> 📷 **摄像头怎么下单（2026-09-17 本机实测后给的明确结论）**
>
> - **买：IMX219、77°、800 万、裸 FPC 模组（不带桌面三脚架）**，配 **22-pin / 0.5 mm
>   间距** MIPI 排线，长度够从头舱绕到主板。77° 就是 Pi Cam v2 标准镜头，mediad 的
>   采集模式（3280×2464）、rkaiq 3A、180° 倒装翻转全按它调过。
> - **像素纠错**：OpenMicroDuck `bom.md` 原文写"IMX219，500 万像素"是**误写**——
>   IMX219 原生 3280×2464 = **808 万**（Pi Cam v2 就是 8MP）；500 万的是 OV5647
>   （Pi Cam v1，2592×1944），别买错。商品页也佐证：IMX219 款一律标"800 万"。
> - **本机三道硬件关的实测**：① `/boot/dtbo/` 里有
>   `radxa-zero3-rpi-camera-v2.dtbo`（IMX219）与 `-v1.3`（OV5647）两个 overlay；
>   ② `/etc/iqfiles/` 里有 `imx219` 的 3A 校准文件，**没有 IMX519**——所以商品页的
>   "78 度 1600 万 IMX519"直接排除（rkaiq 无校准、无 overlay，起不来）；
>   ③ mediad 的 sensor mode 钉死 IMX219，OV5647 虽有 overlay/iqfile 但官方原话
>   "Only the first has ever been used"，不当小白鼠。
> - **广角款（120°/160°）不推荐**：畸变/鱼眼边缘变形对 320×320 YOLO 检测器减分，且未适配。
> - 收到后验证见 §3.5：overlay 启用后 `dmesg | grep imx219` 应出现
>   `Model ID 0x0219`，并出现十个左右 `/dev/videoN`。

**桌面调试版额外**（研发期必须）：

| 器件           | 型号                                | 规格                          | 数量 | 原因                                                                        |
| -------------- | ----------------------------------- | ----------------------------- | ---- | --------------------------------------------------------------------------- |
| IMU 模组       | LSM6DSV16X 模块                     | 支持 I2C 和 SPI               | 1    | IMU_to_servo 自制板未就绪前，成品模组直接挂主控 I2C/SPI 读陀螺仪/加速度     |
| 舵机转接线     | PH2.0 转 5264 3P                    | 1 根 PH2.0 3P 接 1 根 5264 3P | 1    | 外置飞特 URT2 口是 5264/AMP-3，HD-1910 是 AMP2.0-3P，**中间必须转接** |
| 舵机驱动板     | 飞特 URT2 串口总线舵机驱动板        | Type-C 接口                   | 1    | 桌面调试的总线入口，直接连 PC                                               |
| 舵机电源适配器 | 7.5 V 3A，DC 5.5×2.1 插头，3C 认证 | —                            | 1    | 给外置 URT2 / 舵机供电                                                      |
| 主控电源适配器 | Type-C PD（5 V 3A 或 5 V 2A）       | —                            | 1    | 主控外置时单独供电                                                          |

**整机集成版额外**：Zero Robot HAT 转接板（40-pin 半双工舵机换向 + 电池配电 + I²C / I²S 外设）、
IMU_to_servo 转接板（板载 LSM6DSV16X，挂在舵机总线上、规划从机 ID 200，对齐官方 50 Hz 契约——
整机里 IMU 与 15 颗舵机同一趟 `sync_read`，不再走主控上的裸 I²C 模组）、
7.4 V 3400 mAh 2S 锂电池组（18650×2，2C 放电，XH2.54 2P 公头）。

### 3.1 硬件体检：【现在能跑】先养成这个习惯

```bash
robotctl health          # 一次性报告：硬件 + 软件 + 网络 + 电量温度
robotctl health --json   # 机器可读，可接脚本；不健康时退出码非 0
```

`health` 是排障第一站：它把"电机总线识别到没有、IMU 读数正不正常、哪个 daemon 没在跑"
一次性摆出来。退出码非 0 可以拿来写检查脚本（比如每分钟跑一次，非 0 就报警）。

```bash
robotctl monitor         # 实时 TUI，看循环的"真实世界"
```

`monitor` 是调试主力，记住它的版面在说什么：

| 版面         | 看什么                                                                        |
| ------------ | ----------------------------------------------------------------------------- |
| 指令 vs 实际 | 每条腿关节的目标角 vs 传感器实测——**偏差持续 >3° 就是机械/电机问题** |
| IMU 投影重力 | 机器人"以为"自己在往哪倒（跌倒判断的依据）                                    |
| 循环频率     | robotd 是否稳定 50 Hz；掉到 30 Hz 以下先查 CPU 争抢                           |
| 电机总线     | 读写耗时、失败次数、电压/温度（每秒采样一次）                                 |
| 电量 / 温度  | 别让训练好的鸭子饿着肚子干活                                                  |

monitor 按键：`q` 退出、`t` 打开 ToF 矩阵、`c` 打开摄像头窗口、`d` 关右侧机器人小图
（终端窄时按 `d`）、`p` 打开手柄输入流。

> 💡 **把「先 health，再 monitor」练成本能。** 这两个命令不碰任何硬件状态、不产生副作用，
> 任何时候都可以跑——这是硬件调试里唯一可以闭眼执行的环节。

### 3.2 舵机总线调试：【等硬件】Dynamixel 链

**先认识三个基本操作**（`robotctl robot …`，全部由 `robotd` 代理执行，因为它独占总线）：

```bash
robotctl robot init              # 给所有关节上电，约 2 秒位置斜坡到 home 姿势
                                 # 会动所有关节！放架子上或扶住（main.rs L437-448）
robotctl robot relax             # 切电，机器人瘫倒——拿起/收纳前先 relax
robotctl robot reboot-motors     # 重启全部舵机（或 reboot-motors 10 20 只重启这两颗）
                                 # 舵机过载/过热进入硬件错误时用它恢复，无需拔电池
                                 # 注意：重启后 torque off，要再 robot init 或按 Start
                                 # ID 布局见 §3.0（10–14 右腿 / 20–24 左腿 / 30–34 头）
```

**三个操作对应三个心智模型：** `init` = 上电并站稳（无策略也能站）；`enable` = 把控制权交给
策略（等于手柄 Start）；`relax` = 放倒。**调试时永远先 `init` 再操作，结束时 `relax`。**

**总线本身的验证**（robotd 没起也能测）：

```bash
sudo systemctl status robotd          # robotd 必须在跑，总线是它独占的
sudo journalctl -u robotd -f          # 看 50 Hz 循环的实时日志：读回失败会在这里报
robotctl monitor                      # 看总线健康栏：读写耗时、失败计数
sudo fuser -v /dev/ttyS2                   # "谁占着总线"？出现 agetty = serial-getty 复活了
                                             # （ttyS2 是 root:dialout，普通用户看不到占用者，要 sudo）
```

**判断"总线活着"的三个信号**（按可靠度排序）：

1. `robotctl health` 的电机总线行显示"识别到 15 个舵机 + IMU"——**最强信号**；
2. `robotctl monitor` 的实际角会跟着指令角动（哪怕有偏差）——说明读和写都通了；
3. `journalctl -u robotd` 里没有周期性 `read failed` / `timeout`——弱信号，有噪音。

**三个常见总线坑：**

- **只报 1 个舵机或一半舵机**：菊花链断点。从头到尾找松动的接插口，`robotctl robot reboot-motors`
  后重看 health（新插上的舵机按顺序重新上链）；
- **某个舵机一直过热/过载（monitor 电压温度栏飘红）**：机械卡死或减速箱锁死，先 `reboot-motors <id>`
  排除固件态，还红就拆下人工转——电机阻转和固件错误的处理完全不同；
- **换过舵机后第一次 init 很慢或报错**：这是**"收养"机制在工作**（robotd-design.md §2.1）——
  新舵机出厂是 **ID 1 / 57 600 baud**，跟总线（ID 布局 / 1 Mbps）都对不上。`robotd` 启动时
  先 ping 15 个期望 ID；**恰好一个没回应**时，就去 1 Mbps、再降到 57 600 找 ID 1，
  找到后把缺失 ID 和 1 Mbps 波特率写进去、重启它、再跑寄存器检查。所以"换舵机后第一次
  init 慢"是正常的收养周期，等 `journalctl -u robotd` 出现稳定循环再继续；**注意只换一颗**
  ——换两颗以上同时缺位，收养逻辑找不到唯一缺失者，会当作总线故障。

### 3.3 IMU：【等硬件】姿势的裁判

IMU 不是独立传感器，它**住在电机总线上**（id 200，`imu_to_dxl`），和舵机一起被
`robotd` 每 tick 读一次（`robotd-design.md` L144-156：50 Hz 循环、每秒读一次电压/温度）。

- **在哪里看**：`robotctl monitor` 的 IMU 投影重力项——三个值接近 (0,0,-1) 表示板子水平；
  明显偏移且跟随你的摇晃，说明 IMU 活着且在正确工作；
- **跌倒判断**：`robotd` 用 IMU 重力投影决定"我倒了没"，所以**校准/安装方向错误会让机器人
  误以为自己在跌倒**——换过 IMU 板或拆过 HAT 后，先看 monitor 的重力方向对不对再启用控制。
- **ToF HAT 上的 IMU 是另一只**（`[head_imu]`，默认**关闭**，`docs/project/tof-on-demand.md`）：
  ```bash
  sudo robotctl configure      # [head_imu] enabled = true 打开，改完需重启 robotd
  tofd --imu                   # 单独会话里读一次 IMU，验证线路（--imu-hz 可调采样率）
  ```

> 💡 两个 IMU 别搞混：**总线上那个（id 200）是控制用的、必须开**；**ToF HAT 上那个是
> 可选的头部 IMU、默认关**。调错对象是新手最常见的"IMU 坏了"误报来源。

### 3.4 ToF：【等硬件】HAT 上的 8×8 深度矩阵

ToF 由独立的 `tofd` 守护进程独占（跟 robotd 独占总线一个道理），它**只发不收**：
把 8×8 深度矩阵写到 `/run/tofd/tof.sock`，谁想用谁读（`architecture.md` L80-89）。

```bash
robotctl monitor        # 按 t 打开 ToF 矩阵，实时看 8×8 深度（越近越亮）
ls -la /run/tofd/       # 确认 tof.sock 在（tofd 活着）
sudo systemctl status tofd
```

**验证顺序**：`tofd` 在跑 → `/run/tofd/tof.sock` 存在 → monitor 按 `t` 有数据 → 拿手在
HAT 上方 5–20 cm 晃动看矩阵响应。前三步通过但没响应，多半是 I2C 地址或接线。

**ToF 很费 CPU 的教训**（`tof-on-demand.md` 实测）：depth + head IMU 的采样开销在
Radxa ZERO 3 上不可忽略。**不用头部 IMU 就保持默认关闭**，省一档 CPU 给 robotd。

### 3.5 摄像头：【等硬件】看世界的眼睛

摄像头是"硬件门槛最高"的一环，两个前置条件缺一不可（`docs/project/media-bringup.md`）：

1. **CSI 摄像头必须配设备树 overlay**——不配的话插上也"没有任何反应"：不出现
   `/dev/video*`、dmesg 一片空白，看起来和没接一模一样（rkisp 采集节点只在 sensor
   probe 成功后才出现）。**但启用方式按系统区分，别照抄官方文档的 Armbian 做法**：

   **本机 Radxa OS（Debian 12，实测）**——overlay 是 `/boot/dtbo/` 里的文件，
   带 `.disabled` 后缀 = 关闭；extlinux.conf 的 `fdtoverlays` 行用**完整路径**引用，
   不存在 Armbian 那种"overlay_prefix 解析、文件名必须镜像成 `rk3568-` 前缀"的问题
   （摄像头 overlay 本名就叫 `radxa-zero3-rpi-camera-v2.dtbo`，**不带 rk3568 前缀**）：

   ```bash
   # ① 去掉 .disabled 后缀启用 overlay（IMX219 / Pi Cam v2）
   sudo mv /boot/dtbo/radxa-zero3-rpi-camera-v2.dtbo.disabled \
           /boot/dtbo/radxa-zero3-rpi-camera-v2.dtbo
   # ② 把完整路径追加到 fdtoverlays 行（与已有的 i2c4/npu/uart2 并列，空格分隔）
   sudo nano /boot/extlinux/extlinux.conf
   #    fdtoverlays  /boot/dtbo/rk3568-i2c4-m0.dtbo ...  /boot/dtbo/radxa-zero3-rpi-camera-v2.dtbo
   # ③ 重新生成引导配置（命令在 /usr/sbin，普通 SSH 的 PATH 可能找不到，用全路径）
   sudo /usr/sbin/u-boot-update
   sudo reboot
   # 不想手工编辑也可以用 Radxa 官方交互工具：sudo rsetup（Hardware peripherals 菜单）
   ```

   > 📎 **官方文档的 Armbian 做法（在你机器上不适用，仅备查）**：Armbian 的 overlay 名靠
   > `overlay_prefix=rk3568` 解析，而 Armbian 把文件命名为不带前缀的
   > `radxa-zero3-rpi-camera-v2.dtbo`，所以要先镜像一份带 `rk3568-` 前缀的文件名。
   > Radxa OS 用 fdtoverlays 完整路径直接引用，**不要去改名加前缀**。

2. `mediad` 必须启用——**没接摄像头时它找不到 `/dev/media*` 会退出、被 systemd 反复拉起、
   每十几秒烧满一核**（部署教程 §7.3 排障表有这条，你的板子已经把 mediad 停了：
   2026-09-17 实测 `systemctl is-enabled mediad` = `disabled`）。接好
   摄像头、配好 overlay 后第一件事就是把它开回来：
   ```bash
   sudo systemctl enable --now mediad
   ```

**验证命令**（接好摄像头后）：

```bash
robotctl health          # [media] 段显示摄像头型号和分辨率为正常
robotctl monitor         # 按 c 打开摄像头窗口
robotctl frame           # 存一帧到本机：机器人视角的一张快照（--quality 可调 JPEG 质量）
# 帧还有一个 HTTP 出口：http://<robot>:8080/frame（mediad 提供，遥控页面用它）
```

**画面质量**：`robotctl configure` 里 `[media]` 段的 `quality`/`resolution` 控制。
**检测器吃的是流不是快照**——`mediad` 先把帧送进 `duck-detect` 推理，再把带框的结果
编码推给远端（这就是 §2.7 的 NPU 链路在流水线里的位置）。

### 3.6 音频：【等硬件】一句话带过

音频 codec（I2S）由 `mediad` 管，麦克风听声音、喇叭发鸭子叫。验证就一句话：
`robotctl monitor` 按 `q` 旁边的声音栏有电平跳动 = 麦克风活着；放个声音文件喇叭响 = 输出活着。
**音频和视频共用 `mediad`**，所以"声音没了"先查 mediad 日志，再查 codec 驱动。

### 3.7 常见硬件故障速查表

| 现象                           | 先查                                                 | 下一步                                                                                       |
| ------------------------------ | ---------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| 电机总线一个舵机都不认         | `robotctl health` 总线行、`journalctl -u robotd` | 查`/dev/ttyS2` 是否存在（`ls /dev/ttyS2`）；没它就是内核/设备树问题                      |
| 突然全部舵机不认（之前好好的） | `sudo fuser -v /dev/ttyS2`                              | 出现`agetty` = serial-getty 复活占了总线；mask 掉（`sudo systemctl mask serial-getty@ttyS2`，本机实测已 masked） |
| 认到但`read failed` 刷屏     | 总线电压、接线松动                                   | `robotctl robot reboot-motors`；还不行换线                                                 |
| 只认一半舵机                   | 菊花链断点位置                                       | 从主控往后逐段排查插口；新插上的舵机重新上链                                                 |
| 换上新舵机不认                 | 新舵机出厂 ID 1 / 57.6 kbps                          | 等`robotd` 启动"收养"周期（§3.2），看 `journalctl -u robotd`                            |
| 某个关节指令 vs 实际偏差大     | monitor 该关节偏差                                   | 机械卡死/齿轮滑齿，拆下人工转确认                                                            |
| monitor 重力方向不对           | IMU 安装方向                                         | 重装 IMU 板或改标定，别硬调控制参数                                                          |
| 摄像头黑屏 | overlay 有没有去掉 `.disabled` 并写进 `fdtoverlays` 行（Radxa OS） | `sudo journalctl -u mediad -n 30` 看具体报错；`dmesg \| grep -i imx219` 看 sensor 有没有 probe 到（详见 §3.5，**别再按 Armbian 的 rk3568- 前缀镜像法排查**） |
| ToF 无数据但 tofd 活着         | `/run/tofd/tof.sock`、I2C 地址                     | `i2cdetect -y 4` 看设备在不在总线上                                                        |
| 负载莫名飙高                   | `systemctl show mediad -p NRestarts`               | **没接摄像头时 mediad 崩溃循环**，先 `disable --now`，接好再 enable（你已踩过）      |

---

## 4. 软件开发：改代码，推到板子上

> 目标：这一节结束，你能**改一行代码，一分钟内在板子上看到它生效**，并且知道自己推的
> 版本是谁、出问题怎么退回来。整节围绕一个命令：`scripts/dev-push.sh`——它就是"从改一行
> 到跑起来"的循环（`docs/robot/dev-push.md` 全文）。

### 4.1 代码结构：一个 workspace，七个 daemon

`microduck` 是**单个 Rust workspace**（`README.md` L92-99），顶层结构：

```text
microduck/
├── Cargo.toml            # workspace；[workspace.metadata.rknpu] pin 了 NPU runtime 版本（§2.7）
├── .cargo/config.toml    # 定义 cargo board（交叉编译别名，§4.3 用）
├── robotctl/             # CLI：你调试板子的唯一入口（§3 全用它）
├── duckctl/              # CLI：找板子/SSH/SCP（蓝牙、Wi-Fi 都没网也能找到板子）
├── robotd/               # 50 Hz 控制循环，唯一碰电机的人（改它最危险也最直观）
├── mediad/               # 摄像头/音频/WebRTC 远端
├── updaterd/             # 更新引擎：签名、原子切换、健康门、回滚（§4.3 的 apply、§4.7 的错误表都靠它）
├── configd/              # Wi-Fi、名字、配对
├── padd/                 # 手柄接收
├── btd/                  # 手机蓝牙连接
├── tofd/                 # ToF 8×8 深度矩阵（只发不收）
├── duck-detect/          # 检测器推理绑定（§2.7 讲过 dlopen rknn）
├── duck-ipc-proto/       # 共享协议：JSON-RPC 2.0 NDJSON 方法定义（全部 daemon 通信契约）
├── spaces/               # 示例项目（Python）：hello / vision-demo / policy-shop（§4.5）
├── scripts/              # dev-push.sh、provision-board.sh、seed-*.sh 等板级脚本
└── deploy/               # systemd unit、robotd.toml 部署配置
```

**要改什么先想清楚：** 控制逻辑在 `robotd/src/`（`main.rs` 是主循环，`control.rs` 是策略驱动，
`intents.rs` 是意图裁决）；命令契约在 `duck-ipc-proto`；CLI 在 `robotctl/src/`。
**"加个命令给鸭子" = 协议加方法 + robotd 实现 + robotctl 接线**，三处都改。

### 4.2 一次性准备（只在第一台板子做一次）

dev-push 的门槛有三件（`dev-push.md` §Once）：

```bash
# ① 板子必须是 dev board（允许开发密钥）
#    你已经配过了：/etc/robot/updater.toml 里 allow_dev_keys=true 且 team.dev.pub 已放好。
#    验证：sudo grep -c 'DEV BOARD' /var/lib/robot/provision.log   # 输出 1 为是

# ② 本机放开发签名密钥（私钥，板子只留 .pub）
mkdir -p ~/.duck-keys && cp /path/to/team.dev.key ~/.duck-keys/team.dev.key
# 密钥在别处就设：export DUCK_DEV_SECRET_KEY=/path/to/team.dev.key

# ③ 装交叉编译工具链（二选一）
cargo install cargo-zigbuild --locked      # 方式 A：zigbuild + zig
# 或不用装任何东西，每次推加 --docker     # 方式 B：Docker
```

> 💡 **为什么必须签名？** 板子的 `updaterd` 对任何代码一视同仁：验证签名 → 校验哈希 →
> 检查兼容 → 过健康门。开发密钥签的包，客户板子会**拒收**（跟拒收任意 `--ref` 一样），
> 反过来你的 dev 板也拒收任何没签名的东西。**没有"临时绕过"，这是设计不是麻烦。**

### 4.3 dev-push 闭环：改一行 → 板子上看到它

```bash
cd e:\optiDuck\joyandai\microduck

# 方式一：按名字推（板子在蓝牙范围内，会自动找到 IP 并缓存）
scripts/dev-push.sh --name duck-c51b        # 你的板子名，duckctl scan 可查
# 方式二：直接给地址（推荐日常用，绕开蓝牙）
scripts/dev-push.sh radxa@192.168.31.30
# 或设一次环境变量，之后直接跑
export DUCK_BOARD=radxa@192.168.31.30
scripts/dev-push.sh
```

完整输出长这样（每一步都在干什么，一目了然）：

```text
==> building 0.5.1-dev.local.1763400000.g7fc1444 for the board (zigbuild)  # 交叉编译
==> packaging                                                                 # 打成 release 同款包
==> signing with /Users/you/.duck-keys/team.dev.key                          # 签名
==> copying to radxa@192.168.31.30:/home/radxa/duck-sideload                 # 拷上板
==> applying on radxa@192.168.31.30                                          # robotctl update apply --from
==> 0.5.1-dev.local.1763400000.g7fc1444 is live on radxa@192.168.31.30
==> checking every daemon is running it
    current -> 0.5.1-dev.local.1763400000.g7fc1444
    [ok] robotd   [ok] configd   [ok] padd   [ok] updaterd   [ok] btd   [ok] mediad   [ok] tofd
```

版本号 `0.5.1-dev.local.<时间戳>.<commit>`：**每次推送带时间戳，两次推送同一棵 dirty 树不会撞车**。
板子上确认与回退：

```bash
robotctl version                        # 板上每个 daemon 实际跑的版本
sudo robotctl update rollback daemon    # 手动回退到上一个 release（目的性回退就这个命令）
```

**三个常用变体：**

```bash
scripts/dev-push.sh --dry-run radxa@192.168.31.30
# 干跑：构建/签名/拷贝/校验全做，只差最后"切换 current"。板子什么都不动。推前必用。

scripts/dev-push.sh --docker radxa@192.168.31.30
# 本机没有 zig 时用 Docker 构建（更慢，但零工具链要求）

scripts/dev-push.sh --bootstrap radxa@192.168.31.30
# 只用于板子版本 < 0.5.0 的第一次推（老 updaterd 没有 apply --from 的 API）。
# 会停 robotd 并放弃健康门——正常情况下绝不用。
```

**只看代码是否能在板子上编译（不推）：**

```bash
cargo board --bins        # = cargo zigbuild，用板子 target + glibc floor（.cargo/config.toml 定义）
```

### 4.4 第一个真实改动：加一条"被摸"日志

找一处真实代码，把 debug 级别提到 info，推上去就能在 `journalctl` 里看到。位置：
`robotd/src/main.rs` L2171-2182（抚摸检测事件处理）：

```rust
pet_detect::PettingEvent::Start => {
    let calm = !safety.fallen() && controller.as_ref().is_none_or(|c| !c.busy());
    if calm {
        tracing::info!("petting started");
        voice.play("coo", false);
    } else {
        tracing::debug!("petting detected (ignored: busy or down)");
        // 👇 你的第一行改动：改成 info，这样不用开 debug 就能在日志里看到"被摸但忙"的事件
        // tracing::info!("petting detected (ignored: busy or down)");
    }
}
```

改完推上去：

```bash
ssh radxa@192.168.31.30 'journalctl -f -u robotd'   # 开一个窗口盯日志（推之前开好）
scripts/dev-push.sh radxa@192.168.31.30             # 另一台终端推
# 等板上出现 "[ok] robotd"，去摸摸摄像头/ToF（pet_detect 用它们感知）
# journalctl 里出现 "petting detected (ignored: busy or down)" 即你的代码在跑了
```

> 📖 这条改动选它有三个理由：**位置真实**（不是我编的）、**改动最小**（一行）、**验证直观**
> （摸一下就有日志）。第一次 dev-push 用它建立"我改的代码真的在板子上跑"的信心，比任何
> 理论都强。

### 4.5 示例项目：spaces/ 里有什么

`spaces/` 是**独立的小项目**（不是 workspace 的一部分），演示"用现成接口拼功能"，
全部是 Python、用 `uv` 跑。

**① policy-shop —— 策略商店**（`spaces/policy-shop/app.py` L13-16 定义了它依赖的四个调用）：

```text
policy.fetch    {repo, file}   → updaterd 下载并读 manifest      （你的板子用 robotctl policy add 等价）
robot.setSkill  {name, path…}  → robotd 写入技能条目并重读技能
robot.policies                 → 重载成功了吗？change_error 字段说"不"及原因
robot.do        {skill}        → 跑它
```

本地跑：

```bash
cd spaces/policy-shop
uv venv && uv pip install -r requirements.txt
export DUCK_HOST=192.168.31.30     # 板子 IP
export HF_TOKEN=hf_xxx             # 读 HF 公共仓只需要一个真 token
uv run app.py
```

界面上选一个策略 → 点 install → 点 run：上面四个调用按顺序各来一次。
**这是"客户端怎么调鸭子"的最短教材**——四个调用就是 §2.5 那些 `robotctl policy …` 命令
的底层方法名。

**② vision-demo —— 视觉流**：`DUCK_RECEIVER` 指向机器人摄像头帧的接收端，
演示"不用板子也能看它看到什么"。跑法同样是 `uv run app.py`（`README.md` L81-98）。

**③ hello —— 最小演示**：最小可跑的"连上机器人 + 说句话"样板，看目录 README 即可。

> 💡 **spaces 和 daemon 的关系：** daemon 是板子上常驻的系统软件（Rust），spaces 是
> 你 PC/手机上的应用（Python）。**鸭子只提供 JSON-RPC 接口，怎么拼由你的应用决定。**
> 想加"遥控页面""自动投喂""语音对话"，都是在 spaces 这个层次做。

### 4.6 调试三板斧（按顺序用）

```bash
# ① 盯 daemon 日志（推代码前先开好）
ssh radxa@192.168.31.30 'journalctl -f -u robotd -u configd -u btd -u padd'
ssh radxa@192.168.31.30 'journalctl -f -u updaterd'    # 推更新时盯这个：每阶段、健康门、重启
# panic 会带完整 backtrace——发布版二进制不剥离符号，帧名都在

# ② 要 debug 级别的日志：drop-in 覆盖 RUST_LOG（默认 info）
sudo mkdir -p /etc/systemd/system/robotd.service.d
sudo tee /etc/systemd/system/robotd.service.d/log.conf > /dev/null <<'EOF'
[Service]
Environment=RUST_LOG=debug
EOF
sudo systemctl daemon-reload && sudo systemctl restart robotd
# drop-in 在 /etc 下，能挺过所有后续推送。调完删掉 + daemon-reload 即复原。

# ③ 问板子"你是谁、什么版本、健不健康"
robotctl health && robotctl version
```

### 4.7 dev-push 常见错误速查（全部来自 dev-push.md §When it does not work）

| 报错                                                                | 真实含义                                   | 处理                                                                                |
| ------------------------------------------------------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------- |
| `no dev signing key at ...`                                       | 板子拒收未签名产物                         | 放好`team.dev.key`，或设 `DUCK_DEV_SECRET_KEY`                                  |
| `signature did not verify against any of N usable trusted key(s)` | **板子不是 dev board**（不是包坏了） | `grep -c 'DEV BOARD' /var/lib/robot/provision.log` = 0 就走 install-dev.md 补配置 |
| `apply failed (exit 2)` + API mismatch                            | 板子版本太老，没有 apply --from            | 用一次`--bootstrap`，之后恢复正常                                                 |
| `preflight check failed: SideloadDir ... not there`               | 包放在`/tmp`/`/var/tmp` 下             | `updaterd` 有 `PrivateTmp=yes`，看不到你的拷贝；换 `~/duck-sideload` 等路径   |
| `could not reach <name> over Bluetooth`                           | 名字解析不到地址                           | `duckctl scan` 看板子在不在；直接给地址 `radxa@IP` 绕开蓝牙                     |
| ssh 重刷后连不上                                                    | 板子重新生成了 host key                    | `./scripts/provision-board.sh radxa@IP --forget-host-key`                         |
| `is live but not everything is running it`                        | 交换成功、健康门过了，但某 daemon 还在旧版 | `robotctl health` 看 units 块；`journalctl -u updaterd -b                         |
| libudev 链接错误（重刷后）                                          | 缓存的 libudev 拷贝过期                    | `rm -rf ~/.cache/duck-cross/aarch64`，下次推送重新抓                              |

---

## 5. 端到端演练：给鸭子加一个"被摸会回应"的行为

> 把三块串成一个完整功能。这个演练分**两个阶段**，第一阶段现在就能做（纯软件），
> 第二阶段等硬件。每个阶段结束都有明确的"过关"标准——不过关别进下一阶段。

**目标功能**：摸鸭子（ToF 近距离手势 / 摄像头识别），它发出一声"咕"并做个小动作。

### 阶段 0：摸底（现在，10 分钟）

```bash
robotctl health                       # 板子健康吗？哪个 daemon 没跑？
robotctl version                      # 板上 daemon 版本？
ssh radxa@192.168.31.30 'ls -l /opt/robot/policies/current /opt/robot/detector/current'
```

过关标准：health 全绿（mediad 无摄像头是预期状态，不算病）、版本 ≥ 0.5.0、
`policies/current → seed-v5`、`detector/current → seed-duck-v1`。

### 阶段 1：在 PC 上走通"训练 → 导出 → 彩排"（现在，1 小时）

```bash
cd e:\optiDuck\joyandai\microduck_rl

# 1) 冒烟测试（拦配置错误）
uv run train Mjlab-Velocity-Flat-MicroDuck --env.scene.num-envs 64 --agent.max_iterations 5

# 2) 导出（用官方 checkpoint；--checkpoint <iter> 填你冒烟跑出来的实际轮数）
uv run scripts/export.py Mjlab-Velocity-Flat-MicroDuck --onnx-file my_walk.onnx --checkpoint 5

# 3) CPU 彩排：真推理路径测延迟 + 动作序列
cargo run --release -p duck-control --example policy-rehearsal -- my_walk.onnx > rehearsal.json
cat rehearsal.json | head
```

过关标准：导出无警告、`rehearsal.json` 里 p50 延迟 < 20 ms、动作序列有限值。
**不过关就先停在这**——调出问题的是配置，不是鸭子。

### 阶段 2：发布 + 上板（现在能做的部分）

```bash
# 1) 发布到 HF（需要 HF 账号；repo 名必须是 <user>/microduck-<name>）
uv run publish --task Mjlab-Velocity-Flat-MicroDuck --checkpoint 5 \
  --repo <you>/microduck-my-walk --kind perpetual

# 2) 板子上装进 walk 槽位试跑（装到非当前正在跑的位置，安全）
ssh radxa@192.168.31.30
robotctl policy list                                  # 看 7 个槽位
sudo robotctl policy load walk <you>/microduck-my-walk
sudo robotctl policy reset walk                       # 试完放回原样
```

过关标准：`policy load` 返回成功（manifest 校验通过）、`policy list` 里 walk 槽显示你的策略名。

> 💡 想省掉第 1 步也行：先 `scp` 把 ONNX 拷到板子，再
> `sudo robotctl policy load walk /home/radxa/my_walk.onnx`（local 来源，同样持久生效）。
> **文件必须真的在板子上**，否则这个路径会被当成 `org/name` 仓库名而报
> `is not an org/name repo`（见 §2.5 的判定规则）。发布只在你需要
> "叫名字/绑手柄/分享/版本管理"时才有必要——§2.4 开头的分叉说明。

### 阶段 3：等硬件——接上舵机、摄像头后的收尾

```bash
# 1) 把 mediad 开回来（§3.5：现在它因为没摄像头被停着）
sudo systemctl enable --now mediad

# 2) 让鸭子站起来
robotctl robot init            # 上电回 home（放架子上！）
robotctl robot enable          # 交给策略（等于手柄 Start）
robotctl monitor               # 观察：能站住吗？指令 vs 实际偏差？

# 3) 触发"被摸"（阶段 2 的 pet 行为已就绪，取决于 pet_detect 配置的感知来源）
#    观察 monitor 的事件行 + journalctl 里我们的 petting 日志（§4.4）

# 4) 收工
robotctl robot relax           # 切电，收纳
```

过关标准：`init` 后不抽搐、`enable` 后站住超过 10 秒、被摸触发回应、`relax` 干净放倒。

> 🎯 **这套演练的路线图意义：** 阶段 1 验证"你的工具链通"、阶段 2 验证"你的模型契约对"、
> 阶段 3 验证"你的鸭子真身"。**每一步都能独立验收，卡在哪一步就去哪一节找答案**
> （阶段 1 → §2.1-2.3，阶段 2 → §2.4-2.5，阶段 3 → §3 + §4）。

---

## 6. 常见问题速查

**Q1：改完代码不想等整包编译，能不能只推一个文件？**
不能。daemon 是静态编译的 Rust 二进制，任何改动都要重编译 → dev-push。但增量编译约一分钟，
`--dry-run` 先验、再正式推，两分钟一轮。

**Q2：`robotctl policy load walk xxx` 说拒绝，为什么？**
`policy add/load` 在下载前就校验：`obs_len` 必须 61、`action_len` 14、`model_api` ≤ 当前 daemon、
`robot.model` 必须 `microduck`、命令编码必须 `constant`。看报错对应哪条（§2.5 校验规则）。

**Q3：模型怎么才算"部署好了"？**
三条 `current` 链（§2.6）：`/opt/robot/{policies,detector,daemon}/current` 指向你要的版本，
且 `robotctl policy list` / `robotctl duck-detector check` 显示已加载。**模型不重启 daemon，代码要重启。**

**Q4：板子没接摄像头，为什么 CPU 负载会飙高？**
mediad 崩溃循环（部署教程 §7.3 排障表 + §3.7）：找不到 `/dev/media*` 退出，systemd 反复拉起。
`sudo systemctl disable --now mediad`，接好摄像头再 enable。

**Q5：dev-push 报"板子不是 dev board"怎么办？**
`grep -c 'DEV BOARD' /var/lib/robot/provision.log` 输出 0 就是。走 `docs/robot/install-dev.md`：
放 `team.dev.pub`、改 `/etc/robot/updater.toml` 的 `allow_dev_keys=true`、重启 `updaterd`。

**Q6：训练导出的 ONNX 在彩排里延迟没问题，上板后还是慢？**
彩排在 PC 上跑（§2.3），板子 CPU 弱很多。上板后用 `robotctl health` 看 CPU、用 NPU 跑检测器
（§2.7），策略推理预算以**板子实测**为准，PC 数字只是下限参考。

**Q7：怎么把鸭子接回我自己的网络？（IP 变了）**
`duckctl scan` 蓝牙找名字 → `duckctl --name <名字> wifi connect <ssid> --psk <密码>`。
或者板子上 `robotctl system set-name` 改名后走蓝牙（§4.7 错误表）。

**Q8：更新 daemon 后板子失联了，怎么办？**
健康门 + 自动回滚（§4.3）：起不来的 release 会被自动回退。如果连 updaterd 都坏了，
SSH 上板：`sudo robotctl update rollback daemon`。再不行就上板刷机（部署教程兜底）。

---

## 7. 参考资料索引（按主题）

**概念与架构**

| 想读什么                                    | 文件                                                                          |
| ------------------------------------------- | ----------------------------------------------------------------------------- |
| 系统总览、7 daemon 职责、通信方式           | `joyandai/microduck/docs/design/architecture.md`                            |
| robotd 设计：50 Hz 循环、舵机总线、IMU 读取 | `joyandai/microduck/docs/design/robotd-design.md`                           |
| 更新引擎：签名、原子切换、健康门、回滚      | `joyandai/microduck/docs/design/update-pipeline.md`（如缺看 updaterd 源码） |

**模型**

| 想读什么                             | 文件                                                              |
| ------------------------------------ | ----------------------------------------------------------------- |
| NPU 上板全记录（性能、转换、阈值）   | `joyandai/microduck/docs/project/npu-bringup.md`                |
| 策略清单格式（manifest 契约）        | `joyandai/microduck/docs/policy-manifest.md`                    |
| 循环策略（LSTM、model_api: 2、彩排） | `joyandai/microduck/docs/recurrent-policies.md`                 |
| 训练配置 + 命令（HD1910）            | `joyandai/microduck_rl/docs/training_config_velocity_hd1910.md` |
| 训练仓命令与铁律                     | `joyandai/microduck_rl/AGENTS.md`                               |

**硬件**

| 想读什么                              | 文件                                                 |
| ------------------------------------- | ---------------------------------------------------- |
| 板子速查（health/monitor 等全部命令） | `joyandai/microduck/docs/robot/cheatsheet.md`      |
| ToF 上板与 IMU 配置决策               | `joyandai/microduck/docs/project/tof-on-demand.md` |
| 摄像头/媒体上板（overlay 坑）         | `joyandai/microduck/docs/project/media-bringup.md` |

**开发**

| 想读什么                             | 文件                                                |
| ------------------------------------ | --------------------------------------------------- |
| dev-push 全流程（本教程 §4 的原文） | `joyandai/microduck/docs/robot/dev-push.md`       |
| 开发板信任机制（dev key）            | `joyandai/microduck/docs/robot/install-dev.md`    |
| 找板子/SSH/SCP（蓝牙兜底）           | `joyandai/microduck/docs/robot/duckctl.md`        |
| 开发板速查（重启陷阱等）             | `joyandai/microduck/docs/robot/cheatsheet-dev.md` |

**示例项目**

| 项目        | 位置                    | 演示                |
| ----------- | ----------------------- | ------------------- |
| hello       | `spaces/hello/`       | 连上机器人 + 说句话 |
| vision-demo | `spaces/vision-demo/` | 收摄像头帧          |
| policy-shop | `spaces/policy-shop/` | 策略商店四个调用    |

**OpenMicroDuck 资料**（`E:\optiDuck\OpenMicroDuck\docs\`，本教程 §3.0.1/§3.0.2 的参考来源）

| 想读什么                                            | 文件                        |
| --------------------------------------------------- | --------------------------- |
| 舵机选型：XL330 vs HD1910 全对照、波特率、协议      | `docs/servo.md`           |
| 采购清单：桌面调试版 / 整机集成版两阶段 BOM         | `docs/bom.md`             |
| 主控选型：ZERO 3W 接口分配、RK3566、NPU 现状        | `docs/main_controller.md` |
| 结构说明：HD1910 与 XL330 的结构差异、3D 打印优化点 | `docs/structure.md`       |
| 硬件规格书（HD-1910 / HL 系列 / ED330）             | `hardware_spec/servo/`    |
| HD1910 结构 CAD（Bambu Studio 直接打印）            | `cad/openmicroduck.3mf`   |

---

## 8. 更新日志

| 版本 | 日期       | 内容                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ---- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| v1.22 | 2026-09-17 | 按 CAD 模型实测复核 §3.0.2 **轴承**采购项（此前只照抄 `bom.md`）。新增"轴承怎么买"详注块：**尺寸双重确认**——解析训练模型两个轴承网格包围盒，`seeed_bearing__configuration_default` = 外径 15 × 厚 3（= 6700K）、`...__22x16x4` = 外径 22 × 厚 4（= ET2216），与 `bom.md` 逐字吻合；⚠️ 提示 **6700K 必须买"厚 3"薄款**（市面常见 6700 标准件是 10×15×4，买错会顶装配面）；❗ **指出数量冲突**：`bom.md` 写 ET2216 ×1，而 CAD 模型里 16×22×4 出现 **11 次**（躯干×2、yaw2roll、bearing_roll、左右髋、左右膝、neck_pitch、yaw_roll_motion×2，约每关节一个），标注"下单前看装配图/问作者确认，宁按 11 备料"，并给出复核命令（在 `robot_openmicroduck_groundcontact.xml` 搜 `seeed_bearing` 数 `class="visual"` 行数）。原文"轴承 6700K × 3 与 ET2216 × 1"保留但指向该注 |
| v1.21 | 2026-09-17 | **按板子实测全面校正 §3 硬件章（非照抄官方文档）**。§3.0 新增"本机实测基线"（Radxa OS / Debian 12 bookworm，内核 6.1.84-10-rk2410-nocsf，2 GB，AIC8800；已启用 overlay 清单）；**NPU 节点纠错**：by-path `platform-fde40000.npu-render` 实测指向 **renderD129**（renderD128=display、renderD130=GPU），改为按 by-path 识别、编号不固定；**I²C 总线纠错**：本机 overlay 是 `rk3568-i2c4-m0` → `/dev/i2c-4`（OpenMicroDuck 文档写的"I²C3 M0"与本机设备树不符，注明以实测为准）；UART2 serial-getty 段去掉"Armbian 默认坑"归因，改两系统通用 + 本机实测 `masked`；`fuser` 命令补 sudo。§3.0.2 **摄像头纠错与选型**：IMX219 是 **800 万**（3280×2464，官方 BOM"500 万"系误写，500 万是 OV5647）；明确下单规格 77° 800 万裸模组，凭本机 overlay + `/etc/iqfiles/`（无 IMX519）+ mediad 硬编码三重证据排除 IMX519/OV5647/广角款。§3.5 摄像头 overlay 启用改为 **Radxa OS 实际流程**（去 `.disabled` + fdtoverlays 完整路径 + `/usr/sbin/u-boot-update`，摄像头 overlay 不带 rk3568 前缀），Armbian 前缀镜像法降级为"仅备查、本机不适用"；§3.7 故障表摄像头行同步改写；音频补"无 HAT 时只有 rockchip-hdmi 声卡"实测 |
| v1.20 | 2026-09-17 | 按"彩排→发布→上板"实测校正 §2 全链路逻辑。§2 路线图：把"发布到 HF"改为**可选分叉**（自用可直接本地占槽）。§2.3 重写为 2.3.1 训练仓彩排 / 2.3.2 上板仓彩排六步：新增装 Rust（rsproxy 镜像）、用 codeload 把上板仓代码弄到板子、crates `sparse+` 镜像、URL 被渲染成代码样式导致的反引号复制陷阱、彩排结果判定表、"彩排≠上板"警示。§2.4 新增"发布非必经之路"分流；HF token 创建按实际页面（Fine-grained + ⬆ Write 预设）；明确登录/发布都在**开发机**、板子不需要 HF token；新增 `kind`→安装去向映射表（`episodic`→技能 / `perpetual --unwind-s`→`--hold` 技能 / `perpetual`→槽位）、`--slot` 仅显示用、`--force` 覆盖规则、同一份 ONNX 两种装法对比。§2.5 修正 `policy load` 为持久写入（非"临时试试"）；新增"路径不存在会被当成 `org/name` 仓库"判定规则（`is not an org/name repo`）；新增槽位三来源表与 `sitstand`/`ground_pick` 的 encoding 约束；新增实测 `policy list` 输出（`stand` 槽默认空）；新增 `accepted but had not made it after 20s` 报错解读（配置已写入 vs live 未生效 + journalctl 舵机自检证据 + `reset` 撤销）；补 standup 触发链路。§5 阶段 2 补"可跳过发布"等价路径与文件必须存在的提醒 |
| v1.1 | 2026-09-16 | 参考 OpenMicroDuck 文档补充硬件细节：§3.0 修正官方舵机 ID 布局（左腿 20–24 / 右腿 10–14 / 头 30–34 / IMU 200），新增主控接口分配表与总线寄存器参数（1 Mbps /`baud_rate=3` / `return_delay_time=0`）；新增 §3.0.1 舵机选型（HD1910 vs XL330 对照、针序警告、换型号必须重训）；新增 §3.0.2 两阶段采购清单；§3.2 补充舵机"收养"机制与 `fuser -v /dev/ttyS2` 排查、修正 reboot-motors 示例 ID；§3.7 故障表新增 3 行；§2.1 补"换执行器 = 重训策略"；§2.4 补发布前置（`hf auth login` / `HF_TOKEN`、Write 权限 token、Jobs 权限区分）；§7 增加 OpenMicroDuck 资料索引 |
| v1.0 | 2026-09    | 首次成稿：§0 路线图、§1 全局观、§2 模型部署全链路、§3 硬件调试、§4 软件开发、§5 端到端演练、§6 FAQ、§7 索引                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

*本教程与《Radxa_ZERO_3W_零基础部署调试教程.md》配套使用：部署教程回答"怎么把它装起来"，
本教程回答"怎么让它干我想让它干的事"。*
