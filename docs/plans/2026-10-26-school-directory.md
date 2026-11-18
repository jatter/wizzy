# 学校目录实现与验收记录（0022）

> 2026-10-26。本阶段覆盖FR-A09学校治理中的基础目录，不代表完整学校治理或全项目验收。数据库使用本机PostgreSQL，测试使用随机隔离数据库及受限运行角色；未迁移共享开发库。

## 实现范围

迁移`backend/migrations/versions/0022_school_directory.py`基于`0021_account_governance`。`school_directory`保存唯一学校代码和当前版本指针，`school_directory_revisions`记录不可变配置、原因、操作者、请求快照和操作UUID；复合延迟外键将当前指针绑定对应学校的版本。代码固定，新增或修订均从版本历史读取返回值。

后端`backend/src/wizzy/schools.py`提供列表、登记、详情、修订和历史五接口；精确请求、字段及错误契约见[数据模型/API](../architecture/06-data-model-and-api.md#当前学校目录0022)。名称和地区1–120字，原因1–500字，学年为相邻年份，状态为启用/停用；写入含UUID幂等键，修订还需严格整数版本CAS。学校目录及修订历史、审计在同一事务提交。修订历史在数据库层拒绝UPDATE、DELETE和TRUNCATE，当前版本必须引用同一学校的已有修订。

security_admin拥有`platform.schools.read/write`，operator只读。实际平台会话、实时账号和角色每次执行重新校验；FORCE RLS及窄化SECURITY DEFINER能力保护底层表，运行角色无直接CRUD。学校登记/停用不会改变家庭、学生或作业权限。

正式平台`frontend/platform/src/SchoolDirectory.vue`已接通搜索/状态筛选、游标分页、登记、固定代码编辑、启停和历史；详情保存历史原因及处理人。CAS冲突保留草稿并由用户显式载入最新版；写入回执不明时锁定字段、保留原UUID和请求体重试。身份/权限撤销清理页面与草稿，异步响应按页面代次隔离；390px布局有浏览器覆盖。关闭页面后不持久恢复草稿或不确定请求。

## 验证记录

日志位于本机会话临时目录，不能作为永久CI归档。

| 检查 | 命令 | 结果 |
|---|---|---|
| 平台类型检查/生产构建 | `npm run build --prefix frontend/platform` | 通过；`/tmp/wizzy-schools-build.log` |
| 平台API单元 | `npm test --prefix frontend/platform` | 23 passed；`/tmp/wizzy-schools-api.log` |
| 学校专项Chrome交互 | `PLAYWRIGHT_CHANNEL=chrome npm run test:browser --prefix frontend/platform -- tests/school-directory.spec.ts` | 11 passed，6.1s；`/tmp/wizzy-schools-browser.log` |
| 平台完整Chrome交互 | `PLAYWRIGHT_CHANNEL=chrome npm run test:browser --prefix frontend/platform` | 49 passed，19.4s；`/tmp/wizzy-platform-browser-0022.log` |
| 正式构建→真实API→隔离PostgreSQL | `WIZZY_BROWSER_TESTS=1 PLAYWRIGHT_CHANNEL=chrome uv run --directory backend pytest tests/test_platform_web.py -q` | 1 passed，9.07s；`/tmp/wizzy-schools-real-browser.log` |
| 学校/平台身份/迁移专项 | `uv run --directory backend pytest tests/test_school_migration.py tests/test_school_directory.py tests/test_platform_identity.py -q --tb=short` | 97 passed，1 warning，14.64s；`/tmp/wizzy-schools-backend-targeted.log` |
| 后端全量（含家庭/平台真实浏览器） | `WIZZY_BROWSER_TESTS=1 PLAYWRIGHT_CHANNEL=chrome uv run --directory backend pytest -q --tb=short` | 924 passed，1 warning，211.48s；`/tmp/wizzy-backend-0022-regression.log` |
| 后端Ruff | `uv run --directory backend ruff check . --output-format concise` | All checks passed；`/tmp/wizzy-ruff-0022.log` |

真实联调验证安全管理员登记学校、读取详情历史、改名/学年/停用后保留两版；operator可搜索并读取历史，无登记/编辑控件。数据库核对current_revision=2、两版完整配置及真实操作者，以及学校写入审计；operator无学校写入审计。原有模型目录和账号治理流程同时回归，页面错误数为0。

模拟浏览器另覆盖重复代码、CAS冲突保留草稿、落库后丢失响应的同请求重试、读写权限撤销、会话过期、服务端异常重试和390px无横向溢出。首轮两项下拉框检查因测试精确label匹配失败，已改用与实际语义一致的combobox定位，未放宽业务断言，随后专项及完整交互通过。

新增`backend/tests/test_school_migration.py`共9项：空库回退后表/函数/角色权限/审计动作约束清理、重复升级、FORCE RLS及三个运行角色无表权限、仅应用角色可调用入口、辅助函数私有及固定search_path、升级/降级事务回滚、目录历史或仅审计阻止降级、UPDATE/DELETE/TRUNCATE不可变历史和当前版本复合外键。专项通过。TRUNCATE用例先复现未抛错，再补BEFORE TRUNCATE语句级触发器后通过；共享开发库未运行0022，无需改写已有库中历史。

首次后端全量为890 passed、23 failed、2 skipped（194.76s）。23项失败均为既有测试把迁移head硬编码为0021：已将对应head断言及回滚参数更新为0022，保留本阶段回退目标0021；未放宽权限、历史或事务断言。完整复跑开启家庭和平台真实浏览器联调，最终924 passed、1 warning（211.48s），无失败或跳过。warning为Starlette TestClient对httpx的弃用提示。

## 未完成边界

- 组织树、年级班级、学校管理员及组织数据范围。
- 名册导入、逐行错误、校验预览和回滚；在读关系及家长关联，同名学生不能自动合并。
- 正式模型/OCR/批改金标准与评分、教材PDF→TXT及版权审核、独立复习评估等全项目后续需求。
- 小程序真机、生产部署、监控、备份恢复和试点验收。

完整开发状态见[工程台账](14-development-status.md)，需求逐项范围见[三端功能列表](../小知功能列表.md)。
