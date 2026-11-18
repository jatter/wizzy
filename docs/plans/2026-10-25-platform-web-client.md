# 平台正式 Web 客户端（0015 对应切片）

> 本文保留0015初始计划和当时验收范围。0021已新增角色/启停、独立升权审批、会话撤销及最后管理员保护，详见[账号治理增量](2026-10-26-account-governance.md)；最新交付状态以[工程台账](14-development-status.md)为准。

## 初始范围与假设（0015）

- 原型保留在 `docs/prototype`；正式客户端独立位于 `frontend/platform`，不把演示数据当作生产数据。
- 延续已决策的 Vue 3 + TypeScript + Vite + Element Plus，不引入完整后台模板或第二套设计系统。
- 只接通平台身份七接口：登录、me、退出、退出全部会话、角色、账号只读、审计只读。
- 账号创建/停用/角色写入等尚无高危审批保护，不提供可点击的假操作；家庭端和其他平台业务不在本切片中。
- 使用同源 `/api/v1/admin`，本地代理到回环 API，不放宽后端 CORS，不迁移共享开发库。

## 设计

- 色板：墨绿 `#173d35`、青绿 `#177b65`、浅青 `#eaf3ef`、底色 `#f4f6f8`、正文 `#253c35`、弱化文字 `#697c75`。
- 字体：中文系统苹方/微软雅黑；标题与正文同族、通过字重区分；标识符使用系统等宽字体。
- 布局：桌面左导航 + 顶部会话栏 + 主工作区；移动端折叠为顶部导航和可横向滚动的数据表。
- 识别元素：品牌小知的「知」字标与安全工作区标识。避免虚构运营指标和装饰性大图。
- 状态：加载、空结果、错误重试、无权限、会话失效、游标前后页；退出失败明确区分本地清除与服务端撤销。

## 检查清单（证据驱动）

- [x] 阅读原有契约与测试，API 客户端先 RED 后 GREEN；补充回归后共11项。
- [x] 页面交互及异常态回归通过（14项，模拟API）。
- [x] TypeScript 检查与生产构建通过。
- [x] Chrome 桌面/390px 响应式、键盘和错误态检查。
- [x] 隔离 PostgreSQL → 真实 FastAPI → 同源代理 → 浏览器联调通过。
- [x] 后端完整回归与 Ruff 通过。
- [x] 同步本地开发、功能列表、选型与开发台账，记录未完成边界。

## 安全约束

令牌只保存在私有内存，不写 URL、浏览器存储、Cookie 或日志。401 清会话；403 保留会话但清页面旧数据。异步响应带会话版本防止旧请求污染新登录。后端始终执行最终授权。页面重新获得焦点时重验身份与权限；到期定时清理。测试凭据仅在隔离库生成，不落盘、不通过命令行参数传递。

## 2026-10-25 收尾实测

从项目根目录执行：

```bash
npm run build --prefix frontend/platform
npm test --prefix frontend/platform
PLAYWRIGHT_CHANNEL=chrome npm run test:browser --prefix frontend/platform
WIZZY_BROWSER_TESTS=1 uv run --directory backend pytest -q --tb=short
uv run --directory backend ruff check . --output-format concise
```

- TypeScript与Vite生产构建通过（1.72s）；API客户端 **11 passed**，Chrome交互 **14 passed（5.9s）**。覆盖键盘登录、无持久令牌、桌面/390px、分页/空态/重试、401/403、失焦后重验、退出/到期与迟到响应隔离。
- 后端含真实浏览器联调完整回归 **600 passed，1 warning（146.90s）**；Ruff **All checks passed!**。`tests/test_platform_web.py` 使用随机隔离PostgreSQL、真实FastAPI、同源Vite代理及Chrome，不把模拟API截图当作真实链路证据。默认未设置`WIZZY_BROWSER_TESTS=1`时该用例跳过。
- 保留Starlette/httpx弃用警告及Node DEP0205/NO_COLOR工具链警告，不视为业务失败，也不宣称已消除。
- 两个前端工作区Vite均升级至7.3.7、分别重新构建及审计；[正式端证据](../evidence/platform-web-2026-10-25/README.md)、[原型证据](../evidence/prototype-ui-2026-10-25/README.md)。0个已知依赖漏洞不等于生产安全认证。
- 首次完整回归发现bootstrap测试的旧TOTP码会跨30秒窗口失效。仅固定测试模块时钟并补充过期拒绝测试，不放宽生产MFA窗口；专项16项通过后再次运行完整回归，以上600项为修复后结果。

以下为0015当轮历史记录：未迁移或重读共享开发库、未更改真实凭据。当时代码head为0015，不代表部署版本。此切片不提供在线邀请、角色写入、停用、跨人撤销、最后安全管理员保护或高危双审；学校、学生/家长、教材、模型治理和家庭正式客户端仍不能按整票验收。完整缺口见[工程台账](14-development-status.md)。
