# KnowGraph

本地优先的个人知识管理与成长系统。它把「个人画像 → 定时采集 → AI 翻译/概括 → Markdown 落盘 → 标签检索/带引用问答 → 周期回顾」串成一个闭环，并明确区分**用户声明的目标**与**系统推断的近期兴趣**。

完整设计见 [DESIGN.md](DESIGN.md)。

## 当前实现范围

- **本地 Web UI**：画像、收件箱、近一周资讯、检索、问答、回顾、兴趣、设置。
- **个人画像**：简历文本/PDF/TXT/MD 上传并本地保存、技能与熟练度、短期/长期目标。简历提取优先读取技能章节，通过当前模型结构化抽取技术名、类别、等级和原文证据，排除软技能与管理职责，逐项等待确认。
- **资讯采集**：RSS/Atom、手动网页、GitHub 仓库、arXiv；按画像、来源主题/关键词和近期兴趣排序。文章会尝试抓取正文，随后由当前模型翻译标题、生成中文概括和标签，写入 Vault。
- **资讯页面**：按平台筛选最近 7 天获取的内容，列表只显示中文概括；点击查看本地全文，可直接打开原文或 Obsidian 文档。
- **GitHub 榜单**：今日/本周/本月新增 Star、总 Star、Fork、最新优质项目；支持全部、C++、Qt、Python、Go、AI/LLM、AI Agent、机器学习、数据库、网络编程分类。每个仓库显示 AI 中文核心功能说明。
- **Markdown/Obsidian**：稳定 ID、原子写入、人工编辑保护、冲突修订稿、外部编辑重新索引；资料卡片按标签链接到已有相关笔记。
- **检索**：规范标签与别名、SQLite FTS5、中文/符号回退匹配。
- **问答**：本地证据优先；引用由服务端验证文件与片段后生成可点击链接；无资料时明确说明。问答历史持久化，可创建分组、按分组筛选、重新归组和删除。
- **外部模型**：支持 Anthropic Messages 与 OpenAI Chat Completions；内置 Anthropic、DeepSeek、Kimi、智谱 GLM、千问、小米 MiMo、OpenAI、Micu、CCTQ 和自定义平台。可保存多个服务、独立密钥、切换当前服务并测试连接。
- **近期兴趣**：只来自主动检索/问答与显式保存/不相关反馈；14 天半衰期，可固定、屏蔽、删除、清空。
- **回顾**：按本地时区生成周/月 Markdown；同一周期重跑不覆盖人工编辑；只有学习记录支持“已进展”的判断。
- **调度**：持久化任务表，重启后继续；采集和回顾任务幂等。
- **离线降级**：没有可用模型时使用本地抽取式问答；资讯保留原文并标记“待整理”，不编造翻译或链接。

尚未实现（设计中的后续阶段）：学习路径、编程练习、项目任务、面试题、间隔复习、独立可视化工作台。当前知识关联由标签、卡片中的相关笔记链接和 Obsidian 双链承载，不依赖图数据库。

## 快速开始

需要 Python 3.11+。

```bash
python -m pip install -e .
```

```bash
python -m knowgraph init
```

```bash
python -m knowgraph serve --port 8000
```

打开 <http://127.0.0.1:8000>。默认数据目录是 `./data`，Vault 是 `./vault`。

首次使用建议：

1. 在「画像」填写基本信息、技术栈、短期与长期目标，粘贴/上传简历并确认技能建议。
2. 在「设置 → 模型服务」添加并测试当前模型服务。
3. 在「设置 → 资讯来源」查看或添加来源。当前工作区数据库已经配置了下方列出的 25 个来源；全新的 `data/` 不会自动获得这些来源，需要按列表添加。
4. 等待定时采集，或在「收件箱」点击「立即采集」。资料会以 Markdown 卡片写入 `vault/00-Inbox/`。
5. 在「检索」输入技术栈或术语；在「问答」提问并点击引用打开本地文档。
6. 在「回顾」记录一次真实学习事项，再生成上一期周回顾。
7. 在「兴趣」查看系统从检索/问答中推断出的短期主题，并随时固定、屏蔽或删除。

## 配置

环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `KNOWGRAPH_HOME` | `./data` | SQLite、简历原件、密钥、日志等私有数据 |
| `KNOWGRAPH_VAULT` | `./vault` | Obsidian Vault |
| `KNOWGRAPH_TIMEZONE` | `Asia/Shanghai` | 采集与周/月回顾使用的时区 |
| `KNOWGRAPH_MODEL` | `claude-opus-5-5` | 首次创建默认 Anthropic 服务时使用的模型；之后在设置页按服务编辑 |
| `KNOWGRAPH_LLM_ENABLED` | `1` | 设为 `0` 强制只使用本地抽取 |
| `KNOWGRAPH_COLLECTION_LIMIT` | `12` | 全局采集上限；来源可单独配置 `limit` |
| `GITHUB_TOKEN` | 未设置 | 可选，提高 GitHub Search API 速率限制 |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `OPENAI_API_KEY` | 未设置 | 模型服务的全局后备凭据 |

