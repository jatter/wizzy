# 小知正式平台 Web 客户端

Vue 3 + TypeScript + Vite + Element Plus，独立于演示原型。当前接平台身份接口：密码与图形验证码登录、本人/退出/退出全部、角色、只读账号和审计；不提供未实现的在线开户。

- 开发、构建、测试、同源代理与首位初始化：[本地开发说明](../../docs/local-development.md)。
- 范围与安全约束：[客户端计划](../../docs/plans/2026-10-25-platform-web-client.md)。
- 实测记录：[工程台账](../../docs/plans/14-development-status.md)。
- 模拟数据截图及依赖审计：[证据说明](../../docs/evidence/platform-web-2026-10-25/README.md)。

不要把密码/MFA/令牌写入前端环境变量或浏览器存储，不要把 `vite preview` 当作生产服务器。
