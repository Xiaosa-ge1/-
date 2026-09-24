# 云笔迹 Agent 化优化方案（实施终版）

> 本文是「诊断 → 措施 → 实现 → 验证」的完整汇总，可作为交付与复盘依据。
> 配套文档：`agent-product-plan.md`（产品方案）、`agent-refactor-tdd-plan.md`（TDD 实施切片）、
> `eval-corpus-generation.md`（评测语料生成）。
> 方法：TDD 红→绿垂直切片，每个 seam 一个测试、一个最小实现；每阶段跑全量测试。
> 代码风格：遵循原项目（4 空格、双引号、中文 docstring、`app.core.logger_handler.logger`）。

---

## 一、结论速览

把「带 RAG 的聊天机器人」升级为「真正的知识库 Agent」。三缺的达成情况：

| 缺口 | 优化前 | 优化后 | 状态 |
|---|---|---|---|
| **① 检索自主性与可纠正性** | 路由层强制前置检索，Agent 无二次检索能力，首次召回失败=整轮失败 | 检索下沉为工具，Agent 自主判断/换词重试；Prompt 明确重试策略 | ✅ 已达成 |
| **② 溯源透明** | 回答无来源，用户无法验证 | 五段链路全打通（工具→提取→SSE→持久化→渲染） | ✅ 已达成 |
| **③ 知识统一与闭环** | 笔记/知识库两条割裂链路，聊完即丢 | 双源统一检索；对话可一键沉淀为笔记 | ✅ 已达成（统一检索 + 沉淀闭环） |
| **L4 Agent 能力** | 工具被动、无主动发现缺口 | 自主检索已具备；主动发现知识缺口仍未做 | ⚠️ 部分 |

**最大产品缺陷已修复**：系统内部把「笔记」和「知识库文档」当两个东西，用户心里只有一个「我的知识」。

**验证结果**：`72 passed` / `ruff check` 全绿 / `tsc -b` 零错误 / `npm run build` 通过。

---

## 二、诊断：为什么改

### 2.1 定位与现状

不是「能调工具的聊天框」，而是 **替用户打理知识资产的助手**。

| Level | 形态 | 改造前 |
|---|---|---|
| L1 | 聊天问答 | ✅ 远超 |
| L2 | 带 RAG 的问答 | ✅ 检索强，但溯源没做透 |
| L3 | 知识管理 | ✅ 部分，工具被动 |
| **L4** | **知识 Agent** | ❌ 目标 |

### 2.2 三缺

**缺①：检索的自主性与可纠正性。**
改造前是「拼接式」：路由层先跑 RAG 管线 → 取 top-3 → 塞进 system prompt → Agent 基于这段硬答。
后果：Agent 没有二次检索能力，第一次召回失败就是整轮失败，而且**无法换词重试**。

**缺②：溯源透明。**
检索结果只进了模型上下文，用户看不到答案出自哪篇笔记。知识库产品最核心的信任机制是缺失的。

**缺③：知识统一与闭环。**
系统暴露两条割裂链路（笔记检索工具 vs 前置 RAG 管线），结果可能还不一致；对话产生的新知识无处沉淀。

### 2.3 被低估的病灶：框架半迁移

`agent_middleware.py` 用的是 **LangGraph** 的 `AgentState/Runtime` 签名，而实际跑的是
**langchain_classic 的 AgentExecutor**——两者不通，`get_middleware()` 返回的 6 个钩子**全是死代码**。
这是「别把两套框架迁移到一半就上线」的典型：写了可观测性代码，但从未生效。

---

## 三、优化措施与实施结果

### 3.1 结构改造：RAG 从「前置管线」变成「手里的工具」

| 项 | 内容 |
|---|---|
| 措施 | 删除 `chat.py` 中前置 RAG 强制注入，检索下沉为 Agent 工具 |
| 实现 | `app/router/chat.py`（192 行 → 106 行，只做转发） |
| 理由 | **只要还强制塞 context，Agent 就永远学不会自主检索** |
| 效果 | `用户提问 → Agent 自主判断要不要查 → 调用检索工具 → 不够就换关键词重查 → 需要细节再取全文 → 带引用作答` |

