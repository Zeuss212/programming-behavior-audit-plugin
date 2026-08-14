# 安装、演示与回滚

## 校验和安装

在本目录运行：

```bash
shasum -a 256 -c SHA256SUMS
code --install-extension behavior-audit-vscode-0.1.2.vsix --force
```

也可在 VS Code 的“扩展”菜单中选择“从 VSIX 安装…”，然后重新加载窗口。

## 学生端演示

1. 导入教师提供的方案，勾选知情确认并开始监控。
2. 完成操作后点击“结束、生成简报并导出”。
3. AI 建议为可选项；服务未配置或不可用时，课堂简报和 `analysis_log.json` 仍会保留。
4. 选择导出目录；若暂时取消，可使用“仅导出上次会话”继续。

## 回滚

保留本地会话目录后，重新安装本仓库的 `release-0.1.1` 包：

```bash
code --install-extension /path/to/release-0.1.1/behavior-audit-vscode-0.1.1.vsix --force
```
