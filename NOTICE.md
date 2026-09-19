# NOTICE · 许可与署名

OptiDuck 是一个**个人学习 / 复刻项目**，由多份来源不同的内容拼装而成。不同部分的许可**并不相同**，
使用、修改、再分发前请按下面这张表逐项确认。

---

## 1. 本仓自有内容

| 路径 | 内容 | 许可 |
|---|---|---|
| `docs/`（`internal/` 除外）、`tools/`、`board/`、`app/`、`assets/`、根目录 `README.md` | 自研文档、URDF/MJCF 工具链、板端运维脚本、Android App、截图 | **Apache-2.0**（见 [LICENSE](LICENSE)） |
| `docs/internal/` | 个人工作草稿（未整理的上游版教程），保留仅为记录 | 同上 |

Copyright (c) 2026 刘玉龙 (Liu Yulong)

---

## 2. 三个子模块（各有独立仓库与独立许可）

| 子模块路径 | 上游仓库 | 许可 | 说明 |
|---|---|---|---|
| `joyandai/microduck` | [pollen-robotics/microduck](https://github.com/pollen-robotics/microduck) | **Apache-2.0** | 上板 Rust 软件栈；本仓内是**带本地改动的变体**（HD1910 舵机 bring-up、IMU 改 I2C 等） |
| `microduck_rl` | [pollen-robotics/microduck_rl](https://github.com/pollen-robotics/microduck_rl) | **Apache-2.0** | RL 训练仓；本仓内是**带本地改动的变体**（HD1910 台架标定、BAM 作动器参数等） |
| `OpenMicroDuck` | [JoyandAI/OpenMicroDuck](https://github.com/JoyandAI/OpenMicroDuck) | **双许可**：软件 Apache-2.0；`cad/`、`assembly-drawings/`、文档为 **CC BY-NC-SA 4.0（禁止商用）** | 结构与硬件（SolidWorks CAD / BOM / 渲染图） |

> ⚠️ **`OpenMicroDuck` 的 CAD 与文档是不可商用的。** 想拿本项目做商业用途，请把这一块排除，
> 或另行联系原作者获取授权。子模块内的 `NOTICE.md`、`LICENSE-*` 是权威版本。

---

## 3. `training/` 下的派生内容

`training/` 里放的是本人在**别人开源仓库里做的改动**（补丁 + 改动后的文件），
原始项目版权归各自作者所有：

| 子目录 | 来源 | 上游许可 |
|---|---|---|
| `training/microduck_rl/` | [pollen-robotics/microduck_rl](https://github.com/pollen-robotics/microduck_rl)（`develop` 分支） | **Apache-2.0** |
| `training/Open_Duck_Playground/` | [apirrone/Open_Duck_Playground](https://github.com/apirrone/Open_Duck_Playground) | **上游仓库未声明许可证文件**（`pyproject.toml` 亦无 license 字段）。此处仅作个人学习记录保留，**若需再分发请先与原作者确认** |

`training/microduck_rl/src/mjlab_microduck/robot/crazy_chick/` 里的 MJCF 由
[apirrone/Open_Duck_Mini](https://github.com/apirrone/Open_Duck_Mini)（**Apache-2.0**）的结构件导出改造而来，
网格（STL）来源于该项目的 CAD。

> `training/` 的意义是**溯源**：说明哪些是上游的、哪些是本项目改的。上游若更新，请以
> 上游仓库为准重新应用，不要直接把这里的文件当成上游新版本。

---

## 4. `assets/` 里的图

| 类型 | 来源 | 性质 |
|---|---|---|
| `assets/screenshots/*` | 在本机浏览器 / Android 模拟器 / 真实开发板上**实际运行截图** | 自研截图，随本仓 Apache-2.0 |
| `assets/renders/*` | MuJoCo 离线渲染输出的仿真帧 | 自研渲染，随本仓 Apache-2.0 |
| `tools/urdf/models/open_duck_urdf/` | SolidWorks 导出的 Open Duck URDF 与网格 | 结构版权归 Open Duck 项目原作者，见上 |

**本仓不含任何 AI 生成图片**；所有示意图与截图都可追溯到具体命令或运行时。

---

## 5. 硬件免责

本项目涉及锂电池、舵机堵转、3D 打印结构件与自制的舵机电源轨。
复刻过程中若因接线、供电、结构强度问题造成**器件损坏、电池起火或人身伤害**，
由操作者自行负责。上电前请务必确认极性、电压与限流。