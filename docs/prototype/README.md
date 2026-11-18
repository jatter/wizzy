# 小知三端可交互原型

> 更新：2026-10-26。Vue 组件化 H5 原型，提供已构建静态页面与可维护源码。所有业务数据为虚构演示数据。

## 预览已构建页面

在项目根目录运行：

```bash
python3 -m http.server 8080 --bind 127.0.0.1 --directory docs/prototype
```

浏览器访问 `http://127.0.0.1:8080/`。入口为 [index.html](index.html)，也可访问 [孩子端](child.html)、[家长端](parent.html)、[平台端](platform.html)。ES 模块需要 HTTP 服务，不支持直接双击本地 HTML 作为交付预览方式。运行构建后的页面无需 Node、npm 或 CDN。

## 源码与构建

源码在 [ui](ui/package.json)，依赖版本由 package-lock.json 固定。开发环境要求 Node.js 20.19+ 或 22.12+（推荐满足要求的受支持 LTS）及 npm；静态预览只需要 Python 3。

```bash
npm ci --prefix docs/prototype/ui
npm run dev --prefix docs/prototype/ui
npm run build --prefix docs/prototype/ui
```

构建先写入 `ui/dist`，再由 `ui/scripts/publish.mjs` 发布四个 HTML 入口及 `assets/ui`。修改 `ui/src` 后重新构建；不要直接修改生成文件。历史静态资源保留，但新版入口只引用本次构建资产。

## 页面与主要交互

| 端 | 页面路由 | 已提供的演示交互 |
|---|---|---|
| 孩子 | `home`、`pairing`、`homework`、`mistakes`、`review`、`preview`、`handwriting` | 配对/撤销、休息提醒、1–9张选图/排序/旋转/真实裁剪、中断重试、OCR题块定位、订正历史、复习草稿续做、预习缺章/过期及工整度拒评 |
| 家长 | `overview`、`submissions`、`mistakes`、`reports`、`schedule`、`privacy`、`tutoring`、`handwriting` | 孩子切换/空态、作业核对修订、错题计划、报告/课表、监护人/隐私申请、3级提示及家长讲解、工整度趋势和无效排除 |
| 平台 | `ops`、`tenants`、`jobs`、`people`、`schools`、`books`、`models`、`admins`、`audit` | 列表筛选、详情、学校名册校验、PDF 权利登记及模拟解析/TXT 下载、模型注册/评测报告、账号权限申请与审计 |

例如 `platform.html#models` 可直接打开模型页。共 24 个业务页面（孩子7、家长8、平台9）；入口页不计入。表单在错误时保留输入，主要数据变更只保存在当前页面会话，刷新后恢复演示数据。

## 验证

```bash
PLAYWRIGHT_CHANNEL=chrome PROTOTYPE_BUILT=1 npm test --prefix docs/prototype/ui -- --workers=3 --timeout=15000 --reporter=line
```

上述命令需要本机 Google Chrome、Python 3，以及空闲的 4174 端口；测试自动启动构建目录静态服务。没有 Chrome 时可在 `ui` 目录执行 `npx playwright install chromium`，再移除 `PLAYWRIGHT_CHANNEL=chrome`。2026-10-26 使用 Chrome 复验，66 项测试通过（16.2s）：24页×桌面/手机双视口48项，加18项业务流程，详见 [验证记录](VERIFICATION.md)。

## 使用边界与开发依据

- 本地图片与 PDF 不发送到服务器；批改、OCR 和 PDF 解析均为固定模拟结果。TXT 下载为示例文本，不能当成所选 PDF 的真实识别结果。
- 配对/撤销为本地状态演示；无真实登录、服务端配对、MFA、权限执行、数据库、模型调用或删除/导出服务。导航端切换仅用于评审；前端角色标签不能代替鉴权。
- 模型准确率、成本、用户与学校数字均为示意值。95.2% 示例未达准入；家长改判不直接成为金标准；教材提交抽检不等于发布。
- 正式家庭工程仍采用 uni-app；Vant Web 原型不能直接认定兼容微信小程序。学校模块暂列 P1。
- 数据库方案是 PostgreSQL；独立正式后端已有 Alembic 迁移和部分真实 PostgreSQL/Redis 集成测试，但原型尚未联调正式 API。实现与未完成边界见 [14 实施台账](../plans/14-development-status.md)。

开发依据：[逐页交互与开发交接](INTERACTIONS.md)、[三端功能列表与页面映射](../小知功能列表.md)、[UI 框架调研与设计方案](../research/13-frontend-ui-framework-and-design-system.md)、[数据模型](../architecture/06-data-model-and-api.md)。功能列表是完整开发要求，原型覆盖情况须以其中的逐页映射为准。
