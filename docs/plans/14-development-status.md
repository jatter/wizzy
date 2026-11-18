# 14 正式工程实现与验证台账

> 2026-10-25 开始。原型、代码完成、自动化测试通过、真实环境验收是不同状态；不能相互替代。完整需求与测试契约继续以 03、11、12 为准。

## 复习计划历史增量（2026-10-26，0033）

- 已实现按学生和周/月筛选的历史列表、稳定游标分页、授权摘要及详情入口。正式家庭学习页支持家长和已配对儿童重开历史计划、查看当前任务状态并进入既有复习编辑器；刷新失败清除旧详情，切学生/退出隔离迟到响应。预计时长明确为安排估算，提交仍待核验。
- 后端全量 **1377 passed、5 skipped、1 warning（340.22s）**；专项 **28 passed**；家庭客户端 **37 passed**；Chrome浏览器 **9 passed（6.9s）**；真实API/隔离PostgreSQL浏览器 **1 passed（12.82s）**；验证码/教材复验 **85 passed**。全后端Ruff、家庭类型检查、H5和微信构建通过，390px单列检查通过。5项skip为opt-in浏览器；保留Starlette/httpx弃用提示。
- 修正验证码测试将四字符答案误匹配PNG base64的偶发断言，不改变生产认证逻辑。代码head为0033；仅迁移随机隔离库，未迁移或重置共享开发库，未停止8080原型服务。范围与日志见[实施记录](2026-10-26-review-plan-history.md)。完整项目尚未完成。

## 跨账号会话撤销增量（2026-10-26，0031）

- 已完成专用权限、不可变回执、UUID幂等、登录并发边界、原子审计及正式平台详情/历史入口。真实回执丢失后重试不会撤销目标新登录的会话。桌面/390px检查通过。
- 后端完整回归 **1353 passed、5 skipped、1 warning（325.16s）**；平台客户端 **92 passed**；全平台Chrome **142 passed（6.2m）**；真实API浏览器 **1 passed**；全后端Ruff、平台类型检查/构建通过。历史教材迁移夹具已适配旧版认证上下文，专项 **10 passed**。日志及边界见[实施记录](2026-10-26-account-session-revocation.md)。
- 代码head为0032密码验证码登录；共享开发库未部署或重置。5项skip为opt-in浏览器；真实撤销用例单独通过。保留Starlette/httpx弃用及JS包体提示。完整项目尚未完成。

## 固定版本离线评测运行与报告增量（2026-10-26，0030）

