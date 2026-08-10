# myextension 0.3.0 课堂镜像交付包

这是可直接交给运维人员或 Codex 的完整插件交付目录。它只安装 JupyterLab 4 插件，不修改 BAMS/FinColab 原有前后端，不包含 API Key，也不会自动推送镜像。

## 交付内容

| 文件 | 用途 |
| --- | --- |
| `artifacts/myextension-0.3.0-py3-none-any.whl` | 已预构建的前后端插件 |
| `SHA256SUMS` | wheel 完整性校验 |
| `Dockerfile` | 将 wheel 安装进 BLUEDOT/BAMS JupyterLab 4 基础镜像 |
| `runtime.env.example` | 不含密钥的课堂运行参数 |
| `build_image.sh` | 先校验 wheel，再构建镜像 |
| `verify_image.sh` | 检查镜像内版本、扩展、超时和持久目录 |

## 1. 先校验

```bash
cd deploy/bluedot/release-0.3.0
sha256sum -c SHA256SUMS        # Linux
shasum -a 256 -c SHA256SUMS   # macOS
```

必须显示 `artifacts/myextension-0.3.0-py3-none-any.whl: OK`。
本次制品的 SHA-256 是 `ff1b2ad637600a93db0237df3ae3fa73f65813c53a968b79fb2ce0b34bd76050`。

## 2. 直接安装到已有 JupyterLab

使用 JupyterLab 所在环境的同一个 Python：

```bash
python -m pip install --no-cache-dir --no-deps --force-reinstall \
  artifacts/myextension-0.3.0-py3-none-any.whl
python -m jupyter server extension enable myextension --sys-prefix
python -c "import myextension; assert myextension.__version__ == '0.3.0'"
python -m jupyter server extension list
python -m jupyter labextension list
```

前后端列表都出现 `myextension` 后，必须彻底停止并重启 Jupyter Server，关闭旧页签再强制刷新。只刷新浏览器不会替换已运行的旧 Python 后端。

## 3. 构建课堂镜像

基础镜像必须已包含 Python 3.10+、JupyterLab 4、Jupyter Server 2 和 jsonschema 4。Dockerfile 使用 `--no-deps`，构建时不会临时联网补依赖。

```bash
BASE_IMAGE='<BAMS 提供的 JupyterLab 4 基础镜像>'
TARGET_IMAGE='<单位镜像仓库>/behavior-audit:0.3.0-classroom'
chmod +x build_image.sh verify_image.sh
./build_image.sh "$BASE_IMAGE" "$TARGET_IMAGE"
./verify_image.sh "$TARGET_IMAGE"
```

本次交付到本地构建文件即停止。`docker push` 和平台注册必须由有权限的运维人员另行执行。

## 4. BAMS 运行配置

1. 将平台持久卷挂载到 `/workspace/result`。
2. 使用 `runtime.env.example` 的非密钥变量。
3. 通过平台 Secret 注入 `ARK_API_KEY`，不要写入镜像、Dockerfile 或 env 文件。
4. 保留基础镜像的 `ENTRYPOINT`/`CMD`、Jupyter token/Cookie 鉴权和动态 base URL。
5. 用新镜像 digest 新建工作台验证，不要仅重启仍固定旧 digest 的容器。

`/workspace/result/behavior-audit` 必须由 BAMS 配置真正的持久卷。插件只能写这个路径，不能自己保证容器重建后数据仍在。

## 5. 0.3.0 课堂可靠性行为

- 页面刷新后，只要当前服务端会话仍为 `collecting`，会自动续接。
- 尚未获得服务端回执的事件先写入浏览器 IndexedDB，刷新后继续上传。
- 课堂镜像中 300 秒无心跳的会话会被服务端标记为放弃，并生成已采集部分的本地简报。
- 简报固定包含会话结果、有效观察时长、运行统计、行为证据摘要和可选的关注点。
- 简报是确定性本地派生数据，不依赖 AI，不做评分、排名或掌握度判定。

重要边界：关闭网页后浏览器不再产生新的编辑/运行事件。恢复只覆盖关闭前已经写入 IndexedDB 或 BAMS 的数据，不能采集页面关闭期间的操作。

## 6. 教师端/FinColab 边界

本阶段不会自动把学生简报发送到 FinColab 教师端。下一阶段仍需要平台提供统一的课程/班级/学生身份、服务端持久存储和经鉴权的教师查询接口。这些不是只靠 JupyterLab 插件就能安全完成的。

## 7. 回滚

1. 保留 0.3.0 和上一个已验证镜像的不可变 digest。
2. 将工作台模板指回旧 digest，然后新建工作台验证。
3. 软件回滚不会删除 `/workspace/result/behavior-audit`，任何数据删除或迁移都必须单独授权。
