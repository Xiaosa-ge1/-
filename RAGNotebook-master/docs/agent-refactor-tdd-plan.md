# 云笔迹 Agent 化重构：TDD 实施计划

> 前置文档：`agent-product-plan.md`（产品方案）、`eval-corpus-generation.md`（语料生成）
> 方法：红 → 绿 垂直切片，一个 seam、一个测试、一个最小实现
> 代码风格：遵循原项目（4 空格、双引号、中文 docstring、`app.core.logger_handler.logger`）

---

## 零、实施进度

| 阶段 | 状态 | 产出 |
|---|---|---|
| **P0** 清理死代码 | ✅ 完成 | 删除 `get_user_info_tools`，工具集 8→7 |
| **P1** 工具返回值结构化 | ✅ 完成 | `app/rag/source_formatter.py` |
| **P2** 双源合并 + 归一化 | ✅ 完成 | `merge_sources` / `normalize_distances` |
| **P3** Prompt 契约 | ✅ 完成 | `[1]` 引用标注、换词重试、禁止编造 |
| **P0.5** 中间件迁移 LangGraph | ✅ 完成 | `create_agent` 替代 `AgentExecutor`，中间件生效（死代码→7 个生效） |
| **P2 收尾** 删除前置强制注入 | ✅ 完成 | 检索下沉为 Agent 工具，路由层只转发 |
| **P3+** `get_document_detail` | ✅ 完成 | 两级检索第二级 |
| **P4** 溯源链路打通 | ✅ 完成 | sources 事件 + 持久化 + 前端来源卡片 + 历史回放 |
| **P5** 沉淀闭环 | ✅ 完成 | suggestion 事件 + 一键存为笔记 |
| **P6** 评测 | ⏳ 未做 | 评测脚本按用户要求**不重建**；语料生成见 `eval-corpus-generation.md` |
| **收尾** 前端构建修复 | ✅ 完成 | `NoteList.tsx` 4 处既有类型错误（阻断 `npm run build`） |

测试：`72 passed`。每个阶段均先红后绿。
构建：`npm run build` 通过（1.93s）；`tsc -b` 零错误。

### 收尾　前端构建修复（✅ 已完成）

`NoteList.tsx` 有 4 处**既有**类型错误，导致 `npm run build` 失败（与 Agent 化改造无关）：

- 第 55 行 `allValues` 声明后未使用 → 删除
- 第 82 行 `useRef<ReturnType<typeof setTimeout>>()` 缺少初始值（React 19 起类型要求必填）
  → 改为 `useRef<ReturnType<typeof setTimeout> | undefined>(undefined)`
- 第 197 / 207 行向该 ref 赋 `undefined`，随上面类型放宽而消解

### P5　沉淀闭环（✅ 已完成）

对话结束前推送 `{"type":"suggestion","action":"create_note","title":...,"content_preview":...}`，
前端渲染「存为笔记」卡片，点击调 `notesApi.create` 落库。

**克制的触发条件（避免变成骚扰）**：

| 条件 | 行为 |
|---|---|
| 本轮已调用过 `create_note_tool` | 不提示（已经沉淀过了） |
| 回答长度 < 120 字（寒暄、短问答） | 不提示 |
| 其余 | 提示 |

**新增测试**：`test_knowledge_search.py` +6（含 `has_created_note` 检测）、`test_agent_stream.py` +2。
代码风格：改动文件 `ruff check` 全绿；前端 `tsc -b` 改动文件零错误。

### P4　溯源链路打通（✅ 已完成）

**四段链路全部打通**

| 段 | 实现 |
|---|---|
| 1. 工具层 | 返回文本内嵌 `source_id` |
| 2. 提取层 | `extract_sources_from_messages()` —— **只认 ToolMessage**，模型自己编的 `[1]` 不算来源；按 source_id 去重 |
| 3. 传输层 | `build_sources_event()` → SSE `{"type":"sources","items":[...]}`，在 done 之前推送 |
| 4. 持久化 | 存入 `ChatMessage.metadata_` 的 `sources` 键 —— **复用已有 JSON 字段，无需改表/migration** |
| 5. 渲染层 | 前端 `useSSE` 新增 `onSources`；AIChat 在回答下方渲染来源卡片（笔记/知识库图标区分） |
| 6. 历史回放 | `SessionResponse` 新增 `assistant_sources`，与 history 中助手回复一一对应，刷新页面来源不丢 |

**关键设计：只认工具消息**

模型自己写在回答里的 `[1] 笔记《我编的》 (source_id: note:fake)` 必须排除，
否则溯源会指向不存在的资料。已有测试锁死这条。

**流式改造**：`stream_mode="messages"` 拿不到完整消息序列，改为
`stream_mode=["messages", "values"]` —— messages 逐 token 推送，values 提供累积状态用于提取来源。

