# 教师自定义题目分析维度设计

日期：2026-07-28  
状态：实用性与易用性平衡修订版，待用户复核  
适用项目：JupyterLab 编程行为监控分析插件

## 1. 背景

现有系统可以采集 Notebook 和 Python 文件中的输入、删除、粘贴、运行、报错、停顿、页面离开及上下文切换等客观事件，并通过固定的 AI 标签目录推断编写、调试、能力、错误和掌握度。

当前标签目录写死在后端，题目上下文和教师评价标准没有成为稳定输入。同一套通用维度被用于不同题目，导致以下问题：

1. 教师不能指定本题真正关心的行为。
2. AI 可能生成题目无关或定义不一致的标签。
3. 同一维度在不同批次中的含义不稳定。
4. 结果缺少与教师标准对应的证据。
5. 无法复现“某次分析使用了哪套维度定义”。

本设计把固定标签目录改造成题目级、版本化、可复现的“分析维度方案”。

## 2. 目标

### 2.1 产品目标

1. 教师可以为每道题创建一套分析维度方案。
2. 学生开始监控时，会话必须绑定一个已发布的方案版本。
3. AI 只能返回教师方案中定义的维度和等级。
4. 每个推断结果必须包含客观事件证据；AI 模型自评和已校准规则分数分别保存，不生成来源不明的融合置信度。
5. 客观指标优先由规则计算，AI 只处理需要语义理解的部分。
6. 分析过程有明确的排队、运行、部分完成、成功和失败状态。
7. 教师可以修正结果，修正内容与模型结果分开保存。
8. 明确区分“未观察到行为”“证据不足”和“当前数据不可计算”，禁止把缺失数据解释成学生没有表现出某种行为。
9. 教师发布的维度先经过历史样本预览和小规模效度验证，再用于正式教学判断。
10. 普通教师无需理解信号编码、规则权重、阈值、AI 模式或聚合算法，也能在向导中完成维度创建。
11. 复杂技术参数保留在系统模板、高级设置和研究验证后台中，不以降低严谨性换取界面简单。
12. 普通结果页不仅说明“观察到了什么”，还提供由模板或教师预先定义的下一步教学建议，帮助教师把分析结果转化为可执行的课堂动作。

### 2.2 工程目标

1. 停止监控后统一提交一次会话分析任务，不再为每个上传批次创建独立 AI 线程。
2. 已发布方案不可修改；修改必须生成新版本。
3. 所有输出记录方案 ID、版本、内容哈希、模型和提示词版本。
4. 模型输出经过严格校验后才能进入样本数据。
5. 旧会话和未选择自定义方案的会话继续使用内置默认方案。
6. 原始事件、特征、规则结果、AI 结果、融合结果和教师修正分层保存，任一最终结果都可追溯。
7. 上传、结束会话、创建任务和重分析都具有明确的幂等键与状态转换。
8. 最终决策状态、证据状态和行为等级分开保存，冲突时允许最终证据状态和等级为空。
9. 未校准的规则提示不得生成或展示数值支持度。

### 2.3 使用难度基线

默认使用路径必须满足：

- 普通教师只处理教学语义：维度名称、教学问题、符合表现和排除情况；结果说明与教学建议有可直接使用的默认值。
- 系统处理技术配置：信号选择、最低观察机会、分析模式、规则候选和聚合策略。
- 研究管理员处理正式验证：数据集划分、校准、盲测、统计指标和审批。
- 第一次创建包含三个维度的试点方案，目标用时不超过十分钟；教师没有进入高级设置也能完成。
- 当系统不能可靠匹配模板时，明确显示“未找到合适模板”，使用完全自定义试点流程，不用不透明的技术建议冒充确定配置。

## 3. 非目标

本阶段不实现：

1. 班级、学校、课程和账号管理平台。
2. 多校区集中式数据仓库。
3. 自动给学生打分或直接生成最终成绩。
4. 允许教师编写任意 Python 规则代码。
5. 允许学生修改教师已发布的分析方案。
6. 根据单次停顿直接诊断学生心理状态。
7. 在未完成效度验证前宣称某套自定义维度“提高了准确率”。
8. 要求普通教师手工设置规则权重、信号阈值或统计聚合算法。

## 4. 用户与权限

### 4.1 教师

教师负责：

- 创建和编辑草稿方案。
- 发布不可变的方案版本。
- 为题目选择启用的方案。
- 查看维度结果及其证据。
- 修正分析结果。
- 对失败任务发起重新分析。

普通教师默认使用引导模式。只有具有高级权限并明确进入“高级设置”后，才会看到系统生成的信号、规则和聚合策略。

### 4.2 研究管理员

研究管理员负责：

- 维护内置维度模板和信号字典。
- 管理规则校准数据和锁定盲测集。
- 查看一致性、误判率和证据支持率。
- 审批方案从 `pilot` 升级为 `approved`。
- 修改高级规则或算法参数。

本地 MVP 中这是一种功能模式，不代表已经实现强权限隔离；正式部署必须在服务端授权。

### 4.3 学生

学生负责：

- 选择或打开已绑定题目的 Notebook。
- 明确点击“开始监控”。
- 完成编程任务。
- 点击“停止监控”并提交分析。

学生不负责配置模型密钥、修改维度方案或查看其他学生的数据。

### 4.4 MVP 权限边界

当前项目是单用户本地 Jupyter Server。MVP 在界面中区分教师操作和学生操作，但不宣称具备强角色隔离。

正式多人部署必须使用 JupyterHub 单用户实例或独立教师服务，不能让多个学生共享同一个 Jupyter Server 进程。

本地 MVP 只允许用于开发、演示和经教师知情确认的小规模试点。没有服务端角色授权、数据隔离、删除闭环和效度报告时，不得作为正式课堂评价系统部署。

## 5. 核心概念

### 5.1 题目

题目用于提供分析上下文，包含：

- `problem_id`
- 题目名称
- 题目说明
- 预期知识点
- 常见错误
- 可选的正确性测试说明

### 5.2 分析维度方案

分析维度方案由一组维度定义组成。每个已发布版本是不可变快照。

### 5.3 维度

每个维度回答一个清晰问题，例如：

- 是否出现了反复重写？
- 是否形成了有效的失败—修改—验证链条？
- 是否在边界条件上反复出错？
- 是否频繁停顿且缺少后续验证？

维度必须描述可观察证据，不使用“懒惰”“能力差”等不可验证或带价值判断的名称。

每个维度必须声明一种类型：

- `observed_metric`：可直接由事件或特征计算，例如运行失败次数。
- `behavioral_inference`：结合多个行为证据形成的有限推断，例如“失败后是否形成持续调试链”。
- `knowledge_inference`：需要题目测试或多来源证据才能判断的知识表现。本 MVP 只保存定义，不在没有隔离测试的情况下自动给出结论。

维度名称和解释必须使用行为语言。对于 `behavioral_inference`，界面统一使用“证据提示可能存在……”的措辞，不把结果表述为心理诊断或稳定人格特征。

引导模式不提供 `knowledge_inference` 创建入口；它只在研究模式中可见，直到隔离测试能力完成。

### 5.4 会话

一次“开始监控”到“停止监控”构成一个会话。会话在开始时锁定题目、方案 ID 和方案版本。

会话状态及允许转换为：

```text
collecting → finalizing → finalized
collecting → abandoned
finalizing → collecting
abandoned → collecting
```

- `collecting`：允许接收带唯一事件 ID 的片段。
- `finalizing`：拒绝新的普通上传，等待前端剩余队列确认。
- `finalized`：不可再追加事件，只能分析或重分析。
- `abandoned`：超过配置的无活动时间且未完成结束；保留原始事件，但不自动生成正式分析结论。

上传失败时，`finalizing` 可以回到 `collecting`。废弃会话经教师确认并填写恢复原因后可以回到 `collecting`，系统追加恢复审计记录。只有服务端确认全部连续序号已接收后才能进入 `finalized`。

### 5.5 分析任务

停止监控后，后端为会话创建一个持久化分析任务。任务状态为：

- `queued`
- `running`
- `partial`
- `ready`
- `error`

允许的状态转换为：

```text
queued → running
running → ready
running → partial
running → error
error → queued
partial → queued
```

`ready` 是不可变终态。重新分析必须创建新任务，不能覆盖原任务。恢复服务时，遗留的 `running` 任务记录恢复原因后转回 `queued`。

当全部维度均形成终态结果时任务为 `ready`，其中可以包含 `needs_review`。至少一个维度有结果、另有维度因外部依赖或校验失败不可用时为 `partial`；没有任何维度产生结果或输入完整性失败时为 `error`。

每次执行或重试生成不可变的 `attempt` 记录。`error → queued` 和 `partial → queued` 只创建新 attempt，不覆盖上一轮输入、原始响应或部分结果。

