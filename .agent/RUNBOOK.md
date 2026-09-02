# RUNBOOK · 本地课堂服务

## 前置条件

- Docker Desktop 已安装并处于运行状态。
- 本地演示使用 Docker Compose；不要把容器网络服务名直接配置给宿主机运行的 Vite。

## 启动本地课堂栈

在 PowerShell 中：

```powershell
Set-Location D:\Bluedot\fincolab\programming-behavior-audit-plugin
docker compose -p classroom-local-demo -f deploy\classroom\local-demo\docker-compose.yml up --build -d
```

预期端点：

```powershell
Invoke-RestMethod http://127.0.0.1:18080/health/ready
Invoke-RestMethod http://127.0.0.1:18081/classroom-api/health/ready
```

## 排查与停止

```powershell
docker compose -p classroom-local-demo -f deploy\classroom\local-demo\docker-compose.yml ps
docker compose -p classroom-local-demo -f deploy\classroom\local-demo\docker-compose.yml logs -f sync-api
docker compose -p classroom-local-demo -f deploy\classroom\local-demo\docker-compose.yml stop
```

- `down -v` 会删除本地演示数据库与证据数据，除非已确认可丢弃数据，否则禁止执行。
- 生产或测试环境上游、凭据、数据库迁移、镜像发布与真实数据验证均需单独授权。
