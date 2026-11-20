# 普通作业与听写图片批改

家庭端上传支持“自动识别 / 普通作业 / 单词听写”，默认自动识别，可选填最多 8000 字符的参考词表。多页参考应标明图片顺序及对应题号或列位置；批改类型是识别提示，混合页仍逐项识别。

无题号词表使用“左列第3项”等位置编号，短语作为完整一项。结果保留原始错拼，分别提供检查依据、订正建议和答案。

没有可对应的听写目标时，仅检查英语拼写。“拼写未见错误”不代表听写正确，不判断漏写、顺序或是否听对；例如没有目标时不能把 weather 改为 whether。字迹、目标或对应关系不确定时展示“待核对”。清楚的拼写错误可进入错题库，仅通过拼写检查的结果不会增加掌握次数。

## 接口与兼容

- `POST /api/v1/submissions` 新增可选 `activity_type`（auto / exercise / dictation）及 `reference_text`（默认空）。详情返回保存的设置。
- 上传回执丢失后保留相同请求体和幂等键；重新批改沿用提交时的类型、参考词表。
- 单项结果新增 `item_type`（exercise / word / phrase）和 `grading_basis`（task_reference / spelling_only / none）。历史普通作业结果按 exercise / task_reference 解析；词条必须显式声明依据。
- 保留原有两次顺序批改及原始响应。无效 JSON、字段结构、页码覆盖和重复编号分别给出诊断，不用截取 JSON 或自动追加付费重试掩盖错误。
- 图片和参考词表作为数据处理，其中的文字不构成任务指令。

## 发布与核验

本次仅修改本地代码，并在自动创建、销毁的隔离 PostgreSQL 库验证。未迁移共享开发库或生产库，未调用付费模型，尚未部署。

发布时先备份数据库，在现有受控迁移环境升级到 `0044_dictation_grading`，再更新并重启 API、批改 Worker，最后发布家庭端 H5 / 小程序。开发环境初始化命令和凭据边界沿用 `docs/local-development.md`。新后端需要新迁移；旧客户端可以继续省略新增字段。

迁移只更新未被管理员编辑、且内容仍为原始种子的提示词；保留自定义提示词、启用状态和优先级。所有调用都会追加新版统一输出契约。已有非默认听写设置时拒绝降级，避免丢失参考材料。

在仓库根运行后端专项测试（夹具自建隔离测试库）：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m pytest backend/tests/test_homework_results.py backend/tests/test_mistake_bank.py backend/tests/test_submissions.py backend/tests/test_homework_grading.py backend/tests/test_grading_prompts.py backend/tests/test_dictation_migration.py -q
```

在 `frontend/family` 运行 `npm test`、`npm run type-check`、`PLAYWRIGHT_CHANNEL=chrome npm run test:browser -- tests/homework-pages.spec.ts`、`npm run build:h5` 和 `npm run build:mp-weixin`。浏览器测试使用本机已有 Chrome。

上线验收还需用实际配置模型批改真实样本：普通作业、无题号多列单词、完整短语、有参考且写成另一合法单词、无参考的合法单词、模糊字迹、混合页。自动测试使用模拟供应商和固定结果，验证流程与边界，不代表真实手写识别准确率。
