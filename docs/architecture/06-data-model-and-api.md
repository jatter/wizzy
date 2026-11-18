# 06 数据模型、状态机与 API 契约

> **数据库选型：PostgreSQL（非 MySQL）**。本文为逻辑模型/完整实施契约，不等于全部已建表；当前 `backend/migrations` 已实现至 `0033_review_plan_history`，已验证身份、图片、提交/额度、outbox 投递、执行租约、模型调用围栏、受控消费者、内部保守结果/成功页额度结算及授权结果只读查询、题目 OCR 不可变修订、监护人人工改判/事件原子落库、错题派生消费、订正尝试/逐次人工核对、首步复习工作台、课表核心及独立平台身份/只读 RBAC、周/月报告及共享复习计划、模型目录、错题派生回执并发保护、逐学生独立评测授权、平台账号角色/启停治理、学校目录/组织/名册及教材权利审核、PDF隔离接收、持久扫描/内嵌文本草稿及正文校核/独立发布切片及评测数据集/盲标/裁决/锁定、固定版本离线评测报告、跨账号会话撤销、密码验证码登录及复习计划历史切片（0007–0033 在随机隔离库验证，非开发库升级声明），当前 API 由 FastAPI 提供 OpenAPI。其余逻辑表、完整接口、备份恢复与生产验收仍待完成；实际交付边界见 [14](../plans/14-development-status.md)。

## 1. 通用约定

- 主键 UUID；所有时间 timestamptz/UTC；展示与统计按 Asia/Shanghai。金额用整数微元（1元=1,000,000微元），账单展示按币种精度舍入；禁止浮点累计。
- 家庭表：`tenant_id`、`created_at`、`updated_at`，可变业务聚合含 `revision`，需删除屏障的数据含 `deletion_epoch`。禁止用连续ID隐私性替代授权。
- 家庭范围唯一键为 `(tenant_id,id)`，外键同时带 tenant_id。查询即使有 RLS 仍显式租户过滤；儿童范围用 student_id 再验授权。
- 有效结果与历史 revision 分离；历史不可覆盖。JSONB 只放版本化模型响应/证据等变动字段，主关联和权限字段必须有列/约束。

## 2. 核心实体清单

| 表 | 关键字段/关系 | 重要约束/索引 |
|---|---|---|
| users / auth_identities | 监护人主体；手机号/微信主体独立映射 | 外部身份唯一，敏感字段加密，不存明文验证码 |
| tenants / memberships | 家庭；user/role/status | 活跃成员联合唯一；至少一名有效监护人，退出需转交或销户 |
| students / student_access | 昵称、年级；监护人对学生的授权 | 不强制真实姓名/学校；儿童令牌只绑定一个学生 |
| consent_records / child_devices | 目的/版本/授权/撤回；设备令牌哈希/过期/撤销 | 同意事件不可变；配对码哈希、5分钟、一次性 |
| media_objects | 随机key/hash/尺寸/类型/status/retention_until | tenant+id唯一；只有 verified/sanitized 对象可引用 |
| submissions / submission_pages | student/subject/source_revision/status；media/order | 同作业页序唯一；页同租户外键；原始提交与订正分开 |
| questions / question_revisions | 来源页/bbox/type/当前有效revision；题文/识别/学生答 | bbox 归一化[0,1]；source_revision不可倒退 |
| verdict_revisions | question/question_revision/revision/verdict/reason/actor/provenance | 0010 已实现人工历史；effective_verdict_revision 必须指向当前 OCR 版本的本题历史；双 CAS 更新 |
| correction_attempts / correction_judgments | 0012 已建：原题版本快照、文字或图片、逐次人工核对 | 每次尝试与核对追加保存；不改原始结果或正确率，不直接算掌握 |
| grading_jobs / job_stages | owner/source_revision/status/lease/attempt/deletion_epoch | tenant+operation_key唯一；按status/next_attempt_at索引 |
| model_runs | event/job/source_revision/deletion_epoch/stage/provider/model/model_version/operation_key/outcome/billing_state/cost_microyuan/provider_request_id | 已建调用许可与账务元数据；操作键及作业版本/代次/阶段唯一；未知费用为 NULL；usage/价格证据尚未建 |
| outbox_events / consumer_receipts | aggregate/version/event_type/payload；consumer+event | event唯一；receipt唯一，允许安全重复投递 |
| mistake_items | student/question/effective_verdict/state/next_due_at | 同学生同来源题唯一；仅已确认错误自动入库 |
| review_tasks / review_attempts / review_reschedules | 0013 已建：student/question/effective_revision、首步日期/草稿、尝试快照及改期历史 | 当前仅 step=1，question+effective_revision+step 唯一；submitted 仅待核对，不记完成/掌握；完整排程和报表引用待实现 |
| terms / timetable_versions / timetable_slots / lesson_overrides | 0014 已建：学期、统一版本历史、周模板快照、单次覆盖历史 | 同日同节次/时段重叠显式确认；模板及覆盖共用 revision CAS；Asia/Shanghai |
| textbooks / textbook_editions / chapters | ISBN可选/出版社/年级册次版次/章节树 | 公共元数据；授权状态/证据/到期控制内容开放 |
| textbook_rights / student_curriculum | 权利链；学生教材绑定/确认进度 | 授权到期冻结新分发，保留必要审计不继续展示全文 |
| preview_cards | student/lesson_instance/chapter/source_version | 调课/教材版本变化令旧卡stale，不凭空生成页码 |
| reports / report_sources | student/type/period/source_version/快照 | student+type+period_start+source_version唯一；标stale后重算 |
| quota_accounts / quota_reservations / usage_ledger | 周期额度/预留；整数成本、供应商对账状态 | 并发扣减事务化；reservation operation_key唯一 |
| admin_roles / config_releases / audit_events | 平台权限、配置灰度、工单审计 | 平台身份与家庭内容权限分离；审计限制修改 |
| deletion_requests / deletion_tombstones | 范围/代次/状态/备份最迟过期日 | 一次删除请求幂等；恢复备份必须重放墓碑 |

P0 所需表先建，P1工整度可追加 handwriting_assessments（量表、各维、可评分状态、图上证据），不可塞进正式学科分数。共享教材内容如有授权放独立私有bucket/元数据；不混入儿童作业库。

### 2.1 平台治理新增实体（逻辑模型；0015仅落库身份与只读RBAC基础）

| 表/聚合 | 关键字段与约束 |
|---|---|
| school_directory / school_directory_revisions | 0022已建学校代码、名称、地区、学年、启停及不可变版本；不授予家庭内容访问 |
| schools / school_memberships | 完整组织模型待建：组织父级、学校成员角色/数据范围；不能将目录登记视为组织授权 |
| student_school_links / guardian_links | 学生与学校/监护人关联、有效期、来源、状态；关系变更留历史 |
| platform_users / roles / permissions / role_bindings | 完整治理逻辑模型；0015实际采用下述六张platform_*表，账号单角色；组织范围、授权有效期与在线高危双审尚未实现 |
| model_registry / model_versions / model_routes | provider、用途、固定版本、区域、secret_ref、合同状态、路由与灰度；secret_ref 只指向密钥管理服务 |
| eval_datasets / eval_cases / gold_labels / eval_runs / eval_metrics | 数据集版本/锁定状态、脱敏样本引用、双标注和仲裁、模型版本、题型切片、混淆矩阵、置信区间；禁止把家长改判直接当金标准 |
| textbooks / textbook_editions / textbook_assets / parse_jobs / text_sections | rights_basis、许可范围/期限、原 PDF 对象、OCR 版本、章节/页码映射、TXT 修订、抽检与发布状态 |

### 当前 outbox 物理实现（0004～0006）

- `outbox_events` 在原有事件字段上增加 `next_dispatch_at`、`lease_token` / `lease_until`、`dispatch_attempts`、`consecutive_failures`、`last_error_code`；租约必须成对为空或非空，失败计数限 0–9，错误码仅允许 `broker_unavailable` 或 NULL。
- `claim_outbox(batch_size)` 限 1–100；仅锁 outbox 行并 SKIP LOCKED，基于 DB 中 queued 作业或执行租约过期的 running 作业、家庭/学生状态、同意及删除代次发现事件，返回最小标识和租约 token。`0006` 排除已有任意 `model_runs` 的事件，不因调用结束或费用已知而放行。dispatcher 一次仅抢占一条，提交后发布。
- `settle_outbox(event, token, published)` 仅接受匹配且未过期的租约；成功记录 `published_at`、清零连续失败，符合发现条件且无调用记录的 queued 事件 300 秒后仍可重放。`dispatch_attempts` 统计抢占次数，不等于消费次数；`published_at` 不代表执行完成。
- 两个函数固定 `search_path=pg_catalog,pg_temp`、对象全限定、撤销 PUBLIC 执行权，仅授予独立 dispatcher 运行角色。函数 owner 为可绕过 FORCE RLS 的离线迁移身份；API 与 dispatcher 均不得拥有此权限，dispatcher 无业务表直接读写权。
- `0005` 增加 `execution_dispatch_attempt`：外呼前每次执行尝试的首次恢复可绕过旧重播时间；抢占清除该旧延迟，后续仍受投递租约与成功重播/失败退避限制。
- 以上仅为投递租约，**不是执行租约或结果写回围栏**。执行去重由下述独立回执提供；消息不带可信 tenant_id，消费者集成时必须从 DB 重载授权关系并重新校验。

### 当前执行租约物理实现（0005）

- `execution_receipts` 保存事件/作业、可信家庭/学生/提交、源版本/删除代次、state/attempt、`lease_token/lease_until`；事件唯一回执与上下文行锁阻止重复消息并发获得有效执行权。
- `ExecutionStore.claim/heartbeat/release` 通过独立受限 worker 角色调用 DB 函数。claim 后 job/submission 为 running/processing，预占仍 reserved；租约 60 秒，心跳/释放须匹配当前授权、未过期 token、版本及代次。
- 无调用记录时，外呼前中断可有限恢复：前两次 release 分别退避 2 秒、4 秒，最多 3 次执行尝试；耗尽进入 deadletter/failed 并原子释放预占一次。`0006` 封装 claim/release 并排除有调用记录的事件，不能将执行重试当作外呼重试。

### 当前模型调用围栏物理实现（0006）

- `model_runs` 关联执行回执与同家庭作业/学生/提交，固定 `execution_token`、`source_revision` 和 `deletion_epoch`；`operation_key` 全局唯一，另有 `UNIQUE(job_id,source_revision,deletion_epoch,stage)`。stage 仅 `ocr/structure/verify/tutor`，provider/model/model_version 为受限标识，不保存原始模型响应或题文。
- 内部 Python 能力 `ExecutionStore.begin_call` 对应 SQL `begin_model_call`，不是公开 HTTP API。持有规范上下文锁后重验授权、同意、状态、版本、代次及有效执行 token；**事务提交并取得许可后才可外呼**。同阶段仅许可一次，既有调用未成功或费用未知时不能开始其他阶段；成功且费用已知也不代表可重取同阶段或复用业务结果，当前不强制完整阶段顺序。
- 初始 `outcome=in_flight`、`billing_state=unknown`、`cost_microyuan=NULL`。内部 `finish_call` / SQL `finish_model_call` 仅接受 succeeded/failed 结局及受限供应商请求标识；金额为非负 bigint 微元（1 元 = 1,000,000 微元），未知不默认为 0。结局/请求标识不可改写，未知金额可补已知，已知金额仅允许同值幂等，不能改值或退回未知。
- finish 匹配原调用 ID、操作键、token、版本及代次；取消、撤回、租约过期后仍可提交匹配的迟到账务回执，**不写业务结果、job/submission 状态或用户额度**。known 仅表示已提供符合契约的金额，不代表供应商账单已核验；测试金额均为合成输入。
- 任意调用记录均阻断 release/reclaim/恢复投递；提交许可后、网络发送前崩溃也保守停住。本地操作键不证明供应商接收或幂等，尚无自动对账、人工解锁或新的 pending 业务状态。
- 表启用 ENABLE/FORCE RLS，API/dispatcher/worker 均无表/列直接权限；仅 worker 可调用 begin/finish。SECURITY DEFINER 固定安全 search_path、撤销 PUBLIC 执行权；旧 `_unfenced` 函数对 PUBLIC 和全部运行角色均撤权。有任何调用记录时 downgrade 明确拒绝，禁止删记录绕过保护。
- 模型目录/评测发布、usage/token/价格证据、供应商账单核对均未实现；内部结果与成功页额度结算见下节；不能将本表视为完整模型管理或质量评分功能。

### 当前结果与额度结算物理实现（0007）

- `grading_results` 为内部不可覆盖结果：唯一 event_id 及 `UNIQUE(job_id,source_revision,deletion_epoch)`，关联可信 tenant/student/submission/job，记录原执行 token、JSON payload、successful_pages/failed_pages。ENABLE/FORCE RLS，三个运行角色无直接读写权限，不是逻辑模型中全部题目/修订表已实现。
- `quota_reservations.consumed_pages` 保存本次消费页数，非 consumed 状态必须为 0；执行回执增加 settled，outbox 增加 `submission.processed`。完成事件仅带提交/版本/代次/状态/页数，不含题文作答。
- `ExecutionStore.complete` / `complete_execution(event,job,version,epoch,token,doc)` 不是 HTTP API。SQL SECURITY DEFINER 固定安全 search_path，撤销 PUBLIC，仅 worker 可调用。依次锁可信上下文、执行回执、关联调用、额度预占及账户，重验授权/同意/版本/代次；等待额度锁后再次检查租约，防止过期结果结算。
- 所有关联调用必须终态、费用 known 且绑定一致；每个成功页须引用本事件 structure/verify 的 succeeded+known 调用。页数和页位置匹配原提交；原子写结果、扣除成功页额度并释放其余预占、推进状态、清空租约并写完成 outbox。全成功 completed、部分 partial、全失败 failed；全部失败仅释放额度。
- 相同 token/JSON 结果可幂等重放，仍重验当前授权/删除代次；不同结果或 token 拒绝覆盖。事务异常不得留下部分额度或事件；有结果时 downgrade 拒绝销毁证据。`complete=false` 不是重试付费调用的许可。
- Python/SQL 双层强制 `1.0-conservative`：1–10 页、prompt_hash 与规则/评测/图片流水线版本、受限题型、原始题文作答、bbox/同页证据。每题只能 pending_review/unsupported、needs_guardian_review=true，禁止 correct/incorrect/unanswered 与额外答案字段。没有正式评测门禁，不计算模型评分或正确率。
- 成功页额度仅是结构处理结算，不证明答案正确或供应商账单已核验。受控消费者已实现，授权只读投影见下节；题目 OCR 修订见 0009、监护人人工判定见 0010；真实提供方、评测发布、供应商对账、机器判定与生产验收仍未实现。

