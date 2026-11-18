# 本地 PostgreSQL、Redis 与任务投递/执行/结果结算开发环境

## 配置

| 服务 | 固定镜像 | 仅本机连接地址 | 持久化 |
|---|---|---|---|
| PostgreSQL | `postgres:18.6-bookworm` | `127.0.0.1:5432` | `wizzy_postgres_data` → `/var/lib/postgresql`（PostgreSQL 18 镜像挂载位置） |
| Redis | `redis:8.2.10-bookworm` | `127.0.0.1:16379` | `wizzy_redis_data` → `/data`，AOF `everysec`，淘汰策略 `noeviction` |

- 数据库 / 迁移管理用户：`wizzy` / `wizzy`；三个运行角色分别为 API `wizzy_app`、投递 `wizzy_app_dispatch`、执行 `wizzy_app_worker`。管理身份和三个运行身份不得混用。
- 管理密码保存在项目根目录 `.env` 的 `POSTGRES_PASSWORD`；该文件已忽略提交，勿上传或分享。
- PostgreSQL 时区 UTC、编码 UTF-8。代码迁移 head 为 `0028_textbook_content`，已在随机隔离测试库验证；开发库上次验证记录为 `0006_provider_call_fences`，本轮未迁移或重新读取其版本。已有开发库升级前请先备份，再执行下述 bootstrap；不要把测试库 head 当作开发库已升级。
- 管理账号只用于本地初始化、迁移与隔离测试建库。API 角色非业务表 owner、不可绕过 RLS；投递/执行角色无业务表直接读写权限，分别仅能执行指定 outbox / execution / model-call / result-settlement 函数；结果表仍无运行角色直接权限，0008 仅向 API 角色开放授权只读投影函数；0009 的题目/修订表同样不授予运行角色直接权限，仅 API 可执行受限题目查询和监护人 OCR 更正函数；0010 新增人工判定/变更事件私表，仍无运行角色直接权限，仅 API 可执行改判能力函数；0011 的消费回执/错题投影仍为私表，仅 worker 可执行消费、API 可读取授权列表；0012订正尝试/核对同样为私表，仅API可执行提交/读取/核对入口；0013复习任务/尝试/改期三表无运行角色直接权限，仅API可执行六个公开能力函数，worker/dispatcher无入口权限。0014课表四表启用FORCE RLS，API仅SELECT/INSERT及terms两版本列UPDATE，worker/dispatcher无权限。0015平台六表均FORCE RLS，三个运行角色无直接CRUD权限，仅API可执行platform_credentials/platform_login/platform_request三个函数，worker/dispatcher无入口。
- Redis 当前无认证且只绑定 loopback，仅适用于本机开发，不能作为生产网络隔离、认证、TLS 或恢复方案。AOF 和持久化卷也不替代备份或丢失恢复演练。

## 启动与连接

以下命令均在项目根目录执行。首次配置时，复制 `.env.example` 为 `.env` 并设置随机密码；已有环境不要覆盖 `.env`。

```bash
docker compose up -d --wait postgres redis
docker compose ps
# 容器内交互式 SQL 客户端
docker compose exec postgres psql -U wizzy -d wizzy
# 本机安装了 psql 时，使用 TCP 连接并按提示输入密码
psql -h 127.0.0.1 -p 5432 -U wizzy -d wizzy -W
# 仅验证 Redis 服务连通，不读取业务消息
docker compose exec redis redis-cli ping
```

## 后端初始化与 API

要求 Python 3.12 或 3.13、uv，且上述服务已启动：

```bash
uv sync --directory backend --locked
uv run --directory backend python -m wizzy.bootstrap
uv run --directory backend uvicorn wizzy.main:create_app --factory --host 127.0.0.1 --port 8000
```

bootstrap 单事务创建/验证三个独立受限角色并执行 Alembic 迁移，原子保存 `WIZZY_APP_ROLE` / `WIZZY_APP_PASSWORD`、`WIZZY_DISPATCHER_ROLE` / `WIZZY_DISPATCHER_PASSWORD`、`WIZZY_WORKER_ROLE` / `WIZZY_WORKER_PASSWORD` 到权限为 600 的 `.env`。三个密码独立生成。重复执行不更换密码、不重建数据；已有 `0003_submissions` / `0004_outbox_dispatch` / `0005_execution_leases` 环境也通过同一命令升级，不需要重建数据卷。已有角色缺少密码、密码不匹配或权限不安全时拒绝初始化，不会静默重置。不要打印、提交或分享完整连接 URL、`.env` 或会话令牌。

配置边界：

| 配置 | 用途 |
|---|---|
| `WIZZY_MIGRATION_URL` | 仅初始化/迁移；outbox 函数 owner 需有绕过 FORCE RLS 的离线迁移权限，不能给运行角色此权限 |
| `WIZZY_PLATFORM_MFA_KEY` | 历史TOTP加密密钥，登录已不再使用；可留空 |
| `WIZZY_DATABASE_URL` | 可选的受限 API 连接；不设置时从本地 API 角色凭据组装 |
| `WIZZY_DISPATCHER_DATABASE_URL` | 可选的独立受限投递连接；不设置时从本地 dispatcher 凭据组装 |
| `WIZZY_WORKER_DATABASE_URL` | 可选的独立受限执行连接；不设置时由 `WIZZY_WORKER_ROLE` / `WIZZY_WORKER_PASSWORD` 组装，供 `ExecutionStore`、受控 worker 工厂及独立错题消费者 `wizzy.learning` 使用 |
| `WIZZY_BROKER_URL` | 默认 `redis://127.0.0.1:16379/0`，通过私有环境配置注入，不将带凭据 URL 写进命令历史 |
| `WIZZY_GRADING_QUEUE` | 默认 `wizzy.grading.v1`；投递和受控消费者必须使用同一队列配置 |

