// zh-CN 聚合器（源语言，MessageSchema 的类型来源）
// 新增命名空间：在 zh-CN/ 与 en-US/ 下各建同名文件后，在此与 en-US.ts 同步注册。
import common from './zh-CN/common';
import personalization from './zh-CN/personalization';
import toolResults from './zh-CN/toolResults';
import input from './zh-CN/input';
import tutorial from './zh-CN/tutorial';
import monitor from './zh-CN/monitor';
import workflow from './zh-CN/workflow';
import adminDashboard from './zh-CN/adminDashboard';
import adminPolicy from './zh-CN/adminPolicy';
import adminApi from './zh-CN/adminApi';
import adminCustomTools from './zh-CN/adminCustomTools';

export default {
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
} as const;
