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

async function readSettings() {
  try {
    const value = JSON.parse(await fs.readFile(SETTINGS_FILE, 'utf8'));
    const root = typeof value.data_root === 'string' ? value.data_root.trim() : '';
    return root ? path.resolve(root) : '';
  } catch (error) {
    if (error?.code === 'ENOENT') return '';
    console.error('[astrion-desktop] 无法读取运行数据目录设置:', error);
    throw new Error('rundata_settings_invalid');
  }
}

export async function resolveRunDataRoot(env = process.env) {
  return configuredEnvRoot(env) || (await readSettings()) || DEFAULT_DATA_ROOT;
}

export async function getRunDataInfo(env = process.env) {
  const envRoot = configuredEnvRoot(env);
  const storedRoot = envRoot ? '' : await readSettings();
  const activeRoot = envRoot || storedRoot || DEFAULT_DATA_ROOT;
  return {
    success: true,
    env_locked: Boolean(envRoot),
    env_path: envRoot,
    configured_path: storedRoot,
    active_path: activeRoot,
    default_path: DEFAULT_DATA_ROOT
  };
}

async function writeSettings(dataRoot) {
  await fs.mkdir(path.dirname(SETTINGS_FILE), { recursive: true, mode: 0o700 });
  const tempPath = `${SETTINGS_FILE}.${process.pid}.tmp`;
  await fs.writeFile(tempPath, `${JSON.stringify({ data_root: dataRoot }, null, 2)}\n`, {
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
  let bytes = 0;
  async function walk(dir) {
    for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
      count += 1;
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(fullPath);
      else if (entry.isFile()) bytes += (await fs.stat(fullPath)).size;
    }
  }
  await walk(root);
  return { count, bytes };
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

    const parent = path.dirname(target);
    await fs.mkdir(parent, { recursive: true });
    const stage = path.join(parent, `.${path.basename(target)}.astrion-migration-${randomUUID()}`);
    try {
      await fs.mkdir(stage, { recursive: false });
      const sourceExists = await fs.stat(source).then((s) => s.isDirectory()).catch((error) => {
        if (error?.code === 'ENOENT') return false;
        throw error;
      });
      if (sourceExists) await fs.cp(source, stage, { recursive: true, errorOnExist: true, force: false });
      const sourceStats = sourceExists ? await countEntries(source) : { count: 0, bytes: 0 };
      const copiedStats = await countEntries(stage);
      if (sourceStats.count !== copiedStats.count || sourceStats.bytes !== copiedStats.bytes) {
        throw new Error('verification_failed');
      }
      if (targetStat) {
        const targetEntries = await fs.readdir(target);
        for (const name of targetEntries) {
          if (isFinderMetadata(name)) {
            await fs.rm(path.join(target, name), { recursive: true, force: true });
          }
        }
        await fs.rmdir(target);
      }
      await fs.rename(stage, target);
    } catch (error) {
      await fs.rm(stage, { recursive: true, force: true }).catch(() => {});
      throw error;
    }
  } else {
    await fs.mkdir(target, { recursive: true });
  }

  await writeSettings(target);
  return { success: true, active_path: target, restart_required: true };
}

export { DEFAULT_DATA_ROOT };