**已知局限**：多次检索时，每次工具返回的编号都从 1 开始，因此回答里的 `[1]`
无法精确映射到某一次检索。当前做法是展示去重后的来源列表（参考资料），不做逐条精确跳转。
若要精确跳转，需让工具层维护会话级计数（受 ContextVar 限制，需改用消息内计数）。

**新增测试**：`test_knowledge_search.py` +6、`test_agent_stream.py` +4。

---

## 一、测试基建（已就绪）

| 项 | 状态 |
|---|---|
| pytest | 8.3.4，位于 `dev` optional-deps |
| 运行命令 | `uv run --extra dev pytest tests/ -q` |
| 现有测试 | `tests/test_input_sanitizer.py`（7 passed） |
| 风格 | 函数式、无 class、`test_<行为>` 命名 |
| `import app.agent.agent` | ✅ 可用（顶层会实例化工厂，8 个工具） |

> Windows Git Bash 下需先 `export PATH="/usr/bin:/bin:/mingw64/bin:$PATH"`，否则 `ls`/`dirname` 找不到。

---

## 二、Seams（测试边界）——需确认后开工

TDD 纪律：只在已确认的 seam 上写测试，不测内部实现。

| Seam | 公共接口 | 测什么 | 外部依赖 |
|---|---|---|---|
| **S1** 工具注册表 | `AgentFactory._get_default_tools()` | 工具集合、schema 合法性、死工具已移除 | 无 |
| **S2** 双源合并 | `merge_sources()` 纯函数 | 笔记+知识库合并排序、去重、`source_type` 标记 | 无（纯函数） |
| **S3** 分数归一化 | `normalize_scores()` 纯函数 | 向量距离与 BM25 分数可比的归一结果 | 无（纯函数） |
| **S4** 溯源格式化 | `build_sources_event()` 纯函数 | chunk → SSE `sources` 事件映射 | 无（纯函数） |
| **S5** 工具返回结构 | `search_knowledge()` 返回值 | 可解析 JSON、含 `source_id`/`title`/`snippet` | mock 检索服务 |
| **S6** Prompt 契约 | `main_prompt.txt` | 含引用标注要求、含重试策略 | 无（文件断言） |
| **S7** 会话持久化 | `session_manager.add_message(..., sources=)` | sources 存取不丢 | DB（mock） |

**策略：把改造尽量做成纯函数 seam（S2/S3/S4/S6），避免测试依赖 LLM 与数据库。**
红绿循环才能快且稳定。

---

## 三、失效模式（动手前先写清楚）

| # | 失效场景 | 后果 | 防御 |
|---|---|---|---|
| 1 | 双源分数不可比（向量距离 vs BM25 原始分） | 合并排序无意义 | 合并前必须归一化（S3） |
| 2 | `source_id` 不稳定，笔记更新后 chunk 重切 | 溯源指向错误/失效 | 用 `note_id#chunk_index`，不用内容 hash |
| 3 | 模型不按格式引用 | 前端来源卡片缺失或报错 | Prompt 约束 + 解析失败静默降级 |
| 4 | 测试库太简单，全部命中 | 指标虚高，测的是库不是系统 | 按 `eval-corpus` 自检，全命中则作废 |
| 5 | `agent.py` 顶层实例化有副作用 | 测试互相污染 | 工具注册表用 staticmethod，不依赖全局态 |

---

## 四、P0–P6 的 TDD 切片

### P0　清理死代码（地基）

| 循环 | Red（先写失败测试） | Green（最小实现） |
|---|---|---|
| P0.1 | `test_tool_registry.py`：工具集**不含** `get_user_info_tools` | 从 `_get_default_tools` 与 import 中删除 |
| P0.2 | 断言必需工具全部注册 | 无需改动（应已通过） |

> `get_user_info_tools` 是死工具：模型手里没有 JWT，永远调不通，且诱导模型解析 token。

### P0.5　中间件迁移 LangGraph（✅ 已完成）

**结论：迁移而非删除。** 中间件函数签名本来就是 LangGraph 的，只需换运行时。

实施内容：

| 项 | 迁移前 | 迁移后 |
|---|---|---|
| 运行时 | `langchain_classic.AgentExecutor` | `langchain.agents.create_agent`（LangGraph 图） |
| 中间件 | 6 个钩子全是死代码 | 真实生效（已验证日志触发） |
| 迭代控制 | `max_iterations=8` | `ToolCallLimitMiddleware(run_limit=10)` |
| 流式 | 伪流式（全文生成后按 15 字符切片） | 真流式（token 产出即推送） |
| 输入格式 | `{input, chat_history, system_prompt}` | `{messages: [...]}` |
| 工具步骤 | `intermediate_steps` | 从消息序列提取 `tool_calls` / `ToolMessage` |