API 启动拒绝超级用户、BYPASSRLS 或业务表 owner；dispatcher / `ExecutionStore` 还拒绝角色继承/成员关系、对象所有权与业务表直接权限。不要用管理账号启动运行组件，也不要共用三个运行账号。

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
# 交互接口文档 /docs；机器接口契约 /openapi.json
```

`ready` 比对数据库迁移版本，不匹配返回 503；它不检查 Redis 或 worker。`ai_provider: unconfigured` 表示未接入真实 AI。默认开发额度为每家庭每上海自然月 100 页，不代表正式套餐或计费规则。

8000 端口占用时选择其他空闲端口，例如 `--port 18000`，同步替换检查地址，不停止其他项目的服务。当前 Compose 仅管理 PostgreSQL 和 Redis，API/dispatcher 在宿主机运行；以后容器化时须相应改用服务名和容器端口。

## 离线首次初始化平台安全管理员（0015 / 0031）

本节只建立首位管理员，不是账号恢复或部署验收。先备份已有数据库并升到代码head（含`0032_platform_password_captcha`）；初始化命令本身**不执行迁移、不修改.env、不生成运行角色**。仓库不提供默认邮箱或密码。

1. 在受控离线环境通过私有环境/密钥管理显式注入`WIZZY_MIGRATION_URL`，对应具备绕过RLS权限的迁移owner；程序不会从普通API连接推断管理连接。运行API、worker和dispatcher绝不能使用它，也不要把带密码URL写入命令参数/历史或工单。
2. 在项目根目录运行，通过隐藏输入填写并确认 12–128 字符密码：

```bash
uv run --directory backend python -m wizzy.platform_bootstrap --email security-admin@example.test
```

必须使用支持隐藏输入的交互终端，勿将密码通过命令行拼接/日志管道传入。成功仅输出账号UUID；账号为`security_admin`，不创建登录会话。之后用同一邮箱、密码和登录页图形验证码进入工作台。

初始化全局串行化；只要已有任何账号，即使已停用，也拒绝再次初始化或改密。账号与bootstrap审计同事务写入，审计失败不留半成品。错误统一安全提示，不回显驱动异常或连接凭据。该命令仅负责首位管理员初始化；0021已实现角色/启停、最后安全管理员保护及独立升权审批，在线邀请仍待开发。独立正式 Web 客户端已接通身份及下述治理接口，三端演示原型保持不连接 API。接口与权限见[数据模型/API](architecture/06-data-model-and-api.md)。

## 正式平台 Web 客户端（身份、模型、学校与教材治理）

源码在 `frontend/platform`，与 `docs/prototype` 演示原型分离。采用 Vue 3 + TypeScript + Element Plus；包含平台密码与图形验证码登录、本人会话、角色权限、只读账号/审计，以及0018模型目录列表/筛选/登记/详情/修订/启停/历史、0021账号角色/启停及独立升权审批、0022学校目录、0023按学年的校区/部门/年级/班级组织树，以及0024校内学生名册和逐学年在读关系。学校与组织支持登记、修订、启停及不可变历史；组织支持跨年级移动班级，名册支持登记、调班、休学/离校/毕业与不可变历史。名册不自动关联家庭学生或授予家庭访问权限。0025新增教材版本目录、元数据/授权登记、提交、独立审核、撤回/修订及不可变历史；权利批准不发布正文；0026接通≤50 MiB PDF隔离接收、摘要/凭证和重试；0027接通持久处理任务、扫描证据、历史/刷新及逐页内嵌文本草稿。独立worker必须显式配置ClamAV；扫描和提取结果不改上传凭证，原PDF不提供下载/预览。0028已接通逐页正文校核、章节/物理源页映射、独立抽检、发布/TXT下载、撤回与不可变历史。图片型PDF的OCR、原PDF预览和AI检索仍待开发。在线邀请、MFA生命周期、学校管理员范围、名册批量导入/错误下载/回滚、监护关系及真实模型评分/生产发布仍待实现。

建议使用仍受支持的 Node.js LTS（22.12+ 的 22.x 或 24.x，安装该系列的安全补丁版本）与 npm。依赖按 `package-lock.json` 安装；本机验证环境为 Node 26.3.0/npm 11.16.0，不把本机版本作为生产推荐。

```bash
npm --prefix frontend/platform ci
npm --prefix frontend/platform run dev
# 另一个终端：API 须按上节在 127.0.0.1:8000 启动
# 浏览器访问 http://127.0.0.1:4175
```

开发服务器仅监听 `127.0.0.1:4175`，固定端口且占用时报错；`/api/v1/admin` 同源代理到 `127.0.0.1:8000`。不要通过放开 CORS、监听公网或把上游密钥写入 `VITE_*` 来绕过环境配置。

教材PDF接收使用后端 `WIZZY_STORAGE_ROOT`（默认 `.local/private-media`）下的 `textbook-quarantine` 私有目录，目录0700、文件0600。部署时保证与临时上传位于同一支持硬链接及fsync的文件系统，禁止通过静态站点暴露此目录。单份≤50 MiB，上传凭证15分钟，流接收120秒；反向代理须允许50 MiB原始PUT并配置足够超时。接收结果为`quarantined/not_scanned`，不生成TXT；回执丢失可在同一会话中重试，重新登录后可打开教材、选择原文件恢复未过期申请。不要直接修改文件或把隔离目录当作已发布教材资源。

### 教材正文校核与发布（0028）

在已完成提取的任务中显式打开校核工作区，先读取已有内容或创建草稿。逐页编辑文字并确认已核对，维护覆盖全部PDF物理页的章节，再保存并提交。编辑过文字的页面须重新确认。审核账号不能参与该内容的创建、修订或提交；抽检必须覆盖首末页、来源空页及全部文字修订页。发布另要求有效retrieval用途，同一教材版本最多一份已发布正文，替换前先撤回旧内容。

正文、历史正文及TXT读取均复核当前权利修订和storage/ocr/preview用途；TXT还要求已发布且有retrieval用途。权利失效后仍允许有写权限者撤回正文。页面的“已核对”是人工声明，系统尚无原PDF并排预览；章节源页表示PDF物理页码，不是书本印刷页码。已发布TXT尚未接入AI索引或检索。接口与限制见[0028实施记录](plans/2026-10-26-textbook-content.md)。

### 教材扫描与文本提取 worker（0027）

处理申请不会由API自动执行。先显式配置可信ClamAV地址 `WIZZY_CLAMAV_HOST`（必填IP），可选 `WIZZY_CLAMAV_PORT`（默认3310）、`WIZZY_CLAMAV_TIMEOUT`（默认30秒，上限60秒）和 `WIZZY_CLAMAV_DATABASE_TIMEZONE`（默认`UTC`，须与clamd进程的本地时区一致），再从项目根目录执行一次：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m wizzy.textbook_worker
```

该命令使用 `Settings.worker_db_url` 对应的受限worker角色，每次最多领取一项任务后退出；API角色不能领取或保存处理结果。worker与API须使用相同的 `WIZZY_STORAGE_ROOT`，私有临时目录为 `textbook-processing`，隔离源文件保留。不要把凭据放入命令行，也不要将ClamAV服务暴露到公网。没有可用扫描器时任务返回不可用，不跳过扫描或返回模拟成功。

平台在文件记录中显式申请、查看历史、刷新任务并查看逐页草稿；申请须有教材写权限，状态须有读权限，正文另要求当前教材修订一致及有效preview用途。领取与保存均复核原申请人、原会话、权利修订和storage/ocr用途，因此退出、会话到期、失权或教材变化会阻止任务完成。运行租约180秒，最多领取三次；旧令牌或过期结果不能落库。处理期间不持有数据库事务。

项目采用病毒库最多72小时、未来时间最多5分钟的策略（并非ClamAV官方时限）。扫描前后均检查VERSION日期及版本一致性；无库日期、日期无效、夏令时歧义/不存在、过期或超前均返回不可用，扫描前失败不发送PDF、不启动提取。VERSION日期不带时区，必须显式匹配clamd时区，不会自动探测；建议扫描服务统一使用UTC。证据`database_date`归一为带`+00:00`偏移的ISO日期时间，`checked_at`同样为UTC。协议测试已覆盖边界与扫描期间过期，但真实引擎、病毒库自动更新/故障告警及Linux资源隔离仍需部署验收。

终态为 `draft_ready`、`no_text`、`rejected`、`unavailable` 或 `blocked`；只提取PDF内嵌文本，空页不会被伪造为OCR成功。结果固定 `requires_review=true`、`ocr_performed=false`、`content_available=false`，不能用于AI检索。0028在此任务结果之外提供独立的校核与TXT发布流程，不改写0027处理结果；OCR入口仍待实现。正式运行要求Linux以启用地址空间硬上限；协议模拟测试不能代替真实环境验收。

登录前须由环境管理员**先备份再显式升级**目标库到代码head（含`0032_platform_password_captcha`），并使用上节离线首位初始化。本文和本轮测试不会迁移共享开发库或创建通用演示账号；已有平台账号不得为了查看页面而重置凭据。家庭端身份不能登录平台。

