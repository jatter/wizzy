(() => {
  const toast = document.getElementById('toast');
  let timer;
  const notify = (message) => {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(timer);
    timer = setTimeout(() => toast.classList.remove('show'), 2400);
  };
  const showTab = (id) => {
    const pane = document.getElementById(id);
    if (!pane) return;
    document.querySelectorAll('.tab-content').forEach((el) => el.classList.toggle('active', el === pane));
    document.querySelectorAll('[data-tab]').forEach((el) => el.classList.toggle('active', el.dataset.tab === id && el.matches('button')));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  const modalCopy = {
    review: ['核对这道题', '模拟原图与 OCR / 判题对照。请根据作业原图确认识别与机器判定；家长修正会作为新版本保存，不会覆盖机器原始结果。'],
    'create-plan': ['创建复习计划', '已为你准备“长方形周长”复习计划。建议 3 题、约 8 分钟；超过 20 题或 20 分钟时请拆分计划。'],
    'bulk-plan': ['加入复习计划', '已选中的错题将加入一个新的复习计划。重复题目会合并，原始作业记录保持不变。'],
    'add-question': ['手动收录错题', '原型演示：可以从作业记录选择一道题，或手动填写题目与知识点。'],
    'edit-schedule': ['编辑课表', '原型演示：可添加课程、节次与单双周；时间冲突会在保存前提示。'],
    'edit-book': ['确认教材进度', '当前教材：数学 · 人教版 · 三年级上册。请核对章节后保存；未授权教材内容不会自动导入。'],
    invite: ['邀请监护人', '生成一次性家庭邀请。被邀请人需完成监护人认证后才可访问孩子数据。'],
    revoke: ['撤销监护人访问？', '撤销后该监护人的家庭会话和缓存将失效；家庭所有者不能被移除。'],
    export: ['申请导出家庭数据', '将生成仅包含本家庭已授权孩子的数据导出包。下载链接会限时有效并记录审计。'],
    delete: ['撤回同意与删除', '此操作会停止儿童数据处理并启动删除流程。继续前需要家庭所有者二次验证。'],
    'tenant-detail': ['租户元数据', '仅展示脱敏 ID、成员数量、存储用量、任务状态与额度信息。家庭姓名、作业原图和答题内容不可由运营默认查看。'],
    'tenant-export': ['导出租户清单', '导出只包含脱敏租户 ID、状态、创建时间与用量统计，不含任何儿童内容。'],
    'job-detail': ['异步任务详情', '任务阶段：图像识别 → 题目切分 → 规则检查 → 结果可用。当前重试 2 / 3；供应商超时只显示错误码与耗时，不暴露请求内容。'],
    'model-detail': ['模型版本与评测', '固定版本 vision-2.4；关联锁定集 eval-2026-10-r3、费用记录与供应商合同。切换生产版本需完成质量、隐私和合规审批。'],
    'new-model': ['登记模型版本', '原型演示：填写供应商、固定版本标识、用途、合同区域和评测集。未完成准入门禁不能切换到生产。'],
    'new-school': ['新建学校档案', '填写组织名称、唯一编码、地区、学年和授权范围；配置学校管理员前需核验授权。'],
    'school-import': ['导入学生名册', '原型演示：先下载模板并校验重复编码、年级格式和授权范围，错误行需修正后重新导入。'],
    'school-detail': ['学校档案与授权', '可维护组织层级、学年/年级、管理员、学生关联及授权有效期。学校关系不会自动开放家庭学习内容。'],
    'invite-user': ['管理学生/监护人关系', '此原型仅展示目录管理入口。真实变更须验证家庭授权、关系来源并记录操作审计。'],
    'directory-detail': ['脱敏关系详情', '展示关系来源、授权版本、有效期和设备绑定状态。儿童内容默认不可见，内容访问需独立工单授权。'],
    'export-directory': ['导出脱敏目录', '导出仅含授权范围内的脱敏 ID、关系和状态；下载会留审计记录。'],
    'invite-admin': ['邀请后台账号', '发送限时邀请；需配置最小权限角色和组织范围，首次登录强制启用 MFA。'],
    'new-role': ['创建权限角色', '按资源和操作拆分权限点，设置数据范围；敏感内容访问、生产发布等高危权限须双人审批。'],
    'admin-detail': ['后台账号权限', '可调整角色、数据范围、MFA 与会话；变更后立即审计，停用时撤销所有有效会话。'],
    'eval-detail': ['模型评测报告', '报告需可追溯模型/Prompt/规则固定版本、锁定测试集、人工金标准、标注一致性、分题型混淆矩阵、误判为正确率、覆盖率、样本量和置信区间。样本不足应阻断该题型自动判定。'],
    'text-preview': ['TXT 文本与章节映射', '原型演示：可按章节预览 OCR 文本、源 PDF 页码、识别置信/异常标记与文本修订版本；修改后需重新质检。'],
    'book-review': ['教材权利与解析质检', '核对许可来源/地区/用途/有效期，再按章节抽查 PDF 原页、OCR/TXT 和页码映射。权利或质检不通过不得发布。'],

  };
  document.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-tab]');
    if (tab) { event.preventDefault(); showTab(tab.dataset.tab); return; }
    const btn = event.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === 'close-modal') { document.getElementById('modal')?.classList.remove('show'); return; }
    if (action === 'confirm-modal') {
      document.getElementById('modal')?.classList.remove('show');
      notify('已完成模拟操作');
      return;
    }
    if (action === 'submit-homework') {
      if (!document.getElementById('attempted')?.checked) { notify('请先确认已经认真尝试完成'); return; }
      document.querySelectorAll('#homework .step').forEach((el, i) => { el.classList.toggle('done', i < 3); el.classList.remove('current'); });
      notify('作业已提交检查 · 可以离开，稍后回来查看');
      return;
    }
    if (action === 'rotate') { notify('图片已旋转 90°'); return; }
    if (action === 'remove-page') { notify('示例页已移除'); return; }
    if (action === 'add-page') { notify('原型演示：已添加一页图片'); return; }
    if (action === 'view-result') { notify('结果已加载：2 道正确 · 1 道需订正 · 1 道待家长确认'); return; }
    if (action === 'need-help') {
      const area = document.querySelector('#mistakes .result-area');
      if (area) area.innerHTML = '<div class="hint">没关系，可以请家长一起看看。这里不会把“需要帮助”记成已掌握。</div>';
      return;
    }
    if (action === 'check-answer') {
      const input = document.getElementById('answer-input');
      const area = document.querySelector('#mistakes .result-area');
      if (!input?.value.trim()) { notify('先写下你的想法再检查吧'); input?.focus(); return; }
      if (area) area.innerHTML = '<div class="hint">🌟 你愿意再试一次真棒！提示：长边有 2 条，宽边也有 2 条。想想怎样把它们加在一起？</div>';
      return;
    }
    if (action === 'finish-preview') {
      const answer = document.getElementById('preview-answer');
      const area = document.querySelector('#preview .result-area');
      if (!answer?.value.trim()) { notify('写下你的想法后再完成自检'); answer?.focus(); return; }
      if (area) area.innerHTML = '<div class="tag green">已记录自检 · 预习完成</div><p class="tiny-text muted">你的回答已保存为学习记录。</p>';
      return;
    }

    if (action === 'upload-book') {
      document.getElementById('book-file')?.click();
      return;
    }
    if (action === 'start-parse') {
      const file = document.getElementById('book-file')?.files?.[0];
      const status = document.getElementById('parse-status');
      if (!file) { notify('请先选择 PDF 文件'); document.getElementById('book-file')?.focus(); return; }
      if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) { notify('文件格式不正确，请选择 PDF'); return; }
      const rights = document.querySelector('#books select');
      if (!rights || rights.selectedIndex === 0) { notify('请先登记教材权利依据'); rights?.focus(); return; }
      if (status) status.textContent = `已创建演示解析任务：${file.name} · 权利核验 → OCR → 章节映射 → 待人工抽检（未上传文件）`;
      notify('演示解析任务已创建'); return;
    }
    if (action === 'refresh-jobs') { notify('队列已刷新 · 仍有 12 个任务处理中'); return; }
    const copy = modalCopy[action];
    if (copy) {
      const modal = document.getElementById('modal');
      if (!modal) { notify(copy[0] + ' · 原型演示'); return; }
      document.getElementById('modal-title').textContent = copy[0];
      document.getElementById('modal-body').textContent = copy[1];
      modal.classList.add('show');
    }
  });
  document.addEventListener('change', (event) => {
    if (event.target.id === 'book-file') {
      const file = event.target.files?.[0];
      const status = document.getElementById('parse-status');
      if (file && status) status.textContent = `已选择：${file.name} · 文件仅保留在本机，点击“校验并开始解析”模拟后续流程。`;
    }
    if (event.target.id === 'select-all') {
      document.querySelectorAll('.item-check').forEach((el) => { el.checked = event.target.checked; });
    }
    if (event.target.matches('.item-check, #select-all')) {
      const count = document.getElementById('selected-count');
      if (count) count.textContent = document.querySelectorAll('.item-check:checked').length;
    }
  });
  document.querySelectorAll('.filters input:not([type="date"]), .filters select').forEach((field) => {
    field.addEventListener('input', filterTables);
    field.addEventListener('change', filterTables);
  });
  function filterTables() {
    document.querySelectorAll('.tab-content.active .table-wrap').forEach((table) => {
      const pane = table.closest('.tab-content');
      const fields = [...(pane?.querySelectorAll('.filters input:not([type="date"]), .filters select') || [])];
      const query = fields.find((el) => el.tagName === 'INPUT')?.value.trim().toLowerCase() || '';
      const selected = fields.find((el) => el.tagName === 'SELECT')?.selectedOptions[0]?.textContent.trim() || '';
      table.querySelectorAll('tbody tr').forEach((row) => {
        const text = row.textContent.toLowerCase();
        const statusFilter = selected && !selected.startsWith('全部') && !selected.startsWith('最近') && !selected.startsWith('近 ');
        const matchesQuery = !query || text.includes(query);
        const matchesStatus = !statusFilter || text.includes(selected.toLowerCase());
        row.style.display = matchesQuery && matchesStatus ? '' : 'none';
      });
    });
  }
})();
