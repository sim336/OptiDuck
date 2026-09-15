# OptiDuck

OptiDuck 是一台自制的 **microduck** 四足机器人：主控为 **Radxa ZERO 3W**（RK3566 / 2GB），
跑 Radxa OS（Debian 13，6.1 内核），舵机总线为 UART2，姿态传感器走 I2C4，
步态策略用强化学习在 MuJoCo 里训练后导出 ONNX 上板。

这个仓库是**总入口（umbrella）**：它本身只放文档和归档，三块代码分别以 git submodule
的形式引入。

---

## 仓库结构

| 路径 | 内容 | 上游 | 许可 |
|---|---|---|---|
| `joyandai/microduck` | 上板软件栈：`robotd` / `configd` / `btd` / `padd` / `updaterd` 等 Rust daemon，`robotctl` / `duckctl` CLI，板级脚本 | [pollen-robotics/microduck](https://github.com/pollen-robotics/microduck) | Apache-2.0 |
| `microduck_rl` | 强化学习训练仓：mjlab + MuJoCo 任务、机器人 XML、HD1910 台架标定与 BAM 作动器辨识 | [pollen-robotics/microduck_rl](https://github.com/pollen-robotics/microduck_rl) | Apache-2.0 |
| `OpenMicroDuck` | 结构与硬件：SolidWorks CAD、BOM、渲染图、舵机适配评审 | [JoyandAI/OpenMicroDuck](https://github.com/JoyandAI/OpenMicroDuck) | 双许可：**软件 Apache-2.0 / 硬件与文档 CC-BY-NC-SA-4.0（非商用）** |
| `Radxa_ZERO_3W_零基础部署调试教程.md` | 从烧卡到上板的全流程教程（含踩坑记录） | — | — |
| `_scratch_archive/` | 草稿归档：临时 CAD、渲染出图、抓取日志 | — | — |

> `OpenMicroDuck` 的 CAD/文档是 **CC-BY-NC-SA-4.0**，非商用。其余为 Apache-2.0。
> 前两个子仓是上游仓库的**本地变体**，保留了完整上游历史，详见下面「和上游的关系」。

---

## 一次拉全

```bash
git clone --recurse-submodules https://github.com/sim336/OptiDuck.git
```

已经 clone 过、但当时没带子模块：

```bash
git submodule update --init --recursive
```

---

## 从哪读起

板子相关的全部内容都在教程里：
[Radxa_ZERO_3W_零基础部署调试教程.md](./Radxa_ZERO_3W_零基础部署调试教程.md)

| 想做什么 | 看哪一节 |
|---|---|
| 先把坑避掉（电源、烧录、Wi-Fi 自动连接） | §0 踩坑血泪史 |
| 烧系统到 SD 卡 | §4 |
| SSH 免密登录，从此不要键鼠显示器 | §5 |
| 板级环境配置（舵机总线 / I2C IMU / 依赖） | §6 |
| 装 `robotd` 等 daemon 软件栈 | §7 |
| 验证装好了没 | §8 |
| 日常改代码 → 上板 | §10（含 `dev-push.sh`） |
| 自己发版 / 用开发签名 | §12 |

板子的当前地址记在 [ssh远程地址.txt](./ssh远程地址.txt)。

---

## 改动怎么同步

三个代码仓是**独立仓库**，各自提交推送；主仓只记录「指向哪个 commit」。
所以一次完整的改动要两步：

**第 1 步 —— 在子仓里改、提交、推送**

```bash
cd microduck_rl          # 或 joyandai/microduck、OpenMicroDuck
git add -A
git commit -m "..."
git push
```

**第 2 步 —— 回主仓把指针更新一下**

```bash
cd ..                    # 回到 OptiDuck 根目录
git add microduck_rl     # 路径就是子模块路径
git commit -m "bump microduck_rl"
git push
```

第 2 步不能省：别人 clone 主仓时拿到的是主仓里记录的 commit，
你不提交指针，别人就还停在你上次提交的那个版本。

> 如果子仓里出现了没提交的改动，主仓 `git status` 会提示
> `modified: microduck_rl (new commits)` —— 这就是在看第 2 步该不该做。

### 合并上游更新

两个 code 仓都保留了上游历史，`upstream` 指向原作者：

```bash
cd joyandai/microduck
git fetch upstream
git merge upstream/main
```

`microduck_rl` 的上游默认分支是 `develop`。

---

## 和上游的关系

`joyandai/microduck` 和 `microduck_rl` 都不是干净的上游副本，而是**带本地改动的变体**。
为了让以后的合并干净可查，它们都被重新挂到了上游历史里最接近的那个 commit 上，
本地改动各自落成一个独立 commit：

- `joyandai/microduck`：基线 `2c61dcc`（上游 2026-09-02），本地变体 commit `83322b6`
  —— HD1910 台架 bring-up、IMU 迁到 I2C、舵机总线工具、`source-lessons/` 等 57 个文件。
- `microduck_rl`：上游分支 `develop`，本地改动为 HD1910 台架 bring-up、BAM 作动器参数、
  `air_time` 奖励调参与碰撞配置拆分。

这样 `git log` 能直接看出「哪些是上游的、哪些是我们改的」，
`git diff upstream/main` 就是全部本地差异。

---

## 大体积数据

以下数据**不在 git 里**（避免仓库膨胀且历史不可撤销），按需作为 Release 附件发布：

| 数据 | 体积 | 说明 |
|---|---|---|
| `joyandai/microduck_rl/hd1910_calibration/` | ~426 MB | HD1910 舵机台架标定原始 `.log` 轨迹与拟合 `.json` |
| `model_archive/` | ~405 MB | 训练权重归档（sitstand / stand / velocity） |

标定数据拟合后的结果已经浓缩进 `microduck_rl/vendor/bam/bam/params/hd1910/*.json`，
只做仿真训练的话不需要下载原始数据。