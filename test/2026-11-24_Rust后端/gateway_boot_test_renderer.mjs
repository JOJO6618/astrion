// Render the production CLI BootFlow with React and an in-memory terminal adapter.
// Node 24 can exercise the boot effects and keyboard handler without OpenTUI's Bun FFI.
import { existsSync, readFileSync } from 'node:fs';
import { createRequire, registerHooks } from 'node:module';
import { PassThrough, Writable } from 'node:stream';
import { fileURLToPath, pathToFileURL } from 'node:url';

const cli = new URL('../../cli/', import.meta.url);
const source = new URL('src/', cli);
const boot = new URL('boot.tsx', source);
const requireCli = createRequire(new URL('package.json', cli));
const { createElement } = requireCli('react');
const { Box, Text, render, useInput } = await import(pathToFileURL(requireCli.resolve('ink')).href);
const { transformSync } = requireCli('esbuild');

export const RGBA = { defaultForeground: () => undefined };
export const TextAttributes = { DIM: 1, INVERSE: 2 };

export function useKeyboard(listener) {
  useInput((input, key) => {
    const name = key.return ? 'return' : key.escape ? 'escape'
      : key.leftArrow ? 'left' : key.rightArrow ? 'right' : input;
    listener({ name, preventDefault() {} });
  });
}

export function terminalElement(tag, props, ...children) {
  if (tag === 'box') return createElement(Box, props, ...children);
  if (tag === 'text') {
    return createElement(Text, {
      color: props?.fg,
      dimColor: props?.attributes === TextAttributes.DIM,
      inverse: props?.attributes === TextAttributes.INVERSE,
    }, ...children);
  }
  return createElement(tag, props, ...children);
}

async function importBoot() {
  const hooks = registerHooks({
    resolve(specifier, context, next) {
      const parent = context.parentURL;
      if (parent === boot.href && ['@opentui/core', '@opentui/react'].includes(specifier)) {
        return { url: import.meta.url, shortCircuit: true };
      }
      if (parent?.startsWith(source.href) && specifier.startsWith('./')) {
        const candidates = [`${specifier}.ts`, `${specifier}/index.ts`];
        const resolved = candidates.find((path) => existsSync(new URL(path, parent)));
        if (resolved) return next(resolved, context);
      }
      return next(specifier, context);
    },
    load(url, context, next) {
      if (url !== boot.href) return next(url, context);
      const text = readFileSync(boot, 'utf8');
      const { code } = transformSync(text, {
        loader: 'tsx', sourcefile: fileURLToPath(boot), jsxFactory: 'terminalElement',
      });
      return { format: 'module', source: `import { terminalElement } from ${JSON.stringify(import.meta.url)};\n${code}`, shortCircuit: true };
    },
  });
  try {
    return await import(boot.href);
  } finally {
    hooks.deregister();
  }
}

export async function renderBootFlow(t, workspace) {
  const { BootFlow } = await importBoot();
  const stdin = new PassThrough();
  stdin.isTTY = true;
  stdin.ref = stdin.unref = () => stdin;
  stdin.setRawMode = (raw) => { stdin.isRaw = raw; return stdin; };
  let output = '';
  const stdout = new Writable({
    write(chunk, _encoding, done) { output += chunk.toString(); done(); },
  });
  // Keep paths with wide characters on one line in the captured terminal output.
  stdout.columns = Math.max(120, Buffer.byteLength(workspace) + 8);
  stdout.rows = 30;
  let result;
  let exitCode;
  const terminal = render(createElement(BootFlow, {
    cwd: workspace,
    onReady: (bootResult) => { result = bootResult; },
    onExit: (code) => { exitCode = code; },
  }), { stdin, stdout, stderr: stdout, debug: true, exitOnCtrlC: false, patchConsole: false });
  t.after(() => {
    terminal.unmount();
    stdin.destroy();
    stdout.destroy();
  });
  return {
    get output() { return output; },
    get result() { return result; },
    get exitCode() { return exitCode; },
    confirm() { stdin.write('\r'); },
  };
}
