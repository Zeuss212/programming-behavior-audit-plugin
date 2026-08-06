# 编程行为监控分析插件 0.2.0 盲审测试与整改复验报告

日期：2026-07-29  
审查对象：`myextension 0.2.0` 本地单用户 Pilot  
当前结论：**B+，8.6/10；原盲审 I-01～I-06 已完成代码整改和自动化复验，
可恢复本地单用户 Pilot 的 Notebook/Python 主流程验证。Python 3.10–3.14
兼容矩阵、多租户隔离和正式研究校准仍未完成，不建议扩大到无人值守或多班级
部署。**

首次盲审结论为 B-、7.2/10。下文保留修复前发现作为历史基线，并在最前面给出
整改后的新鲜证据，避免把旧截图或旧风险描述误认为当前状态。

## 整改复验摘要

### 已完成

| 原发现 | 当前状态 | 实现与复验证据 |
|---|---|---|
| I-01 Python 路径越界/未等待 | 已修复 | Route 改为异步等待 Contents 校验；解析后强制位于 Jupyter 根目录内；6 个正常、越界、目录、缺失和不支持 Manager 直接测试通过 |
| I-02 未配置 AI 仍显示结论卡 | 已修复 | `ai_not_configured` 使用独立“数据采集完成，尚未进行 AI 分析”状态；不渲染结果卡或教师复核表单 |
| I-03 Key 无清除入口 | 已修复 | 仅在已配置时显示“清除已保存 Key”；二次确认后只发送 `clear_api_key: true`；确认、取消和失败路径有测试 |
| I-04 配置错误返回 500 | 已修复 | 闭合的 400 `ai_config_validation_failed` 契约携带安全 `field/reason`；浏览器实测错误定位到 Base URL 字段 |
| I-05 核心采集器无直接测试 | 已修复 | Notebook/Python 监控器新增 13 个直接测试和文件级 70/60/70/70 覆盖率门槛 |
| I-06 Python 入口不可发现/英文 | 已修复 | 中文命令同时进入命令面板、顶部“运行”菜单和文件右键菜单；中文显示退出码、耗时和输出 |
| I-07 Python 版本矩阵未验证 | 保留 | 按交付决定不收紧 `requires-python >=3.10`；本轮仅把 Python 3.12.13 记录为已验证环境 |

### 新鲜执行证据

| 检查 | 整改后结果 |
|---|---|
| `jlpm lint:check` | PASS |
| 前端 Jest | 12/12 suites，224/224 tests PASS |
| 前端覆盖率 | 总语句 82.48%；Notebook 78.50%/分支 66.66%；Python 90.17%/分支 78.09% |
| 后端 pytest | 469/469 PASS |
| `jlpm build:prod` | PASS，Rspack success |
| wheel 制品图测试 | 1/1 PASS；重建后的 0.2.0 wheel 与仓库前端制品一致 |
| 干净安装 | Python 3.12.13；93 个包兼容 |
| Jupyter Server extension | `myextension 0.2.0 enabled OK` |
| JupyterLab extension | `myextension v0.2.0 enabled OK` |
| 隔离浏览器冒烟 | 侧栏加载；运行菜单中文命令可用；合成 Python 输出正确；Base URL 字段错误可操作 |
| 外部数据边界 | 未调用外部 AI，未配置或写入 API Key，只使用一次性目录和固定合成代码 |

字段错误验证会按设计产生一次 HTTP 400，浏览器将该网络响应记录为
`Failed to load resource`；验证前控制台为 0 error/0 warning，页面没有脚本异常。

### 当前仍需注意

- 包元数据保留 Python 3.10–3.14 声明，但本轮只验证 Python 3.12.13，其他版本
  不能据此宣称已兼容。
- `api_key_preview`、旧 0.1.0 wheel、脚手架仓库元数据和生产 Hello 探针仍是
  次要整改项。
- `pageMonitor.ts` 和插件装配层仍缺少直接覆盖；本轮门禁聚焦用户指定的
  Notebook/Python 核心监控器。
- 本项目仍是本地单用户 Pilot，不具备 JupyterHub 多租户授权或研究级效度验证。

## 首次盲审原则与安全边界（修复前基线）

- 不采用此前验收结论作为本轮通过依据，重新执行测试、构建、安装和界面流程。
- 不读取、搜索、打印、复制或哈希真实日志、真实 API Key、学生身份、真实会话或
  现有 Notebook 内容。
- 浏览器流程只使用一次性 Python 3.12 环境、临时 Jupyter 根目录、固定合成方案、
  固定合成代码和本机回环地址。
- 未调用外部 AI。AI 未配置流程使用产品自身的闭合失败路径；配置接口只使用固定
  合成值，并在验证后清除合成 Key。