### 当前结果只读投影（0008）

- 已实现 `GET /api/v1/submissions/{submission_id}/result`，使用已验证的监护人或儿童会话；不接受客户端传入 tenant/user/student 身份作为授权依据。家庭 owner、明确获授该学生权限的活跃监护人，以及仅绑定该学生的儿童会话可读。
- 路由先在 RLS 下检查提交及学生权限、锁住当前同意/学生状态；`read_grading_result(uuid)` 再校验家庭/主体、活跃学生/同意、提交/任务状态、源版本、删除代次及 settled 回执的一致性。SECURITY DEFINER 固定安全 search_path，撤销 PUBLIC 执行权，仅 API 角色可执行；三个运行角色仍无 `grading_results` 直接表权限。
- 成功返回统一 `{data, request_id}` 包装。`data` 字段仅为 `submission_id`、`student_id`、`source_revision`、`status`、`created_at`（结果落库时间）、`schema_version`、`successful_pages`、`failed_pages`、`needs_guardian_review`、`review_notice`、`pages`。`schema_version` 固定 `1.0-conservative`，复核标识恒为 true，提示明确不能作为题目对错或模型正确率依据。
- `pages` 按原提交 `position` 排序，公共字段为 `position/media_id/status`；成功页增加 `questions`，失败页仅增加受限 `error_code`。题目按 `ordinal` 排序，仅保留 `ordinal/question_type/stem/student_answer_raw/bbox/ocr_confidence/verdict/reason_codes/needs_guardian_review/evidence`，不提供参考答案。`verdict` 仅 pending_review/unsupported。
- 不返回内部 model_run_id、prompt_hash、规则/评测/图片流水线版本、执行 token、提供方请求 ID 或费用信息；media_id 不是公开图片 URL，图片访问仍须走原有受限端点。
- completed/partial 及已结算的全失败结果均返回 200；全失败不伪造题目。未生成结果或关联状态/版本不再有效返回 409 `result_not_ready`。不存在、跨家庭/跨学生、旧删除代次或非活跃学生返回 404；仍有效的监护人会话遇到未同意返回 403；会话失效/家庭停用返回 401；非法 UUID 返回 422。撤回后重新同意不恢复旧代次结果。
- 查询响应 `Cache-Control: no-store`；只读取已结算结果，不触发外呼、结果改写、额度扣减或重试。0008 仅增加读取函数，降级删除函数，不删除结果；开发库本轮未升级，最新验证见 [14](../plans/14-development-status.md)。

### 当前题目与 OCR 修订物理实现（0009）

- `questions` 持有稳定 UUID、可信 tenant/student/submission/result/deletion_epoch、页序/题序、不可变 `machine_question` 和 `current_revision`。`question_revisions` 以 `(question_id,revision)` 为主键，保存题文、学生原答、machine/guardian 来源、操作者、原因与时间；作用域复合外键及延迟的当前版本外键禁止悬空指针。
- 结果插入触发器在结算同一事务内为**成功页**建立题目及 machine revision=1，失败页不建假题；迁移对既有结果回填。题目物化异常会使结果、额度及完成状态一并回滚，原结果 JSON 不覆盖。
- `GET /api/v1/submissions/{submission_id}/questions` 返回 `{submission_id,questions}`，按页序/题序排序；全失败返回空数组，未有有效结果 409。`GET /api/v1/questions/{question_id}` 返回当前题文/原答及完整历史：`question_id/submission_id/student_id/source_revision/page_position/ordinal/revision/stem/student_answer_raw/verdict/needs_guardian_review/machine_question/history`。`machine_question` 保留机器题目证据，但已由原始保守结果契约排除内部调用字段；history 含 revision、stem、student_answer_raw、origin、reason、created_at，不暴露操作者内部 ID。
- `POST /api/v1/questions/{question_id}/ocr-corrections` 仅当前有权监护人可写；儿童可读自己的题目但写入 403。必填 JSON 示例（当前 revision=1）：

```json
{
  "expected_revision": 1,
  "stem": "12 + 8 = ?",
  "student_answer_raw": "20",
  "reason": "核对原图，识别成了21"
}
```

- 成功 201 返回更新后的题目投影；并发基于 `expected_revision` CAS，失配返回 409 `revision_conflict`，不得覆写既有历史。版本号为严格整数 1–2147483647（不接受布尔值）；题文 1–8000 字符、原答 0–8000 字符、原因 1–1000 字符；题文/原因拒绝纯空白，文本拒绝 NUL，多余字段拒绝，非法请求 422。缺失/越权/旧代次 404，当前有效监护人未同意 403；统一 no-store。
- API 复用逐学生授权和实时同意锁，数据库函数按 tenant→membership→student→student_access→question 加锁并重验读取权限。两张私表 ENABLE/FORCE RLS，运行角色无直接表权限；仅 API 可执行固定 search_path、撤销 PUBLIC 的 `question_submission/read_question/read_submission_questions/correct_question_ocr`，dispatcher/worker 不可执行。`question_submission` 仅返回获授范围内的所属提交 ID，内容投影仍须实时同意/状态/版本围栏。
- API 更正与不含题文的 `question.ocr_corrected` 审计同事务提交；这是 API 事务保证，直接调用数据库能力函数本身不自动写审计。保留原 `/result` 输出不变，更正后有效 verdict 只为 pending_review、needs_guardian_review=true；不调用模型、不扣额度、不计算对错或模型正确率。
- **版本不能混用**：0009 的题目 `revision` 不等于提交 `source_revision`，后者未创建新的重跑作业。0010 另增判定历史与有效版本，见下节。重分题/替代映射、OCR 重跑、提示及衍生统计重建仍未实现；历史当前不分页，逐题投影尚未通过容量/性能验收。
- **两层降级保护独立生效**：0009→0008 仅允许无 guardian 修订的情况，有人工历史则拒绝销毁；即使没有人工历史，继续降至 0006 时，0007 仍拒绝删除已结算结果与额度结算证据。两种拒绝分别断言各自错误，不能把 0009 的提前拦截当成 0007 已通过。0008→0009 回填/重复升级及上述保护在逐测试独立临时库验证；开发库未升级或重读版本，见 [14](../plans/14-development-status.md)。

### 当前监护人人工改判与有效版本（0010）

- `verdict_revisions` 保存不可覆盖的人工判定历史，主键 `(question_id,revision)`，记录题文版本 `question_revision`、判定、原因、guardian 来源、操作者与时间。`questions.verdict_revision` 为累计判定历史版本（初始 0），`effective_verdict_revision` 为当前题文下的有效人工判定指针（初始 NULL），`effective_revision` 为用于衍生失效的单调版本（初始 1）。复合延迟外键确保有效判定属于本题且匹配当前 OCR 版本，不能复用旧题文下的确认。
- 题目读取投影新增 `verdict_revision/effective_verdict_revision/effective_revision/verdict_provenance/verdict_history`。历史含 revision、question_revision、verdict、reason、provenance、created_at，不返回 actor_id。有有效人工确认时来源为 guardian；OCR 修订后无有效确认时为 ocr_pending；初始为 machine。无有效人判或人判为 pending_review/unsupported 时，`needs_guardian_review=true`。
- `POST /api/v1/questions/{question_id}/verdict-overrides` 已实现，仅有权监护人可写。客户端须读取最新题目，同时携带题文 `expected_revision`（严格整数 1–2147483647）和累计判定 `expected_verdict_revision`（严格整数 0–2147483647），任一不匹配均为 409 `revision_conflict`，刷新核对后再决定，不能自动覆盖。初次确认示例：

```json
{
  "expected_revision": 1,
  "expected_verdict_revision": 0,
  "verdict": "correct",
  "reason": "监护人核对原图及解题过程"
}
```

- `verdict` 仅允许 correct/incorrect/pending_review/unanswered/unsupported；原因 1–1000 字符，拒绝纯空白、NUL 和额外字段，不裁剪合法输入。成功 201，返回统一 `{data,request_id}` 题目投影；非法输入 422，儿童写入 403，缺失/越权/旧代次 404，当前有效监护人未同意 403。实时授权与同意锁延续 0009，双 CAS 与 OCR 并发不能把确认套用到另一版题文。
- 人工确认追加历史、更新有效指针和有效版本、写 `question_verdict_events`，与 API 的 `question.verdict_overridden` 审计在同一事务提交，失败则全部回滚。OCR 更正同样递增有效版本、清空当前有效人判指针，保留旧人工历史并产生事件；之后须重新确认。API 将 request_id 传入事务供事件关联；直接调用数据库函数不自动生成 API 审计。
- `question_verdict_events` 为私有事件表：id、tenant_id、student_id、question_id、deletion_epoch、old_revision、new_revision、payload、request_id、created_at；`UNIQUE(question_id,new_revision)` 且 new_revision=old_revision+1。payload 中 old/new 是 **effective_revision**，不是 OCR 或人工历史版本；事件仅带版本和代次，不含题文、答案或原因：

```json
{
  "type": "VerdictChanged",
  "schema_version": 1,
  "question_id": "00000000-0000-0000-0000-000000000001",
  "old_revision": 1,
  "new_revision": 2,
  "question_revision": 1,
  "verdict_revision": 1,
  "deletion_epoch": 0
}
```

- 两张新私表 ENABLE/FORCE RLS，运行角色无直接读写权限。新增改判能力函数固定安全 search_path、撤销 PUBLIC，仅 API 可调用；worker/dispatcher 不可调用。0010 当时仅落库；0011 新增独立数据库消费者及错题投影，见下节，不经 outbox/Redis 转发。报告失效重建仍未实现，不能据此认定全部派生一致性完成。
- 人工改判不覆盖 `/result` 的机器原始结果，不调用模型、不创建 source_revision/新任务、不消费额度。它是家庭业务确认，**不是评测金标准，也不能直接作为模型批改正确率/评分证据**；教材、模型管理与质量发布门禁仍待实现。
- 0010 有人工判定历史时拒绝降级；即使仅有 OCR 变更事件也拒绝销毁。空历史/事件允许降至 0009，恢复原 reader 与权限，随后仍受 0009/0007 原有降级保护约束。0008 连续升级至 0010 时，先定点执行 `SET CONSTRAINTS public.question_current_revision IMMEDIATE` 冲刷回填遗留检查，再恢复 DEFERRED 后 ALTER；没有提交中间事务、禁用触发器或放宽约束。升级失败整体回滚、两个外键仍为 DEFERRABLE INITIALLY DEFERRED、回填不伪造人工历史及重复升级均有隔离库回归证据，见 [14](../plans/14-development-status.md)。

### 当前错题派生物理实现与接口（0011）

数据库继续采用 **PostgreSQL**（不是 MySQL），本切片使用数据库持久队列，不新增基础设施。

| 物理表 | 关键字段/约束 | 权限与用途 |
|---|---|---|
| `question_derivation_receipts` | event_id PK/FK、question_id FK、new_revision>1、state、created_at；UNIQUE(question_id,new_revision) | 私有不可重复消费回执；state 为 applied/obsolete/invalidated |
| `mistake_projections` | question_id PK/FK、effective_revision>1、event_id FK→回执 | 每题当前错误投影指针，不复制题文/作答；非完整收藏/掌握模型 |

- 两表 ENABLE/FORCE RLS，API/dispatcher/worker 无直接表权限。仅 worker 可执行 `consume_question_derivation()`，仅 API 可执行 `read_student_mistakes(uuid,integer,timestamptz,uuid)`，均 SECURITY DEFINER、固定 `pg_catalog,pg_temp` search_path 且撤销 PUBLIC；授权源沿用 `read_grading_result` 的实时租户/会话/学生范围与有效源校验。
- 消费按 `(created_at,event_id)` 选择无回执事件，`FOR UPDATE SKIP LOCKED` 支持多个消费者，随后锁 tenant → student → question 并复核活动/同意/删除代次、提交源版本、成功任务及 settled 回执。无效源记 invalidated 并删投影；非当前有效版本记 obsolete，不触碰最新投影；当前有效版本记 applied，只有当前题文的 guardian/incorrect 才 upsert，其他判定删投影。回执与投影同事务；失败回滚后可重试，不能声称跨系统 exactly-once。
- 读接口只返回投影版本等于当前 effective_revision 且仍 guardian/incorrect 的条目；OCR 更正或任何改判提交后，旧投影立即隐藏，后台处理后才出现新的错误条目。撤回/旧代次源即使投影尚未清理也不可读。原始 `/result` 与历史不被改写，不新增模型调用/额度结算；人工确认不是模型评测金标准。
- `GET /api/v1/students/{student_id}/mistakes` 接受 `limit`（默认20，1–100）与不透明 `cursor`。稳定按 `(grading_results.created_at,question_id)` 升序，取 limit+1 生成 next_cursor；不是按改判时间排序，不是跨请求快照。返回 data.items、data.next_cursor 和 data.pending_updates。条目字段：question_id/submission_id/student_id/source_revision/page_position/ordinal/revision/effective_revision/stem/student_answer_raw/verdict/verdict_provenance/created_at；不返回内部调用、改判人/原因或全历史。
- `pending_updates` 统计该学生当前有权且源有效、effective_revision>1 但无该版本回执的题目数；不是剩余事件数/进度百分比/时延承诺。可将列表显示为“数据更新中”，当前接口不实现通知推送。错误边界沿用现有契约：未登录/失效会话401，监护人有效学生未同意403，跨家庭/儿童越界/非活动学生404，非法参数或游标422；仅源版本/代次过期时有效学生列表返回200空列表，不把集合本身当404。
- `wizzy.learning` 使用受限 worker 连接；`--once` 至多处理一个，无参数处理至当前 idle 后退出，不是 daemon 或生产批改 CLI。事件留在 PostgreSQL，不依赖 Redis。本切片未部署调度/常驻消费，未承诺生产5分钟SLA。
- 0010 旧事件升级后无需重建即可消费，重复升级/空队列重复消费不增加回执；0011 有任意回执或投影时拒绝降级。派生状态为空可退至0010但保留旧事件，重升后继续消费。测试仅在隔离库进行，开发库未升级。
- 0011 本切片不含收藏/手工收录、业务筛选、去重、订正/掌握、复习、报告失效重建与模型准确率评分；0012 另实现订正记录与人工核对，见下节。隔离测试与故障证据见 [14 实施台账](../plans/14-development-status.md)。


