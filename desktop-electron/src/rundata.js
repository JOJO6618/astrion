import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

const DEFAULT_DATA_ROOT = path.join(os.homedir(), '.astrion', 'astrion-desktop');
const SETTINGS_FILE = path.join(os.homedir(), '.astrion', 'rundata_settings.json');

function expandUserPath(value) {
  const raw = String(value || '').trim();
  if (raw === '~') return os.homedir();
  if (raw.startsWith(`~${path.sep}`)) return path.join(os.homedir(), raw.slice(2));
  return raw;
}

function configuredEnvRoot(env = process.env) {
  const raw = expandUserPath(env.ASTRION_DESKTOP_DATA_ROOT);
  return raw ? path.resolve(raw) : '';
}

const migrationProgress = {
  active: false,
  phase: 'idle',
  files_done: 0,
  files_total: 0,
  bytes_done: 0,
  bytes_total: 0,
  error: null,
  backend_ready: false,
  completed_at: null
};

function setMigrationProgress(phase, values = {}) {
  Object.assign(migrationProgress, values, {
    active: !['idle', 'done', 'error'].includes(phase),
    phase
  });
}

export function getMigrationProgress() {
  return { ...migrationProgress };
}

export function markMigrationBackendReady() {
  migrationProgress.backend_ready = true;
}

async function readSettings() {
  try {
    const value = JSON.parse(await fs.readFile(SETTINGS_FILE, 'utf8'));
    const root = typeof value.data_root === 'string' ? value.data_root.trim() : '';
    return {
      data_root: root ? path.resolve(root) : '',
      pending_migration: value.pending_migration && typeof value.pending_migration === 'object'
        ? value.pending_migration
        : null,
      last_error: typeof value.last_error === 'string' ? value.last_error : null
    };
  } catch (error) {
    if (error?.code === 'ENOENT') return { data_root: '', pending_migration: null, last_error: null };
    console.error('[astrion-desktop] 无法读取运行数据目录设置:', error);
    throw new Error('rundata_settings_invalid');
  }
}

export async function resolveRunDataRoot(env = process.env) {
  if (configuredEnvRoot(env)) return configuredEnvRoot(env);
  return (await readSettings()).data_root || DEFAULT_DATA_ROOT;
}

export async function hasPendingMigration(env = process.env) {
  if (configuredEnvRoot(env)) return false;
  return Boolean((await readSettings()).pending_migration);
}

export async function getRunDataInfo(env = process.env) {
  const envRoot = configuredEnvRoot(env);
  const settings = envRoot ? null : await readSettings();
  const storedRoot = settings?.data_root || '';
  const activeRoot = envRoot || settings?.pending_migration?.from || storedRoot || DEFAULT_DATA_ROOT;
  return {
    success: true,
    env_locked: Boolean(envRoot),
    env_path: envRoot,
    configured_path: storedRoot,
    active_path: activeRoot,
    default_path: DEFAULT_DATA_ROOT,
    last_error: settings?.last_error || null
  };
}

async function writeSettings(settings) {
  await fs.mkdir(path.dirname(SETTINGS_FILE), { recursive: true, mode: 0o700 });
  const tempPath = `${SETTINGS_FILE}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(tempPath, `${JSON.stringify(settings, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600
  });
  await fs.rename(tempPath, SETTINGS_FILE);
}

function isFinderMetadata(name) {
  return name === '.DS_Store' || name === '.localized' || name.startsWith('._');
}

async function directoryIsEmpty(target) {
  try {
    const entries = await fs.readdir(target);
    return entries.every(isFinderMetadata);
  } catch (error) {
    if (error?.code === 'ENOENT') return true;
    throw error;
  }
}

async function canonicalPath(value) {
  let cursor = path.resolve(value);
  const tail = [];
  while (true) {
    try {
      const real = await fs.realpath(cursor);
      return path.join(real, ...tail);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
      const parent = path.dirname(cursor);
      if (parent === cursor) throw error;
      tail.unshift(path.basename(cursor));
      cursor = parent;
    }
  }
}

async function assertNoOverlap(source, target) {
  const canonicalSource = await canonicalPath(source);
  const canonicalTarget = await canonicalPath(target);
  const relative = path.relative(canonicalSource, canonicalTarget);
  const reverse = path.relative(canonicalTarget, canonicalSource);
  if (!relative && !reverse) throw new Error('same_directory');
  if ((relative && !relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative)) ||
      (reverse && !reverse.startsWith(`..${path.sep}`) && reverse !== '..' && !path.isAbsolute(reverse))) {
    throw new Error('overlapping_directories');
  }
}

async function countEntries(root) {
  let count = 0;
  let files = 0;
  let bytes = 0;
  async function walk(dir) {
    for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
      if (isFinderMetadata(entry.name)) continue;
      count += 1;
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(fullPath);
      else if (entry.isFile()) {
        files += 1;
        bytes += (await fs.stat(fullPath)).size;
      }
    }
  }
  await walk(root);
  return { count, files, bytes };
}

