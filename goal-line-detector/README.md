# 足球进球检测（门框俯视机位 · RF-DETR）

机位在球门框上方/后方俯视，判断足球是否**整体越过白色门线**。第一阶段只做**离线视频验证**：
输入一段俯视录像 → 输出每帧球位置、越线时刻、进球事件与可视化回放，用于验证算法链路与调参。

> 模型框架说明：原计划的 Football Transformer（FoT, Sci Rep 2025）**没有公开代码与权重**，无法直接复现；
> 本项目改用同样面向 SoccerNet 场景的 **RF-DETR**（Roboflow 开源，Apache-2.0，DINOv2 主干）作为球检测器。
> 球检测只负责"球在哪"，"是否进球"由几何 + 多帧判定完成，不依赖端到端模型。

## 1. 整体流程

```
视频解码 ──► RF-DETR 检测足球 ──► 卡尔曼跟踪（补丢帧/平滑） ──► 几何判定（球心到门线带符号距离）
                                                                    │
                            多帧确认（连续 N 帧整体越线）+ 回溯越线时刻 + 冷却/重新武装
                                                                    ▼
                        annotated.mp4 + frames.csv + events.json
```

关键点：

- **门线由人工标定**：在首帧上点几个点画出球门线（支持多段折线，兼容镜头畸变），再点一个"球门内侧"点确定方向。
- **"整体越线"**：默认 `require_whole_ball=true`，判据为 `球心到门线内侧距离 ≥ 球半径 + 白线半宽`。
- **多帧确认**：连续 `confirm_frames` 帧满足越线才确认，抑制误检；期间用跟踪预测补帧，但确认窗口内至少要有
  `min_visible_in_confirm` 帧是真实检测（防止"跟丢后靠预测硬凑"）。
- **越线时刻回溯**：确认后回退到最后一个仍在线外的有效样本，线性插值出球心穿过门线的帧号/时间，比直接报
  "确认帧"更准。
- **去抖**：进球后必须连续 `release_frames` 帧离开越线区、且距上次进球超过 `cooldown_s`，才重新武装。

## 2. 环境

已在当前机器实测通过：

| 项 | 版本/结果 |
| --- | --- |
| Python | 3.13.5 (Anaconda) |
| torch | 2.11.0+cu128，CUDA 可用 |
| GPU | RTX 5070 Laptop 8GB |
| rfdetr | 1.12.0（`pip install rfdetr`） |
| 实测权重 | `rf-detr-small.pth` 386MB，MD5 `fb37061c...` 校验通过，`sports ball` = COCO 类别 32 |

```powershell
python -m pip install rfdetr
# 权重默认缓存到 %USERPROFILE%\.roboflow\models，可用 RF_HOME 指定目录：
$env:RF_HOME = "D:\ball\.models"
```

8GB 显存建议 `--model small`（512px，推理显存约 0.2GB）；`medium`(576px) 也可跑，`large` 不建议。

## 3. 使用

```powershell
cd d:\ball\soccer-django\goal-line-detector

# 1) 标定门线：左键点门线（≥2 点，右键撤销），ENTER 进入下一步，再左键点球门内一点，ENTER 保存
python -m goalline label --video D:\path\clip.mp4 --out configs\my_goal.json

# 2) 离线跑一遍（先不加 --weights 会走默认缓存/自动下载）
python -m goalline run --video D:\path\clip.mp4 --config configs\my_goal.json ^
    --out runs\clip01 --model small --weights D:\ball\.models\rf-detr-small.pth

# 3) 微调后的球模型
python -m goalline run --video D:\path\clip.mp4 --config configs\my_goal.json ^
    --out runs\clip01 --checkpoint runs\finetune\checkpoint_best_ema.pth
```

常用参数：`--limit 300`（只处理前 300 帧，冷启动调试）、`--start 12.5`（从 12.5s 开始）、`--show`（边跑边预览）、
`--batch-size 8`（检测批大小）、`--threshold 0.3`、`--confirm-frames 3`。

输出目录：

| 文件 | 内容 |
| --- | --- |
| `annotated.mp4` | 叠加门线、球框/轨迹、深度数值、GOAL 标记的回放视频 |
| `frames.csv` | 每帧：球心、半径、置信度、带符号深度、`t_on_line`、是否越线、状态、事件号 |
| `events.json` | summary + 进球事件（回溯越线帧/时间、确认帧/时间、最大越线深度、命中门线位置比例） |
| `summary.json` | 处理帧数、时长、进球数、使用的判定参数 |

单元测试（不需要视频与模型）：

```powershell
python -m unittest discover -s tests -v
```

## 4. 配置项（`configs/*.json`）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `goal_line` | — | 门线折线点 `[[x,y], ...]`，像素坐标（输入视频分辨率） |
| `inside_point` | — | 球门内侧一点，用于确定"进球方向" |
| `detection.threshold` | 0.35 | 球检测置信度阈值 |
| `detection.class_names` | `["sports ball", ...]` | 模型标签里视为"球"的类名，按顺序匹配 |
| `detection.min/max_radius_px` | 4 / 120 | 按框尺寸过滤误检 |
| `tracking.gate_px` | 140 | 跟踪门限，超出门限视为新目标（重新初始化） |
| `tracking.max_missed` | 10 | 连续丢检帧数上限，超过则重置轨迹 |
| `judge.line_half_width_px` | 6 | 白线半宽（像素），影响"整体越线"判据；**按实际画面量** |
| `judge.require_whole_ball` | true | `false` 则球心越线即算 |
| `judge.confirm_frames` | 3 | 连续越线确认帧数（25fps 约 0.12s） |
| `judge.release_frames` | 5 | 离开越线区多少帧后重新武装 |
| `judge.cooldown_s` | 2.0 | 两次进球最小间隔（秒） |
| `judge.min_visible_in_confirm` | 1 | 确认窗口内最少真实检测帧数 |

