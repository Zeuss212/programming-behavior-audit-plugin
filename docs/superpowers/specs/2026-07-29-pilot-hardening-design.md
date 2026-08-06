# 编程行为监控 Pilot 加固设计

日期：2026-07-29

## 目标

在保持 0.2.0 已有会话、分析结果文件和 API 兼容的前提下，依次完成：

1. 修复 Python 文件运行的异步 Contents 校验和根目录越界问题；
2. 在界面上区分“数据采集完成”和“AI 已生成结论”；
3. 增加 API Key 清除功能和可操作的配置字段错误；
4. 为 Notebook 与 Python 文件监控器补充直接自动化测试和渐进覆盖率门禁；
5. 改善 Python 文件运行入口并完成相关中文化。

## 明确不做

- 不修改 `requires-python >=3.10` 和 Python 3.10–3.14 classifiers。
- 不重写分析任务状态机。
- 不改变现有 `partial + ai_not_configured` 后端结果契约。
- 不迁移或改写历史会话、结果及教师复核文件。
- 不增加 Python 编辑器自定义工具栏按钮。
- 不扩大到 JupyterHub 多租户或远程代码执行场景。

Python 3.10–3.14 的兼容性矩阵属于后续验证项。本轮只保证不缩小当前声明，
不把未执行的版本测试表述成已经验证。

## 总体方案

采用兼容式加固，不做 2.0 状态机重构：

- 后端先封闭 Python 文件执行边界，再保留现有执行响应格式；
- 前端利用已有 `error_code` 对未配置 AI 的结果做显示分流；
- AI 配置沿用现有接口，通过稳定错误码补齐清除和字段错误反馈；
- 自动化测试直接覆盖监控器的外部行为，而不是只提高全局覆盖率数字；
- Python 运行命令沿用同一 command id，增加命令面板和 Run 菜单入口并中文化。

## 1. Python 文件运行安全

### 当前根因

`RunPythonFileRouteHandler.post()` 是同步方法，却调用异步
`contents_manager.get()` 而没有等待。随后代码调用私有 `_get_os_path()`，
对结果执行 `Path.resolve()`，但没有验证解析后的路径仍位于 Jupyter
`root_dir` 中。根目录内指向根外文件的符号链接因此能够越过 Contents 边界。

### 设计

- 将 Route 改为 `async def post()`。
- 使用 `await contents_manager.get(path, content=False)` 完成 Contents
  Manager 的存在性与访问策略校验。
- 校验返回模型的 `type == "file"`，并继续限制扩展名为 `.py`。
- 只有提供本地 `_get_os_path()` 和有效 `root_dir` 的 Contents Manager 才支持
  此运行能力；非本地 Manager 返回稳定的“不支持本地运行”错误。
- 分别解析 `root_dir` 和候选路径的真实路径，然后通过 `relative_to()` 验证候选
  路径仍在根目录中。越界符号链接返回 400，不执行子进程。
- 文件不存在返回 404；不支持的 Contents Manager 返回 400；服务器内部执行
  故障保持闭合响应，不返回真实绝对路径。
- 保留现有超时、输出截断和 Python 解释器选择逻辑。

### 测试

- 正常根目录内 `.py` 文件可以解析和运行。
- `contents_manager.get()` 确实被等待。
- 根目录外符号链接被拒绝，目标脚本没有执行。
- 目录、非 `.py`、不存在文件和非本地 Contents Manager 被拒绝。
- 超时和输出截断行为保持不变。

## 2. 区分采集完成与 AI 结论

### 兼容约束

后端继续生成：

```json
{
  "status": "partial",
  "error_code": "ai_not_configured"
}
```

不新增必填字段，不修改 Schema 枚举，不改写历史结果。

### 前端设计

当分析结果的 `error_code === "ai_not_configured"` 时：

- 会话采集区继续显示事件数量和上传状态；
- 结果区显示独立空状态：“数据采集完成，尚未进行 AI 分析”；
- 提示用户配置 AI 服务后点击“重试分析”；
- 不渲染普通维度结论卡；
- 不把维度计入“待复核”；
- 不提供教师确认或修正表单。

其他 `partial` 状态仍沿用现有结果卡，以保留模型部分失败、部分维度需要复核等
合法场景。

### 测试

- `ai_not_configured` 只显示未分析空状态。
- 空状态中不存在“部分结果”“待复核”和复核表单。
- 普通 `partial` 结果仍显示维度卡。
- 配置成功并重试后，现有 ready/partial 结果路径不受影响。

## 3. AI 配置的清除与错误反馈

