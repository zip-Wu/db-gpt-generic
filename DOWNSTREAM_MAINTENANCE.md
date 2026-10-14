# DB-GPT 下游版本维护规范

> 适用仓库：`<your-fork>`（自行托管的下游 fork）<br>
> 官方仓库：`https://github.com/eosphoros-ai/DB-GPT.git`<br>
> 文档版本：`v1.0`<br>
> 建立时官方基线：`v0.8.2`（tag `v0.8.2`，当前跟踪提交 `28312854`）

这份规范用于维护 DB-GPT 的下游定制版：持续吸收上游修复和能力，同时让业务改动可审计、可测试、可回滚。它是仓库协作约定，不替代上游发布说明、许可证或生产变更流程。

## 1. 仓库角色与分支模型

| 名称 | 地址/用途 | 规则 |
| --- | --- | --- |
| `upstream` | 官方 DB-GPT，只读跟踪 | 不直接在其远程分支上开发，不向其推送 |
| `origin` | `<your-fork>`，下游发布仓库 | 下游分支、PR、Release 的唯一发布入口 |
| `main` | 下游默认分支 | 只接受已验收的同步、功能、修复和发布合并 |
| `sync/upstream-vX.Y.Z` | 某个官方版本的同步分支 | 从 `main` 创建，完成一次官方版本升级后合并回 `main` |
| `feature/*` | 下游功能开发 | 一个主题一个分支，完成后通过 PR 合并 |
| `fix/*` / `hotfix/*` | 普通修复 / 生产紧急修复 | `hotfix/*` 合并后必须回补 `main` 和仍在维护的发布分支 |
| `release/*` | 下游候选发布 | 只做版本、迁移、文档和发布阻塞问题，不引入新功能 |

推荐的长期关系：

~~~text
upstream/main 或 upstream/vX.Y.Z
              |
              v
