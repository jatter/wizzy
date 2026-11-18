# 平台身份与只读 RBAC 基础（0015）

## 范围与假设

- FR-A06 / FR-A10 的前置切片，不整票验收。平台身份不复用家庭用户/会话，不授予儿童内容权限。
- 离线首次初始化一个安全管理员；无公开注册、邀请、密码找回或 MFA 绕过。在线角色变更、停用、跨人撤销和高危双人审批另行实现。
- 密码 Argon2；TOTP 使用成熟依赖，30 秒、六位、±1 步，数据库行锁原子消耗时间步；密钥以独立 Fernet 配置加密，不写明文。未配置密钥时平台登录关闭，家庭服务不受影响。
- 平台会话固定 30 分钟，令牌只存摘要；每次请求重验状态、凭据版本、权限。账号与审计查询仅安全管理员可用。
- 应用角色只执行固定 search_path 的专用函数，不能直接读写平台表；worker/dispatcher 无平台权限。

## 执行与验证顺序

1. 真实 PostgreSQL / API 测试 RED：独立身份、MFA、重放与并发、实时权限/状态、审计原子性、最小权限。
2. 0015 迁移、服务、离线初始化及 API；专项测试 GREEN。
3. 独立库迁移往返/历史保护/回滚测试；全量 pytest、Ruff。
4. 同步架构、功能列表、台账、追踪、联调及验收文档，记录证据和边界。

## 结果

2026-10-25：0015后端基础切片及离线首次初始化已实现，正式三端未接入；不构成生产安全认证或整票验收。

- 专项 `uv run --directory backend pytest -q tests/test_platform_identity.py tests/test_platform_migration.py tests/test_platform_bootstrap.py --tb=short`：**64 passed，1 warning，13.28s**。
- 全量 `uv run --directory backend pytest -q --tb=short`：**598 passed，1 warning，144.28s**（本次收尾复跑），退出码0；保留既有Starlette TestClient/httpx弃用提示，不升级依赖或屏蔽警告。
- 静态检查 `uv run --directory backend ruff check . --output-format concise`：**All checks passed!**，退出码0。
- 本轮7份Markdown结构校验：**83处相对文件链接存在、34个代码围栏闭合、16个JSON示例可解析**；排除围栏/行内代码中的伪链接，不检查远程地址或标题锚点；0015计划无验证结果占位。

### 已验证行为

- 平台/家庭令牌互不通用，operator无账号/审计读取权限；密码+TOTP、±1步窗口、数据库自身时钟限制、并发重放仅一次、较新步消费后更旧步不可用。
- 30分钟会话到期、本人单次/全部撤销、角色或权限撤回实时生效；登录和已登录请求等待账号锁后重验停用/凭据版本。
- 账号/审计最小投影与分页；403拒绝审计正常提交；成功登录、读取、拒绝、退出/全部退出的审计故障均失败关闭并回滚计数/会话变更。
- 六表FORCE RLS、运行角色无直接CRUD、API专属三个固定search_path函数；隔离库升级/空降级重升、故障回滚和有身份/审计历史拒绝降级。
- 首位管理员规范化email、密码哈希与secret加密存储、初始化码已消费、不创建会话；已有停用账号亦拒绝重建；并发只创建一人、审计失败无半成品、CLI错误不回显秘密，缺显式离线连接时不收集秘密。

### 边界与交付

- 同步[架构/API](../architecture/06-data-model-and-api.md)、[本地离线操作](../local-development.md)、[功能列表](../小知功能列表.md)、[任务追踪](12-backlog-and-traceability.md)、[工程台账](14-development-status.md)及[测试验收记录](../11-test-acceptance-and-operations.md)。
- 未迁移/重读共享开发库，未重置真实凭据；共享库仅保留上次0006记录，代码head0015不代表部署版本。未启动API、构建/修改前端、浏览器/真机或正式三端联调，未调用真实模型。
- 在线邀请/停用/角色写入/跨人撤销、最后安全管理员保护与高危双审、MFA恢复/轮换、工单授权和模型管理/评分发布均待做。FR-A06/FR-A10、WZ-15、T14不整体验收；平台基础身份不扩大儿童内容访问权限。