export async function applyRunDataRoot(payload = {}, env = process.env) {
  if (configuredEnvRoot(env)) throw new Error('environment_locked');
  const dataRoot = payload.data_root ?? payload.dataRoot;
  const migrate = Boolean(payload.migrate);
  if (typeof dataRoot !== 'string' || !dataRoot.trim()) throw new Error('path_required');

  const target = path.resolve(expandUserPath(dataRoot));
  const source = await resolveRunDataRoot(env);
  await assertNoOverlap(source, target);

  if (migrate) {
    const targetStat = await fs.lstat(target).catch((error) => {
      if (error?.code === 'ENOENT') return null;
      throw error;
    });
    if (targetStat?.isSymbolicLink() || (targetStat && !targetStat.isDirectory())) {
      throw new Error('target_not_directory');
    }
    if (!(await directoryIsEmpty(target))) throw new Error('target_not_empty');
  } else {
    await fs.mkdir(target, { recursive: true });
  }

  await writeSettings({
    data_root: target,
    pending_migration: migrate ? { from: source, to: target } : null,
    last_error: null
  });
  return { success: true, active_path: target, restart_required: true };
}

export async function runPendingMigration() {
  if (configuredEnvRoot()) return configuredEnvRoot();
  const settings = await readSettings();
  const pending = settings.pending_migration;
  if (!pending) return settings.data_root || DEFAULT_DATA_ROOT;
  if (typeof pending.from !== 'string' || !pending.from.trim() || typeof pending.to !== 'string' || !pending.to.trim()) {
    const error = 'migration_settings_invalid';
    setMigrationProgress('error', { error, backend_ready: false });
    throw new Error(error);
  }

  const source = path.resolve(pending.from);
  const target = path.resolve(pending.to);
  setMigrationProgress('scanning', { files_done: 0, files_total: 0, bytes_done: 0, bytes_total: 0, error: null, backend_ready: false, completed_at: null });
  let stage = '';
  try {
    await assertNoOverlap(source, target);
    const sourceExists = await fs.stat(source).then((stat) => stat.isDirectory()).catch((error) => {
      if (error?.code === 'ENOENT') return false;
      throw error;
    });
    const sourceStats = sourceExists ? await countEntries(source) : { count: 0, files: 0, bytes: 0 };
    const targetStat = await fs.lstat(target).catch((error) => {
      if (error?.code === 'ENOENT') return null;
      throw error;
    });
    if (targetStat?.isSymbolicLink() || (targetStat && !targetStat.isDirectory())) {
      throw new Error('target_not_directory');
    }
    if (!(await directoryIsEmpty(target))) throw new Error('target_not_empty');

    await fs.mkdir(path.dirname(target), { recursive: true });
    stage = path.join(path.dirname(target), `.${path.basename(target)}.astrion-migration-${randomUUID()}`);
    await fs.mkdir(stage, { recursive: false });
    setMigrationProgress('copying', {
      files_done: 0,
      files_total: sourceStats.files,
      bytes_done: 0,
      bytes_total: sourceStats.bytes
    });
    if (sourceExists) {
      await fs.cp(source, stage, {
        recursive: true,
        errorOnExist: true,
        force: false,
        filter: async (entryPath) => {
          if (isFinderMetadata(path.basename(entryPath))) return false;
          const stat = await fs.lstat(entryPath);
          if (stat.isFile()) {
            migrationProgress.files_done += 1;
            migrationProgress.bytes_done += stat.size;
          }
          return true;
        }
      });
    }
    setMigrationProgress('verifying');
    const copiedStats = await countEntries(stage);
    if (sourceStats.count !== copiedStats.count || sourceStats.bytes !== copiedStats.bytes) {
      throw new Error('verification_failed');
    }
    if (targetStat) {
      for (const name of await fs.readdir(target)) {
        if (isFinderMetadata(name)) await fs.rm(path.join(target, name), { recursive: true, force: true });
      }
      await fs.rmdir(target);
    }
    await fs.rename(stage, target);
    stage = '';
    await writeSettings({ data_root: target, pending_migration: null, last_error: null });
    setMigrationProgress('done', { completed_at: Date.now() });
    return target;
  } catch (error) {
    if (stage) await fs.rm(stage, { recursive: true, force: true }).catch(() => {});
    const message = String(error?.message || error || 'unknown');
    try {
      await writeSettings({ data_root: source, pending_migration: null, last_error: message });
    } catch (persistError) {
      const fatalMessage = `migration_state_write_failed: ${persistError?.message || persistError}`;
      setMigrationProgress('error', { error: fatalMessage });
      throw new Error(fatalMessage);
    }
    setMigrationProgress('error', { error: message });
    return source;
  }
}

export { DEFAULT_DATA_ROOT };