### 5.6 证据状态

每个维度先通过数据覆盖门槛，再由规则或 AI 形成最终证据状态：

- `observed`：数据完整，且存在支持该等级的证据。
- `not_observed`：数据完整、达到最低观察机会，但没有发现定义中的行为。
- `insufficient_evidence`：事件数量、观察时长或必要上下文不足。
- `not_computable`：当前会话没有采集该维度必需的信号，常见于旧日志或采集器版本不兼容。

覆盖门槛通过后，内部状态为 `sufficient_for_analysis`，但该状态不写入最终结果。只有候选证据状态均为 `observed` 时才比较行为等级；规则和 AI 对“是否观察到”意见不一致也属于冲突。最终 `not_observed` 表示“在充分观察下未发现该行为”，行为等级为 `null`；后两种状态不能被映射成最低行为等级，也不用于准确率统计。

### 5.7 信号字典

规则编辑器只能使用后端发布的信号字典。每个信号定义必须包含：

- 稳定编码、中文名称和说明。
- 数据类型和单位。
- 统计范围：事件、阶段或完整会话。
- 聚合方式：计数、累计值、最大值、比例或分位数。
- 分母和零分母处理。
- 缺失值语义。
- 最低观察机会。
- 来源事件类型和采集器最低版本。

方案发布时复制所引用信号定义的版本和哈希。后端信号语义变化必须生成新版本，不能静默改变历史分析。

### 5.8 配置方式

系统提供三种配置方式，但普通教师默认只看到第一种：

- `guided`：引导模式。教师从模板开始，或填写教学问题、支持表现、排除情况和结果描述；系统生成内部配置。
- `advanced`：高级模式。研究管理员或具有高级权限的教师可以查看和调整必要信号、规则条件和聚合策略。任何技术参数变化都会使既有校准失效，方案保持 `pilot`。
- `research`：研究验证模式。用于数据集划分、规则校准、盲测、指标计算和部署审批，不出现在学生界面。

### 5.9 维度模板

MVP 内置少量经过说明和测试的题型无关模板：

- 反复修改。
- 失败—修改—再运行的调试链。
- 连续运行失败。
- 主动停顿后缺少验证。

模板包含教师看不到的默认信号、最低观察机会、候选规则和聚合策略。普通教师可以修改名称、教学问题、支持表现、排除情况和等级描述；修改技术参数必须进入高级模式。

完全自定义且无法匹配模板的维度默认使用 `llm_evidence`：AI 只依据教师定义和事件证据作出有限判断，方案保持 `pilot`。系统不得为它自动生成看似精确但未经校准的规则分数。

### 5.10 最终决策状态

最终决策状态与证据状态分离：

- `resolved`：已形成可展示的最终证据状态；若观察到行为，同时形成最终等级。
- `needs_review`：规则和 AI 冲突，或结果只能由教师决定。
- `partial`：部分分析来源不可用，但仍保留有效中间结果。
- `failed`：没有形成任何可用结果。

`needs_review`、`partial` 和 `failed` 的最终证据状态与最终等级允许为 `null`，不得为了填充字段而选择任意等级。

## 6. 推荐用户流程

### 6.1 教师创建方案

1. 教师打开“管理分析方案”。
2. 选择已有题目或新建题目。
3. 选择“从推荐模板创建”或“创建自定义维度”。
4. 填写本题希望观察的教学问题。
5. 填写一至三条“哪些表现算符合”和“哪些情况不算”。
6. 接受默认结果文字和教学建议；需要时再修改“可能出现”“明显出现”及其建议。
7. 系统在后台选择分析方式、必要信号、最低观察机会和聚合策略。
8. 系统执行完整性、信号兼容性和措辞风险校验，并用普通语言解释将如何分析。
9. 教师使用三至五个已脱敏历史会话预览结果，逐条选择“符合预期”“不符合”或“不确定”。
10. 系统根据反馈提示修改定义或选择更合适的模板，不自动偷偷改动已确认的教学含义。
11. 教师确认后发布 `pilot` 版本。
12. 研究验证后台完成独立校准和盲测后，研究管理员才可将版本标记为 `approved`。

没有足够历史会话时，教师可以使用模板自带的合成示例或完成一次示例监控。此时允许发布 `pilot`，但标记 `preview_status=pending_real_samples`；收集到真实会话后必须再次预览，且合成示例不能进入正式效度统计。

### 6.2 学生采集

1. 侧边栏显示当前题目和方案版本。
2. 学生点击“开始监控”。
3. 系统创建会话并返回 `session_id`。
4. 前端使用单调递增序号和唯一事件 ID 持续上传客观行为片段。
5. 学生点击“停止监控”。
6. 前端刷新剩余队列并提交最后连续序号，然后调用会话结束接口。
7. 浏览器异常关闭时，系统保留未完成会话，并由超时策略标记为 `abandoned`，不生成正式结论。

### 6.3 分析与查看

1. 后端校验事件连续性、采集器版本和必要信号覆盖率。
2. 特征提取器计算客观指标和每个维度的证据状态。
3. 后端执行规则层；仅为可计算的 `llm_evidence` 和 `hybrid` 维度创建 AI 分析请求。
4. 界面使用逐步退避的轮询策略获取任务状态。
5. 默认界面展示教师可理解的结论、证据和数据完整性；技术来源折叠在“分析详情”中。
6. 教师展开某个维度查看证据事件及代码差异摘要。
7. 教师可以接受或修正结果；修正不覆盖原始分析。

## 7. 界面设计

### 7.1 侧边栏

侧边栏只保留当前会话相关操作：

1. 当前题目。
2. 当前分析方案及版本。
3. 监控状态。
4. 开始或停止按钮。
5. 采集事件数量。
6. 分析任务进度。
7. 最新分析结果摘要。

文件列表移动到“高级数据”折叠区，不再作为主要结果。

### 7.2 首次使用空状态

首次打开时展示：

- 这个工具采集什么。
- 它能回答哪些教学问题。
- 需要先完成什么配置。
- 数据是否会发送给外部模型。
- “使用推荐模板创建方案”和“开始一次示例监控”两个入口。

### 7.3 引导式方案编辑器

方案编辑器在 JupyterLab 主区域打开，不塞进狭窄侧边栏。

普通教师默认看到：

- 创建方式：推荐模板或完全自定义。
- 名称：必填，1–50 个字符。
- 教学问题：必填，例如“学生失败后是否进行了有效验证？”。
- 符合表现：至少一条，使用普通语言。
- 排除情况：模板自动给出常见选项；完全自定义时至少填写一条，或明确选择“暂无已知排除情况”并在预览时重点检查误判。
- 结果说明：默认提供“未发现明显证据”“可能出现”“明显出现”。
- 教学建议：模板按结果预置，教师可以选择保留、修改或关闭。
- 可选正例和反例。

表单自动保存草稿。离开页面后可以继续编辑；普通教师完成模板方案时，不会被要求进入第二个技术配置页面。

默认三个用户结果在内部映射为：

- “未发现明显证据” → `decision.final_evidence_status=not_observed`、`decision.final_level_code=null`。
- “可能出现” → `decision.final_evidence_status=observed`、`decision.final_level_code=possible`。
- “明显出现” → `decision.final_evidence_status=observed`、`decision.final_level_code=clear`。

具有高级权限的用户可以增加行为等级，但不能删除证据不足和不可计算两种系统状态。

系统生成只读的“分析方法摘要”，例如：

> 系统将结合主动停顿、重复修改和后续运行验证进行判断；页面离开和程序运行等待不计入主动停顿。

普通教师看到的一个完整维度示例为：

- 名称：失败后是否继续验证。
- 教学问题：学生运行失败后，是否修改相关代码并再次运行？
- 符合表现：失败后修改报错附近代码，并在修改后再次运行。
- 排除情况：仅修改注释或运行与错误无关的 Cell 不计入。
- 结果建议：可能出现时查看证据并询问调试思路；明显出现时安排一次“修改后立即验证”的短练习。

普通界面不出现 `rule`、`llm_evidence`、`hybrid`、信号编码、权重、阈值、Kappa 或聚合算法名称。

发布前的质量检查必须阻止以下情况：

- 使用人格、能力或心理诊断式名称。
- 把“证据不足”写入最低行为等级定义。
- 引用当前采集器无法提供的信号。
- 阈值不单调、分母不明确或没有最低观察机会。
- `knowledge_inference` 没有题目测试或其他充分证据来源却启用自动判断。

### 7.4 高级设置

高级设置默认折叠，并在展开前提示“修改后需要重新校准，方案只能作为试点使用”。研究管理员或具有高级权限的教师可以查看：

- 自动生成的稳定编码和维度类型。
- 分析模式与模板来源。
- 必要和可选信号。
- 最低观察机会。
- 规则条件及校准状态。
- 阶段聚合策略。

