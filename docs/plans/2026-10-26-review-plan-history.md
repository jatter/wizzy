# 家庭端复习计划历史（0033）

补齐WZ-09已有计划在刷新后无入口的问题。沿用0017周/月计划和共享复习任务，不生成新任务或完成统计。

1. 增加0033只读受限能力和GET `/api/v1/review-plans`。按学生、可选周/月筛选，按创建时间及UUID倒序游标分页；当前授权与deletion_epoch失效立即隐藏。摘要仅含周期、创建时间、题量、估算时长、来源报告ID，题干/草稿由详情按权限读取。验证：隔离PostgreSQL授权、稳定分页、空列表、撤回及迁移权限/回滚。
2. 正式家庭学习页展示历史、筛选、分页、空态、详情及共享任务入口；家长和已配对学生均可读取。复用既有视觉与复习编辑器。切学生/会话/卸载隔离迟到响应，刷新失败清除旧详情；估算时长与待核验状态明确显示。验证：客户端、类型检查、H5/小程序构建与浏览器交互/390px。
3. 运行相关回归并更新架构、功能列表、台账；检查实际结果后记录。仅随机隔离库迁移测试，不迁移共享库，不停止8080原型服务。

## 验证结果

2026-10-26完成0033切片。后端全量1377 passed、5 skipped、1 warning（340.22s）；新增历史专项28 passed；家庭客户端37 passed；浏览器9 passed（6.9s）；构建产物到真实API及隔离PostgreSQL浏览器1 passed（12.82s）。全后端Ruff、vue-tsc、H5和微信构建均通过；390px历史/详情/任务入口无横向溢出。

第一次全量中验证码夹具误将四字符答案与PNG base64做子串比较，随机命中导致1 error。已改为校验响应字段集合及ID不等于答案；验证码/教材85项复验通过，再运行全量得到上述1377 passed。生产认证未改。

日志：

- 后端专项/全量：`/tmp/wizzy-plans-backend-target.log`、`/tmp/wizzy-plans-backend-final.log`。
- 家庭客户端/类型/构建：`/tmp/wizzy-plans-family-client.log`、`/tmp/wizzy-plans-family-types.log`、`/tmp/wizzy-plans-family-h5.log`、`/tmp/wizzy-plans-family-mp.log`。
- 浏览器/真实联调/复验/Ruff：`/tmp/wizzy-plans-family-browser.log`、`/tmp/wizzy-plans-family-real-api.log`、`/tmp/wizzy-plans-captcha-recheck.log`、`/tmp/wizzy-plans-lint-final.log`。

仅随机隔离库验证，未迁移共享开发库。未增加自动复习评估、跨日掌握或D+3/7/14；submitted不代表掌握。5项skip为显式开启的浏览器测试；本切片真实家庭浏览器已单独通过。完整项目仍未完成。
