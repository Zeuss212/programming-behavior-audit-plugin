# 0.2.1 测试建议延迟与可靠性修复验证

日期：2026-08-06  
分支：`fix/ui-hotfix-0.2.1`

## 目标与边界

本轮修复教师确认知识点后生成测试建议约 60 秒失败的问题。作者辅助请求改为
`2048 → 4096` 输出预算、`thinking.type=disabled` 和 JSON 对象模式；完整会话
分析继续使用 `8192 → 16384` 预算，默认使用三次 60 秒调用机会和 180 秒总时限。

只使用合成诊断输入和自动化测试。没有把真实题目、知识点、学生数据、现有草稿、
API Key 或 Provider 响应正文写入测试、日志、文档或提交。

## 已验证根因

- 现场三次测试建议接口均在约 60 秒后返回 502；
- 当前模型的短合成 JSON 请求可成功，排除了模型名、Key 和基础路由不可用；
- 等规模合成请求在默认深度思考下耗时长并发生长度截断；
- 同输入关闭思考并启用 JSON 对象模式后，一次返回有效闭合结构；
- 旧路由把所有传输失败压缩为 `test_generation_failed`，旧前端再压缩为通用提示。

根因不是模型需要 180 秒，而是作者辅助误用了完整分析的深思考与大输出预算。

## TDD 证据

| 闭环 | RED | GREEN |
| --- | --- | --- |
| 共享传输参数 | 定向 pytest：`6 failed, 1 passed`；失败为新关键字不存在 | 定向与原截断回归：`10 passed` |
| 作者辅助调用点 | 定向 pytest：`3 failed`；实际仍为 8192/16384 且缺少请求字段 | 作者辅助与传输选择集：`23 passed, 59 deselected` |
| 后端安全错误码 | 沙箱首次为 10 个回环绑定 setup error，不计为 RED；获本机回环权限后 `9 failed, 1 passed`，失败值均为旧 `test_generation_failed` | API 与相邻路由：`17 passed, 17 deselected` |
| 前端可操作提示 | 定向 Jest：`9 failed`；均显示旧通用提示 | 编辑器套件：`19 passed`，手工测试内容在超时后保留 |
| 编译制品身份 | 首次失败于 repository labextension 缺少新标记；生产构建后失败于旧 delivery wheel 缺少标记 | 新 wheel 同步后 artifact 与 release 测试合计 `9 passed` |
| 完整分析自动恢复 | 定向 pytest：`12 failed, 5 passed`；旧默认仍为 120 秒、最多两次调用且超时后固定等待 | 配置、worker 与分析器选择集：`164 passed`；60/120/180 秒分别限制为 1/2/3 次 |
| 分析中状态提示 | 侧栏定向 Jest：新增两个状态均失败，原有 `88 passed`；repository artifact 缺少新标记 | 侧栏定向 Jest（关闭全局覆盖率）：`90 passed`；生产构建后 artifact/release `9 passed` |

对应本地提交：

- `8e07668`：共享传输闭合参数；
- `63377b3`：作者辅助专用请求预算；
- `b6a5d6c`：安全 Provider 错误码；
- `4180d2e`：前端可操作提示和草稿保留；
- `b3a178a`：180 秒共享预算内的第三次 Provider 调用；
- `5a14c49`：分析中自动恢复提示和制品标记。

## 全量源码与构建

