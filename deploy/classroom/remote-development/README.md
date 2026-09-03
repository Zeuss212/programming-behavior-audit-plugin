# 远程 FinColab 开发配对

这套 Compose 仅用于本地前端连接远程 FinColab 时的课堂同步服务。它与 `classroom-local-demo` 的数据库、MinIO、网络和端口完全隔离，不会读取 `.env.local-demo`。

必须保证以下三者一致：

- 前端 `VITE_PROXY_TARGET` 指向的 FinColab；
- `CLASSROOM_FINCOLAB_BASE_URL` 指向的 FinColab；
- `CLASSROOM_FINCOLAB_ORGANIZATION_ID` 与前端当前组织一致。

否则浏览器的 bearer token 会被另一套身份系统拒绝，页面会显示 401/403。

## 本地配置

~~~sh
cp deploy/classroom/remote-development/runtime-config.example \
  deploy/classroom/remote-development/.env.remote-development.local
~~~

编辑 `.env.remote-development.local`，只写本机的远程开发地址、组织 ID 和本地插件 JWT secret。该文件命中全局 `*.local` 忽略规则，不得提交。

## 启动与停止

~~~sh
scripts/start_remote_classroom_development.sh
scripts/stop_remote_classroom_development.sh
~~~

就绪后 API 仅监听 `http://127.0.0.1:18083`。前端开发模式的 `VITE_CLASSROOM_PROXY_TARGET` 应指向该地址。

停止脚本保留本地 PostgreSQL 和 MinIO 卷。旧的错配服务不会自动迁移数据；如果某实验从未成功发布课堂版本，界面会明确提示重新发布，不会伪造时间或参与范围。
