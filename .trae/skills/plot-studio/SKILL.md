---
name: "plot-studio"
description: "可视化游戏剧情框架 Plot Studio 的操作手册：启动本地服务、用 agent.py 会话桥读写工程、按 ops 规范提交改动。当用户要求设计/续写/检查/整理该框架里的剧情、埋伏笔、把零散灵感用进故事（合并/扩写）、生成事件或驱动其界面时使用。"
---

# Plot Studio · 剧情框架操作手册

本仓库/工作区里的「剧情框架」是一个可视化游戏剧情设计工具：单文件前端 `index.html` +
纯标准库启动器 `server.py` + agent 侧命令行 `agent.py`。数据以「工程」为单位存为单个 `.json`。

**你的角色**：用户会把这个页面当作你界面的一部分。你在页面外的终端里用 `agent.py` 收消息、
读状态、提交改动、驱动界面。不要直接手改 `.json`——那会绕过校验与撤销，且页面看不到你的意图。

## 何时使用

- 用户要求在剧情框架里**续写剧情、生成事件、补分支、埋伏笔、找伏笔**
- 用户要求**检查 / 整理**已有工程的因果结构（依赖、引用线、悬空伏笔）
- 用户要求**启动 / 打开**该工具，或让你**驱动它的界面**（聚焦某事件、切视图）

## 前置：确认服务在跑

页面必须由 `http://127.0.0.1:<port>` 托管，端口写在工具目录的 `.port` 文件里（默认 `8760`）。

```bash
python server.py            # 启动（会自动写 .port 并开浏览器）
python agent.py status      # 探活：看 pageConnected / agentListening
```

`pageConnected=false` 表示页面没开，用户看不到效果；`agentListening=false` 表示用户在页面里会看到
「当前没有 agent 在监听」的提示。**每次开工前先 `status`，向用户确认页面已打开。**

## 一条消息的往返

```bash
python agent.py poll --wait 120     # 长轮询等用户说话，返回一行 JSON（含 text 与 context）
python agent.py state               # 需要更全面的数据时，拉页面当前状态（选中了什么 / 在哪个视图）
python agent.py say "已补好三个后续事件"   # 回话
python agent.py patch ops.json --text "补了两个事件并加了伏笔引用线"   # 回话 + 提交改动
python agent.py cmd center --args '{"id":"ev_1"}'                    # 只驱动界面
```

- 输出一律是 JSON，可直接解析。消息 id 单调递增，用它当游标即可，无需去重。
- 用户消息里的 `context` 是他提问那一刻的选中状态（选中了哪个事件 / 章节 / 角色，在哪个视图）。
  **优先据此判断"就地"的对象**，而不是回头去猜。
- 落盘后不用额外动作：页面在监听文件变更，会自动刷新。

## 数据模型速查

- **事件 (event)**：画布节点。`t` 是逻辑时间（水平，越大越晚），`y` 是自由纵向位置。
- 事件**不属于任何轴**，它同时出现在所有轴上。**不要给事件写 `chapterId`**——章节归属由 `t` 与各轴
  章节区间求交派生而来。要知道某事件在哪一章，用 `list_timeline` 看轴与章节，自己算交集。
- **轴 (axis)**：一层铺满画面的半透明色带，有自己的 `chapters`（同轴首尾相接、不留空隙）、设定文本与可见性开关。
- **章节 (chapter)**：由轴上相邻切割线切出的区间，只框定范围、不承载内容，携带 `title` 与 `brief`。
- **依赖 (edge)**：`from → to` 表示"to 在 from 完成后才解锁"。这是因果骨架。
- **分支 (branch)**：一种依赖，把源事件上某个选项 id 填进 `viaChoiceId`。
- **引用线 (link)**：非强依赖的叙事关联（做过某事件会让此事件走向不同）。**不要用依赖表达它。**
- **伏笔 (foreshadow)**：引用线的一种，必须带 `state`：`open`（已埋未回收）/ `closed`（已回收）。
- **触发方式 (trigger)**：`passive`（事件自己找上主角）/ `active`（主角去了才发生）。
- **剧情 (scene)**：事件**内部**的有序步骤流，`step.type ∈ main | narr | battle | explore | check | move | reward | custom`。
- **灵感 (idea)**：尚未组织进故事的零散想法。它是**独立集合**（ADR-0007），不落在画布上、不占逻辑时间、
  不属于任何轴，**没有 `t` / `y`**，也不要给它写 `chapterId`。`kind ∈ meme(梗) | scene(桥段) | line(台词) | twist(转折) | lore(设定) | note(杂记)`，
  `status ∈ raw | used | parked | merged`。三条铁律：`text` 是原文**永远不要改写**（扩写进 `expansion`、
  变体进 `variants`）；采用是**引用而非搬运**（用 `idea.mark` 转状态，别删）；合并是**软合并**
  （`mergedFrom` / `mergedInto` 记录来源与去向，被合并的条目留在库里，可反悔拆开）。