| 门禁 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest -q myextension/tests` | `673 passed in 22.78s` |
| `.venv/bin/jlpm test --runInBand` | `20` 套件、`302 passed`，覆盖率门槛通过 |
| `.venv/bin/jlpm stylelint:check` | 退出 0 |
| `.venv/bin/jlpm prettier:base --check` | 退出 0，全部匹配 Prettier |
| `.venv/bin/jlpm eslint:check` | 退出 0 |
| `.venv/bin/jlpm build:lib:prod` | 退出 0 |
| `.venv/bin/jupyter-builder build .` | Rspack 2.0.8 编译成功，152 ms |

pytest-jupyter 需要绑定 `127.0.0.1` 临时测试端口；沙箱内的 setup error 在授予仅
本机回环权限后消失，不是代码失败。

## Wheel 与交付目录

- wheel：`myextension-0.2.1-py3-none-any.whl`
- 最终 SHA-256：`047367eb210819eddd7850f29324a05df7172278b289ec9d7cbb411cdc1e7e29`
- 上一 assessment-assist SHA-256：`2461f4e24e3a1914b6471e8444d92de5719b83ad41b1df389ae18e627a20a3f2`
- 上一 UI-hotfix SHA-256：`c7bffe0ad1528715b9bdd371965d0bc52d762429c31e2f3664d3136a60547386`
- 前端入口：`remoteEntry.80d52557487e4002.js`
- 两个交付目录的 wheel、README、Dockerfile 和 `runtime.env.example` 逐字节一致；
- `check-wheel-contents`、wheel ZIP 完整性、两份 SHA-256、脚本语法和发布测试均通过；
- SHA 文件中的 wheel 路径相对交付目录，因此校验必须在各交付目录内运行。首次从
  仓库根目录调用只产生“找不到相对路径”，改用正确工作目录后两份均显示 `OK`。

最终 wheel 仅安装到 `/private/tmp/myextension-analysis-retry.bXxaIn/site`，没有修改
项目 `.venv` 或系统 Python。使用 Python `-P` 排除 worktree 源码后，导入版本为
`0.2.1`，模块来自隔离目标，JupyterLab 4.6.1 报告 `myextension v0.2.1 enabled OK`。

## 合成 Provider 验收

通过隔离 wheel 和现有私有 AI 配置发送一次 195 字合成题目、5 个合成知识点；
包装器会在任何第二次网络调用前本地阻断。实际结果：

```text
synthetic_provider_ok calls=1 tests=5 elapsed_sec=6.356
```

5 个知识点全部被闭合测试覆盖，未发生第二次请求，耗时明显低于旧失败边界约
60 秒。没有输出或保存 Provider 正文，也没有发送实际草稿或学生数据。

## Assessment-assist wheel 本地预览

- 最新非密钥基础地址：`http://127.0.0.1:18998/lab`；
- Jupyter config、data、runtime、workspace、用户设置和插件数据均使用新的隔离目录；
- 打开浏览器前 API `profile_count=0`；
- 页面显示“还没有已发布方案”，监控已停止、事件数为 0；
- 当时实际加载 `remoteEntry.cb43c0aff88bc38a.js`，HTTP 200；
- “行为分析”计算样式为 `writing-mode: vertical-rl`、
  `text-orientation: upright`、`transform: none`；
- Playwright 截图不含 token。用户随后在 headed 预览窗口自行进行创建方案测试，
  自动化因此停止点击，没有与用户争夺界面控制。

第一次预览只隔离插件数据目录，Jupyter 默认 workspace 仍可被交互恢复；用户确认
当时状态变化来自其手工测试。最终 `18998` 预览进一步隔离全部 Jupyter 状态目录，
并在浏览器使用前独立证明方案数为 0。

该预览在本轮完整分析自动恢复代码写入前启动。为不打断用户正在进行的手工测试，
没有替换或重启 18997/18998 服务；最终 wheel 的前端身份和 Python 行为通过制品测试、
隔离安装及全量自动化验证，而不是把旧预览误报为最终 wheel 预览。

## 完整分析首次超时现场证据

用户在最终隔离预览中完成一次真实合成演示。第一个持久化 attempt 从
`09:23:30.607` 运行到 `09:25:30.740`，约 120.13 秒，结果为 `partial`，错误码
为 `ai_analysis_timeout`，安全响应快照中的 Provider 响应数为 0。教师手动重试后，
第二个 attempt 从 `09:25:40.935` 运行到 `09:26:38.752`，约 57.82 秒，结果为
`ready`，六个维度均有 AI 结果。

同一任务输入第二次成功，排除了题目、方案、Key、模型名和结果结构不兼容。代码检查
进一步确认旧版单个 attempt 内最多执行两次 60 秒调用，且每次可重试错误后固定等待
2 秒。修复后默认总预算为 180 秒，最多调用三次；真实 Provider 超时后不再额外等待，
网络错误、429 和 5xx 仍保留 2 秒退避。第三次自动恢复路径只使用假 Provider 和假
时钟验证，本轮没有再触发真实 Provider 请求。

## 最终交付制品

- 完整目录：`myextension-0.2.1-BLUEDOT-完整交付包/`；
- wheel：`myextension-0.2.1-py3-none-any.whl`，SHA-256 为
  `047367eb210819eddd7850f29324a05df7172278b289ec9d7cbb411cdc1e7e29`；
- ZIP：`myextension-0.2.1-BLUEDOT-完整交付包-20260806-analysis-retry-fix.zip`；
- ZIP SHA-256：`e1fffc768af41a1ba7fc379a1ecf94deeaccf2608ce88c638b54ee6809d711ea`；
- ZIP 完整性测试与 `.zip.sha256` 校验均通过；
- 两个交付目录各自在正确工作目录执行 `shasum -a 256 -c SHA256SUMS`，
  wheel 均显示 `OK`；四个镜像构建/验证脚本均通过 `sh -n`。

## 未执行的外部动作

- 未构建或运行 Docker 镜像；
- 未登录或推送镜像仓库；
- 未注册或修改 BLUEDOT 工作台；
- 未推送 Git；
- 未向 Provider 发送真实课程内容、实际草稿或学生数据。

源代码回滚点为自动恢复设计提交 `4dad09d` 的父提交；制品回滚可重新安装上一
assessment-assist SHA-256 wheel。软件
回滚不会删除或迁移任何插件数据目录。
