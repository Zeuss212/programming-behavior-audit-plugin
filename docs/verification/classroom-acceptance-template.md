# 课堂平台验收记录模板

此模板记录测试课程的客观证据，不代替发布授权。所有空白项由验收人填写；不得把
“未执行”“页面前置项未完成”或 `acceptance_valid=false` 写成通过。

## 版本与范围

| 字段 | 填写值 |
| --- | --- |
| 测试课程标识 | |
| 验收日期与时区 | |
| 验收人 / 观察员 | |
| Git SHA | |
| sync-api / worker digest | |
| 插件 digest | |
| 教师与学生前端 digest | |
| BAMS 测试模板标识 | |
| BAMS 工作台入口（应为 HTTPS 40037） | |
| 旧版本与回滚负责人 | |

## 本地加速预检（开发证据）

在隔离本机 Compose 环境执行：

```bash
docker compose --project-name classroom-soak-verify-YYYYMMDD -f deploy/classroom/docker-compose.test.yml up -d --build --wait
python scripts/classroom_soak.py --accelerated --students 30
docker compose --project-name classroom-soak-verify-YYYYMMDD -f deploy/classroom/docker-compose.test.yml down --volumes --remove-orphans
```

把 `YYYYMMDD` 换成当日未被使用的标识，并确保三条命令使用完全相同的项目名。将 JSON
原文或脱敏文件位置填入下表。该命令必须产生 `acceptance_valid: false`；它仅
验证本地 API 并发路径，不替代 BAMS、40037、浏览器、插件持久卷或真实 45 分钟课堂。

| 指标 | 结果 |
| --- | --- |
| `acceptance_valid` | false / 未执行 |
| students | |
| heartbeat p50 / p95（ms） | |
| evidence chunks attempted / accepted receipts / server stored | |
| duplicates（evidence / briefs；本脚本为 not_observed） | |
| missing ranges（本脚本为 not_observed） | |
| outbox peak（本脚本为 not_observed，未覆盖插件发件箱） | |
| final status（completed / partial） | |
| brief revision min / max | |
| 报告位置 | |

## 真实 45 分钟课堂验收

真实环境只在完成部署只读预检、数据库/对象存储备份、功能开关和回滚表后执行。运行
少于 45 分钟视为不合格；`scripts/classroom_soak.py --duration-minutes 45` 目前只做
时长门槛，实际课堂由人工盲审流程记录，避免在没有授权的环境自动发送请求。

| 检查点 | 计划时间 | 实际时间 | 结果与证据 |
| --- | --- | --- | --- |
| 上课开始 | | | |
| 学生 A 首次同步 | | | |
| 学生 A 误关恢复 | | | |
| 学生 A 手动提交 | | | |
| 教师实际下课 | | | |
| +14 分 59 秒仍收集中 | | | |
| +15 分钟学生 B 自动收口 | | | |
| 容器释放后简报可读 | | | |
| BAMS 40037 工作台无回归 | | | |

## 指标、隐私与结论

| 项目 | 填写值 |
| --- | --- |
| heartbeat p50 / p95（ms） | |
| 提交完成时间 | |
| 证据上传失败与补传次数 | |
| 缺失区间 | |
| 完成 / 部分 / 失败简报数 | |
| SSE 可用或轮询降级 | |
| MinIO / 持久卷错误 | |
| 教师跨课程拒绝验证 | |
| 学生方案写保护验证 | |
| 是否泄露 token、Key、完整代码 | |
| `acceptance_valid` | true / false |
| 验收签字与日期 | |

结论规则：只有完成 45 分钟、误关恢复、+15 分钟自动收口、单份简报、40037 无回归、
权限负测和数据保留检查，且验收人签字后，才可以填写 `acceptance_valid: true`。当前
教师端/学生端产品集成与证据下钻页面尚未在本工作树完成时，结论必须为 false。
