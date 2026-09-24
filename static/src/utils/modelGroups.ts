/**
 * 模型选项分组工具：输入栏模型菜单与设置页/个人空间模型选择下拉共用。
 * 分组规则：provider 同步模型按 providerId/providerName 分组；codex/* 归 Codex 组；
 * 其余（手写 custom_models）归「自定义」组。组顺序 = 选项首次出现顺序。
 */
export interface ModelGroupOption {
  key: string;
  label: string;
  providerId?: string;
  providerName?: string;
  disabled?: boolean;
  [extra: string]: unknown;
}

export interface ModelGroup<O extends ModelGroupOption = ModelGroupOption> {
  id: string;
  name: string;
  options: O[];
}

export function groupModelOptions<O extends ModelGroupOption>(
  options: O[],
  labels: { codex: string; custom: string }
): Array<ModelGroup<O>> {
  const groups: Array<ModelGroup<O>> = [];
  const byId = new Map<string, ModelGroup<O>>();
  for (const opt of options) {
    const key = String(opt.key || '');
    let groupId: string;
    let groupName: string;
    if (opt.providerId) {
      groupId = `provider:${opt.providerId}`;
      groupName = opt.providerName || opt.providerId;
    } else if (key.startsWith('codex/')) {
      groupId = 'codex';
      groupName = labels.codex;
    } else {
      groupId = 'custom';
      groupName = labels.custom;
    }
    let group = byId.get(groupId);
    if (!group) {
      group = { id: groupId, name: groupName, options: [] };
      byId.set(groupId, group);
      groups.push(group);
    }
    group.options.push(opt);
  }
  return groups;
}

/**
 * 搜索过滤：只匹配菜单里显示出来的名字（label 小写子串），
 * 不匹配 key/模型 id（前缀会污染：搜 "co" 命中全部 codex/*），也不匹配组名。
 * 空组剔除；无搜索词时原样返回。
 */
export function filterModelGroups<G extends ModelGroup<ModelGroupOption>>(
  groups: G[],
  query: string
): G[] {
  const q = String(query || '')
    .trim()
    .toLowerCase();
  if (!q) return groups;
  const matched: G[] = [];
  for (const group of groups) {
    const options = group.options.filter((opt) =>
      String(opt.label || '')
        .toLowerCase()
        .includes(q)
    );
    if (options.length) {
      matched.push({ ...group, options });
    }
  }
  return matched;
}
