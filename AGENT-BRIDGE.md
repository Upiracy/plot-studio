# Agent 会话桥协议

让剧情框架页面成为 agent 界面的一部分。开启会话模式后，页面里的对话框不再直连模型，
而是与本 agent 会话互通：用户说的话进队列，agent 的回复、改动提案与界面指令实时回推。

所有通道都是普通 HTTP，端口写在工具目录的 `.port` 文件里（默认 8760）。
不用手写请求也行——用 `agent.py`：

```
python agent.py status                    # 会话状态
python agent.py state                     # 页面当前状态（选中了什么、在哪条轴、在哪个视图）
python agent.py poll --wait 60            # 等用户说话，最多等 60 秒
python agent.py poll --wait 60 --loop     # 持续等，每条消息输出一行 NDJSON
python agent.py say "已经补好三个后续事件"
python agent.py patch ops.json            # 提交一批改动
python agent.py cmd center --args '{"id":"ev_1"}'
```

## 让页面进入会话模式

打开 `http://127.0.0.1:<port>/?agent=1`（或点页面右下「会话与模型」→ 接入外部 Agent 会话）。
该选择会记在浏览器本地，之后直接打开也是会话模式；`?agent=0` 可强制回到直连模型。

## 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/session/send` | 页面 → agent：`{text, context}`，`context` 是用户提问时的选中状态 |
| GET | `/api/session/inbox?since=N&wait=S` | agent 取消息，`wait` 为长轮询秒数（上限 120）；`from=all` 可取双向 |
| POST | `/api/session/say` | agent → 页面：`{text, patch, commands}`，三者可任选或同时 |
| POST | `/api/session/command` | agent → 页面：`{cmd, args}`，单个界面指令 |
| GET | `/api/session/state` | 页面最近一次上报的状态 |
| GET | `/api/session/status` | `{seq, pageConnected, agentListening, lastReportAge, state}` |
| GET | `/api/session/log?since=N` | 全部会话记录（页面重连时用来恢复对话） |
| POST | `/api/session/reset` | 清空会话 |

`pageConnected` 反映页面是否还开着（页面每 8 秒上报一次心跳）。
`agentListening` 反映最近 90 秒内是否有 agent 取过消息——为 false 时页面会在对话框顶部提示"当前没有 agent 在监听"，
用户就知道消息只是排队、不会有人应答。`inbox` / `say` / `command` 被调用时都会刷新这个时间戳。
消息 id 单调递增，用它做游标即可，无需去重。

## 消息结构

```jsonc
{
  "id": 12,
  "from": "user" | "agent",
  "text": "用户或 agent 的话",
  "patch": { "summary": "一句话说明", "ops": [ /* 见下 */ ] },   // 可空
  "commands": [ { "cmd": "center", "args": { "id": "ev_1" } } ],  // 可空
  "context": { /* 仅 user 消息带：提问时的选中状态 */ },
  "ts": 1790000000000
}
```

## 改动 ops

与页面内直连模型的 `propose_patch` 完全同构，落盘策略也共用同一套
（默认：改文本自动写入，增删节点/改依赖需用户在卡片上点确认，见 ADR-0005）。

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
{ "op": "link.create", "from": "ev_6", "to": "ev_3", "type": "influence", "label": "…", "note": "…" }
{ "op": "link.create", "from": "ev_1", "to": "ev_5", "type": "foreshadow", "state": "open", "label": "九号泊位的钩子" }
{ "op": "link.update", "id": "lk_1", "patch": { "state": "closed", "note": "已在第三章回收" } }
{ "op": "link.delete", "id": "lk_1" }
{ "op": "character.create", "data": { "name": "…", "brief": "…" } }
{ "op": "character.update", "id": "ch_lin", "patch": { "brief": "…" } }
{ "op": "place.create", "data": { "name": "…", "brief": "…" } }
{ "op": "place.update", "id": "pl_pier", "patch": { "brief": "…" } }
{ "op": "axis.update", "id": "ax_main", "patch": { "brief": "…" } }
{ "op": "chapter.update", "id": "ch_a1", "patch": { "title": "…", "brief": "…" } }
{ "op": "world.update", "patch": { "brief": "…" } }
```

`link.type` ∈ `influence | echo | foreshadow | parallel | note`。
`foreshadow` 必须带 `state`：`open`（已埋设、尚未回收）或 `closed`（已回收）；
未回收的伏笔会在画布上高亮、在事件卡片上挂标记，并出现在 `list_graph` 返回的 `openForeshadows` 里，
这是"检查并补埋伏笔"这类任务的入口。

`requirements[].type` ∈ `choice | flag | item | stat | custom`，表示"除了完成源事件之外还需要的条件"。
门槛是给人和 AI 读的约束说明，工具不会自动推导它。

`step.type` ∈ `main | narr | battle | explore | check | move | reward | custom`。

## 界面指令

`cmd` 可取：`select`（`{type, id, center?}`）、`center`（`{id}`）、`focusEntity`（`{kind: "character"|"place", id}`）、
`view`（`{mode: "timeline"|"character"|"place"}`）、`scene`（`{id}` 打开剧情细节编辑器）、
`fit`、`reload`（重读工程文件）、`undo`、`redo`、`toast`（`{text, kind}`）。

## 一个回合的样子

```
$ python agent.py poll --wait 120
{"id":7,"from":"user","text":"帮我把第二章的冲突补出来","context":{"selection":{"type":"chapter","id":"ch_a2"},
 "chapter":{"title":"第二章 · 灯塔不亮","axis":"主线：雾的来处","start":320,"end":700}, ...}}

$ python agent.py state          # 需要更全面的数据时再拉一次
$ python agent.py patch ops.json --text "补了两个事件，并给旧事件加了伏笔引用线"
{"ok":true,"id":8}
```

写完工程文件后不需要额外动作：页面自己在监听文件变更，会自动刷新。

## 数据模型要点

- 事件的 `t` 是逻辑时间（水平方向，越大越晚），`y` 是自由纵向位置。
- 事件**不属于任何轴**，它同时出现在所有轴上；"属于哪一章"由 `t` 与各轴章节区间求交算出，
  不要试图给事件写 `chapterId`。
- 轴是铺满画面高度的半透明色带，各自持有 `chapters`（同轴首尾相接、不留空隙）。
- 依赖表示"目标在源完成后解锁"；分支 = 依赖 + 源事件上某个选项 id 填进 `viaChoiceId`。
- 引用线表示非强依赖的叙事关联（做过某事件会让此事件走向不同），不要滥用依赖来表达它。
- 详细结构见 `工程/*.json`，术语表见 `GLOSSARY.md`。
