# 编程行为分析 VS Code 扩展 0.1.0

这是一个独立的 VS Code Desktop 扩展，不依赖 Jupyter Server、BAMS 或 FinColab
运行。教师可发布并导出考核方案，学生导入方案并确认采集范围后开始本地监控，结束
时生成确定性的课堂简报和两份可读日志。

## 支持范围

- 支持本地 `.py` 编辑、保存及扩展自有 Python 运行命令。
- 支持 `.ipynb` 结构修改和 VS Code 能稳定提供的单元格运行摘要。
- 每秒或每 20 条事件刷新到 `ExtensionContext.globalStorageUri`。
- VS Code 关闭后不再产生新事件；重新打开只能恢复关闭前已经保存的事件。
- 核心采集、恢复、简报和导出不依赖 AI。
- 可选 AI Key 只保存在 VS Code SecretStorage；除 localhost 外只允许 HTTPS。

扩展不读取普通终端命令、终端输出、全局键盘、剪贴板内容、环境变量，也不提供考试
系统隔离。课堂简报不评分、不排名、不判断能力、人格或知识掌握程度。

## 基本操作

1. 安装 Microsoft Python 扩展并选择解释器。
2. 打开一个受信任的本地文件夹工作区。
3. 打开活动栏“编程行为分析”。
4. 教师端发布方案并导出 JSON；学生端导入后勾选知情确认。
5. 点击“开始监控”，编辑或保存 Python/Notebook，使用命令“运行当前 Python 文件并记录”。
6. 点击“结束并生成简报”，选择目录导出会话。
7. 使用命令“打开本地数据位置”查看本机原始会话目录。

不会自动把简报发送到 FinColab、BAMS 或其他平台。平台接入需要后续中转服务和身份、
班级、课程、作业映射接口。

## 开发验证

```bash
npm ci
npm run verify
npm run test:soak
npm run test:integration
npm run package
```

`test:integration` 首次运行会从微软官方下载约 249 MB 的 VS Code 1.125.0 测试运行时。
若外网无法访问 `update.code.visualstudio.com`，该项会保持未验证，而不是被跳过后宣称
成功。也可直接使用已安装的 VS Code：

```bash
VSCODE_EXECUTABLE_PATH='/Applications/Visual Studio Code.app/Contents/MacOS/Code' \
  npm run test:integration
```

集成测试会把 fixture 复制到短临时目录，不会修改仓库中的原始测试文件，并避免 macOS
IPC socket 路径长度限制。
