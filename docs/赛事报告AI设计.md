# 赛事报告 AI 设计（方案 A：轻量内嵌）

> 状态：设计定稿，待实施
> 日期：2026-11-04
> 分支：`xy`（= ysy 全部内容 + 直播/运动员端）
> 参考：GitHub `SaranGowrishVelisetty99/PitchIQ_AI` 的 story agent prompt 与输出 schema

---

## 一、目标与边界

### 1.1 目标

比赛进入 `finished` 状态后，一键生成本场的中文比赛报告：包含全文叙事、分章内容、关键节点、高光点与置信度，供赛事门户首页与运动员端展示。

### 1.2 输入 / 输出

| 项 | 内容 |
|---|---|
| 输入 | 一场 `status=finished` 的 `live.Match`，及其 `match_events`、双方 `lineup_home/lineup_away`、裁判、比分（字段与数量见 6.1.1） |
| 输出 | 结构化报告：`narrative` / `chapters[]` / `key_moments[]` / `highlights[]` / `data_notes[]` / `confidence` |
| 触发 | 管理员或省级手动触发（`POST`），支持 `force` 重新生成 |
| 展示 | 公众端免登可读 |

### 1.3 不做的部分

首版**不**引入 PitchIQ 的 formation / momentum / VAR 三个 agent，也**不**引入 RAG 向量库与 CrewAI。

依赖仅 `openai` SDK（OpenAI 兼容协议直连阿里云 DashScope），体积小、无额外服务。

### 1.4 与 PitchIQ 的关系

只吸收其 **story agent 的 prompt 思路与 JSON 输出 schema**，不 fork 仓库、不部署 FastAPI 微服务。原因：

- PitchIQ 是 Next.js + FastAPI 架构，无法嵌进 Django；
- 其依赖（crewai / chromadb / sentence-transformers）约 500MB，而本项目只需要一次文本生成；
- 该仓库无 License 声明，不直接复用代码。

---

## 二、新增文件清单

```
apps/report/
├── __init__.py
├── apps.py
├── models.py                   # MatchReport
├── migrations/0001_initial.py
├── llm.py                      # Qwen3 客户端 + JSON 容错 + mock 模式
├── prompts.py                  # SYSTEM / USER 模板 + PROMPT_VERSION
├── services.py                 # build_payload / generate_report / 状态机
├── serializers.py              # 输出结构校验 + 响应序列化
├── views_public.py             # 公众端读取
├── views_admin.py              # 管理端生成 / 读取
├── urls_public.py
└── urls_admin.py
```

### 2.1 需要改动的既有文件

| 文件 | 改动 |
|---|---|
| `config/settings.py` | `INSTALLED_APPS` 追加 `'apps.report'`；读取 `REPORT_LLM_*` 环境变量 |
| `config/urls.py` | 追加两条 include：`/api/public/live/`、`/api/admin/live/` 的 report 子路由 |
| `apps/common/codes.py` | 新增 `AI_SERVICE_FAILED = 50009` 及中文消息 |
| `.env.example` | 新增 `REPORT_LLM_*` 配置项说明 |
| `scripts/smoke_test.py` | 新增 6 项断言 |

**重要约束**：`apps/report` 是全新 app，迁移从 `0001` 开始，**不会与 `accounts/0004` 的既有编号冲突**；本设计不修改 `accounts` 模型，也不新增 `accounts` 迁移。

---

## 三、数据模型 `MatchReport`

