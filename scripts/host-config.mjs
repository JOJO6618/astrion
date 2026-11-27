import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const root = fileURLToPath(new URL('../', import.meta.url));
export const absolute = (value) =>
  path.resolve(
    root,
    value.replace(/^~(?=$|[\\/])/, () => os.homedir())
  );
export const dataRoot = (env) => absolute(env.ASTRION_DATA_ROOT || '~/.astrion/astrion');
const flatSettings = {
  'terminal.sandbox_mode': 'TERMINAL_SANDBOX_MODE',
  'terminal.execution_mode_default': 'HOST_EXECUTION_MODE_DEFAULT',
  'terminal.project_max_storage_mb': 'PROJECT_MAX_STORAGE_MB',
  'server.port': 'WEB_SERVER_PORT',
  'server.host': 'WEB_SERVER_HOST',
  'admin.secondary_password_hash': 'ADMIN_SECONDARY_PASSWORD_HASH',
  'secrets.web_secret_key': 'WEB_SECRET_KEY',
  'search.tavily_api_key': 'AGENT_TAVILY_API_KEY',
  'search.tavily_api_key_2': 'AGENT_TAVILY_API_KEY_2',
  'models.default_response_max_tokens': 'AGENT_DEFAULT_RESPONSE_MAX_TOKENS'
};

export function settingsEnvironment(env) {
  const inherited = new Set(Object.keys(env));
  const dotenv = path.join(root, '.env');
  const ignore = /^(1|true|yes|on)$/i.test(env.ASTRION_IGNORE_DOTENV || '');
  if (!ignore && fs.existsSync(dotenv)) {
    for (const line of fs.readFileSync(dotenv, 'utf8').split(/\r?\n/)) {
      const match = /^\s*([^#=\s]+)\s*=(.*)$/.exec(line);
      if (!match) continue;
      const [, key, raw] = match;
      if (!inherited.has(key) || key === 'ASTRION_DATA_ROOT')
        env[key] = raw.trim().replace(/^["']|["']$/g, '');
    }
  }
  const file = path.join(dataRoot(env), 'settings.json');
  if (fs.existsSync(file)) {
    const settings = JSON.parse(fs.readFileSync(file, 'utf8'));
    for (const [key, value] of Object.entries(settings.env_vars || {})) {
      if (!inherited.has(key) && value) env[key] = String(value);
    }
    for (const [field, key] of Object.entries(flatSettings)) {
      const [section, item] = field.split('.');
      const value = settings[section]?.[item];
      if (!inherited.has(key) && value != null) env[key] = String(value);
    }
  }
  if (
    (env.TERMINAL_SANDBOX_MODE || 'host').trim().toLowerCase() !== 'host' ||
    !/^(0|false)$/i.test(env.LINUX_SAFETY || '0')
  )
    throw Error('此入口仅支持 Host 配置');
  return env;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const env = settingsEnvironment({ ...process.env });
  const runtime = dataRoot(env);
  const data = absolute(env.DATA_DIR || path.join(runtime, 'host/data'));
  console.log(
    JSON.stringify({
      DATA_DIR: data,
      RUNTIME_ROOT: runtime,
      DEPLOY_CONFIG_DIR: absolute(env.DEPLOY_CONFIG_DIR || path.join(runtime, 'config')),
      SUB_AGENT_TASKS_BASE_DIR: absolute(
        env.SUB_AGENT_TASKS_BASE_DIR || path.join(data, 'sub_agent_tasks')
      )
    })
  );
}
