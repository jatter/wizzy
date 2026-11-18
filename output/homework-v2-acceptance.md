# 作业上传与逐图详情验收

验收日期：2026-11-14（北京时间）。正式站： http://wizzy.refineview.com/ 。

## 已发布功能

- 拍作业与作业记录分为独立界面；上传不再选择学科，也无需额外勾选完成确认。每批可上传 1–9 张，语文、数学、英语可混合，后台逐图识别学科。
- 作业记录使用日期标签：今天、昨天、已有记录的更早日期。日期及当天提交均倒序排列，不再显示日期/学科/状态筛选表单。
- 详情按第 1 张、第 2 张等切换；逐张展示原图、对错统计、原作答、教学用解题思路、步骤和正确答案。原图支持放大。
- 未作答、题目缺失、模糊或缺少听力材料分别标记，不算作错误。统计由已验证的逐题 verdict 计算。
- 按后台启用提示词的最高两项顺序调用最高优先级模型；两份返回原文和完整响应保存入库。页面采用最后一次成功、完整的逐图结果，可展开查看两份原文。

## 正式站真实验收

使用已建立的“三科作业验收家庭”，浏览器模拟选图、点击提交，无直接写入数据库替代用户提交。

本次完成记录：`05dbb8f2-e066-46ec-8760-d72e12d7afc2`，2026-11-14 18:22:08 提交。

上传顺序：

1. 数学：`imgs/007668a44d45fc6a2d39224708906625.jpg`
2. 英语：`imgs/126a34acf6649ccc5851079371edc188.jpg`
3. 语文：`imgs/1664a539f386744904ffe3b98297de90.jpg`

模型：`gpt-6.1-sol`，优先级 1。提示词依次为“错题原因与正确答案”（10）、“逐题对错判定”（20）。两次均 succeeded，作业 completed，任务 succeeded。

第二轮展示结果（模型判定，非人工判分）：

| 图片 | 学科 | 对 | 错 | 未作答 | 待核对 | 识别小题 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 数学 | 10 | 3 | 3 | 0 | 16 |
| 2 | 英语 | 12 | 1 | 0 | 12 | 25 |
| 3 | 语文 | 10 | 1 | 0 | 0 | 11 |

共核对 52 道题：页面的题号、原作答、解题思路、步骤、答案逐项匹配第二轮已保存 JSON。三张原图均成功加载（2448×3264）。两次模型的判断存在差异，均保留原文；页面明确使用最后一次成功结果。

两份原文的页面/数据库 SHA256 一致，完整响应中的 content 也与保存原文一致：

- 第一次，10135 字符：`fda6dad6c1799263f0d7235f1c6fb8ffaa828255dbd2431e97cf2f6331d0a7d9`
- 第二次，9163 字符：`2ce7bbf15e598b2b0761d460495be63cb7929665edc9b6a30561502ebeb17d70`

执行最终发布后重新加载正式站、恢复账号、从日期列表重新打开该记录，再次核对全部内容通过；数据库输出未因发布变化。390px 手机与 1440px 桌面无横向溢出。最终页面控制台无错误。

## 发现并修复的问题

首次真实混合图片验收中，上游返回了三页内容但漏写 JSON 外层结束括号。原文与完整响应已保留，作业明确显示失败，未伪造统计。请求已启用 `response_format: {"type":"json_object"}`，再次真实混合验收两轮均成功。达到模型长度上限的返回原文也保留入库，避免丢失诊断内容。

长提示文字的行内边框影响可读性，已修为块状提示并再次发布、检查正式站。

历史旧记录保留原图与原始批改文本；没有逐图结构的数据不会臆造对错总数。此次功能不追溯调用模型重算旧作业。

## 测试与发布

- 完整后端回归：1452 passed，5 skipped（按配置跳过的浏览器集成）。
- 最后一次后端改动相关回归：30 passed，包括 JSON 格式请求和长度上限返回保存。
- 前端单元：45 passed；完整浏览器回归：20 passed。
- 真实家庭 API 浏览器集成：1 passed，覆盖上传、重排、旋转裁剪、提交丢失回执的幂等重试、原图像素、日期标签、权限撤销。
- 最后样式改动后：类型检查、H5 构建通过；作业浏览器回归 3 passed（Chrome）。
- Ruff 检查通过。
- 按授权执行 `./build-deploy.sh 0`，最终发布退出码 0。API ready 为 ok，`wizzy-homework.service` active。现有健康字段 ai_provider=unconfigured 属于旧 provider 路径，此次数据库模型管理的批改 worker 已真实执行成功。

## 证据

- 最终发布：`.local/homework-v2/deploy-final.log`
- 后端完整回归：`.local/homework-v2/backend-all.log`
- 最终后端相关回归：`.local/homework-v2/backend-json-mode.log`
- 真实 API 浏览器：`.local/homework-v2/real-api-browser.log`
- 发布前后 DB 快照：`.local/homework-v2/production-current.json`、`production-final.json`
- 发布后逐题/哈希核验：`.local/homework-v2/verified-after-reload.log`
- 上传界面：`output/playwright/homework-v2-capture.png`
- 最终记录列表：`output/playwright/homework-v2-records-final.png`
- 最终桌面原图与结果：`output/playwright/homework-v2-english-final.png`
- 最终手机详情：`output/playwright/homework-v2-mobile-result-final.png`