对应 `apps/report/models.py`，`db_table = 'match_reports'`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BigAuto | 主键 |
| `match` | OneToOne → `live.Match`，`on_delete=CASCADE` | 一赛一报告 |
| `status` | Char(16)，`pending/generating/succeeded/failed`，默认 `pending` | 状态机 |
| `narrative` | Text，默认空 | 报告全文 |
| `chapters` | JSON，默认 `[]` | `[{title, content, minute_range, emotion}]` |
| `key_moments` | JSON，默认 `[]` | `[{minute, type, description, impact}]` |
| `highlights` | JSON，默认 `[]` | `[str]` |
| `data_notes` | JSON，默认 `[]` | 数据不足处的显式说明，防编造 |
| `confidence` | PositiveSmallInt，null | 0–100，模型自评 |
| `fingerprint` | Char(64) | 输入指纹，用于幂等 |
| `prompt_version` | Char(32) | 如 `v1` |
| `model_name` | Char(64) | 如 `qwen3-max` |
| `input_tokens` | Int，null | 成本追踪 |
| `output_tokens` | Int，null | 成本追踪 |
| `duration_ms` | Int，null | 生成耗时 |
| `error_msg` | Char(500)，null | 失败原因（脱敏） |
| `generated_by` | BigInt，null | 操作人 user id |
| `generated_at` | DateTime，null | 最近一次成功时间 |
| `created_at` / `updated_at` | DateTime | 审计 |

索引：`(match_id)`、`(status)`。

### 3.1 状态机

```
(无记录) --POST--> generating --成功--> succeeded
                        └────失败────> failed
succeeded --force/指纹变化--> generating --> ...
```

规则：

- 写入 `generating` 在同一事务内完成，先落库再做外部调用；
- 失败时 `status=failed` + `error_msg`，**不清空上一次成功的正文**，只覆盖状态；
- `succeeded` 保留 `generated_at` 供幂等判断。

### 3.2 输入指纹

```
fingerprint = sha256( match_id | home_score | away_score
                    | [ (event.id, event.type, event.minute, event.updated_at) ... ]
                    | json(lineup_home) | json(lineup_away) )
```

用途：同一场比赛事件未变时，重复 `POST` 直接返回旧报告，不重复计费。

---

## 四、LLM 接入

### 4.1 环境变量

```ini
REPORT_ENABLED=1
REPORT_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
REPORT_LLM_API_KEY=sk-xxxxxxxx
REPORT_LLM_MODEL=qwen3.8-max
REPORT_LLM_TIMEOUT=60
REPORT_LLM_MAX_TOKENS=2000
REPORT_LLM_DISABLE_THINKING=1
REPORT_LLM_MOCK=0
REPORT_LLM_TEMPERATURE=0.3
```

说明：

- `REPORT_LLM_MOCK=1` 时返回固定 JSON，用于本地联调与冒烟，不消耗额度；
- `REPORT_LLM_BASE_URL` 指向 DashScope 的 OpenAI 兼容端点，因此 `ChatGPT` 风格的 SDK 可直接用；
- 模型名只改这一个变量即可切换；
- 调用带 `response_format={"type":"json_object"}`（DashScope 不支持 `json_schema`），并默认 `extra_body={"enable_thinking": false}`。

### 4.1.1 实测结论（2026-11-04，qwen3.8-max）

| 项 | 结论 |
|---|---|
| 可用模型 | `qwen3.8-max`、`qwen3.7-max`、`qwen3.8-flash`、`qwen-plus`、`qwen-max`；`qwen3-max` 在该账号返回 `404 Model not exist` |
| 思考模式 | 开启时同一场报告 338s / 输出 3688 token（reasoning 占绝大多数）；关闭后 16–18s / 输出 ~700 token |
| 输出质量 | 事件、比分、球员、裁判均无误；缺阵容/技术统计时会写入 `data_notes` 并把 `confidence` 降到 45–75 |
| 稳健性 | SDK 层 `max_retries=0`，重试由本模块循环控制（否则单次失败会叠加成数分钟） |

### 4.2 客户端接口 `apps/report/llm.py`

```python
def chat_json(system: str, user: str) -> tuple[dict, dict]:
    """调用 Qwen3 并解析 JSON。

    返回 (payload, usage)，usage = {input_tokens, output_tokens, model, duration_ms}
    失败抛 BusinessException(ResponseCode.AI_SERVICE_FAILED)
    """
```

