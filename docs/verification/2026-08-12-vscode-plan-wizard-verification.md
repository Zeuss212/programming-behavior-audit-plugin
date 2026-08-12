# VS Code 方案向导 0.1.1 验证记录

## 自动化验证

2026-08-12 执行：

```bash
cd vscode-extension
npm run package
```

结果：

- ESLint 通过。
- TypeScript `tsc --noEmit` 通过。
- Vitest：26 个测试文件、106 项测试通过。
- esbuild 生产构建通过。
- `behavior-audit-vscode-0.1.1.vsix` 生成成功，共15 个包内文件。
- VSIX 验证通过，包含 `plan-wizard.css` 与 `plan-wizard.js`，未包含源码、测试、source map、绝对路径或测试密钥。

专项测试覆盖：

- HTTP 400 错误原因的有界读取和脱敏展示。
- 只对明确拒绝 `response_format` 的请求降级一次。
- 无关 HTTP 400 不自动重试。
- AI 返回空说明或空观察依据时使用客观默认文本补齐。
- 草稿保存、恢复、损坏状态忽略和清理。
- 三步向导协议、严格消息校验、CSP 和可访问标记。
- 旧命令 ID 与新向导入口的兼容。

## 未验证范围

- 本轮未读取用户 VS Code SecretStorage，也未调用真实方舟 API。真实模型请求需要安装 0.1.1 后使用已配置的 Key 进行冒烟。
- 本轮尚未在 VS Code 可视界面中完成键盘遍历、深浅主题和 640 px 窄窗口的人工验收。

因此当前准确状态是：实现、自动化测试、构建和 VSIX 打包完成；真实 AI 与可视界面人工冒烟待安装后执行。
