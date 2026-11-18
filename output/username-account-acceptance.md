# 家庭用户名登录验收

验收日期：2026-11-14。正式站：http://wizzy.refineview.com/。

- 原验收家庭账号已改为 `liyuhan`，密码已按用户指定修改。密码不写入本报告。
- 登录、注册使用“用户名”，支持普通姓名拼音；新注册密码至少 8 位。旧邮箱账号及 email 请求字段继续兼容，新增 username 请求字段。
- 保留原 users 记录与家庭关系，无数据库结构迁移。修改凭据时撤销了该账号原有家长会话。
- 事务内核对修改前后数据一致：1 条家庭成员关系、1 个学生、5 条原有作业、9 份原有模型返回内容。原文和完整模型响应参与一致性校验。
- 以新用户名及指定密码在正式站浏览器成功登录；`/auth/me` 返回 username=liyuhan、原 user_id=1968d029-2937-4ecd-8402-b3b0ce2c1539。刷新后保持 guardian 登录，原学生档案正常。
- 浏览器重新打开原三科混合完成记录 `05dbb8f2-e066-46ec-8760-d72e12d7afc2`：原图加载成功，数学页 16 道题，对 10、错 3、未作答 3，解题内容保留。验收期间列表出现新增记录，本次未删除或覆盖任何作业。

验证结果：后端账号及会话相关 69 项通过；前端单元 45 项通过；浏览器回归 20 项通过；真实 API 浏览器集成 1 项通过；类型检查、Ruff 和 H5 构建通过。真实 API 测试补齐了默认进入“拍作业”后的显式页面导航。

执行 `./build-deploy.sh 0` 完整发布，最终退出码 0。前两次 SSH 连接中断，最终使用本次任务专用的 SSH 保活参数完成重试，没有修改产品的部署脚本。API ready=ok，作业 worker=active；刷新后的页面控制台无错误。

证据：

- `.local/username-account/backend.log`
- `.local/username-account/frontend-unit.log`
- `.local/username-account/browser.log`
- `.local/username-account/real-api.log`
- `.local/username-account/deploy-final.log`
- `.local/username-account/production-preservation.json`
- `output/playwright/username-login.png`
- `output/playwright/username-preserved-homework.png`