```bash
# API 客户端单测，生产类型检查/构建，模拟 API 的 UI 回归
npm --prefix frontend/platform test
npm --prefix frontend/platform run build
PLAYWRIGHT_CHANNEL=chrome npm --prefix frontend/platform run test:browser
# 无本机 Chrome 时，先安装 Playwright Chromium，UI 回归可不设 channel
cd frontend/platform && npx playwright install chromium
# 该命令之后回到项目根目录
```

UI 测试自行启动 4175，不复用已运行的 dev 服务；先停止手工启动的前端，避免端口冲突。UI 测试使用模拟响应，不能当作真实后端证据。真实全链路测试需已构建 `dist`、可用 PostgreSQL 和本机 Chrome（默认 channel 为 chrome）；使用 Playwright Chromium 时显式设 `PLAYWRIGHT_CHANNEL=chromium`：

```bash
WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest -q tests/test_platform_identity.py tests/test_model_catalog.py tests/test_model_catalog_migration.py tests/test_textbook_rights.py tests/test_textbook_migration.py tests/test_platform_web.py --tb=short
# 完整回归包含上述真实浏览器用例；普通 pytest 不设置此变量时跳过该用例
WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest -q --tb=short
npm --prefix frontend/platform audit --registry=https://registry.npmjs.org
```

真实联调自动创建随机隔离测试库/受限角色/临时平台账号与 MFA 密钥，通过 stdin 将临时凭据交给浏览器脚本；回环 API 与构建产物的同源代理使用临时端口，结束后关闭进程并由 fixture 清理自己的数据库/角色。不会使用共享库平台账号、不改真实密码、不清 Redis、不保存真实账号截图。测试服务使用真实FastAPI和PostgreSQL，包含管理员登记/修订模型、账号治理、学校登记/修订/停用、组织创建/班级移动/上级停用、学生登记/调班/休学、教材登记/提交/独立批准/撤回/修订/重提及operator只读/拒绝写入验证；目录、组织/名册/教材修订、审计和会话撤销均检查数据库真实记录，不调用外部模型。