### 4.3 三项必做容错

1. **不使用 `response_format`**：Qwen3 兼容模式对 `json_object` 支持不稳定，改由 prompt 强约束输出 JSON。
2. **解析兜底链**：`json.loads` → 正则抠 ```json``` 代码块 → 失败则重试 1 次（`temperature` 降至 0.2）。
3. **异常收敛**：超时 / 429 / 5xx / 鉴权失败统一转 `50009`；`error_msg` 只记录错误类型与状态码，**不落原始响应体**，避免泄漏 key 与端点。

### 4.4 新增响应码

| 码 | 名称 | 消息 |
|---|---|---|
| `50009` | `AI_SERVICE_FAILED` | AI 报告生成服务异常，请稍后重试 |

---

## 五、Prompt 设计

### 5.1 SYSTEM

```
你是校园足球赛事的中文比赛报告撰写者，面向赛事门户的普通球迷读者。
只依据用户提供的比赛数据写作，禁止编造未给出的比分、球员、时间、战术或裁判信息。
若数据不足（例如缺少某队事件、缺少阵容），在 data_notes 中如实说明。
输出必须是单个 JSON 对象，不要 markdown 代码块，不要额外解释文字。
文风：客观、有画面感、每章 100–200 字。
```

### 5.2 USER 模板

```
【比赛】{组别} | 主队 {A} {home_score} - {away_score} 客队 {B} | {时间} | 场地：{venue}
【裁判】主裁：{chief}；边裁：{assistant}；第四官员：{fourth}
【事件时间线】
{minute}' {type_label} {team} {player_no}号 {player_name}（助攻/换上：{related}）{content}
...
【主队阵容】{no} {name}({position}{,队长})
【客队阵容】...
【派生统计】
进球：主 {n} / 客 {n}；黄牌：主 {n} / 客 {n}；红牌：…；换人：…
进球时段分布：0-15 {n}，16-30 {n}，31-45 {n}，46-60 {n}，61-75 {n}，76-90 {n}
比分变化：12' 1-0 → 34' 1-1 → 78' 2-1
先进球方：主队；是否出现红牌：否
【输出要求】
返回 JSON：{ "narrative": str, "chapters": [...], "key_moments": [...],
              "highlights": [...], "data_notes": [...], "confidence": 0-100 }
```

### 5.3 输出 schema（与表字段一一对应）

```json
{
  "narrative": "string",
  "chapters": [{"title": "string", "content": "string", "minute_range": "string", "emotion": "string"}],
  "key_moments": [{"minute": 12, "type": "goal", "description": "string", "impact": "string"}],
  "highlights": ["string"],
  "data_notes": ["string"],
  "confidence": 0
}
```

`PROMPT_VERSION = 'v1'`，写入 `MatchReport.prompt_version`，便于后续对比不同模板效果。

---

## 六、数据组装 `services.build_payload(match)`

### 6.1 原始取数

- `match`：组别、队名、比分、时间、场地、状态
- `match.referees`：主裁 / 边裁 / 第四官员
- `match.events`：按 `minute, id` 排序（模型内部已定义 `ordering = ['minute', 'id']`）
- `lineup_home` / `lineup_away`：`[{name, no, position, is_captain}]`

### 6.1.1 输入清单：类型、数量与实测用量

报告只吃四类数据：比赛本体 1 条、事件 N 条、裁判 0–4 条、双方阵容（可选）。进球数、时段分布、比分变化等数值全部由服务层算好后随 payload 传入，模型不做算术。

**表 1：字段级清单**

