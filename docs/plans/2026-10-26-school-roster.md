# School Roster and Enrollment Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** 实现 FR-A09 的学校学生名册与逐学年在读关系，接通正式平台；不得自动授予家庭或学校管理员权限。

**Architecture:** PostgreSQL 0024 以学校内固定学生代码标识学生，每学年一条在读记录及不可变修订。独立平台权限、受限 SECURITY DEFINER、实时会话检查、学校行锁、UUID 幂等、CAS 和同事务审计。沿用 Vue 3 / Element Plus 正式页面。

**Tech Stack:** PostgreSQL / Alembic / FastAPI / pytest / Vue 3 / TypeScript / Playwright。

## 约定与边界

- 学生身份为 `(school_id, code)`，不以姓名、年级、同名匹配；跨学校代码不共享身份。学年记录固定学生与学年，显示名作为该学年登记快照；跨年使用同代码明确引用同一校内身份。
- 显示名 1–80 字；仅存代码、显示名、学年、班级、状态、变更原因与审计。不收集证件/电话，不创建或关联家庭 `students` / `student_access`。
- 状态 `enrolled/paused/left/graduated`；在读必须关联同校同学年且学校/全部祖先启用的班级。非在读允许班级为空或保留历史班级。停用学校可读及原请求重放，不可新增变更。
- 登记/修订锁定学校行，与组织移动/启停串行；相同学生同学年冲突不覆盖。逐学年列表按登记时间/ID 稳定游标分页，搜索代码/显示名（按文字，不当作 SQL 通配符），状态筛选。
- 新增 `platform.students.read/write`：security_admin 读写，operator 只读；这些权限不代表学校管理员授权或家庭内容权限。
- 本轮不含批量导入/预览/错误下载/回滚、组织范围授权、家长关联验证、跨学校转学身份合并。FR-A09、WZ-15/WZ-17 及完整项目保持未验收。

## Task 1: 后端测试先行

Files: `backend/tests/test_school_roster.py`, `backend/tests/test_school_roster_migration.py`。

1. 添加登记、跨年同身份/跨校不同身份、同名独立、调班/休学/历史分页、代码冲突、CAS/UUID重放测试。
2. 添加家庭/儿童/无权限拒绝、实时权限撤销、同校同年有效班级约束、直接SQL畸形请求、并发CAS、审计失败回滚。
3. 运行 `.venv/bin/pytest -q tests/test_school_roster.py`，先确认接口未实现导致失败。

## Task 2: 数据库与 API

Files: `backend/migrations/versions/0024_school_roster.py`, `backend/src/wizzy/school_roster.py`, `backend/src/wizzy/main.py`。

1. 三表：校内学生身份、学年在读记录、不可变修订（共三表）；身份与记录都有校内外键，不允许隐式跨校关联。
2. 五接口：GET/POST `/api/v1/admin/schools/{school_id}/roster`、GET详情、GET/POST `/{enrollment_id}/revisions`。
3. 数据库权限/强制RLS/历史禁止UPDATE、DELETE、TRUNCATE；复用已有平台审计，降级有数据时拒绝。
4. 运行专项测试及 Ruff，检查无共享库重置或迁移。

## Task 3: 正式平台

Files: `frontend/platform/src/api.ts`, `frontend/platform/src/SchoolRoster.vue`, `frontend/platform/src/SchoolDirectory.vue`, `frontend/platform/src/styles.css`, `frontend/platform/tests/api.test.mjs`, `frontend/platform/tests/school-roster.spec.ts`。

1. API类型/响应验证，学校详情嵌入权限控制的名册：学年/搜索/状态、游标翻页、登记、详情、历史、调班与在读状态。
2. 409保留草稿且显式载入最新；回执不明锁定输入并同UUID/同请求体重试；撤权/卸载/旧响应不能复活资料。
3. 浏览器覆盖读写/只读/错误/重试/移动布局；运行 `npm test`、`npm run build`、`npm run test:browser`。

## Task 4: 真实联调与文档

Files: `frontend/platform/tests/real-api.mjs`, `backend/tests/test_platform_web.py`, `docs/architecture/06-data-model-and-api.md`, `docs/plans/14-development-status.md`, `docs/小知功能列表.md`, `docs/local-development.md`。

1. 正式构建→真实API→隔离PostgreSQL创建学生、调班、休学，operator只读；数据库核对历史、操作者、审计、没有家庭授权。
2. 完整后端/平台回归；按实际输出记录证据与未完成边界，不把原型或模拟测试当生产验收。

## 执行记录

- 已核对 FR-A09/FR-A10/FR-A15、0023 组织树与现有家庭授权。`rtk` 不可用（exit127），使用原生命令，不打印 `.env` 凭据。

- 已交付0024迁移、名册API、学校详情名册组件、API类型/校验与模拟/真实浏览器链路。账号先于学校加锁；名册修订同事务写审计；数据库禁止历史UPDATE/DELETE/TRUNCATE并保护非空降级。
- 平台API单元 `npm test --prefix frontend/platform`：**29 passed，0 failed**；`npm run build --prefix frontend/platform`：类型检查与构建成功（1.90s）。
- `PLAYWRIGHT_CHANNEL=chrome npm run test:browser --prefix frontend/platform -- --workers=1 --reporter=list`：**67 passed（1.2m）**，退出码0。覆盖名册登记、调班/休学、历史、冲突、回执不明重试、只读和移动布局；日志 `/tmp/wizzy-platform-0024-browser-confirm.log`。
- 后端名册/迁移/身份等专项 **92 passed，1 warning（20.08s）**；真实API浏览器专项 **1 passed（17.11s）**。真实链路创建 `STU_WEB_01`、调入B班、休学并清空班级，核对三条历史与审计、操作者及操作UUID。
- 首次完整回归为 **1 failed，1011 passed**：真实联调用例错误假定会话级隔离库没有其他测试创建的家庭学生。已改为比较本用例操作前后 `students` / `student_access` 数量，验证名册不自动创建家庭身份或授权；不是删除其他测试数据或放宽业务权限。
- 修正后运行 `WIZZY_BROWSER_TESTS=1 PLAYWRIGHT_CHANNEL=chrome backend/.venv/bin/pytest -q backend/tests --tb=short`：**1012 passed，1 warning（243.63s）**，退出码0，无跳过。warning为既有Starlette TestClient/httpx弃用提示。日志 `/tmp/wizzy-backend-0024-regression.log`。
- `backend/.venv/bin/ruff check backend/src backend/tests backend/migrations`：**All checks passed**。
- 原型重新构建并验证静态入口：**45 passed（11.4s）**，详见[原型验证记录](../prototype/VERIFICATION.md)。本轮未重新做视觉截图或依赖漏洞审计。
- 已同步[数据模型与API](../architecture/06-data-model-and-api.md)、[功能列表](../小知功能列表.md)、[本地开发](../local-development.md)、[UI框架方案](../research/13-frontend-ui-framework-and-design-system.md)与[实施台账](14-development-status.md)。未重置/迁移共享开发库；保留导入/回滚、学校管理员范围与监护人关联等未完成边界，不认定FR-A09/WZ-15/WZ-17整体验收。
