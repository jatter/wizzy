# 外部产品与技术参考

> 用途是学习工作流与数据治理，不代表产品选型背书，也不复制品牌视觉；访问日期：2026-10-25。

| 主题 | 官方资料 | 对小知需求的启发 |
|---|---|---|
| 家校协作 | [ClassDojo for Teachers](https://www.classdojo.com/teachers/) | 家庭与学校沟通要有明确受众、角色与组织范围；小知对应家长/学校授权边界与家庭视图。 |
| 模型评测 | [OpenAI Evals](https://developers.openai.com/learn/evals)、[Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices) | 评测应绑定代表性数据集、grader/规则、运行报告并可复现；小知需锁定集、人工金标准、切片指标和证据不足门禁。 |
| PDF/OCR | [Google Document AI OCR](https://docs.cloud.google.com/document-ai/docs/enterprise-document-ocr)、[Processor list](https://docs.cloud.google.com/document-ai/docs/processors-list) | OCR 不仅输出纯文本，也涉及版面/页级结构；小知应保留源文件页码、章节映射、处理版本和质量抽检。 |

> 官方产品文档会变更；设计评审及采购前需再次核对供应商当前功能、服务条款、数据处理地域与费用。教材版权流程和儿童隐私规则需结合实际法域由法务确认。


## 前端 UI 开源选型补充（2026-10-25）

本轮通过 GitHub 官方 API 检查仓库、依赖清单、许可证及 release，原始摘要见 [证据 JSON](ui-framework-evidence-2026-10-25.json)，决策见 [13 UI 框架调研](13-frontend-ui-framework-and-design-system.md)。未运行这些完整第三方应用，未对其做安全审计。

| 官方仓库 | 核验用途 | 采用边界 |
|---|---|---|
| [pure-admin/vue-pure-admin](https://github.com/pure-admin/vue-pure-admin) | Vue / Element Plus 后台组合与 MIT 许可 | 管理台布局模式参考 |
| [vbenjs/vue-vben-admin](https://github.com/vbenjs/vue-vben-admin) | Vue 多组件库管理应用与 MIT 许可 | 表格/表单/权限组织参考 |
| [ant-design/ant-design-pro](https://github.com/ant-design/ant-design-pro) | React / Ant Design / ProComponents 与 MIT 许可 | 信息结构参考，不切换 React |
| [frappe/lms](https://github.com/frappe/lms) | Vue / frappe-ui 教育应用与 AGPL-3.0 | 仅产品流程参考，无代码复制 |
| [classroomio/classroomio](https://github.com/classroomio/classroomio) | Svelte / SvelteKit 教育应用与 AGPL-3.0 | 仅业务组织参考，无代码复制 |
| [element-plus/element-plus](https://github.com/element-plus/element-plus) | Vue Web 组件及 release / MIT | 原型使用固定版本 2.14.7 |
| [youzan/vant](https://github.com/youzan/vant) | Vue 移动 Web 组件及 release / MIT | 原型使用固定版本 4.10.2；不等同 Vant Weapp |

版本为核验/安装快照。AGPL 不禁止商用，但实际代码使用及网络服务方式可能触发开源义务；MIT 与图标等依赖也需保留版权和许可声明。正式发行需根据最终依赖清单审查，不能把参考项目许可视为整个平台的许可。
