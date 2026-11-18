# 教材 PDF 安全接收实施计划（0026）

日期：2026-10-26。FR-A11 / WZ-11 的文件接收切片；接续0025权利登记，不代表整票验收。

## 范围与假设

- 仅已批准、授权日期当前有效且包含storage用途的教材可申请/完成上传；上传凭证固定绑定教材修订，修订或撤回后拒绝继续上传。地区授权留存，存储本身不向任何地区提供正文检索。
- 单份PDF最多50 MiB，先登记文件名、字节数及SHA-256，15分钟内完整上传；服务器校验MIME、长度、摘要及PDF签名，私有目录使用服务端UUID文件名、0600文件和0700目录。
- 状态仅awaiting_upload → quarantined。签名校验不是结构解析或病毒扫描；隔离文件没有下载/模型检索接口，content_available始终false。病毒扫描、页数/结构校验、异步解析/OCR、TXT校核和发布作为后续切片，禁止伪造通过状态。
- 元数据申请使用actor+UUID幂等；同一凭证允许完整字节流重传，摘要一致才确认接收；PUT完成和已隔离重放都重新检查原申请人、当前权限、权利与绑定修订；15分钟期限仅对awaiting_upload生效。POST相同操作及参数重放在会话和write权限检查后返回历史凭证，不重新核验权利、修订或期限，不代表可继续上传。原始文件写入与数据库提交无法构成跨资源事务，失败时最多留下无可读接口的隔离孤儿文件；重传可恢复，不自动删除可能已提交的文件。
- 不迁移共享开发库，不修改真实凭据；使用隔离PostgreSQL、受限角色和临时存储测试。

## 实施与验证

1. 0026迁移和四个公开接口（列表、申请、详情、PUT内容）：验证权限/授权、幂等、冻结修订、到期、审计回滚、RLS及迁移保护。
2. 正式平台PDF接收面板：文件选择/摘要/申请/上传/重试、清晰隔离状态；验证类型/构建、浏览器及真实API流程。
3. 同步API架构、功能列表、开发记录；专项和完整回归通过后记录实际证据。

## 执行记录

- 开始：0025完整后端1064项和正式平台81项浏览器通过，未迁移共享开发库。

- 实现：0026 `textbook_documents`、FORCE RLS和受限SQL能力，四个公开接口；私有0700目录、0600文件、SHA-256/长度/签名、120秒流超时、无覆盖提交及原字节恢复。正式平台接通文件摘要、隔离接收、分页详情、同请求重试和刷新恢复。scan_status固定not_scanned，content_available固定false。
- 修复：浏览器测试的403期望改为权限错误与上传状态清理（保持登录），选择器改为文件标签精确匹配；真实联调审计核对使用platform_audit_events.actor_id/action。
- `backend/.venv/bin/pytest -q backend/tests/test_textbook_documents.py --tb=short`：35 passed；教材文件迁移专项5 passed。日志 `/tmp/wizzy-0026-api-retry.log`、`/tmp/wizzy-0026-migration.log`。
- 平台`npm run build`类型检查和Vite构建通过（1.74s），API单元34 passed；教材PDF浏览器10 passed（4.6s），全平台浏览器91 passed（55.9s）。日志 `/tmp/wizzy-platform-0026-build.log`、`/tmp/wizzy-platform-0026-unit.log`、`/tmp/wizzy-platform-0026-browser-retry.log`、`/tmp/wizzy-platform-0026-browser-full.log`。
- `WIZZY_BROWSER_TESTS=1 PLAYWRIGHT_CHANNEL=chrome backend/.venv/bin/pytest -q backend/tests/test_platform_web.py --tb=short`：1 passed（48.13s）。真实构建→API→隔离PostgreSQL→私有文件，核对教材revision3、文件元数据及摘要、隔离状态、精确字节、0600与两项审计。日志 `/tmp/wizzy-platform-0026-real-api.log`。
- 后端Ruff通过；`WIZZY_BROWSER_TESTS=1 PLAYWRIGHT_CHANNEL=chrome backend/.venv/bin/pytest -q backend/tests --tb=short`：1104 passed，1 warning（302.39s），退出码0，无失败或跳过。warning是既有Starlette TestClient/httpx弃用提示。日志 `/tmp/wizzy-backend-0026-ruff.log`、`/tmp/wizzy-backend-0026-regression.log`。
- 全部数据库验证使用随机隔离库与受限角色，未迁移/重置共享开发库。结构/页数及病毒校验、解析/OCR、TXT校核、抽检和发布/撤回/检索仍未开发，FR-A11/WZ-11未整体验收。
