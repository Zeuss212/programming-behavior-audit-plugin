# 0.2.1 测试建议延迟与可靠性修复验证

日期：2026-08-06  
分支：`fix/ui-hotfix-0.2.1`

## 目标与边界

本轮修复教师确认知识点后生成测试建议约 60 秒失败的问题。作者辅助请求改为
`2048 → 4096` 输出预算、`thinking.type=disabled` 和 JSON 对象模式；完整会话
分析仍使用原 `8192 → 16384` 预算和 120 秒总时限。

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

对应本地提交：

- `8e07668`：共享传输闭合参数；
- `63377b3`：作者辅助专用请求预算；
- `b6a5d6c`：安全 Provider 错误码；
- `4180d2e`：前端可操作提示和草稿保留。

## 全量源码与构建

| 门禁 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest -q myextension/tests --ignore=myextension/tests/test_labextension_artifact.py` | `668 passed in 21.49s` |
| `.venv/bin/jlpm test --runInBand` | `20` 套件、`300 passed`，覆盖率门槛通过 |
| `.venv/bin/jlpm stylelint:check` | 退出 0 |
| `.venv/bin/jlpm prettier:base --check` | 退出 0，全部匹配 Prettier |
| `.venv/bin/jlpm eslint:check` | 退出 0 |
| `.venv/bin/jlpm build:lib:prod` | 退出 0 |
| `.venv/bin/jupyter-builder build .` | Rspack 2.0.8 编译成功，134 ms |

pytest-jupyter 需要绑定 `127.0.0.1` 临时测试端口；沙箱内的 setup error 在授予仅
本机回环权限后消失，不是代码失败。

## Wheel 与交付目录

- wheel：`myextension-0.2.1-py3-none-any.whl`
- 新 SHA-256：`2461f4e24e3a1914b6471e8444d92de5719b83ad41b1df389ae18e627a20a3f2`
- 上一 UI-hotfix SHA-256：`c7bffe0ad1528715b9bdd371965d0bc52d762429c31e2f3664d3136a60547386`
- 前端入口：`remoteEntry.cb43c0aff88bc38a.js`
- 两个交付目录的 wheel、README 和 `runtime.env.example` 逐字节一致；
- `check-wheel-contents`、wheel ZIP 完整性、两份 SHA-256、脚本语法和发布测试均通过；
- SHA 文件中的 wheel 路径相对交付目录，因此校验必须在各交付目录内运行。首次从
  仓库根目录调用只产生“找不到相对路径”，改用正确工作目录后两份均显示 `OK`。

最终 wheel 仅安装到 `/private/tmp/myextension-assist-fix.<random>/site`，没有修改
项目 `.venv` 或系统 Python。使用 Python `-P` 排除 worktree 源码后，导入版本为
`0.2.1`，模块来自隔离目标，JupyterLab 4.6.1 报告 `myextension v0.2.1 enabled OK`。

## 待最终检查点补充

- 新参数的一次合成 Provider 验收；
- 新 wheel、空数据目录的本地 JupyterLab 预览；
- 新 ZIP 名称和 SHA-256；
- 最终全量回归和本地回滚标签。

## 未执行的外部动作

- 未构建或运行 Docker 镜像；
- 未登录或推送镜像仓库；
- 未注册或修改 BLUEDOT 工作台；
- 未推送 Git；
- 未向 Provider 发送真实课程内容、实际草稿或学生数据。

源代码回滚点为设计提交 `05628ef`；制品回滚可重新安装上一 SHA-256 wheel。软件
回滚不会删除或迁移任何插件数据目录。
