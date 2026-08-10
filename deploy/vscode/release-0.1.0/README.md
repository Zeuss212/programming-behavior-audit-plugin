# VS Code 编程行为分析插件 0.1.0 交付包

本目录面向安装和课堂演示人员，与 JupyterLab wheel 交付相互独立。

## 文件

- `behavior-audit-vscode-0.1.0.vsix`：VS Code Desktop 安装包。
- `SHA256SUMS`：安装包完整性校验值。
- `INSTALL.md`：安装、使用、卸载和回滚步骤。
- `demo/`：5 分钟试运行使用的合成 Python 示例和演示步骤。

## 当前验证状态

- lint、类型检查、94 项单元测试和生产构建已通过。
- 40 分钟加速模拟已通过：2400 条事件连续，中途恢复成功，写入队列无持续增长。
- 本机构建环境无法连接 `update.code.visualstudio.com`，因此官方 Extension Host 和
  真实 VS Code Desktop 5 分钟试运行尚未执行。
- 当前准确状态：`implementation complete, real soak verification incomplete`。

本扩展不会自动把学生数据或简报发送到 FinColab/BAMS。部署到教学平台前仍需平台方
提供认证、中转服务、课程/班级/作业映射和数据保留策略。