- 首次盲审阶段不修改产品源代码；后续整改按已确认修复顺序实施。

## 首次盲审总体判断（修复前基线）

本项目已经不再是“只有采集文件、用户不知道有什么用”的原型。教师创建维度、
发布方案、学生确认、会话采集、停止后分析和教师复核形成了可理解的主流程。
Notebook 和 Python 文件的合成编辑、运行与事件计数均在真实 JupyterLab 中工作。

主要风险同时存在于 Python 文件运行边界，以及结果与配置的使用语义：

1. Python 文件运行路径调用异步 Contents 校验却未等待，并会沿符号链接解析到
   Jupyter 根目录之外；
2. AI 未配置时仍出现“部分结果/待复核”卡片，容易被首次用户当成已有分析结论；
3. 已保存的 API Key 后端支持清除，但界面没有撤销入口；
4. Python 文件运行入口只存在于英文右键菜单，发现成本高；
5. 核心 Notebook/Python 采集器缺乏直接自动化覆盖；
6. wheel 宣称支持 Python 3.10–3.14，但交付文档和本轮证据只覆盖 3.12。

## 首次盲审执行证据（修复前基线）

| 检查 | 本轮结果 |
|---|---|
| `jlpm lint:check` | PASS |
| 前端 Jest | 10/10 suites，202/202 tests PASS |
| 前端语句覆盖率 | 68.54%；`index.ts`、`notebookMonitor.ts`、`pythonFileMonitor.ts`、`pageMonitor.ts` 为 0% |
| 后端 pytest | 461/461 PASS |
| `jlpm build:prod` | PASS，Rspack success |
| wheel 制品图测试 | 1/1 PASS |
| 三份 wheel SHA-256 | 均为 `d596f5f65950c479997ab95fd54afb079b3fb0a8d25af08822d999ddbde6d1e3` |
| 干净安装 | Python 3.12；93 个包兼容 |
| Jupyter Server extension | `myextension 0.2.0 enabled OK` |
| JupyterLab extension | `myextension v0.2.0 enabled OK` |
| 页面控制台 | 0 error，0 warning |
| 源码固定凭据扫描 | 未发现嵌入式真实 Key 模式 |

## 实际流程与健康度

### 1. 首次进入：健康

首次页面说明工具能回答什么、会采集什么、何时可能向外部模型发送内容，并提供
单一主按钮。

![首次进入](../output/playwright/blind-audit-2026-07-29/01-first-run.png)

### 2. 模板、维度填写与发布确认：健康

模板选择、完全自定义、1–10 个维度和发布前汇总均可使用；三维度方案可在同一
引导流程中完成。必填校验具有字段标签、`aria-invalid` 和错误说明关联。

![模板选择](../output/playwright/blind-audit-2026-07-29/02-template-selection.png)

![维度表单](../output/playwright/blind-audit-2026-07-29/03-dimension-form.png)

![发布确认](../output/playwright/blind-audit-2026-07-29/04-publish-confirmation.png)

### 3. 方案发布、即时刷新和本次同意：健康

发布后方案无需刷新页面即可出现在侧栏。监控默认关闭，未选择方案或未勾选本次
用途确认时不能启动。

![选择方案与同意](../output/playwright/blind-audit-2026-07-29/06-profile-consent.png)

### 4. Notebook 失败、修改、成功：健康

固定的除零错误被采集，修改后成功运行；会话事件计数从 0 增长到 10，待上传数
最终归零。

![Notebook 失败到成功](../output/playwright/blind-audit-2026-07-29/07-capture-failure-success.png)

### 5. AI 未配置的停止后状态：需改进

页面正确提示检查 AI 配置并提供重试，但同时展示两个“部分结果”卡片。卡片有
“已达到最低观察要求”和“查看 0 条证据”，语义容易冲突。

![无 AI 的任务状态](../output/playwright/blind-audit-2026-07-29/08-no-ai-partial-results.png)

![无 AI 的部分结果卡](../output/playwright/blind-audit-2026-07-29/09-partial-result-cards.png)

### 6. AI 配置：功能存在，控制不完整

Key 输入框为密码类型，服务端配置文件有私有权限和安全失败测试；但 UI 只有
“保存”，没有清除已保存 Key 的操作。

![AI 配置](../output/playwright/blind-audit-2026-07-29/10-ai-config-defaults.png)

### 7. Python 文件编辑与运行：基本路径可用，边界需修复

编辑事件会被采集，右键菜单运行成功且产生 scheduled/success 事件；但编辑器
工具栏没有运行按钮，命令也未加入命令面板，唯一入口是英文右键菜单。服务器同时
产生“coroutine was never awaited”警告；合成符号链接测试证明当前路径可解析到
Jupyter 根目录之外。