### 3.2 工具集重构

改造前 8 个 → 现在 8 个，但**成分完全变了**：

| 工具 | 处理 | 理由 |
|---|---|---|
| `get_user_info_tools` | **删除** | 死工具：模型手里没有 JWT，永远调不通，且诱导模型解析 token |
| `search_notes_tool` | **删除** | 只搜笔记；与统一检索并存会让模型选错 |
| `search_knowledge_tool` | **新增** | 笔记 + 知识库统一检索（补缺①③） |
| `get_document_detail_tool` | **新增** | 按 source_id 取全文，两级检索第二级（补缺①） |
| `what_time_is_now` / `get_note_stats_tool` / `get_today_reviews_tool` / `mark_reviewed_tool` / `create_note_tool` / `get_related_notes_tool` | 保留 | — |

当前工具集（8 个，已由测试锁定）：
```
what_time_is_now / search_knowledge_tool / get_document_detail_tool / get_note_stats_tool
get_today_reviews_tool / mark_reviewed_tool / create_note_tool / get_related_notes_tool
```

> **两级检索**（「先便宜缩小范围，再昂贵提升精度」的落地）：
> 一级 `search_knowledge_tool` 返回 snippet（便宜、快、覆盖广）；
> 二级 `get_document_detail_tool` 取全文（贵、慢，只在确实需要时用）。

### 3.3 框架迁移：中间件从死代码到真实生效

| 项 | 迁移前 | 迁移后 |
|---|---|---|
| 运行时 | `langchain_classic.AgentExecutor` | `create_agent`（LangGraph 图） |
| 中间件 | 6 个钩子全是死代码 | 7 个真实生效 |
| 迭代控制 | `max_iterations=8` | `ToolCallLimitMiddleware(run_limit=10)` |
| 流式 | 伪流式（生成完再切 15 字符 + sleep） | **真流式**（token 产出即推送） |

附带清理的死代码：`create_agent_executor` / `get_agent_executor` / `_create_prompt`、
`set_thinking_callback` / `get_thinking_callback_from_context` / `thinking_callback_var`
（思考通道**从未被填充**，那个 `thinking_queue` 一直在空转）。

副作用：测试耗时 17s → 10s（不再加载 classic 依赖）。

### 3.4 溯源链路（缺②，五段全打通）

| 段 | 实现 |
|---|---|
| 1 工具层 | 返回文本内嵌 `source_id`（`format_for_model`） |
| 2 提取层 | `extract_sources_from_messages()` —— **只认 `ToolMessage`** |
| 3 传输层 | SSE `sources` 事件，在 `done` 之前推送 |
| 4 持久化 | 存 `ChatMessage.metadata_` 的 `sources` 键 |
| 5 渲染层 | 前端来源卡片（笔记/知识库图标区分）+ 历史回放（`SessionResponse.assistant_sources`） |

**只认工具消息是硬要求**：模型自己写在回答里的 `[1] 笔记《我编的》` 是幻觉，必须排除，
否则溯源会指向不存在的资料。有专门测试锁死这条。

### 3.5 沉淀闭环（缺③）

对话结束前推送 `suggestion` 事件，前端渲染「存为笔记」卡片，一键落库。

关键在**克制**，否则这功能就是骚扰：

| 条件 | 行为 |
|---|---|
| 本轮已调过 `create_note_tool` | 不提示（已经存过了） |
| 回答 < 120 字（寒暄） | 不提示 |
| 其余 | 提示 |

### 3.6 Prompt 契约

`app/prompt/main_prompt.txt` 由测试锁定的三条：
1. 关键结论后用 `[n]` 标注来源编号
2. 检索无结果时换关键词重试，**最多 2 次**
3. 两次仍无结果，如实告知，**不得编造**

---

## 四、实施中发现的关键真相

以下几条是**实测推翻假设**的，不是设计时的推测。记录下来避免后续踩回同一个坑。