普通教师无需进入高级设置即可完成方案创建。高级设置不允许执行任意脚本，也不允许绕过信号白名单。

### 7.5 研究验证后台

研究验证后台独立于普通方案编辑器，展示：

- 开发集、校准集和锁定盲测集。
- 各等级样本分布。
- 教师间一致性。
- 系统与裁决标签一致性。
- 证据—结论支持率。
- 规则或模型版本变化造成的验证失效。
- `pilot`、`approved` 和 `retired` 状态操作。

### 7.6 结果页

每个维度显示：

- 维度名称。
- 普通语言结论：“未发现明显证据”“可能出现”“明显出现”“数据不足”“当前记录无法分析”或“需要教师复核”。
- 证据数量。
- 一句话解释。
- 展开后的事件时间、行为类型和代码差异摘要。
- 数据覆盖率和缺失原因。
- 由方案预置的“下一步建议”；教师可以关闭，不把建议当作成绩或处分依据。
- 教师修正入口。

“分析详情”折叠区显示规则等级、AI 等级、模型自评置信度、融合理由，以及模型、提示词、信号字典和分析流水线版本。未经校准的数值支持度不显示给普通教师。

下一步建议来自当前方案快照中的模板或教师配置，不由 AI 根据学生身份、历史成绩或推测出的心理特征临时生成。修改建议文字不改变维度判定含义，也不使规则校准失效。

页面顶部显示：

- 会话状态。
- 成功维度数。
- 待复核维度数。
- 证据不足或不可计算的维度数。
- 失败维度数。
- 方案版本。

## 8. 数据模型

### 8.1 方案模型

```json
{
  "schema_version": 3,
  "profile_id": "uuid",
  "problem_id": "average-debug",
  "version": 1,
  "status": "published",
  "deployment_status": "pilot",
  "preview_status": "completed",
  "title": "平均分调试题分析方案",
  "configuration_mode": "guided",
  "signal_dictionary_version": 1,
  "signal_dictionary_hash": "sha256",
  "validation_report_id": null,
  "problem_context": {
    "statement": "计算一组成绩的平均值并修复越界错误",
    "expected_knowledge_points": ["循环边界", "列表索引", "函数定义"],
    "common_errors": ["range 上界错误", "空列表除零"]
  },
  "dimensions": [
    {
      "code": "ACTIVE_PAUSE_WITHOUT_VALIDATION",
      "name": "主动停顿后缺少验证",
      "question": "学生主动停顿后是否缺少及时的运行验证？",
      "configuration_source": "template",
      "template_ref": {
        "template_id": "active-pause-without-validation",
        "version": 1,
        "compatibility_hash": "sha256"
      },
      "dimension_type": "behavioral_inference",
      "directionality": "negative",
      "definition": "在有效编辑时段内出现较长主动停顿，之后继续小范围修改但没有及时运行验证",
      "evidence_criteria": [
        {
          "id": "support-1",
          "direction": "support",
          "statement": "停顿后重复修改相近内容且没有及时运行验证",
          "rule_condition_ids": ["condition-1", "condition-3"]
        },
        {
          "id": "exclude-1",
          "direction": "exclude",
          "statement": "程序运行等待、页面离开或停顿后及时运行不计入",
          "rule_condition_ids": ["condition-2"]
        }
      ],
      "levels": [
        {
          "code": "possible",
          "name": "可能出现",
          "definition": "存在相关行为证据，但范围或持续性有限"
        },
        {
          "code": "clear",
          "name": "明显出现",
          "definition": "在多个有效阶段持续出现，并明显缺少后续验证"
        }
      ],
      "teaching_actions": {
        "not_observed": "继续常规观察，无需仅凭本次记录进行额外干预",
        "possible": "结合证据片段询问学生停顿后的思路，并提醒先运行验证再继续改写",
        "clear": "安排一次短时调试练习，要求学生明确记录每次修改后的验证动作"
      },
      "analysis_config": {
        "mode": "hybrid",
        "required_signal_refs": [
          "valid_observation_duration_ms",
          "edit_event_count",
          "run_opportunity_count",
          "active_idle_count"
        ],
        "optional_signal_refs": [
          "verification_run_after_idle_ratio",
          "repeated_edit_count"
        ],
        "minimum_observation": {
          "valid_observation_duration_ms": 300000,
          "edit_event_count": 5,
          "run_opportunity_count": 1
        },
        "rule_policy": {
          "calibration_status": "validated",
          "calibration_id": "rule-calibration-uuid",
          "conditions": [
            {
              "id": "condition-1",
              "criterion_id": "support-1",
              "signal": "active_idle_count",
              "scope": "stage",
              "operator": "gte",
              "value": 2
            },
            {
              "id": "condition-2",
              "criterion_id": "exclude-1",
              "signal": "verification_run_after_idle_ratio",
              "scope": "stage",
              "operator": "gte",
              "value": 0.8
            },
            {
              "id": "condition-3",
              "criterion_id": "support-1",
              "signal": "repeated_edit_count",
              "scope": "stage",
              "operator": "gte",
              "value": 3
            }
          ]
        },
        "aggregation": {
          "strategy": "prevalence",
          "min_stage_ratio": 0.5
        }
      }
    }
  ],
  "created_at": "ISO-8601",
  "published_at": "ISO-8601",
  "content_hash": "sha256"
}
```

`deployment_status` 允许：

- `pilot`：只用于预览、小规模试点和效度收集。
- `approved`：达到第 20 节效度门槛，可用于正式教学观察。
- `retired`：不允许绑定新会话，历史结果继续可读。

发布版本不可变。`deployment_status`、`preview_status` 和 `validation_report_id` 是读取方案时合并展示的投影字段，实际保存在独立审计记录中，不写回版本文件，也不参与方案内容哈希。`minimum_observation` 中的数值均为包含边界的最小值。

`preview_status` 允许 `not_started`、`pending_real_samples` 和 `completed`。合成示例只能把状态推进到 `pending_real_samples`；至少完成一次真实会话预览后才能变为 `completed`。

`question`、`evidence_criteria[].statement`、`levels` 和可选的 `teaching_actions` 是普通教师可修改内容。`dimension_type`、`directionality` 和 `analysis_config` 由模板或系统建议生成，在引导模式中只读。`required_signal_refs` 与 `optional_signal_refs` 为证据覆盖计算提供机器可读边界。

完全自定义维度使用 `template_ref=null`、`configuration_source=custom`、`analysis_config.mode=llm_evidence` 和 `rule_policy=null`。系统可以建议模板，但必须由教师明确确认后才能改变配置来源。

模板校准只绑定模板锁定的证据含义、等级结构和内部配置，其规范哈希写入 `compatibility_hash`。教师可以修改标题、不改变含义的展示文字和 `teaching_actions`；一旦增删证据标准、改变等级含义或修改内部配置，系统把维度转换为 `custom`，使模板校准失效并保持 `pilot`。

### 8.2 允许的规则信号

MVP 只允许选择系统内置信号。以下是版本 1 的最小信号契约；实现时以相同内容生成独立 JSON Schema 和信号字典文件。

| 信号 | 单位/类型 | 范围与聚合 | 缺失及零值语义 |
|---|---|---|---|
| `valid_observation_duration_ms` | 毫秒 | 页面可见、监控开启且内核非运行等待期间的累计时长 | 生命周期事件缺失时为 `null` |
| `active_idle_total_duration_ms` | 毫秒 | 有效观察期间，连续 2 秒无编辑、运行或导航行为的主动停顿累计时长 | 数据完整且未发生为 `0` |
| `active_idle_max_duration_ms` | 毫秒 | 单次主动停顿最大值 | 数据完整且未发生为 `0` |
| `active_idle_count` | 整数 | 主动停顿事件数 | 数据完整且未发生为 `0` |
| `edit_event_count` | 整数 | 被编辑器确认的代码变更事件数 | 编辑器事件源缺失时为 `null` |
| `delete_event_count` | 整数 | 包含字符删除的代码变更事件数 | 编辑器事件源缺失时为 `null` |
| `delete_edit_ratio` | 0–1 | `delete_event_count / edit_event_count` | 分母为 0 时为 `null` |
| `paste_ratio` | 0–1 | 粘贴插入字符数 / 全部插入字符数 | 分母为 0 时为 `null` |
| `run_count` | 整数 | Notebook Cell 或 Python 文件运行次数 | 运行事件源缺失时为 `null` |
| `run_opportunity_count` | 整数 | 自上次运行后代码发生实质变化、具备再次验证机会的阶段数 | 无编辑时为 `0`；阶段算法不可用时为 `null` |
| `failed_run_count` | 整数 | 产生 Jupyter error 输出或执行异常的运行次数 | 运行输出不完整时为 `null` |
| `time_to_first_success_ms` | 毫秒 | 会话开始到首次“无执行错误运行”的时长，不代表答案正确 | 没有无错误运行时为 `null` |
| `failure_edit_success_chain_count` | 整数 | 失败运行后编辑相关代码并出现无执行错误运行的链条数 | 必要事件不完整时为 `null` |
| `repeated_edit_count` | 整数 | 同一区域删除、恢复或相似小范围改写的次数 | 快照不足时为 `null` |
| `error_type_change_count` | 整数 | 相邻失败运行的异常类型发生变化的次数 | 少于两次失败运行时为 `0` |
| `verification_run_after_idle_ratio` | 0–1 | 主动停顿后 120 秒内发生运行的次数 / 可验证主动停顿次数 | 分母为 0 时为 `null` |