术语原文与反例见仓库的 `GLOSSARY.md`，字段级样例见 `AGENT-BRIDGE.md`。

## 读取工具（直连模型时自带；你作为外部 agent 需要自己读文件或调页面状态）

| 工具 | 用途 |
|---|---|
| `list_entities(kind)` | 列出全部角色 / 地点 / 轴（含设定摘要） |
| `get_entity(id)` | 读单个角色 / 地点 / 轴 / 章节的完整设定文本 |
| `search_events(query)` | 按关键词搜事件（标题、摘要、详细设定、剧情步骤） |
| `get_event(id)` | 读单个事件的完整信息：属性、选项、步骤、依赖与引用线 |
| `list_timeline()` | 所有事件按逻辑时间排序的概览 + 每条轴的章节划分 |
| `list_graph()` | 全部依赖与引用线；含 `openForeshadows`（悬空伏笔清单） |
| `list_ideas(status?, kind?, query?)` | 列灵感库。`status` 传 `raw` 只看未采用的，是"找机会"的起点 |
| `get_idea(id)` | 读单条灵感：原文、扩写、变体、合并来源与去向、被用在哪里 |

作为外部 agent，最短路径是直接读 `工程/<名字>.json` 拿全量数据，再用 `agent.py state` 拿实时选中状态。

## 写入：只用 ops

改动以 `{ "summary": "一句话", "ops": [ ... ] }` 的形式提交给 `propose_patch`（或 `agent.py patch`）。
**这是唯一能修改工程的方式。**

```jsonc
{ "op": "event.create", "data": { "id": "ev_x", "title": "…", "t": 520, "y": 140,
    "kind": "main", "trigger": "passive", "summary": "…", "brief": "…",
    "placeIds": [], "characterIds": [], "choices": [ { "id": "op_a", "label": "…" } ],
    "scene": { "steps": [ { "id": "st_a", "type": "main", "fields": { "speaker": "…", "text": "…" } } ] } } }
{ "op": "event.update", "id": "ev_1", "patch": { "summary": "…", "brief": "…" } }
{ "op": "event.delete", "id": "ev_1" }
{ "op": "edge.create", "from": "ev_1", "to": "ev_2", "viaChoiceId": "op_a",
    "requirements": [ { "type": "item", "key": "锈蚀的钥匙", "value": "" } ], "note": "…" }
{ "op": "edge.update", "id": "ed_1", "patch": { "requirements": [ { "type": "flag", "key": "见过灯塔" } ], "note": "…" } }
{ "op": "edge.delete", "id": "ed_1" }
{ "op": "link.create", "from": "ev_1", "to": "ev_5", "type": "foreshadow", "state": "open", "label": "九号泊位的钩子" }
{ "op": "link.create", "from": "ev_6", "to": "ev_3", "type": "influence", "label": "…", "note": "…" }
{ "op": "link.update", "id": "lk_1", "patch": { "state": "closed", "note": "已在第三章回收" } }
{ "op": "link.delete", "id": "lk_1" }
{ "op": "character.create", "data": { "name": "…", "brief": "…" } }
{ "op": "character.update", "id": "ch_lin", "patch": { "brief": "…" } }
{ "op": "place.create", "data": { "name": "…", "brief": "…" } }
{ "op": "place.update", "id": "pl_pier", "patch": { "brief": "…" } }
{ "op": "axis.update", "id": "ax_main", "patch": { "brief": "…" } }
{ "op": "chapter.update", "id": "ch_a1", "patch": { "title": "…", "brief": "…" } }
{ "op": "world.update", "patch": { "brief": "…" } }
{ "op": "idea.create", "data": { "text": "雾里的钟声每十二分钟响一次", "title": "钟声的节奏", "kind": "meme" } }
{ "op": "idea.update", "id": "id_1", "patch": { "expansion": "扩写后的长文…", "variants": [ { "id": "vr_1", "text": "另一种写法", "label": "更冷" } ] } }
{ "op": "idea.update", "id": "id_1", "patch": { "status": "raw", "mergedInto": "" } }   // 从合并中拆开
{ "op": "idea.delete", "id": "id_1" }
{ "op": "idea.merge", "ids": ["id_1", "id_2"], "data": { "title": "合并后的标题（可选）" } }
{ "op": "idea.mark", "id": "id_1", "status": "used" }
```