### 4.1 `wrap_model_call` / `wrap_tool_call` 必须 async（否则线上全崩）

原中间件写的是**同步函数**。照搬迁移到 LangGraph 后，生产链路（`ainvoke`/`astream`）会直接抛：

```
NotImplementedError: Asynchronous implementation of awrap_model_call is not available
```

**这是异步上下文才暴露的问题**——只做静态迁移、不跑一次真实调用，是发现不了的。
已改为 `async def` + `await handler(request)`。反过来中间件改异步后，测试也必须用 `ainvoke`
（同步 `invoke` 会报反向错误），所以测试也一并调整。

### 4.2 来源回传不能用 ContextVar（LangGraph 子任务隔离）

原计划用 `ContextVar` 把来源从工具传给 SSE 层。**实测不可行**：

```
ContextVar after ainvoke: None
messages: ['HumanMessage', 'AIMessage', 'ToolMessage', 'AIMessage']
```

LangGraph 在**子任务**里执行工具，工具内的 `set()` 不会传播回外层；`asyncio.run` 内外同样。

好在同一行输出给了答案：`ToolMessage` 里带着工具返回的文本。改为**「渲染/解析 roundtrip」**：

```
[1] 笔记《MySQL 索引》 (source_id: note:n1)
索引失效常见于隐式类型转换
```

`format_for_model()` 与 `parse_sources_from_text()` 互为逆操作，用 **roundtrip 测试**锁死契约。
标题可能含《》（如「读《人类简史》有感」），所以正则用**贪婪匹配 + 尾部 `(source_id: ...)` 锚定**。

> 这个坑如果不实测，会写出「本地测试通过、线上拿不到来源」的代码。

### 4.3 笔记根本不切块（影响 source_id 设计）

`note_service` 是整篇 `add_documents(ids=[note_id])`，**只有知识库才切块**。
所以 `source_id` 设计成 `note:{note_id}` / `kb:{chroma_id}`——笔记没有 `chunk_index` 这一层。
硬套 `note_id#chunk_index`（产品方案初稿的写法）会是错的。

### 4.4 `source_type` 是检索后临时补的

笔记写入时存的是 `doc_type:"note"` + `note_id`；`source_type` 是 `rag_service` 检索后才补上的。
这意味着**直接查 vector_store 拿不到 `source_type`**，溯源代码必须自己兜底判断
（已处理：有 `note_id` 即判为笔记）。

### 4.5 双源分数必须各自归一化（失效模式 #1 的防御）

笔记的距离与知识库的 BM25 分数量纲不同，**直接比大小是错的**。
`merge_sources` 对两源**各自归一化**再合并——这是 reranker 降级时的兜底路径。

### 4.6 流式拿不到完整消息序列

`stream_mode="messages"` 只有逐 token 的分片，提取不了来源。改用 `["messages","values"]`：
messages 逐 token 推，values 提供累积状态用于提取来源。

---

## 五、验证证据

### 5.1 测试（72 passed）

| 测试文件 | 用例 | 覆盖 |
|---|---|---|
| `test_agent_middleware.py` | 4 | 中间件真实触发（从死代码变为生效） |
| `test_agent_response.py` | 3 | 非流式契约 |
| `test_agent_stream.py` | 9 | SSE 契约 + 来源事件 + 历史持久化 |
| `test_agent_tools_registry.py` | 4 | 工具集锁定（防误删/误留） |
| `test_knowledge_search.py` | 23 | 检索/合并/溯源/建议的纯函数 |
| `test_knowledge_tool.py` | 7 | 检索工具核心路径与边界 |
| `test_prompt_contract.py` | 5 | Prompt 契约 |
| `test_source_formatter.py` | 3 | source_id 构造 |
| `test_source_merge.py` | 7 | 双源归一化合并 |
| `test_input_sanitizer.py` | 7 | （既有） |

