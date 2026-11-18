# 学校组织、年级与班级实施契约（0023）

对应 FR-A09，承接 0022 学校目录。此增量包含真实 PostgreSQL 能力、平台正式页面、修订历史和测试。学校管理员组织授权、学生名册、在读与监护关系属于后续工作，不因结构创建自动授权。

## 规则与接口

- 学校作为隐式根；校区只能位于根，部门可位于根/校区/部门，年级可位于根/校区/部门，班级必须位于年级。节点最多八层（根不计）；禁止循环、自指、跨校、跨学年挂接，移动节点后所有后代仍须满足八层限制。
- 节点固定学校、学年、代码与类型；名称、父节点和启停状态可修订。同校同学年代号唯一，不同学校或学年可复用。年级和班级同样是节点，不根据姓名或显示名称合并。
- 学年为 2000–9998 开始的相邻 ASCII 年份；学校默认学年仅提供页面初值，历史学年可读取和维护。每校每学年上限 2000 节点；树响应一次返回完整快照，达到上限明确拒绝新增，避免分页丢失父节点。
- 节点启停与学校/祖先的有效状态分别展示。父级停用使后代有效状态停用，不修改后代原始配置或历史；学校停用时允许读取和原操作重放，拒绝新增结构变更。
- 复用 `platform.schools.read/write`，实时校验独立平台会话；operator 只读，security_admin 可写。应用/worker/dispatcher 不直接访问结构表；仅应用拥有窄安全定义者能力。
- 所有变更带原因、UUID 操作编号、CAS 版本；同一账号操作编号对应唯一原请求，重放返回当次修订。账号锁保护幂等，同校写锁防并发成环，修订和审计同事务提交，历史不可 UPDATE/DELETE/TRUNCATE。
- `GET /api/v1/admin/schools/{school_id}/structure?academic_year=2026-2027` 返回 `{items, academic_year, school_status, school_revision}`，包含停用节点。
- `POST .../structure` 新建；`GET .../structure/{node_id}` 详情；`GET .../structure/{node_id}/revisions` 游标历史；`POST .../structure/{node_id}/revisions` 修订。
- 正式页面支持学年切换、组织树、详情、追加历史、登记/编辑/移动/启停；版本冲突保留草稿并显式载入最新版本；回执不明锁定原请求以相同编号重试；权限撤销与页面切换隔离迟到响应；390px 布局可用。

## 验证记录

- 后端组织、组织迁移及学校迁移专项：`60 passed，1 warning（14.90s）`；全后端`.venv/bin/ruff check .`通过。
- 平台`npm test`：`26 passed`；`npm run build`类型检查及Vite生产构建通过。
- `PLAYWRIGHT_CHANNEL=chrome npm run test:browser -- tests/school-structure.spec.ts`：`9 passed（5.6s）`；全平台Chrome：`58 passed（22.7s）`。
- `WIZZY_BROWSER_TESTS=1 .venv/bin/pytest tests/test_platform_web.py -q --tb=short`：`1 passed（14.17s）`。浏览器从正式构建经同源代理操作真实受限API，登记两年级与一班、跨年级移动班级、停用上级并读取有效停用状态；operator重登录只读历史。数据库核对三个节点的固定标识、五条原始/追加配置、原因、操作者、五个不同操作编号和审计。
- 完整后端回归`WIZZY_BROWSER_TESTS=1 .venv/bin/pytest -q --tb=short`：`975 passed，1 warning（222.31s）`，无失败或跳过；包含家庭/平台真实浏览器。运行日志：`/tmp/wizzy-backend-0023-regression.log`。共享开发库不在此阶段自动迁移；测试使用随机隔离数据库及受限角色。
- warning为既有Starlette TestClient/httpx弃用提示。移动端视觉检查截图：`/tmp/wizzy-school-structure-mobile.png`（模拟数据，非生产验收）。