| 来源 | 字段 | 类型 | 必填 | 数量 | 进 Prompt |
|---|---|---|---|---|---|
| `live.Match`（1 条，且 `status=finished`） | `group_type`（含中文 label） | char(32) | 是 | 1 | 是 |
| | `home_team_name` / `away_team_name` | char(100) | 是 | 2 | 是 |
| | `home_score` / `away_score` | smallint | 是（可为 0） | 2 | 是 |
| | `match_time` | datetime → `Y-m-d H:i:s` | 是 | 1 | 是 |
| | `venue` | char(200) | 否 | 1 | 是（空则渲染为「未提供」） |
| | `lineup_home` / `lineup_away` | JSON：`[{name, no, position, is_captain}]` | 否 | 0–N 人/队 | 是 |
| `match_events` | `type` | enum：`goal/yellow/red/substitution/text` | 是 | ≥1，典型 5–30 | 是 |
| | `minute` | smallint | 是 | 每行 1 | 是 |
| | `team_side` | `home` / `away` / `null` | 进球、牌、换人必填；`text` 为 null | — | 是 |
| | `player_name` / `player_no` | char(100) / char(16) | 否 | — | 是 |
| | `related_player_name` | char(100) | 否（助攻 / 换上球员） | — | 是 |
| | `content` | char(500) | `text` 类型必填 | — | 是 |
| `match_referees` | `role` + `name` | enum + char(50) | 否 | 0–4（主裁≤1、边裁≤2、第四官员≤1） | 是 |

**表 2：数据量与实测用量**（qwen3.8-max、关闭思考模式、prompt v2）

| 场景 | 事件数 | 阵容 | 裁判 | 输入 token | 输出 token | 耗时 | confidence |
|---|---|---|---|---|---|---|---|
| 最小可用 | 2（1 进球 + 1 文字） | 1 / 1 | 2 | 630 | 615 | 13.0 s | 45 |
| 典型完整 | 7（3 进球 + 黄牌 + 红牌 + 换人 + 文字） | 4 / 4 | 3 | 767 | 885 | 17.7 s | 75 |

- 输入规模可用上表反推：`输入 token ≈ 550 + 23×事件数 + 10×阵容人数`；
- 输出受 `REPORT_LLM_MAX_TOKENS=2000` 约束，单场稳定在 600–900 token；
- 同一场重复点击不消耗额度（指纹幂等），只有 `force=true` 才重新计费。

**表 3：数据完整度分档**

| 档位 | 条件 | 结果 |
|---|---|---|
| 最小可用 | `finished` + ≥1 条事件 | 可以出报告，但 `confidence` 约 40–50，`data_notes` 会写明缺阵容 / 缺技术统计 |
| 推荐 | ≥5 条事件、双方各 ≥7 人阵容、裁判齐全、且比分与事件一致 | 文案质量最好（实测 `confidence` 75 以上） |
| 不可生成 | 事件数为 0 | 返回 `40001` |
| 不可生成 | 比赛未到 `finished` | 返回 `40002` |

**不纳入模型的字段**：`home_team_logo` / `away_team_logo` / `live_link` / `match_files` / `face_verify_records` / `version` / `athlete_id` 关联——与叙事无关或涉及图片文件，首版不传入。

### 6.2 服务层先算派生统计（不交给模型算数）

| 派生项 | 说明 |
|---|---|
| 各队进球 / 黄牌 / 红牌 / 换人数 | 事件按 `team_side` + `type` 聚合 |
| 进球时段分布 | `0-15 / 16-30 / 31-45 / 46-60 / 61-75 / 76-90` |
| 比分变化序列 | `[(minute, home, away)]`，用于“扳平 / 反超”叙事 |
| 先进球方 | 首个 `goal` 事件的 `team_side` |
| 是否出现红牌 | 影响叙事基调 |
| 数据完整度 | 事件总数、双方事件是否失衡、阵容是否缺失、是否只有 `text` 事件 |

### 6.3 前置校验

| 条件 | 结果 |
|---|---|
| `match.status != finished` | `40002` 比赛已结束才可生成（文案按码表） |
| 事件数为 0 | `40001` 无可分析事件 |

