# 三科作业功能上线验收

验收时间：2026-11-14T09:52:00+08:00

已使用正式域名 http://wizzy.refineview.com/ 登录家庭账号，通过真实浏览器选图、确认并提交。三科依次批量上传全部 14 张图片，共产生 6 条成功批改结果。

家庭：三科作业验收家庭；学生：三科验收同学（6 年级）。
账号：homework-test-20261114@refineview.com。密码保存在本地 `.local/homework-acceptance/account.json`，文件权限 0600。

每科均调用后台排序第一的模型 `gpt-6.1-sol`（优先级 1），依次执行“错题原因与正确答案”（优先级 10）与“逐题对错判定”（优先级 20）。数字越小越优先，提示词仅选择已启用项。

| 学科 | 图片数 | 作业编号 | 第一条结果字数 | 第二条结果字数 | 状态 |
|---|---:|---|---:|---:|---|
| 英语 | 5 | `ad213cf0-5e8d-43b8-b649-5bcee341aeca` | 2054 | 1741 | 完成 |
| 数学 | 4 | `7daa0d8e-2093-48c7-8285-dfc735133044` | 1783 | 809 | 完成 |
| 语文 | 5 | `0e6101d0-e9b2-4267-a597-37929f0a0420` | 690 | 423 | 完成 |

完整模型响应 JSON 与可显示文本均已保存至数据库。页面六条内容的 SHA256 与数据库相同；最终发布、服务重启及页面刷新后，内容仍保持一致。作业列表可以按学科、日期和状态查询，详情可查看原图、批改结果、模型与提示词优先级。

最终执行 `./build-deploy.sh 0` 成功（退出码 0），生产健康检查通过，常驻批改 worker 为 active。已修复正式域名登录的 Origin 校验，以及新批改结果额外请求旧接口导致的 409。最终三份详情的接口请求均为 200，浏览器控制台 0 错误、0 警告。

验证结果：后端 1440 项通过、5 项按配置跳过；另行开启的家庭端真实 API 浏览器测试 1 项通过。最终前端类型检查、44 项单元测试、17 项浏览器回归与 H5 构建全部通过。

结果截图：

- [英语两条批改结果](/Users/Admin1/workspace/refineview/wizzy/output/playwright/english-results.png)
- [数学两条批改结果](/Users/Admin1/workspace/refineview/wizzy/output/playwright/math-results.png)
- [语文两条批改结果](/Users/Admin1/workspace/refineview/wizzy/output/playwright/chinese-results.png)

验收证据：

- [最终生产数据库检查](/Users/Admin1/workspace/refineview/wizzy/.local/homework-acceptance/production-final.json)
- [最终正式发布日志](/Users/Admin1/workspace/refineview/wizzy/.local/homework-acceptance/deploy-final.log)
- [最终页面接口请求](/Users/Admin1/workspace/refineview/wizzy/.local/homework-acceptance/network-final.txt)
- [后端回归日志](/Users/Admin1/workspace/refineview/wizzy/.local/homework-acceptance/backend-all-tests-verified.log)
- [最终浏览器回归日志](/Users/Admin1/workspace/refineview/wizzy/.local/homework-acceptance/family-browser-final.log)

图片清单：

英语：`126a34acf6649ccc5851079371edc188.jpg`、`19fa44d546214bae06b3ece1c0a7b455.jpg`、`a42a25a6ff13d967aa4cdf2d037104b0.jpg`、`ce8560bfa39c304b0f60c5c37541d881.jpg`、`f6ebeda955b6e4d85a8b1303d431c913.jpg`

数学：`007668a44d45fc6a2d39224708906625.jpg`、`2bc6e4d70ce95c98f894da2b509b6ebd.jpg`、`307a554750c8e0eabc9fcaf6e14290e2.jpg`、`4fdec174122ebdb1c1c3ee061c2f1929.jpg`

语文：`1664a539f386744904ffe3b98297de90.jpg`、`286e07aec1df72a6715b4b4940ff3986.jpg`、`bb871c453ce17ef90262728dcd076b1b.jpg`、`d6de90ed399ba659d3387c4563342d15.jpg`、`ea2909006d46b1a597b0a4de51327170.jpg`