- 已实现固定数据集摘要/模型历史修订及prompt/schema/rule/image版本的运行、完整离线预测集导入、服务器六指标/区间/分层报告，以及正式平台创建/导入/报告页面。三表FORCE RLS、受限能力函数、实时授权、CAS/UUID幂等与事务审计均已覆盖；完成历史不可覆盖。
- 后端全量 **1350 passed、3 skipped、1 warning（324.12s）**；客户端 **91 passed**；全平台Chrome模拟浏览器 **135 passed（6.7m）**；评测与原平台真实API/隔离PostgreSQL浏览器 **2 passed、1 warning（53.22s）**。类型/生产构建及全后端Ruff通过；390px报告页面溢出已修复并验证。全量中的3项skip为opt-in浏览器，新增评测真实联调用例在全量后单独执行。JS 581.84kB有分包提示，Starlette/httpx有弃用提示。
- 补充验证：教材时间戳回归先红后绿，撤掉原值保留修复时新用例失败，恢复后教材专项 **36 passed、1 warning（8.46s）**；评测报告视口截图重跑 **1 passed、1 warning（6.38s）**，390px截图和布局断言通过。新增用例未计入前述1350项历史全量；新增测试后的全后端Ruff通过。日志：`/tmp/wizzy-timestamp-regression-red.log`、`/tmp/wizzy-timestamp-regression-green.log`、`/tmp/wizzy-runs-real-screenshots.log`。
- source_reference只是操作人声明的离线来源；未调用提供方，production_eligible始终false。自动PII脱敏、撤回物理清理、真实金标准、真实模型质量及发布治理未完成。共享开发库未升级或重置，完整项目未完成。契约、验证日志和边界见[评测实施记录](2026-10-26-model-evaluation.md#0030固定版本运行完整离线预测与统计报告2026-10-26)。

## 评测数据集、双人盲标、裁决与锁定增量（2026-10-26，0029）

- 已实现私有数据集/样本、双人独立盲标、第三人分歧裁决、人工脱敏确认及不可逆摘要锁定；具体授权/源题修订实时校验、跨分组防泄漏、权限、FORCE RLS、CAS、UUID幂等和事务内审计均已覆盖。授权失效隐藏正文/标注/金标准，重新授权不复活旧样本。
- 正式平台页面已接通两层分页、采集/标注/裁决/锁定、权限空态和错误恢复；不确定回执原样重试、401/403清理和迟到响应隔离。双标一致率不是模型正确率，production_eligible始终false；没有自动PII检测或真实模型调用。
- 本次客户端 **84 passed**、全平台Chrome模拟浏览器 **125 passed（59.0s）**（评测11项）、真实API/隔离PostgreSQL浏览器 **1 passed（47.00s）**、类型/生产构建及全后端Ruff通过。真实联调核对创建/持久读取、修订1、空样本、唯一审计与运营入口隔离；审计检查覆盖翻页。JS 546.86kB仍有500kB分包提示。
- 后端实现阶段全量 **1326 passed、1 warning（358.53s）**为历史结果，本次没有重跑整个后端；本次增量修改前端验证/真实联调及文档。共享开发库未升级或重置；0029当时尚无固定版本评测运行/导入/报告，现由0030补齐离线路径；发布治理与真实模型验收仍待完成，完整项目未完成。详细契约、日志和范围见[评测实施记录](2026-10-26-model-evaluation.md)。

## 教材授权单页 PDF 预览增量（2026-10-26，沿用0028）

- 正式平台已接通按页授权PNG、原件/正文桌面并排与手机上下校核、放大/适合宽度、失败重试。切页/卸载取消请求并释放Blob URL，旧响应隔离；会话、权限、权利或来源失效时清理正文和原件。长教材编号导致390px溢出的问题已修复并固定回归。
- 后端渲染前后复核授权，校验源文件大小/SHA-256，使用临时私有快照和受限子进程，并限制并发/超时/图片；响应禁止缓存。前端校验PNG及大小/尺寸，仅请求当前页。
- API单元**75 passed**；后端预览/正文专项**32 passed、1 warning（8.83s）**；全平台模拟浏览器**114 passed（5.7m）**（最终CSS修复前），修复后教材目录/上传/处理/校核共**47 passed**；真实API/隔离PostgreSQL浏览器**1 passed、1 warning（9.55s）**。三个账号完成真实图片加载、桌面/手机布局、独立发布、TXT下载及撤回，浏览器pageerror为0；Ruff、脚本语法、类型/生产构建均通过，JS 518.67kB仍有分包提示。命令、日志和范围见[0028实施记录](2026-10-26-textbook-content.md)。
- 沿用0028，无新增迁移；未升级或重置共享开发库。本次未重跑完整后端，下面1265项仍为历史结果。扫描使用显式测试替身，图片OCR、AI索引/检索、真实ClamAV与试点部署仍待验收；人工勾选仍非自动原件比对证据，FR-A11/WZ-11及完整项目未整体验收。以下各节保留历史验证范围。

## 教材正文校核、独立抽检与发布增量（2026-10-26，0028）

- 0028新增`textbook_contents`与不可变`textbook_content_revisions`、十二种接口路径；正式平台接通逐页编辑/确认、章节层级及PDF物理源页、提交、独立抽检批准/驳回、TXT发布/下载、撤回和历史。每来源任务一份内容，同教材版本最多一份已发布正文；审核人不得参与该内容的任何创建、修订或提交。
- 正文与历史正文实时复核当前权利及storage/ocr/preview用途，发布和TXT另需retrieval；权利失效仍可撤回。FORCE RLS、API专属受限能力、UUID原请求幂等、CAS、审计原子性及禁止破坏性降级有隔离数据库覆盖；前端包含未保存修改保护、权限失效清理及迟到响应隔离。
- 本轮完整后端**1265 passed，2 skipped，1 warning（271.95s）**，退出码0；后端Ruff通过。两项跳过为需显式开启的家庭/平台真实浏览器测试；warning为既有Starlette/httpx弃用提示。内容/迁移专项24项通过，平台API单元68项通过，正文模拟浏览器专项7项、全平台模拟浏览器107项通过；类型/生产构建通过，JS 513.24kB触发分包提示。
- 0028真实API浏览器隔离库联调已通过（1 passed、1 warning，8.80s），覆盖三个账号、3页真实PDF、worker提取、五版正文/审计、独立发布、TXT下载及撤回；扫描为显式测试替身，未验收真实ClamAV。内容/迁移专项复核24 passed、1 warning（10.98s）。前述1265/2 skipped为新增此真实浏览器测试前的历史完整回归；模拟UI与真实隔离PostgreSQL测试分别记录。未升级/重置或重读共享开发库，未改真实凭据。人工确认不等于系统验证原PDF，TXT发布不等于AI已接入；授权原PDF单页预览已在后续增量完成；图片OCR、AI索引/检索与真实ClamAV部署验收仍待完成，FR-A11/WZ-11及完整项目未整体验收。文件、命令与日志见[0028实施记录](2026-10-26-textbook-content.md)。以下保留历史验证范围。

## 教材持久处理任务与逐页草稿增量（2026-10-26，0027）

- 0027新增一张`textbook_processing_jobs`表、四接口与受限worker能力；申请绑定文档、权利修订、申请人及原会话，同操作键幂等，每文档限一个活动任务。180秒租约、随机令牌、最多三次领取及执行前/保存前授权复核均有覆盖，处理期间不持数据库连接或事务。
- 正式平台接通显式申请、丢回执原键重试、历史分页/刷新、扫描证据与逐页内嵌文本草稿；正文另核preview用途及当前修订，失权清理正文并阻止迟到响应恢复。扫描证据与逐页正文保存在任务结果JSONB；接收凭证仍为`not_scanned`，不能代表后续任务扫描结论。
- 前端生产构建、API单元64项及全平台Chrome100项已通过；本次修复真实联调脚本重复声明，`node --check`通过，真实API/隔离PostgreSQL浏览器专项**1 passed（47.43s）**，后端Ruff通过。修复联调遗留任务的测试串扰后，开启家庭/平台真实浏览器的完整后端回归**1248 passed，1 warning（319.48s）**，退出码0，无失败或跳过；本轮全后端Ruff再次通过。warning为既有Starlette/httpx弃用提示，日志`/tmp/wizzy-backend-processing-freshness-final.log`。
- 新增病毒库时效门禁：扫描前后均校验日期，项目策略为最多72小时、未来容差5分钟，配置clamd时区并将证据归一为UTC；无效/歧义时间及扫描期间过期均拒绝。PDF扫描/提取专项93项通过，真实浏览器与教材队列专项47项通过。
- 未迁移/重置共享开发库，未改真实凭据。草稿始终待校核、未OCR、不可供AI/检索；真实ClamAV、病毒库自动更新/告警与Linux资源隔离部署尚待验收。本节0027验证时尚未提供校核/发布；0028已补章节/源页映射、TXT校核/抽检、发布/撤回，OCR及AI检索仍待开发，WZ-11及完整项目未整体验收。详见[0027实施记录](2026-10-26-textbook-processing-jobs.md)。以下保留历史验证范围。

## 教材 PDF 隔离接收增量（2026-10-26，0026）

- 0026新增绑定教材不可变修订的PDF接收凭证、四接口与两项事务内审计；FORCE RLS、受限SQL能力、实时授权、幂等、15分钟接收期限、50 MiB上限、120秒流超时及私有文件权限均有覆盖。原始字节始终处于隔离区；接收凭证不含扫描结论，后续0027处理结果仍不可供AI使用。
- 正式平台接通文件选择/摘要、申请/接收、原请求重试、刷新恢复、分页详情及失权清理；403显示权限错误并保留会话，401退出。POST原申请重放与PUT授权重查语义见实施记录。
- 文档API35项、迁移5项通过；平台API单元34项通过，类型/生产构建通过；PDF浏览器10项通过，全平台Chrome **91 passed（55.9s）**；真实API/隔离PostgreSQL/文件浏览器 **1 passed（48.13s）**。完整后端（含家庭与平台真实浏览器）**1104 passed，1 warning（302.39s）**，退出码0，Ruff通过。warning为既有Starlette/httpx弃用提示。
- 未迁移/重置共享开发库，未修改真实凭据。0027补充扫描/结构/页数检查及内嵌文本草稿；0028已补章节/物理页码、TXT校核与抽检、正文发布/撤回；OCR、原PDF预览与检索仍待开发，真实扫描服务与部署待验收；FR-A11/WZ-11与完整项目未整体验收。详见[0026实施及日志](2026-10-26-textbook-pdf-upload.md)。以下各节保留历史验证范围。

## 教材版本与权利审核增量（2026-10-26，0025）

- 0025新增教材版本、不可变修订与九接口；正式平台接通目录/筛选/分页、教材资料及授权表单、提交、独立批准/驳回、撤回、修订与历史。标题允许重名、代码唯一，授权用途/地区/起止日期均有校验。
- security_admin具备读写和审核权限、operator只读；创建者、当前编辑者及提交人不可审核。实时权限、UUID幂等、CAS、事务内审计、FORCE RLS及受限能力均有覆盖。approved仅表示权利批准，content_available固定false；到期/未来授权按上海日期实时判断。
- 教材API专项 **43 passed（6.00s）**；教材迁移及身份专项 **55 passed（9.63s）**；平台API单元 **32 passed**，类型检查/生产构建通过（2.17s）；教材Chrome **14 passed（20.1s）**，全平台Chrome **81 passed（1.4m）**；全后端Ruff通过。
- 正式构建→真实API→隔离PostgreSQL浏览器专项 **1 passed（46.77s）**：登记、提交、阻止自审、第二账号MFA登录批准、撤回、修订重提及operator只读；数据库核对六条修订、独立审核人、退出会话及审计。开启家庭/平台真实浏览器的完整后端回归 **1064 passed，1 warning（287.27s）**，退出码0，无失败或跳过。warning为既有Starlette TestClient/httpx弃用提示。日志`/tmp/wizzy-backend-0025-regression.log`、`/tmp/wizzy-platform-0025-browser.log`、`/tmp/wizzy-textbook-real-api.log`。
- 使用随机隔离数据库和受限角色，未迁移或重置共享开发库。PDF上传/病毒与文件校验、异步文本解析/OCR、章节页码映射、TXT校核、抽检、正文发布/撤回与检索仍待开发；FR-A02/FR-A11/WZ-11与完整项目未整体验收。详见[实施记录](2026-10-26-textbook-rights.md)。以下各节保留历史验证范围。

## 学校学生名册与学年在读增量（2026-10-26，0024）

- 0024新增校内学生身份、逐学年在读记录及不可变修订三表和五接口；正式平台学校详情接通学年/姓名或代码/状态筛选、游标分页、登记、详情、历史、调班、休学、离校与毕业。同校固定代码跨年复用身份，不按姓名合并，不自动创建家庭学生或家庭访问授权。
- 在读必须选择同校同学年、学校及全部上级启用的班级；非在读允许保留历史班级或不分班。security_admin读写、operator只读；实时权限、UUID幂等、CAS冲突、同事务审计、强制RLS与受限数据库能力均有覆盖。停用学校允许读取与原请求重放，拒绝新变更。
- 平台API单元 **29 passed**，类型检查/生产构建通过；全平台Chrome **67 passed（1.2m）**；后端名册/迁移/身份等专项 **92 passed，1 warning（20.08s）**；真实API/隔离PostgreSQL浏览器专项 **1 passed（17.11s）**；全后端Ruff通过。真实浏览器登记学生、调班、休学、验证operator只读，数据库核对三条修订、操作者、UUID和审计。
- 开启家庭/平台真实浏览器的完整后端回归 **1012 passed，1 warning（243.63s）**，退出码0，无失败或跳过。首次全量运行发现联调用例将全局家庭学生数量误设为零；已改为核对本用例操作前后家庭学生和授权数量不变，再运行完整回归通过。warning为既有Starlette TestClient/httpx弃用提示。完整日志 `/tmp/wizzy-backend-0024-regression.log`；命令见[实施记录](2026-10-26-school-roster.md)。
- 三端演示原型重新构建 **2.10s**，构建产物浏览器 **45 passed（11.4s）**，见[原型验证记录](../prototype/VERIFICATION.md)。此45项与正式平台、后端结果分别计数；原型仍使用虚构数据。
- 测试使用随机隔离数据库和受限角色，未重置或迁移共享开发库。名册批量导入/逐行错误/回滚、学校管理员范围、经验证的监护人关联、跨校身份合并仍待实现；FR-A09/WZ-15/WZ-17与完整项目未整体验收。以下各节保留历史验证范围。

## 学校组织、年级与班级增量（2026-10-26，0023）

- 0023新增学校组织节点、不可变修订及五接口；正式平台支持校区/部门/年级/班级树、学年切换、登记/改名/移动/启停及历史。类型、学校、学年与代码固定，禁止跨校/跨学年、成环和超过八层，每校每学年最多2000节点。
- 自身与有效状态分开，学校停用拒绝新写入且允许原请求重放；复用实时权限、UUID幂等/CAS、受限数据库能力和同事务审计。平台API单元 **26 passed**，类型检查/生产构建通过；学校组织Chrome **9 passed（5.6s）**，全平台Chrome **58 passed（22.7s）**；真实API/隔离PostgreSQL浏览器 **1 passed（14.17s）**。后端组织及迁移专项 **60 passed，1 warning（14.90s）**，全后端Ruff通过。
- 真实浏览器登记两个年级和一个班级、将班级移动到另一个年级、停用上级并验证operator只读；数据库核对三个节点、五条完整修订、操作者、操作UUID与审计，班级自身仍active且有效状态disabled。开启家庭/平台真实浏览器的后端全量 **975 passed，1 warning（222.31s）**，无失败或跳过；warning为既有Starlette TestClient/httpx弃用提示。日志`/tmp/wizzy-backend-0023-regression.log`。
- 测试使用随机隔离数据库及受限角色，未迁移共享开发库。学校管理员范围、名册导入/逐行错误/回滚、在读及监护关系仍待实现；FR-A09/WZ-15/WZ-17与完整项目未整体验收。详见[实施记录](2026-10-26-school-structure.md)。以下各节保留历史验证范围。

## 学校目录正式页面增量（2026-10-26，0022）

- 0022新增学校目录、不可变修订及五接口；正式平台接通搜索、状态筛选、分页、登记、编辑/启停和历史。security_admin可读写，operator只读；固定学校代码、UUID幂等、版本CAS、实时权限与同事务审计均有验证，回执不明时复用原请求重试。
- 平台类型检查/构建通过，API单元 **23 passed**；学校专项Chrome **11 passed（6.1s）**，全平台Chrome **49 passed（19.4s）**；正式构建→真实API→隔离PostgreSQL浏览器 **1 passed（9.07s）**。后端学校/迁移/身份专项 **97 passed，1 warning（14.64s）**；开启家庭/平台真实浏览器的后端全量 **924 passed，1 warning（211.48s）**，无失败或跳过；全后端Ruff通过。warning为Starlette TestClient/httpx弃用提示。
- 新增9项迁移测试，覆盖权限、事务回滚、降级保护、不可变历史和当前版本外键；先复现修订表可被TRUNCATE，再补数据库清表拦截并通过专项验证。测试使用随机隔离数据库及受限运行角色，未迁移共享开发库。
- 仅完成FR-A09基础目录：组织树、年级/班级、学校管理员范围、名册导入与逐行错误/回滚、在读及家长关联仍待实现；FR-A09/WZ-15/WZ-17及完整项目未整体验收。命令与证据见[学校目录实施记录](2026-10-26-school-directory.md)。以下各节保留历史验证范围。

## 平台账号治理正式页面增量（2026-10-26，0021）

- 新增角色/启停变更、不可变历史、独立升权审批和拒绝；实际变更撤销目标全部已有会话，保护最后一位启用的安全管理员。`frontend/platform` 接通详情、历史、申请、批准、拒绝五接口，包含权限重验、版本冲突保留草稿、回执丢失时复用原操作编号重试及390px布局。
- 当前生产构建通过；接口单元 **19 passed**；Chrome模拟API交互 **38 passed（15.9s）**；构建产物→真实API→隔离PostgreSQL浏览器 **1 passed（7.40s）**；联调测试文件Ruff通过。真实浏览器验证停用/启用及独立升权申请，数据库核对版本0→1→2、三条申请、两条即时决定和审计；跨管理员批准/拒绝由后端专项及模拟浏览器覆盖。
- 后端0021专项此前 **73 passed，1 warning（16.96s）**，完整回归此前 **873 passed，1 warning（192.51s）**；该全量运行使用旧前端构建，不能替代上述本轮新页面真实联调，本轮未重跑后端全量。命令、证据与未实现边界见[账号治理实施记录](2026-10-26-account-governance.md)。
- 未迁移共享开发库。在线邀请/开户、MFA生命周期、组织数据范围、学校/学生/家长治理及完整模型评分仍待实现；FR-A10/WZ-15不能整票关闭，完整项目仍在开发。

## 家庭课表正式页面增量（2026-10-26）

- `frontend/family` 已接通学期创建/分页切换、单双周模板、按周有效课程、冲突显式确认、单次调课/停课/恢复及历史；家长可写、孩子只读。科目清空保持字段校验；并发版本冲突和回执丢失均先刷新核对。
- 类型检查、H5/微信小程序构建通过；单元 **36 passed**；Chrome语义/键盘 **1 passed（2.4s）**；真实API/隔离PostgreSQL浏览器 **1 passed，1 warning（11.56s）**；测试文件Ruff通过。浏览器验证实际冲突、并发写入和响应丢失，数据库核对4次模板及4条单次调整。详见[家庭客户端计划](2026-10-26-family-client.md)。
- 本轮未迁移共享开发库，未重跑后端全量、正式平台或演示原型。课表OCR导入/提醒、预习教材联动、小程序真机及完整项目仍待交付；以下记录保留各轮验证范围。

## 家庭报告与共享计划正式页面增量（2026-10-26）

- `frontend/family`接通周/月报告生成/筛选分页、来源快照下钻、过期提示与显式重建，以及报告生成有界共享计划、幂等重试和计划任务实时状态。空样本不显示正确率，儿童提交仍待核验，报告重建保留旧版；当前会话的计划入口可跨页保留，没有历史计划列表或跨刷新恢复。
- 类型检查、H5/微信小程序构建通过；单元测试 **30 passed**；Chrome语义/键盘 **1 passed（2.4s）**；真实API/隔离PostgreSQL浏览器联调 **1 passed，1 warning（8.89s）**；测试文件Ruff通过。浏览器验证响应丢失后唯一计划、旧报告保留、实时待核验状态与390px无横向溢出；数据库核对条目、审计、分母与来源。详见[家庭客户端计划](2026-10-26-family-client.md)。
- 本轮未迁移共享开发库，未重跑后端全量、正式平台与演示原型。报告导出/自动刷新、知识点和独立复习评估、完整课表/预习/配额、真机及完整项目验收仍待完成；下方各节保留其历史验证范围。

## 家庭正式客户端阶段验收（2026-10-26）

- `frontend/family` 使用 uni-app / Vue 3 / TypeScript，已接通家庭注册/登录、学生档案、服务及独立评测授权、一次性孩子配对、错题加入复习、儿童草稿/待核验提交、家长只读记录与设备撤销；新增图片排序/旋转裁剪、逐页上传、回执丢失后的幂等重试、任务状态/取消、授权原图以及按日期/学科/状态筛选作业记录。家庭端整体仍未验收。
- 本轮类型检查、H5/微信小程序构建通过；单元测试 **24 passed**（18 API + 6上传批次）；Chrome 语义/键盘测试 **1 passed（2.5s）**；构建 H5 → 真实 API → 隔离 PostgreSQL 浏览器验收 **1 passed，1 warning（7.67s）**；后端提交测试 **11 passed，1 warning（3.08s）**；后端 Ruff 通过。浏览器核对裁剪后图片像素、唯一提交、审计与取消后的配额释放，并回归筛选失败保留旧记录。修复图片列表不刷新和筛选失败误标已应用。
- 详细命令和边界见[家庭客户端计划](2026-10-26-family-client.md)。小程序真机、相机拒绝回退、图片质量识别、持久续传、完整课表/预习/报告/配额、真实模型与全量评测仍待交付；本次未迁移共享开发库，未重跑平台和演示原型。

## 续接回归核验（2026-10-26，0020）

- 后端完整回归：`WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest -q --tb=short`，**843 passed，1 warning，167.06s**；日志 `/tmp/wizzy-backend-0020-regression.log`。保留一条 Starlette TestClient/httpx 弃用提示。`uv run --directory backend ruff check . --output-format concise` 通过。
- 新增逐学生独立模型评测授权：公开用途说明、监护人查询/同意/撤回、版本与幂等控制、授权失效及审计；专项 **60 passed，1 warning，7.18s**，覆盖并发、越权、数据库权限、不可变历史及迁移回滚。退出登录不撤回持久授权，服务授权撤回后再次同意不会复活原评测授权。
- 该切片仅实现内部离线去标识批改评测的授权前提，尚无样本采集、双人盲标、裁决、锁定数据集、真实模型运行或生产准入。后续采样和运行仍需原子复核授权并处理撤回。迁移0020仅在隔离 PostgreSQL 测试库验证，未升级共享开发库。
- 本轮未修改或重跑正式平台与三端原型；其最近验证记录见下方0019及历史记录。完整项目仍在开发中，本次回归通过不代表三端或整体验收完成。

## 续接回归核验（2026-10-26，0019）

- 后端完整回归：`WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest -q --tb=short`，**783 passed，1 warning，160.54s**；`uv run --directory backend ruff check . --output-format concise` 通过。测试使用隔离 PostgreSQL 数据库，不代表共享开发库已迁移到0019。
- 正式平台：`npm run build --prefix frontend/platform` 通过；`npm test --prefix frontend/platform` 为 **15 passed**；`PLAYWRIGHT_CHANNEL=chrome npm run test:browser --prefix frontend/platform` 为 **26 passed，9.8s**。首次默认浏览器运行因本机缺少 Playwright Chromium 可执行文件而未启动测试，指定已有 Chrome 后通过，未修改业务代码或放宽断言。
- 已实现模型评测统计内核及0019错题派生回执并发保护；相关统计/迁移组合专项此前为 **54 passed**。统计内核尚未接通授权样本、双人盲标、第三人裁决、锁定数据集、固定版本模型运行和发布审批；不授予模型生产资格。0019解决消费者旧快照下竞争同一事件导致回执唯一键冲突的问题。
- 三端原型仍是19个业务页面与演示交互；此前记录的45项原型浏览器测试本轮未重跑。正式家庭两端、真实模型与账单、教材PDF→TXT、学校/学生/家长治理、完整RBAC、隐私与部署验收仍未全部完成。完整项目继续进行，不能以本次回归通过认定交付完成。

## 已实现切片（不等于整票验收）

1. [x] FastAPI 正式工程、锁定依赖、Alembic、真实迁移健康检查与安全错误契约。
2. [x] 家庭/逐学生授权、监护人会话、同意/撤回、一次性配对、非 owner 运行账号与 FORCE RLS。
3. [x] 私有图片真实解码重编码、1–10 页校验、提交幂等、同事务 quota/job/outbox/audit。
4. [x] 取消/撤回释放预占、旧 deletion_epoch 禁止访问及重放、成员移除/邀请接受并发回归。
5. [x] 原子 bootstrap、迁移/文件写失败回滚、API 实际启动、三端原型构建与回归。
6. [x] WZ-06 投递切片：独立低权限 dispatcher、真实 Redis/Celery JSON 投递、outbox 租约/退避/重放与 CLI 隔离测试。
7. [x] WZ-06 执行边界切片：独立低权限 worker 角色、最小消息校验、DB 执行去重/租约/心跳、外呼前有限恢复、失效授权围栏及并发测试。
8. [x] WZ-06 调用围栏切片：0006 一次性外呼许可、未知/已知费用回执、迟到账务、禁止有调用记录的恢复及并发/降级保护测试。
9. [x] WZ-06 结果结算切片：0007 保守结果契约、授权/版本/删除/租约围栏、结果与成功页额度及完成事件原子提交、并发幂等与回滚测试。
10. [x] WZ-06 受控消费者切片：单 structure 阶段消费者、显式提供方注入的 Celery 工厂、真实 dispatcher→Redis→任务→原子结算与重复/故障测试；没有生产模型或可直接启动的 worker CLI。
11. [x] WZ-06 保守结果只读切片：0008 授权投影、逐学生/家庭隔离、撤回与旧删除代次保护、内部调用字段不外泄；读取不结算、不触发模型。
12. [x] WZ-07 OCR 更正切片：0009 稳定题目 ID、不可变机器原文/逐题修订、监护人 CAS 更正与同事务 API 审计、授权历史读取及隔离库回填/降级保护。
13. [x] WZ-07 人工改判切片：0010 判定历史、题文/判定双 CAS、有效版本、OCR 后人工确认失效及私有 VerdictChanged/API 审计原子落库；0010 当时仅落库，0011 的错题消费者见下节。
14. [x] WZ-07/WZ-08 错题派生切片：0011 私有事件持久消费、版本幂等回执、即时隐藏旧投影及逐学生分页只读 API；不含报告/订正/掌握。
15. [x] WZ-07 订正记录切片：0012文字/已清洗图片引用尝试、原题版本快照、历史分页、监护人逐次核对与CAS/幂等；不含自动评估、掌握或报告。
16. [x] WZ-09 首步复习工作台：0013自然唯一D+1任务、儿童文字草稿CAS、待核对作答/求助/跳过与监护人改期；含授权/幂等/审计/迁移隔离测试，不含后续排程或掌握。
17. [x] WZ-10 课表核心：0014学期/单双周模板、冲突显式确认、单次调停课/恢复、日期实例和统一CAS；含权限/审计/迁移隔离测试，不含正式端/提醒/教材联动。
18. [x] WZ-15 平台身份基础：0015离线首位安全管理员、密码+TOTP、独立平台会话、本人撤销、只读RBAC与同事务审计；含并发/权限/回滚/隔离迁移测试，不含完整账号治理。
19. [x] WZ-17平台身份正式Web切片：独立Vue/TypeScript/Element Plus客户端，身份七接口、只读账号/角色/审计、异常态与会话保护；隔离真实API浏览器联调，不含家庭正式客户端或全平台治理。
20. [x] WZ-13周期报告后端：0016不可变周/月汇总、正确分母、来源快照下钻、stale与显式重建；不含后台自动刷新、知识点推断或正式家庭UI。
21. [x] WZ-09/WZ-13共享计划后端：0017引用唯一review_task的有界周/月集合、报告入口、题量/预计时长双限、UUID幂等与实时状态；不含独立评估/掌握或正式页面。
22. [x] WZ-15/WZ-17模型目录：0018固定身份/不可变修订、合同/费用/密钥引用、幂等CAS与最小审计；正式平台Web接通目录五接口。不含真实模型调用、评分、生产路由或双人发布。
23. [x] WZ-02 独立评测授权：0020逐学生可选同意/撤回、用途版本、不可变历史、幂等/CAS、服务授权失效及审计；仅后端API，不含样本/评测运行或正式页面。
24. [x] WZ-15/WZ-17账号治理：0021角色/启停CAS、UUID幂等、独立升权审批、最后管理员保护、会话撤销及不可变历史；正式Web五接口已联调，不含在线邀请/MFA管理和组织范围。
25. [x] WZ-15/WZ-17学校目录：0022固定代码、不可变修订、UUID幂等/CAS、实时权限及同事务审计；正式Web五接口已联调，不含学校组织/名册/在读和家长关系。
26. [x] WZ-15/WZ-17学校组织：0023按学年组织树、年级/班级约束、移动/启停、不可变历史及正式Web五接口已联调，不含学校管理员范围、名册/在读和监护关系。
27. [x] WZ-15/WZ-17学校名册：0024校内学生身份、逐学年在读/调班/休学/离校/毕业与不可变修订，正式Web五接口已联调；不含批量导入、学校管理员范围与监护关系。
28. [x] WZ-11教材基础切片：0025版本与独立权利审核、0026 PDF隔离接收、0027持久扫描/内嵌文本草稿、0028逐页校核/章节源页/独立抽检/发布TXT/撤回及正式平台；授权单页原PDF预览及并排校核已实现；真实扫描部署、图片OCR和AI检索尚未验收。
29. [ ] WZ-06 后续切片：真实提供方、真实账单对账、评测准入及完整故障验收。

## 完整交付范围（未划掉的均仍待实现/验收）

| 任务 | 当前状态 | 剩余验收重点 |
|---|---|---|
| WZ-01 范围与授权决策 | 文档已有，待签字 | 产品/法务与教研确认 |
| WZ-02 评测/选模 | 统计内核、0020独立评测授权、0029数据集双标/裁决/锁定及0030固定版本离线运行已实现，整票未验收 | 授权采样与撤回清理、双标锁定集、真实供应商与准入门禁 |
| WZ-03 工程基础 | 本地核心已实现并测试 | CI/监控与完整验收 |
| WZ-04 家庭身份授权 | 核心 API/权限已测试 | 正式客户端、账户恢复与生产验收 |
| WZ-05 图片上传 | 本地私存/解码/授权已测试 | 对象存储、物理清理与生产验收 |
| WZ-06 可靠处理 | 投递、受控消费任务注册、DB 执行、调用围栏、保守结果/额度结算及授权结果只读 API 已实现并测试，整票未验收 | 真实提供方、质量门禁/账单对账、Redis 重启/丢失与完整删除竞态 |
| WZ-07 判定/订正/改判 | OCR修订、监护人双CAS人工确认、错题派生、订正文字/图片引用及逐次人工核对历史已实现并测试，整票未验收 | 儿童OCR修正请求、自动订正评估、有限提示、其他衍生后台消费及生产调度；0016已有显式报告重建 |
| WZ-08 错题 | 监护人确认错误的版本化列表、持久事件消费者、授权与幂等已局部实现并测试 | 正式客户端、机器准入判题、手工收录/收藏、业务筛选、去重、独立练习/掌握 |
| WZ-09 复习 | 0013首步D+1、文字草稿/待核对尝试与改期已局部实现并测试，整票未验收 | 评估证据、反馈解锁、D+3/7/14与失败重置、跨日独立成功/掌握；0017共享计划正式页面已接，历史计划列表仍缺 |
| WZ-10 课表 | 0014后端核心已实现并测试，整票未验收 | 正式端接入/学期切换交互、节次配置与提醒、教材/预习联动；截图OCR属P1 |
| WZ-11 教材与权利 | 0025权利独立审核、0026隔离接收、0027持久扫描/内嵌文本草稿、0028逐页校核/章节物理源页/独立抽检/发布TXT/撤回及授权单页原PDF预览已局部实现并测试，整票未验收 | 真实ClamAV与部署验收、图片OCR与AI索引/检索 |
| WZ-12 预习/首页 | 待实现 | 已确认章节、授权内容、完成事件 |
| WZ-13 报告 | 0016周期快照/分母/下钻/stale/显式重建及0017报告建共享计划后端已实现，整票未验收 | 报告导出、后台自动刷新、独立复习评估/薄弱点证据；正式报告UI已接 |
| WZ-14 配额/费用 | 预占/释放、成功页额度消费及整数微元/未知费用元数据局部已测试 | 真实费用证据、供应商对账与完整预算治理 |
| WZ-15 平台治理 | 0015独立身份（0032密码+图形验证码）、0018模型目录、0021角色/启停治理及0031独立跨账号会话撤销、0022学校目录、0023学校组织及0024学生名册API/正式Web已实现并测试，含独立升权审批、最后管理员保护及变更撤销会话，整票未验收 | 在线邀请/开户、MFA生命周期、组织数据范围；真实模型评分/生产准入/双人发布、工单授权及外部防篡改归档 |
| WZ-16 隐私 | 待实现 | 导出/删除、对象回执、墓碑、备份过期 |
| WZ-17 三端正式客户端 | 平台身份、模型目录读写、账号治理、学校目录、组织树及学生名册Web已联调；家庭uni-app已接身份/授权/上传/错题复习/报告计划/课表，整票未验收 | 学校管理员范围、名册批量导入/回滚与监护关系、教材OCR/AI检索、账号邀请/MFA管理、家庭预习/配额等剩余页面、小程序真机 |
| WZ-18 部署运维 | 本地 PG/Redis、API/dispatcher CLI、三角色 bootstrap 已验证，受控批改 worker 工厂已测试但无生产批改 CLI；新增独立错题 drain CLI，0007–0033 在隔离库验证，开发库上次记录 0006 | worker 消费进程/scheduler/生产私有对象/监控/恢复；当前 API/dispatcher 仍宿主机运行 |
| WZ-19 安全性能 | 待实现 | T01–T29分场景、故障、压测、恢复演练 |
| WZ-20 试点发布 | 待实现/外部证据 | 真机、法务、真实账单与小流量 |
| WZ-21 原生客户端 | 待实现 | Android/iOS真机与发布凭据 |
| WZ-22 工整度 | 待实现 | 量表/双标/样本不足拒评 |
| WZ-23 打印复习 | 待实现 | 分离题答、私有PDF与回填 |
| WZ-24 引导辅导 | 待实现 | 安全边界、会话次数、预算 |
| WZ-25 课表OCR | 待实现 | 识别草稿、人工确认后应用 |
| WZ-26 受控变式 | 待实现 | 独立答案验证、授权 |
| WZ-27 扩学科 | 待实现 | 分学科门禁、教材权利 |
| 新增学校/目录治理 | 0022学校档案、0023组织树、0024学生名册/学年在读已联调，整票未验收 | 学校管理员范围、批量名册导入/回滚、经验证的家长关联 |

## 外部发布依赖（不阻断本地核心工程开发）

- 真实 AI 提供方凭据、隐私合同、固定模型版本与费用证据。
- 经授权双标/裁决金标准集；家长改判不能代替金标准。
- 教材版权凭据、真实设备/微信与 App 发布凭据、法务及产品签字。
- 自动化合成数据测试不等于模型准确率、真机或生产上线验收。

## 验证记录

### 上一切片基线（历史记录，2026-10-25）

本机 PostgreSQL 18.6、Python 3.12、系统 Chrome；以下前端及 API 启动检查未在本切片重跑：

- `uv run --directory backend pytest -q --tb=short`：42 passed，6.17s。每次使用随机隔离数据库/受限角色，结束只删除自己的对象。
- `uv run --directory backend ruff check . --output-format concise`：通过。
- `npm run build --prefix docs/prototype/ui`：成功；主 bundle 500.04 kB 警告保留，不以提高阈值掩盖。
- `PLAYWRIGHT_CHANNEL=chrome PROTOTYPE_BUILT=1 npm test --prefix docs/prototype/ui -- --workers=3 --timeout=15000 --reporter=line`：45 passed，13.4s。
- 开发库 bootstrap 连续两次成功，`.env` 权限 600；运行角色所有危险标志为 false、业务表 owner 数为 0。
- 临时端口 18000 实际启动 Uvicorn：live/ready/OpenAPI 均 200，28 个接口路径；验证 request-id、nosniff、no-store。未停止占用 8000 的其他服务，检查后已停止临时 API。
- 无上下文时 RLS 对 students/tenants/submissions 返回 0 行；这不是全库无数据的证明。
- 已知警告：Starlette TestClient/httpx 弃用提示、Node 警告与 favicon 404；不影响现有断言，但不是生产验收通过证明。

### 投递切片复验（历史记录，2026-10-25）

- `uv run --directory backend pytest -q --tb=short`：**61 passed，1 warning，12.37s**。真实 PostgreSQL 隔离数据库/受限账号、真实 Redis 随机队列；不清空开发库或共享 Redis。
- `uv run --directory backend ruff check . --output-format concise`：通过。
- 其中投递模块共 19 个参数化用例：越权拒绝、最小消息、SKIP LOCKED 并发抢占、过期租约/旧 token、授权/取消/删除代次失效、真实 Redis JSON、发布后未标记崩溃重放、消息被取走后的数据库重放、实际 CLI 发布/idle、真实连接拒绝且不泄露合成凭据、连续退避封顶与成功重置。
- 真实连接故障使用预留但未监听的端口，子进程 15 秒超时内返回 `dispatch=retry`；没有停止共享 Redis。消息丢失恢复测试通过取走/确认消息后强制隔离 DB 事件到期来验证重放，**不是 Redis 重启/AOF 丢失演练**，也不等于 T13 全项通过。
- 本地 bootstrap 连续两次成功，第二次 `.env` 内容保持不变、权限 600，开发库迁移为 `0004_outbox_dispatch`；API/dispatcher 危险角色标志均 false、public 关系 owner 数均 0，dispatcher 业务表直接权限数 0。初始化新增独立投递凭据，未重置已有 API 密码或重建数据库。
- `docker compose ps`：PostgreSQL 18.6、Redis 8.2.10 均 healthy，仅绑定本机端口。未为验证启动无人值守 dispatcher；实际 CLI 发布在隔离库/随机队列完成。
- 仍有 1 项 Starlette TestClient/httpx 弃用警告；不是生产验收证据。本切片不改动三端原型。

### 执行边界切片复验（历史记录，2026-10-25，迁移 0005）

- `uv run --directory backend pytest -q --tb=short`：**98 passed，1 warning，18.04s**；`uv run --directory backend ruff check . --output-format concise`：通过。测试使用真实 PostgreSQL 隔离数据库/受限角色及 Redis 随机队列，不清空开发库或共享 Redis。
- 执行模块共 37 个参数化用例，覆盖最小消息拒绝、DB 可信上下文、并发唯一抢占、权限隔离、心跳/旧 token、取消/撤回/版本/删除代次失效、有限恢复与额度仅释放一次。bootstrap 另验证第三角色独立密码、`ExecutionStore` 初始化及不存在事件返回空租约。
- 同意并发测试通过 `pg_blocking_pids` 确认真实锁等待：claim/heartbeat/release 等待学生锁后重新检查同意；反向验证 API 撤回等待执行锁释放后取消任务并释放额度。不是仅靠 sleep 推断并发正确。
- 恢复投递分别覆盖发布成功、发布失败和抢占后未结算崩溃。先用测试复现旧未来 `next_dispatch_at` 阻塞崩溃恢复，再修复抢占时清除被绕过的旧延迟；活跃 relay 租约、成功重播间隔及失败退避仍阻止热循环。测试仅推进隔离 DB 中目标事件的时间，不调整系统时钟。
- 开发库从 `0004_outbox_dispatch` 升至 `0005_execution_leases`；连续两次 bootstrap 成功，第二次 `.env` 内容不变、权限 600，原 API/dispatcher 密码保留。三角色危险权限标志均 false、public 关系 owner 数均 0，dispatcher/worker 业务表直接权限均无；开发库恢复函数已包含本次修复。
- 仍有 1 项 Starlette TestClient/httpx 弃用警告；不为消除警告扩展依赖升级。本切片未重跑原型构建/45 项 Chrome 测试及 API 启动检查，未运行无人值守 dispatcher 或未注册的通用 worker，未执行真实模型评测、Redis 重启/AOF 丢失或完整 T01–T29 验收。

### 调用围栏切片复验（历史记录，2026-10-25，迁移 0006）

- 最新续接执行 `uv run --directory backend ruff check . --output-format concise`：通过；`uv run --directory backend pytest -q --tb=short`：**136 passed，1 warning，23.16s**。使用真实 PostgreSQL 隔离库/受限角色与 Redis 随机队列，不清空开发库或共享 Redis。
- 其中调用围栏模块 38 项参数化测试；此前专项运行 `pytest tests/test_provider_calls.py -q --tb=short` 为 **38 passed，1 warning，6.00s**。覆盖提交后一次性许可、同阶段/跨阶段并发、未知费用禁止恢复、授权锁/版本/代次/旧 token、迟到账务、整数金额及不可变回执、低权限角色和降级保护。
- 本切片补充四项回归：Barrier 跨阶段并发仅一许可、并发冲突金额仅一获胜且不可覆盖、failed+known 禁止下一阶段/释放、有记录拒绝 downgrade 且事务回滚保留迁移 head/围栏。上述测试不调用真实 AI，金额均合成，不证明模型准确率或真实账单正确。
- 此前已将开发库从 `0005_execution_leases` 升至 `0006_provider_call_fences` 并连续两次 bootstrap，保留原三个运行角色密码，第二次 `.env` 不变、权限 600；本次续接未重跑 bootstrap。最新只读复核确认 head 为 0006、`model_runs` ENABLE/FORCE RLS、三角色危险标志/成员关系/业务关系 owner 均无、无 model_runs 直接表/列权限，旧 `_unfenced` 函数均不可执行、begin/finish 仅 worker 可执行。
- 仍有 1 项现有 Starlette TestClient/httpx 弃用警告。本次不改后端生产代码或三端原型；未重跑原型构建/45 项 Chrome 回归及 API 启动检查，未接真实提供方或启动无人值守 dispatcher/未注册 worker，未执行 Redis 重启/AOF 丢失、真机或完整 T01–T29 验收。

### 结果结算切片续接复验（2026-10-25，迁移 0007）

- `uv run --directory backend pytest -q --tb=short`：**223 passed，1 warning，43.89s**；`uv run --directory backend ruff check . --output-format concise`：通过。使用真实 PostgreSQL 随机隔离库/受限角色与 Redis 随机队列，测试结束仅清理自身对象。
- 结果模块共 **87 项参数化用例**，覆盖全成功/部分成功/全失败、成功页额度与完成事件仅结算一次、冲突结果不可覆盖、未完成/失败/未知费用调用拒绝写回、授权/版本/删除代次/租约围栏、事务失败整体回滚、双层契约校验及角色权限。
- 本次续接重点复验配额行锁等待后租约过期拒绝提交、已结算结果在授权撤回/删除代次变化后拒绝重放、相同结果并发只结算一次；这些均为合成结果测试，不是模型准确率或供应商账单证据。
- 按结果→调用围栏→执行边界的顺序运行 `uv run --directory backend pytest tests/test_results.py tests/test_provider_calls.py tests/test_execution.py -q --tb=short`：**162 passed，1 warning，31.92s**。修正的降级测试根据当前是否已有结果匹配首先触发的保护：存在结果由 0007 拦截，否则由 0006 的持久调用记录保护拦截；仍检查异常后迁移 head 不变、调用记录保留以及无法重复领取调用许可或释放执行。
- 单独运行 `uv run --directory backend pytest tests/test_provider_calls.py::test_downgrade_cannot_remove_existing_paid_call_fences -q --tb=short`：**1 passed，1 warning，0.79s**，在无既有结果的隔离库验证 0006 保护，避免仅在 0007 提前拦截的路径下通过。
- 当前仍有 1 项 Starlette TestClient/httpx 弃用警告。本次未升级依赖、迁移或重新读取开发库版本、重置凭据、清空共享 Redis；未重跑前端构建/Chrome 测试或 API 启动检查，也未接真实 AI、注册/启动消费任务或执行完整生产验收。0007 迁移验证仅针对测试隔离库，不能据此宣称开发库已升级。

## 已实现投递边界

- API、dispatcher 与 worker 使用三个独立受限账号。dispatcher 无业务表直接权限，只能执行限定的 `claim_outbox` / `settle_outbox`。函数为固定 search_path 的 SECURITY DEFINER，撤销 PUBLIC 执行权，owner 是离线迁移身份而非运行账号。
- 抢占租约 60 秒；每次一条，事务提交后再发布。异常仅记录 `broker_unavailable`，按 2→300 秒封顶退避；有效 token 才能结算。发现阶段排除无效授权，但不能消除已投递消息，执行与结果写回仍须再次检查。
- 成功发布仅设置 `published_at`，符合条件且无 `model_runs` 记录的 queued 事件 300 秒后仍可重投；无调用记录且执行租约已过期的 running 事件也可恢复投递。`execution_dispatch_attempt` 允许每个执行 attempt 首次恢复绕过旧重播时间，随后受投递租约/结算退避约束；抢占后未结算崩溃仅等投递租约到期，不遗留旧未来延迟。
- 任务 ID 固定不提供消费者去重。当前无注册的批改消费者，会积累重复消息，仅用于受控联调，不启动未注册该任务的通用 worker。`dispatch=published` **不等于 AI 批改成功**；仅投递不改变 job/submission 的 queued 状态或 quota 的 reserved 状态。启动命令见 [本地开发指南](../local-development.md)。

## 已实现 DB 执行边界（仅外呼前）

- `ExecutionStore` 严格校验五字段消息，不信任消息附带租户/学生信息；数据库重载家庭、学生、作业、提交，校验有效状态、同意、request_id、源版本及 deletion_epoch。
- worker 无业务表直接权限，只能执行限定的 claim/heartbeat/release、begin/finish 调用及 complete 结算函数。唯一执行回执及行锁阻止重复消息并发取得有效租约；租约 60 秒，心跳、释放都须匹配当前未过期 token、版本、代次和授权。取消/撤回后旧消息及旧 token 不可继续取得有效执行权限。
- claim 后 job/submission 分别变为 running/processing，预占仍 reserved。没有任何 `model_runs` 记录的中断可 release，前两次按 2 秒、4 秒退避回到 queued；无调用记录的过期 running 租约可恢复。最多 3 次执行尝试，第三次释放或过期后再次恢复时进入 deadletter，job/submission failed，并原子释放预占一次。
- 上述去重/恢复是数据库边界，受控消费者集成见下节；它不是完整 AI 流水线，更不是外部调用 exactly-once。**不得把可能已经发出的付费调用当成外呼前中断直接 release 或自动重试。** 结果写回与成功页配额结算已由下述 0007 切片实现；仍无真实提供方或供应商账单核验。

## 已实现调用围栏（0006，不是完整模型治理）

- `begin_call` 在规范上下文锁内重验授权/同意、job/submission、版本/删除代次与有效执行 token。许可事务提交并返回 run_id/operation_key 后，才允许一次外呼；本地标识不是供应商幂等或请求已接收证明。
- `model_runs` 唯一约束覆盖 operation_key 及 job/版本/删除代次/stage。阶段为 ocr/structure/verify/tutor，记录 provider/model/model_version；同阶段不再发许可。已有调用非 succeeded 或费用 unknown 时禁止新阶段；当前没有完整阶段顺序或结果复用。
- 初始调用 in_flight、费用 unknown/NULL。`finish_call` 仅保存调用与账务元数据，非负 int64 微元金额，未知不当成 0；不写结果、不推进 job/submission、不结算用户额度。known 不等于已核验供应商账单，测试金额均合成。
- 匹配原调用的迟到回执在取消/撤回/租约过期后仍可记账，但不复活业务。outcome/request id 不可变，未知金额可补已知，已知金额只允许同值幂等；冲突金额不可覆盖。
- 有任何调用记录就禁止 release/reclaim/恢复投递，即使已知费用或提交后尚未发请求。仅原有效租约可在先前调用 succeeded+known 后申请未调用阶段；尚无自动对账、运维解锁或额外 pending 状态。
- 表启用并强制 RLS，三个运行角色均无直接表/列权限；仅 worker 可执行 begin/finish，固定 search_path、撤销 PUBLIC，旧 `_unfenced` 函数不可执行。有记录时拒绝 downgrade，不能删记录绕过围栏。

## 已实现结果写回与额度结算（0007，内部保守契约）

- `ExecutionStore.complete` / SQL `complete_execution` 通过受限 worker 调用；先锁可信家庭/学生/作业/提交，重验授权、同意、源版本及 deletion_epoch，再锁执行回执、模型调用和配额。配额行锁等待结束后再次检查租约，过期不写回。
- `grading_results` 强制 RLS，三个运行角色无直接读写权限；只经 0008 的受限函数提供当前授权下的保守只读投影。同事件及同作业/版本/代次唯一，已结算时仅相同 token 与 JSON 内容可幂等返回成功，且仍须通过当前授权检查；冲突不覆盖。
- 所有关联调用须已终态且费用 known，成功页须引用本事件 succeeded+known 的 structure/verify 调用。1–10 页须匹配原提交；结果、额度、job/submission、回执 settled 与 `submission.processed` outbox 在同一事务提交，任一失败全部回滚。
- 仅消费结构处理成功的页数，失败页释放预占；全成功/部分成功/全失败分别为 completed/partial/failed，全失败额度为 released。**“成功页”不是答案正确率、模型评分或供应商账单已核验。**
- `1.0-conservative` 契约由 Python 与 SQL 双层校验：版本与 prompt_hash、题文/原始作答、bbox 和同页证据；题目只能 pending_review/unsupported，必须监护人复核，不能给出 correct/incorrect/unanswered 或参考答案。尚无正式评测门禁，不允许将合成测试当作 AI 准确率证据。
- 有结果时拒绝降级，保护结果与额度证据；受控消费集成及保守只读 API 见下节，仍无真实 AI 调用。开发库本轮未迁移，0007/0008 只在随机隔离库验证。

## 已实现受控消费集成（单 structure 阶段，非生产模型流水线）

- `consumer.py` 串联严格消息校验、claim、提供方本地 prepare、心跳、一次性调用围栏、单次 invoke、费用回执及原子 complete。prepare 只允许本地校验/私有输入读取，不允许外呼；invoke 须遵守传入的 30 秒超时且关闭 SDK 重试，这是适配器契约，不是 Python 强制中断。
- 围栏调用前即标记“可能已提交”，避免许可提交成功但响应丢失时误 release。围栏前失败才有限释放/重试；围栏后任何不确定、未知金额或无效结果只返回 `reconciliation_required`，不自动再调用、不伪造 0 费用。complete 已提交但响应丢失时，DB 仍是事实来源，重复消息不会再外呼。
- 回执在重新检查租约前持久化，取消/撤回/租约过期后的迟到费用仍能入账，但不复活结果。固定 provider/model/version 只是调用身份，不代表版权/隐私或评测发布准入。
- `worker.create_worker(settings, provider=...)` 缺少提供方即失败，启动检查独立受限角色，每次消息新建并释放数据库 engine，不跨 prefork 共享连接。仅注册 JSON 最小消息任务，无默认/生产 mock 提供方、全局 Celery app、结果 backend 或通用 worker CLI。
- 任务禁止 Celery 自动重试，prefetch=1、late ack、失败/超时确认且不因 worker lost 主动拒绝重排；软/硬时间限制为 50/55 秒。真实 Redis 测试使用 solo pool，**不能证明 prefork 强杀、供应商超时或崩溃对账已验收**。

### 受控消费者续接复验（2026-10-25）

- 全量 `uv run --directory backend pytest -q --tb=short`：**261 passed，1 warning，52.51s**；Ruff 全量检查通过。消费者模块 28 项、worker 模块 10 项，包含真实受限 PostgreSQL、dispatcher→Redis→注册任务→结果/额度原子结算，以及重复投递仅一次调用/一次结算。
- 覆盖授权/取消/过期、调用前有界失败、未知/非法费用、迟到回执、begin/finish/complete 提交响应丢失、无效结果及数据库不可用。合成提供方只在测试中注入，金额不是供应商证据。
- 全量回归曾暴露旧 dispatcher fixture 未隔离先前测试的过期执行恢复事件；已仅在会话专属随机测试库抑制先存事件的普通及过期恢复分支。修复后消费者/dispatcher/worker 联跑 **57 passed，15.75s**，再全量通过；未改生产调度逻辑。
- 本轮未迁移或重读开发库版本、重新 bootstrap、重置凭据、清空共享 Redis、启动无人值守消费者，亦未重跑前端或 API 启动检查。保留 1 条 Starlette TestClient/httpx 弃用警告。

## 已实现保守结果只读查询（0008，2026-10-25）

- `GET /api/v1/submissions/{submission_id}/result` 对当前有权监护人/对应儿童会话开放；路由复用当前会话、逐学生授权及同意锁，数据库投影再检查活跃家庭/主体、同意、源版本、删除代次、最终提交/任务状态及 settled 回执。不允许仅凭 tenant 上下文读取。
- 仅输出页面/题文/原始作答/bbox/置信度/证据和 pending_review/unsupported；保留恒为 true 的监护人复核提示，不返回调用 ID、Prompt/规则/评测版本、执行 token、提供方请求或费用信息。成功页不等于题目正确，不能据此评分模型。
- 页及题目稳定排序，支持已结算的 partial/全失败结果；无有效结果 409 `result_not_ready`，越权/旧代次 404，有效监护人会话未同意 403。撤回后重新同意不能复活旧结果。只读且 no-store，不外呼、不扣费或修改结果。
- API/dispatcher/worker 仍无结果表直接权限，仅 API 可执行固定 search_path、撤销 PUBLIC 的读取函数。接口字段及状态详见 [06](../architecture/06-data-model-and-api.md)。

### 结果只读续接复验（2026-10-25）

- 专项 `uv run --directory backend pytest -q tests/test_result_read.py -x --tb=short`：**18 passed，1 warning，4.76s**，覆盖投影、无副作用、部分/全失败、监护人/儿童授权、兄弟姐妹/其他家庭隔离、撤回/重授权、版本/代次/状态失效、同意锁等待及数据库函数权限。
- 全量 `uv run --directory backend pytest -q --tb=short`：**279 passed，1 warning，54.90s**；`uv run --directory backend ruff check . --output-format concise`：通过。
- 首次全量回归为 278 passed、1 failed，失败是 bootstrap 测试仍断言 head 为 0007；将该期望更新为 0008 后得到上述全量通过结果，没有放宽生产约束。
- 0008 只在随机隔离库迁移/验证；开发库上次版本记录仍为 0006，本轮未迁移或重读其版本、重新 bootstrap、重置凭据、清空共享 Redis 或启动常驻 worker，亦未重跑前端/API 启动检查。保留 1 条 Starlette TestClient/httpx 弃用警告；测试不是模型正确率或 T01–T29 整体上线验收。

## 已实现题目 OCR 更正与不可变历史（0009，2026-10-25）

- 成功页结果插入时，同一事务物化稳定题目 UUID 与 machine revision=1；既有结果通过迁移回填。原机器题目和 `/result` 输出不覆盖。题目创建失败会回滚结果、额度结算及完成状态；重复结算不重复建题。
- `GET /api/v1/submissions/{submission_id}/questions`、`GET /api/v1/questions/{question_id}` 提供按页/题排序的当前识别内容与历史；监护人限获授学生，儿童限自己。结果未就绪 409；越权/旧删除代次 404，当前有效监护人未同意 403，撤回后重授权不复活旧结果。
- `POST /api/v1/questions/{question_id}/ocr-corrections` 仅有权监护人可写；按 `expected_revision` CAS 追加不可变历史并更新当前指针，冲突 409。API 在同一事务追加不含题文的审计。严格整数版本、长度、空白、NUL 和额外字段校验均已覆盖；超 PostgreSQL int32 的版本在请求层返回 422，不进入失败的数据库函数匹配。
- 两张表 FORCE RLS，运行角色无直接读写权限；固定 search_path 的受限能力函数仅 API 可执行，worker/dispatcher/PUBLIC 无调用权。更正路径重验实时同意、授权、源版本与删除代次；等待撤回锁后不可继续写历史。
- 更正后只标 pending_review，仍需监护人复核；不调用模型、不消费额度、不生成判定改判/重跑作业或评分证据。逐题 `revision` 不等于提交 `source_revision`。该 0009 切片当时尚无判定修订、人工对错确认或 VerdictChanged；0010 的新增实现见下节。分页、容量验收、订正作答、提示及衍生统计重建仍未完成。
- 0009 在有 guardian 历史时拒绝降级；无人工历史时可从 0008 原结果回填。回填/重复升级和两层结果保护测试均使用各自临时数据库，开发库未升级。

### OCR 更正续接复验（2026-10-25）

- 专项 `uv run --directory backend pytest -q tests/test_question_revisions.py tests/test_question_migration.py --tb=short`：**27 passed，1 warning，8.27s**（24 项 OCR 用例、2 项新迁移用例及 1 项移入的既有结果降级保护用例）。独立迁移模块另跑 **3 passed，1 warning，2.28s**。
- 全量 `uv run --directory backend pytest -q --tb=short`：**305 passed，1 warning，63.23s**；`uv run --directory backend ruff check . --output-format concise`：**通过（All checks passed!）**。
- 首轮全量为 304 passed、1 failed：既有 0007 结果保护用例共用的会话测试库含新测试产生的 guardian 历史，被 0009 先行正确拦截。已把该用例移入逐测试独立建库的迁移模块，仍明确断言 0007 的结果保护错误；没有放宽任一生产降级保护，也没有把两种错误都算通过。
- 文档复验：本轮更新的 4 份文档共 20 个内部文件链接有效、4 个 JSON 示例可解析、代码围栏配对；API/本地开发/验收/台账的最新测试结果占位符已替换，不把旧 0008 的 279 项历史记录当成当前全量。
- 全部使用真实随机隔离 PostgreSQL，既有集成测试另使用自己的 Redis 随机队列。未迁移或重读开发库版本、重新 bootstrap、重置凭据、清空共享数据/Redis、启动常驻 worker，也未重跑前端或 API 启动验证。保留 1 条 Starlette TestClient/httpx 弃用警告；不等于真实模型正确率、供应商账单或 T01–T29 整体验收。

## 已实现监护人人工改判与有效版本（0010，2026-10-25）

- `POST /api/v1/questions/{question_id}/verdict-overrides` 仅允许实时有权且已同意的监护人提交；`expected_revision` 与 `expected_verdict_revision` 双 CAS，任一失配返回 409。儿童写入 403，越权/旧删除代次 404；严格输入与原因边界见 [06](../architecture/06-data-model-and-api.md)。
- 追加不可变人工判定历史，保留机器证据与原 `/result`。累计 `verdict_revision`、当前有效人工指针与单调 `effective_revision` 分离；后续 OCR 更正使有效人工结论失效并标记 `ocr_pending`，历史不丢失，新题文必须重新确认。只读投影返回来源、版本与历史，不泄露内部 actor_id。
- 人工历史、当前指针、有效版本、私有 `question_verdict_events` 与 API 审计同事务提交。事件唯一键为 `(question_id, new_revision)`；事件不包含题文、答案或改判原因，request_id 单独保存。两张新表 FORCE RLS，API 仅可执行受限函数，运行角色无直接读写权限，worker/dispatcher 无调用权。
- 改判不调用模型、不创建新 source_revision/作业、不增加额度消费；监护人确认不是模型评测金标准，不能直接转换成准确率得分。**0010 当时事件仅原子落库；0011 已接独立数据库事件消费者更新错题，见下节。报告和模型评分仍不自动重建。**
- 有人工历史或仅有 OCR 变更事件均拒绝降级，空历史/事件可降至 0009 并恢复原 reader/权限，既有 0009/0007 数据保护继续生效。

### 人工改判续接复验（2026-10-25）

- Ruff `uv run --directory backend ruff check . --output-format concise`：**通过（All checks passed!）**。
- 专项 `uv run --directory backend pytest -q tests/test_question_revisions.py tests/test_question_migration.py tests/test_verdict_revisions.py tests/test_verdict_migration.py --tb=short`：**73 passed，1 warning，22.77s**。
- 全量 `uv run --directory backend pytest -q --tb=short`：**351 passed，1 warning，82.77s**。覆盖双 CAS/并发 OCR、授权撤回与锁等待、输入边界、私表权限、事件/审计失败整体回滚、无模型/额度副作用及迁移/降级保护。警告为既有 Starlette TestClient/httpx 弃用提示。
- 首轮全量 **350 passed、1 failed、1 warning，88.55s**：0008→head 同事务升级时，0009 回填触发延迟外键检查，0010 随后的 ALTER TABLE 报 pending trigger events。独立复现 **1 failed，1 warning，1.15s**；0010 仅定点将 `question_current_revision` 切至 IMMEDIATE 完成检查，再恢复 DEFERRED 后执行 DDL，不提交中间事务、不禁用触发器或放宽约束。增强原回填用例，断言失败整体回滚至 0008、原结果不变、两个外键仍默认延迟及回填不产生虚构人工历史；修复后该用例 **1 passed，1 warning，1.06s**，随后专项/全量均通过。
- 前端同轮构建成功 **2.35s**，原型浏览器回归 **45 passed，13.0s**（19 路由 × 两种宽度 38 项，加 7 项关键交互）；保留单 chunk 超过 500KB 告警，没有提高阈值，本轮未重新截图。详细边界见 [原型验证记录](../prototype/VERIFICATION.md)。
- 文档复验：本轮更新的 7 份 Markdown 共 **46 个内部文件链接有效、7 个 JSON 示例可解析、代码围栏配对**；历史测试记录保留，最新结果与 0010 边界已同步到架构、本地开发、验收、功能列表及原型记录。
- 使用真实随机隔离 PostgreSQL 库/角色及专属随机 Redis 队列；**未迁移或重读共享开发库、重新 bootstrap、重置凭据或清空共享数据/Redis**。开发库版本仅保留上次记录 0006，不声称已升级到代码 head 0010。未重新验证 API 启动、部署常驻消费者或调用真实模型，不代表 T01–T29 整体验收或商用上线条件达标。

## 已实现错题持久派生与授权列表（0011，2026-10-25）

- 新增私有 `question_derivation_receipts`（事件及题目/版本唯一回执）与 `mistake_projections`（每题当前有效版本指针），均 ENABLE/FORCE RLS。运行角色无表直接权限，API 仅可读授权投影，worker 仅可执行消费能力；固定安全 search_path、撤销 PUBLIC。
- `question_verdict_events` 本身作为 PostgreSQL 持久队列，不向 Redis 重复投递。消费者 `FOR UPDATE SKIP LOCKED` 领取事件，按 tenant → student → question 锁顺序重验活动状态、同意/删除代次和源结果/任务/结算回执；回执与投影同事务。异常全部回滚，重跑可重试；旧版本记 obsolete，不覆盖最新投影；无效源记 invalidated。
- 当前切片仅监护人确认的 incorrect 入错题；改为正确/待确认/未作答/不支持，或 OCR 更正后，有效版本变更立即使旧投影不可见，不等待消费。待确认与保守机器结果不自动收录。撤回再同意不复活旧删除代次内容。事件/投影不复制题文或答案，不调用模型、不消费额度，人工改判不是模型金标准。
- 已实现 `GET /api/v1/students/{student_id}/mistakes?limit=20&cursor=...`：监护人与绑定儿童逐学生授权，稳定 `(结果创建时间, question_id)` 游标分页，`limit` 1–100；返回安全当前题文/原答/来源版本、`next_cursor` 和授权范围内尚无当前版本回执的 `pending_updates`。该数不是队列总事件数或预计耗时，不含失效源。
- `python -m wizzy.learning --once` 消费至多一个事件；无参数消费至当前 idle 后退出，只打印状态，不打印儿童内容或标识符。使用既有受限 worker 连接，不是生产批改 worker/常驻服务或 scheduler。已有 0010 未消费事件升级后可处理；有任意派生回执/投影拒绝降级，空派生状态降级保留原事件。

### 0011 验证记录与边界

- 五模块专项：**51 passed，1 warning，19.30s**，覆盖授权/撤回、即时失效、乱序/并发重复消费、事务故障回滚、私表/能力隔离、旧事件升级回放、降级保护及隔离 CLI。
- 专项首轮为 50 passed、1 failed：CLI 测试把 SQLAlchemy URL 对象直接传给子进程环境变量，被 Python 类型检查拒绝；只修正隔离测试环境的连接串序列化，未改运行配置或业务规则，之后上述专项通过。
- 全量首轮 **388 passed、1 failed、1 warning，91.95s**：旧付费调用降级测试使用会话共享测试库，被先前用例留下的 0011 派生回执保护提前拦截。将该测试移至独立随机库的 `test_provider_migration.py`，直接验证 0006 付费调用保护；未删数据或放宽任一迁移保护。独立复验 **1 passed，1 warning，0.93s**。
- 全量 `uv run --directory backend pytest -q --tb=short`：**389 passed，1 warning，93.32s**；`uv run --directory backend ruff check . --output-format concise`：**All checks passed!**
- 文档复验：本次更新的 **6 份 Markdown、43 个内部文件链接、7 个 JSON 示例**校验通过，代码围栏配对且验证结果占位符已清除；0010 历史证据与 0011 当前记录分节保留。
- 仅使用随机隔离 PostgreSQL 库/角色及专属随机 Redis 队列；未迁移、重读或 bootstrap 共享开发库，未重置凭据或清空共享数据。开发库仍仅保留上次记录 0006，不能视作已升级到代码 head 0011。本轮未重跑前端构建/浏览器测试或 API 启动，正式三端未联调，无真实提供方/账单/质量/生产验收证据。
- 在0011验证时，WZ-07/WZ-08仍是局部切片；当时订正尝试尚未实现，0012已另行实现记录/人工核对层，见下节。收藏/手工收录、科目/知识点等业务筛选、复习/掌握、报告失效重建、模型准确率评测及常驻消费调度仍未实现，不宣称T06/T07整票或5分钟生产一致性验收通过。

## 已实现订正记录与监护人逐次核对（0012，2026-10-25）

- 新增私有 `correction_attempts/correction_judgments`：每次尝试保存原题题文/判定/有效版本快照、文字或已清洗同学生图片引用，每次人工核对追加revision、判定、原因与来源；运行角色无直接表权限，ENABLE/FORCE RLS。五个SECURITY DEFINER函数固定安全search_path、撤销PUBLIC，仅API可执行三个公开能力入口。
- 已实现POST/GET `/api/v1/questions/{id}/correction-attempts`、POST `/api/v1/questions/{id}/correction-attempts/{attempt_id}/judgments`。儿童只可提交/读绑定学生，监护人逐学生授权后才可核对；UUID操作键按可信actor/资源隔离，同键同体复用、异体409；源effective_revision与核对judgment_revision分别CAS。历史按created_at/id稳定分页。
- 新尝试始终pending_review/unverified；原题须当前guardian/incorrect。原题版本变化后旧尝试/判断保留并标source_is_current=false，旧操作重放仍需实时授权，禁止为旧源新增核对。图片必须同租户/学生/代次且ready/已清洗；不返回操作者ID、供应商字段或存储路径。
- API提交/核对与最小化审计同事务，故障整体回滚；并发同键不重复创建，同版本核对一成一冲突。原题/OCR/判定历史、模型/额度、改判事件不变；人工核对不作为独立练习、掌握或模型金标准。没有报告联动或自动判分。
- 迁移验证：0011→head、重复升级、空降级/重升；存在尝试或核对时拒绝销毁历史；模拟0012升级末段失败后新表/函数及版本变更全部回滚，原题与人工判定历史保持不变。

### 0012 验证记录与边界

- 上一轮订正专项40项通过；首次全量 **428 passed、1 failed、1 warning，109.69s**。失败是数据库JSON时间戳省略小数尾零、列表经datetime序列化补齐，导致审计回滚测试对同一记录的字符串比较不一致，不是历史内容发生变化。统一提交/核对返回的created_at序列化，并新增0–6位小数的7项确定性回归，未放宽原历史完整性断言。
- 订正专项 `uv run --directory backend pytest -q tests/test_correction_attempts.py tests/test_correction_migration.py --tb=short`：**47 passed，1 warning，13.45s**。
- 全量 `uv run --directory backend pytest -q --tb=short`：**436 passed，1 warning，109.14s**；`uv run --directory backend ruff check . --output-format concise`：**All checks passed!**。警告为既有Starlette TestClient/httpx弃用提示，未作为通过项或屏蔽。
- 文档复验：本轮更新的 **7份Markdown、54个内部文件链接、9个JSON示例**校验通过，代码围栏配对，验证结果占位符已清除。0011及更早验证记录保留为历史，与0012当前记录分节区分。
- 本轮只使用随机隔离PostgreSQL库/角色及测试专属Redis队列；未迁移、重读或bootstrap共享开发库、未清理共享数据或重置凭据。代码head0012不等于开发库部署版本，后者仍仅保留上次0006记录。
- 未运行真实模型/账单/质量评测；未重跑前端构建、浏览器测试、截图或API启动；三端原型没有接入新API。FR-C06仅记录/人工核对层完成，自动评估、提示、当时FR-C07草稿/不会/跳过尚未实现（0013现已补首步记录层），FR-M03独立练习/掌握、报告与模型评分仍待实现，不宣称WZ-07/WZ-08/T06/T07整票验收。

## 已实现首步复习工作台（0013，2026-10-25）

- 新增私有 `review_tasks/review_attempts/review_reschedules` 三表及六个 API：监护人建首步任务/改期，授权监护人或绑定儿童查询，儿童保存文字草稿/提交/求助/跳过。数据库仍为 PostgreSQL；任务按题目/有效版本/step自然唯一，D取当前题文最早监护人确认错误的上海日期，首次到期D+1。
- 草稿和改期分别CAS；提交/求助/跳过保存服务端草稿快照并推进草稿版本，改期追加原/新日期和原因。submitted仅进入awaiting_review，全部尝试仍pending_review、independent_correct=false；needs_help/skipped保持open，不记完成/掌握或推进后续间隔。
- 列表按created_at/id稳定分页且只列当前有效源；due_only按上海到期日过滤，不排除awaiting_review。旧题文/判定版本的任务历史可读并标source_is_current=false，但禁止新写。原键原体重放实时复核授权，返回当前投影而非首次快照；同键异体409，重复不追加历史或审计。
- 三表ENABLE/FORCE RLS，API/worker/dispatcher无直表CRUD。八个SECURITY DEFINER函数固定安全search_path、撤销PUBLIC，仅API可执行六个公开能力，两辅助函数不开放。API写入与审计同事务；不改原答/OCR/判定/订正，不调用模型、不扣额度或重建报告。
- 新增5项独立数据库迁移验证：空降级/重升及重复升级与权限检查；任务、作答、改期三种历史分别拒降级；升级后故障注入整体事务回滚，新表/函数与版本无残留、原题不变，重新升级可用。每项使用独立随机隔离库，不降级其他模块使用的测试库。

### 0013 验证记录与边界

- 首轮44项行为专项有2项失败：监护人邀请夹具错误地绑定空学生列表，以及撤回同意后任务ID查询预期403而实际按隐藏存在性契约返回404。修正夹具为绑定另一学生，并保留未授权404断言；学生列表撤回同意仍断言403，未放宽生产权限或错误码。
- 专项 `uv run --directory backend pytest -q tests/test_review_workbench.py tests/test_review_migration.py --tb=short`：**49 passed，1 warning，14.81s**（44项行为、5项迁移），覆盖权限/撤回/旧源、草稿恢复、不会/跳过、CAS并发、幂等重放、分页/到期日期、输入约束、审计回滚、私表和能力隔离。
- 全量 `uv run --directory backend pytest -q --tb=short`：**485 passed，1 warning，124.59s**；`uv run --directory backend ruff check . --output-format concise`：**All checks passed!**。保留既有Starlette TestClient/httpx弃用警告，不屏蔽处理。
- 文档复验：本轮更新的 **7份Markdown、68个内部文件链接、12个围栏JSON示例**校验通过，代码围栏配对，验证结果占位符已清除。
- 本轮未迁移、重读或bootstrap共享开发库，未重置真实凭据；共享库仅保留上次0006版本记录，代码head0013不代表部署版本。未运行前端构建、浏览器/截图、API启动或正式三端联调，无真实模型、账单、质量和生产验收结论。
- 仅FR-C07首步文字记录、FR-M04首次日期/家长改期局部实现；WZ-09/T08不能整票验收。图片复习、反馈/答案解锁、独立正确证据、D+3/7/14、失败重置、掌握/复错、周月优先级计划和报告仍待做，不能以submitted或人工订正核对替代。

## 已实现课表核心（0014，2026-10-25）

- PostgreSQL新增terms/timetable_versions/timetable_slots/lesson_overrides四表与六个API操作：学期创建/列表、课表读写、日期实例、单次覆盖。学期首尾含且最多370天，ISO周起始可配，首日所在周为1；固定Asia/Shanghai，all/odd/even模板，学期外无实例，查询最多93天。
- 模板稳定slot UUID，每次全量快照追加；move/cancel/restore以slot/source_date为锚点，可同学期跨周。实例id不因调课改变；有效覆盖关联课程必须先恢复才能改删模板，不静默丢历史。
- 按实际覆盖后学期实例检测同日同节次或时间半开区间相交；409结构化冲突仅在用户显式确认后保存。模板和覆盖共用term.revision CAS，按学生→学期锁序；并发同版本仅一个成功，审计失败回滚所有写入。
- 家长写/绑定儿童读、逐学生授权/家庭隔离、撤回与删除代次围栏；四表FORCE RLS。API最小表权限及受限安全函数，worker/dispatcher无入口。有学期/模板/覆盖历史拒绝降级；空降重升、重复升级、升级故障整体回滚均用独立随机库验证。

### 0014 验证记录与边界

- 专项 `uv run --directory backend pytest tests/test_timetables.py tests/test_timetable_migration.py -q`：**49 passed，1 warning，8.85s**，其中44项行为、5项迁移验证。补充覆盖撤销逐学生授权、跨家庭隐藏、模板/覆盖CAS竞争、恢复遇新冲突及创建审计回滚。
- 上次全量为515 passed/10 failed；10项均为既有bootstrap/迁移测试硬编码旧head0013导致，现仅调整head预期为0014，明确降级目标/回滚基线仍保留0013。新增恢复冲突测试的课程设为第2周对应even，避免夹具在其他日期提前冲突。
- 全量 `uv run --directory backend pytest -q`：**534 passed，1 warning，127.51s**，退出码0；`uv run --directory backend ruff check src tests migrations`：**All checks passed!**。保留既有Starlette TestClient/httpx弃用警告，不屏蔽处理；不以测试通过替代产品整票验收。
- 文档复验：本轮更新的 **7份Markdown、73个内部文件链接、15个围栏JSON示例**校验通过，代码围栏配对，最终测试结果占位符已清除。
- 本轮未迁移/重读共享开发库、未改凭据；共享库仅有上次0006记录，代码head0014不代表部署版本。未运行API服务/前端构建/浏览器截图/三端联调、真实模型或生产验收。截图OCR、教材章节/预习失效联动、提醒、学期编辑/删除仍未实现，WZ-10/T09不能整票验收。

## 平台登录简化为图形验证码（0032）

- 登录改为邮箱、密码和一次性 4 位字母数字图形验证码；不再要求验证器或 `WIZZY_PLATFORM_MFA_KEY`。
- 首位管理员离线初始化只收邮箱和密码；已有账号仍拒绝重建。
- 接口新增 `GET /api/v1/admin/auth/captcha`；`POST /auth/login` 使用 `captcha_id` 与 `captcha`。迁移 `0032_platform_password_captcha`。

## 已实现平台身份与只读RBAC基础（0015，2026-10-25）

- 六张独立平台私表、两角色与两个只读权限；密码Argon2、历史TOTP列保留、独立30分钟摘要会话。七个admin接口覆盖登录/本人/本人退出与退出全部、只读角色/账号/审计，不授予儿童内容访问。登录方式见上方0032。
- TOTP±1步且DB重验自身时钟，账号行锁内原子消耗时间步；已消费较新步后更旧步也拒绝。每次请求重验状态/凭据版本与实时权限，账号→会话锁序；403审计提交，审计失败整体回滚。
- 离线首次管理员初始化只允许一次；显式迁移连接与独立MFA密钥、隐藏输入、初始化码消费、账号/审计原子写入、不创建会话。已有账号（含停用）拒绝重建，无公开注册、找回或MFA绕过。
- 六表FORCE RLS、运行角色无直接CRUD，仅API有三个SECURITY DEFINER入口，固定search_path且PUBLIC撤权。有账号/会话/审计拒绝降级。实现与验证详见[0015计划](2026-10-25-platform-identity-core.md)。

### 0015验证记录与边界

- 专项 `uv run --directory backend pytest -q tests/test_platform_identity.py tests/test_platform_migration.py tests/test_platform_bootstrap.py --tb=short`：**64 passed，1 warning，13.28s**。
- 全量 `uv run --directory backend pytest -q --tb=short`：**598 passed，1 warning，144.28s**（本次收尾复跑），退出码0；保留既有Starlette TestClient/httpx弃用提示，不升级依赖或屏蔽警告。
- 静态检查 `uv run --directory backend ruff check . --output-format concise`：**All checks passed!**，退出码0。
- 本轮7份Markdown结构校验：**83处相对文件链接存在、34个代码围栏闭合、16个JSON示例可解析**；排除围栏/行内代码中的伪链接，不检查远程地址或标题锚点；0015计划无验证结果占位。

- 未迁移/重读共享开发库、未更改真实凭据；代码head0015不代表部署版本。未做API启动、前端构建、浏览器/截图、三端联调、真机、真实模型或生产验收。
- 在线邀请/角色写入/停用/跨人撤销、最后安全管理员保护、高危双审、MFA恢复/轮换、组织范围/期限、工单内容授权、模型治理与外部防篡改归档仍待实现，FR-A06/FR-A10、WZ-15/T14不整体验收。

## 平台身份正式Web与三端原型收尾（2026-10-25）

- 正式源码在 `frontend/platform`；仅0015身份七接口，账号/角色/审计只读，不用本地假操作冒充账号治理。范围、安全约束及命令见[客户端计划](2026-10-25-platform-web-client.md)，[视觉证据](../evidence/platform-web-2026-10-25/README.md)使用模拟API/虚构账号，不是实际数据库截图。
- 本次重跑：TypeScript与Vite7.3.7生产构建通过（1.72s）；API客户端 **11 passed**；Chrome UI **14 passed（5.9s）**。覆盖390px、键盘、401/403、错误重试、游标分页、到期/焦点重验和异步会话隔离。
- `WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest -q --tb=short`：**600 passed，1 warning，146.90s**，退出码0。含随机隔离PostgreSQL→真实FastAPI→同源Vite代理→Chrome联调；没有该环境开关时真实浏览器用例默认跳过。`uv run --directory backend ruff check . --output-format concise`：**All checks passed!**。保留既有Starlette/httpx弃用警告。
- 首轮默认后端回归曾为597 passed/1 failed/1 skipped：bootstrap夹具使用旧30秒TOTP码，跨时间窗导致重复初始化断言偏离。仅固定测试模块时钟并新增过期拒绝回归，专项 **16 passed**；不修改生产MFA时间窗。上面的600项是修复后完整重跑，不将失败记录写成通过。
- 原型工作区Vite7.2.2→7.3.7；移动菜单遮罩点击区域改为侧栏外，先观察390px断言失败（点击中心195px落在246px侧栏之内），再通过19路由小屏回归。构建 **2.00s**、浏览器 **45 passed（12.0s）**。主JS499.98kB/gzip175.12kB，本次无超过500kB告警，但仍接近阈值，不视为正式首屏性能验收。
- 两个前端工作区分别经官方registry审计，均 **0个已知漏洞**；[原型证据](../evidence/prototype-ui-2026-10-25/README.md)。不是源码或全仓库安全审计；开发/预览服务仅用于回环本地验证，不作生产服务器。
- 未迁移/重读共享开发库、未更改真实凭据；代码head0015不代表部署。在线邀请/角色写入/停用、高危双审、模型/教材/学校完整业务、家庭正式UI、真机和生产验收仍未完成，WZ-15/WZ-17不整体验收。此前各节结果保留为历史切片证据。

## 周期报告与共享计划后端收尾（2026-10-26，0016–0017）

- 0016 周/月不可变报告、原始有效题分母、订正/复习独立统计、来源快照下钻、stale及显式重建已实现；0017 有界周/月计划复用唯一 review_task，支持当前报告入口、稳定优先级、题量/预计时长双限及幂等并发。
- 全量含真实API浏览器联调：`WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest -q --tb=short`，**686 passed，1 warning，196.04s**。此前685通过/1失败为bootstrap旧迁移版本断言，更新断言后完整复验通过；既有Starlette/httpx弃用警告保留。
- 所有迁移/授权/回滚测试使用随机隔离PostgreSQL，不升级/重读共享开发库，不修改真实凭据。正式家庭页面、报告自动刷新、独立评估/掌握及真实模型门禁仍待完成，不能将上述切片视作WZ-09/WZ-13整体验收。

## 未完成边界与下一切片

受控消费任务已注册并经测试工厂运行，但当前没有真实 AI 或可生产启动的 worker。真实模型准确率、T01–T29 完整场景、三端正式 API 联调、微信/App 真机、教材权利与法务签字均未完成。

保守结果只读API、不可变题目/OCR修订、监护人双CAS人工确认、错题持久派生消费、订正记录/逐次人工核对、0013首步复习记录与0014课表核心已实现；下一切片应先明确并验证独立练习的评估证据，再推进**后续复习间隔/掌握状态、报告后台自动刷新及正式家庭UI**，不能用submitted、求助/跳过或人工订正核对直接判掌握。0011 仅有错题 drain/once CLI，不得将其误作生产常驻批改 worker、调度器或完整学习闭环。OCR 更正尚不创建新的 source_revision 或机器重跑；继续禁止未经评测的自动对错结论。真实适配器、评测发布门禁和账单对账仍待实现/外部证据。未知调用的运维处置与完整故障恢复仍未实现；真实凭据和质量证据属独立验收，不承诺 exactly-once，WZ-06/WZ-14 仍未整票验收。
