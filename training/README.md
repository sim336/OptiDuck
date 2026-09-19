# training/ · 强化学习训练侧的改动

这里放的是**在别人开源仓库里做的改动**（补丁 + 改动后的文件），用于溯源：
哪些是上游的，哪些是本项目自己加的。

训练本身需要在装了 CUDA 的机器上跑 mjlab + MuJoCo，本目录**不是可直接运行的训练仓**，
它是「改动集」——把它应用回上游仓才能跑。

---

## 一、两个上游仓与本地改动

| 子目录 | 上游仓库 | 本地改动性质 |
|---|---|---|
| `microduck_rl/` | [pollen-robotics/microduck_rl](https://github.com/pollen-robotics/microduck_rl)（`develop` 分支） | 4 个官方文件被改写 + 一套新增的 `crazy_chick` 模型与任务 |
| `Open_Duck_Playground/` | [apirrone/Open_Duck_Playground](https://github.com/apirrone/Open_Duck_Playground)（`main` 分支） | 1 处：给 MJCF 补 `sts3215_12v` 执行器类 |

---

## 二、目录结构

```
training/
├── microduck_rl/
│   ├── patches/
│   │   └── tracked_changes.patch            ← 4 个官方文件的 diff，可直接 git apply
│   ├── scripts/                             ← 新增：冒烟 / 渲染 / 实时可视化脚本
│   │   ├── live_open_duck.py
│   │   ├── render_open_duck_rollout.py
│   │   ├── smoke_crazy_chick.py
│   │   └── smoke_open_duck.py
│   ├── src/mjlab_microduck/
│   │   ├── actuator/friction_dr_bam.py      ← 修改版
│   │   ├── robot/crazy_chick/               ← ★ 自研：MJCF / 常量 / 生成器 / 网格
│   │   │   ├── crazy_chick_*.xml            （walk / allcollisions / 各 backlash 变体）
│   │   │   ├── open_duck_walk.xml           （SolidWorks 形态）
│   │   │   ├── make_crazy_chick.py / make_open_duck.py
│   │   │   ├── assets/params_sts3215_m6.json
│   │   │   └── open_duck_assets/*.STL
│   │   └── tasks/
│   │       ├── microduck_jump_env_cfg.py        ← ★ 新增任务（62D 观测 / 15D 动作）
│   │       ├── microduck_open_duck_env_cfg.py   ← ★ 新增任务（61D / 14D，velocity）
│   │       ├── microduck_roller_standup_env_cfg.py  ← 修改版（关节索引修正）
│   │       └── __init__.py                      ← 修改版（注册新任务）
│   └── tests/test_roller_standup_cfg.py     ← 修改版（配套索引测试）
│
└── Open_Duck_Playground/
    ├── patches/0001-12v-parameters.patch
    └── playground/open_duck_mini_v2/xmls/open_duck_mini_v2_backlash.xml
```

---

## 三、改了哪些、为什么

### 1. `actuator/friction_dr_bam.py` —— 修一个 AttributeError

`FrictionDRBamActuator.initialize()` 里缺了 `q_target_smooth` 的初始化，
走 mjlab 这条路径时会抛 `AttributeError`。补上即可，不影响 BAM 的辨识语义。

### 2. `tasks/__init__.py` —— 注册两个新任务

- `Mjlab-Jump-Flat-CrazyChick` —— 跳跃任务，**62 维观测 / 15 维动作**
- `Mjlab-Velocity-Flat-OpenDuck` —— 速度跟踪任务，**61 维观测 / 14 维动作**（SolidWorks 导出形态）

> 观测/动作维度是**硬契约**：训出来的 ONNX 输入输出必须和板端 `robotd` 期望的维度一致，
> 维度不对会在上板推理时直接报错。

### 3. `microduck_roller_standup_env_cfg.py` —— 修 CUDA 越界

`_LEG_JOINTS` / `_NECK_JOINTS` 用的还是「16 个舵机」的索引，但滚轮关节被 `passive_` 过滤掉之后
实际只剩 14 个，索引会越界。改成 14 舵机视图的 0–13 后正常。

### 4. `robot/crazy_chick/` —— 整套自研仿真模型

从 SolidWorks 结构件导出、重算质心与惯量后生成的 MJCF，包含：

- `crazy_chick_walk.xml` 系列 —— 走步任务用
- `*_allcollisions*.xml` —— 打开全部碰撞体（含滚轮变体），用于排查自碰撞
- `*_backlash*.xml` —— 带**齿隙/回差**建模的变体（舵机减速箱回差是 sim2real 的主要 gap 之一）
- `open_duck_walk.xml` —— SolidWorks 形态的 Open Duck，配套 `open_duck_assets/` 网格

配套的 `assets/params_sts3215_m6.json` 是 STS3215 舵机的作动器参数。

### 5. `Open_Duck_Playground/…/open_duck_mini_v2_backlash.xml` —— 12 V 参数

上游默认按 7.4 V 的舵机特性给的增益与限幅。换 12 V 供电后阻尼、摩擦损耗、armature、
kp、力范围都要跟着改，于是新增了一个 `sts3215_12v` 执行器类，并把各关节的
`class="sts3215"` 改为 `class="chosen_actuator"`。

---

## 四、怎么把改动放回上游仓

> 前提：上游仓已经 pull 到与本目录记录时相同的基线；若上游又更新过，应用补丁可能冲突，
> 冲突时请以 `patches/*.patch` 为准人工合并。

### microduck_rl

```bash
git clone https://github.com/pollen-robotics/microduck_rl.git
cd microduck_rl
git checkout develop

# 1) 应用对官方文件的修改
git apply /path/to/OptiDuck/training/microduck_rl/patches/tracked_changes.patch

# 2) 复制新增文件（保持相对路径）
cp -r /path/to/OptiDuck/training/microduck_rl/src/mjlab_microduck/robot/crazy_chick  src/mjlab_microduck/robot/
cp    /path/to/OptiDuck/training/microduck_rl/src/mjlab_microduck/tasks/*.py       src/mjlab_microduck/tasks/
cp    /path/to/OptiDuck/training/microduck_rl/scripts/*.py                          scripts/
cp    /path/to/OptiDuck/training/microduck_rl/tests/test_roller_standup_cfg.py      tests/
```

### Open_Duck_Playground

```bash
git clone https://github.com/apirrone/Open_Duck_Playground.git
cd Open_Duck_Playground
git apply /path/to/OptiDuck/training/Open_Duck_Playground/patches/0001-12v-parameters.patch
```

---

## 五、和 `microduck_rl` 子模块的关系

主仓根目录的 `microduck_rl/` 是**子模块**（可编译、可跑训练的那份）；
本目录的 `training/microduck_rl/` 是**改动集**（小而全，方便看懂与复用）。

两者内容有重叠但不完全等同：子模块里还包含 HD1910 台架标定数据、BAM 参数、
训练权重归档等大体积内容，那些走 Release 发布，不在这里。

---

## 六、许可

见仓库根目录 [NOTICE.md](../NOTICE.md) §3。
`microduck_rl` 为 Apache-2.0；`Open_Duck_Playground` 上游未声明许可证文件，
此处仅作学习记录保留，再分发前请与原作者确认。