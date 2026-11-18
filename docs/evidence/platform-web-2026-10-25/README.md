# 正式平台身份工作区：视觉与依赖证据

日期：2026-10-25。来源：`frontend/platform/tests/platform.spec.ts`，本机 Chrome；桌面1280px和手机390px。截图已经人工逐张查看：桌面登录分栏、只读列表/分页，手机导航和数据表内部横向滚动、无文档横向溢出。

**全部截图使用 Playwright 模拟 API 和 `example.test` 虚构账号**，不含真实平台密码、MFA、令牌、学生或家长数据。截图中应用固定页脚“当前界面连接真实API”描述的是正式应用运行模式，在本组UI测试中请求由测试拦截；不能据截图声称连接了真实数据库。真实链路证据独立来自 `backend/tests/test_platform_web.py`，运行随机隔离PostgreSQL、FastAPI及构建产物，不保存临时真实账号截图。

- [桌面登录](desktop-login.png)
- [桌面只读账号](desktop-accounts.png)
- [390px只读账号](mobile-accounts.png)：表格和顶部导航可内部滚动，不代表截图以外的所有机型已测试。
- [npm audit 结果](npm-audit.json)：官方registry，正式平台锁文件升级Vite7.3.7后0个已知漏洞。仅依赖数据库检查，不含原型工程、不代表无安全缺陷；依赖信息会随时间变化。原型工程已另行升级与审计，见 [独立原型证据](../prototype-ui-2026-10-25/README.md)。

实现范围、自动化验证和未完成边界见 [客户端计划](../../plans/2026-10-25-platform-web-client.md) 与 [工程台账](../../plans/14-development-status.md)。本证据不代表三端全业务、真机或生产验收。