2 秒主动停顿基线、120 秒验证窗口、相关代码区域识别和重复改写相似度都属于信号字典版本内容；改变任一参数必须增加信号字典版本，不能覆盖版本 1。

允许的操作符：

- `eq`
- `gte`
- `lte`
- `gt`
- `lt`

教师不能通过界面上传或执行任意脚本。

所有比例信号的定义必须声明分子、分母和零分母结果。除表中明确允许的完整数据零值外，采集不完整产生 `null`，不能伪装成 `0`。普通教师只看到中文解释，不看到信号编码。

### 8.3 会话模型

```json
{
  "schema_version": 3,
  "session_id": "uuid",
  "problem_id": "average-debug",
  "profile_id": "uuid",
  "profile_version": 1,
  "profile_content_hash": "sha256",
  "profile_snapshot_path": "sessions/<session_id>/profile.json",
  "signal_dictionary_version": 1,
  "signal_dictionary_hash": "sha256",
  "collector_version": "0.1.0",
  "privacy_notice_version": 1,
  "started_at": "ISO-8601",
  "ended_at": null,
  "status": "collecting",
  "last_contiguous_sequence": 42,
  "received_event_count": 42,
  "abandon_reason": null,
  "recovered_at": null,
  "recovered_by": null,
  "recover_reason": null
}
```

每个上传片段包含 `segment_id`、`first_sequence`、`last_sequence`、事件列表和内容哈希；每个事件包含会话内唯一的 `event_id`。同一片段或事件重复上传时返回原接收结果，不重复写入。

### 8.4 维度结果模型

```json
{
  "schema_version": 3,
  "analysis_id": "uuid",
  "job_id": "uuid",
  "attempt_id": "uuid",
  "session_id": "uuid",
  "dimension_code": "ACTIVE_PAUSE_WITHOUT_VALIDATION",
  "decision": {
    "status": "resolved",
    "final_evidence_status": "observed",
    "final_level_code": "possible",
    "display_label": "可能出现",
    "source": "hybrid_agreement"
  },
  "data_quality": {
    "signal_coverage": 1.0,
    "observation_opportunities": 4,
    "missing_required_signals": [],
    "missing_optional_signals": [],
    "reason": null
  },
  "rule_result": {
    "calibration_status": "validated",
    "calibration_id": "rule-calibration-uuid",
    "evidence_status": "observed",
    "level_code": "possible",
    "calibrated_score": 0.61,
    "matched_condition_ids": ["condition-1"],
    "matched_criterion_ids": ["support-1"],
    "evidence_event_ids": ["session-uuid:3", "session-uuid:5"]
  },
  "ai_result": {
    "evidence_status": "observed",
    "level_code": "possible",
    "confidence": 0.78,
    "evidence_event_ids": ["session-uuid:3"],
    "evidence_claims": [
      {
        "event_id": "session-uuid:3",
        "criterion_id": "support-1",
        "direction": "support",
        "claim": "该事件位于主动停顿后，随后继续编辑但没有立即运行"
      }
    ],
    "explanation": "多个有效阶段出现主动停顿，且停顿后没有及时运行验证"
  },
  "fusion_result": {
    "status": "agreed",
    "reason_code": "same_evidence_status_and_level",
    "reason": "规则和AI均判断为可能出现",
    "aggregation_strategy": "prevalence",
    "aggregation_parameters": {
      "min_stage_ratio": 0.5
    }
  },
  "provenance": {
    "profile_id": "uuid",
    "profile_version": 1,
    "profile_content_hash": "sha256",
    "template_id": "active-pause-without-validation",
    "template_version": 1,
    "template_compatibility_hash": "sha256",
    "calibration_id": "rule-calibration-uuid",
    "signal_dictionary_hash": "sha256",
    "input_snapshot_hash": "sha256",
    "analysis_pipeline_version": "1",
    "feature_extractor_version": "1",
    "stage_segmenter_version": "1",
    "model_provider": "configured-provider",
    "model_name": "configured-model",
    "model_version": "provider-version",
    "model_parameters": {
      "temperature": 0
    },
    "prompt_version": "3",
    "prompt_content_hash": "sha256",
    "provider_request_id": "provider-request-id",
    "raw_response_hash": "sha256",
    "result_validator_version": "2"
  },
  "review": {
    "status": "unreviewed",
    "corrected_decision_status": null,
    "corrected_evidence_status": null,
    "corrected_level_code": null,
    "corrected_evidence_event_ids": [],
    "reason_code": null,
    "comment": null,
    "reviewer_subject_id": null,
    "reviewed_at": null,
    "revision": 0,
    "original_result_hash": "sha256"
  }
}
```

当 `decision.status` 为 `needs_review`、`partial` 或 `failed` 时，`final_evidence_status` 和 `final_level_code` 可以为 `null`。当最终证据状态为 `insufficient_evidence`、`not_computable` 或 `not_observed` 时，`final_level_code` 必须为 `null`。

不适用于当前分析模式的 `rule_result` 或 `ai_result` 保存为 `null`。单一来源模式的 `fusion_result.status=single_source`。未校准规则的 `calibrated_score` 必须为 `null`，不能参与自动融合。界面和导出中的有效结果取最新教师修正；没有教师修正时才取 `decision`，且不得删除或覆盖原始结果。

### 8.5 任务与执行记录

一个分析任务可以有多次不可变执行：

```json
{
  "job_id": "uuid",
  "session_id": "uuid",
  "status": "partial",
  "active_attempt_id": "attempt-2",
  "attempt_ids": ["attempt-1", "attempt-2"],
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

每个 attempt 保存自己的输入清单哈希、开始与结束时间、状态、错误代码、重试原因、原始响应哈希和生成的 `analysis_id`。重试只追加 attempt；job 的聚合状态可以变化，但旧 attempt 永不修改。

### 8.6 规则校准工件

校准工件至少包含：

- `calibration_id` 和创建时间。
- 模板版本及 `compatibility_hash`。
- 信号字典、特征提取器和校准算法版本。
- 开发集与校准集清单哈希。
- 条件系数、连续分数定义和等级边界。
- 各类别样本数、召回率、误判率和校准指标。
- 适用题目范围和失效条件。

校准工件不可编辑。任何输入版本或兼容哈希不一致时，`calibration_status` 必须变为 `invalidated`，不能继续沿用旧分数。

## 9. 存储设计

### 9.1 本地 MVP

在日志根目录下增加：

```text
config/
  problems/
  signal_dictionary/
    v1.json
  dimension_templates/
    <template_id>/
      v1.json
  rule_calibrations/
    <calibration_id>.json
  dimension_profiles/
    <profile_id>/
      draft.json
      v1.json
      v2.json
sessions/
  <session_id>/
    session.json
    profile.json
    signal_dictionary.json
    raw_events.jsonl
    features.json
jobs/
  <job_id>/
    job.json
    attempts/
      <attempt_id>.json
analyses/
  <analysis_id>/
    input_manifest.json
    dimension_results.json
    review_history.jsonl
audit/
  profile_deployment.jsonl
validation/
  <validation_report_id>/
    manifest.json
    metrics.json
