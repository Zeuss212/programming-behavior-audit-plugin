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
- 本机 VS Code 1.132.0 arm64 的真实 Extension Host 集成测试已通过。
- VS Code Desktop 真实流程已通过：监控、失败运行、代码修复、成功运行、关闭重开、
  恢复、结束生成简报、导出和 SHA-256 清单校验均成功。
- 真实会话生命周期为 48 分 34.308 秒，事件序号 1–469 且缺口为 0；本次按用户要求
  记为 5 分钟验收通过，不替代后续正式课堂的 40 分钟持续操作验收。
- 当前准确状态：`implementation complete, local 5-minute acceptance passed`。

本扩展不会自动把学生数据或简报发送到 FinColab/BAMS。部署到教学平台前仍需平台方
提供认证、中转服务、课程/班级/作业映射和数据保留策略。
