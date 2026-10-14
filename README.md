# DB-GPT 通用问数增强版（下游发行版）

> **如果你（人或 AI）第一次读这个仓库，读完本 README 就能建立完整认知。**
> 这是 [eosphoros-ai/DB-GPT](https://github.com/eosphoros-ai/DB-GPT) **v0.8.2**（基线提交 `28312854`）的下游发行版：
> 在上游之上叠加了一套**与具体业务无关**的问数（ChatData）增强能力，并已**清空全部客户业务逻辑**。
> 你的工作 = 在标好的 `TODO(business)` 扩展点上接入你自己的业务。上游英文原版说明见 [README_UPSTREAM.md](./README_UPSTREAM.md)。

---

## 一、这版的逻辑是什么（一分钟版）

```
上游 DB-GPT v0.8.2
        │  保留：全部引擎能力（Agent / AWEL / RAG / 多模型接入 / Serve 组件）
        ▼
本仓库 = 上游 + 通用问数增强层（见第二节清单）
        │  已移除：全部客户业务（口径/码表/区划/专属工具/品牌/内网信息）
        ▼
你拿到的是「通用增强底座」，业务逻辑由你在扩展点自建
```

问数主链路（代码入口，按阅读顺序）：

| 顺序 | 文件 | 职责 |
|---|---|---|
| 1 | `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/agentic_data_api.py` | **问数总入口**（约 5200 行）：会话记忆、技能管理、system prompt 组装、ReAct 流式循环、子代理派发、零工具护栏、防编造护栏 |
| 2 | `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tools/` | 内置工具集：`sql_query`（SQL 执行 + 口径自动回述）、`code_interpreter` / `shell_interpreter` / `html_interpreter`（沙箱执行与离线报告）、`question`（向用户提问弹卡片）、`todowrite`、`load_file` 等 |
| 3 | `packages/dbgpt-core/src/dbgpt/agent/` | Agent 框架增强：多轮上下文窗口化、流式 tool_calls 合并、tool_calling_agent、agent_errors |
| 4 | `packages/dbgpt-serve/src/dbgpt_serve/observability/` 与 `packages/dbgpt-core/src/dbgpt/observability/` | Trace 可观测栈（写 SQLite；前端 `web/pages/observability/` 可视化） |
| 5 | `packages/dbgpt-core/src/dbgpt/util/code/docker_execution.py` | Docker 沙箱执行后端（代码/文件分析在无网络的沙箱容器里跑） |
| 6 | `packages/dbgpt-core/src/dbgpt/model/proxy/llms/customer_gateway.py` | SM3 每请求签名的客户网关适配（OpenAI 兼容，provider 名 `proxy/customer_gateway`） |

## 二、这版相对上游改了什么

**新增的通用增强（全部保留，可直接用）：**

- **多轮上下文污染修复**：历史消息窗口化截断、按结构边界裁剪、去掉逐条 speaker 前缀（防模型把历史当"要续写的转录"）、历史回灌时身份证/手机号打码、连续表格行折叠。
- **防编造护栏（结构性）**：零工具调用却输出统计数字 → 判定无效并撤回；动作被截断/格式损坏的兜底话术；子任务结论必须回溯工具原始输出才能引用。
- **口径自动回述**：`sql_query` 从 SQL 直接提取 FROM/WHERE/GROUP BY/统计方式/数据查询时刻（北京时间），附在结果末尾；提示词硬性要求答复末尾照抄口径块 + 确定性兜底补写。
- **流式 tool_calls 合并**：OpenAI 风格流式增量按 index 正确合并（修复工具调用解析）。
- **Observability 全栈**：span 写 SQLite（`sqlite_span_storage`）、core/serve 双层查询端点、前端 trace 页面。
- **Docker 沙箱**：代码/Shell/文件分析跑在无网络、只读根、限额的 sibling 容器里；`html_interpreter` 离线报告规范（禁外部资源、内置 Chart.js、单轮长度预算）。
- **SM3 客户网关**：`proxy/customer_gateway`，每请求签名 + 网关错误识别 + 私有 CA 可配。
- **Web UI 增强**：会话文件面板（session-files）、新建任务流（new-task）、对话回放分享页（share）、执行步骤面板、追问建议（followups）、北京时间显示、会话标题去库前缀。
- **工程化**：离线构建/部署脚本（`docker/offline/`）、前端构建并发写入保护、超长 SQL 自动裁剪、SQL 连续失败熔断、列名无效自动纠错。

**已移除的内容（= 你需要自建的部分）：**

客户业务口径与区划映射提示词、码表字典工具、业务专属工具（岗位匹配/报表导出等）、业务前端页面与品牌资源、内网地址与现场部署手册。每个移除点在代码里留了 `TODO(business)` 标记，共 **9 处**，即官方扩展点：

| # | 位置 | 用途 |
|---|---|---|
| 1 | `agentic_data_api.py:85` | `_BASE_TABLE_EXCLUDE`：主档/字典类表名（不参与业务表清单） |
| 2 | `agentic_data_api.py:96` | 码表字典提示词段（若你的库有字典表，参考上游历史的 code_lookup 做法） |
| 3 | `agentic_data_api.py:260` | 跨实例数据的提示词段（如业务库与外部库分实例部署的场景） |
| 4 | `agentic_data_api.py:2294` | **业务口径条款**（database_context 提示词，最核心的定制点） |
| 5 | `agentic_data_api.py:2856` | 跨库/外部数据源 → 固定口径专用工具注册 |
| 6 | `agentic_data_api.py:3769` | 业务提示词段注入点（workflow_prompt 组装处） |
| 7 | `sql_query.py:467` | SQL 口径闸门（按业务规则静态核对 SQL，只提醒不改写） |
| 8 | `web/modules/new-task/provider.tsx:29` | 前端独立对话页路由前缀（`STANDALONE_CHAT_PREFIXES`） |
| 9 | `docker/offline/site.env.example` | 部署环境变量（业务 schema、沙箱镜像等） |

## 三、如何跑起来

### 方式 A：本机开发调试

```bash
# 1. 安装依赖（uv 管理，Python 3.10/3.11）
uv sync

# 2. 配置模型：复制一个示例配置（均为 OpenAI 兼容格式，任选）
cp configs/dbgpt-proxy-openai.toml configs/my.toml
#    编辑 configs/my.toml：改 api_base / api_key / 模型名；端口默认 5670

# 3. 启动
uv run dbgpt start webserver --config configs/my.toml --yes
# 打开 http://127.0.0.1:5670 ，页面"数据源"里接入你的业务库即可开始问数
```

### 方式 B：离线 Docker 部署（生产推荐）

```bash
cd docker/offline
bash build-frontend.sh     # 构建前端产物（约 5-15 分钟，脚本自带行尾归一化）
bash build-backend.sh      # 构建后端镜像
cp site.env.example site.env && chmod 600 site.env   # 按需填写
bash start.sh              # 默认 http://127.0.0.1:5670
```

详见 [docker/offline/README.md](./docker/offline/README.md)（镜像结构、沙箱约束、运维命令）。

### 关键环境变量（本版统一改为 DBGPT_ 前缀）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DBGPT_AGENT_MAX_NEW_TOKENS` | `16384` | Agent 单轮输出预算（报告类任务建议保持） |
| `DBGPT_SANDBOX_BACKEND` | 未设 | 设为 `docker` 启用 Docker 沙箱（分析类工具走容器执行） |
| `DBGPT_SANDBOX_HOST_PILOT` | — | 沙箱模式必填：宿主 pilot 目录绝对路径 |
| `DBGPT_SANDBOX_IMAGE` | `dbgpt-sandbox-python:latest` | 沙箱镜像 |
| `DBGPT_DISABLE_DEFAULT_DATA_SOURCES` | `false` | 设 `true` 不注册内置示例数据源 |
| `DBGPT_CROSS_TURN_MEMORY` | `0` | 设 `1` 恢复跨轮回放（默认关闭，防上下文污染） |
| `DBGPT_BIND_ADDRESS` / `DBGPT_PORT` | `127.0.0.1` / `5670` | start.sh 监听地址与端口 |
| `NEXT_PUBLIC_DOCS_URL` | 上游文档站 | 前端文档链接（构建期注入） |

## 四、适配你的项目（建议顺序）

1. **接数据源**：页面「数据源」里添加你的业务库（Oracle/MySQL/SQLite 均可；Oracle 11g thick 模式已内置支持）。
2. **写业务口径**（最重要）：在扩展点 #4 补充口径条款——表用途与关联键、状态字段过滤规则、维度列匹配方式。这是问数准确率的最大杠杆；写法直接参考同文件保留的通用条款（主动澄清 / 人数去重 / 年龄口径 / 排除统计 / 分布合计解释）。
3. **码表字典**（可选）：若库里有字段级/值级字典表，参考扩展点 #2 做 `code_lookup` 工具，编码字段就不会被模型瞎猜。
4. **跨库数据**（可选）：外部数据源做成「固定口径专用工具 + 环境变量开关」，参考扩展点 #5。
5. **前端品牌**：替换 `web/public/` 下 logo 图片、`web/pages/_document.tsx` 的站点描述；界面文案在 `web/locales/{zh,en}/`。
6. **验证**：跑通一条简单统计问题 → 检查答复末尾是否有「口径说明」块 → 打开 observability 页面看 trace 是否完整。

## 五、目录导航

```
packages/
  dbgpt-core/    # 引擎：Agent 框架、AWEL、模型接入、RAG 基础、沙箱执行
  dbgpt-app/     # 应用层：webserver、问数主入口 agentic_data_api、内置工具
  dbgpt-serve/   # Serve 组件：会话/数据源/Flow/知识库/observability
  dbgpt-ext/     # 数据源连接器（含 Oracle thick）、向量库
  dbgpt-client/  # Python SDK
  dbgpt-sandbox/ # 沙箱服务（镜像内运行）
configs/         # 模型配置示例（OpenAI 兼容代理/本地模型等）
docker/offline/  # 离线构建与部署工具链
skills/          # Agent 技能（含 excel-data-analysis）
web/             # Next.js 前端源码
pilot/           # 运行时数据目录（元数据库/向量库/上传文件）
```

## 六、维护规范

- 与上游的同步流程、分支模型、冲突决策记录：见 [DOWNSTREAM_MAINTENANCE.md](./DOWNSTREAM_MAINTENANCE.md)。
- 提交约定：一主题一提交，`fix(<范围>): <主题>` 格式，大改动前打 tag。
- 许可：上游 DB-GPT 采用 MIT License（见 [LICENSE](./LICENSE)），本仓库完整保留。
