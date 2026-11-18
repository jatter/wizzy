# 平台账号治理实施与验证记录（0021）

日期：2026-10-26。此记录对应账号治理增量，不代表 FR-A10、WZ-15、WZ-17 或整个项目验收完成。数据库继续使用本地 PostgreSQL；测试使用随机隔离数据库，本轮没有迁移或重读共享开发库。

## 实现范围

迁移 `0021_account_governance` 基于 `0020_evaluation_consent`，为平台账号新增 `governance_revision`，并增加不可变申请与决定表 `platform_account_changes`、`platform_account_decisions`。后端实现位于 `backend/src/wizzy/account_governance.py`，正式页面位于 `frontend/platform/src/AccountGovernance.vue`，账号列表入口由 `App.vue` 接入。

统一接口前缀 `/api/v1/admin`：

| 方法与路径 | 行为 |
|---|---|
| GET `/accounts/{account_id}` | 最小详情及当前版本 |
| GET `/accounts/{account_id}/changes` | 游标分页申请/决定历史与失效状态 |
| POST `/accounts/{account_id}/changes` | 按版本提交角色、启停变更与原因 |
| POST `/account-changes/{change_id}/approve` | 独立管理员批准升权 |
| POST `/account-changes/{change_id}/reject` | 独立管理员拒绝，允许拒绝已失效申请 |

详情/历史要求读取权限，申请要求写入权限，审批要求审批权限。两项新增权限仅授予安全管理员。授予启用安全管理员权限需另一管理员审批，禁止自审；其余有效角色/状态变更即时生效。实际变更增加版本并撤销目标全部已有会话，重新启用不恢复旧会话；保护最后一位启用安全管理员。

后端使用 CAS、按操作者与 UUID operation_id 幂等、同事务审计；批准时重新检查申请人资格及目标版本。申请/决定表 FORCE RLS，运行角色无直接 CRUD 权限，治理函数固定安全 search_path 并撤销 PUBLIC 权限。有治理历史禁止降级。

正式页面支持详情、角色/启停、原因校验、游标历史和独立审批。提交前重验身份/会话/权限；冲突保留草稿并要求显式刷新，网络或5xx导致结果不确定时保留相同 UUID 与请求体原样重试并锁定编辑。异步响应按页面代次隔离；自审无审批按钮，失效申请不能批准，390px 页面布局有浏览器验证。

## 已执行验证

以下为本次页面构建及联调记录，日志存于本机会话临时目录，不作为永久 CI 归档。

| 检查 | 命令 | 结果与证据 |
|---|---|---|
| 生产类型检查与构建 | `npm run build --prefix frontend/platform` | 通过；`/tmp/wizzy-platform-build-0021.log` |
| API 单元测试 | `npm test --prefix frontend/platform` | 19 passed；`/tmp/wizzy-platform-unit-0021.log` |
| Chrome 模拟 API 页面测试 | `PLAYWRIGHT_CHANNEL=chrome npm run test:browser --prefix frontend/platform` | 38 passed，15.9s；`/tmp/wizzy-platform-browser-0021.log` |
| 真实 API / PostgreSQL 页面联调 | `WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest tests/test_platform_web.py -q --tb=short` | 1 passed，7.40s；`/tmp/wizzy-platform-real-0021.log` |
| 联调测试静态检查 | `uv run --directory backend ruff check tests/test_platform_web.py` | All checks passed；`/tmp/wizzy-platform-ruff-0021.log` |

真实浏览器使用正式构建、真实 API 与隔离 PostgreSQL：验证 operator 停用后版本1、重新启用后版本2、提交安全管理员升权后仍为 operator/版本2且等待审批；核对三条申请、前两条生效决定、待审批记录、处理人和审计落库。浏览器继续验证退出与 operator 登录后权限仍受限，页面错误数为0。此联调没有覆盖第二位管理员实际批准/拒绝，相关分支由后端专项与模拟浏览器覆盖，不能混称为真实端到端覆盖。

联调曾因同一原因文字同时出现在“变更原因”和“处理说明”而产生严格选择器冲突；已将定位收紧到 `.governance-reason`，未修改业务逻辑或放宽断言，随后联调通过。

0021后端此前专项为 **73 passed，1 warning，16.96s**（`/tmp/wizzy-governance-tests.log`）；此前完整回归为 **873 passed，1 warning，192.51s**（`/tmp/wizzy-backend-0021.log`）。这次完整回归使用旧前端构建，属于历史后端证据；新页面的真实联调证据为上表独立运行，本次文档收尾没有重跑后端全量。

## 仍未实现或未验收

- 在线邀请/开户、改密与 MFA 生命周期、组织数据范围。独立跨账号会话撤销已由[0031切片](2026-10-26-account-session-revocation.md)补齐；本节其余验证数字仍属于0021历史范围。
- 关闭页面后恢复不确定提交的 UUID/草稿；当前只在本次页面内保留原样重试状态。
- 学校、学生与家长完整治理、真实教材 PDF 解析与授权、真实模型批改/金标准评分/生产发布等后续业务。
- 小程序真机、生产部署、监控和备份恢复演练；本轮未升级共享开发库，代码迁移 head 不表示部署版本。

最新全局范围见[正式工程台账](14-development-status.md)，需求逐项清单见[三端功能列表](../小知功能列表.md)，数据库与错误契约见[数据模型/API](../architecture/06-data-model-and-api.md)。
