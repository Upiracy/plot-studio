# 剧情框架 · Plot Studio

一个用于**可视化设计、存档与 AI 辅助编辑**游戏剧情的本地工具。

它以「工程 (Project)」为单位，把世界观、角色、地点、章节与事件组织在一张可自由编排的二维平面上，
既能在时间线上推演因果，也能在角色 / 地点视图里聚焦某条线索，还能让 AI 在其上续写、补全关联与埋伏笔。

- 单文件前端：`index.html`，零依赖，纯原生 JS + SVG，无需构建
- 本地启动器：`server.py`，只用 Python 标准库（Python 3.8+）
- 数据即文件：每个工程是一个 `.json`，是唯一数据来源，可版本管理、可外部编辑
- 实时刷新：工程文件被外部改动后，页面自动重载

## 特性

**自由画布与多轴时间**

- 事件是画布上的节点，水平方向为逻辑时间，竖直方向自由摆放
- 支持多条**轴**（如「主线」「某角色的成长」「某个事件自身的时间逻辑」），每条轴是一层铺满画面的半透明色带，可分别开关、分别编写设定
- 轴上的**切割线**把时间切成**章节**（同轴首尾相接、不留空隙），拖动切割线即调整章节范围
- 事件不归属于任何轴，它同时出现在所有轴上；「属于哪一章」由逻辑时间落在哪个区间派生而来

**因果结构**

- **依赖**（实线箭头）：目标事件在源事件完成后才解锁，构成剧情的骨架
- **分支** = 依赖 + 源事件上某个选项（`viaChoiceId`），靠选项区分走哪一条
- **引用线**（虚线）：非强依赖的叙事关联，例如「做过某事件会让此事件走向不同」
- **伏笔**：引用线的一种，带 `open` / `closed` 状态；未回收的伏笔会高亮并出现在 AI 可读的 `openForeshadows` 里
- **门槛 (requirements)**：`choice | flag | item | stat | custom`，给人和 AI 读的约束说明

**投影视图**

- 角色视图 / 地点视图：同一份数据的过滤 + 聚焦呈现，不持有独立副本

**剧情细节**

- 每个事件可展开为有序的**步骤**流：`main | narr | battle | explore | check | move | reward | custom`

**编辑体验**

- 拖动 / 连线 / 框选 / 批量操作，撤销重做，关键词与类型筛选
- 世界观、角色、地点、轴的设定文本均可直接编辑

**AI 辅助**

- 直连任意 OpenAI 兼容接口，或接入外部 Agent 会话（见下）
- AI 通过工具读取世界 / 角色 / 地点 / 事件 / 图结构，再以 `propose_patch` 提交改动
- **分级写入**：改文本自动落盘，增删节点 / 改依赖需用户点确认

## 快速开始

```bash
# 1. 启动本地服务（会写入 .port 并自动打开浏览器）
python server.py

# Windows 上也可直接双击
启动.bat
```

浏览器打开 `http://127.0.0.1:8760/`（端口被占用时会自动顺延）。
首次使用点「示例」载入示例工程，或「导入 / 导出」管理自己的 `.json`。

> 为什么必须跑本地服务？`file://` 页面的 origin 是 opaque origin，浏览器会拒绝文件系统访问，
> 且 `Origin: null` 的跨域请求会被 CORS 拦截。详见 [ADR-0002](docs/adr/0002-local-launcher-file-as-truth.md)。

## 配置 AI

**方式一：直连模型** —— 页面右下「会话与模型」，填入 `baseURL` / `model` / `apiKey`。
配置存在 `config.json`（已被 `.gitignore` 忽略，API Key 不会进入版本库），也可先复制一份 `config.example.json`。

**方式二：接入外部 Agent 会话** —— 让本页面成为某个 agent 的界面：

```
python agent.py status                # 会话状态
python agent.py poll --wait 60        # 等用户说话（长轮询）
python agent.py say "已补好三个后续事件"
python agent.py patch ops.json        # 提交一批改动
python agent.py cmd center --args '{"id":"ev_1"}'
```

打开 `http://127.0.0.1:8760/?agent=1` 进入会话模式，页面里的对话框就不再直连模型，而是与该 agent 互通。
完整协议见 [AGENT-BRIDGE.md](AGENT-BRIDGE.md)。

## 目录结构

```
剧情框架/
├── index.html            前端全部内容（结构 / 样式 / 逻辑）
├── server.py             本地启动器：静态托管、JSON 读写、变更推送、AI 代理、会话桥
├── agent.py              Agent 侧命令行（给外部智能体用）
├── 启动.bat              Windows 一键启动
├── config.example.json   配置样例
├── 工程/                 工程文件目录，一个工程一个 .json
│   └── 默认工程.json      示例工程「雾港之夜」
├── docs/adr/             架构决策记录
├── GLOSSARY.md           术语表（数据模型词汇）
├── AGENT-BRIDGE.md       Agent 会话桥协议
└── .trae/skills/         配套 skill（给 AI agent 的操作手册）
```

## 数据模型要点

- 事件用 `t` 表示逻辑时间（水平位置），`y` 表示自由纵向位置
- 事件**不存储** `chapterId`，章节归属由 `t` 与各轴章节区间求交得出
- 轴持有自己的 `chapters`，同轴首尾相接
- 依赖表示「目标在源完成后解锁」；分支 = 依赖 + `viaChoiceId`
- 引用线表示非强依赖的叙事关联，不要用依赖去表达它

完整词汇见 [GLOSSARY.md](GLOSSARY.md)，字段样例见 [AGENT-BRIDGE.md](AGENT-BRIDGE.md)。
配套 skill 见 [.trae/skills/plot-studio/SKILL.md](.trae/skills/plot-studio/SKILL.md)。

## 设计决策

| ADR | 决策 |
|---|---|
| [0001](docs/adr/0001-single-html-zero-dependency.md) | 单 HTML 文件、零依赖 |
| [0002](docs/adr/0002-local-launcher-file-as-truth.md) | 本地启动器，文件即数据源 |
| [0003](docs/adr/0003-dependency-edges-derived-questlines.md) | 用依赖边表达因果，任务线是派生概念 |
| [0004](docs/adr/0004-axis-layers-derived-chapter-membership.md) | 轴为图层，章节归属为派生 |
| [0005](docs/adr/0005-ai-tiered-write-access.md) | AI 分级写入权限 |
| [0006](docs/adr/0006-chapters-tile-the-axis.md) | 章节平铺整条轴 |

## 开发

没有构建步骤，也没有第三方依赖。改 `index.html` 直接刷新页面即可；改 `server.py` / `agent.py` 后重启进程。

## 许可证

[MIT](LICENSE)
