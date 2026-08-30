# 本地课堂互动演示

这套演示只使用本机回环地址上的数据和服务：

| 服务 | 地址 |
| --- | --- |
| Vue 教师/学生端 | http://127.0.0.1:5175 |
| 演示 FinColab façade | http://127.0.0.1:18082 |
| 课堂同步调试 API | http://127.0.0.1:18080 |
| Vue/Jupyter 课堂代理 | http://127.0.0.1:18081/classroom-api |
| 学生 JupyterLab | http://127.0.0.1:8888/lab |

它不会连接 FinColab、BAMS、5179、5180、真实 AI 或生产数据。首次 Docker 构建若本机没有缓存，Docker 仍会拉取公开基础镜像及 Python 依赖；这不包含课堂账户、BAMS 或生产服务的数据连接。

## 本地演示账号

| 角色 | 用户名 | 密码 | 可见课程 |
| --- | --- | --- | --- |
| 教师 | 1 | 1 | course-001 |
| 学生 | 2 | 2 | course-001 |
| 隔离负例 | student002 | local-demo-student2 | course-002 |

教师和学生登录后仍分别使用内部身份 `teacher001` 与 `student001`，已有课堂数据不变。这些密码和令牌均为本地固定测试数据，不能用于任何其他环境。

## 启动

在课堂服务 worktree 执行：

~~~sh
scripts/start_local_classroom_demo.sh
uv build --wheel
scripts/start_local_classroom_jupyter.sh
~~~

Jupyter 启动器保持前台运行。它只监听 127.0.0.1:8888，并且只在该脚本中设置课堂学生模式、回环同步地址和明文回环例外。

在独立的 Vue frontend worktree 执行：

~~~sh
npm ci
scripts/start-local-classroom-frontend.sh
~~~

Vue 启动器同样保持前台运行，只监听 127.0.0.1:5175。它的 local-demo 模式把 /api 转发给本地 façade，并把 /classroom-api 原样转发给本地 Nginx。

## 可选：本地 GLM Coding Plan 教学分析

默认不启用 AI，学生简报仍会正常提交并显示“未配置 AI 分析”。只有在你明确准备消耗 Coding Plan 配额时，才在课堂服务 worktree 本地执行：

~~~sh
cp deploy/classroom/local-demo/.env.ai.example deploy/classroom/local-demo/.env.ai
~~~

在 `.env.ai` 的 `CLASSROOM_AI_API_KEY=` 后粘贴你的 GLM Coding Plan Key，保留默认的 `https://open.bigmodel.cn/api/coding/paas/v4` 与 `glm-5.2`，再停止并重新启动本地 demo。该 Key 只会被 `sync-api` 和 `deadline-worker` 容器读取；不要粘贴到聊天、Jupyter、浏览器存储或 Git。`.env.ai` 已被忽略，不能提交。

重新启动后，以学生账号 `2`（内部身份 `student001`）提交本节简报，在教师课堂监控页刷新：会先显示“AI 分析生成中”，成功后显示“AI 分析已完成”，失败三次后显示“AI 分析不可用”。无论失败与否，学生的基础简报都会保留；AI 内容仅作辅助教学分析，不自动评分。

## 教师—学生演示顺序

教师端的三步“创建实验”弹窗可在本地演示中完整使用：它会为所选学生创建关联实验和本地工作台。这些新建记录只保存在 `demo-fincolab` 进程内；重启该容器后会恢复本文档所述的固定演示数据。

1. 在独立浏览器 profile A 打开 http://127.0.0.1:5175，以 `1 / 1` 登录教师端。
2. 打开 admin/projects，找到 parent-experiment-001 的“课堂方案”，填写计划并发布，然后同步学生任务。
3. 在独立浏览器 profile B 打开相同地址，以 `2 / 2` 登录学生端，进入课堂任务。
4. 学生接受任务，点击进入工作台；页面会打开本地 JupyterLab 并把一次性课堂票据放在 URL fragment 中。
5. 在 JupyterLab 运行任意 notebook 单元，确认插件显示课堂学生模式，然后手动提交。
6. 回到教师 profile，刷新课堂监控页并打开 student001 的课堂简报。
7. 可在 profile C 登录 student002：该账号只能看 course-002，读取 course-001 资源或学生任务会被拒绝。

教师、学生和负例必须使用不同 browser profile，因为 Vue 把登录态保存在 profile 的 localStorage 中。

## 自动验证

启动本地 demo 后，可先执行 C++ 课堂第一阶段后端门禁：

~~~sh
python3 scripts/cpp_classroom_phase1_smoke.py
~~~

该命令登录本地教师，核对顺序表阻断项与链表原始维度问题，两次恢复同一个链表备课会话，保存经过修正的 v3 方案并发布，最后确认会话已关闭。它默认不请求 AI，不同步作业，不调用学生运行或插件端点；标准输出只包含状态、标识符和调用计数，不包含材料源文、教师测试内容或任何提供方输出。

完整的 Python 教师—学生交互闭环仍使用：

在课堂服务 worktree 执行：

~~~sh
PYTHONPATH=scripts uv run --no-project python scripts/local_classroom_demo_smoke.py
~~~

该命令验证 façade 登录、student002 跨课程拒绝、同步服务就绪，并完成发布、接受、插件会话、证据提交和简报生成。它需要一个全新的本地 demo 数据库；若 student001 已提交过本节实验，先按下方“停止与重置”显式重置，再运行该命令。输出不包含 bearer、课堂票据、插件令牌或原始证据。

## 升级顺序

对保留数据的环境升级时，先备份 PostgreSQL，再由单个 `sync-api` 实例执行 `alembic upgrade head`。只有在迁移成功且 `sync-api` 就绪检查通过后，才重启 `deadline-worker` 和学生插件。`0006` 与 `0007` 只添加可空列和唯一约束；应用回滚时可先回退应用镜像并保留这些列，不要在未备份时执行降级迁移。旧证据块没有可信分析摘要时，基础简报仍会保留，AI 分析标记为不可用。

## 停止与重置

~~~sh
scripts/stop_local_classroom_demo.sh
~~~

停止只关闭 classroom-local-demo 容器，保留本地 PostgreSQL 和 MinIO 数据。

开始一场全新的演示前，显式运行：

~~~sh
scripts/reset_local_classroom_demo.sh --yes-reset-local-demo
scripts/start_local_classroom_demo.sh
~~~

reset 只删除 classroom-local-demo-postgres 和 classroom-local-demo-minio 两个命名卷；它不会触碰其他 Docker 卷、BAMS 或生产数据。

## 排障

| 现象 | 处理方式 |
| --- | --- |
| 18080、18081、18082 或 5175 被占用 | 先确认占用者；启动脚本不会结束未知进程。关闭已知演示进程后重试。 |
| Compose 就绪失败 | 运行 docker compose -p classroom-local-demo -f deploy/classroom/local-demo/docker-compose.yml ps，检查 postgres、demo-fincolab 和 sync-api 的健康状态。 |
| Jupyter 启动器提示 wheel 缺失 | 在课堂服务 worktree 重新运行 uv build --wheel。 |
| Vue 页面没有课堂入口 | 确认用 scripts/start-local-classroom-frontend.sh 启动；该命令使用 .env.local-demo。 |
| Jupyter 显示未注册课堂会话 | 必须先从 student001 的课堂任务页点击进入工作台，不能直接打开 Jupyter URL。 |
