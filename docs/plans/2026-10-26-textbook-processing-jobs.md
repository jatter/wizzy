# 教材 PDF 持久处理任务

日期：2026-10-26。接续0026和独立PDF检查/提取组件。

## 本次范围与假设

1. PostgreSQL新增0027：任务绑定文档、教材权利修订、申请人及会话。平台教材写权限可显式申请；同一文档仅一个活动任务，同操作键同体重放，异体冲突；历史与正文分离。
2. 专用数据库能力供现有受限worker角色领取/完成任务；180秒租约、随机令牌围栏、最多三次崩溃恢复领取。执行前和结果保存前复核申请人/会话、教材修订、有效授权及storage/ocr用途。拒绝落库过期worker结果。处理期间不保持数据库事务或行锁。
3. 保存扫描证据及不可变逐页草稿。平台读权限可读状态；正文另需当前教材有效、修订匹配及preview用途。草稿始终待人工校核，不供AI/检索；本次不实现OCR、校核发布。
4. 平台教材文件面板增加申请、任务历史、刷新、状态及逐页草稿查看。网络结果不确定时复用操作键。worker以显式ClamAV配置运行，不提供模拟生产扫描器。正式运行要求Linux；本地macOS仍没有地址空间内存上限。

## 验证标准

- 隔离PostgreSQL验证RLS/能力ACL、迁移重复/往返/事务失败/历史保留。
- API验证身份权限/会话撤销/教材授权变化、幂等及并发、分页、草稿访问用途；worker验证租约围栏/过期回收/结果原子保存与正文不泄露。
- 浏览器验证申请/重试/失败/历史/页码正文/权限变化；完整后端、前端类型检查和构建。
- 病毒库时效代码验证扫描前/后、72小时/未来5分钟边界、显式时区、无效/歧义日期和拒绝后不提取。真实ClamAV引擎、病毒库自动更新/告警、生产Linux资源隔离及部署另行验收。模拟协议/测试注入扫描器只能说明代码行为。

## 依据

- PostgreSQL 18 `SELECT`官方文档：任务领取的行锁与SKIP LOCKED行为。
- ClamAV官方ClamD Protocol：VERSION/INSTREAM及错误回复。
- pypdf官方extract-text：嵌入文本提取不等于OCR，存在资源开销。

## 实施结果（2026-10-26）

- 迁移`0027_textbook_processing`只新增一张`textbook_processing_jobs`表；扫描证据和逐页草稿保存在结果JSONB，增加request/worker受限能力及两个审计动作。API与worker角色无直接表访问权，API不能领取/提交任务，worker不能读取平台页面接口。
- `textbook_processing.py`提供申请、分页历史、元数据详情及授权逐页读取四接口；`textbook_worker.py`每次最多消费一任务，调用现有`pdf_processing.py`与真实ClamAV适配器，不在扫描/提取期间持有数据库连接。
- `TextbookProcessing.vue`接入教材文件面板，包含原因校验、网络回执丢失的原请求重试、活动任务去重、历史分页、扫描证据、空页提示、草稿页码及失权/迟到响应保护，覆盖390px布局。
- 真实联调已覆盖权利独立审批→PDF隔离接收→显式登记任务→历史/详情→撤回修订；数据库核对任务、修订及同事务申请审计。该联调不启动真实ClamAV，不能作为真实扫描部署验收。

## 验证记录

| 检查 | 命令 | 结果与日志 |
|---|---|---|
| 平台类型与生产构建 | `npm --prefix frontend/platform run build` | 通过；`/tmp/wizzy-platform-build.log` |
| 平台API单元 | `npm --prefix frontend/platform test` | 64 passed；`/tmp/wizzy-platform-api-processing.log` |
| 全平台Chrome交互 | `PLAYWRIGHT_CHANNEL=chrome npm --prefix frontend/platform run test:browser` | 100 passed（55.7s）；`/tmp/wizzy-platform-all-browser.log` |
| 真实平台浏览器/API/隔离PostgreSQL | `WIZZY_BROWSER_TESTS=1 backend/.venv/bin/pytest -q --tb=short backend/tests/test_platform_web.py` | 1 passed（47.43s）；`/tmp/wizzy-textbook-processing-real-api.log` |
| 后端完整回归（包含家庭/平台真实浏览器） | `WIZZY_BROWSER_TESTS=1 backend/.venv/bin/pytest -q --tb=short` | 1248 passed，1 warning（319.48s），退出码0，无失败或跳过；`/tmp/wizzy-backend-processing-freshness-final.log` |
| 病毒库时效与PDF处理专项 | `backend/.venv/bin/pytest -q backend/tests/test_pdf_scan.py backend/tests/test_pdf_processing.py --tb=short` | 93 passed（1.47s）；`/tmp/wizzy-pdf-freshness.log` |
| 真实浏览器与全部教材队列专项 | `WIZZY_BROWSER_TESTS=1 backend/.venv/bin/pytest -q backend/tests/test_platform_web.py backend/tests/test_textbook_processing.py --tb=short` | 47 passed，1 warning（59.50s）；`/tmp/wizzy-queue-isolation-after.log` |
| 后端静态检查 | `backend/.venv/bin/ruff check backend` | All checks passed，本轮复核通过 |

真实联调前发现Node脚本有重复申请段，导致重复声明`processing`且提前访问`documents`；删除上传前的误插段，保留上传后的申请流程，`node --check frontend/platform/tests/real-api.mjs`及真实专项已通过。

随后首次完整后端回归为9 failed、1218 passed（317.45s，`/tmp/wizzy-backend-all-processing-final.log`）。根因是浏览器测试申请后撤回教材，留下queued任务，后续全局领取测试误领该已失权任务；定向红灯复现为1 failed、1 passed（47.26s，`/tmp/wizzy-queue-isolation-before.log`）。联调末尾改用受限worker能力将撤回任务置为blocked，并核对attempts=0和authorization_changed，保留历史，不清表、不改生产领取策略。修复后浏览器与全部教材队列专项47 passed（59.50s，`/tmp/wizzy-queue-isolation-after.log`）。

新增病毒库时效策略：库龄最多72小时、未来容差5分钟（项目策略，并非ClamAV官方规定）；VERSION日期不带时区，使用`WIZZY_CLAMAV_DATABASE_TIMEZONE`显式匹配clamd时区，默认UTC。扫描前后均校验，拒绝无效/歧义/不存在时间、过期及超前；证据日期输出UTC ISO格式。扫描/提取专项93 passed（1.47s，`/tmp/wizzy-pdf-freshness.log`），包含扫描期间过期及过期不启动提取。该结果不能替代真实引擎、更新服务及生产隔离验收。

最终完整回归1248项全部通过；1条warning为既有Starlette TestClient/httpx弃用提示，不代表扫描部署已验收。

所有数据库验证使用随机隔离测试库及受限角色，未迁移/重置共享开发库、未改真实凭据。WZ-11与全项目仍未整体验收；草稿不可用于AI/检索。