### 当前订正尝试与监护人逐次核对（0012）

**状态：**已实现后端局部切片，正式三端尚未接入；不是自动订正批改、独立练习/掌握或报告联动。每次订正保留原题版本与原答，人工核对只作用于该尝试，不改变原题判定、原始作业正确率或模型准确率。

| 私有表 | 关键字段/约束 | 用途 |
|---|---|---|
| `correction_attempts` | tenant/student/question、deletion_epoch、question/verdict/effective_revision、source JSON、student_answer 或 media_id、可信 actor、operation_key、created_at；UNIQUE(question_id,actor_kind,actor_id,operation_key) | 原题版本快照和每次订正作答；文字/图片严格二选一；复合外键绑定同学生图片及本题修订 |
| `correction_judgments` | attempt_id、revision、expected_revision、verdict、reason、actor_id、operation_key、created_at；UNIQUE(attempt_id,revision)、UNIQUE(attempt_id,actor_id,operation_key) | 逐次人工核对历史；revision 从1开始追加，不覆盖旧判断 |

#### 提交尝试

`POST /api/v1/questions/{question_id}/correction-attempts`，对应儿童或有权监护人可调用，必填 UUID 格式 `Idempotency-Key` 请求头。先读取当前题目，以最新 `effective_revision` 提交；新尝试仅接受当前监护人已确认 `incorrect` 的题目，不要求0011错题投影已经消费。

```json
{
  "expected_effective_revision": 2,
  "student_answer": "重新计算，答案是20"
}
```

图片订正时省略 `student_answer`，改传已完成清洗的 `media_id`；图片须同租户、同学生、同删除代次且 ready，有 clean_key。接口只返回 media_id，内容仍走既有鉴权图片入口，不返回存储路径或直接公开链接。不能同时传文字/图片或两者均缺失。文字1–8000字符、拒绝纯空白和NUL；版本为严格整数1–9223372036854775807，拒绝布尔值和额外字段，不裁剪合法文本。

新尝试与合法幂等重放均返回201及 `{data,request_id}`。data 为尝试投影：`id/created_at/student_answer/media_id/source/source_is_current/verdict/verdict_provenance/judgment_revision/judgment_history`。source 含提交时的 `question_revision/verdict_revision/effective_revision/stem/student_answer_raw/verdict/verdict_provenance`。初始 `verdict=pending_review`、`verdict_provenance=unverified`、`judgment_revision=0`、`judgment_history=[]`，不得在客户端显示为“AI判定正确”或“已掌握”。created_at 在提交、核对及列表响应采用相同 ISO 8601 序列化，避免数据库时间小数位尾零造成字符串不一致。

#### 查询历史与逐次核对

- `GET /api/v1/questions/{question_id}/correction-attempts?limit=20&cursor=...`：对应儿童/有权监护人可读，data 为 `{items,next_cursor}`；limit 1–100，稳定按 `(created_at,id)` 升序，不透明游标不是跨请求快照；每条含完整核对历史，历史自身未分页。没有尝试时200空列表。
- `POST /api/v1/questions/{question_id}/correction-attempts/{attempt_id}/judgments`：仅有权监护人可写，儿童403；同样必填 UUID `Idempotency-Key`。客户端读取该尝试当前 judgment_revision 作为 CAS 输入。

```json
{
  "expected_judgment_revision": 0,
  "verdict": "correct",
  "reason": "核对订正作答与计算过程"
}
```

- `expected_judgment_revision` 为严格整数0–2147483646；verdict 仅 correct/incorrect/pending_review；reason 1–1000字符，拒绝纯空白、NUL和额外字段。成功201返回最新尝试投影，revision递增，来源为guardian；历史每项含 `revision/verdict/reason/provenance/created_at`，不暴露操作者内部ID。判断可以再次修正；再次订正须提交新尝试，不能覆盖前次作答。
- 幂等键按可信actor及题目（提交）或尝试（核对）隔离。同键同输入复用、不新增记录/审计，同键异输入409 `idempotency_conflict`。重放返回该尝试的**当前投影**，并非首次请求响应的永久快照，后续核对会体现在投影中。未存在该操作键时才检查当前源版本/CAS。
- OCR更正或原题改判导致 effective_revision 变化后，旧尝试与人工核对仍可读取、原操作可重放，但 `source_is_current=false`；旧尝试不得新增核对，返回409 `source_conflict`。新尝试需刷新题目，且新版本仍为guardian/incorrect；核对CAS不匹配为409 `revision_conflict`。失效源内容或撤回授权不能借重放读取。
- 未登录/失效会话401；有效监护人未同意403；题目/尝试/图片缺失、越权、非活动或旧代次404；输入/游标非法422，数据库能力校验失败可为 `invalid_correction`。统一 no-store，拒绝请求不回显原作答或内部标识。

#### 持久化、安全与升级边界

两表 ENABLE/FORCE RLS；API/worker/dispatcher 无直接表读写权限。五个函数均 SECURITY DEFINER、固定 `pg_catalog,pg_temp` search_path、撤销 PUBLIC：仅API可执行 `submit_correction_attempt/read_correction_attempts/judge_correction_attempt`，内部 `lock_correction_question/correction_attempt_view` 不授予运行角色。API实时授权与同意锁、数据库再校验和题目锁共同约束源版本及核对CAS，并发同键只建一次、同版本核对一成一冲突。

API写入与最小化 `correction.submitted/correction.judged` 审计同事务；审计失败则尝试/核对全部回滚，重放不重复审计。直接执行数据库能力函数不自动生成API审计。原题、不可变机器证据、原判定历史、改判事件、额度/模型调用与错题投影不被订正接口修改；人工核对不作为模型金标准或掌握证据。

0011→0012升级、重复升级、空状态降级/重升已在隔离库验证；有任意尝试或判断时拒绝降级，保留版本和历史；模拟升级中途失败时新表/函数整体回滚，原题与判定历史不变。没有迁移共享开发库。测试记录见 [实施台账](../plans/14-development-status.md)，联调步骤见 [本地开发](../local-development.md)。0012不含复习草稿/不会/跳过；0013已实现首步记录层，见下节。自动评估、有限提示、跨日独立正确证据/掌握、后续排程、报告重建、模型准确率评测和正式客户端仍待实现。

### 当前首步复习工作台物理实现与接口（0013）

**状态：**后端局部切片，正式三端未联调；仅建立第1步 D+1 任务与文字作答记录。提交不等于完成，不生成独立正确证据或掌握状态，不推进 D+3/7/14。数据库仍为 **PostgreSQL，非 MySQL**。

| 私有表 | 关键约束 | 用途 |
|---|---|---|
| `review_tasks` | 同租户/学生题目复合外键；UNIQUE(question_id,effective_revision,step)；step固定1；state仅open/awaiting_review | 保存题文/有效版本、Asia/Shanghai基准与到期日期、草稿和两类CAS版本 |
| `review_attempts` | UNIQUE(task_id,student_id,operation_key)及(task_id,expected_draft_revision)；independent_correct强制false | 每次提交/求助/跳过的草稿快照，追加而不覆盖 |
| `review_reschedules` | UNIQUE(task_id,revision)及(task_id,actor_id,operation_key)；expected_revision=revision-1 | 追加原日期/新日期/原因，保存家长改期历史 |

#### 任务建立与查询

- `POST /api/v1/questions/{question_id}/review-tasks`：仅有权监护人，body为 `{"expected_effective_revision":2}`，严格整数1–9223372036854775807；原题须当前guardian/incorrect，不依赖0011投影消费完成。成功201；按题目/有效版本/step自然幂等，不需Idempotency-Key，重复建不重复审计。题文或有效版本变化后须刷新版本，旧版本返回409。
- D为**当前question_revision最早一次guardian/incorrect判定创建时间的上海日期**，初始due_date=D+1；不是建任务日期。每个有效修订有独立首步任务，不自动推进后续步数；当前固定Asia/Shanghai，未支持学生自定义时区。
- `GET /api/v1/students/{student_id}/review-tasks?limit=20&due_only=false&cursor=...`：对应儿童/有权监护人；data为 `{items,next_cursor}`，limit 1–100，按(created_at,id)升序稳定分页，不是跨请求快照。只列当前有效来源；due_only仅筛选上海due_date≤当日，**不排除awaiting_review**，不得据此当作“待作答数量”。
- `GET /api/v1/review-tasks/{task_id}`：对应儿童/有权监护人实时授权下读详情，旧源历史可读并标source_is_current=false；不返回原始作答、参考答案/模型反馈、操作者ID、存储路径或幂等键。

#### 草稿、提交与改期

仅绑定儿童可写草稿与提交（监护人403）；仅有权监护人可改期/建任务（儿童403）。新写入要求当前来源且state=open；源变化返回409 source_conflict，已待核对返回409 task_conflict。所有请求拒绝额外字段。

`PATCH /api/v1/review-tasks/{task_id}/draft`，成功200；expected_draft_revision严格整数0–2147483646，文字0–8000字符、禁止NUL，允许空串清空。每次成功revision+1；没有幂等键，网络不确定时先读详情核实版本再保存，不能盲目覆盖。

```json
{
  "expected_draft_revision": 0,
  "student_answer": "重新列式，得到20"
}
```

`POST /api/v1/review-tasks/{task_id}/attempts`，必填UUID `Idempotency-Key`，成功201。提交保存**服务端当前草稿**快照，不接受新答案字段；先保存草稿，再用返回的最新draft.revision提交。

```json
{
  "expected_draft_revision": 1,
  "outcome": "submitted"
}
```

outcome仅submitted/needs_help/skipped；三者均令草稿版本+1并保留草稿。submitted要求非空白答案，任务进入awaiting_review并禁止新写；needs_help/skipped允许空草稿，任务保持open。每次尝试一律verdict=pending_review、independent_correct=false；不存在本切片的复习核对/解锁接口，不能复用订正核对冒充独立评估。

`POST /api/v1/review-tasks/{task_id}/reschedules`，必填UUID `Idempotency-Key`，成功201；expected_schedule_revision严格整数0–2147483646，due_date为日期，reason为1–1000非空白字符且禁止NUL。成功schedule_revision+1；允许过去日期用于补计划，不自动裁剪为今天。

```json
{
  "expected_schedule_revision": 0,
  "due_date": "2026-10-28",
  "reason": "调整本周学习安排"
}
```

#### 响应、重放与持久化边界

- 成功响应统一 `{data,request_id}`；任务投影字段为id/question_id/student_id/effective_revision/question_revision/stem/source_is_current/step/timezone/base_date/due_date/schedule_revision/state/created_at；draft含revision/student_answer；attempts含id/created_at/draft_revision/outcome/student_answer/verdict/independent_correct；reschedules含revision/due_date/previous_due_date/reason/created_at。两个历史数组按版本升序，目前不单独分页。各入口created_at统一ISO 8601序列化。
- 作答键按任务/绑定学生隔离，改期键按任务/可信监护人隔离；同键同体重放不重复历史/审计，返回**最新任务投影而非首次响应快照**；同键异体409 idempotency_conflict。原键原体可在源变化或任务待核对后重放，但仍需实时授权。新键的CAS不匹配409 revision_conflict；不得借更换键绕过冲突。
- 未登录/失效会话401；角色禁止403；任务ID缺失、越权、撤回同意或底层提交/执行/结算来源失效时404以隐藏存在性；仅题文/有效判定修订变化的旧任务仍可读历史（source_is_current=false）。学生列表的同意检查在撤回时403。非法字段/键/游标422（数据库能力校验可返回invalid_review）；响应no-store，不回显内部标识。
- 三表ENABLE/FORCE RLS，API/worker/dispatcher均无直接CRUD权限。八个SECURITY DEFINER函数固定pg_catalog,pg_temp search_path、撤销PUBLIC；仅API可执行create_review_task/read_review_task/read_student_review_tasks/save_review_draft/submit_review_attempt/reschedule_review_task，内部review_task_view/lock_review_task不授权运行角色。写入沿用源题授权锁序后锁任务，数据库入口再次校验权限和输入。
- API写入与review.enrolled/review.draft_saved/review.attempted/review.rescheduled审计同事务，审计失败全部回滚；直接数据库能力调用不自动创建API审计。不改原作业/OCR/判定/正确率、模型调用/额度或订正记录，不触发报告重建。
- 隔离库验证0012→0013、重复升级、空降级/重升；有任务、尝试或改期历史即拒绝降级，不可删除历史绕过。模拟升级失败，新表/函数和版本更新整体回滚，原题不变。未迁移共享开发库；证据见 [实施台账](../plans/14-development-status.md) 和 [验收记录](../11-test-acceptance-and-operations.md)。

### 当前课表核心物理实现与接口（0014）

数据库继续使用 **PostgreSQL**。本节是已实现契约，不代表课表截图导入、教材绑定、提醒或正式三端 UI 已完成。迁移文件为 `backend/migrations/versions/0014_timetable_core.py`。

#### 表、版本和实例