### Key 清除

- AI 配置区在已保存 Key 时显示“清除已保存 Key”按钮。
- 点击后使用 JupyterLab 对话框进行二次确认。
- 确认后只提交：

  ```json
  {"clear_api_key": true}
  ```

- 成功后清空输入框、更新 `api_key_configured=false`，显示“AI 状态：未配置”。
- 取消确认不发送请求。
- 不在前端展示或依赖 `api_key_preview`。

### 字段错误

- `save_ai_config()` 抛出的 `ValueError` 映射为 HTTP 400。
- 响应使用稳定结构：

  ```json
  {
    "code": "ai_config_validation_failed",
    "message": "AI 配置格式不正确。",
    "retryable": false,
    "details": {"field": "base_url", "reason": "invalid_url"}
  }
  ```

- `OSError` 保持 HTTP 500，错误码为 `ai_config_save_failed`，
  `retryable=true`。
- 响应不包含 Key、provider 原始正文、绝对路径或 traceback。
- 前端根据 `details.field` 将错误显示在对应字段下；无法定位字段时显示配置区通用
  错误。

### 测试

- 已配置状态显示清除按钮，未配置状态不显示。
- 取消确认不调用 API；确认后只发送 `clear_api_key`。
- 清除成功和失败均有可访问的 `aria-live` 状态。
- 非 HTTPS、非回环 Base URL 返回 400 和稳定错误码。
- 文件写入失败返回 500，响应不泄露配置内容。

## 4. Notebook/Python 监控器自动化测试

新增直接行为测试，而不是通过 Sidebar 测试间接覆盖。

### NotebookMonitor

- 启动时连接 tracker；
- Cell 内容变化产生编辑行为；
- 执行 scheduled/success/error 产生对应事件；
- Notebook 切换后上下文更新，新 Notebook 被监听；
- 同一 Panel/Cell 重复出现时不会重复绑定监听器。

### PythonFileMonitor

- 打开 `.py` 文件后建立当前上下文；
- 文本变化产生编辑行为；
- 文件切换更新 path、name 和 source；
- 非 `.py` 文件不进入 Python 上下文；
- `close()` 会结束当前编辑区间并取消空闲计时器；它是运行前的 flush 操作，
  之后的编辑仍应继续被监控。

### 覆盖率门禁

在直接测试稳定后，为 `src/notebookMonitor.ts` 和
`src/pythonFileMonitor.ts` 设置文件级门禁：

- statements：70%
- branches：60%
- functions：70%
- lines：70%

门禁只针对这两个核心监控器，不使用无关文件提高全局数字。

## 5. Python 文件运行入口和中文化

- 保留 command id：`myextension:run-current-python-file`。
- 命令标签改为“运行当前 Python 文件”。
- 命令说明、未打开文件提示、运行结果、超时提示、stdout/stderr 标题中文化。
- 将命令加入：
  - 命令面板；
  - JupyterLab Run 菜单；
  - `.jp-FileEditor` 右键菜单。
- 没有活动 `.py` 文件时命令禁用。
- 继续在运行前保存当前文件，并沿用现有 scheduled/success/error 行为事件。
- 本轮不增加工具栏按钮，避免引入 Document Registry 工具栏扩展和额外生命周期
  复杂度。

## 数据与隐私边界

- 自动化测试只使用临时目录、固定合成代码和合成配置。
- 测试不得读取或输出现有 `log/`、真实 Notebook、用户身份或真实 Key。
- 错误响应不得返回绝对路径、Key 派生内容、provider 原始响应或 traceback。
- 浏览器回归测试不调用真实外部 AI。

## 实施顺序与验收

1. Python 路径安全测试先失败，再实现异步与根目录校验并通过后端全量测试。
2. 添加未配置 AI 的前端失败测试，实现独立空状态并通过前端测试。
3. 添加 Key 清除和配置错误测试，实现前后端变更并分别验证。
4. 添加两个监控器的直接测试，达到文件级门禁。
5. 添加命令注册和中文化测试，实现命令面板、Run 菜单和右键入口。
6. 运行前端 lint、前端全量测试、后端全量测试、生产构建、wheel 制品测试和隔离
   JupyterLab smoke。

完成条件是上述测试均以新鲜执行结果通过，且隔离浏览器流程确认：

- 越界 Python 文件不能运行；
- 未配置 AI 不显示行为结论；
- 已保存 Key 可以从界面清除；
- 配置字段错误能够定位；
- Python 运行命令可以从命令面板、Run 菜单和右键菜单找到。