---

## 七、生成流程

```
POST /api/admin/live/matches/{id}/report
  └─ view (role_permission('admin','province'))
       └─ services.generate_report(match_id, user, force=False)

1. 取 match（不存在 → 30001）
2. 前置校验（40002 / 40001）
3. 计算 fingerprint
4. 若已 succeeded 且指纹一致且非 force → 直接返回旧报告（幂等，零调用）
5. 【事务内】写 status=generating、generated_by
6. 【事务外】build_payload → prompts → llm.chat_json
7. 校验输出结构（缺 narrative / chapters 视为失败）
8. 【事务内】写 status=succeeded + 全部内容 + sparkline token / duration
9. 异常 → status=failed + error_msg（保留旧正文）
```

### 7.1 事务约束

项目规范：**事务内禁止第三方 IO**。因此 LLM 调用严格位于事务外，事务只负责状态落库，避免长事务锁表（SQLite 尤甚）。

### 7.2 同步 vs 异步

当前 `QUEUE=sync`、无 Celery。首版采用**同步生成**：

- 单场耗时约 5–15 秒（Qwen3，输入 1.5–3k tokens、输出约 1k）；
- 前端按钮 loading 即可；
- 预留 `?async=1`：接口立即返回 `status=generating`，后台线程生成，前端轮询 `GET`。**首版不实现**，避免 SQLite 并发写问题。

---

## 八、接口契约

