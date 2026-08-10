# 安装、使用与回滚

## 前置条件

- VS Code Desktop 1.125.0 或兼容的 `^1.125.0` 版本。
- Microsoft Python 扩展。
- 可运行的 Python 解释器。
- 一个受信任的本地文件夹工作区。

## 校验文件

在本目录运行：

```bash
shasum -a 256 -c SHA256SUMS
```

输出必须包含 `behavior-audit-vscode-0.1.0.vsix: OK`。

## 安装

图形界面：

```text
VS Code -> Extensions -> ... -> Install from VSIX...
```

命令行：

```bash
code --install-extension behavior-audit-vscode-0.1.0.vsix
```

安装后重新加载 VS Code，活动栏会出现“编程行为分析”。

## 教师端

1. 打开“编程行为分析”并切换到“教师端”。
2. 点击“发布方案”，输入题目、知识点名称和客观观察依据。
3. 可选：先运行“配置 AI Key”，再使用“生成 AI 方案建议”；采用前必须人工复核。
4. 点击“导出方案”，把 JSON 发给学生。

API Key 只写入 VS Code SecretStorage，不写入工作区、VSIX 或导出包。不需要 AI 时可
完全手工发布方案，并可运行“清除 AI Key”。

## 学生端

1. 打开教师提供的方案 JSON，运行“导入考核方案”。
2. 切换到“学生端”，勾选知情确认，点击“开始监控”。
3. 编辑、保存 `.py` 或 `.ipynb`；运行 Python 时使用扩展命令“运行当前 Python 文件并记录”。
4. 点击“结束并生成简报”，再运行“导出会话”。
5. 需要排查本地文件时运行“打开本地数据位置”。

关闭 VS Code 会停止新事件采集。重新打开后只能恢复关闭前已刷新到本地的数据；页面
关闭期间的行为无法补录。扩展不会自动把报告发送到 FinColab。

## 5 分钟试运行

按照 `demo/README.md` 执行。至少包含一次失败运行、一次成功运行、一次关闭/重新打开、
恢复会话、结束并导出。完成后核对导出目录的 `manifest.json` 哈希。

## 卸载、重装和回滚

卸载：

```bash
code --uninstall-extension bluedot-ai.behavior-audit-vscode
```

重装当前版：先卸载，再重新执行安装命令。回滚时先保留通过“打开本地数据位置”找到的
会话目录，然后卸载当前版并安装上一份已校验 VSIX。卸载扩展不会等同于删除平台侧
数据；本版本也不会主动操作平台数据。