| 物理表 | 核心字段与约束 |
|---|---|
| terms | id、tenant_id、student_id、deletion_epoch、name（1–80非空白）、start_date/end_date、week_starts_on（1–7）、timezone、revision、template_revision、created_at；首尾包含且最多370天，初始两个版本为0 |
| timetable_versions | term_id+revision主键，kind=template/override、actor_id、confirm_conflicts、created_at；保存每次写入的操作者与冲突确认记录 |
| timetable_slots | term_id+template_revision+id主键；position、weekday、period、subject、weeks、start_time/end_time、room；每次模板写入完整追加快照，id为客户端提供的稳定UUID |
| lesson_overrides | id、term_id、revision、template_revision、slot_id、source_date、action、target_date、替换课程字段、reason；外键绑定原模板版本和slot；同一term_id+revision唯一，历史不覆盖 |

- 时区固定 `Asia/Shanghai`，不是可任意设置的家庭时区。星期 ISO 1=周一至7=周日；`week_starts_on`决定周边界，学期首日所在周为第1周；周模板 `weeks=all/odd/even` 按此计数，不按自然年周号。学期外无实例。
- 一张模板最多100节；`period`为1–20，`subject`为1–80非空白字符，`room`最多100字符。可选时间必须成对使用24小时制 `HH:MM`，开始早于结束。模板中slot UUID不能重复。
- 同日同节次，或双方都填写时间且半开区间 `[start_time,end_time)` 相交，即冲突；时间首尾相接不算相交。检测基于应用覆盖后的整个学期，不只是当前展示周。
- 实例不单独存表；稳定id为 `term_id:slot_id:source_date`，调课后仍保持源实例id，返回目标 `date`、目标 `week_number`、`slot_id/source_date`、`overridden`和课程字段。停课不出现在有效实例列表，覆盖历史保留在课表详情。
- `terms.revision`是模板和覆盖共用的CAS版本，每次成功写入加1；`template_revision`仅模板保存时推进。先锁学生再锁学期，模板与调课并发同预期版本仅一个成功。所有写入与审计同事务，失败无部分版本/快照/审计。

#### 已实现 API（统一前缀 `/api/v1`）

| 方法与路径 | 请求与结果 |
|---|---|
| POST `/students/{student_id}/terms` | name/start_date/end_date，week_starts_on默认1；201，data为学期对象（含两个初始版本0） |
| GET `/students/{student_id}/terms` | limit默认20、1–100，cursor稳定分页；200，data含items/next_cursor |
| GET `/timetables/{term_id}` | 200，data含term、当前slots、按revision排序的overrides完整历史、conflicts |
| PUT `/timetables/{term_id}` | expected_revision、完整slots数组、confirm_conflicts默认false；200，返回更新后课表详情；空数组可清空无有效覆盖的模板 |
| GET `/timetables/{term_id}/instances` | 必填start_date/end_date，含首尾最多93天且起止有序；200，data含items/timezone/revision/conflicts；查询可跨学期边界但不会生成学期外实例 |
| POST `/lesson-overrides` | term_id/expected_revision/slot_id/source_date/action/reason，confirm_conflicts默认false；move还须target_date及完整replacement；201，返回更新后课表详情 |

创建示例（日期仅用于演示课表规则）：

```json
{"name":"2026秋季","start_date":"2026-09-02","end_date":"2027-01-31","week_starts_on":1}
```

首次保存模板示例（用实际term_id调用PUT；后续编辑保留已有slot id）：

```json
{"expected_revision":0,"confirm_conflicts":false,"slots":[{"id":"4c4790b2-c051-4a6e-bcc3-6b8312916434","weekday":1,"period":1,"subject":"数学","weeks":"all","start_time":"08:00","end_time":"08:40","room":"301"}]}
```

单次跨周调课示例（用实际term_id替换示例值）：

```json
{"term_id":"921408c9-9434-41b3-a3e2-1e45d561ba82","expected_revision":1,"slot_id":"4c4790b2-c051-4a6e-bcc3-6b8312916434","source_date":"2026-09-07","action":"move","target_date":"2026-09-15","replacement":{"period":2,"subject":"数学","start_time":"09:00","end_time":"09:40","room":"302"},"reason":"学校临时安排","confirm_conflicts":false}
```

- `source_date`必须是当前模板按单双周实际存在的源课程日期；`move`目标必须在同学期，可跨周，也可保留原日更换节次/内容。再次修改仍以原slot/source_date为锚点，不把上一次目标日当新源日。
- `cancel`仅停指定一次，`restore`恢复该源实例的模板课程；两者不允许target_date/replacement。三种动作均要求1–1000非空白字符原因。相同源实例按最后一条覆盖生效，旧覆盖仍可读。
- 存在有效move/cancel的slot不能直接修改或删除模板；先restore，再修改模板。恢复本身也检查与后来新增课程的冲突；未确认失败不会丢失历史。其余slot可正常编辑。
- 当前不提供操作键幂等：成功后丢响应并原体重试会因旧expected_revision返回409，客户端应先重读确认，不自动重复创建学期或盲目重试写入。

#### 错误、权限与迁移边界

| 状态/错误码 | 含义与客户端动作 |
|---|---|
| 409 revision_conflict | 模板或覆盖已更新；重读整份课表并处理差异，不盲目覆盖 |
| 409 schedule_conflict | error.field_errors为冲突数组，每项含date、slot_ids、instance_ids、reason=same_period/time_overlap；展示冲突，用户明确接受后才携带confirm_conflicts=true重交 |
| 409 active_override_conflict | 被编辑/删除slot有有效单次覆盖；先恢复对应源实例 |
| 422 invalid_lesson_source / invalid_lesson_target | 源课程不存在或目标越出学期 |
| 422 invalid_date_range / validation_error | 实例区间不合法，或字段/时间/动作组合不合法；不产生版本 |
| 404 not_found | 学期不存在或当前会话无权访问；不暴露他人家庭/学生存在性 |

有权监护人可写，绑定儿童仅可读本学生。沿用活跃家庭/成员、逐学生授权、同意与deletion_epoch即时围栏；按学生创建/列表也需通过同意检查。四表ENABLE/FORCE RLS；API仅SELECT/INSERT，另仅可UPDATE terms的两个版本列，无历史UPDATE/DELETE/TRUNCATE权限；worker/dispatcher无表或函数权限。`timetable_access`为固定安全search_path的SECURITY DEFINER函数，撤销PUBLIC执行权限，仅API可执行。

隔离库验证0013→0014、重复升级、空降级/重升、三类历史拒绝降级及升级故障事务回滚；有学期本身即拒绝降级，不能删除历史绕过。未迁移或重读共享开发库。课表与教材章节/预习的失效联动、截图OCR、提醒、正式页面接入仍未实现，不能据此判WZ-10/T09整体验收。


### 当前平台身份与只读 RBAC（0015）

以下保留0015切片边界；角色/启停写入与独立审批已由下方0021增量补齐，在线开户、改密与MFA生命周期仍未实现。

**状态：**独立后端基础切片已实现，FR-A06/FR-A10、WZ-15与T14均未整体验收；原型未接入。平台身份、会话和令牌与家庭/儿童体系分离，`audience=platform`，两类令牌不能互用；平台权限不授予儿童作业、答案或图片访问。

| 物理表 | 当前用途与边界 |
|---|---|
| `platform_roles` / `platform_permissions` / `platform_role_permissions` | 两角色`security_admin`与`operator`；两个权限`platform.accounts.read`、`platform.audit.read`仅授予安全管理员；operator仍可查看自身与角色目录、撤销自己的会话 |
| `platform_accounts` | 唯一规范化email、Argon2密码哈希、历史`encrypted_totp`列（新账号写入占位`unused`）、凭据版本、单角色、active/disabled状态；登录验证码在应用内存一次性消费，不再依赖TOTP |
| `platform_sessions` | 随机令牌仅存SHA-256摘要；固定30分钟有效期、凭据版本与撤销时间；不滑动续期 |
| `platform_audit_events` | bootstrap、成功登录、已认证读取、权限拒绝、本人退出/退出全部会话；actor/request_id/时间，无儿童内容。失败登录仅有现有限流，不宣称完整失败登录审计或外部防篡改归档 |

六表均ENABLE/FORCE RLS，API/worker/dispatcher无直接CRUD权限。仅API可执行`platform_credentials(text)`、`platform_login(uuid,uuid,bigint,text,uuid,text)`、`platform_request(text,text,text,integer,timestamptz,uuid)`；均为SECURITY DEFINER、固定`search_path=pg_catalog,pg_temp`、对象全限定且PUBLIC撤权。函数owner是离线可绕过RLS的迁移身份，不可给运行服务使用。

#### 已实现接口

统一前缀`/api/v1/admin`；除登录外均须`Authorization: Bearer <platform token>`，成功响应沿用`{data,request_id}`。

| 方法/路径 | 当前契约 |
|---|---|
| GET `/auth/captcha` | 匿名；返回`captcha_id`与PNG data URI；答案不回传客户端，120秒有效，一次性消费 |
| POST `/auth/login` | email/password/captcha_id/captcha；先核密码再消费4位字母数字验证码；数据库行锁内重验状态/凭据版本；验证码错误、过期或重放与密码错误同为401 |
| GET `/auth/me` | 当前账号、角色、实时permissions、audience、session_id、expires_at；每次请求重验状态、版本与权限 |
| POST `/auth/logout` | 只撤销当前平台会话，返回`revoked=true` |
| POST `/auth/logout-all` | 只撤销本人全部平台会话，不支持跨账号撤销 |
| GET `/roles` | 已登录平台账号可读角色与权限目录，返回`data.items`；不是角色编辑接口 |
| GET `/accounts` | 须`platform.accounts.read`；投影仅id/email/role/status/created_at |
| GET `/audit-events` | 须`platform.audit.read`；投影仅id/actor_id/action/request_id/created_at |

登录示例均为占位演示数据，不是可用凭据：

```json
{
  "email": "security-admin@example.test",
  "password": "example-only-not-a-real-password",
  "captcha_id": "11111111-1111-1111-1111-111111111111",
  "captcha": "K7M2"
}
```

登录成功的`data`含`access_token`、`token_type=bearer`、`audience=platform`、`session_id`、`expires_in=1800`；令牌仅在该响应返回，禁止日志记录。账号/审计列表`limit`默认50、范围1–100，按`(created_at,id)`倒序游标分页，返回`items/next_cursor`；不是跨请求快照。不向客户端返回密码哈希、加密TOTP或凭据版本。

错误：401为无效密码/验证码、验证码过期或重放、无效/过期/撤销会话或账号停用/凭据版本失效；403为权限不足且`access.denied`审计必须提交；422为输入、游标或limit不合法；429为登录限流；500为内部故障，审计写失败时登录计数/会话创建或撤销整体回滚，不带数据库错误细节。账号→会话锁序固定，等待锁后重新检查状态与凭据版本。

首位安全管理员只可通过离线`wizzy.platform_bootstrap`用邮箱和密码初始化，账号与bootstrap审计同事务写入，不创建会话。任何账号已存在（包括disabled）均拒绝重建或重置；没有公开注册或找回。操作前备份并迁移，详见[本地开发与离线初始化](../local-development.md)。有账号、会话或审计历史即拒绝降级0015，不能删除历史来规避保护。

0015最初不含角色/启停写入、最后管理员保护或高危双审；这些账号变更能力已由下方0021增量补齐。在线邀请/改密、MFA生命周期、组织数据范围、工单内容授权及完整模型评测发布仍待实现；身份、模型目录及账号治理正式Web已接通，不等于完整治理。证据见[0015实施计划](../plans/2026-10-25-platform-identity-core.md)与[工程台账](../plans/14-development-status.md)。

### 当前平台账号治理（0021）

**状态：**后端及正式平台Web五接口已实现，隔离PostgreSQL真实浏览器联调通过；不等于FR-A10/WZ-15完整验收。迁移`0021_account_governance`未部署到共享开发库。账号新增`governance_revision`，初始0；两张不可变表`platform_account_changes`、`platform_account_decisions`记录申请、基准版本、前后角色/状态、原因、处理人及决定，拒绝不增加账号版本。

统一前缀`/api/v1/admin`，仅独立平台会话可调用；详情及历史验`platform.accounts.read`，申请验`platform.accounts.write`，批准/拒绝验`platform.accounts.approve`，新增两权限仅授予`security_admin`。

| 方法/路径 | 当前契约 |
|---|---|
| GET `/accounts/{account_id}` | 最小账号投影及当前revision；无密码、TOTP或令牌 |
| GET `/accounts/{account_id}/changes` | 游标历史，含pending/applied/approved/rejected、处理说明及stale |
| POST `/accounts/{account_id}/changes` | UUID operation_id、expected_revision、operator/security_admin角色、active/disabled状态及1–500字原因；新申请201，幂等重放200 |
| POST `/account-changes/{change_id}/approve` | 独立审批人、UUID operation_id及独立处理原因；200 |
| POST `/account-changes/{change_id}/reject` | 独立审批人、UUID operation_id及拒绝原因；200；失效申请仍可拒绝 |

- 授予**启用的安全管理员**权限需要另一位有审批权限的管理员批准；禁止自审。其余角色/启停调整即时生效。批准时重新检查申请人权限及目标版本，禁止降级或停用最后一位启用的安全管理员。
- 每次实际角色/状态变更使revision增加1并撤销目标已有全部会话；重新启用不会恢复旧会话。operation_id按操作者幂等，同键异体、跨目标或跨动作冲突；CAS并发仅允许一个生效。
- 409代码：`operation_conflict`、`revision_conflict`、`already_decided`、`separate_approver_required`、`proposer_ineligible`、`last_security_admin`。前端保留冲突草稿，显式刷新才清除；网络/5xx结果不确定时锁定表单并原样重试，关闭页面后不持久恢复该草稿。
- `accounts.change`、`accounts.approve`、`accounts.reject`与决定/账号/会话变更同事务；审计失败整体回滚，403拒绝审计正常提交。两表FORCE RLS，运行角色无直接CRUD；仅API可执行固定安全search_path的SECURITY DEFINER治理函数，PUBLIC撤权。存在治理历史则拒绝降级。
- 正式页面包含权限重验、自审无审批按钮、stale禁批准、迟到响应隔离及390px布局。真实联调覆盖停用/启用及待审批升权申请；独立批准/拒绝另有后端和模拟浏览器验证。独立跨账号会话撤销已由0031补齐（见下）；在线邀请/开户、MFA生命周期及组织数据范围仍待实现。

