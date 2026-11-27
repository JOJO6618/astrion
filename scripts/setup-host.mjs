import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { randomBytes, randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';

function writePrivate(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  if (fs.existsSync(file))
    fs.copyFileSync(file, file + '.bak_' + randomUUID(), fs.constants.COPYFILE_EXCL);
  const temp = file + '.' + randomUUID() + '.tmp';
  try {
    fs.writeFileSync(temp, text, { mode: 0o600, flag: 'wx' });
    fs.renameSync(temp, file);
  } finally {
    fs.rmSync(temp, { force: true });
  }
}

function renderEnv(template, values) {
  const pending = new Map(Object.entries(values));
  const lines = [];
  for (const line of template.split(/\r?\n/)) {
    const key = /^\s*([^#=\s]+)\s*=/.exec(line)?.[1];
    if (['AGENT_ADMIN_USERNAME', 'AGENT_ADMIN_PASSWORD_HASH'].includes(key)) continue;
    lines.push(pending.has(key) ? key + '=' + pending.get(key) : line);
    pending.delete(key);
  }
  for (const [key, value] of pending) lines.push(key + '=' + value);
  return lines.join('\n') + '\n';
}

export async function configure(root, force) {
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  const lines = rl[Symbol.asyncIterator]();
  async function ask(label, fallback = '', required = false) {
    for (;;) {
      process.stdout.write(label + (fallback ? ' [' + fallback + ']' : '') + ': ');
      const result = await lines.next();
      if (result.done) throw Error('配置输入已结束');
      const value = result.value.trim() || fallback;
      if (value || !required) return value;
    }
  }
  try {
    const file = path.join(root, '.env');
    if (
      fs.existsSync(file) &&
      !force &&
      (await ask('已有 .env，是否备份并重新配置？(y/n)', 'n')).toLowerCase() !== 'y'
    )
      return;
    const port = await ask('Web 监听端口', '8091');
    const host = await ask('Web 监听地址', '127.0.0.1');
    const data = await ask('自定义数据根目录（留空使用 ~/.astrion/astrion）');
    const name = await ask('模型显示名', '默认模型');
    const url = await ask('API 地址（Base URL）', '', true);
    const apikey = await ask('API Key', '', true);
    const id = await ask('模型 ID', '', true);
    const thinking = (await ask('支持思考模式？(y/n)', 'n')).toLowerCase() === 'y';
    const model = {
      model_name: name,
      description: name,
      visible: true,
      url,
      apikey,
      multimodal: 'none',
      reasoning_capability: thinking ? 'fast,thinking' : 'fast',
      context_window: 128000,
      max_output_tokens: 32768,
      thinkmode_status: {
        type: 'param_toggle',
        model_id: id,
        fast_extra_parameter: {},
        thinking_extra_parameter: {}
      },
      extra_parameter: {},
      model_description: '你的基础模型是 ' + name + '。'
    };
    const env = {
      TERMINAL_SANDBOX_MODE: 'host',
      WEB_SERVER_PORT: port,
      WEB_SERVER_HOST: host,
      WEB_SECRET_KEY: randomBytes(32).toString('hex'),
      ...(data && { ASTRION_DATA_ROOT: data })
    };
    const example = path.join(root, '.env.example');
    const rendered = renderEnv(fs.existsSync(example) ? fs.readFileSync(example, 'utf8') : '', env);
    const dataRoot = path.resolve(
      root,
      (data || '~/.astrion/astrion').replace(/^~(?=$|[\\/])/, () => os.homedir())
    );
    writePrivate(
      path.join(dataRoot, 'config/custom_models.json'),
      JSON.stringify({ models: [model] }, null, 2) + '\n'
    );
    writePrivate(file, rendered);
    console.log('配置已保存。');
  } finally {
    rl.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  configure(fileURLToPath(new URL('../', import.meta.url)), process.argv.includes('--force')).catch(
    (error) => {
      console.error(error.message);
      process.exitCode = 1;
    }
  );
}