sync/upstream-vX.Y.Z  --->  main  --->  origin/main
                               |
                   +-----------+-----------+
                   v           v           v
               feature/*    fix/*      release/*
~~~

核心原则：

1. 官方 tag 是生产同步的优先基线；`upstream/main` 只用于预研，不直接作为生产版本。
2. `main` 始终可构建、可启动、可回滚；未经验收的官方改动停留在 `sync/*`。
3. 下游业务代码优先放在扩展层（TOML、`skills/`、AWEL、Serve、provider、连接器、`dbgpts`），减少修改高冲突核心文件。
4. 不改写已经共享的历史：禁止对 `main`、`release/*` 和已推送的 `sync/*` 执行 rebase 或强制推送。
5. 每次同步都记录官方版本、完整 commit 范围、冲突决策、测试结果和回滚方式。

## 2. 一次性仓库初始化

当前个人仓库已有一个只包含 `.gitignore` 的初始化提交，且与官方历史无共同祖先。首次导入时必须保留它，使用一次明确的“无共同祖先合并”，不要 push --force。DB-GPT 本身的许可证、版权声明和第三方 notices 也必须随代码保留；下游新增文件按个人仓库的许可证策略补充声明。

在本地 DB-GPT 工作区执行：

~~~bash
# 先确认工作区没有不明的修改；已有调研 HTML 请保留，不要加入本次提交
git status --short --branch

# 官方仓库改名为只读跟踪远程，个人仓库命名为发布远程
git remote rename origin upstream
git remote rename personal origin
git fetch upstream --tags --prune
git fetch origin --tags --prune

# 在官方基线上创建下游文档提交（本规范提交完成后再做合并）
git switch main

# 将个人仓库的初始化历史接入当前 main；若 .gitignore 冲突，保留官方版本并检查差异
git merge --allow-unrelated-histories origin/main \
  -m "chore: bootstrap downstream repository history"

# 只有合并报告冲突时才执行以下两行；若合并已自动完成，不要再运行 `git commit`
git status
git add <已解决的文件>
git merge --continue

# 首次推送到个人仓库。这里是普通 fast-forward（远程初始化提交已在本地历史中）
git push -u origin main
~~~

如果远程在此期间出现了新的提交，先 git fetch origin 并重新审查历史；不要用强推覆盖远程。若无法确认远程提交用途，推送到 bootstrap/downstream-import 临时分支，请仓库管理员审核后再合并。

完成初始化后，远程应当是：

~~~text
upstream  https://github.com/eosphoros-ai/DB-GPT.git
origin    git@github.com:<your-org>/<your-fork>.git
~~~

建议在本机开启重复冲突记忆，降低后续同步成本：

~~~bash
git config rerere.enabled true
git config fetch.prune true
~~~

## 3. 官方版本同步流程

### 3.1 同步前评估

每次官方发布后，先阅读 release notes、升级说明和涉及模块的变更，再决定是否纳入。至少记录：

- 目标 tag，例如 `v0.9.0`；若官方没有 tag，记录完整 commit SHA。
- 上一次下游基线和目标基线：git describe --tags、commit SHA。
- 是否涉及数据库 schema、依赖锁文件、API/SSE、鉴权、代码执行、前端静态资源。
- 是否需要停机、备份、数据迁移或重新构建镜像。
- 官方修复是否与下游补丁重复、冲突或已经失效。

### 3.2 在隔离分支导入

推荐以当前 `main` 为起点：

~~~bash
git fetch upstream --tags --prune
git switch main
git pull --ff-only origin main
git switch -c sync/upstream-vX.Y.Z

# 稳定版本优先合并 tag；不要只合并一个看起来相关的提交而漏掉依赖提交
git merge --no-ff --no-edit upstream/vX.Y.Z

# 官方尚未打 tag 时，使用明确的基线 SHA
# git merge --no-ff --no-edit <upstream-commit-sha>
~~~

如果官方版本是从另一个维护分支发布的，使用该发布分支的 tag，并在同步记录中写明来源；不要把 `upstream/main` 的未发布内容混入生产同步。

### 3.3 冲突处理顺序

按以下顺序处理，而不是盲目选择 ours/theirs：

1. 先保留官方的安全修复、数据一致性修复和兼容性修复。
2. 将下游业务逻辑移回扩展点；若必须保留核心改动，补充原因、责任人和待移除条件。
3. 对配置冲突逐项比较默认值、环境变量名和启动行为，不能只看 TOML 文本是否能合并。
4. 对 API、事件名、序列化结构和数据库字段做调用方反向搜索，确认没有隐性破坏。
5. 每一组冲突解决后运行针对性测试，再继续下一组；完成后运行完整验收清单。

常用检查命令：

~~~bash
git diff --name-only main...HEAD
git diff --check
git log --oneline --left-right main...upstream/vX.Y.Z
rg -n "旧接口名|旧配置名|旧表名" packages web configs tests
~~~

### 3.4 验收并合并

~~~bash
# 同步说明可放在提交信息或 PR 描述中
git diff --check
make fmt-check
make test
make test-doc
make mypy

# 前端源码或依赖发生变化时才执行；构建产物必须与源码一起审查
bash scripts/build_web_static.sh

git status --short
git switch main
git merge --no-ff --no-edit sync/upstream-vX.Y.Z
git push origin main
~~~

同步分支合并前必须有代码审核。若测试因环境依赖无法运行，PR 中要写明未执行项、原因和人工替代验证，不得把“本机未复现”当作通过。

## 4. 选择 merge、cherry-pick 还是不纳入

| 场景 | 推荐做法 | 要求 |
| --- | --- | --- |
| 整个官方稳定版本 | 在 `sync/*` 合并官方 tag | 保留官方提交关系，完整检查 release notes 和迁移 |
| 独立且紧急的安全/缺陷修复 | git cherry-pick -x <sha> 到专用修复分支 | -x 保留来源，确认其前置提交和后续回补 |
| 多个相互依赖的官方提交 | 合并完整提交范围或 tag | 不要只摘最后一个提交 |
| 仅文档、示例或非生产能力 | 评估后 cherry-pick 或暂缓 | 记录为什么不跟随整版 |
| 与下游改动高度耦合 | 暂不直接合并，手工移植并补测试 | 记录官方原始 SHA 和等价实现 |
| 不稳定实验分支 | 默认不纳入生产 `main` | 可在 `experiment/*` 验证，禁止混入发布分支 |

任何已推送的同步提交都用新的修复提交或 git revert 回滚。不要通过改历史“修正”生产分支。

## 5. 下游 / DB-GPT 上游同步边界

DB-GPT 是 uv workspace monorepo。同步时把下列路径视为一个整体检查单元：

| 边界 | 检查重点 |
| --- | --- |
| `packages/*/pyproject.toml`、各包 `_version.py`、根 `pyproject.toml` | 所有 workspace 包版本一致；需要发版时使用 `scripts/update_version_all.py`，先 `--dry-run` 再执行 |
| `uv.lock`、extras 和 provider 依赖 | 先审查 Python 版本与平台约束，再运行 uv lock/uv sync；锁文件变化必须经过测试 |
| `web/` 与 `packages/dbgpt-app/src/dbgpt_app/static/web/` | 前端源码、依赖和构建产物配套；执行 bash scripts/build_web_static.sh 后审查产物差异 |
| `pilot/meta_data/alembic/`、`pilot/meta_data/alembic.ini`、`assets/schema/upgrade/` | 迁移脚本、ORM、启动初始化和升级说明一致；已发布 revision 不修改，只新增 migration |
| `packages/dbgpt-core`、`packages/dbgpt-serve`、`packages/dbgpt-app` | Agent/AWEL、API/SSE、配置装配和启动链路的调用方测试必须覆盖 |
| `packages/dbgpt-sandbox`、代码/命令执行工具、`skills/` | 检查实际执行隔离、路径权限、网络策略、超时和审计日志；不能只依据文档描述 |
| `configs/`、环境变量和 secrets | 示例配置可提交，真实 .env、密钥、模型权重、运行数据和日志不得提交 |

### 5.1 版本号策略

- 官方版本保持官方号，例如 `0.9.0`。
- 仅有下游兼容补丁时可使用 PEP 440 的 `0.9.0.post1`；下游新增功能不应伪装成官方补丁，建议采用产品自己的 minor/major 版本体系，或明确标记为下游构建号。不要伪装成官方发布号。
- 统一修改版本时运行：

  ~~~bash
  cd scripts
  uv run update_version_all.py 0.9.0.post1 --dry-run
  uv run update_version_all.py 0.9.0.post1 --yes
  ~~~

  该脚本按当前工作目录解析项目根目录，因此必须从 `scripts/` 执行；先用 `--dry-run` 审查文件清单，再应用修改。

- Release 说明必须同时写“官方基线”和“下游变更”，并给出升级/回滚步骤。

### 5.2 数据库迁移规则

1. 生产升级前备份元数据库，并在与生产同版本的副本上演练。
2. 从 v0.8.1 升级到 v0.8.2 这类版本，使用对应目录的增量脚本，例如 assets/schema/upgrade/v0_8_2/upgrade_to_v0.8.2.sql；不要把完整 schema 当增量脚本执行。
3. SQLite、MySQL 等数据库的行为分别验证；记录实际数据库类型和迁移命令。
4. 迁移失败时停止服务并按备份恢复，不在半升级数据库上继续试运行。
5. 新增字段/表要同步 ORM、API、前端和测试；已发布 migration 文件不重写。当前仓库的 `.gitignore` 可能忽略 `pilot/meta_data/alembic/versions/`，新增迁移经审核后要确认它确实被 Git 跟踪，必要时显式 `git add -f`。

### 5.3 前端构建规则

只要 web/ 的源码、依赖、环境变量或 API 类型发生变化，就必须：

1. 在固定 Node/Yarn 环境安装依赖并执行 yarn compile（脚本会调用）。
2. 检查 web/out/ 生成结果是否被正确复制到 packages/dbgpt-app/src/dbgpt_app/static/web/。
3. 运行后端启动和关键页面/接口冒烟测试。
4. 不提交本地 .env、web/node_modules、临时 out 目录（除非仓库明确要求的静态产物发生变化）。

## 6. 下游开发规范

### 6.1 优先级和改动位置

优先采用以下低冲突路径：

1. configs/*.toml 和环境变量：默认配置、provider 参数、功能开关。
2. skills/：领域流程、脚本和提示词；脚本数据处理、LLM 洞察和模板合成分层。
3. examples/awel/、AWEL operator、Serve 扩展和 dbgpts：工作流与应用能力。
4. provider、datasource、向量库和连接器适配层：隔离第三方差异。
5. 只有公共契约确实不足时才修改核心 Agent、执行器、API 或前端公共组件。

每个下游改动都要标注“官方可回馈 / 仅本地业务 / 临时兼容”三种状态之一。具备通用价值的改动优先整理成可独立提交的官方 PR，减少长期补丁负担。

### 6.2 提交和 PR

采用 Conventional Commits，类型至少包括：feat、fix、refactor、test、docs、chore、security。

示例：

~~~text
feat(skill): add monthly sales analysis workflow
fix(api): preserve task events during reconnect
chore(sync): merge upstream v0.9.0 (upstream 1a2b3c4)
~~~

同步 PR 必须包含：

- 官方来源 tag/SHA 和本次合并范围；
- 冲突文件及每项取舍；
- 依赖、数据库、配置、API、前端和安全影响；
- 测试命令与结果，未执行项及原因；
- 发布、监控和回滚方案。

## 7. 验收清单

将以下清单复制到每个同步或发布 PR：

~~~text
[ ] 官方 release notes、upgrade notes 和安全公告已阅读
[ ] 官方基线 tag/SHA、上一次基线和完整 commit 范围已记录
[ ] git diff --check 通过，无意外生成物、密钥或运行数据
[ ] uv workspace 包版本、uv.lock 和 extras 已检查
[ ] 依赖安装/锁定可复现，Python/Node/Yarn 版本已记录
[ ] make fmt-check 通过
[ ] 相关 pytest 通过；必要时 make test、make test-doc、make mypy 通过
[ ] web 变更已重新构建静态资源并完成页面/API 冒烟
[ ] 数据库迁移已备份、演练，并核对 ORM/API/前端兼容性
[ ] Agent/AWEL、API/SSE、鉴权和配置启动链路已验证
[ ] code_interpreter、shell_interpreter、skill 脚本等执行路径的隔离/权限/超时已检查
[ ] Docker/compose 或生产启动方式已完成最小化部署验证
[ ] 监控、日志、回滚点和发布说明已准备
[ ] 同步台账已更新，PR 已完成审核
~~~

按变更风险选择最小范围测试，但以下情况不能只跑单元测试：数据库迁移、API/SSE 契约、鉴权、安全执行路径、依赖大版本升级和前端静态构建变更必须做集成或冒烟验证。注意：本仓库当前 `make fmt-check` 目标内部包含 `ruff check --fix`，可能修改文件；执行前确认工作区干净，执行后重新查看 `git diff`。

## 8. 发布、回滚与分支保留

- `main` 合并后先构建候选版本，验证通过再创建下游 tag，例如 `downstream-v0.9.0` 或 `downstream-v0.9.0.post1`。
- 生产部署记录镜像 digest、代码 SHA、配置版本、数据库 revision 和迁移时间。
- 保留至少一个可运行的上一生产版本；发布失败优先回滚应用和镜像，再按迁移设计回滚数据库。
- 不把数据库 downgrade 当作默认回滚方案；破坏性迁移应采用向前兼容、双写/回填或经过演练的恢复方案。
- `sync/*` 在合并并稳定观察后可保留为审计分支；不得删除唯一的同步证据。临时实验分支可在确认没有发布或 PR 引用后清理。

## 9. 同步频率与责任

| 类型 | 建议频率 | 处理时限 |
| --- | --- | --- |
| 官方安全公告/高危修复 | 立即评估 | 发现后 1 个工作日内决定是否热修复 |
| 官方稳定 tag | 每 2--4 周或每个业务窗口 | 先在 `sync/*` 验证，再安排发布 |
| 官方 `main` 预览 | 按需 | 仅实验分支，不影响生产 |
| 下游独有补丁清理 | 每次同步后 | 能回馈官方的改动单独整理 PR |

至少一名维护者负责同步评估，另一名成员负责审核高风险冲突。生产变更由运行负责人确认窗口、备份和回滚点。

## 10. 同步台账模板

建议在仓库的 docs/ 或发布 PR 中维护一份复制版；不要把真实密钥和生产连接串写入台账。

~~~markdown
## Upstream Sync YYYY-MM-DD

- 官方仓库：<URL>
- 官方基线：<tag> / <full SHA>
- 上一次下游基线：<tag> / <full SHA>
- 下游发布版本：<version>
- 同步分支：sync/upstream-<tag>
- 同步提交范围：<old SHA>..<new SHA>
- 合并的官方 commits：<列表或 release 链接>
- 下游独有改动：<模块/PR/commit>
- 冲突与决策：<文件、选择、原因>
- 数据库迁移：<无 / 脚本 / 演练结果 / 回滚方式>
- 依赖与配置变化：<摘要>
- 安全检查：<执行路径、权限、网络、审计>
- 测试命令与结果：<命令、通过/失败、未执行原因>
- 发布窗口与负责人：<信息>
- 监控观察期：<时长、指标>
- 回滚点：<代码 SHA、镜像、数据库备份>
- 待回馈官方：<候选改动和 issue/PR>
- 已知风险：<风险、影响、后续动作>
~~~

## 11. 常用命令速查

~~~bash
# 查看远程和分支关系
git remote -v
git branch -vv
git log --graph --oneline --decorate --all -20

# 获取官方更新
git fetch upstream --tags --prune
git log --oneline main..upstream/main
git tag --sort=-version:refname | head

# 创建同步分支
git switch main
git pull --ff-only origin main
git switch -c sync/upstream-vX.Y.Z

# 发现并记录下游独有提交
git log --oneline upstream/main..main
git diff --stat upstream/vX.Y.Z...main

# 失败时安全取消尚未提交的合并（仅限当前未提交合并过程）
git merge --abort

# 已发布同步的回滚
git switch main
git revert -m 1 <merge-commit>
git push origin main
~~~

git merge --abort 只用于当前正在进行且尚未完成的合并；任何清理、删除或历史重写操作都必须先确认目标和备份。

## 12. 本规范的变更

规范本身按普通文档 PR 修改。变更时说明适用范围、迁移影响和生效日期；涉及分支保护、发布权限、CI 门禁或数据库操作的调整，必须由维护者审核后生效。

## 13. GitHub 仓库设置建议

在 `origin` 的 GitHub 设置中为 `main` 开启分支保护：要求 PR 合并、至少一名审核者、CI 检查通过、分支已更新后再合并，并禁止 force push 和直接删除分支。为 `sync/*` 保留 PR 记录；如果使用自动同步 Action，令 Action 只创建/更新同步分支和 PR，不直接写入 `main`，并把密钥放在 GitHub Actions Secrets 中。