### 当前跨账号会话撤销（0031）

**状态：**后端与正式平台账号详情已接通，真实API/隔离PostgreSQL浏览器已验证丢失回执后的原请求重放。代码迁移链当前为`0033_review_plan_history`；本轮未迁移或重读共享开发库。

| 方法/路径（前缀 `/api/v1/admin`） | 权限与当前契约 |
|---|---|
| GET `/accounts/{account_id}/session-revocations` | `platform.accounts.read`；limit默认50、1–100，时间/id倒序游标分页；返回不可变回执，不枚举token |
| POST `/accounts/{account_id}/session-revocations` | `platform.accounts.sessions.revoke`仅安全管理员；UUID operation_id及trim后1–500字无控制字符原因；新操作201、原样重放200 |

- 私有表`platform_session_revocations`保存id、account_id、actor_id、operation_id、reason、revoked_count和created_at。操作者/操作UUID唯一；禁止自撤销、更新和删除；FORCE RLS且运行角色无直接CRUD/TRUNCATE。受限固定search_path能力函数与审计`accounts.sessions.revoke`同事务；审计失败回滚会话和回执，有历史则拒绝降级。
- 每次重验会话和实时权限，包括幂等重放；本人使用现有logout-all，跨账号操作返回409 `self_session_revoke`。相同UUID换目标或原因返回409 `operation_conflict`。
- 和账号治理共用全局锁顺序，并锁定目标账号，与登录确定撤销边界；撤销目标全部未撤销会话，只将尚未过期且凭据版本匹配的有效会话计入revoked_count，零条也留回执。保留角色、启停和governance_revision；同一操作重试不会再次撤销后续新登录。
- 正式页面支持原因校验、影响提示、回执历史/分页、网络与5xx不确定结果冻结原UUID/body、冲突核对和401/403清理、迟到响应隔离及390px布局。不确定草稿仅在本次页面内保留，关闭页面后需核对历史。验证证据见[0031实施记录](../plans/2026-10-26-account-session-revocation.md)。

### 当前学校目录（0022）

**状态：**后端五接口及正式平台Web已实现，真实API/隔离PostgreSQL浏览器联调通过；仅为FR-A09学校治理的目录切片。迁移`0022_school_directory`基于`0021_account_governance`，未升级共享开发库。

- `school_directory`保存唯一、固定的大写代码与当前版本指针；`school_directory_revisions`保存不可变配置、原因、操作者、操作UUID及请求快照。配置只含name、region、academic_year、status，当前版本通过复合延迟外键绑定对应学校历史，初始revision=1；修订表在数据库层拒绝UPDATE、DELETE和TRUNCATE。
- `platform.schools.read`授予security_admin和operator；`platform.schools.write`仅授予security_admin。每次执行重验实际平台会话、账号状态和实时角色；学校登记/停用不授予或撤销任何家庭、学生或作业内容权限。

统一前缀`/api/v1/admin/schools`：

| 方法/路径 | 当前契约 |
|---|---|
| GET 空路径 | q按代码/名称/地区字面子串查询，可按active/disabled筛选；limit=1–100，默认50；按登记时间/id倒序游标分页 |
| POST 空路径 | UUID operation_id、code、name、region、academic_year、status、reason；登记201，原请求幂等重放200 |
| GET `/{school_id}` | 当前配置及revision、revision_id、actor_id、reason、created_at、registered_at |
| GET `/{school_id}/revisions` | 不可变版本历史，按修订时间/revision_id倒序游标分页，分页限制同列表 |
| POST `/{school_id}/revisions` | UUID operation_id、expected_revision及除code以外的配置和原因；新增修订201，重放200 |

- code去首尾空格并转大写，匹配`^[A-Z0-9][A-Z0-9_-]{1,39}$`，登记后不可编辑。name/region为1–120字，reason为1–500字；去首尾空格并拒绝控制字符。academic_year为相邻ASCII年份，例如2026-2027，首年2000–9998；status仅active/disabled。
- expected_revision为严格整数1–999999998，保存版本上限999999999。幂等键绑定操作者、操作和请求快照；409代码为`operation_conflict`、`school_code_conflict`、`school_revision_conflict`。401无效会话、403缺少权限、404学校不存在、422字段无效；错误响应不回显原始请求或数据库细节。
- 两表FORCE RLS，运行角色无直接CRUD，SECURITY DEFINER能力固定安全search_path并撤销PUBLIC执行权。目录写入、历史和`schools.create`/`schools.revise`审计同事务；403拒绝审计正常提交。存在学校或相关写入审计时拒绝降级。
- 正式页面支持登记、固定代码编辑、启停、筛选分页及详情历史；CAS冲突保留草稿并显式载入最新版，回执不明时锁字段并用同UUID/同请求体重试。权限撤销清理界面与草稿，迟到响应按页面代次隔离，390px布局有验证；关闭页面后不持久恢复不确定请求。
- 组织树、年级/班级由下方0023增量实现，校内学生名册与逐学年在读关系由0024增量实现；学校管理员数据范围、名册导入/逐行错误/回滚及家长关联仍待实现，同名学生不得自动合并。目录实施与验证见[学校目录记录](../plans/2026-10-26-school-directory.md)。

### 当前学校组织、年级与班级（0023）

**状态：**后端五接口及正式平台Web已实现，真实API/隔离PostgreSQL浏览器联调通过；FR-A09仍未整体验收。迁移`0023_school_structure`基于0022，未升级共享开发库。

- `school_structure_nodes`固定school_id、academic_year、code、kind，保存当前版本指针；`school_structure_revisions`记录不可变name/status/parent_id、revision、reason、actor_id、operation_id及请求快照。复合延迟外键绑定当前历史，禁止历史UPDATE/DELETE/TRUNCATE。唯一约束为同校同学年代码、同节点版本及同操作者操作UUID。
- 学校为隐式根；campus只能直属学校，department/grade可位于学校、campus或department下，class必须位于grade下。拒绝跨校/跨学年/自指/循环，移动后含后代最多八层；每校每学年最多2000节点。学年为2000–9998开始的相邻ASCII年份，历史学年可查询和维护。
- 自身status与学校/祖先计算的有效状态分开。停用上级不改写下级配置；停用学校拒绝新写入，允许读取及相同原请求重放。创建组织不授予学校管理员、学生或家庭内容权限。

统一前缀`/api/v1/admin/schools/{school_id}/structure`：

| 方法/路径 | 当前契约 |
|---|---|
| GET 空路径 | 必填academic_year；返回`{items,academic_year,school_status,school_revision}`完整快照，包含停用节点 |
| POST 空路径 | operation_id/code/kind/academic_year/name/status/parent_id/reason；parent_id必传，可为null；登记201，原请求重放200 |
| GET `/{node_id}` | 当前节点及版本元数据，按school_id绑定读取 |
| GET `/{node_id}/revisions` | 不可变历史，limit=1–100默认50，按时间/revision_id倒序游标分页 |
| POST `/{node_id}/revisions` | operation_id/expected_revision/name/status/parent_id/reason；追加201，重放200；固定代码、类型、学校、学年不可修改 |

- 复用`platform.schools.read/write`，security_admin可写、operator只读，实时校验独立平台会话。字段长度、代码规范与学校目录一致；expected_revision严格整数1–999999998。409包括operation_conflict、structure_code_conflict、structure_revision_conflict、school_disabled、structure_capacity；422包括invalid_structure_parent、structure_cycle、structure_depth。
- FORCE RLS且运行角色无表直接CRUD；仅API获得窄`school_structure_request`能力，固定安全search_path并撤销PUBLIC执行权。同校写锁防并发成环，账号锁保护幂等；历史、版本指针与`schools.structure.create/revise`审计同事务，审计失败回滚。存在组织数据或写审计时拒绝降级。
- 正式Web支持组织树、学年、详情/游标历史、登记、改名、移动及启停；409保留草稿并要求显式载入最新节点版本，回执不明锁定原请求重试，权限撤销/页面切换隔离迟到响应，390px布局可用。真实浏览器验证两个年级、一班、跨年级移动、上级停用和operator只读，并核对五条修订未覆盖历史。详见[实施记录](../plans/2026-10-26-school-structure.md)。

### 当前学校学生名册与学年在读关系（0024）

**状态：**后端五接口及正式平台Web已接通，真实API/隔离PostgreSQL浏览器联调通过；仅完成FR-A09的校内名册切片。迁移`0024_school_roster`基于0023，未升级共享开发库。

- `school_roster_students`以`(school_id, code)`唯一标识校内学生；同代码跨学年引用同一校内身份，同名不合并、跨校不共享身份。`school_enrollments`固定学生、学校与学年，每学生每学年最多一条；`school_enrollment_revisions`保存不可变display_name/class_id/status、revision、reason、actor_id、operation_id和请求快照。当前指针通过复合延迟外键绑定历史；历史禁止UPDATE、DELETE和TRUNCATE。
- 不收集证件/电话，不创建或自动关联家庭`students`、`student_access`。姓名仅为该学年快照。状态为`enrolled/paused/left/graduated`；在读必须关联同校同学年班级，且学校、班级及全部祖先启用。其余状态允许空班级或保留同校同学年历史班级。停用学校仍可读及重放完全相同请求，拒绝新登记和修订。
- 独立权限`platform.students.read/write`：security_admin读写，operator只读；每次调用重验实际平台会话和当前角色。这些权限不授予学校管理员范围或家庭内容访问权。

统一前缀`/api/v1/admin/schools/{school_id}/roster`：

| 方法/路径 | 当前契约 |
|---|---|
| GET 空路径 | 必填academic_year；q按代码/显示名进行不区分大小写的字面子串搜索；可筛status；limit=1–100默认50，按登记时间/id倒序游标分页 |
| POST 空路径 | operation_id/code/academic_year/display_name/class_id/status/reason；class_id必传，可为null；登记201，原请求重放200 |
| GET `/{enrollment_id}` | 返回固定身份与学年、当前配置及revision/revision_id/actor_id/reason/created_at/registered_at；绑定school_id |
| GET `/{enrollment_id}/revisions` | 不可变修订历史；limit和游标规则同列表，按修订时间/revision_id倒序 |
| POST `/{enrollment_id}/revisions` | operation_id/expected_revision/display_name/class_id/status/reason；修订201，重放200；固定学生代码、学校与学年不可编辑 |

- code去首尾空格并转大写，2–40字符，规范同学校代码；显示名1–80字、原因1–500字，去首尾空格且拒绝控制字符。学年为首年2000–9998的相邻ASCII年份；expected_revision严格整数1–999999998。UUID绑定操作者与原始操作快照，先锁账号再锁学校，同校名册与组织变更串行。
- 401无效会话，403无权限，404学校或在读记录不存在；409包括`operation_conflict`、`enrollment_exists`、`enrollment_revision_conflict`、`school_disabled`；422包括字段错误与`invalid_enrollment_class`。错误不回显请求或数据库细节。
- 三表FORCE RLS且运行角色无直接CRUD；仅通过固定安全search_path、撤销PUBLIC执行权的窄`school_roster_request`能力访问。登记/修订、不可变历史和`schools.roster.create/revise`审计同事务；审计失败全部回滚。存在名册数据或相关写入审计时拒绝降级。
- 正式平台学校详情支持按学年查询、搜索/状态筛选/分页、登记、详情、历史、调班及在读状态修订。版本冲突保留草稿并要求显式载入最新版；回执不明锁定原字段，使用同UUID/同请求体重试；撤权及页面切换隔离旧响应，支持390px布局。
- 批量导入/字段映射/逐行错误/回滚、学校管理员组织范围、监护人验证关联、跨校身份合并仍待开发；FR-A09、WZ-15/WZ-17不能整票关闭。实施与验证见[名册记录](../plans/2026-10-26-school-roster.md)。

### 当前教材版本与权利审核（0025）

对应 FR-A02、FR-A11 / WZ-11 的资料和权利审核前置流程。物理表为 `textbook_editions` 与不可变的 `textbook_edition_revisions`，历史以 `edition_id` 关联版本。两表 FORCE RLS；API 运行角色无表直接读写权限，仅可执行受限 `textbook_request`。目录登记、状态变更、历史和平台审计同事务提交。

| 接口（前缀 `/api/v1/admin/textbook-editions`） | 权限与行为 |
|---|---|
| GET 根路径、GET `/{edition_id}` | `platform.textbooks.read`；列表支持 q/state、limit 1–100、游标分页 |
| POST 根路径 | `platform.textbooks.write`；登记版本和可选权利草稿，返回201 |
| GET `/{edition_id}/revisions` | read；不可变完整快照，游标分页 |
| POST `/{edition_id}/revisions` | write；仅 draft/withdrawn，修订后回 draft |
| POST `/{edition_id}/submit` | write；draft → pending_review，必须有完整且未过期的权利 |
| POST `/{edition_id}/approve`、`/reject` | `platform.textbooks.review`；pending_review → approved/draft，审核人不得为创建者、当前编辑者或本次提交人 |
| POST `/{edition_id}/withdraw` | write；pending_review/approved → withdrawn |

