# 复习工作台首切片实施计划

> 2026-10-25。承接 0012 订正记录；不是 WZ-09 或整个项目验收。

## 需求与边界

- FR-C07：先作答、草稿可恢复、不会/跳过不记完成。
- FR-M03：只有两次不同日期的独立正确证据才建议掌握。当前无通过质量门禁的评估器，所以本切片提交只能进入待核对；不产生独立正确证据、不推进掌握。
- FR-M04：先建立首次 D+1 任务和家长改期。D 取当前 OCR 题文版本首次监护人确认错误的上海日期；沿用架构当前 Asia/Shanghai 约定，不冒称已支持学生自定义时区。
- 原始作业、订正、复习统计隔离。题文或有效判定源变化立即禁止旧任务写入，历史保留。
- 单题/有效修订的第 1 步任务自然唯一；重复建任务不重复排程。列表不等于周月优先级计划。

## 可执行契约

1. 监护人 POST `/api/v1/questions/{id}/review-tasks`，携带 `expected_effective_revision`，自然幂等建首个任务。
2. 有权儿童/监护人 GET `/api/v1/students/{id}/review-tasks`（created_at/id 稳定分页，可只看到期）；GET `/api/v1/review-tasks/{id}` 保留旧源历史及 `source_is_current`。
3. 仅绑定儿童 PATCH `/api/v1/review-tasks/{id}/draft`，`expected_draft_revision` CAS，文字 0–8000 字符，可清空、不允许 NUL。
4. 仅绑定儿童 POST `/api/v1/review-tasks/{id}/attempts`，UUID `Idempotency-Key`，携带 `expected_draft_revision` 和 `outcome=submitted/needs_help/skipped`。保存当时草稿快照并推进草稿版本；submitted 需非空白并冻结为 awaiting_review，另外两种仍 open、保留草稿。操作不能推进 step 或记完成。
5. 监护人 POST `/api/v1/review-tasks/{id}/reschedules`，UUID 键，`expected_schedule_revision/due_date/reason`，保留追加历史。允许过去日期以显式标为逾期，不偷偷裁剪。
6. 重试返回当前任务投影（不是首次 HTTP 快照），实时授权；同键异体 409。读接口不提供原答、参考答案或反馈。

## 实施与验证

1. 新增真实 PG 行为测试，先记录缺失接口失败。
2. 0013 迁移：私有 FORCE RLS 表、最小能力函数、固定安全 search_path、无运行角色直表权限；源题和任务锁序一致；API 写入和审计同事务。
3. 专项验证：隔离库迁移、重复升级、空降级/重升、有历史拒降级、升级失败回滚；鉴权、同意、旧源、幂等、CAS 并发与审计故障回滚。
4. 全量 pytest 与 Ruff；更新 06/功能列表/11/12/14/本地开发文档，记录真实结果，不迁移共享开发库。

## 未包含

图片复习作答、答案/提示解锁、独立评估证据、D+3/7/14 推进、失败重置 D、同日成功去重、掌握/复错、自动排程 worker、周月计划和完成汇总、正式客户端联调。不能用本切片的 submitted 或人工订正核对代替这些能力。


## 执行记录（2026-10-25）

- 已落地0013三张私有表、六接口、两类CAS、作答/改期幂等、实时授权和同事务审计；实现仍严格限定首步文字复习记录，不把提交计为完成或掌握。
- 行为专项最初44项有2项夹具/错误码预期失败，修正为有效监护人学生绑定及任务ID撤回同意404契约后通过；未放宽安全规则。另补5项独立随机数据库迁移测试，覆盖受限能力、空降级重升、重复升级、有三种历史拒降级及升级故障整体回滚。
- 本轮专项：`uv run --directory backend pytest -q tests/test_review_workbench.py tests/test_review_migration.py --tb=short`，**49 passed，1 warning，14.81s**；全量：`uv run --directory backend pytest -q --tb=short`，**485 passed，1 warning，124.59s**；Ruff全量 **All checks passed!**。保留既有Starlette TestClient/httpx弃用警告。
- 已同步 [API](../architecture/06-data-model-and-api.md)、[功能列表](../小知功能列表.md)、[验收](../11-test-acceptance-and-operations.md)、[任务追踪](12-backlog-and-traceability.md)、[实施台账](14-development-status.md)、[本地开发](../local-development.md)。这些记录是首切片后端证据，不是WZ-09/T08整体验收。
- 未迁移/重读共享开发库、未bootstrap/重置真实凭据；未重跑前端构建、浏览器、截图或API启动，正式三端尚未接入，无真实模型/账单/质量与生产验收。本计划上文“未包含”项继续保留，不标为已交付。
