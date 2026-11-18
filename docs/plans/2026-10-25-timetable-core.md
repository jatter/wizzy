# 课表核心实施计划（2026-10-25）

## 范围与假设

对应 FR-T01、FR-T02 的 P0 与 T09：本轮实现 PostgreSQL 学期、周模板、冲突显式确认、单次调课/停课/恢复、按日期读取实例。上海时区固定；不实现截图 OCR、教材章节绑定、预习生成或正式三端 UI，不以此宣称 WZ-10/T09 全项验收。

- 学期起止含首尾，最长 370 天；周起始 ISO 1=周一至 7=周日，起始日所在周是第 1 周，学期外不生成实例。
- 模板每节有稳定 UUID、星期、节次、科目、all/odd/even、可选成对 HH:MM 时间和教室；最多 100 节。
- 同日同节次或明确时间半开区间相交即冲突；先返回 409 及结构化冲突提示，显式 confirm_conflicts 才保存。
- 整模板及覆盖写入共用学期 revision 做 CAS，锁学期串行化；模板快照和覆盖历史只追加，审计同事务。
- 覆盖锚定原 slot_id 与 source_date；move 可跨周但仅限同学期，cancel 停课，restore 撤销覆盖；改后仅影响单个实例。模板若修改仍有有效覆盖的课程，先要求恢复覆盖再改模板，不静默丢弃调课。
- 家长写、儿童仅绑定学生读；数据库 FORCE RLS 实时核验家庭/学生/成员状态、细分学生授权、同意及删除代次。worker/dispatcher 无权限。
- 接口：POST/GET /students/{id}/terms，GET/PUT /timetables/{term_id}，GET /timetables/{term_id}/instances，POST /lesson-overrides。列表沿用游标；实例查询最多 93 天。

## 步骤与验证

1. 先写真实 PG API 行为测试并确认缺接口导致预期 RED。
2. 新迁移 0014、输入约束、课表服务和路由 → 专项测试 GREEN。
3. 增补跨周/单双周/冲突确认/单次覆盖/CAS并发/隔离/撤回/审计回滚/迁移最小权限 → 全量 pytest 与 Ruff。
4. 同步架构、功能列表、验收与开发台账，记录实际结果和未覆盖范围；不迁移共享开发库。

## 执行结果与验证证据

- 已增加0014迁移、课表服务与输入契约，注册六个API操作；新增44项行为测试及5项独立库迁移测试。
- 专项 `uv run --directory backend pytest tests/test_timetables.py tests/test_timetable_migration.py -q`：**49 passed，1 warning，8.85s**。
- 全量 `uv run --directory backend pytest -q`：**534 passed，1 warning，127.51s**，退出码0；`uv run --directory backend ruff check src tests migrations`：**All checks passed!**。以上命令从工作区根目录运行；警告为既有Starlette TestClient/httpx弃用提示。
- 全量首轮的10项旧head0013断言已精确改为当前head0014；显式降级目标/回滚基线仍保留0013。恢复冲突夹具改为第2周对应even，未放宽实际冲突或权限规则。
- 同步 [数据模型与API](../architecture/06-data-model-and-api.md)、[本地开发](../local-development.md)、[三端功能列表](../小知功能列表.md)、[开发台账](14-development-status.md)、[需求追踪](12-backlog-and-traceability.md) 与 [验收证据](../11-test-acceptance-and-operations.md)。

## 未完成边界

测试仅使用随机隔离PostgreSQL库与受限角色，未迁移或重读共享开发库、未改凭据。没有启动API或执行前端构建、浏览器/真机、正式三端联调及真实模型调用。学期编辑/删除、截图OCR、教材章节/预习联动、提醒及正式UI仍未实现；写接口不支持操作键幂等，成功后旧revision重试409，创建丢响应先查列表。此切片不代表WZ-10/T09或项目整体完成。
