# macOS 演示包同步至 0.4.0 设计

## 目标

修复 macOS 真实 AI 演示包与课堂候选插件版本不一致的问题，使预检始终使用版本控制中的 `myextension 0.4.0` wheel，并在不调用真实 AI、不构建镜像、不变更 BAMS 的前提下通过发布前本地验证。

## 背景与边界

课堂候选提交已将 `package.json` 升至 `0.4.0`，并提供经过 `deploy/bluedot/release-0.4.0/SHA256SUMS` 校验的 wheel。现有演示脚本却从被 `.gitignore` 排除的 `dist/myextension-0.3.0-py3-none-any.whl` 读取制品，同时硬编码旧哈希；这使干净工作树无法复现预检。

本变更只同步下列演示交付元数据：默认 wheel 路径、默认 SHA-256、环境示例、故障排查文案与安全测试。它不修改插件源码、wheel 内容、API 行为、演示流程、密钥处理或外部环境。

## 方案比较

1. **引用受版本控制的 0.4.0 候选 wheel（采用）**：脚本默认引用 `deploy/bluedot/release-0.4.0/artifacts/myextension-0.4.0-py3-none-any.whl`，并保留精确 SHA-256 检查。制品可复现，且仍允许通过 `.env` 显式覆盖为另一份本地 wheel。
2. 继续默认引用 `dist/`：需要每台机器手工构建或保存被忽略的制品，不能保证预检可复现，拒绝采用。
3. 在脚本中动态解析发布目录的 checksum 文件：减少重复的哈希文字，但引入额外 shell 解析与路径信任面；当前没有必要，拒绝采用。

## 行为与安全

- 默认预检仅接受已跟踪的 `0.4.0` wheel 和固定 SHA-256 `bc9cb1cdd3e95056f5ed9eed1aff19e1cf36e112966772b9fbdc86cd3b10804c`。
- `DEMO_WHEEL` 与 `DEMO_EXPECTED_WHEEL_SHA256` 仍必须一同覆盖，用于管理员明确验证其他本地 wheel；现有允许列表和禁止 API Key/命令替换逻辑保持不变。
- README 明确预检失败时应恢复本仓库受校验的 `0.4.0` 制品，不要求或触发 wheel 重建。

## 验收

1. 测试先证明旧 `0.3.0` 默认值无法满足当前 `0.4.0` 发布契约。
2. 更新脚本和文档后，演示资产与 shell 安全测试通过，且默认预检通过。
3. `shasum -a 256 -c deploy/bluedot/release-0.4.0/SHA256SUMS` 输出 `OK`。
4. 受控本机环境的整仓 pytest 通过；不执行真实 AI、Docker 镜像构建、推送、BAMS 配置或服务器变更。