**测试策略**：全部使用假模型（`GenericFakeChatModel` 子类补 `bind_tools`），
**不依赖真实 LLM 与数据库**，全量跑完约 11 秒。这是能做 TDD 红绿循环的前提。

### 5.2 复现命令

```bash
# 后端测试
cd backend && uv run --extra dev pytest tests/ -q

# 代码风格（已全绿）
cd backend && uv run --extra dev ruff check app/ tests/

# 前端类型检查 + 构建
cd front && npx tsc -b && npm run build
```

### 5.3 交付物清单

| 类型 | 文件 |
|---|---|
| 新增模块 | `backend/app/rag/source_formatter.py`（source_id 构造 / 双源合并 / 分数归一化） |
| 新增模块 | `backend/app/rag/knowledge_search.py`（统一检索 / 两级检索 / 溯源提取 / 沉淀建议） |
| 新增测试 | `backend/tests/` 下 9 个文件，65 条新用例 |
| 文档 | `agent-optimization-report.md`（本文）、`agent-refactor-tdd-plan.md`、`agent-product-plan.md`、`eval-corpus-generation.md` |
| Git | `9e87204` 初始 → `c2072da` Agent 化改造 → `e968fd4` 溯源+沉淀（均已推送 `origin/main`） |

> 注：source_formatter / knowledge_search 中的**纯函数**（合并、归一化、渲染解析、建议构造、
> 来源提取）全部可独立测试，是本次改造能被 TDD 覆盖的关键设计。

---

## 六、已知局限与未完成项

诚实列出，避免下一轮基于错误前提工作。

| # | 项 | 说明 |
|---|---|---|
| 1 | **`[n]` 精确映射不到具体来源** | 多次检索时每次工具返回的编号都从 1 开始，回答里的 `[1]` 无法精确对应某一次检索。当前展示去重后的来源列表（参考资料），不做逐条跳转。要精确跳转需工具层维护**会话级计数**，但受 ContextVar 限制（跨不过子任务），得改用消息内计数——成本较高。 |
| 2 | **`update_note` 未实现** | 产品方案 3.2 中列出的「改写已有笔记」工具**未做**，缺③的闭环目前只覆盖「新建」不覆盖「更新」。 |
| 3 | **P6 评测未做** | 评测脚本按用户要求**不重建**。三层评测体系（工具调用正确性 / 溯源准确性 / 端到端验收）与 golden set 均未建立，因此**当前所有结论都是「测试通过」，不是「效果达标」**。 |
| 4 | **主动发现知识缺口未做** | L4 的「主动发现空白与矛盾」（JTBD #5「补」）完全没有实现。 |
| 5 | **预热机制未做** | 产品方案 3.5 的可选折中（Top-1 作为预热提示）未实现，因此删掉前置注入后**首轮延迟增加**（Agent 要多一轮工具调用）。是否可接受需真实使用后判断。 |

### 一条方法论提醒

本项目改造全程守住了两条纪律，后续应继续：

1. **讨论与验证不在同一段对话里**——讨论阶段只讨论，验证先落成文件。
2. **动手前先写失效模式**——尤其是「输入长得像真实输入吗」。
   本次的失效模式 #1（双源分数量纲不可比）在动手前就被识别并加了测试，
   而 4.1 / 4.2 两个坑是**实测暴露**的——说明「想清楚」不能替代「跑一次」。

---

## 七、下一步建议（按优先级）

| 优先级 | 动作 | 理由 |
|---|---|---|
| 高 | 建 golden set 跑 Layer 1 评测（工具调用正确性） | 当前只有单元测试，「效果」是空白。**尤其是「检索失败型」与「干扰型」两类样本**——分别对应「会不会幻觉」和「会不会过度调用」 |
| 中 | 实现 `update_note` | 补完缺③闭环的另一半 |
| 中 | 精确引用映射（消息内计数方案） | 把「参考资料列表」升级为「逐条可跳转」 |
| 低 | 预热机制 | 视真实使用中首轮延迟的体感决定 |
| 低 | 主动知识缺口发现 | L4 的最后一块，产品价值高但实现成本也高 |