模型凭据优先使用设置页保存的每个服务密钥，也支持上述环境变量。服务密钥写入 `data/secrets/providers/`，POSIX 下文件权限为 0600，Windows 依赖数据目录 ACL；不写入 Vault 或 SQLite。可选安装系统密钥链支持：

```bash
python -m pip install -e ".[keyring]"
```

可以把 `.env.example` 复制为 `.env`；程序会从当前工作目录读取，但不会覆盖已经存在的环境变量。

## 模型服务

预设参考 [CC-Switch](https://github.com/farion1231/cc-switch) 的公开 provider 配置。

| 服务 | 协议 | 默认 Base URL | 默认模型 |
|---|---|---|---|
| Anthropic 官方 | Anthropic Messages | `https://api.anthropic.com` | `claude-opus-5-5` |
| DeepSeek | Anthropic Messages | `https://api.deepseek.com/anthropic` | `deepseek-v4-pro[1M]` |
| Kimi | Anthropic Messages | `https://api.moonshot.cn/anthropic` | `kimi-k2.7-code` |
| 智谱 GLM | Anthropic Messages | `https://open.bigmodel.cn/api/anthropic` | `glm-5.3[1M]` |
| 千问 / 通义 | Anthropic Messages | `https://dashscope.aliyuncs.com/apps/anthropic` | `qwen3.8-max[1M]` |
| 小米 MiMo | Anthropic Messages | `https://api.xiaomimimo.com/anthropic` | `mimo-v2.6-pro[1M]` |
| OpenAI 官方 | OpenAI Chat Completions | `https://api.openai.com/v1` | 需按账号填写 |
| Micu | Anthropic Messages | `https://www.micuapi.ai` | 需按平台填写 |
| CCTQ | Anthropic Messages | `https://www.cctq.ai` | 需按平台填写 |
| 自定义平台 | Anthropic 或 OpenAI | 手动填写 | 手动填写 |

预设中的模型 ID、地址和可用性可能随服务商调整；官网/文档链接仅作参考，不参与请求，真正使用 Base URL、认证方式和模型 ID。OpenAI Chat Completions 服务需要模型支持该协议；本系统的摘要和问答依赖 JSON 文本输出，较弱模型可能无法稳定遵循。

切换“当前服务”后，后续资讯整理、问答和回顾使用该服务。每个服务有独立密钥、连接测试和编辑入口。DeepSeek 等推理模型在兼容端点会尝试关闭隐藏思考，避免输出预算全部消耗在推理上；不支持该字段的服务自动回退。

## 资讯来源

当前工作区数据库已配置 25 个来源：

- **综合平台**：Hacker News（hnrss）、InfoQ 中国、V2EX、掘金、少数派。
- **C++ 博客**：C++ Stories、Fluent C++、Andreas Fertig、Herb Sutter、Meeting C++、Modernes C++。
- **Qt**：KDAB、Planet Qt、Qt Blog。
- **AI/Agent**：Hugging Face Blog、Google AI Blog、LangChain Blog、arXiv cs.AI/cs.CL/cs.MA、OpenAI Agents SDK、LangGraph Releases、AutoGen Releases。
- **CAD/CAE**：FreeCAD Blog、SimScale Blog、OCCT Releases、FreeCAD Releases。

Linux 中国已停止运营且主域名不可用，未配置。ISO C++、Reddit r/cpp 和 OpenAI News 的 `robots.txt` 禁止订阅，未强行抓取；OpenAI/Agent 更新改由 GitHub Releases 覆盖。Andreas Fertig 在当前网络下需要绕过系统代理，来源配置已标记 `no_proxy`。平台类来源使用标题/摘要关键词过滤；专门博客使用来源主题，避免整站内容被无差别收录。

每个来源支持以下配置：

- `platform`：资讯页平台分组。
- `limit`：单次最多收录条数。
- `enrich_limit`：单次最多 AI 整理条数。
- `fetch_full_text`：是否尝试抓取文章正文。
- `topics`：专门来源的无条件主题。
- `keywords`：综合来源的标题/摘要关键词过滤。
- `no_proxy`：该来源及其正文抓取绕过系统代理。

采集流程：候选筛选 → 正文抓取（遵守 robots/限额）→ 当前模型翻译标题、生成中文概括和标签 → 写入 Markdown 与 SQLite。抓取失败或页面需要登录/JavaScript 时保留摘要/摘录，并在资讯详情标记“本地摘录”。

## 资讯页面

资讯页面按平台筛选最近 7 天获取的内容：

- 列表只显示 AI 中文概括、来源、时间和标签，不展示占用空间的全文。
- 点击“查看全文”显示本地保存的正文；本地只有摘录时明确提示。
- 可打开原始网页，或通过 `obsidian://` 链接直接在 Obsidian 打开对应 Markdown。
- 没有可用模型时，内容保留原文并标记“待整理”，仍可本地查看。

## GitHub 榜单

资讯页选择 GitHub 平台后显示独立榜单：

- 今日 / 本周 / 本月新增 Star。
- 总 Star 排名、Fork 排名。
- 最新优质项目：最近 30 天创建、Star 不少于 50，按 Star 排序。
- 分类：全部、C++、Qt、Python、Go、AI/LLM、AI Agent、机器学习、数据库、网络编程。

每个榜单会用一个批量模型请求为整页仓库生成中文核心功能说明（约 70 字），卡片直接显示中文说明并标注“AI 概括”；模型不可用时回退原始描述。榜单和概括缓存 10 分钟。

GitHub Search API 不提供“指定周期新增 Star”排序：

- 全部、C++、Python、Go 的新增 Star 榜单使用 GitHub Trending，是实际新增 Star。
- Qt、AI/LLM、Agent、机器学习、数据库、网络编程使用近期活跃 + 总 Star 的近似排序，界面会标注口径，不伪装成精确新增 Star。

配置 `GITHUB_TOKEN` 可提高 Search API 速率限制。

## 数据布局

```text
data/
  knowgraph.sqlite3     # 画像、来源、条目、事件、兴趣、问答历史、任务、索引
  resume/               # 简历原文，不进入 Vault
  secrets/providers/    # 各模型服务的独立密钥，不进入 Vault/SQLite
vault/
  00-Inbox/             # 自动采集、待处理卡片
  10-Sources/           # 标记保存的资料
  20-Notes/             # 用户知识笔记（可在 Obsidian 创建）
  30-Maps/
  40-Practice/
  50-Reviews/           # 自动生成、可人工编辑的回顾
  Attachments/
```

Vault 内的 Markdown 是资料卡片、笔记与回顾正文的权威内容。SQLite 保存可重建的全文/标签索引，以及不可从 Markdown 重建的行为、任务、目标状态和采集状态。删除或重建索引不会删除笔记。

人工编辑过的卡片不会被后台覆盖：再次生成时写入带 `supersedes` 的修订稿。后台也不会自动修改技能熟练度、短期或长期目标。

## 安全边界

- 服务默认只绑定 `127.0.0.1`；没有开放 CORS，跨源写请求会被拒绝。
- 网页、README、论文等外部内容只作为数据处理，不能改变系统指令；不执行下载内容。
- 抓取遵守 `robots.txt`、请求限额和站点许可，不绕过登录或付费墙。文章正文只在允许保存时落盘；失败时保留摘要/摘录。
- 简历、问答历史、画像和模型密钥默认只保存在本地。只有启用模型且配置凭据时，相关文本才可能发送给模型服务。
- 本机/内网 URL 默认禁止抓取，可在设置中显式放开。
- 模型密钥不会出现在 API 响应中，只返回末四位提示。

## 测试

```bash
python -m pytest
```

当前测试覆盖 Vault 原子写入与冲突修订、重复采集、相关性与探索配额、标签/符号/中文检索、引用校验、兴趣衰减与撤销、回顾周期与人工编辑保护、简历技能结构化抽取与分类、问答历史与分组、模型服务预设/CRUD/密钥、正文抓取与 AI 翻译概括、近一周资讯、GitHub 榜单与中文概括、API 与跨源写保护。

## 开发演示

使用隔离的 `.devdata` 和 `.devvault`，不会影响正式数据：

```bash
KNOWGRAPH_HOME=.devdata KNOWGRAPH_VAULT=.devvault python scripts/seed_demo.py
```

```bash
python scripts/dev.py
```

然后打开 <http://127.0.0.1:8766>。

## 目录

```text
knowgraph/
  api.py              HTTP 接口与静态 UI
  app.py              组合根
  adapters.py         RSS/Web/GitHub/arXiv 来源适配器与正文抓取
  ingest.py           采集、排序、去重、正文与卡片落盘
  github_rankings.py  GitHub Trending/Search 榜单与仓库概括
  search.py           标签与全文检索
  qa.py               有引用的问答
  history.py          问答历史与分组
  interests.py        短期兴趣与衰减
  reviews.py          周/月回顾
  scheduler.py        持久化本地调度
  vault.py            Markdown 写入与索引
  profile.py          画像、简历技术栈提取与目标
  providers.py        多模型服务配置
  credentials.py      密钥存储
  progress.py         学习记录
static/               本地 Web UI
tests/                自动化测试
scripts/              隔离数据的开发服务器与演示数据
```