![Python 文件没有可见运行按钮](../output/playwright/blind-audit-2026-07-29/11-python-file-no-run-control.png)

![右键运行入口](../output/playwright/blind-audit-2026-07-29/12-python-context-run.png)

![Python 文件运行成功](../output/playwright/blind-audit-2026-07-29/13-python-run-success.png)

## Findings

### Critical

无。

### Important

#### I-01：Python 文件运行绕过了异步 Contents 校验和根目录边界

- 证据：`myextension/routes.py:1748-1764`；浏览器真实运行后服务器出现
  `AsyncFileContentsManager.get was never awaited` 警告。
- 隔离复现：在一次性临时 Jupyter 根目录中创建指向根外固定合成 `.py` 文件的
  符号链接；`AsyncFileContentsManager.get` 被确认为 coroutine，
  `_contents_os_path()` 返回的解析路径位于 Contents 根目录之外，并捕获到 1 个
  未等待 coroutine 的 `RuntimeWarning`。测试没有执行根外脚本。
- 当前行为：同步 Route 调用 `contents_manager.get(...)` 却没有 `await`，随后
  使用私有 `_get_os_path()` 并对结果 `resolve()`；解析后没有再次验证路径仍在
  Jupyter 根目录内。
- 影响：已认证的本地 Jupyter 用户可通过根目录内符号链接让该接口运行 Contents
  根目录外的 Python 文件；同时 Contents Manager 的访问策略和存在性校验没有按
  预期执行。当前单用户 Pilot 降低了跨用户影响，但这是明确的执行边界缺陷。
- 建议：把 Route 改为 `async def post` 并 `await contents_manager.get(...)`；
  优先通过公开 Contents API 读取受管内容并执行受控临时副本。若仍需本地路径，
  对解析后的路径做根目录包含校验并显式拒绝越界符号链接；补充正常文件、根外
  符号链接、自定义 Contents Manager、非本地 Manager 和不存在文件测试。在修复
  前禁用 `run-python-file` 路由或不向 Pilot 用户开放 Python 文件运行入口。

#### I-02：AI 未配置时的结果卡容易被理解为已有分析结论

- 证据：截图 08、09；`src/ui/analysisResultView.ts:53-59,402-405`。
- 复现：不配置 AI，完成满足最低信号覆盖的会话并停止。
- 当前行为：顶部提示配置 AI，但维度卡显示“部分结果，建议结合课堂观察”、
  “已达到最低观察要求”和 0 条证据，汇总计入“待复核”。
- 数据完整性说明：后端没有伪造等级；`final_evidence_status` 和
  `final_level_code` 均为 `null`。问题是用户语义，不是虚假数据库值。
- 影响：教师可能把“信号覆盖足够”误解为“行为维度已经得到模型判断”。
- 建议：当 `error_code=ai_not_configured` 时，不渲染普通结果卡；改为统一的
  “尚未进行 AI 分析”空状态。若保留覆盖结果，应明确写成“采集数据可分析，
  尚无 AI 结论”，并禁止默认进入教师确认。

#### I-03：AI Key 缺少可见的清除/撤销操作

- 证据：`src/ui/behaviorAnalysisSidebar.ts:1030-1100` 仅发送
  `base_url/model/api_key`；后端 `save_ai_config({"clear_api_key": true})`
  已有能力和测试。
- 复现：保存 Key 后把输入框留空再次保存，旧 Key 会继续保留。
- 影响：教师无法从普通界面确认停止使用某个 provider，也无法在交接机器前可靠
  清除本地凭据。
- 建议：增加“清除已保存 Key”按钮、二次确认和成功状态；只发送
  `clear_api_key: true`，并立即刷新 `api_key_configured`。

#### I-04：AI 配置的用户输入错误被错误映射成 HTTP 500

- 证据：`myextension/routes.py:338-343` 仅捕获 `OSError`。
- 本轮复现：提交 `http://example.invalid`，返回
  `500 {"message":"Unhandled error","traceback":""}`。
- 正面边界：响应没有泄露 traceback、Key 或 provider 内容。
- 影响：普通字段错误被当成服务器故障；前端只能显示“保存失败，请重试”，用户
  不知道需要 HTTPS 或回环地址。
- 建议：捕获 `ValueError` 并返回闭合的 400/422 错误码，例如
  `ai_config_validation_failed`；前端将安全字段错误显示在 Base URL 下方。

#### I-05：核心采集器没有直接自动化覆盖，也没有覆盖率门禁

- 证据：本轮 Jest 报告中 `index.ts`、`notebookMonitor.ts`、
  `pythonFileMonitor.ts`、`pageMonitor.ts` 均为 0%；`jest.config.js`
  没有 `coverageThreshold`。