```

要求：

1. 配置目录权限为当前用户可读写。
2. 已发布版本只读，不原地修改。
3. 所有 JSON 配置使用临时文件加 `os.replace` 原子写入。
4. 会话开始时复制方案和信号字典快照，并保存内容哈希。
5. API Key 不写入方案文件。
6. 内容哈希使用 UTF-8、Unicode NFC、对象键排序、无无效空白的规范 JSON 后计算 SHA-256；计算方案哈希前排除 `content_hash`、`deployment_status`、`preview_status` 和 `validation_report_id`。
7. 读取分析输入前重新计算哈希。哈希不一致时任务以 `input_integrity_error` 失败，禁止继续分析。
8. 文件级“只读”只代表应用层约束，不等同于操作系统防篡改；正式部署使用数据库不可变记录和独立审计日志。
9. 原始结果不可覆盖。教师每次修正追加一条带修订号的记录。
10. `input_snapshot_hash` 由按连续序号排序的事件 ID 与事件内容哈希、方案哈希、信号字典哈希和采集器版本共同计算。
11. 原始事件追加写入使用会话级文件锁；每条记录带长度和内容哈希，启动时截断最后一条未完整写入的记录并保留恢复审计。
12. 所有路径参数只接受服务端生成的 UUID 或经过白名单验证的稳定编码，拒绝路径分隔符、`..` 和符号链接越界。
13. 每次 AI 调用保存脱敏后的提示词快照、提示词哈希、模型参数和原始响应哈希；包含学生代码的快照仍受删除和保留期限约束。

### 9.2 未来多人部署

多人部署时将方案、作业和教师修正迁移到数据库；原始行为数据按用户、课程和题目隔离。MVP 文件模型保持稳定，便于后续迁移。

## 10. API 设计

所有接口位于现有 `/myextension` 命名空间并要求 Jupyter 身份认证。

所有请求和响应使用带 `schema_version` 的 JSON；响应包含 `request_id`，时间统一为带时区的 ISO-8601。错误响应结构固定为 `code`、`message`、`retryable` 和可选 `details`。接口至少区分 `400`、`401`、`403`、`404`、`409`、`413`、`422`、`429` 和 `5xx`。

单片段请求默认限制为 1 MiB，部署配置可以在 256 KiB–5 MiB 内调整。超限返回 `413`，前端拆分片段但保持事件边界和连续序号。正式部署必须在服务端对方案、复核和验证报告接口执行教师权限校验。

实施前必须把本节接口固化为机器可读的 OpenAPI 和 JSON Schema。仅有端点名称不视为接口完成；请求、成功响应、错误响应、字段条件、枚举和大小限制都必须进入契约测试。

### 10.1 题目与方案

- `GET /problems`
- `POST /problems`
- `GET /dimension-templates`
- `GET /dimension-templates/<template_id>/versions/<version>`
- `GET /dimension-profiles?problem_id=<id>`
- `POST /dimension-profiles`
- `PUT /dimension-profiles/<profile_id>/draft`
- `POST /dimension-profiles/<profile_id>/suggest-config`
- `POST /dimension-profiles/<profile_id>/publish`
- `GET /dimension-profiles/<profile_id>/versions/<version>`
- `POST /dimension-profiles/<profile_id>/versions/<version>/preview`
- `POST /dimension-profiles/<profile_id>/versions/<version>/validation-reports`
- `PATCH /dimension-profiles/<profile_id>/versions/<version>/deployment-status`

发布接口：

1. 校验字段。
2. 生成版本号。
3. 计算内容哈希。
4. 固化信号字典版本和哈希。
5. 写入不可变版本文件，并在独立审计记录中创建初始 `deployment_status=pilot`。
6. 返回 `profile_id`、`version`、`content_hash` 和状态投影。

`suggest-config` 根据教师填写的教学问题和正反例推荐模板及内部配置，但只写入草稿的“待确认建议”。教师确认前不得改变维度含义；完全自定义维度允许拒绝全部建议并使用 `llm_evidence`。

只有存在通过的验证报告时，部署状态才能从 `pilot` 变为 `approved`。状态变更必须记录操作者、时间、验证报告和原状态。

允许的部署状态转换为 `pilot → approved`、`approved → pilot`、`pilot → retired` 和 `approved → retired`；`retired` 是终态。验证数据被删除或依赖版本失效时，服务端自动执行 `approved → pilot` 并记录原因。再次启用已退役方案必须创建新版本。

### 10.2 会话

- `POST /sessions/start`
- `POST /sessions/<session_id>/segments`
- `POST /sessions/<session_id>/finalize`
- `POST /sessions/<session_id>/abandon`
- `POST /sessions/<session_id>/recover`
- `GET /sessions/<session_id>`

片段上传以 `session_id + segment_id + content_hash` 幂等，事件以 `session_id + event_id` 去重。相同 ID 携带不同哈希时返回 `409 Conflict`，不得覆盖原事件。

`finalize` 请求必须携带前端的 `last_sequence`。服务端仅在 `1..last_sequence` 连续且哈希校验通过时结束会话；否则返回缺失区间，前端补传后重试。`finalize` 必须幂等，重复调用返回同一个分析任务，不创建重复任务。

超过默认 30 分钟没有活动的 `collecting` 会话转为 `abandoned`。管理员可配置超时，但变更只影响新会话。`recover` 必须记录操作者和原因，成功后状态回到 `collecting`；随后仍要满足连续序号检查才能结束。

### 10.3 分析任务

- `GET /analysis-jobs/<job_id>`
- `POST /analysis-jobs/<job_id>/retry`
- `GET /analysis-jobs/<job_id>/attempts`
- `POST /sessions/<session_id>/reanalyze`
- `GET /sessions/<session_id>/analysis`
- `PATCH /sessions/<session_id>/analysis/<dimension_code>/review`

`reanalyze` 必须显式传入已发布的 `profile_id` 和 `profile_version`。它创建新的分析任务并保留旧结果。

任务幂等键由以下内容的规范哈希组成：

```text
session_id
+ input_snapshot_hash
+ profile_id
+ profile_version
+ profile_content_hash
+ signal_dictionary_hash
+ analysis_pipeline_version
+ feature_extractor_version
+ stage_segmenter_version
+ model_provider
+ model_name
+ model_version
+ model_parameters_hash
+ prompt_version
+ prompt_content_hash
+ result_validator_version
```

幂等键相同的重复请求返回已有任务；任一输入版本变化都创建新任务。

复核接口必须携带当前 `review.revision`。修订号过期时返回 `409 Conflict`，避免两次教师修改互相覆盖。

`retry` 在同一 job 下创建新的 `attempt_id`；历史 attempt 只读。任务页面默认展示最新 attempt，并允许研究管理员查看每次输入版本、错误原因和部分结果。

## 11. 前端架构

现有 `src/index.ts` 职责过多。重构为：

```text
src/
  index.ts
  ui/
    behaviorAnalysisSidebar.ts
    guidedProfileEditor.ts
    advancedProfileSettings.ts
    researchValidationView.ts
    analysisResultView.ts
    firstRunView.ts
  services/
    templateApi.ts
    profileApi.ts
    sessionApi.ts
    analysisApi.ts
    validationApi.ts
  models/
    dimensionProfile.ts
    analysisResult.ts
```

现有采集代码继续保留：

- `notebookMonitor.ts`
- `pythonFileMonitor.ts`
- `editState.ts`
- `behaviorTimelineBuilder.ts`

修改 `behaviorEventUploader.ts`：

1. 不再自行决定完整会话语义。
2. 接收后端创建的 `session_id`。
3. 为事件生成会话内唯一 `event_id`，为片段生成 `segment_id`、连续序号范围和内容哈希。
4. 上传时携带会话 ID，并安全重试相同片段。
5. 停止时等待队列清空并核对后端最后连续序号。
6. 成功清空后携带 `last_sequence` 调用 `finalize`。
7. 上传失败时保留队列、显示缺失片段并允许继续重试。

## 12. 后端架构

新增模块：

```text
myextension/
  dimension_profile_store.py
  dimension_template_store.py
  profile_validator.py
  config_suggester.py
  signal_dictionary.py
  feature_extractor.py
  evidence_coverage.py
  rule_engine.py
  calibration_store.py
  fusion_engine.py
  analysis_job_store.py
  analysis_worker.py
  analysis_result_validator.py
  review_store.py
  validity_report_store.py
