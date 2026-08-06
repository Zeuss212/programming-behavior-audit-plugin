# myextension 0.2.1 自包含交付文件夹设计

日期：2026-08-06

## 目标

在项目根目录生成一个可直接复制给管理员或交给 Codex 的自包含文件夹：

```text
myextension-0.2.1-BLUEDOT-完整交付包/
```

接收方不需要理解仓库结构即可找到插件 wheel、完整安装部署步骤、镜像文件、校验值和操作边界。

## 范围

只复制已经通过验证的 `deploy/bluedot/release-0.2.1/` 资产，并新增三份入口信息：

- `00_从这里开始.md`：面向人的最短操作路径；
- `AGENTS.md`：Codex 在该文件夹内工作的范围、安全约束和验证顺序；
- `MANIFEST.json`：机器可读的版本、制品、SHA-256、源提交和验证状态。

不重新修改插件代码，不重新构建 wheel，不调用真实 AI，不构建或推送镜像，不操作 BLUEDOT。

## 目录

```text
myextension-0.2.1-BLUEDOT-完整交付包/
├── 00_从这里开始.md
├── AGENTS.md
├── MANIFEST.json
├── README.md
├── Dockerfile
├── .dockerignore
├── build_image.sh
├── verify_image.sh
├── runtime.env.example
├── SHA256SUMS
└── artifacts/
    └── myextension-0.2.1-py3-none-any.whl
```

## 信息入口

`00_从这里开始.md` 首先说明交付内容、校验命令、wheel 直装命令、镜像构建命令和下一步应阅读的文件。正文保持短小，完整细节链接到同目录 `README.md`。

`AGENTS.md` 要求 Codex：

1. 先读 `00_从这里开始.md`、`MANIFEST.json` 和 `README.md`；
2. 修改或安装前先验证 `SHA256SUMS`；
3. 不重新生成 wheel，不修改版本和哈希；
4. 未经明确授权不登录、推送、部署、调用真实 AI 或写入密钥；
5. 若只被要求说明部署，必须引用本目录现有文件和命令，不返回旧 `dist/` wheel。

`MANIFEST.json` 固定记录：

- 包名和版本：`myextension 0.2.1`；
- wheel 相对路径；
- SHA-256：`8436b8e69f9e25c58df68c0024723c660e9fe8751c52a60b320c1e97f28ea16e`；
- 源 Git 提交和标签；
- 已完成的本地验证；
- Docker daemon、真实基础镜像和 BLUEDOT 验收尚未执行。

## 一致性与安全

- 新文件夹中的 wheel 必须与源 release wheel 字节完全一致；
- 新文件夹和源 release 的 `README.md`、Dockerfile、脚本、环境示例和 SHA 文件必须一致；
- 不复制 `.DS_Store`、日志、Notebook、源码、`.ark_ai_config.json`、`.env` 或任何密钥；
- shell 脚本保留可执行权限；
- `SHA256SUMS` 必须在新文件夹根目录验证通过。

## 验收

1. 文件清单与设计目录完全一致；
2. `shasum -a 256 -c SHA256SUMS` 通过；
3. 源 wheel 与复制 wheel 的 SHA-256 相同；
4. 两个脚本通过 `sh -n`，且保留 `0755` 权限；
5. `MANIFEST.json` 可解析，无占位符；
6. 目录内不存在密钥赋值、`.DS_Store` 或未列出的文件；
7. Git 工作区最终干净，并建立单独提交和本地交付标签。

## 停止点

生成、验证并提交该文件夹后停止。镜像构建、推送、平台注册和真实 AI 验收继续由接收方按 `README.md` 执行。
