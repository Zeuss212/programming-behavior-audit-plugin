# 本地课堂互动演示设计

## 目标

在一台开发 Mac 上提供可重复的互动闭环：教师发布课堂方案，学生接受任务并打开本地 JupyterLab 开始监控，教师随后查看本地生成的简报。所有身份、课程、证据与日志均为演示数据。

## 范围与非目标

- 保留现有 `docker-compose.test.yml` 作为 API 契约/故障测试环境，不改变其占位前端和占位 Jupyter 服务。
- 新增独立的 `local-demo` 编排、卷与端口；不连接远端 FinColab、BAMS、5179/5180、真实 AI 或真实学生数据。
- 不合并根目录 `main`，不推送镜像，不修改 BAMS 配置或模板。
- 本轮演示不覆盖真实 45 分钟课堂容量验收、BAMS HTTPS 入口或正式发布。

## 架构

本地服务层由 Docker Compose 运行：PostgreSQL、私有 MinIO bucket、课堂同步 API/worker、Nginx，以及一个仅供演示的 FinColab façade。façade 提供教师与学生的固定登录、项目/工作台读取与课堂同步服务所需的身份验证；它只接受本地测试 token，并对跨课程学生返回拒绝结果。

真实 Vue 前端在独立前端 worktree 中通过 Vite 运行，课堂开关固定开启。Vite 将 `/api` 转发给 façade，将 `/classroom-api` 原样转发给本地 Nginx。教师和学生使用不同浏览器 profile 登录，避免共享 localStorage token。

真实 JupyterLab 在 Mac 本机运行并安装当前候选插件。仅在 `LOCAL_CLASSROOM_DEMO=true` 时使用 `http://127.0.0.1:18081/classroom-api` 与 `JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK=true`；这两个开发例外不得进入镜像、默认运行配置或 BAMS 模板。学生工作台链接由 façade 指向本地 JupyterLab。

## 固定本地拓扑

| 入口 | 地址 | 责任 |
| --- | --- | --- |
| 教师/学生网页 | `http://127.0.0.1:5175` | Vite 实际 Vue 前端 |
| 演示 FinColab façade | `http://127.0.0.1:18082` | 本地登录、项目与工作台数据 |
| 课堂同步 API | `http://127.0.0.1:18080` | 仅调试健康检查 |
| 课堂 API 代理 | `http://127.0.0.1:18081/classroom-api` | 前端与插件使用的统一前缀 |
| 本地学生 JupyterLab | `http://127.0.0.1:8888/lab` | 安装插件后的学生工作台 |

Compose 使用独立的 `classroom-local-demo-*` 命名卷。停止演示默认只停止容器；清除演示数据必须是显式的、仅匹配这些命名卷的 reset 命令。

## 演示数据与权限

façade 固定提供教师 `teacher001`、学生 `student001`、跨课程负例 `student002`、一个课程与一个父实验。密码、token、插件 JWT、数据库凭据和对象存储凭据全部是 Compose 内的本地测试值，不能复用到远端。教师只能发布自己的实验方案；`student001` 只能看到自己的任务；`student002` 不能读取该课程；原始证据只存入本地私有 MinIO bucket，教师页面仅显示简报。

## 交互流程与错误处理

1. 教师登录，打开已有实验的“课堂方案”，填写并发布。
2. 学生使用独立 profile 登录，接受任务并打开本地 JupyterLab。
3. 插件以本地 loopback 开发配置注册、采集并手动提交；网络或服务失败应显示现有可恢复错误状态，不伪造成功。
4. 教师刷新监控页，读取该学生的简报；跨课程账户被拒绝。

启动脚本必须先检查端口、Compose readiness、前端构建期开关与插件 wheel；任一检查失败即停止并显示受影响组件。停止脚本只关闭本地 demo 进程和本地 demo Compose 项目，不结束未知进程。

## 验收

- 自动化：façade 权限测试、Compose readiness、课堂 API 契约、Vite proxy 配置、插件 loopback 开发配置边界与现有完整回归均通过。
- 手工：按上述教师/学生流程完成一次发布、接任务、JupyterLab 监控/提交和教师简报读取；`student002` 读取被拒绝。
- 隔离：演示过程不访问远端地址、不含真实凭据，且 `main`、BAMS、候选容器与持久生产数据均无改动。