### 8.1 路由表

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/public/live/matches/{id}/report` | 免登 | 未生成也返回 200，用 `status` 区分 |
| `POST` | `/api/admin/live/matches/{id}/report` | `admin` / `province` | body `{"force": false}` |
| `GET` | `/api/admin/live/matches/{id}/report` | `admin` / `province` | 含 tokens、耗时、error_msg |

### 8.2 公众端响应（已生成）

```json
{
  "code": 0,
  "msg": "success",
  "success": true,
  "trace_id": "a1b2c3",
  "data": {
    "status": "succeeded",
    "match": {
      "id": 3,
      "group_type": "high_school_male",
      "group_type_label": "高中男子组",
      "home_team_name": "A中学",
      "away_team_name": "B中学",
      "home_score": 2,
      "away_score": 1,
      "match_time": "2026-11-04 15:30:00"
    },
    "report": {
      "narrative": "……",
      "chapters": [{"title": "上半场：僵局与首开纪录", "content": "……", "minute_range": "1-45", "emotion": "紧张"}],
      "key_moments": [{"minute": 12, "type": "goal", "description": "……", "impact": "打破僵局"}],
      "highlights": ["……"],
      "confidence": 82
    },
    "generated_at": "2026-11-04 17:02:11"
  }
}
```

### 8.3 未生成 / 生成中 / 失败

```json
{"status": "pending",  "match": {...}, "report": null, "generated_at": null}
{"status": "generating","match": {...}, "report": null, "generated_at": null}
{"status": "failed",   "match": {...}, "report": null, "generated_at": null}
```

公众端一律 `code=0`，前端按 `status` 渲染「暂无报告 / 生成中」占位，不处理 404。

### 8.4 管理端额外字段

`model_name`、`input_tokens`、`output_tokens`、`duration_ms`、`prompt_version`、`error_msg`、`generated_by`。

---

## 九、权限与数据范围

- 生成与查看管理端报告的权限：`role_permission('admin', 'province')`，与「比赛由省级或管理员管理」的既定决策一致。
- `city` / `school` / 个人角色：仅可读公众端，不可触发生成。
- 报告内容为公开信息（球员姓名、球衣号本就对公众展示），无需脱敏；`report` 模块不处理身份证号、学籍等敏感字段（那些在 `admin_console` 的脱敏层）。

---

## 十、验收标准（冒烟新增 6 项）

| # | 用例 | 期望 |
|---|---|---|
| 1 | finished 比赛生成 | `status=succeeded`，字段齐全，`confidence ∈ [0,100]` |
| 2 | 未结束比赛 | `40002` |
| 3 | 无事件比赛 | `40001` |
| 4 | 重复 POST（指纹未变） | 秒回同一 `generated_at` |
| 5 | `force=true` | `generated_at` 变化，`prompt_version` 不变 |
| 6 | 错误 key / `REPORT_ENABLED=0` | `50009` 且 `status=failed`；公众端仍 200 |

附加回归：`python manage.py test apps.admin_console` 167 项保持全绿；`smoke_test.py` 既有 63 项不退化。

---

## 十一、成本与风险

| 项 | 评估 |
|---|---|
| 单场 token | 输入 1.5–3k、输出约 1k，合计 3–4k |
| 主要风险 | 模型输出结构不稳 → 重试 1 次 + 结构校验 + 失败留痕，不污染已成功报告 |
| 次要风险 | 幻觉编造比分 → prompt 硬约束 + `data_notes` 显式声明数据缺口 + `confidence` 低分可见 |
| 运维 | `REPORT_ENABLED` 可一键关闭，失败自动降级为「暂无报告」，不影响直播主链路 |
| 安全 | key 只走环境变量；错误日志不落上游响应体 |

---

## 十二、实施顺序

| 步骤 | 内容 | 验收方式 |
|---|---|---|
| S1 | 建 `apps/report`、模型、`0001` 迁移、注册 app 与路由、`50009` | `migrate` 通过；`GET` 返回 `pending` |
| S2 | `llm.py` + `prompts.py` + `REPORT_LLM_MOCK=1` | 链路跑通，零额度消耗 |
| S3 | `build_payload` + 派生统计 | 单测覆盖比分变化、时段分布、数据完整度 |
| S4 | 生成接口 + 状态机 + 幂等 + 失败路径 | 冒烟 1–6 全绿 |
| S5 | 接真实 Qwen3 联调（`REPORT_LLM_MOCK=0`） | 人工评审 1 场实际文案质量 |

S1–S4 可在无 API key 的情况下完成（mock 模式）；S5 需要 `DASHSCOPE_API_KEY`。

当前状态：

- S1–S4 已完成，mock 冒烟 74/74；
- S5 已完成：`qwen3.8-max` 真实调用跑通，冒烟 74/74 全绿（含真实生成 2 次），单场约 17s、约 1.5k token；
- 待办：把 `.env` 中的 key 换成正式账号 key（当前 `.env` 已被 `.gitignore` 忽略，不入库）。

---

## 附录 A：与 PitchIQ story agent 的对照

| PitchIQ | 本设计 | 说明 |
|---|---|---|
| `role/goal/backstory/system_prompt` | `prompts.SYSTEM` | 角色设定移植，补反幻觉与中文约束 |
| `expected_output`（JSON 字段清单） | `chapters/key_moments/highlights/confidence/data_notes` | 字段对齐，新增 `data_notes` 防编造 |
| `CrewAI Agent + Task` | 单次 `chat_json` 调用 | 去掉 Agent 框架，减少依赖 |
| `Chroma RAG`（FIFA 法条 / 战术库） | 未采用 | 首版无需法条引用；后续 VAR 功能再引入 |
| `SSE 逐 token 推送` | 同步返回 | 报告体量小，无需流式 |
| `registry + 多 agent 编排` | 单 agent | 首版只做比赛报告 |

## 附录 B：后续可扩展方向

1. `injury/suspension` 联动：结合 `admin_console.PlayerSuspension`，报告可标注停赛影响。
2. VAR 解释：引入 PitchIQ 的 `var` agent + FIFA 法条 RAG，做判罚解释。
3. 异步队列：接入 Celery/RQ 后改为后台生成 + 轮询/SSE。
4. 报告分享图：由 `narrative + key_moments` 生成图文卡片（前端渲染，无需后端图片合成）。
