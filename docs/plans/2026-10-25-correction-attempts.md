# 订正记录与监护人逐次核对 Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** 实现 FR-C06 的文字/重拍订正、不可变尝试及人工判定历史，原作业证据与正确率口径不变。

**Architecture:** PostgreSQL 私有 FORCE RLS 表与限定 SECURITY DEFINER 函数；API 延用实时家庭/学生授权及同意锁。提交绑定题目 effective_revision，判定另用 CAS 与幂等键；人工确认不是模型金标准，也不是独立练习/掌握证据。

**Tech Stack:** 现有 FastAPI、Pydantic、SQLAlchemy、Alembic、PostgreSQL 18、pytest；不新增依赖。

---

## 契约与边界

- POST `/questions/{id}/correction-attempts`：`expected_effective_revision`，`student_answer` 或 `media_id` 二选一；`Idempotency-Key` UUID。只能对当前监护人确认错误的新版本提交；不依赖派生队列是否已消费。新尝试一律 pending_review/unverified，不自动批改。
- GET 同路径：已授权的该题尝试，created_at/id 升序游标；返回原题版本快照、作答、每次人工核对、source_is_current；无供应商内部数据/用户ID/存储路径。
- POST `/questions/{id}/correction-attempts/{attempt_id}/judgments`：授权监护人、expected_judgment_revision、verdict（correct/incorrect/pending_review）、reason；带 UUID 幂等键。保存每次判断、不改题目判定、不直接掌握。
- 同键同体复用，同键异体409。键按题目/尝试及可信 actor 隔离；重放仍需实时授权，允许返回已失效的历史但标记 source_is_current=false。新判断要求源版本仍有效。
- 函数与 API 双层校验；同意/删除代次/原作业结果有效性持续重验。无真实模型调用、额度预占/结算、题目改判事件或学习状态副作用。
- 图片引用必须同租户/学生/删除代次且已清洗，内容仍通过原有鉴权入口获取。不支持在本接口混传图片与文字或伪造判定/掌握。
- 本切片不涵盖自动订正评估、提示、FR-C07 草稿/不会/跳过、FR-M03 独立练习与掌握、报告/三端正式接入。

### Task 1: 行为契约（RED）

**Files:** Create `backend/tests/test_correction_attempts.py`。

1. 使用真实结果 fixture，确认原题错误；测试待核对、核对历史、再次尝试、原证据不变。
2. 运行 `uv run --directory backend pytest tests/test_correction_attempts.py -q --tb=short`；预期新增路由404，验证缺失而非测试错误。
3. 补权限、图片、幂等/CAS、源版本、撤回/代次、分页、并发/回滚及私有权限测试。

### Task 2: 持久化与接口（GREEN）

**Files:** Create `backend/migrations/versions/0012_correction_attempts.py`, `backend/src/wizzy/corrections.py`; modify `backend/src/wizzy/schemas.py`, `backend/src/wizzy/main.py`。

1. 建不可变尝试与判断表（复合外键、唯一操作键、校验与分页索引），不授予运行账号直接读写权限。
2. 实现固定 search_path、撤销 PUBLIC 的授权读/锁/提交/判断函数，API 只授权执行公共入口；worker/dispatcher 无入口执行权。
3. 新增输入校验、统一错误、审计事务和游标分页。失败不留下半条尝试或审计，重放不重复审计。
4. 针对性运行测试，再运行 Ruff；未验证前不得声明完成。

### Task 3: 迁移与安全回归

**Files:** Create `backend/tests/test_correction_migration.py`; update head assertions in `test_bootstrap.py`, `test_mistake_migration.py`, `test_verdict_migration.py`。

1. 每测试随机隔离库验证0011升级、空降级、存在历史拒降级；失败后版本与记录保留。
2. 验证 restricted API/worker/dispatcher 不能直接修改私有表或越权执行。
3. 运行全量 `uv run --directory backend pytest -q --tb=short` 与 `uv run --directory backend ruff check . --output-format concise`。

### Task 4: 文档与验收证据

**Files:** Update `docs/architecture/06-data-model-and-api.md`, `docs/plans/14-development-status.md`, `docs/plans/12-backlog-and-traceability.md`, `docs/小知功能列表.md`, `docs/11-test-acceptance-and-operations.md`, `docs/local-development.md`。

1. 记录已实现 API/操作键/状态及测试证据、未完成边界；历史记录不冒充新验证。
2. 检查 Markdown 链接、JSON 与代码围栏。
3. 不迁移共享开发库、不调用真实模型、不清空 Redis；当前目录非 Git 仓库，不创建提交/worktree。

## 实施与验证记录（2026-10-25）

- Task 1–3 的接口、私有迁移与测试已落地；专项47项通过、全量436项通过（109.14s，保留1条既有依赖弃用警告），Ruff通过。此处只记录已有执行证据，不补称未记录的初始RED运行。
- 首次全量428项通过、1项失败：写入与列表返回同一时间戳时小数尾零格式不同。已统一写入响应的datetime序列化，新增0–6位小数的7项确定性测试，保留审计故障整体回滚的完整对象相等断言。
- Task 4 已同步API契约、三端功能列表、需求追踪、实施台账、测试证据及本地联调说明；文档校验结果见 [实施台账](14-development-status.md)。
- 未迁移或重读共享开发库、未清理共享Redis、未重置凭据、未调用真实模型；前端未联调此API。本切片不包含自动评估、独立练习/掌握或报告联动，不等于整票验收。