调参建议：检测漏检多 → 降 `threshold`、升 `max_missed`；误报多 → 升 `confirm_frames`、提高 `threshold`、
收紧 `min/max_radius_px`；球速极快导致穿过门线后立刻被网弹回 → 降 `confirm_frames`。

## 5. 没有素材时怎么冷启动

当前手上没有视频与标注，按三步走，每步都有可交付物：

**阶段 A：零样本基线（0 标注）**
用 COCO 预训练的 `sports ball` 类直接跑真实片段，人工记录：能否检出、漏检集中在什么场景（球太小、运动模糊、
被球员遮挡、白球贴白线）。这一步只验证"机位 + 分辨率 + 球像素尺寸"是否够用。经验上球在画面里小于 12~15px
直径时，COCO 模型召回会明显下降，此时必须走阶段 C。

**阶段 B：公开数据预热（0 自采）**
用公开足球数据集（SoccerNet 帧、Roboflow Universe 的 football/soccer ball 检测集）先微调出单类 `ball` 模型，
让主干适应"草皮背景 + 高速小目标"，比 COCO 80 类更聚焦。

**阶段 C：俯视场景小样本微调（100~300 帧足够起步）**
1. 采集：同一机位、同一镜头，覆盖白天/夜灯、有/无遮挡、进球/不进球、球速快慢；建议 8~12 段短片（每段 10~30s），
   分辨率与实际部署一致。
2. 半自动标注：先用阶段 B 的模型推理，导出预测框；人工只做"改框/删错框/补漏框"，比纯手标快 3~5 倍。
   标注规范：只标 `ball` 一类；框贴球外接矩形；被完全遮挡的帧不标（不要凭空猜）；球贴线时不要标成门柱。
3. 划分：按**片段**划分 train/valid（不要按帧随机划分，否则相邻帧泄漏会虚高指标），建议 70/30。
4. 格式：RF-DETR 要求 COCO 目录结构 `train/_annotations.coco.json`、`valid/_annotations.coco.json`；
   CVAT / Roboflow 导出 COCO 即可直接用。
5. 微调（8GB 显存建议 nano/small + batch 2~4）：
   ```powershell
   python -m pip install "rfdetr[train,loggers]"
   python train\finetune.py --dataset data\goal_ball --variant small --epochs 40 --batch-size 4
   ```
6. 评估指标（必须分开看，否则会误判）：
   - 球检测：Recall@0.5（重点，漏检直接导致漏判进球）、mAP@0.5、误检/分钟；
   - 判定：进球事件级 **召回率 / 误报率**、越线时刻误差（与人工标注帧对比，单位帧）、重复事件数；
   - 端到端：每段视频人工标注"进球 / 未进球"，统计混淆矩阵。

**验收门槛建议（第一阶段）**：球检测 Recall ≥ 0.95（进球前后 1s 窗口内）、判定召回 100%、误报 ≤ 1 次/30 分钟、
越线时刻误差 ≤ 2 帧。

## 6. 已知局限

- 未用真实俯视素材验证，当前只保证**链路正确**，不代表现场精度。
- 单目 2D 门线：球在门线上方（空中、门梁附近）与贴地越线在图像上可能重合，纯 2D 判定会把"高出横梁的球"误判为进球。
  规避：门线只画在门框内下沿区域 + 结合球框尺寸突变（球变大=靠近镜头）做辅助过滤，或后续加第二机位/深度信息。
- 球被守门员或门柱完全遮挡时依赖跟踪预测，超过 `max_missed` 会重置轨迹。
- `line_half_width_px` 与球半径都以像素为单位，机位或分辨率变化后必须重新标线 + 重新量白线宽度。
- 极端快球 + 低帧率（<25fps）可能只有 1~2 帧越线，需降低 `confirm_frames` 或提高帧率。

## 7. 目录

```
goal-line-detector/
├── goalline/
│   ├── config.py      # JSON 配置与覆盖
│   ├── detector.py    # RF-DETR 球检测（零样本 / 微调权重）
│   ├── tracker.py     # 单目标卡尔曼跟踪
│   ├── geometry.py    # 门线折线、带符号距离、越线插值
│   ├── judge.py       # 多帧确认状态机 + 事件生成
│   ├── render.py      # 可视化叠加
│   ├── pipeline.py    # 视频 → mp4/csv/json
│   ├── label.py       # 交互式门线标定
│   └── cli.py         # 命令行入口
├── configs/example_goal.json
├── train/finetune.py  # COCO 数据集微调
├── tests/test_geometry_judge.py
└── requirements.txt
```

## 8. 后续可扩展（不在本阶段）

- 多机位/球门侧视角融合，解决"球在空中越过门线投影"的歧义；
- 检测 + 分割（RF-DETR Seg）输出球的掩膜，用掩膜与门线相交面积替代半径近似；
- 把 `events.json` 接入现有赛事系统（计分板/进球榜单），事件里保留 `ball_xy`、`cross_time_s` 便于人工复核。
