# 编程行为分析 VS Code 扩展 0.1.3

这是一个独立的 VS Code Desktop 扩展，不依赖 Jupyter Server、BAMS 或 FinColab
运行。教师可发布并导出考核方案，学生导入方案并确认采集范围后开始本地监控，结束
时生成确定性的课堂简报、教师可读简报和两份可读日志。

## 支持范围

- 支持本地 `.py` 编辑、保存及扩展自有 Python 运行命令。
- 支持 `.ipynb` 结构修改和 VS Code 能稳定提供的单元格运行摘要。
- 每秒或每 20 条事件刷新到 `ExtensionContext.globalStorageUri`。
- VS Code 关闭后不再产生新事件；重新打开只能恢复关闭前已经保存的事件。
- 核心采集、恢复、简报和导出不依赖 AI。
- 可选 AI Key 只保存在 VS Code SecretStorage；除 localhost 外只允许 HTTPS。

扩展不读取普通终端命令、终端输出、全局键盘、剪贴板内容、环境变量，也不提供考试
系统隔离。课堂简报会以固定 S/A/B/C/D 展示“课题实践表现”，仅供课后教学反馈与教师
查看，不是考试成绩、能力评价、人格评价或知识掌握度判定。等级只依据本地记录的运行
验证、调试与修正、任务推进；课堂专注记录单独展示，不参与评级。运行成功也不等同于
题目答案正确。

## 基本操作

1. 安装 Microsoft Python 扩展并选择解释器。
2. 打开一个受信任的本地文件夹工作区。
3. 打开活动栏“编程行为分析”。
4. 教师端点击“创建考核方案”，在三步向导中输入题目、确认知识点、复核并发布。
5. AI 建议是可选项；生成结果只进入草稿，可编辑、删除和排序，不会自动发布。
6. 发布后导出 JSON；学生端导入后勾选知情确认。
7. 点击“开始监控”，编辑或保存 Python/Notebook，使用命令“运行当前 Python 文件并记录”。
8. 点击“结束并生成简报”，选择目录导出会话。V2 导出会同时包含面向教师的
   `teacher_brief.md`、结构化 `classroom_brief.json` 和可选 AI 建议；AI 只做建议，不能改写
   本地固定的课题实践表现。
9. 使用命令“打开本地数据位置”查看本机原始会话目录。

方案向导会把未发布内容保存在当前 VS Code 工作区状态中。关闭向导后重新打开可继续编辑。AI 请求失败也不会清空草稿；界面会显示经脱敏的服务端错误，教师可重试或手动继续。

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