- 正面边界：本轮浏览器实际验证了 Notebook 和 Python 文件的最小成功路径。
- 影响：JupyterLab API、焦点切换、Cell 生命周期、文件切换、运行失败和释放资源
  等回归不能被现有单元测试稳定拦截。
- 建议：先补 `notebookMonitor` 和 `pythonFileMonitor` 的直接契约测试，再逐步
  设置文件级阈值；不要用提高全局数字替代核心路径覆盖。

#### I-06：Python 文件的运行入口不可发现且语言不一致

- 证据：截图 11–13；`src/index.ts:138-196` 只注册英文命令并加入右键菜单。
- 影响：第一次使用的人可以看到 Python 文件编辑被采集，却不知道如何触发插件
  的 Python 文件运行路径；这与 Notebook 的显式运行按钮体验不一致。
- 建议：把命令加入命令面板和 Run 菜单，提供中文标签“运行当前 Python 文件”，
  最好增加文件编辑器工具栏按钮或首次提示；保留右键入口作为快捷方式。

#### I-07：Python 运行时支持声明超过已验证范围

- 证据：`pyproject.toml:9,19-23` 允许并标注 Python 3.10–3.14；
  `启动说明.md` 和本轮安装证据只覆盖 Python 3.12，项目未提供版本矩阵 CI。
- 影响：pip 会允许在未验证版本上安装，部署问题可能被误归因于 Jupyter 或插件。
- 建议：Pilot 阶段将 `requires-python` 收紧到已验证范围，或补充
  3.10–3.14 的安装、后端测试和扩展 smoke 矩阵后再保留当前声明。

### Minor

#### M-01：AI 配置状态接口返回未被 UI 使用的 Key 尾部

`myextension/llm_transport.py:263-280` 返回 `api_key_preview` 的最后 6 位。
本轮用固定合成 Key 证实响应确实包含尾部预览。当前为本地单用户、认证接口，风险
有限，但前端只使用布尔值，建议删除不必要的秘密派生字段。

#### M-02：交付目录同时包含 0.1.0 和 0.2.0 wheel

文档使用精确文件名，因此不会自动装错；但人工拖拽或通配符安装时仍可能选择旧包。
建议最终交付 `dist/` 只保留当前版本，旧包移到带版本说明的归档目录。

#### M-03：安装包元数据仍是脚手架值

wheel 中 Homepage 为空、Bug Tracker 为 `/issues`、Repository 为 `.git`。不影响
运行，但不利于问题追踪、来源确认和后续交付审计。

#### M-04：仍保留生产 Hello 探针和英文技术状态

插件启动调用 `/myextension/hello`，侧栏直接显示 `partial`，Python 运行对话框为
英文。它们不造成安全问题，但削弱正式交付感和首次理解速度。

## 已确认的优点

- 监控默认关闭；方案选择和每次用途确认是启动前置条件。
- 发布版本与会话绑定，前后端测试覆盖版本/hash/事件引用一致性。
- 上传重试、exact finalize、单 job、review 追加和删除竞态有较完整后端测试。
- 模型提示边界有绝对路径、凭据标记和指令注入清洗测试。
- Provider 错误不返回原始响应正文或 Key。
- AI 配置文件、结果和复核文件使用私有权限与原子写入。
- 无数据时明确显示数据不足；结果不会新增教师未配置的维度。
- wheel 可复现、制品图与源码匹配，干净安装可启用前后端扩展。

## 首次盲审提出的建议修复顺序（历史记录）

1. 先修 I-01；修复前禁用 Python 文件运行路由或不对 Pilot 开放该入口。
2. 再修 I-02、I-03、I-04：避免误读，补齐 Key 撤销和可操作错误。
3. 修 I-06，让 Python 文件主流程可发现且中文一致。
4. 补 I-05 的核心监控器测试，再设置覆盖率门禁。
5. 在扩大部署前处理 I-07 和交付目录/元数据清理。

## 审查限制

- 没有向真实外部模型发送数据；成功 AI 结果的 provider HTTP 集成由合成后端测试
  覆盖，本轮浏览器重点验证未配置路径。
- 未声称完整 WCAG 合规。本轮确认了主要标签、按钮、details、表单错误关联和
  焦点语义，但未完成屏幕阅读器、200% 缩放、对比度和完整键盘路径测试。
- 未在 Windows 上执行 `start_project.bat`，只做了源码和文档检查。
- 未测试 JupyterHub 多租户；产品文档已明确当前仅为本地单用户 Pilot。
- Python 路径越界复现只验证解析结果，没有执行根目录外的合成脚本。