- code为2–40位大写字母/数字/下划线/连字符，首位字母或数字，创建时规范为大写并唯一；标题允许重名。title/subject/publisher/edition_label各1–120字符。stage为primary/junior/senior，对应严格整数grade 1–6/7–9/10–12；academic_year为2000–9999内的连续双年份；volume为first/second/full。code固定，其余元数据通过追加历史修订。
- rights草稿可为null；非空时须完整包含basis（publisher_contract/open_license/public_domain）、holder/reference（各1–200字符）、scopes（storage/ocr/retrieval/preview中不重复1–4项，必含storage）、regions（不重复1–20个大写地区代码，如CN/CN-33）、valid_from/valid_until（真实YYYY-MM-DD，起日不晚于止日）。reference只是受控业务凭据引用，不上传文件。
- 写入均带operation_id UUID与reason 1–500字符；修订和状态操作另带expected_revision严格整数。先验证当前权限，再按actor+operation_id核对原请求重放，之后检查CAS/状态；键复用不同请求为409 operation_conflict，并发版本冲突为409 textbook_revision_conflict。不可自审返回409 independent_reviewer_required，授权不完整返回422 textbook_rights_incomplete。
- security_admin具备读/写/审三权限，operator只读；界面实时复核权限并清理撤权后的资料。草稿、待审、批准、撤回分别展示；授权可用性依据上海日期实时计算，未来授权为scheduled、过期为expired。0025批准只确认权利，其响应的`content_available`仍固定false；0028另以正文发布状态和当前检索权利判断正文可用性，不表示已接入AI检索。
- 正式平台 `TextbookCatalog.vue` 已接通目录/筛选/分页、资料及授权表单、独立审核、撤回、历史、CAS与原请求重试。PDF隔离接收由0026补充，持久扫描/结构检查/内嵌文本逐页草稿由0027补充（见下）；0028已补章节/物理源页映射、TXT校核、独立抽检及正文发布/撤回；OCR、原PDF预览和AI检索仍待开发，FR-A02/FR-A11未整票验收。

### 当前教材 PDF 隔离接收（0026）

物理表 `textbook_documents` 保存教材修订、上传人、操作UUID、原文件名、字节数、SHA-256、接收原因、到期/接收时间；状态仅 `awaiting_upload → quarantined`。表 FORCE RLS，API、worker、dispatcher角色无直接表权限，仅API可执行受限 `textbook_document_request`；接收凭证与 `platform_audit_events` 中的 `textbooks.documents.intent`、`textbooks.documents.received` 同事务写入。

| 接口（前缀 `/api/v1/admin/textbook-editions/{edition_id}`） | 权限与行为 |
|---|---|
| GET `/documents`、GET `/documents/{document_id}` | `platform.textbooks.read`；列表支持limit 1–100和游标，返回接收元数据 |
| POST `/documents` | `platform.textbooks.write`；operation_id、expected_revision、filename、size_bytes、sha256、reason；原请求重放返回原凭证，成功新建返回201 |
| PUT `/documents/{document_id}/content` | write且为原申请人；`application/pdf`原始字节流，15分钟内完成；成功只返回隔离元数据 |

- 仅当前已批准、上海日期内有效且含storage用途的教材可申请与完成上传。凭证绑定不可变权利修订，上传前和接收提交时分别复核会话、权限、申请人、权利、修订和到期；传输过程不长期持有授权行锁。撤权、撤回、修订、到期均阻止新接收；PUT已接收凭证重放仍需当前授权与绑定修订，但不再受15分钟接收期限限制。POST相同operation_id和相同参数重放，在会话和write权限检查后返回原凭证，不重新检查权利、修订或期限；返回历史凭证不代表允许继续接收。
- 文件名5–160字符且以.pdf结尾，禁止路径/控制字符和首尾空白；大小1字节至50 MiB，摘要为64位小写十六进制。流式检查声明长度、实际长度、SHA-256与`%PDF-`签名；单次流接收上限120秒。签名校验不代表结构有效或无病毒。
- 私有 `storage_root/textbook-quarantine` 目录0700，文件0600，以服务端UUID命名；硬链接无覆盖提交并同步文件与目录。数据库提交失败可留下不可读取的隔离孤儿，完整原字节重传时校验并恢复；不对外提供下载、预览、TXT或检索端点。
- 接收凭证的`scan_status=not_scanned`、`content_available=false`固定；实际扫描证据另见0027处理任务，不通过接收凭证推断扫描结果。前端提供摘要计算、申请/上传、原操作重试、刷新后选择同名/同大小/同摘要原文件恢复、记录分页与隔离凭证。失去权限清空所选字节，迟到响应不得恢复已清理的上传。
- `409 upload_expired`、`document_rights_unavailable`、`textbook_revision_conflict`、`operation_conflict`用于重申请或刷新；`413 pdf_too_large`、`422`类型/大小/摘要/签名拒绝，`408 upload_timeout`及`503 storage_unavailable`允许复用原凭证重试。存在文件记录或相关审计时拒绝降级0026。
- 接收接口不执行病毒扫描或解析；0027另行提供持久处理任务。OCR、章节与源页映射、TXT校核、抽检、发布与检索仍待实现。接收验证记录见[0026计划](../plans/2026-10-26-textbook-pdf-upload.md)。

### 当前教材持久处理与逐页草稿（0027）

新增一张物理表 `textbook_processing_jobs`，绑定文档、教材不可变权利修订、申请人、原平台会话及操作UUID。任务保存原因、状态、领取次数、租约及结果JSONB；扫描证据与逐页正文草稿存于结果，不另建草稿表。`(created_by, operation_id)`唯一，同键同体返回原任务、异体409；部分唯一索引限制每文档至多一个queued/running任务。

| 接口（前缀 `/api/v1/admin/textbook-editions/{edition_id}/documents/{document_id}`） | 权限与行为 |
|---|---|
| POST `/processing-jobs` | `platform.textbooks.write`；operation_id、expected_revision、reason；新建201、同体重放200；须已隔离接收且当前修订授权含storage/ocr |
| GET `/processing-jobs` | `platform.textbooks.read`；limit 1–100与游标分页，仅元数据，不返回正文 |
| GET `/processing-jobs/{job_id}` | read；状态、扫描证据与草稿页数等摘要，不返回正文 |
| GET `/processing-jobs/{job_id}/pages/{page_number}` | read；另复核当前修订、有效权利及preview用途，按1–500页码读取待校核正文 |

- FORCE RLS且运行角色无直接表权限；API仅获`textbook_processing_request`执行能力，受限worker仅获`textbook_processing_worker`执行能力，辅助函数对PUBLIC不可执行。申请与`textbooks.processing.request`、完成与`textbooks.processing.finished`分别在同事务保存。
- 状态为`queued → running → draft_ready | no_text | rejected | unavailable | blocked`。worker采用180秒租约、随机令牌和最多三次领取，过期租约可回收，旧令牌不能提交；执行前和完成时均复核原申请人/会话、写权限、教材/文件修订及storage/ocr用途，撤回或失权阻止继续处理/保存草稿。文件处理期间不持有数据库连接或事务。
- 独立worker从私有隔离路径检查大小和摘要，经显式配置的ClamAV服务扫描，再以资源受限子进程检查PDF结构/页数和提取内嵌文本；最多500页、每页100000字符、合计1000000字符。草稿保留页号及空文本页，始终`requires_review=true`、`ocr_performed=false`、`content_available=false`。
- 扫描前后检查病毒库日期与版本一致性，项目策略为库龄≤72小时、未来容差≤5分钟；无效日期、夏令时歧义/不存在、过期或超前均拒绝。VERSION原日期按`WIZZY_CLAMAV_DATABASE_TIMEZONE`（默认UTC，须匹配clamd）解释，扫描证据`database_date`保存带`+00:00`的UTC ISO日期时间；`checked_at`为UTC扫描时间。此为结果JSONB值语义细化，不增加表或字段；无时区ctime原文不再作为新扫描证据输出。
- 正式平台支持显式申请、丢回执原键重试、历史分页/刷新、扫描证据、逐页草稿与失权清理。病毒库时效代码及协议边界已测试；真实ClamAV、自动更新/告警及Linux资源隔离部署尚待验收。0027草稿本身不可用于AI/检索；校核、抽检及TXT发布由下节0028接续实现，OCR与AI检索仍待开发。
- 使用方法见[本地开发](../local-development.md)，验证记录见[0027实施计划](../plans/2026-10-26-textbook-processing-jobs.md)。

### 当前教材正文校核、独立抽检与发布（0028）

物理表`textbook_contents`保存绑定edition/权利修订/document/job的当前内容头，`textbook_content_revisions`逐次追加不可变正文、状态、操作者、原因、原请求与抽检页。两表FORCE RLS，三个运行角色均无直接CRUD；仅API可执行受限`textbook_content_request`，worker和dispatcher不可调用。正文操作与`textbooks.content.*`审计同事务提交；有正文或该审计记录时拒绝降级，避免丢失历史。

| 接口（前缀 `/api/v1/admin/textbook-editions/{edition_id}`） | 权限与行为 |
|---|---|
| POST `/processing-jobs/{job_id}/content` | write；仅draft_ready来源，每任务一份校核内容，复制源页且verified=false，章节为空 |
| GET `/contents`、GET `/contents/{content_id}` | read；列表支持limit 1–100/游标；详情返回状态、贡献者、必抽页等元数据 |
| GET `/contents/{content_id}/history` | read；before修订号/limit分页，返回不可变历史元数据 |
| GET `/contents/{content_id}/revisions/{revision}/body`、GET `/contents/{content_id}/revisions/{revision}/pages/{page_number}` | read且当前权利有效；历史正文或单页读取，绑定来源权利修订 |
| POST `/contents/{content_id}/revisions` | write；draft/withdrawn可修订，追加正文后回draft |
| POST `/contents/{content_id}/submit` | write；draft→pending_review；全部页面已确认、整本至少含一个非空白字符（按Unicode规则识别）、章节覆盖全部页 |
| POST `/contents/{content_id}/approve`、POST `/contents/{content_id}/reject` | review；pending_review→published/draft，拒绝所有创建/修订/提交贡献者自审；批准另带sample_pages |
| POST `/contents/{content_id}/withdraw` | write；撤回待审/已发布内容，权利失效后仍可撤回 |
| GET `/contents/{content_id}/txt` | read、当前权利有效且正文published/retrieval可用；JSON返回content_id/revision/text/chapters，前端生成TXT下载 |

- 所有写入带operation_id UUID、expected_revision及reason 1–500字符；创建时expected_revision对应教材权利版本，其余对应内容版本。重试冻结原请求，UUID不同请求冲突、CAS冲突均返回409；实时权限优先于幂等回放。
- body.pages为1–500个连续PDF物理页（从1开始），每页text最多10万字符、合计100万字符，verified为严格布尔；拒绝NUL和孤立代理字符。body.chapters最多500项，code唯一、title 1–200字符，父章节先列出且子范围在父范围内；提交时章节联合覆盖全部源页。空白源页允许人工确认，但整本不能只有空白字符。
- 批准抽检须覆盖首末页、来源提取为空的页以及所有与来源文字不同的页，sample_pages不重复且在有效页数内。审核人不得参与该内容任何创建、修订或提交。同教材版本已有发布内容时须先撤回，数据库串行约束发布决策。
- 正文/历史正文/TXT均复核当前权利修订及有效storage/ocr/preview用途；发布和TXT另需retrieval用途。`content_available`仅表示当前发布正文满足检索授权，不代表存在AI索引。TXT以`[PDF page N]`保留物理页边界，章节另随JSON返回。
- 正式平台`TextbookContent.vue`已接通校核/审核/发布/历史，未保存改动需显式放弃；失权清理正文，迟到响应不能恢复旧正文，同一操作丢回执可按原请求重试。人工“已核对”和抽检勾选是人工声明；尚无原PDF并排预览、图片OCR或AI索引/检索。验证边界见[0028实施记录](../plans/2026-10-26-textbook-content.md)。

### 当前周期学习报告（0016）

**状态：**周/月报告后端及正式家庭页面已接通；不等于WZ-13整体验收。监护人可生成、分页列出、读取、下钻来源、显式重建，儿童不可直接访问。

- `reports`保存不可变汇总、`source_version`（SHA-256来源指纹）、同周期递增revision及rules_version=1；`report_sources`保存original/correction/review逐项来源、题目/提交ID、时间与证据快照。两表ENABLE/FORCE RLS，运行角色无直接CRUD；仅API可执行窄化、固定search_path且PUBLIC撤权的SQL能力。
- 周期为上海时间周一/月首起始、左闭右开，年份2000–9998。原题按提交创建时间入期，订正/复习按尝试创建时间入期；单周期最多10000条来源，超出返回422 `report_too_large`，不静默截断。
- 原始正确率分母仅有效correct+incorrect原题；pending_review/unanswered/unsupported单列，空分母为null。订正正确率另用已核对correct+incorrect尝试计算，不改原始分母。当前复习提交仅awaiting_review，完成数为0；薄弱点返回`status=insufficient_evidence, items=[]`，不得将求助、跳过、提交或人工核对当作掌握。guardian来源保留，不是模型评分金标准。
- 读取比较当前来源指纹并显示stale，不改旧快照；显式重建保留历史，相同来源复用已有版本。唯一约束绑定学生、删除代次、周期及来源；并发生成不重复，只有新版本生成/重建才在同事务写审计。列表/来源limit默认20、范围1–100，按created_at+id游标分页。
- 家庭成员、逐学生授权、同意与删除代次在SQL能力层实时检查并加锁；撤回/删除后不能读取或重建旧证据。有报告历史禁止降级0016。正式家庭UI支持生成/筛选分页/来源下钻/显式重建；后台自动刷新、报告导出、独立复习评估及知识点推断仍未实现。

### 当前共享周/月复习计划（0017、0033）

**状态：**有界共享计划后端、正式家庭报告生成及学习页历史入口已接通，FR-P04/FR-C07与WZ-09/WZ-13仍未整体验收。`review_plans`与`review_plan_entries`保存不可变集合，引用0013同一个唯一review_task；不复制尝试或另算完成次数。

