import path from 'node:path';
import os from 'node:os';

export const modifierChoices = platform => platform === 'win32'
  ? ['alt', 'left-control', 'right-control', 'shift']
  : ['option', 'command', 'control'];

export function quickSettingsPath(platform = process.platform, env = process.env) {
  return env.ASTRION_QUICK_SETTINGS_FILE || path.join(os.homedir(), '.astrion',
    platform === 'win32' ? 'quick-entry-windows.json' : 'quick-entry.json');
}

export function defaultQuickConfig(platform = process.platform) {
  return { enabled: false, modifier: modifierChoices(platform)[0], workspace: '', model: '' };
}

export function patchQuickConfig(current, patch, platform = process.platform) {
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) throw new Error('Invalid quick configuration');
  const next = { ...current };
  for (const [key, value] of Object.entries(patch)) {
    if (key === 'enabled' && typeof value === 'boolean') next.enabled = value;
    else if (key === 'modifier' && modifierChoices(platform).includes(value)) next.modifier = value;
    else if (['workspace', 'model'].includes(key) && typeof value === 'string' && value.length < 1024) next[key] = value;
    else throw new Error(`Invalid quick configuration field: ${key}`);
  }
  return next;
}
