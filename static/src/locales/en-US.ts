// en-US 聚合器（zh-CN 的镜像）
// DeepString<typeof zhCN> 强制 key 结构与 zh-CN 完全一致：en-US 缺 key / 多 key 都会 tsc 报错。
import type zhCN from './zh-CN';
import common from './en-US/common';
import personalization from './en-US/personalization';
import toolResults from './en-US/toolResults';
import input from './en-US/input';
import tutorial from './en-US/tutorial';
import monitor from './en-US/monitor';
import workflow from './en-US/workflow';
import adminDashboard from './en-US/adminDashboard';
import adminPolicy from './en-US/adminPolicy';
import adminApi from './en-US/adminApi';
import adminCustomTools from './en-US/adminCustomTools';

type DeepString<T> = { [K in keyof T]: T[K] extends string ? string : DeepString<T[K]> };

const enUS: DeepString<typeof zhCN> = {
  common,
  personalization,
  toolResults,
  input,
  tutorial,
  monitor,
  workflow,
  adminDashboard,
  adminPolicy,
  adminApi,
  adminCustomTools,
};

export default enUS;