- `link.type ∈ influence | echo | foreshadow | parallel | note`
- `requirements[].type ∈ choice | flag | item | stat | custom`
- 门槛是给人和 AI 读的约束说明，**工具不会自动推导它**，别指望它生效。

## 分级写入（重要）

默认策略：**改文本自动落盘；增删节点 / 改依赖需要用户在卡片上点确认**。
所以提交大批 `event.create` 后，页面会出现待确认卡片——**要主动 `say` 告诉用户去点确认**，
否则用户以为你没干活。

灵感遵循同一套，但边界值得记住：`idea.update` **只有写 `expansion` / `variants` 时才自动落盘**
（也就是"扩写"这条路是顺的）；改动原文、标题、状态、标签或合并关系，以及 `idea.create` /
`idea.merge` / `idea.mark` / `idea.delete`，都要用户确认。

## 界面指令

`agent.py cmd <名称> --args ...`，可选：`select`（`{type,id,center?}`）、`center`（`{id}`）、
`focusEntity`（`{kind:"character"|"place", id}`）、`view`（`{mode:"timeline"|"character"|"place"|"ideas"}`）、
`scene`（`{id}` 打开剧情细节编辑器）、`fit`、`reload`、`undo`、`redo`、`toast`（`{text,kind}`）。

`select` 的 `type` 传 `idea` 会自动切到灵感视图；`center` 对灵感无意义（灵感没有位置）。

改完之后用 `center` / `scene` 把用户带到你改的地方，比一句"改好了"有用得多。

## 常见任务配方

**续写支线**：`list_graph` 找到锚点事件 → `get_event` 读它的选项与后置 → 参考 `list_entities` 里的
角色 / 地点设定文本 → 提交 `event.create` + `edge.create`（分支记得填 `viaChoiceId`）→ `say` + 提示确认。

**检查并补埋伏笔**：`list_graph` 取 `openForeshadows` → 对每条 `get_event` 看它埋在哪、指向哪 →
要么补一个回收事件并 `link.update` 置 `closed`，要么明确告诉用户"这条伏笔目前没有回收计划"。

**按角色聚焦**：`list_entities('character')` 拿 id → 过滤事件的 `characterIds` → 提交改动后用
`cmd focusEntity --args '{"kind":"character","id":"ch_lin"}'` 把视图切过去。

**生成冲突**：先读世界观（`world`）与相关角色 `brief`，冲突必须落在已有设定允许的范围内，
不要引入世界观没写过的要素（例如设定里"没有魔法"就别写魔法）。

**把灵感用起来**（用户说"这些想法怎么用""帮我找机会"时）：先 `list_ideas` 通读灵感库
（未采用 `raw` 与搁置 `parked` 都要看），再对照 `list_timeline` / `list_graph` 与各章节 `brief`，
逐条给出**具体**的落点：放进哪个事件、哪一章，或朝什么方向扩写。两条纪律：
发现多条灵感其实讲的是同一件事时，**明确指出可以合并的组合**（`idea.merge`，或建议用户在灵感视图里
多选后手动合并）；确实用不上的直接说明理由，不要硬塞。

**扩写某条灵感**：先 `get_idea` 看它的 `mergedFrom` 与同标签的其他灵感——**能融合的一并融进去**，
并在回复里说明融合了哪几条、各自的取舍。产出只写 `expansion` 与 `variants`，`text` 一个字都不要动。

**落地一条灵感**：转成事件用 `event.create`（把原文写进 `brief` / `summary`），挂到已有事件则改该事件的
`scene.steps` 或 `brief`。两种做法都要顺手用 `idea.mark` 把灵感转成 `used` 并在 `usedBy` 里记下位置——
灵感不该因为被用了就消失。

## 陷阱

- **不要给事件写 `chapterId`**，归属是派生的。
- **不要改写灵感的 `text`**，那是原文；扩写进 `expansion`，变体进 `variants`。
- **不要用删除来表达"这条灵感用过了"**，用 `idea.mark`；也不要为了合并而删掉来源条目。
- **不要用依赖表达"影响"**，那是引用线。
- 事件 `t` 是逻辑时间，不是日期；不同轴的 `t` 不可比较。
- 不要把 AI Key 写进工程 `.json`；配置在 `config.json`（已被 `.gitignore` 忽略）。
- PowerShell 下 `--args` 的 JSON 双引号会被吞，可用 `--args 'id=ev_1,center=true'` 或 `--args '@args.json'`。