- `POST /api/v1/review-plans`要求有权监护人和UUID `Idempotency-Key`；输入为student_id、type=weekly/monthly、对齐的period_start，可选report_id及三个时长/题量参数。max_questions默认20（1–100），max_minutes默认20（1–120），minutes_per_question默认2（1–30），因此默认实际最多10题。预计时长明确标为`duration_basis=planning_estimate`，不是测量耗时。
- 仅选本学生当前版本、open、due_date早于周期结束（包含逾期）的任务。可从当前未过期报告中的监护人确认错误原题补建缺失任务。逾期优先，其次有重复错误证据，再其余候选；同组按当前版本已核对错误订正次数降序、到期日、题目ID排序。不虚构相似题聚类、掌握度或客观评测结果。
- 幂等键绑定学生、删除代次和全部输入；相同请求复用原计划，变更输入409 `idempotency_conflict`，过期报告409 `source_conflict`。空计划合法，不能填入虚构任务。
- `GET /api/v1/review-plans/{id}`允许有权监护人/绑定儿童读取，条目包含任务实时状态；题文/有效判定/改期/报告来源变化显示stale。仍只有D+1任务，submitted表示待核对，不算完成/掌握；不能将旧条目当新任务绕过任务自身版本与授权检查。
- 0033新增`GET /api/v1/review-plans`：必填student_id，可选type=weekly/monthly、limit（默认20，1–100）及cursor，按created_at/id倒序分页。摘要仅含周期、创建时间、题量、估算时长与来源报告ID，不含题干/草稿；详情仍按当前授权读取，列表不声明实时stale。当前学生授权、同意与删除代次失效立即隐藏旧计划。
- 正式家庭页面支持从报告生成计划、相同输入/UUID的失败重试；学习页支持周/月/全部历史筛选、分页、刷新、空态、重新打开详情及共享任务入口。家长与配对儿童均可使用；切学生/退出/组件卸载隔离迟到响应，详情刷新失败清除旧题文，403/404同时清除历史。儿童不可读取监护人报告。
- 两表ENABLE/FORCE RLS；运行角色无直接表权限；仅API可执行`generate_review_plan(uuid,text,date,integer,integer,integer,uuid,uuid)`、`read_review_plan(uuid)`和0033的`read_review_plans(uuid,text,integer,timestamptz,uuid)`，内部辅助函数不对运行角色开放。全部固定search_path、撤销PUBLIC，worker/dispatcher不可执行。有任意计划（含空计划）即拒绝降级0017；0033降级仅移除列表能力，保留计划与审计。

实施及隔离库证据见[报告计划](../plans/2026-10-25-learning-reports.md)、[共享计划](../plans/2026-10-26-shared-review-plans.md)、[历史列表](../plans/2026-10-26-review-plan-history.md)与[台账](../plans/14-development-status.md)。代码head不代表共享开发库已升级。

### 当前模型目录（0018）

**状态：**FR-A12目录后端及正式平台Web已实现；FR-A13已由0030实现固定版本离线预测统计，真实提供方质量尚未验收；FR-A14生产准入/双人发布仍未实现，不能将目录启用作为自动判题许可。

- `model_catalog`固定供应商、模型标识、固定版本、区域，四元组唯一且创建后不可改；换版本建新条目。拒绝latest/auto/default/stable/current/preview等浮动版本片段。`model_catalog_revisions`保存递增revision、不可变配置/原因/操作者/操作UUID/请求快照；触发器拒绝更新或删除修订，延迟外键绑定当前修订。
- 两表ENABLE/FORCE RLS，API/worker/dispatcher无直接CRUD。仅API可执行`model_catalog_request`能力，内部辅助函数PUBLIC撤权、固定search_path。平台会话、账号状态/凭据版本及权限每次实时检查；security_admin有`platform.models.read/write`，operator仅read，家庭令牌禁止使用。
- 元数据含display_name、purposes（ocr/structure/verify/tutor/report）、contract_status、contract_reference、contract_expires_on、input/output_price_micros、enabled。价格为0至9007199254740991的整数微元或null，不能用浮点或未知价格0填充。approved合同必须同时有引用和到期日；启用还须当前合同有效及密钥引用。
- `secret_ref`只接受`secret://providers/...`服务引用，不接受明文key或URL。任何读取/写回响应/历史都移除该引用，仅返回credential_configured；修订省略表示保留，显式null表示清除，清除须停用。目录不解析引用、不调用供应商。
- enabled为保存的配置。catalog_status为服务端按上海当前日期投影的disabled/contract_expired/contract_unapproved/credential_missing/available；合同到期日当天仍有效。合同后续过期不篡改历史配置或自动新建修订。所有接口的created_at/registered_at统一日期时间序列化，避免SQL JSON省略微秒末尾零导致列表/详情/历史不一致。
- 每次创建/修订需要UUID operation_id和reason；修订还需expected_revision CAS。相同操作者/UUID、相同请求重放原修订，异请求或旧版本返回409；目录/修订与最小审计同事务，审计失败整体回滚。没有删除接口；有目录或models.create/models.revise审计历史即拒绝降级0018。
- 投影明确返回`accuracy=null`、`evaluation_status=not_evaluated`、`production_eligible=false`。家长改判、订正核对或成功页比例不构成独立金标准，目录available也不授予生产资格。

以下路径均在`/api/v1/admin/models`下，响应仍用统一data/request_id包装：

| 方法/路径 | 约束 |
|---|---|
| GET 空路径 | q最长120字符、可选enabled；limit默认50、1–100；created_at/id倒序游标，返回items/next_cursor |
| POST 空路径 | 登记固定身份及第一修订，成功201；需write权限 |
| GET `/{model_id}` | 读取当前修订，密钥引用不回显 |
| POST `/{model_id}/revisions` | 不可改固定身份；幂等/CAS修订及启停，成功201 |
| GET `/{model_id}/revisions` | 不可变修订历史，同一分页契约；历史可用性仍按读取时合同日期投影 |

401为无效平台会话，403权限拒绝且保留access.denied审计，404为目录不存在，409为身份/操作键/版本冲突，422为字段/合同/游标无效。正式Web在409保留草稿并要求显式加载最新修订；不确定网络重试复用相同操作UUID；401、退出或write权限撤销清除敏感草稿。

实现与证据见[模型目录计划](../plans/2026-10-26-model-catalog.md)、[工程台账](../plans/14-development-status.md)。模型目录迁移为0018，当前工程迁移版本见台账；不代表共享开发库已升级。

### 当前逐学生模型评测授权（0020）

**状态：**已实现独立授权记录及 API，作为评测样本采集的前置条件；0029已接入数据集、授权使用时复核及独立双标/裁决/锁定。0030已接入固定版本离线运行与统计报告，家庭正式页面已接通独立评测授权/撤回；自动脱敏、物理删除与真实提供方质量验收仍未完成。

| 方法/路径 | 请求与行为 |
|---|---|
| GET `/api/v1/evaluation-policy` | 无需登录；返回固定政策 `evaluation-2026-10-26-v1`、可选用途和用途限制 |
| GET `/api/v1/students/{student_id}/evaluation-consent` | 当前有权监护人读取 revision、granted、active、authorization_id、last_decision、reason、policy_version；初始 revision=0、active=false |
| POST `/api/v1/students/{student_id}/evaluation-consent` | UUID operation_id、严格整数 expected_revision、严格布尔 granted、固定 policy_version；新决定201，相同请求重试200，操作键异请求或旧版本409 |

- 本政策只授权经脱敏和隐私检查后的内部离线批改评测，不包含训练、公开分享、向提供方传输；未来这些用途须另行授权。服务同意不会自动授予评测权限；评测授权前须已有服务同意，撤回评测后服务及儿童会话仍可继续。
- `evaluation_consent_records`保存不可变决定，绑定真实监护人、实际认证会话、学生/家庭 deletion_epoch、政策、请求快照和递增版本；`evaluation_consent_states`用复合外键指向该学生当前决定。历史禁止 UPDATE/DELETE/TRUNCATE；写入、当前指针与最小审计同事务。
- 两表 FORCE RLS，应用、worker、dispatcher 无直接 CRUD 权限；只有应用可执行主能力函数。该函数验证实际令牌哈希与当前权限，不信任调用方身份 GUC；内部辅助函数撤销 PUBLIC 与运行角色执行权，固定 search_path。
- active 依据当前决定及实时家庭/学生状态、服务同意、来源代次、监护人账号/成员/逐学生权限计算；旧授权不能因重新同意服务或重新授权评测而复活。登录会话到期或主动退出不撤销已记录的持久决定。拒绝/撤回请求在服务同意撤回后仍可提交。
- 401无效会话；403儿童或不满足授权前提；404无权学生；409版本/幂等冲突；422字段或政策无效。记录、当前指针或评测授权审计存在时拒绝降级；空库支持回退0019再升级。
- 后续采集/运行必须在样本使用事务中重新校验授权并与撤回协调；当前授权接口本身不会导入样本、外呼模型或生成准确率。

### 当前离线评测数据集（0029）

物理表 `evaluation_datasets`（name/split/handbook_version/revision/state/锁定摘要）、`evaluation_samples`（源题revision/摘要、具体authorization、学生/页/年级、近重复组、脱敏正文）、`evaluation_annotations`（sample/actor唯一、独立标签）、`evaluation_adjudications`（第三人标签与原因）、`evaluation_dataset_operations`（actor/operation UUID唯一、原请求快照）。全部 FORCE RLS，运行角色无直接CRUD；只通过受限 `evaluation_dataset_request` 能力访问。标注/裁决/操作历史及锁定数据禁止覆盖、删除或清表；存在历史时阻止降级。

接口根 `/api/v1/admin/evaluation-datasets`：

| 方法/路径 | 约束与行为 |
|---|---|
| GET 空路径 | 列表，limit默认50、最大100，offset分页 |
| POST 空路径 | 名称≤120、train/validation/test、handbook_version；expected_revision=0 |
| GET `/{dataset_id}` 或 `/{dataset_id}/samples` | 详情/样本，相同分页；统计及正文按实时授权投影 |
| POST `/{dataset_id}/samples` | question_id/source_revision/authorization_id/near_duplicate_group/脱敏题干答案/脱敏说明 |
| POST `/{dataset_id}/samples/{sample_id}/annotations` | 当前操作者独立标签，不泄露另一人标签；每题两人 |
| POST `/{dataset_id}/samples/{sample_id}/adjudications` | 两份分歧标签由独立第三人裁决，reason≤1000 |
| POST `/{dataset_id}/lock` | 全部有效授权、双标/必要裁决、人工脱敏安全确认后锁定SHA-256摘要，不可逆 |

所有写操作携带UUID operation_id和expected_revision；相同原请求重试200、新写201、旧版本或操作键异请求409。401会话无效，403权限不足，404目标/授权来源不可用，422字段无效；写入与审计同事务。标签含题干/学生答/正确答、五种判定、题型/难度、requires_abstention/redaction_safe与0..10000整数有效矩形；两人的完整标签一致或第三人裁决才构成金标准。

`platform.evaluation.read/curate/annotate/adjudicate`分权；security_admin具全部权限，evaluation_annotator只读与独立标注，evaluation_adjudicator只读与裁决，operator无评测权限。来源使用与读取事务协调授权撤回；失效后正文、own_label/gold_label/disagreements隐藏，重新授权不复活旧样本。按学生、页面、近重复组、归一题干阻断跨split泄漏；train不表示训练授权。无自动PII检测，人工确认不代表自动脱敏；撤回隔离不代表已物理删除。

正式平台已接通此切片并完成模拟浏览器及真实API/隔离PostgreSQL联调；双标一致率不是模型正确率，production_eligible固定false。固定版本运行、完整离线预测导入及统计报告API/UI已由下述0030补齐；发布治理与真实提供方质量仍未验收。验证范围见[评测计划](../plans/2026-10-26-model-evaluation.md)，迁移head不表示共享开发库已升级。

### 当前固定版本离线评测运行（0030）

物理表 `evaluation_runs` 保存dataset/model外键、历史模型revision、固定manifest/摘要、状态/修订、操作者声明的source_reference与时间；`evaluation_run_predictions` 以run/sample为主键保存五种verdict和automatic_release；`evaluation_run_operations` 以actor/operation UUID为主键保存原请求与回执。三表全部FORCE RLS，运行角色无直接CRUD/TRUNCATE；只通过 `public.evaluation_run_request(text,text,text,jsonb,uuid,integer,integer)` 受限SECURITY DEFINER能力函数访问，固定search_path。完成记录、预测和幂等历史不可覆盖、删除或清表；存在历史时拒绝降级。

接口根 `/api/v1/admin/evaluation-runs`：

| 方法/路径 | 约束与行为 |
|---|---|
| GET 空路径 | 默认limit50、最大100，offset≤1000000；返回固定manifest、摘要、状态、修订、预测数及当前usable |
| POST 空路径 | dataset_id/dataset_revision、model_id/model_revision、prompt_hash、schema_version、rule_version、image_pipeline_version、required_strata；expected_revision=0 |
| GET `/{run_id}` | 读取固定运行；manifest记录数据集locked_digest及provider/model/version/region，不返回题干或学生身份 |
| POST `/{run_id}/predictions` | UUID operation_id、expected_revision=1、manifest_digest、source_reference、完整predictions数组；一次完成运行 |
| GET `/{run_id}/report` | 当前授权下服务器计算六指标、单侧区间/簇区间、总体/年级/题型/难度/年级×题型报告；不接受客户端分数 |

- 仅接受已锁定、非空且当前来源授权有效的validation/test集；固定具体数据集修订/摘要与模型历史修订，required_strata显式列出1–100个不重复年级×题型。prompt_hash为小写SHA-256。列表权限platform.evaluation.read；创建/导入权限platform.evaluation.run仅security_admin具有。
- predictions最多50000项，question_id必须是sample UUID，集合精确覆盖样本；缺失/额外/重复、旧版本或manifest不符返回409。verdict为correct/incorrect/unanswered/pending_review/unsupported，automatic_release为严格布尔，后两种不允许自动放行。运行只允许awaiting_predictions/revision1 → completed/revision2。
- 新写201、原body/operation UUID重试200、异请求复用键409；401无效会话、403权限不足、404运行不存在、422字段无效。CAS、幂等回执和审计同事务；HTTP及SQL双层严格校验。读/导入/报告和幂等重试实时复核具体授权及源修订，协调撤回，重新授权不复活旧样本。
- 报告包含有效分母、空分母null、分层缺样本及安全违规；统计期间持有授权协调锁。正式平台接通列表、创建、JSON文件/文本导入和报告，并验证390px宽度。网络/5xx原请求冻结重试，409核对，失权/离页清理及迟到响应隔离，不写浏览器持久存储。
- source_reference仅为操作人声明，没有真实提供方调用或执行真实性核验；production_eligible始终false。真实授权金标准、自动PII脱敏、撤回物理清理、真实提供方质量和发布治理仍待完成。0030切片的迁移不表示共享开发库已升级；验证见[实施记录](../plans/2026-10-26-model-evaluation.md)。

