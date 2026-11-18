export interface PermissionAction {
  code: string;
  short: string;
  detail: string;
}

export interface PermissionModule {
  id: string;
  label: string;
  actions: PermissionAction[];
}

export const permissionModules: PermissionModule[] = [
  {
    id: 'accounts',
    label: '平台账号',
    actions: [
      {code: 'platform.accounts.read', short: '查看', detail: '查看账号列表、详情和变更历史'},
      {code: 'platform.accounts.write', short: '申请变更', detail: '提交角色或启停变更'},
      {code: 'platform.accounts.approve', short: '审批变更', detail: '批准或拒绝他人提交的变更'},
      {code: 'platform.accounts.sessions.revoke', short: '撤销会话', detail: '让指定账号退出全部平台会话'},
    ],
  },
  {
    id: 'audit',
    label: '安全审计',
    actions: [
      {code: 'platform.audit.read', short: '查看', detail: '查看登录、退出和权限拒绝等审计事件'},
    ],
  },
  {
    id: 'models',
    label: '模型管理',
    actions: [
      {code: 'platform.models.read', short: '查看', detail: '查看模型目录、连接配置和批改提示词'},
      {code: 'platform.models.write', short: '维护', detail: '维护模型连接和批改提示词，并测试连接'},
    ],
  },
  {
    id: 'textbooks',
    label: '教材管理',
    actions: [
      {code: 'platform.textbooks.read', short: '查看', detail: '查看教材目录、正文和文档'},
      {code: 'platform.textbooks.write', short: '维护', detail: '登记教材并处理文档'},
      {code: 'platform.textbooks.review', short: '审核', detail: '审核教材权利，不能审核自己提交的内容'},
    ],
  },
  {
    id: 'schools',
    label: '学校与组织',
    actions: [
      {code: 'platform.schools.read', short: '查看', detail: '查看学校档案、组织、年级和班级'},
      {code: 'platform.schools.write', short: '维护', detail: '登记和调整学校、组织、年级与班级'},
    ],
  },
  {
    id: 'students',
    label: '学生名册',
    actions: [
      {code: 'platform.students.read', short: '查看', detail: '查看学生名册和在读记录'},
      {code: 'platform.students.write', short: '维护', detail: '登记学生、调班和变更在读状态'},
    ],
  },
  {
    id: 'evaluation',
    label: '评测数据集',
    actions: [
      {code: 'platform.evaluation.read', short: '查看', detail: '查看数据集、样本和评测运行'},
      {code: 'platform.evaluation.curate', short: '整理', detail: '采集和整理评测样本'},
      {code: 'platform.evaluation.annotate', short: '标注', detail: '独立标注样本'},
      {code: 'platform.evaluation.adjudicate', short: '裁决', detail: '裁决标注分歧'},
      {code: 'platform.evaluation.run', short: '运行', detail: '发起模型评测运行'},
    ],
  },
];

const knownCodes = new Set(permissionModules.flatMap(module => module.actions.map(action => action.code)));

export interface ScopeAction extends PermissionAction { granted: boolean }
export interface ScopeRow {
  id: string;
  label: string;
  granted: number;
  total: number;
  actions: ScopeAction[];
}

export function unknownPermissions(codes: string[]) {
  return [...new Set(codes.filter(code => !knownCodes.has(code)))].sort();
}

export function permissionRows(codes: string[]): ScopeRow[] {
  const has = new Set(codes);
  return permissionModules.map(module => {
    const actions = module.actions.map(action => ({...action, granted: has.has(action.code)}));
    return {id: module.id, label: module.label, granted: actions.filter(action => action.granted).length, total: actions.length, actions};
  });
}

export function permissionBrief(codes: string[]) {
  const has = new Set(codes);
  const groups = permissionModules.map(module => ({
    label: module.label,
    total: module.actions.length,
    hits: module.actions.filter(action => has.has(action.code)),
  })).filter(group => group.hits.length);
  const unknown = unknownPermissions(codes);
  if (!groups.length && !unknown.length) return '只能查看自己的会话和角色目录';
  if (groups.length === permissionModules.length && groups.every(group => group.hits.length === group.total) && !unknown.length) {
    return `全部 ${permissionModules.length} 个功能`;
  }
  const bits: string[] = [];
  let readRun: string[] = [];
  const flush = () => {
    if (!readRun.length) return;
    bits.push(`查看${readRun.join('、')}`);
    readRun = [];
  };
  for (const group of groups) {
    if (group.hits.length === 1 && group.hits[0].code.endsWith('.read')) {
      readRun.push(group.label);
      continue;
    }
    flush();
    bits.push(group.hits.length === group.total
      ? `${group.label}（全部）`
      : `${group.label}（${group.hits.map(action => action.short).join('、')}）`);
  }
  flush();
  if (unknown.length) bits.push(`另有 ${unknown.length} 项未收录权限`);
  return bits.join('；');
}