模型配置密钥仅用`secret://providers/...`服务引用，不粘贴明文key；引用只写不读。目录启用与上海日期合同可用性分别展示；即使available，所有未评测条目仍为accuracy=null、production_eligible=false。不要用家长改判或本地演示评分解除门禁。完整API约束见[0018数据模型](architecture/06-data-model-and-api.md#当前模型目录0018)。

令牌仅放私有 JS 内存，不落 URL、localStorage/sessionStorage 或 Cookie，刷新即重新登录。401 清会话、403 清受限列表但保留会话；窗口获得焦点/重新可见时重验权限，到期自动清除；退出请求失败会明确提示“本地已退出，服务端撤销未确认”。客户端菜单不是授权边界。

本地可用 `npm --prefix frontend/platform run preview` 检查构建产物；**`vite preview` 不是生产服务器**。生产应使用 HTTPS 静态托管并为 `/api/v1/admin` 配置同源反向代理，禁止缓存鉴权 API；生产部署/CSP/基础设施和正式安全验收仍待独立交付，不把开发代理作为上线配置。参见 [Vite 静态部署说明](https://vite.dev/guide/static-deploy.html)。

本切片验证和模拟数据截图见 [正式客户端计划](plans/2026-10-25-platform-web-client.md)、[工程台账](plans/14-development-status.md) 与 [视觉证据](evidence/platform-web-2026-10-25/README.md)。

## 独立 outbox 投递进程（不是批改 Worker）

完成 bootstrap 后，在另一个终端运行以下任一命令：

```bash
# 最多抢占并投递一个事件后退出
uv run --directory backend python -m wizzy.dispatch --once
# 持续轮询；Ctrl-C 退出
uv run --directory backend python -m wizzy.dispatch
```

| 输出 | 含义 | `--once` 退出码 |
|---|---|---|
| `dispatch=idle` | 当前没有可抢占事件，不证明 Redis 可用 | 0 |
| `dispatch=published` | broker 发布调用成功且有效租约结算成功，不是任务执行成功 | 0 |
| `dispatch=retry` | 发布异常，已记录安全错误码并安排重试 | 1 |
| `dispatch=lease_lost` | 租约过期或被替换，结算被围栏拒绝；消息仍可能已发出 | 1 |

启动/数据库异常返回 1，只打印不含凭据的通用错误；持续模式空闲时不反复刷日志，重试/空闲轮询间隔 2 秒。

### 已实现的投递语义

- 提交事务把 submission/job、额度预占和 outbox 一并写入 PostgreSQL；DB 是待处理作业的持久事实来源，Redis 不是唯一副本。
- `claim_outbox` 用 `FOR UPDATE OF e SKIP LOCKED` 抢占，租约 60 秒。CLI 每次只取一条，提交数据库事务后才访问 Redis，不跨网络持有该行锁。
- 发现家庭/学生有效、有同意、删除代次匹配的 queued 作业，以及执行租约已过期的 running 作业；两类均要求该事件没有任何 `model_runs` 记录。该检查不能替代执行边界和将来结果写回前的授权/版本/代次检查。
- 发布失败仅保存 `broker_unavailable`，不保存异常文本；连续退避为 2、4、8、16、32、64、128、256、300 秒，之后保持 300 秒，成功后清零连续失败计数。此处是 outbox 基础设施重试，不是模型外呼重试次数。
- `settle_outbox` 必须匹配未过期的租约 token。发布后、标记前崩溃可以重投；发布成功会记录 `published_at`，但只要任务仍符合 queued 条件且没有调用记录，300 秒后仍可再次投递，以支持消息丢失后的 DB 重放。`published_at` 不是终态或消费回执。
- 无调用记录的过期执行通过 `execution_dispatch_attempt` 标记每个 attempt 的恢复投递，首次可绕过旧未来重播时间；抢占同时移除被绕过的旧延迟。成功结算安排 300 秒重播，失败结算保持指数退避；抢占后未结算崩溃则只等待 60 秒投递租约到期，不被旧未来时间卡住，也不在活跃租约内热循环。
- Celery 任务名 `wizzy.grade_submission.v1`；JSON 参数仅含 `event_id`、`job_id`、`source_revision`、`deletion_epoch`、`request_id`，不含租约 token、原图、题文、手机号、私有路径或签名 URL。任务 ID 固定为 event ID，**不构成消费者去重**，不承诺 exactly-once。
- 已有显式注入提供方的受控任务工厂，但没有全局 Celery app、默认模型或 worker CLI；不要启动通用 Celery worker 消费该队列。没有有效消费者时队列会积累重复消息，持续投递仅用于受控联调，不应无人值守长期运行。
- 仅入队后 job/submission 仍为 queued，额度仍 reserved，不生成批改结果、不结算成功消费，也不伪造供应商费用。DB 执行去重与租约已实现，见下节；保守结果写回和成功页额度结算已实现；受控任务集成见下节，仍无真实提供方和真实供应商费用核对。

技术依据：[PostgreSQL 18 行锁与 SKIP LOCKED](https://www.postgresql.org/docs/18/sql-select.html)、[SECURITY DEFINER 安全 search_path 与函数授权](https://www.postgresql.org/docs/18/sql-createfunction.html)、[Celery 5.6 调用与 task_id](https://docs.celeryq.dev/en/v5.6.3/userguide/calling.html)。本仓库实际语义仍以迁移、实现与隔离测试为准。

## DB 执行边界与受控消费者

`backend/src/wizzy/execution.py` 提供 `ExecutionStore.claim` / `heartbeat` / `release`，通过 `Settings.worker_db_url` 连接独立受限 worker 账号。通过 `worker.create_worker(settings, provider=...)` 显式集成已注册任务；**没有全局 app、默认提供方或 worker CLI**，不要启动通用 worker 代替它。

- claim 只接受上述五字段消息；DB 重载可信家庭/学生/作业/提交，检查同意、状态、request_id、源版本及删除代次，唯一回执与锁保证同一作业不并发取得有效执行租约。
- 执行租约 60 秒，claim 后 job/submission 为 running/processing，额度继续 reserved。心跳与释放都重新检查授权并匹配有效 token/版本/代次；旧 token、撤回、取消及失效版本不可继续推进。
- 没有任何 `model_runs` 记录的外呼前中断可 release，前两次分别退避 2 秒、4 秒，无调用记录的过期运行也可恢复；最多三次执行尝试。第三次释放或过期后再次恢复时进入 deadletter，job/submission failed，并原子释放预占一次。仅投递而没有有效消费者时不会触发这些执行行为。
- **这些恢复语义仅适用于尚未持久化调用许可的阶段。** `0006_provider_call_fences` 已持久化调用围栏；即使尚未实际发送请求，只要存在调用记录，也不得 release、重新 claim 或恢复投递。0007 已实现结果写回围栏和成功页额度结算（见下节），但费用对账仍未实现，也不承诺 exactly-once。

## 模型调用围栏（不是已接入真实模型）

`ExecutionStore.begin_call` / `finish_call` 是内部 Python/SQL 边界，不是公开 HTTP 接口。仍只供隔离测试或受控集成，没有注册的消费者或真实提供方。

- 顺序为 claim → `begin_call` → **数据库提交成功且取得 `run_id` / `operation_key` 后**才允许一次外呼 → `finish_call`。没有取得许可（包括返回丢失/提交确认不明）不得发送或自动重试。`operation_key` 仅是本地防重标识，不证明供应商提供幂等或已收到请求。
- begin 在规范上下文锁内重新检查家庭/学生授权、同意、任务状态、版本、删除代次和有效执行 token；绑定 `ocr|structure|verify|tutor` 阶段及 provider/model/model_version。每个作业版本/删除代次/阶段只有一条调用记录；已有调用非 succeeded 或费用 unknown 时，禁止申请新阶段。当前没有完整阶段顺序与业务结果复用。
- 初始 `outcome=in_flight`、`billing_state=unknown`、`cost_microyuan=NULL`；费用是非负 int64 微元整数（1 元 = 1,000,000 微元），未知不能记作 0。finish 只写脱敏调用/账务元数据，不写批改结果、不改变 job/submission 状态、不结算用户额度。known 仅表示收到符合输入契约的金额，不代表已核验供应商账单；测试金额均为合成数据。
- finish 允许匹配原调用的迟到账务回执，即使已经取消、撤回或租约过期，也不得借此复活业务。outcome 和供应商 request id 一经记录不可更换；未知金额可补为已知，已知金额只能同值幂等，不可覆盖或退回未知。
- **事件有任何调用记录就禁止 release/reclaim/恢复投递**，包括费用已知、租约过期、提交许可后尚未发送即崩溃等情况。仅原来仍有效的执行租约可在既有调用 succeeded+known 后申请未调用阶段；不存在自动对账、运维解锁或额外 pending 业务状态。
- `model_runs` 启用并强制 RLS，三个运行角色均无直接表/列权限；仅 worker 可执行 begin/finish，旧 `_unfenced` 函数不可由运行角色调用。有调用记录时迁移拒绝降级，不得删记录绕过围栏。

## 内部结果与成功页额度结算（0007）

`ExecutionStore.complete(lease, result)` 对应仅 worker 可执行的 `complete_execution`，不是公开 API，也不是自动消费任务。调用失败返回 false 不表示可以重复付费外呼。

- 契约为 `1.0-conservative`，Python/SQL 双层拒绝非法结构；1–10 页、版本标识、prompt_hash、题文/原始作答、bbox 和同页证据。题目仅 pending_review/unsupported，始终须监护人复核；没有答案正确/错误判定或参考答案。合成测试不证明模型能力。
- 数据库重载授权/同意/源版本/删除代次并持锁；须有效未过期执行 token，所有关联调用终态且费用 known，成功页须关联本事件 succeeded+known 的 structure/verify 调用。等待配额锁后再次重验租约。
- 一笔事务写入私有 `grading_results`、消费成功页额度/释放其余预占、推进 job/submission、结束执行回执并写 `submission.processed` 事件；全成功/部分/零成功均覆盖。结构处理成功页数不是批改正确率，也不等于供应商账单已核验。
- 相同 token 与 JSON 结果重复提交只结算一次；冲突拒绝覆盖。即使已经结算，撤回授权或改变 deletion_epoch 后旧结果重放也会拒绝。有结果时迁移拒绝降级，禁止删证据绕过。
- 结果表 ENABLE/FORCE RLS，三运行角色无直接表权限；0008 已提供当前授权下的保守结果只读端点，见下节。本轮不迁移开发库，不重置凭据，不清空数据/Redis，不接真实提供方或启动常驻消费进程。

## 保守结果只读查询（0008）

- `GET /api/v1/submissions/{submission_id}/result`：家庭 owner、明确授权到该学生的监护人，以及对应学生的有效儿童会话可读。无需额外请求体，身份来自已验证会话，不从客户端 tenant/student 参数推断。
- 只展示当前有效的 `1.0-conservative` 结算结果，含稳定排序的页面/题文/原始作答及复核提示；没有自动正确/错误、参考答案或模型正确率。内部调用标识、Prompt/评测版本、执行 token 及费用不外泄，原图仍须受限访问。
- 已结算完整/部分/全失败结果均可返回 200；尚无有效结果 409 `result_not_ready`，跨家庭/学生或旧删除代次 404；有效监护人未同意 403，会话失效 401。撤回后重新同意不能恢复旧代次结果。读取不外呼、不计费、不修改结果，响应 no-store。
- 数据库函数只授予 API 执行权，三个运行角色无结果表直接权限；开发库须备份后按 bootstrap 流程升级到 0008 方可使用。字段与完整状态语义见 [06](architecture/06-data-model-and-api.md)，本轮未操作开发库。

## 自动化验证

```bash
uv run --directory backend pytest -q --tb=short
uv run --directory backend ruff check . --output-format concise
npm ci --prefix docs/prototype/ui
npm run build --prefix docs/prototype/ui
PLAYWRIGHT_CHANNEL=chrome PROTOTYPE_BUILT=1 npm test --prefix docs/prototype/ui -- --workers=3 --timeout=15000 --reporter=line
```

后端测试连接本机真实 PostgreSQL 与 Redis，自动创建随机隔离数据库、受限角色及随机测试队列；结束仅删除自己的对象，不清空开发库，不执行 Redis FLUSHDB/PURGE。需本地管理账号建库/建角色权限。请保留 `--tb=short`，不要在报告中导出 SQL 参数或敏感配置。前端命令需系统 Chrome；原型测试不代表正式三端 API 联调或微信/App 真机验收。

2026-10-25 执行边界切片复验（历史记录，迁移 0005）：后端 **98 passed（18.04s，1 warning）**、Ruff 通过，包含 37 个执行边界参数化用例。除真实 Redis JSON 投递、CLI 和投递故障测试外，新增执行去重/心跳/旧 token、授权及版本失效、有限恢复与额度释放、同意锁等待，以及恢复发布成功/失败/抢占后未结算崩溃三条路径。bootstrap 测试也验证 worker 独立凭据及受限执行连接。

该历史切片的开发库从 `0004_outbox_dispatch` 升至 `0005_execution_leases`，bootstrap 连续两次成功，第二次 `.env` 内容不变且权限 600、原 API/dispatcher 密码保留；三角色危险标志均 false、public 关系 owner 数均 0，dispatcher/worker 业务表直接权限均无，恢复函数修复已安装。未清空开发数据或 Redis，未启动无人值守投递/消费。

2026-10-25 调用围栏切片：此前专项运行 `pytest tests/test_provider_calls.py -q --tb=short` 为 **38 passed（6.00s，1 warning）**，包含跨阶段并发唯一许可、冲突金额并发不可覆盖、failed+known 禁止下一阶段/释放、有记录拒绝降级且事务回滚保留围栏四项补充回归。全量复验结果见下方最新记录。

同一调用围栏切片此前已完成开发库 `0005_execution_leases` → `0006_provider_call_fences` 迁移及连续两次 bootstrap，保留原三个运行角色密码，第二次 `.env` 不变、权限 600。随后只读复核确认迁移 head、`model_runs` FORCE RLS、三角色无直接表/列权限、旧 `_unfenced` 函数不可执行且 begin/finish 仅 worker 可调用；不是重新迁移或重建开发库。

同日投递切片的 **61 passed（12.37s）**、0004 初始化是历史证据；前端构建、Chrome 45 项与 API live/ready/OpenAPI 启动检查也是上一切片记录，本轮未重跑。仍有 Starlette TestClient/httpx 弃用警告；**没有执行 Redis 重启、AOF 丢失、全套 T13/T01–T29、真实提供方、正式三端联调或真机验收**。完整记录见 [14 实施台账](plans/14-development-status.md)。

调用围栏续接复验（历史记录，2026-10-25，迁移 0006）：`uv run --directory backend ruff check . --output-format concise` 通过；`uv run --directory backend pytest -q --tb=short` 为 **136 passed，1 warning，23.16s**，包含上述 38 项调用围栏参数化测试。本次只读开发库检查确认 head 为 `0006_provider_call_fences`、`model_runs` 启用/强制 RLS、三运行角色权限边界及 `.env` 权限 600。本次仅同步相关文档、执行后端测试和只读检查；未重新执行 bootstrap、前端构建/Chrome 回归或 API 启动检查，未接真实 AI、启动无人值守 dispatcher/消费者或执行生产验收。

### 结果结算续接复验（历史记录，2026-10-25，迁移 0007）

后端全量 `uv run --directory backend pytest -q --tb=short` 为 **223 passed，1 warning，43.89s**，Ruff 检查通过；其中结果模块 87 项参数化用例。按结果→调用围栏→执行边界顺序专项运行为 **162 passed，1 warning，31.92s**，覆盖配额锁等待后的租约过期、授权撤回后的已结算重放及并发幂等。

降级保护测试已消除模块运行顺序依赖：有结算结果时接受 0007 先拦截，没有结果时由 0006 保护持久调用记录，均保留迁移回滚与调用记录检查。该降级测试另在全新隔离库单独运行，**1 passed，1 warning，0.79s**，确认 0006 路径确实执行。

以上均使用随机隔离测试库，未迁移或重新读取开发库版本、重置凭据、清空共享 Redis。没有重跑前端构建/Chrome 或 API 启动检查，没有注册/启动消费任务、接真实 AI 或核验真实账单。现有 Starlette TestClient/httpx 弃用警告保留；合成结果与金额不代表模型准确率或生产验收。完整边界见 [14 实施台账](plans/14-development-status.md)。

### 受控消费集成验证（历史记录，2026-10-25，迁移 0007）

`consumer.py` 仅串联单 structure 阶段。适配器 prepare 只读本地可信输入；invoke 一次且必须关闭 SDK 重试、遵守传入的 30 秒超时（不是 Python 强制中断）。调用许可可能已提交后，异常/未知费用只能待对账，不释放或自动再调用；迟到回执可保存但不能复活取消结果。当前保守结果只允许 pending_review/unsupported，不是自动判题。

工厂缺少 provider 即拒绝创建，并校验受限 worker 账号；每条消息使用独立 engine 并释放，避免继承预建连接。任务 JSON-only、无结果 backend、无自动重试，prefetch=1，软/硬限制 50/55 秒；真实 Redis 测试使用 solo，不能据此宣称 prefork 强杀/真实超时可恢复。**测试提供方不作为生产选项，不提供生产启动命令。**

```bash
uv run --directory backend pytest -q tests/test_consumer.py tests/test_dispatch.py tests/test_worker.py --tb=short
uv run --directory backend pytest -q --tb=short
uv run --directory backend ruff check . --output-format concise
```

该切片全量为 **261 passed，1 warning，52.51s**，Ruff 通过；消费者 28 项、worker 10 项，真实 dispatcher→Redis→任务→DB 验证重复消息只调用并结算一次。包含调用许可/回执/结算提交响应丢失、授权失效、未知成本和数据库故障。修复的是隔离测试库的事件归属，不是清空共享数据或放宽生产恢复围栏。

本轮未迁移/重读开发库、重新 bootstrap、重跑前端/API 启动，未接真实 AI、核验真实账单或启动常驻 worker。现存依赖弃用警告保留，完整实现/验收边界见 [14 实施台账](plans/14-development-status.md)。

### 保守结果只读验证（历史记录，2026-10-25，迁移 0008）

新增 `tests/test_result_read.py` 的 18 项验证覆盖当前权限、跨家庭/兄弟姐妹隔离、撤回与重授权、结果/回执版本及任务状态失效、等待同意锁、私表/函数最小权限、稳定投影和读取不产生结算副作用。

- 专项：`uv run --directory backend pytest -q tests/test_result_read.py -x --tb=short` → **18 passed，1 warning，4.76s**。
- 当时全量：`uv run --directory backend pytest -q --tb=short` → **279 passed，1 warning，54.90s**。
- 静态检查：`uv run --directory backend ruff check . --output-format concise` → **通过**。

全量首次失败来自 bootstrap 测试硬编码旧 head 0007，更新为 0008 后全量通过。0008 仅在随机隔离库验证，开发库上次记录仍为 0006，未重新读取或升级；未 bootstrap/改凭据/清空共享数据或 Redis，未重跑前端构建、API 启动或启动常驻消费者。现存 Starlette TestClient/httpx 弃用警告保留，不代表真实模型/账单或生产验收通过。

### OCR 更正与历史接口（历史验证，2026-10-25，迁移 0009）

0009 新建私有 `questions/question_revisions` 并为已有成功页回填机器版本。API 角色只能通过受限函数读取/追加，dispatcher/worker 不可读写。开发库上次记录仍为 0006，**本次没有升级或重读**；以下接口示例仅供备份、审阅迁移并升级后联调，不表示本机开发 API 已运行在 0009。

1. 用当前获授学生的监护人或对应儿童会话请求 `GET /api/v1/submissions/{submission_id}/questions`，取得稳定 `question_id` 和当前 `revision`。
2. 用监护人会话请求 `POST /api/v1/questions/{question_id}/ocr-corrections`，携带以下 JSON；不要在公开日志或报告中粘贴会话令牌、真实题文或儿童信息。
3. 成功 201 返回新版本及完整历史；再用 `GET /api/v1/questions/{question_id}` 查询。旧版本再提交为 409 `revision_conflict`，需刷新核对，不能自动覆盖新内容。

```json
{
  "expected_revision": 1,
  "stem": "12 + 8 = ?",
  "student_answer_raw": "20",
  "reason": "核对原图，识别成了21"
}
```

版本必须为严格整数 1–2147483647；题文 1–8000 字符，原答可为空且最多 8000 字符，原因 1–1000 字符。题文/原因不接受纯空白，文本不接受 NUL，禁止额外字段，非法输入 422。儿童只能读自己的题目，更正为 403；越权/旧代次 404；当前有效监护人尚未同意为 403；有效结果未就绪时列表为 409，全失败结果列表为空。

更正只追加逐题 OCR 历史，原机器结果不变、有效结论待复核；与 API 审计同事务，不调用模型、不消费额度、不生成新的 source_revision 或正确率。该 0009 切片当时尚无判定修订或人工确认；0010 的新增能力见下节。重跑、模型评分和正式三端联调仍未实现；历史不分页，尚无容量验收。接口完整字段/权限见 [06](architecture/06-data-model-and-api.md)。

```bash
uv run --directory backend pytest -q tests/test_question_revisions.py tests/test_question_migration.py --tb=short
uv run --directory backend pytest -q --tb=short
uv run --directory backend ruff check . --output-format concise
```

- 专项：**27 passed，1 warning，8.27s**；独立迁移模块 **3 passed，1 warning，2.28s**。
- 当时全量：**305 passed，1 warning，63.23s**；Ruff：**通过（All checks passed!）**。
- 覆盖稳定 ID/排序、不可变历史、CAS 并发、授权撤回及锁等待、输入边界、私表/函数权限、部分/失败结果、结算原子回滚、0008 回填/重复升级和降级保护。
- 首轮全量发现旧 0007 降级用例被其他测试留下的 guardian 历史先行拦截；已把该用例迁至逐测试独立临时数据库，继续分别验证 0009 的人工历史保护与 0007 的结算结果保护，未改生产规则。保留现有 Starlette TestClient/httpx 弃用警告。

本次仅在随机隔离库验证，未 bootstrap/改凭据/清空共享数据或 Redis，未重跑前端构建、API 启动或启动常驻消费者；不代表真实 AI、账单或生产验收通过。

### 监护人人工改判接口（2026-10-25，迁移 0010）

代码已实现 0010，**未升级或重读共享开发库**。下列步骤供备份、审阅迁移并由环境维护者升级后联调，不是当前本机 API 已运行在 0010 的声明。

1. 通过有权监护人会话读取 `GET /api/v1/questions/{question_id}`，核对题文、`revision` 和 `verdict_revision`。
2. 请求 `POST /api/v1/questions/{question_id}/verdict-overrides`，按读取值填写两个 expected 版本与必填原因：

```json
{
  "expected_revision": 1,
  "expected_verdict_revision": 0,
  "verdict": "correct",
  "reason": "监护人核对原图及解题过程"
}
```

3. 成功 201，检查 `verdict_revision` 和 `effective_revision` 均递增，有效指针及 `verdict_provenance=guardian`，历史包含本次原因。原 `/result` 不变。双版本任一过期为 409，须刷新并重新核对，不能自动重试覆盖。
4. 后续 OCR 更正会清空有效人工判定并将来源改为 ocr_pending，原历史保留；需基于新题文和累计判定版本再确认。不要把清空有效指针当成累计 `verdict_revision` 回到 0。

题文版本是严格整数 1–2147483647，判定版本是严格整数 0–2147483647；判定只允许 correct/incorrect/pending_review/unanswered/unsupported。原因 1–1000 字符，拒绝全空白/NUL/额外字段。非法输入 422，儿童写 403，越权/旧代次 404，当前有效监护人未同意 403。不要将真实题文、原因、儿童信息或令牌粘贴到公共日志。

改判历史、有效指针和私有 VerdictChanged 事件与 API 审计同事务；不触发模型、重跑、额度消费或准确率评分。0010 当时事件尚未消费；0011 已实现独立持久消费者与错题列表，见下节。报告和模型评分仍不会自动更新。正式三端仍未联调，原型全部是演示数据。

验证命令与本轮实测结果见下方记录。全部后端测试创建随机隔离 PostgreSQL 库/角色，Redis 用随机专属队列，不对共享开发库执行迁移或 bootstrap。

```bash
uv run --directory backend pytest -q tests/test_question_revisions.py tests/test_question_migration.py tests/test_verdict_revisions.py tests/test_verdict_migration.py --tb=short
uv run --directory backend pytest -q --tb=short
uv run --directory backend ruff check . --output-format concise
```

### 0010 本轮实测结果

- Ruff：**通过（All checks passed!）**；上述四模块专项：**73 passed，1 warning，22.77s**；全量：**351 passed，1 warning，82.77s**。保留既有 Starlette TestClient/httpx 弃用警告。
- 首轮全量 350 passed、1 failed：0008→head 同事务升级因 0009 回填遗留延迟外键检查，0010 ALTER TABLE 被 PostgreSQL 拒绝。已在 0010 定点冲刷该外键检查并恢复延迟模式；没有中途提交、禁用触发器或放宽外键。增强原用例，验证失败整体回滚、原结果不变、默认延迟约束及不伪造人工历史，随后专项/全量通过。
- 同轮原型构建与 45 项浏览器测试通过，但未重新截图；这些证据不等于正式客户端联调或真实 AI/生产验收。完整复现过程和测试边界见 [实现台账](plans/14-development-status.md) 与 [原型验证记录](prototype/VERIFICATION.md)。

### 错题派生消费者与列表联调（2026-10-25，迁移 0011）

**前提：**先备份、审阅0011并按上文由维护者升级，不能对仍在0006的开发库直接运行此命令。本文记录的是隔离库证据，本轮未 bootstrap、迁移或重读共享开发库。沿用受限 `WIZZY_WORKER_DATABASE_URL` 或 worker 角色凭据，禁止使用管理/API/dispatcher 连接。

```bash
# 消费至多一个 PostgreSQL 私有改判事件
uv run --directory backend python -m wizzy.learning --once
# 处理至当前队列 idle 后退出；不是常驻 daemon
uv run --directory backend python -m wizzy.learning
```

输出仅为 `applied` / `obsolete` / `invalidated` / `idle` 状态，不输出学生内容/标识符。消费者直接读取数据库私有持久事件；无需将改判事件再投递到 Redis，也不调用模型或结算额度。异常事务回滚，排查后重跑；不能通过删回执/事件或改管理角色绕过失败。有派生回执/投影时0011拒绝降级，须另行设计保留证据的回退方案。

联调顺序：
1. 用授权监护人基于最新双版本确认某题 incorrect，保存 effective_revision。
2. 读取 `GET /api/v1/students/{student_id}/mistakes?limit=20`；消费前列表不展示旧版本，pending_updates 表示授权有效题目中尚无当前版本回执的数量，不是队列事件总数。
3. 运行上述 once/drain 命令；再次读取确认题目进入列表、来源为 guardian 且版本匹配。只处理一条时未必恰好消费该题事件。
4. 改判 correct 或更正 OCR 后，不运行消费者直接读列表，旧错题应立即隐藏；drain 后 pending_updates 清零（没有其他并发更改时）。用儿童会话仅可读绑定学生，监护人撤回授权/同意后不可再读旧内容。
5. 超过 limit 时通过 next_cursor 翻页；limit 1–100，按结果创建时间及 question_id 稳定排序。无收藏、业务过滤、掌握状态和报告联动 API，勿将正式客户端或原型标为已联调。

```bash
uv run --directory backend pytest -q tests/test_mistakes.py tests/test_mistake_migration.py tests/test_question_migration.py tests/test_verdict_migration.py tests/test_bootstrap.py --tb=short
uv run --directory backend pytest -q --tb=short
uv run --directory backend ruff check . --output-format concise
```

0011 五模块专项 **51 passed，1 warning，19.30s**。全量 `uv run --directory backend pytest -q --tb=short`：**389 passed，1 warning，93.32s**；`uv run --directory backend ruff check . --output-format concise`：**All checks passed!**。既有 Starlette TestClient/httpx 警告保留；测试使用随机隔离库/角色与专属队列。未部署常驻消费或 scheduler，未验收5分钟生产一致性、真实模型/账单或正式三端；本轮未重跑前端/API启动。人工改判不构成模型准确率金标准。完整记录见 [实施台账](plans/14-development-status.md)。

## 订正记录与监护人核对联调（2026-10-25，迁移0012）

**前提：**维护者备份并审阅0012后按上文升级；不要在仍为0006的共享开发库直接试调用。本轮仅在隔离库验证，未自动迁移开发库或启动API。完整字段、上限与错误码见 [数据模型/API](architecture/06-data-model-and-api.md)。

1. 有权监护人读取当前题目并确认incorrect，保存最新effective_revision；无需等待0011错题消费者。使用对应儿童或有权监护人会话，不得使用管理数据库凭据模拟业务授权。
2. 生成一次性操作UUID，作为 `Idempotency-Key`，POST `/api/v1/questions/{question_id}/correction-attempts`，携带 `expected_effective_revision` 与 `student_answer`；图片则先经现有上传/完成清洗，再以media_id替代文字。不能同时提交两者。同一次网络重试保留同键同体，新的作答才换键。
3. 确认201且初始pending_review/unverified、judgment_revision=0；GET同路径读取历史。分页limit1–100，后续请求只透传next_cursor，不自行解析或构造；旧作答不得被再次提交覆盖。
4. 有权监护人读取该尝试版本，POST `.../{attempt_id}/judgments`，传新的操作UUID及 `expected_judgment_revision/verdict/reason`；只允许correct/incorrect/pending_review。儿童写403；并发CAS冲突409后刷新核对，不能盲改版本自动覆盖。
5. 同键同体重放不追加记录/审计；同键异体409。重放返回最新尝试投影，并非首次请求响应快照。OCR或原题改判后旧尝试标source_is_current=false且禁止新核对；旧记录只在实时授权仍有效时可读/重放，撤回后不能复活内容。
6. 核对原题/原始作业结果、有效版本、改判事件与额度未被订正改变。人工核对不直接代表掌握、报告刷新或模型正确率。前端若显示“已掌握/AI自动判分”，不属于当前API能力。

```bash
uv run --directory backend pytest -q tests/test_correction_attempts.py tests/test_correction_migration.py --tb=short
uv run --directory backend pytest -q --tb=short
uv run --directory backend ruff check . --output-format concise
```

专项 **47 passed，1 warning，13.45s**；Ruff **All checks passed!**。全量 `uv run --directory backend pytest -q --tb=short`：**436 passed，1 warning，109.14s**。0012有任意订正/核对历史即拒绝降级，不允许通过删除历史绕过；升级故障事务回滚在隔离库验证。既有Starlette TestClient/httpx警告保留，未重跑前端/API启动、无真实模型/账单或三端联调；详细失败及复验记录见 [实施台账](plans/14-development-status.md)。

## 首步复习工作台联调（2026-10-25，迁移0013）

**前提：**维护者先备份并审阅0013，再按上文迁移；本轮只使用随机隔离PostgreSQL库/受限角色验证，未迁移或重读共享开发库、未启动API。共享库仅保留上次0006记录，不能把代码head当部署版本。字段与错误码详见 [数据模型/API](architecture/06-data-model-and-api.md)，完整切片边界见 [实施台账](plans/14-development-status.md)。

1. 用有权监护人会话读取当前题目，确认guardian/incorrect；POST `/api/v1/questions/{question_id}/review-tasks`，body为 `{"expected_effective_revision":2}`（以实际读取版本为准）。自然唯一首步任务，不需操作键，重复调用不重复审计；D取当前题文最早监护人确认错误的上海日期，初始到期D+1。
2. 监护人或绑定儿童GET `/api/v1/students/{student_id}/review-tasks?limit=20&due_only=true`，列表稳定游标分页且只含当前有效来源；due_only按上海日期判断，仍可能含awaiting_review，不等于待作答数量。GET `/api/v1/review-tasks/{task_id}`可恢复草稿与历史。
3. **绑定儿童**PATCH `/api/v1/review-tasks/{task_id}/draft`，例如 `{"expected_draft_revision":0,"student_answer":"重新列式，得到20"}`；使用实际draft.revision，成功200并revision+1。允许空串清空；版本冲突409先刷新，不盲目覆盖。
4. 儿童POST `/api/v1/review-tasks/{task_id}/attempts`，带新UUID `Idempotency-Key`，例如 `{"expected_draft_revision":1,"outcome":"submitted"}`，必须使用保存后最新版本。201保存当前草稿快照，revision再+1，submitted进入awaiting_review；needs_help/skipped保持open且保留草稿。三者都不算完成/掌握；当前没有复习核对、答案解锁或后续步数推进接口。
5. **监护人**可在任务仍open且来源当前时POST `/api/v1/review-tasks/{task_id}/reschedules`，带新UUID键，例如 `{"expected_schedule_revision":0,"due_date":"2026-10-28","reason":"调整本周学习安排"}`。允许过去日期；成功201并schedule_revision+1，历史保留原/新日期。监护人不能代儿童写草稿或提交，儿童不能改期/建任务。
6. 同键同体重试返回最新投影而非首次快照；同键异体409，且仍复核实时授权。题文/有效判定变化后旧源禁止新写但保留历史与原操作重放；旧源不在学生列表出现。任务ID读/重放在撤回同意后404，学生列表同意检查403；撤回后不允许借重放取内容。

只验证后端首步记录，不含图片复习、自动评估、D+3/7/14、跨日独立成功/掌握、周月共享、报告或正式三端接入。有任何任务/尝试/改期历史均拒绝降级，不得删除历史绕过保护；升级故障整体回滚已在隔离库验证。

### 0013 本轮实测结果

```bash
uv run --directory backend pytest -q tests/test_review_workbench.py tests/test_review_migration.py --tb=short
uv run --directory backend pytest -q --tb=short
uv run --directory backend ruff check . --output-format concise
```

专项 **49 passed，1 warning，14.81s**；全量 **485 passed，1 warning，124.59s**；Ruff **All checks passed!**。44项行为测试与5项独立库迁移测试覆盖CAS/重放/实时授权/源变化/审计回滚，以及空降级重升、重复升级、有历史拒降级和升级故障事务回滚。保留既有Starlette TestClient/httpx弃用警告；这些是隔离后端证据，不是共享库升级、正式三端联调或T08整体验收。

## 课表核心联调（2026-10-25，迁移0014）

**前提：**维护者先备份并审阅0014后自行迁移。本轮只在随机隔离PostgreSQL库测试，未迁移/重读共享开发库；其上次0006记录不是本轮核验的部署版本。正式三端原型未接入以下API。详细字段与错误码见 [课表契约](architecture/06-data-model-and-api.md)。

1. 使用有权监护人会话POST `/api/v1/students/{student_id}/terms`，例如 `{"name":"2026秋季","start_date":"2026-09-02","end_date":"2027-01-31","week_starts_on":1}`；201返回学期id，revision/template_revision均为0。GET相同路径可按limit/cursor列学期。
2. PUT `/api/v1/timetables/{term_id}`，提交expected_revision=0、完整slots数组和默认false的confirm_conflicts；每个slot自行生成稳定UUID，字段见契约。成功200后保存响应中的term.revision，后续编辑不得重新生成已有slot id。
3. GET `/api/v1/timetables/{term_id}/instances?start_date=2026-09-01&end_date=2026-09-30`；查询含首尾最多93天。日期仅为演示，不依赖当前日期；固定上海时区，学期首日所在周为1，按week_starts_on划分周界。
4. POST `/api/v1/lesson-overrides`，带term_id、最新expected_revision、slot_id、source_date、action和reason。move需同学期target_date与完整replacement；cancel/restore不得带替换字段。跨周调课仍锚定原source_date。201返回详情和递增版本。
5. 409 schedule_conflict时展示error.field_errors中的日期/实例/原因，由用户明确确认后才以confirm_conflicts=true重交；revision_conflict则先刷新。模板与覆盖共用版本，不可各维护一套CAS；即使只有恢复动作也可能触发新冲突。
6. 有效覆盖关联slot不能静默改/删模板，需先restore；每次写入后GET详情核对slots/overrides/conflicts。切换绑定学生后重读，不复用旧学期缓存。儿童只读，撤回同意或学生授权后拒绝访问。

当前无学期编辑/删除或写入操作键幂等接口；模板重试依赖CAS，创建丢响应先查学期列表，不盲目重复创建。未实现截图导入、教材/预习联动、提醒或正式三端接入。验证命令在工作区根目录运行：

```bash
uv run --directory backend pytest tests/test_timetables.py tests/test_timetable_migration.py -q
uv run --directory backend pytest -q
uv run --directory backend ruff check src tests migrations
```

本轮隔离库验证：课表专项 **49 passed，1 warning，8.85s**（44项行为、5项迁移）；全量 **534 passed，1 warning，127.51s**，退出码0；Ruff **All checks passed!**。警告为既有Starlette TestClient/httpx弃用提示；这些结果不代表共享库已升级或三端已联调。

## 周/月报告与共享复习计划联调（2026-10-26，0016–0017）

只在随机隔离PostgreSQL库验证；未迁移/重读共享开发库。接入前先备份并由维护者审阅迁移。以下使用已有监护人Bearer会话和实际学生ID，不把令牌写进URL、仓库或截图；正式家庭UI尚未接入。

1. POST `/api/v1/reports`，JSON为`{"student_id":"实际UUID","type":"weekly","period_start":"2026-10-19"}`。周报必须周一，月报必须月首，周期固定上海时间；201返回报告，重复相同来源复用同版本，不重复审计。
2. GET `/api/v1/reports?student_id=实际UUID&type=weekly&limit=20`，使用响应next_cursor继续分页。GET `/api/v1/reports/{id}`读取汇总，GET `/api/v1/reports/{id}/sources?limit=20`下钻逐项来源。儿童不可读取报告。
3. 原始correct+incorrect才入正确率分母，空分母为null；订正与复习独立。当前复习提交仅待核对，完成数0；薄弱点证据不足不显示虚构知识点。保留machine/guardian来源，人工判定不是模型金标准。
4. 改判、OCR或核对后重读报告可见stale；POST `/api/v1/reports/{id}/rebuild`生成/复用当前来源版本，保留旧报告。不提供后台自动刷新承诺。
5. POST `/api/v1/review-plans`，带新的UUID `Idempotency-Key`。JSON为`{"student_id":"实际UUID","type":"weekly","period_start":"2026-10-19","max_questions":20,"max_minutes":20,"minutes_per_question":2}`，可加当前未过期report_id。默认最多10题，是20分钟/每题估计2分钟的双限结果；时长不是测量。
6. GET `/api/v1/review-plans/{id}`。监护人与绑定儿童读取同一个任务集合，任务状态实时读取，不复制作答。空集合合法；已提交任务仍待核对，不等于完成或掌握。
7. 相同幂等键必须携带完全相同输入；409 idempotency_conflict不盲重试。409 source_conflict先刷新/重建报告，再由用户确认新请求。改判/OCR/改期后计划stale，任务接口仍执行自身版本/授权检查；儿童不能生成计划。

验证命令（只对隔离库运行迁移）：

```bash
uv run --directory backend pytest -q tests/test_learning_reports.py tests/test_report_migration.py tests/test_review_plans.py tests/test_review_plan_migration.py --tb=short
WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest -q --tb=short
uv run --directory backend ruff check . --output-format concise
```

迁移往返/ACL/历史降级保护、并发幂等、撤回等待竞态与审计回滚均须保留。最新结果见[工程台账](plans/14-development-status.md)；不把隔离库通过等同于共享库升级、家庭正式UI或整体验收。

## 日常管理

```bash
docker compose logs --tail=100 postgres redis
docker compose stop postgres redis
docker compose start postgres redis
# 移除容器和网络，但保留两个命名数据卷
docker compose down
# 重新创建容器，数据卷保留
docker compose up -d --wait postgres redis
# 手动逻辑备份（包含本地开发数据，请妥善保存）
docker compose exec -T postgres pg_dump -U wizzy -d wizzy -Fc > /tmp/wizzy-dev.dump
```

停服务前先停止宿主机 API/dispatcher。**不要执行 `docker compose down -v`，除非明确需要永久删除开发数据库和队列数据。** 持久化卷不是备份。

`.env` 中的初始化管理密码只对首次创建空 PostgreSQL 卷生效；已有数据库改密码时，应先在 `psql` 中运行 `\password wizzy`，再同步修改私有配置。修改主版本前必须先备份并制定迁移方案，不能直接用新主版本镜像启动旧数据目录。

## 周期报告与共享复习计划复验（2026-10-26，0016–0017）

周/月报告已支持不可变快照、分母与来源下钻、过期识别及显式重建；共享计划引用唯一 review_task，按题量/预计时长双限生成，可从当前报告创建，不复制学习完成事件。不是自动评估、掌握或正式家庭端。

包含真实 API 浏览器链的全量命令 `WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest -q --tb=short`：**686 passed，1 warning，196.04s**。上一轮唯一失败是 bootstrap 测试硬编码0016，改为0017后重跑；没有修改生产迁移逻辑。未迁移/重读共享开发库、未重置凭据，开发库上次记录仍为0006。

### 教材正文真实浏览器联调（0028）

`WIZZY_BROWSER_TESTS=1 PLAYWRIGHT_CHANNEL=chrome backend/.venv/bin/pytest backend/tests/test_textbook_content_web.py -q --tb=short` 已在2026-10-26通过（1 passed、1 warning，8.80s）。测试自建隔离PostgreSQL与受限角色，执行真实构建前端/API/worker/数据库链路，覆盖独立权利审核、3页PDF、逐页校核/章节、五版历史、独立抽检发布、TXT下载和撤回。扫描器为显式`CleanForTest`，不能替代真实ClamAV或试点验收；不会升级共享开发库。日志：`/tmp/wizzy-textbook-real-browser.log`。