## 3. PostgreSQL 约束与 RLS 示例

以下为可独立理解的**最小模式片段**，不是可直接全量执行的迁移脚本。实际迁移以 `backend/migrations` 为准；已在隔离 PostgreSQL 库运行身份、图片与提交/额度部分的 RLS/事务测试，其余逻辑模型尚未建表，见 [14](../plans/14-development-status.md)。生产迁移用受控 owner；运行应用使用另一个无 BYPASSRLS、无表所有权、无建表权限的角色。

```sql
CREATE TABLE students (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    nickname text NOT NULL,
    UNIQUE (tenant_id, id)
);
CREATE TABLE submissions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    student_id uuid NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    status text NOT NULL,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, student_id) REFERENCES students (tenant_id, id)
);
ALTER TABLE students ENABLE ROW LEVEL SECURITY;
ALTER TABLE students FORCE ROW LEVEL SECURITY;
ALTER TABLE submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE submissions FORCE ROW LEVEL SECURITY;
CREATE POLICY students_tenant_policy ON students
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
CREATE POLICY submissions_tenant_policy ON submissions
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```

服务端通过事务内参数绑定执行 `SELECT set_config('app.tenant_id', :trusted_tenant_id, true)`，最后参数 true 表示事务局部。上下文必须来自已授权成员关系，不能将请求体直接拼 SQL。未设置上下文应读不到家庭行，写入失败；非法 UUID 应失败而非回退到全局权限。

**RLS 不是完整授权系统**：此例只隔离家庭，儿童A与儿童B同租户时还要应用授权限制。授权完成后才能生成签名URL/发队列/导出；不是查出所有行后在前端隐藏。平台元数据使用独立角色；跨家庭删除维护由受限独立作业执行，并逐租户设上下文，不能给 Web 应用 superuser。[S16](../research/sources.md)

## 4. 状态机与不变量

### 提交/任务

- 提交：`draft → queued → processing → completed | partial | failed | cancelled`。
- 异步任务：`queued → running → succeeded | retry_wait | failed | cancelled`；retry_wait 回 queued。deadletter是失败队列处置属性。
- `completed`只表示处理结束，题目仍可 pending/unsupported；partial表示至少一页失败。不会用任务 succeeded 表示“全题正确”。
- 每题有效结论：`correct | incorrect | pending_review | unanswered | unsupported`；修改通过新 verdict_revision，不覆盖。
- 人工改判事务：锁题/校验expected_revision→新历史→更新有效指针和revision→outbox。旧自动任务 CAS 失败不覆盖人工结论。
- 学生订正生成 correction_attempt，不改变原始作业历史；错题掌握依据独立练习，不看打开次数。
- 错题：`new → practicing → mastered → archived`；掌握后再错回 practicing。复习算法见 [03](../03-software-requirements.md)，归档不删证据。
- 教材：`draft → pending_review → approved → expired | withdrawn`；公开展示只允许approved且未到期的授权范围。
- 删除：`requested → blocked_for_processing → deleting_live → waiting_backup_expiry → completed`；撤回训练/营销等不同目的同意不必等同删除所有服务数据，需按授权范围解释。

### 幂等与并发

同一幂等键+同请求体返回原资源；同键不同体返回409。范围为 `(tenant,actor,endpoint,key)`，请求哈希规范化；初始保存至少24小时，已建业务对象保持永久唯一操作键。网络重试不能创建重复提交/复习/计费；相同图片的新练习不能仅凭hash误合并，必须同提交版本同操作。

## 5. REST API 分组

统一 `/api/v1`；客户端不能自选“管理员模式”。响应只返回其可见字段，不泄露内部标准答案/供应商提示词。儿童基础提示接口不返回完整答案，需按提示级别重新授权。

| 方法与路径 | 调用者 | 用途/验收 |
|---|---|---|
| POST `/admin/auth/login`；GET `/admin/auth/captcha`、`/admin/auth/me`；POST `/admin/auth/logout`、`/admin/auth/logout-all` | 独立平台身份 | 0032为密码+图形验证码、固定30分钟会话及本人撤销；不授予家庭内容权限 |
| GET `/admin/roles`、`/admin/accounts`、`/admin/audit-events` | 已登录平台账号；账号/审计另验权限 | 0015只读RBAC基础，实时授权、最小投影及同事务审计；0021另增角色/启停与独立升权审批，邀请/MFA管理仍待做 |
| POST `/auth/wechat-login`、`/auth/sms-login` | 监护人 | 服务端换凭证；验证码限流及防枚举；不把openid当通用登录token |
| GET `/families`；POST `/families`；POST `/families/{id}/invites` | 监护人 | 家庭创建/邀请，切换家庭重验成员关系 |
| POST `/students`；GET `/students/{id}` | 授权监护人 | 学生档案；儿童仅可读自己的安全字段 |
| POST `/students/{id}/pairing-codes`；POST `/child-sessions`；DELETE `/child-sessions/{id}` | 监护人/配对设备 | 一次性配对/撤销；码不能枚举儿童信息 |
| POST `/media/upload-intents`；POST `/media/{id}/complete`；GET `/media/{id}/view-url` | 有权用户 | 上传校验/签名访问；配额与类型限制 |
| POST `/submissions`；GET `/submissions`；GET `/submissions/{id}` | 有权用户 | 异步返回202+job；分页列表与证据 |
| GET `/submissions/{id}/result` | 对应学生/有权监护人 | 已实现保守只读投影；200完整/部分/全失败，未就绪409，不提供自动正确率结论 |
| GET `/jobs/{id}`；POST `/jobs/{id}/cancel` | 有权用户 | 轮询阶段/错误/可重试；不可越权ID探测 |
| GET `/submissions/{id}/questions`；GET `/questions/{id}` | 对应学生/有权监护人 | 已实现稳定题目 ID、当前 OCR 与不可变历史 |
| POST `/questions/{id}/ocr-corrections` | 监护人 | 已实现 expected_revision CAS 更正，不自动重判；儿童修正申请另待实现 |
| POST `/questions/{id}/verdict-overrides` | 监护人 | 已实现题文/判定双 CAS，追加人工历史、有效版本与私有事件，同事务 API 审计；保留机器证据；0011 已接错题派生消费；0016报告读取标stale并可显式重建 |
| POST/GET `/questions/{id}/correction-attempts` | 对应学生/有权监护人 | 已实现文字/重拍引用、源版本绑定、操作键幂等与历史分页；新尝试待人工核对，不自动掌握 |
| POST `/questions/{id}/correction-attempts/{attempt_id}/judgments` | 有权监护人 | 已实现逐次人工核对历史、judgment_revision CAS与幂等；原题版本变化拒绝新核对，不改原判 |
| POST `/questions/{id}/hints` | 对应学生/监护人 | 有限提示级别；P1会话单独限额 |
| GET `/students/{id}/mistakes` | 对应学生/有权监护人 | 已实现当前确认错误列表、游标分页与 pending_updates；不含业务筛选/收藏/掌握 |
| GET `/mistakes`；PATCH `/mistakes/{id}` | 有权用户 | 规划：条件过滤/归档，批量操作逐条授权，尚未实现 |
| POST `/questions/{id}/review-tasks`；GET `/students/{id}/review-tasks` | 监护人建任务；对应学生/有权监护人读 | 0013已实现当前错误的首步D+1任务、到期筛选与稳定分页 |
| GET `/review-tasks/{id}`；PATCH `/review-tasks/{id}/draft`；POST `/review-tasks/{id}/attempts` | 授权读；仅对应儿童写 | 0013文字草稿CAS、提交/求助/跳过、UUID重放；提交仅待核对，不算掌握 |
| POST `/review-tasks/{id}/reschedules` | 有权监护人 | 0013改期CAS/UUID幂等及追加历史；0017另提供周月共享计划，后续步数仍待实现 |
| POST `/review-plans`；GET `/review-plans`、`/review-plans/{id}` | 监护人生成；有权监护人/绑定儿童读 | 0017有界周/月共享集合、UUID幂等与详情实时状态；0033按学生/周期筛选、游标分页的摘要历史 |
| POST `/exports` | 监护人 | P1打印异步任务，待实现 |
| POST/GET `/students/{id}/terms` | 有权监护人创建；对应学生/有权监护人读 | 0014学期创建与游标列表；固定上海时区 |
| GET/PUT `/timetables/{term_id}`；GET `/timetables/{term_id}/instances`；POST `/lesson-overrides` | 有权监护人写；对应学生/有权监护人读 | 0014周模板、单双周实例、单次调停课/恢复；共用expected_revision及冲突确认 |
| PUT `/students/{id}/curriculum`；GET `/previews` | 监护人/学生受限读 | 教材/进度确认与授权预习卡 |
| POST/GET `/reports`；GET `/reports/{id}`；GET `/reports/{id}/sources`；POST `/reports/{id}/rebuild` | 仅有权监护人 | 0016不可变周/月统计、来源分页下钻、实时stale与显式重建；不是后台自动刷新 |
| GET `/quota`；POST `/privacy/exports`；POST `/privacy/deletions` | 监护人 | 自身额度及导出/删除；敏感操作二次验证 |
| GET `/admin/tenants`；PATCH `/admin/tenants/{id}/status` | 平台运营 | 不返回题文/图片 |
| GET/POST `/admin/textbook-editions`；详情/修订/submit/approve/reject/withdraw | 教材读/写/审核权限 | 0025已实现资料及权利审核（见上）；不自建自审，权利批准与0028正文发布独立 |
| GET/POST `/admin/textbook-editions/{id}/documents`；GET详情；PUT内容 | 教材读/写权限，接收限原申请人 | 0026已实现≤50 MiB PDF隔离接收、摘要与凭证重试；接收不执行扫描，处理证据另见0027 |
| GET/POST `/admin/textbook-editions/{id}/documents/{document_id}/processing-jobs`；GET任务详情及逐页草稿 | 教材读/写权限；正文另核当前修订和preview授权 | 0027持久任务、扫描证据及内嵌文本草稿；校核/发布另见0028，未OCR |
| POST `/admin/textbook-editions/{id}/processing-jobs/{job_id}/content`；GET `/contents`、详情/历史/修订正文/TXT；POST修订/submit/approve/reject/withdraw | 教材read/write/review；正文另核当前权利 | 0028逐页校核、章节/物理源页、独立抽检、发布TXT与撤回；AI检索未接入 |
| POST `/admin/config-releases`；POST `/admin/config-releases/{id}/approve` | 模型管理员/审核员 | 关联评测产物和灰度；不能跳过回归 |
| GET `/admin/jobs`；POST `/admin/jobs/{id}/retry` | 授权运维 | 状态/重试；不默认有内容查看 |
| POST `/admin/access-tickets` | 支持人员 | break-glass审批后短时最小内容读取，访问全审计 |

P0 先实现表内核心读写；P1接口可保留设计但不返回虚构功能。Webhook 仅服务端供应商验签受众，防重放，不开放任意 URL 回调造成 SSRF。

## 6. 请求响应样例

`POST /api/v1/submissions` 带 `Idempotency-Key`；家庭由已验证会话上下文确定，学生必须在其授权范围。禁止跨家庭混入media。

```json
{
  "student_id": "8ed465eb-ed04-4b49-bad7-6c0fd3d96208",
  "subject": "math",
  "completed_attempt": true,
  "media_ids": ["f42283ca-f75e-4b89-8604-eed46cdf0246"],
  "client_request_id": "0811af4f-09f7-4977-997d-8b60d4b0bbc9"
}
```

202示例：

```json
{
  "data": {
    "submission_id": "5ea8f34c-5c97-4309-955f-66ca5f86ce59",
    "source_revision": 1,
    "status": "queued",
    "job_id": "46a5a548-feec-4692-83c2-b0edf633560c",
    "poll_after_seconds": 3
  },
  "request_id": "req_example_001"
}
```

错误统一 `error.code/message/field_errors/retryable` + `request_id`；401需重新登录，403无权限，跨家庭资源可统一404防枚举，409版本/幂等冲突，413过大，422结构无效，429限流，503供应商暂不可用。响应不含堆栈。分页 `cursor/limit≤100`，服务端按created_at+id稳定排序；过滤字段白名单。

## 7. 统计和派生一致性

- 有效结论变更发 `VerdictChanged(question_id, old_revision, new_revision)`；0010 已完成私有事件原子落库（版本指 effective_revision），0011 已直接消费该持久队列并按源版本幂等更新错题，未经过 Redis 投递；0016报告读取比较来源指纹并支持显式重建；后台统计/报告自动消费仍待实现，不靠每天全量重算掩盖错账。
- 报告保存题目ID/有效revision/过滤规则版本/时间窗口；0016读取时显示stale，显式重建生成完整新快照；5分钟内后台自动重算仍是未实现目标。过渡时明确“来源已变化，请重建”，不可混用新分母旧分子。
- 每周/月计划引用 review_task_id；同题同任务完成一次，不因两个计划引用重复计数。
- OCR变更导致切题变化时旧题标superseded，保留订正关联；需要人工确认迁移关系的待确认，不按题号强行配对。

## 8. 数据保留初始策略（待法务/试点确认）

默认原图180天、活动错题与有效结构化学习记录至家长删除/账号终止；用户可选择更短原图期，删除原图时保留文字须另有有效服务目的和清楚提示。失去原图的题不得再宣称可核验，标“原图已删除”。原始模型响应建议30天排障期、生产普通日志30天、备份最长35天；法律要求的日志/审计按经确认适用期限独立配置，不用30天覆盖法定留存。最终采用 [08](../08-security-privacy-compliance.md) 的个人信息保护影响评估结果。