```

职责：

- `dimension_profile_store.py`：草稿、发布版本、读取和哈希。
- `dimension_template_store.py`：读取不可变模板及其默认内部配置。
- `profile_validator.py`：字段长度、编码、等级、信号和操作符校验。
- `config_suggester.py`：把教师的教学问题和正反例映射为待确认模板建议，不直接发布。
- `signal_dictionary.py`：提供带版本的信号语义、采集器兼容性和最低观察机会。
- `feature_extractor.py`：从完整会话生成客观指标。
- `evidence_coverage.py`：先判定不可计算、证据不足或可分析，再汇总规则与 AI 的候选证据状态。
- `rule_engine.py`：记录候选规则命中，并仅通过匹配的校准工件生成连续分数和等级。
- `calibration_store.py`：保存规则校准工件、数据集哈希和适用版本。
- `fusion_engine.py`：按确定性策略合并规则、AI和阶段结果。
- `analysis_job_store.py`：持久化任务状态和重试次数。
- `analysis_worker.py`：单进程有界队列、恢复待处理任务、执行分析。
- `analysis_result_validator.py`：严格校验模型输出。
- `review_store.py`：追加保存教师复核历史并执行乐观并发控制。
- `validity_report_store.py`：保存盲标、对照指标和部署状态依据。

修改现有模块：

- `routes.py`：注册题目、方案、会话、任务和复核接口。
- `llm_labeler.py`：从固定目录切换为动态方案分析。
- `behavior_log_store.py`：记录会话与方案元数据，保持旧文件兼容。

## 13. 特征提取与规则层

规则层只输出客观指标和规则匹配结果，不直接做心理诊断。

规则执行前先由 `evidence_coverage.py` 判断必要信号是否可用以及最低观察机会是否满足：

1. 采集器不支持必要信号：`not_computable`。
2. 支持信号但观察机会不足：`insufficient_evidence`。
3. 数据完整且满足最低观察机会：继续执行规则。

规则条件先产生可解释的“命中/未命中/不可计算”记录。教师不手工填写权重，也不由系统临时拼接支持分数。

规则政策分为：

- `uncalibrated`：只提供命中证据，`calibrated_score` 和规则等级为 `null`，不得参与自动融合。
- `validated`：引用不可变 `calibration_id`。校准工件包含同一权重尺度、等级边界、适用模板版本、开发集和校准集哈希及校准指标。
- `invalidated`：模板、信号语义、规则条件或等级定义变化后自动失效，行为与 `uncalibrated` 相同。

缺失规则信号不得作为“未命中”参与计算。必要信号缺失时使用证据状态中止本维度；非必要信号缺失时降低 `signal_coverage` 并在结果中说明。

只有验证报告证明规则在目标题目上具有足够覆盖率和召回率时，规则层才能单独输出 `not_observed`。否则“没有命中支持规则”只能表示规则未发现证据，不能自动证明行为没有发生。

不同分析模式的最终结果生成方式：

- `rule`：只允许使用 `validated` 规则校准工件，直接生成规则等级和证据，不调用 AI。
- `llm_evidence`：只在覆盖门槛为 `sufficient_for_analysis` 时调用 AI，使用通过结构和证据校验的 AI 证据状态与等级；完全自定义维度默认使用此模式。
- `hybrid`：只在规则为 `validated` 时自动融合；始终分别保存规则等级和 AI 等级。

混合模式采用以下确定性策略，AI 不得静默覆盖规则：

- 两者均为 `not_observed`：`decision.status=resolved`，最终证据状态为 `not_observed`，最终等级为 `null`。
- 两者均为 `observed` 且等级一致：`decision.status=resolved`，自动形成最终等级。
- 一方为 `observed`、另一方为 `not_observed`：`decision.status=needs_review`，两个最终字段均为 `null`。
- 两者等级不一致：`decision.status=needs_review`，最终等级为 `null`，界面并列显示候选等级。
- 任一结果缺失或校验失败：`decision.status=partial`，保留有效中间结果，但两个最终字段均为 `null`。
- 规则未校准或已失效：不执行混合自动决策，退化为 `llm_evidence` 单一来源并保持 `pilot`。

普通教师界面不展示规则分数或合成支持度。高级详情可以显示已校准规则的内部连续分数，但必须同时显示校准版本，且不得把它解释为正确概率。

### 13.1 停顿

- 只统计页面可见、监控开启且不是代码运行等待的停顿。
- 页面离开不计入主动停顿。
- 保存每次停顿的开始时间、结束时间和上下文。

### 13.2 重复修改

- 比较同一 Cell 或函数连续代码快照。
- 识别删除后恢复近似内容。
- 识别同一区域重复的小范围修改。
- 输出重复次数和相关事件 ID。

### 13.3 调试链

调试链定义为：

```text
运行失败 → 编辑相关代码 → 再次运行
```

若再次运行成功，标记为有效恢复链；若错误类型发生变化，记录错误转移；不直接把所有失败后编辑都判定为有效调试。

### 13.4 正确性

没有题目测试用例时，只能输出“运行成功”，不能输出“答案正确”或“已掌握”。

存在教师配置的测试用例时，测试必须在隔离环境中执行。测试执行隔离不纳入本次 MVP。

### 13.5 阶段聚合

阶段切分算法和版本必须记录在分析来源信息中。只聚合证据状态为 `observed` 或 `not_observed` 的有效阶段，其他阶段单独计入数据质量。普通教师不选择聚合算法；模板给出默认值，只有高级设置可以更改并触发重新校准。

模板或高级内部配置必须指定一种聚合策略：

- `ever_occurred`：取有效阶段中的最高等级，只允许用于“出现一次即有教学意义”的维度；编辑器显示长度偏差警告。
- `duration_weighted`：只对经过校准的连续分数按有效观察时长加权，然后再映射等级；不得直接平均“可能出现”“明显出现”等有序标签。
- `prevalence`：计算达到各等级的有效阶段比例，选择满足 `aggregation.min_stage_ratio` 的最高等级。
- `latest_stage`：选择最后一个有效阶段的等级，同时展示此前阶段分布。
- `majority`：选择有效阶段众数；并列时选择较低等级。
- `trend`：至少需要三个有效阶段。`directionality=positive|negative` 时才允许使用“改善/恶化”，`neutral` 维度只显示“上升/稳定/下降”；不足三个阶段时为 `insufficient_evidence`。

不得使用证据事件数量作为置信度权重。事件多只代表日志更密集，不代表判断更可靠。

## 14. AI 分析设计

### 14.1 输入

模型输入包含：

1. 系统约束。
2. 方案内容快照。
3. 题目上下文。
4. 客观特征摘要。
5. 按时间排序的紧凑行为片段。
6. 严格输出 Schema。
7. 每条候选证据的事件 ID、事件类型、相对时间和经过截断的差异摘要。

学生代码和注释被视为不可信数据。系统提示必须明确禁止执行或遵循代码中的指令。

模型输入只包含 `llm_evidence` 和 `hybrid` 维度。`rule` 维度由规则层直接生成，不要求模型重复判断。

MVP 每个方案最多启用 10 个 AI 维度，每个维度最多发送 20 条候选证据，每段代码差异摘要最多 300 个字符。后端在调用前估算输入规模；超过模型上下文预算时按第 15.4 节切分，不静默截断必要证据。

### 14.2 输出约束

模型必须为本次请求中的每个 `llm_evidence` 或 `hybrid` 维度返回且仅返回一个结果。结果必须包含 `observed` 或 `not_observed` 证据状态；只有 `observed` 可以返回行为等级。每条证据除事件 ID 外还要引用 `evidence_criteria.id`，说明它支持或排除哪项教师定义。最终分析文件由后端合并规则结果和模型结果，并覆盖方案中的全部维度。

禁止：

- 生成方案外维度。
- 生成方案外等级。
- 引用不存在的事件。
- 将页面离开当作主动停顿证据。
- 没有证据时给出高置信度。
- 把证据不足映射成最低行为等级。
- 根据代码注释中的指令改变系统约束。

### 14.3 响应校验

校验顺序：

1. JSON 语法。
2. 顶层结构。
3. 维度集合与本次请求完全一致。
4. 等级属于对应维度。
5. 置信度在 0–1。
6. 证据事件存在于当前会话。
7. 证据事件类型、时间范围和代码位置与对应证据声明一致。
8. 同一证据没有被用于互相矛盾的结论。
9. 解释长度不超过 500 字符。

响应按维度独立校验并立即保存有效维度。首次响应中的无效或缺失维度集中使用一次修复提示重试，不重复请求已经有效的维度。再次失败后，相应维度设置 `decision.status=partial`，保留其他有效结果并向教师提供“重试分析”操作；原始无效响应不得进入训练样本。

上述校验只能验证引用关系，不能证明证据在教学语义上必然支持结论。`pilot` 方案必须通过教师抽样复核评估这种“证据—结论一致性”。

## 15. 分析任务调度

### 15.1 创建时机

只有会话 `finalize` 后才创建正式分析任务。

采集过程中不再为每个上传批次调用 AI。

### 15.2 队列

MVP 使用单进程、单工作线程的有界队列：

- 同一会话最多一个活动任务。
- 队列状态持久化到文件。
- 服务重启后重新加载 `queued` 和 `running` 任务。
- 原 `running` 任务记录 `recovered_after_restart` 后重置为 `queued`。
- 本地模式启动时获取队列目录独占锁；发现另一个工作进程时拒绝启动第二个 worker。
- 多进程或多人部署必须改用支持事务和租约的外部任务队列。

### 15.3 超时与重试

- 单次模型请求超时：90 秒。
- 网络、超时和 5xx：最多重试两次。
- 重试间隔：2 秒、8 秒。
- 认证、权限和 Schema 配置错误不自动重试。
- 分析失败不影响原始行为日志。
- 未配置 AI 时，`rule` 维度照常生成；`llm_evidence` 和 `hybrid` 维度标记为待配置，任务状态为 `partial`。
- 单任务总执行时间上限为 5 分钟，达到上限后保留已完成维度并标记为 `partial`。

### 15.4 长会话

当完整会话超过模型输入限制时：

1. 先按带版本的编码阶段算法切分。
2. 每个阶段独立计算信号、证据状态、规则结果和 AI 结果。
3. 使用维度配置的聚合策略合并阶段结果。
4. 合并去重后的有效证据事件，并保留其来源阶段。
5. 分别保留各阶段 AI 模型自评和已校准规则分数，普通界面不合成为单一支持度。
6. 聚合只生成最终证据状态与等级；不可用阶段单独进入数据质量说明，不按证据数量加权。

不得按网络上传批次直接切分分析语义。

## 16. 错误处理与界面状态

界面不得再使用笼统的“读取失败”。

错误状态至少区分：

- 未配置 AI。
- 采集上传失败。
- 分析排队中。
- 分析运行中。
- 部分维度成功。
- 模型超时。
- 模型认证失败。
- 输出格式不合法。
- 会话事件序号不连续。
- 分析输入哈希不一致。
- 证据不足。
- 当前日志不可计算。
- 规则与 AI 结果冲突，等待教师复核。
- 结果文件读取失败。

每个状态显示下一步动作，例如“重新分析”“检查模型配置”或“查看原始记录”。

## 17. 隐私与安全

1. 监控默认关闭。
2. 开始前展示采集内容和外发说明。
3. API Key 只保存在服务端，不返回完整值。
4. 正式部署使用环境变量或密钥管理，不依赖明文配置文件。
5. 发送模型前移除真实用户身份和无关绝对路径。
6. 只发送分析所需代码快照或差异。
7. 开始监控前展示采集字段、使用目的、外部模型提供方、保留期限和撤回方式，并保存隐私说明版本与确认时间。
8. 日志提供明确保留期限、导出入口和删除入口；默认期限必须在部署配置中给出，不允许无限期。
9. 删除会话时同步删除原始事件、特征、模型输入、分析结果和教师修正；审计日志只保留不含内容的删除记录。
10. 调用外部模型前确认其数据保留和训练政策符合部署要求；能关闭训练或日志保留时必须关闭，并在界面展示实际配置。
11. 发送模型的数据使用会话级匿名标识，映射表不进入模型输入。
12. 正式多人部署必须在服务端校验教师和学生权限，不能只依赖界面隐藏按钮。
13. 模型请求、错误日志和调试日志不得记录 API Key、真实身份、完整绝对路径或未脱敏的完整代码。
14. 学生代码、注释、输出和错误文本都作为不可信输入处理，不能参与构造系统指令。
15. 隐私说明变更后，新会话必须重新确认；历史确认记录不可覆盖。
16. Windows 启动脚本不得关闭 Jupyter 身份认证。
17. 教师修正和模型原始结果分开存储，保留审计记录。
18. 正式部署只允许通过 HTTPS 访问，并对外部模型提供方使用 TLS；跨服务日志使用相同的脱敏规则。
19. 部署方必须记录处理学生数据的制度依据和责任人；课程参与具有强制性时，不能把形式上的“同意”作为唯一依据。
20. 允许的情况下提供不向外部模型发送代码的替代流程；无法提供时必须在开始前明确说明，且试点结果不得用于成绩、处分或自动决策。
21. 被删除会话必须从开发、校准和盲测清单中移除；引用该会话的校准工件或验证报告自动失效，重新计算前不能继续支持 `approved` 状态。

## 18. 兼容与迁移

1. 保留现有原始事件、时间线和 Markdown 输出。
2. 内置保留标识 `profile_id=builtin-default`、`profile_version=1`，映射现有固定标签目录。
3. 未绑定方案的旧会话标记为 `profile_id=builtin-default`。
4. 旧会话缺少新信号时，相应维度标记为 `not_computable`，禁止标记为 `not_observed`。
5. 新版聚合样本增加方案和证据字段，但不删除现有字段。
6. 旧的 `behavior-events` 接口保留一个版本周期，新界面使用新的会话接口。
7. 迁移过程不伪造事件 ID；无法生成稳定 ID 的旧事件使用“旧会话 ID + 原始文件哈希 + 原始行号”生成可复现标识。

## 19. 测试策略

### 19.1 后端单元测试

- 方案字段校验。
- 维度编码唯一性。
- 已发布版本不可修改。
- 内容哈希稳定性。
- 规范 JSON 在键顺序、空白和 Unicode 表示变化时哈希稳定。
- 方案或信号字典快照篡改后拒绝分析。
- 规则信号和操作符白名单。
- 信号单位、范围、零分母和缺失值语义。
- 特征提取。
- 调试链识别。
- 最低观察机会与四种证据状态。
- 必要和可选信号分别影响可计算状态与覆盖率。
- 未校准规则只输出命中证据，不输出等级或数值分数。
- 校准工件与模板、信号和等级版本严格匹配。
- 技术参数变化使校准工件自动失效。
- 非必要信号缺失时的覆盖率计算。
- 所有阶段聚合策略及并列规则。
- 规则与 AI 一致、存在性冲突、等级冲突和单侧失败对应正确的 `decision.status`。
- 未决状态的最终证据状态和等级为空。
- 模型输出拒绝未知维度。
- 模型输出拒绝不存在的事件证据。
- 模型输出拒绝证据类型、时间范围或代码位置不匹配。
- 单个无效维度不丢失其他有效维度。
- 超时重试分类。
- 会话 `finalize` 幂等。
- 非法状态转换被拒绝。
- 事件和片段重复上传不重复写入。

### 19.2 后端集成测试

- 创建草稿、发布和读取版本。
- 开始会话、上传片段、停止会话。
- 相同片段重复上传返回相同结果。
- 相同片段 ID 携带不同哈希时返回冲突。
- 序号缺失时结束失败，补传后结束成功。
- 浏览器异常退出后会话进入 `abandoned`。
- 废弃会话经审计恢复后可以补传和结束。
- 任务从排队到完成。
- 每次重试生成新 attempt，旧 attempt 结果不变。
- 服务重启后恢复队列。
- 第二个本地 worker 无法获取队列锁。
- 模型版本、分析流水线版本或输入快照变化时重分析创建新任务。
- 提示词快照、模型参数、提供方请求 ID 和原始响应哈希可追溯。
- 部分失败结果。
- 方案快照哈希漂移时拒绝分析。
- 教师修正追加保存，旧修订号产生冲突。
- 删除操作覆盖原始数据和所有派生数据。
- 删除验证数据中的会话后，相关校准工件和批准状态自动失效。
- 同一学生、同一题目尝试或派生会话跨开发集、校准集和盲测集时，验证任务被拒绝。
- 路径穿越、符号链接越界和非法稳定编码被拒绝。

### 19.3 前端测试

- 首次使用空状态。
- 推荐模板、完全自定义和样本预览流程。
- 引导模式不显示规则、信号、阈值和聚合算法。
- 系统建议必须经教师确认才写入草稿。
- 高级设置展开前显示校准失效警告。
- 开始前必须选择已发布方案。
- 监控状态切换。
- 停止后等待上传完成。
- 使用退避策略轮询任务状态。
- 不同错误类型显示不同动作。
- 结果卡片和证据展开。
- 默认结果使用普通语言，技术来源位于折叠详情中。
- 下一步教学建议来自方案快照，可关闭，且不会被误显示为 AI 对学生的自动处置建议。
- 区分未观察到、证据不足和不可计算。
- 并列显示规则等级、AI 等级和融合理由。
- 未通过验证的方案显示“试点”标识和用途限制。
- 研究验证后台不出现在普通教师和学生流程中。

### 19.4 端到端测试

测试路径：

1. 创建“调试策略”方案。
2. 教师从“调试链”模板创建维度，只填写教学问题和正反例。
3. 使用三至五个历史会话预览并发布试点版本。
4. 新建 Notebook。
5. 开始监控。
6. 输入带越界错误的代码。
7. 运行失败。
8. 修改并运行成功。
9. 停止监控并等待分析完成。
10. 验证默认页面只出现普通语言结果和教师定义的证据。
11. 验证每个 `observed` 结果包含有效证据事件。
12. 人为制造证据不足会话，验证不会输出最低行为等级。
13. 使用模拟模型制造规则与 AI 冲突，验证最终字段为空并进入教师复核。
14. 对相同会话重复结束和重分析，验证幂等结果与 attempt 历史。

### 19.5 非功能与隐私测试

- 核心状态机、规则引擎、融合引擎和哈希模块分支覆盖率不低于 90%。
- 其他新增后端和前端代码行覆盖率不低于 80%。
- 使用包含伪造系统指令的代码注释验证提示词注入防护。
- 使用身份、绝对路径和密钥样例验证模型输入及日志脱敏。
- 验证保留期限任务、导出和删除闭环。
- 使用不同长度但相同行为分布的模拟会话，检查聚合结果没有非预期长度偏差。
- 验证 JSONL 写入中断后的恢复不会产生半条事件或重复事件。
- 在包含一万条事件的会话中，采集器事件入队耗时 p95 不高于 5 毫秒，普通本地界面操作 p95 不高于 200 毫秒。
- 使用键盘完成模板选择、预览、发布和结果复核；所有表单控件具有可读标签和清晰焦点。

### 19.6 可用性测试

至少邀请五名未参与开发的目标教师完成“创建三个维度—预览—发布—解释结果”任务：

- 至少四人无需口头指导，在十分钟内完成包含三个维度的试点方案。
- 至少四人全程无需打开高级设置。
- 至少四人能正确说明“数据不足”和“未发现明显证据”的区别。
- 教师不能理解的术语、步骤或错误提示必须记录并在发布前修正。

## 20. 验收标准

### 20.1 功能完成标准

功能被视为开发完成必须同时满足：

1. 普通教师可以从模板或完全自定义入口创建维度，只需填写名称、教学问题、符合表现和排除情况；结果说明与教学建议有默认值，可按需修改。
2. 默认流程不显示信号编码、权重、阈值、分析模式、Kappa 或聚合算法。
3. 五名受测教师中至少四名能在十分钟内独立创建、预览并发布包含三个维度的试点方案。
4. 系统建议必须经教师确认，拒绝建议后仍能使用 `llm_evidence` 试点方案。
5. 学生开始监控前能够看到当前题目、方案版本、试点状态和数据外发说明。
6. 重复上传不会产生重复事件，缺少序号的会话不能结束。
7. 废弃会话可以经审计恢复；每次任务重试保留独立 attempt。
8. 会话停止后只创建一个相同幂等键的正式分析任务。
9. 分析结果只包含教师定义的维度和等级。
10. 每个 `observed` 结果至少引用一个与 `evidence_criteria.id` 关联的有效事件。
11. 系统正确区分 `decision.status`、最终证据状态和最终行为等级。
12. `needs_review` 和 `partial` 不填充伪造的最终证据状态或等级。
13. 未校准规则不生成等级、数值支持度或自动融合结论。
14. 结果记录方案、模板、校准工件、信号字典、分析流水线、模型、模型参数、提示词快照和输入快照版本。
15. 规则与 AI 不一致时不静默采用 AI，而是保留双方结果并进入复核。
16. 同一会话可以选择另一个方案重新分析，原结果和全部来源信息保留。
17. 未配置 AI 时仍能查看原始行为、规则特征和部分结果。
18. 模型超时后界面显示明确状态并允许重试。
19. 教师修正以追加方式保存，能够查看原始结果和历史修订。
20. 旧日志仍可打开，缺失新信号时显示不可计算。
21. 第 19 节规定的自动化、性能、无障碍和可用性测试全部通过。
22. 普通结果卡片能够直接给出方案预置的教学建议；技术版本信息只出现在折叠详情中。

### 20.2 效度门槛

每个试点方案从 `pilot` 变为 `approved` 前必须完成以下验证：

1. 系统根据教师填写内容生成“构念卡”，教师只需审核教学目的、可观察证据、排除情况、适用题目和禁用解释。
2. 数据严格划分为开发集、校准集和锁定盲测集；同一学生、同一题目尝试及其派生会话不能跨集合。
3. 开发集可以用于修改定义、模板和提示词；校准集只能用于确定规则权重、等级边界和固定模型策略；锁定盲测集在版本冻结后才能运行一次正式评价。
4. 历史预览使用的三至五个会话属于开发集，不能进入校准集或锁定盲测集。
5. 默认正式审批门槛为每个维度至少 30 个可分析校准会话和 30 个可分析盲测会话；盲测集每个组合结果类别原则上不少于 5 个样本。
6. 至少两名教师在看不到规则、AI结果和其他教师标签的情况下，对校准集和盲测集独立标注；分歧通过第三方或共同讨论形成裁决标签。
7. 一致性计算使用组合序列 `not_observed < possible < clear...`，排除 `insufficient_evidence` 和 `not_computable`。教师间加权 Kappa 不低于 0.70；低于门槛时先修改维度定义。
8. 系统结果与盲测裁决标签的加权 Kappa 不低于 0.60，并分别报告各等级召回率、误判率、证据覆盖率和 95% 置信区间。
9. 教师抽样判断“引用证据确实支持结论”的比例不低于 90%。
10. `insufficient_evidence` 和 `not_computable` 单独报告，不作为错误或正确样本混入准确率。
11. 只有自定义维度与内置维度测量同一构念时，才在同一锁定盲测集上进行配对比较。主要指标改善且 95% 置信区间支持改善时，才能宣称“提高了该构念的判断准确性”；新增构念只能宣称“更贴合教师关注点”，不能与不同问题的默认维度比较准确率。
12. AI 置信度在完成独立校准前只显示为模型自评，不展示为正确概率。
13. 研究验证后台自动检查集合重叠、数据集哈希、版本冻结和指标计算，普通教师不需要手工计算统计量。

上述数量是正式审批的默认最低门槛，不影响教师使用三至五个会话发布 `pilot` 方案。样本分布不足、类别过少或无法形成独立盲测集时，方案可以继续试点，但不能升级为 `approved`。

方案证据含义、等级、模板兼容哈希、规则校准、信号语义、聚合策略、提示词、模型参数或模型主版本变化后，必须生成新的验证报告。已经用于盲测的会话不能在调参后再次作为同一版本的盲测集。

### 20.3 部署门槛

- `development`：允许本地开发和合成数据测试，不处理真实学生数据。
- `pilot`：允许知情的小规模试点，结果只能辅助教师观察，不用于成绩或惩罚。
- `production`：必须使用服务端角色授权、用户数据隔离、密钥管理、删除闭环、已批准方案和部署隐私审查。

任一门槛不满足时，服务端和界面都必须阻止相应部署模式，而不是只显示警告。

## 21. 实施顺序

本设计是项目级规格，不应压缩成一个超大实施计划。每个阶段单独形成实施计划、测试结果和评审门槛；上一阶段的核心验收通过后再进入下一阶段。

阶段一至四构成可实际试用的本地 `pilot MVP`；阶段五提供正式效度审批能力；阶段六才进入多人生产部署。这样不会为了完整研究平台而阻塞教师尽早试用。

### 阶段一：采集完整性和会话可靠性

- 会话状态机。
- 事件与片段唯一标识、连续序号和去重。
- 结束、废弃与恢复策略。
- 方案和信号字典快照、规范哈希与完整性检查。
- 数据外发说明、脱敏和删除骨架。

### 阶段二：信号、规则和证据状态

- 带版本的信号字典。
- 客观特征提取和采集器兼容性。
- 最低观察机会。
- 四种证据状态。
- 必要与可选信号。
- 候选规则命中记录和不可变校准工件。
- 未校准规则禁止自动输出等级。
- 阶段聚合策略和长度偏差测试。

### 阶段三：教师引导模式

- 四个内置模板。
- 引导式方案编辑器和普通语言结果。
- 系统配置建议与教师确认。
- 草稿、不可变发布版本和部署状态。
- 三至五个历史样本预览。
- 会话绑定。
- 折叠的分析详情。
- 第一轮目标教师可用性测试。

### 阶段四：AI、融合和复核

- 持久化任务队列、独占 worker 和恢复。
- 动态提示词、输入预算和长会话切分。
- 逐维度严格结果校验和部分结果保存。
- 不静默覆盖规则的融合策略。
- 教师修正、审计和并发控制。
- 完整错误状态和结果解释。

### 阶段五：高级设置与研究验证

- 高级技术参数和校准失效提示。
- 开发集、校准集和锁定盲测集管理。
- 教师盲标和裁决。
- 教师间一致性、系统一致性、证据支持率、置信区间和基线对照。
- 方案从 `pilot` 升级为 `approved`。

### 阶段六：正式部署

- JupyterHub 单用户部署。
- 服务端授权、数据隔离、隐私治理和保留策略。
- 性能、无障碍和删除闭环验收。

## 22. 主要取舍

1. 采用结构化表单而不是自由提示词，牺牲少量灵活性，换取稳定、可验证和可复现。
2. 采用文件存储完成本地 MVP，降低迁移成本；多人部署时再迁移数据库。
3. 采用停止后统一分析，牺牲实时 AI 反馈，换取完整上下文和更少超时。
4. 采用规则与 AI 混合，避免把可计算事实交给模型猜测。
5. 不把运行成功等同于答案正确，避免超出当前证据能力。
6. 规则与 AI 冲突时不自动给出单一结论，牺牲部分自动化率，换取可审计性。
7. 把效度验证放在正式部署之前，牺牲上线速度，避免把“可配置”误当成“已验证更准确”。
8. 普通教师只编辑教学含义，系统和研究管理员维护技术参数，牺牲部分自由度，换取更低学习成本。
9. 完全自定义维度允许快速试点，但在独立盲测通过前不提供自动化“正式可用”承诺。
