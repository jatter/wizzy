# 三端原型：当前视觉与依赖证据

日期：2026-10-25（Asia/Shanghai）。对象：`docs/prototype/ui` 的 Vite 7.3.7 构建产物，经本地 HTTP 提供 `docs/prototype`；不是正式平台身份客户端工程。

## 视觉检查

以下截图来自本机 Google Chrome，本次已人工逐张查看：

- [1440px 平台模型与评测](platform-models-desktop.png)：深色分区侧栏、工作区导航、指标卡、筛选表格和模型详情/评测入口。示例正确率 95.2% 明确标识未达准入，不代表真实模型能力。
- [390×844px 孩子端展开导航](mobile-navigation.png)：按实际视口截图，侧栏宽 246px，关闭遮罩位于右侧；未用全页截图的额外高度评估固定定位遮罩。

全部为虚构演示数据，不含真实学生、家长、账号或密钥。模型、PDF 解析与批改均为模拟，无真实鉴权或后端调用。截图检查不是像素回归、无障碍全量审计或生产验收。

## 构建与交互回归

```bash
npm run build --prefix docs/prototype/ui
PLAYWRIGHT_CHANNEL=chrome PROTOTYPE_BUILT=1 npm test --prefix docs/prototype/ui -- --workers=3 --timeout=15000 --reporter=line
```

交付前再次构建 2.03s，45 项通过（12.8s）。38 项页面检查覆盖 19 路由 × 1440px/390px，另有 7 项关键业务交互。小屏关闭遮罩的新断言先观察到失败，再修复并通过全量测试。详细结果和边界见 [原型验证记录](../../prototype/VERIFICATION.md)。

## 依赖审计

```bash
npm audit --prefix docs/prototype/ui --registry=https://registry.npmjs.org --json
```

[完整审计 JSON](npm-audit.json)：退出码 0，0 个已知依赖漏洞。原型 Vite 已从 7.2.2 升级到固定 7.3.7，相关官方披露见 [Vite 安全公告](https://github.com/vitejs/vite/security/advisories/GHSA-rq7h-c2jc-7f22)。此结果仅是核验时该锁文件的已知漏洞数据库快照，不是源码安全审计，不保证无安全问题；不要将开发/预览服务暴露公网。

独立正式平台工程有自己的 [视觉与审计证据](../platform-web-2026-10-25/README.md)，不能混用两个工程的审计结论。
