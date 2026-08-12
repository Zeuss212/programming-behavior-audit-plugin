# 安装、配置与演示

## 1. 校验安装包

在本目录打开终端，运行：

```bash
shasum -a 256 -c SHA256SUMS
```

必须显示 `behavior-audit-vscode-0.1.1.vsix: OK`。

## 2. 安装

图形界面：

```text
VS Code -> 扩展 -> … -> 从 VSIX 安装……
```

选择 `behavior-audit-vscode-0.1.1.vsix`，然后重新加载 VS Code。

命令行：

```bash
code --install-extension behavior-audit-vscode-0.1.1.vsix --force
```

## 3. 配置 AI

1. 在 VS Code 命令面板运行“编程行为分析: 配置 AI Key”。
2. 粘贴 API Key 并回车。Key 只保存在 VS Code SecretStorage。
3. 在设置中搜索 `behaviorAudit.ai`，确认 Base URL 和模型名与你的方舟服务配置一致。

AI 不是必需功能。未配置或请求失败时，仍可手动完成方案。

## 4. 教师端演示

1. 打开一个本地文件夹，再打开活动栏“编程行为分析”。
2. 选择教师端，点击“创建考核方案”。
3. 第一步：在大文本框输入完整题目，点击“下一步”。
4. 第二步：点击“生成 AI 建议”，或“手动添加”。修改知识点名称、说明和观察依据，必要时上移、下移或删除。
5. 第三步：确认题目和所有观察依据，点击“发布方案”。
6. 点击“导出方案”，把 JSON 文件交给学生端。

关闭向导前不需要手动保存；输入会保留为当前工作区草稿。

## 5. 学生端

1. 导入教师提供的方案 JSON。
2. 勾选知情确认，点击“开始监控”。
3. 结束时点击“结束并生成简报”，然后导出会话。

## 6. 回滚

如需回滚，先保留通过“打开本地数据位置”找到的会话目录，再运行：

```bash
code --uninstall-extension bluedot-ai.behavior-audit-vscode
code --install-extension /path/to/behavior-audit-vscode-0.1.0.vsix --force
```
