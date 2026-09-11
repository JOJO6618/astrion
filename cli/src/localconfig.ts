// 本地配置读取（仅 host 本机单人模式）：与 host_api_token 同一安全语义——本机信任；
// 服务端读的也是同一份文件（resolve_deploy_config 回退链 / personalization manager），读一致性有保证。
// 边界：本模块只读；写操作（保存模型/增删路径授权）必须走 Gateway API，不在此列。
import { homedir } from 'node:os';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { EffortLevel, ModelOption, PathAuth } from './data';

const DATA_ROOT = `${homedir()}/.astrion/astrion`;
const DEPLOY_CONFIG_DIR = `${DATA_ROOT}/config`;
const HOST_DATA_DIR = `${DATA_ROOT}/host/data`;
/** 源码树（cli/src → 项目根；fileURLToPath 正确解码中文路径） */
const REPO_ROOT = resolve(fileURLToPath(new URL('../..', import.meta.url)));

function readJson(path: string): any | null {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

/** 部署级配置回退链（对齐 config.resolve_deploy_config）：部署目录 → 源码树 */
function resolveDeployConfig(name: string): any | null {
  const deployPath = resolve(DEPLOY_CONFIG_DIR, name);
  if (existsSync(deployPath)) return readJson(deployPath);
  const repoPath = resolve(REPO_ROOT, 'config', name);
  if (existsSync(repoPath)) return readJson(repoPath);
  return null;
}

/** 模型清单（custom_models.json；visible=false 的条目不上架；context_window 透传供上下文百分比） */
export function loadCustomModels(): ModelOption[] {
  const cfg = resolveDeployConfig('custom_models.json');
  const items = Array.isArray(cfg?.models) ? cfg.models : [];
  return items
    .filter((m: any) => m && m.model_name && m.visible !== false)
    .map((m: any) => ({
      name: String(m.model_name),
      meta: String(m.model_description || m.description || ''),
      contextWindow: typeof m.context_window === 'number' && m.context_window > 0 ? m.context_window : null,
    }));
}

export interface BootPreferences {
  model: string;
  /** default_run_mode：fast/thinking/deep → thinking 布尔（面板显示用） */
  thinking: boolean;
  runMode: string;
  effort: EffortLevel;
  workMode: string;
  permMode: string;
  /** 自动深度压缩开关与触发阈值（上下文百分比分母，对齐 web 端算法） */
  autoDeepCompress: boolean;
  deepCompressLimit: number;
}

const EFFORT_VALUES: EffortLevel[] = ['default', 'low', 'medium', 'high', 'xhigh', 'max'];

/** 个性化默认（personalization.json：默认模型/运行模式/推理强度/工作模式/权限模式/压缩设置） */
export function loadPersonalizationDefaults(): BootPreferences {
  const p = readJson(resolve(HOST_DATA_DIR, 'personalization.json')) ?? {};
  const effortRaw = String(p.default_reasoning_effort ?? 'default');
  const limitRaw = Number(p.deep_compress_trigger_tokens);
  return {
    model: String(p.default_model ?? ''),
    runMode: String(p.default_run_mode ?? 'fast'),
    thinking: String(p.default_run_mode ?? 'fast') !== 'fast',
    effort: (EFFORT_VALUES as string[]).includes(effortRaw) ? (effortRaw as EffortLevel) : 'default',
    workMode: String(p.default_work_mode ?? 'ask'),
    permMode: String(p.default_permission_mode ?? 'unrestricted'),
    autoDeepCompress: !!p.auto_deep_compress_enabled,
    deepCompressLimit: Number.isFinite(limitRaw) && limitRaw > 0 ? limitRaw : 150000,
  };
}

/** 路径授权（host_sandbox_policy.json：writable→rw 组 + readable_extra→ro 组） */
export function loadPathAuths(): PathAuth[] {
  const cfg = resolveDeployConfig('host_sandbox_policy.json') ?? {};
  const out: PathAuth[] = [];
  for (const p of cfg.macos_writable_paths ?? []) {
    if (typeof p === 'string' && p.trim()) out.push({ path: p, access: 'rw' });
  }
  for (const p of cfg.macos_readable_extra_paths ?? []) {
    if (typeof p === 'string' && p.trim()) out.push({ path: p, access: 'ro' });
  }
  return out;
}
