# Jupyter 学生知识点侧栏验证记录

日期：2026-09-02
分支：codex/jupyter-knowledge-points-20260902
基线：2920f93

## 范围

- 学生模式只读显示服务端 classroom_session.profile 的发布知识点快照。
- 上下文加载失败或刷新异常时，保持学生权限边界与已有快照，不回退到教师作者界面。
- 构建并校验 myextension 0.4.0 的本地候选 wheel 与 Linux AMD64 交接归档。
- 未修改 BAMS 源码、数据库、课堂资源服务或学生工作区文件。

## 固定验证环境

- Python：3.12.13
- JupyterLab：4.6.3
- Jupyter Server：2.21.0
- 本 worktree editable myextension：0.4.0
- Node/Yarn：Node 26.5.0、jlpm 3.5.0

隔离 worktree 的 .venv 通过以下命令准备；它不改写项目锁文件：

~~~bash
PATH="$PWD/.venv/bin:$PATH" uv pip install --python .venv/bin/python -e ".[dev,test]"
uv pip install --python .venv/bin/python build hatch-nodejs-version
~~~

python -m build --wheel 的默认隔离环境需要联网获取构建后端；本机 DNS 不可用时，使用已安装且满足 pyproject.toml 声明的构建后端执行 --no-isolation。这不是发布镜像时的依赖下载。

## 已执行验证

| 命令 | 结果 |
| --- | --- |
| .venv/bin/jlpm jest src/__tests__/ticketBootstrap.spec.ts src/__tests__/classroomUiBootstrap.spec.ts --runInBand --coverage=false | 2 suites / 8 tests passed |
| .venv/bin/jlpm jest src/__tests__/studentModeSidebar.spec.ts src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand --coverage=false | 2 suites / 97 tests passed |
| PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check | passed |
| PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand | 29 suites / 348 tests passed |
| .venv/bin/python -m pytest -q myextension/tests/test_platform_registration.py myextension/tests/test_student_mode_routes.py | 27 passed；仅绑定本机 127.0.0.1 测试端口 |
| .venv/bin/python -m pytest -q myextension/tests --ignore=myextension/tests/test_labextension_artifact.py --ignore=myextension/tests/test_classroom_release_040.py | 750 passed |
| .venv/bin/python -m pytest -q myextension/tests/test_labextension_artifact.py | 1 passed |
| .venv/bin/python -m pytest -q myextension/tests/test_classroom_release_040.py | 10 passed |
| sh -n deploy/bluedot/release-0.4.0/{build_image,verify_image,export_image}.sh | passed |

wheel 构建和完整性检查：

~~~bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm clean:all
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:prod
.venv/bin/python -m build --wheel --no-isolation
(cd deploy/bluedot/release-0.4.0 && shasum -a 256 -c SHA256SUMS)
cmp dist/myextension-0.4.0-py3-none-any.whl deploy/bluedot/release-0.4.0/artifacts/myextension-0.4.0-py3-none-any.whl
.venv/bin/python -m zipfile -t dist/myextension-0.4.0-py3-none-any.whl
~~~

结果：wheel 的 zipfile 检查完成，候选交付 wheel 的 SHA-256 校验为 OK，两个 wheel 字节一致。

## 产物哈希

| 产物 | SHA-256 |
| --- | --- |
| dist/myextension-0.4.0-py3-none-any.whl | 43232d306d0d9c29c67ddbad752f389d41ad283c03afc3fb9b2bc316b02549a3 |
| deploy/bluedot/release-0.4.0/artifacts/myextension-0.4.0-py3-none-any.whl | 43232d306d0d9c29c67ddbad752f389d41ad283c03afc3fb9b2bc316b02549a3 |
| releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz | 3142e1ff6120be62b440b0c3e22de6e38d1d0bf796a17f2c22807476ec9678d6 |

归档通过如下命令生成和校验：

~~~bash
.venv/bin/python scripts/package_classroom_image_handoff.py --source deploy/bluedot/release-0.4.0 --output releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz
(cd releases && shasum -a 256 -c behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256)
~~~

## 未执行的远端操作与发布前置条件

本轮没有执行 Docker build/push、镜像导入、BAMS 模板替换、工作台重建或远端服务访问。

在单独获得一次性部署授权前，BAMS 运维方必须提供并确认：

1. 唯一的测试工作台模板 ID，以及可恢复的变更窗口；
2. 当前线上插件实际为 0.2.2 的镜像与 wheel 不可变 digest；
3. 0.2.2 的明确回滚 digest。候选包 README 中仍提到 0.3.0，必须先消除该文档与实际运行版本的差异；
4. Linux AMD64 基础镜像的不可变 digest，且其中包含 JupyterLab 4、Jupyter Server 2 与 jsonschema；
5. 已运行工作台是否需要重建、已启动学生任务的资源与发布快照保留策略；
6. BAMS HTTPS 下真实的 /classroom-api 地址和运行时 Secret 注入方案，不能使用 loopback 或工作台端口 40037。

在这些信息齐备前，本地候选包是停止点，不应替换远端 0.2.2 环境。
