# 教材 PDF 检查与文本提取组件

日期：2026-10-26。接续0026隔离接收，FR-A11/WZ-11的处理组件切片；本次不新增数据库迁移，head仍为0026。

后续更新：0027已接通持久处理任务/API/正式平台，并补充扫描前后病毒库时效校验（72小时、未来容差5分钟）及显式clamd时区；见[0027实施记录](2026-10-26-textbook-processing-jobs.md)。下文保留独立组件当时的范围和验证记录，真实引擎/自动更新/部署仍待验收。

## 范围与假设

- API接收继续保持quarantined/not_scanned/content_available=false。先实现供后续任务消费者调用的真实扫描适配器及独立解析进程，不在Web请求内解析PDF。
- 显式配置可信私网ClamAV；按官方VERSION/INSTREAM协议发送已验证大小和SHA-256的私有副本，仅精确clean回复允许提取。记录引擎、病毒库版本/日期与检查时间，扫描前后版本变化则重试。无服务、超时、未知/错误响应或超限均不产生可用草稿，不设跳过扫描开关。ClamAV协议没有认证，不允许连接不可信公网端点；病毒库更新与新鲜度门禁仍属于后续运行配置。
- 使用pypdf提取嵌入文本，子进程40秒墙钟超时/20秒CPU限制，1 GiB地址空间限制、8 MiB结果文件上限、500页、单页10万/整份100万字符。Linux用于正式运行并设置1 GiB地址空间上限；macOS拒绝该地址空间设置，本地运行不设置内存上限，其余限制保留。不能将进程边界宣称为完整操作系统沙箱，也不能把macOS运行等同于Linux内存隔离验收。
- 严格解析拒绝损坏/加密PDF、检测到的活动动作/脚本/附件/XFA等特征。提取结果保留1起算源页码，所有页无可提取文本返回no_text；混合页面列出pages_without_text，可能包含空白页，不等同已完成OCR或断定所有这些页必须OCR。
- 所有文本是requires_review=true、ocr_performed=false、content_available=false的内存草稿。隔离文件不修改，临时副本与结果清理；没有预览/下载/发布或检索权限扩张。

## 验证计划

1. 本地协议测试服务验证真实socket分块、精确回复、错误/超时/版本变化；明确这是协议测试，不是ClamAV引擎查毒验收。
2. 真实有效PDF子进程测试验证页码/正文、空页、加密/活动内容/损坏/超限拒绝；验证摘要不一致不进入扫描、扫描失败不进入解析、文件权限与清理、环境隔离和错误不泄漏正文。
3. 依赖锁、Ruff和后端回归；本切片未改前端，不重复前端构建。后续任务持久化/租约与受限worker能力、授权复核、草稿保存/校核、OCR及发布另行实现，不能将组件存在写成完整业务已接通。

## 实施记录

- 开始基线：0026完整后端1104 passed（302.39s），正式平台91项浏览器通过；共享开发库未升级。
- 官方依据：ClamAV仓库`docs/man/clamd.8.in`已读取VERSION/INSTREAM/空字符分隔及4字节网络序块；pypdf官方extract-text文档已核对内存风险与OCR边界。依赖通过uv锁定pypdf 6.21.2，范围>=6,<7。

- 修复macOS `RLIMIT_AS`设置失败导致提取前退出；新增平台分支和限制失败不可忽略验证。修复ClamAV回复4096字节正文加NUL的上限边界。活动脚本测试直接构造PDF字典，不使用已弃用的测试API。
- 2026-10-26：`backend/.venv/bin/ruff check backend`退出0，All checks passed；`WIZZY_BROWSER_TESTS=1 PLAYWRIGHT_CHANNEL=chrome backend/.venv/bin/pytest -q backend/tests --tb=short`退出0，**1176 passed, 1 warning in 292.48s**。日志`/tmp/wizzy-backend-pdf-processing-regression.log`。警告为既有Starlette TestClient/httpx弃用提示。
- 该结果验证0026和独立处理组件；此时没有任务持久化/API/UI接线，也没有真实ClamAV引擎/病毒库验收。