**⚠️ 迁移中最关键的坑（不修则线上全崩）：**

`wrap_model_call` / `wrap_tool_call` 原来写的是**同步函数**。在异步上下文
（`ainvoke` / `astream`，也就是本项目的生产链路）下会抛：

```
NotImplementedError: Asynchronous implementation of awrap_model_call is not available
```

必须改成 `async def` + `return await handler(request)`。
反过来，中间件改异步后，测试也必须用 `ainvoke`（同步 `invoke` 会报反向错误）。

**清理的死代码：**
- `AgentFactory.create_agent_executor` / `get_agent_executor`（无调用方）
- `_create_prompt` + `ChatPromptTemplate` / `MessagesPlaceholder` import
- `agent_tools.set_thinking_callback` / `get_thinking_callback_from_context` / `thinking_callback_var`
  （思考通道从未被填充，改真流式后彻底无用）

**新增测试**：`test_agent_middleware.py`（4）、`test_agent_response.py`（3）、`test_agent_stream.py`（3）。
均用假模型（`GenericFakeChatModel` 子类实现 `bind_tools`），不依赖真实 LLM 与数据库。

### P2/P3+　统一检索与两级检索（✅ 已完成）

**工具集变化（8 → 8，但语义重构）**

| 工具 | 变化 |
|---|---|
| `search_notes_tool` | **删除**——仅搜笔记，与统一检索并存会让模型选错 |
| `search_knowledge_tool` | **新增**——同时检索笔记 + 知识库，返回带 `[n]` 编号与 `source_id` 的文本 |
| `get_document_detail_tool` | **新增**——按 `source_id` 取全文（两级检索第二级） |

**路由层前置注入已删除**

`chat.py` 不再跑 RAG 管线再塞 system prompt，只做转发。
理由：只要还强制注入 context，Agent 就永远学不会自主检索。

**⚠️ 关键发现：ContextVar 跨不过 LangGraph 子任务边界**

原计划用 `ContextVar` 把结构化来源从工具传给 SSE 层，**实测不可行**：

```python
result = await agent.ainvoke(...)
print(captured.get())   # → None
```

LangGraph 在子任务中执行工具，工具内的 `set()` 不会传播回外层。
`asyncio.run` 内外同样如此。

**解决方案：渲染/解析 roundtrip**

工具返回的文本内嵌 `source_id`，后端用正则解析回来：

```
[1] 笔记《MySQL 索引》 (source_id: note:n1)
索引失效常见于隐式类型转换
```

- `format_for_model(items) -> str`：渲染
- `parse_sources_from_text(text) -> list[dict]`：解析（与上者互为逆操作）

标题可能含《》（如「读《人类简史》有感」），因此正则用**贪婪匹配 + 尾部 `(source_id: ...)` 锚定**，
并用 roundtrip 测试锁死两个函数的契约。

**新增测试**：`test_knowledge_search.py`（11，全纯函数）、`test_knowledge_tool.py`（7）。

### P1　工具返回值结构化

| 循环 | Red | Green |
|---|---|---|
| P1.1 | 工具返回可解析为 JSON，含 `source_id` | 新增 `format_search_result()` |
| P1.2 | `source_id` 可反查原文档 | ID 规则 `note_id#chunk_index` |

### P2　双源统一检索

| 循环 | Red | Green |
|---|---|---|
| P2.1 | `merge_sources` 合并后按分数降序 | 实现合并 |
| P2.2 | 两源分数归一化后可比 | 实现 `normalize_scores` |
| P2.3 | `source_type` 正确标记 note/kb | 已有字段，断言即可 |

### P3　两级检索 + 重试策略

| 循环 | Red | Green |
|---|---|---|
| P3.1 | 新增 `get_document_detail` 工具已注册 | 实现工具 |
| P3.2 | Prompt 含"换关键词重试，最多 2 次" | 改 `main_prompt.txt` |
| P3.3 | `max_iterations >= 8` | 改 `create_agent_executor` |

### P4　溯源链路

| 循环 | Red | Green |
|---|---|---|
| P4.1 | `build_sources_event` 产出合法 SSE 事件 | 纯函数实现 |
| P4.2 | `chat_message` 可持久化 sources | 加字段 |
| P4.3 | 前端 `useSSE` 处理 `sources` 事件 | 加 case |

### P5　沉淀闭环

| 循环 | Red | Green |
|---|---|---|
| P5.1 | `suggestion` 事件结构合法 | 纯函数实现 |

### P6　评测

按 `eval-corpus-generation.md` 造库与 golden set，跑三层评测。
**脚本是否重建、放哪个路径，待你确认。**

---

## 五、纪律

- 每个切片：先红后绿，不提前写未来的测试
- 重构不属于红绿循环，留到 review 阶段
- 每完成一个 P，全量跑一次 `pytest tests/ -q` 确保没有回归
