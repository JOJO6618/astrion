// 测试辅助：临时 fixture 根目录 + reviewServer 中间件调用通道。
//
// 通道有两种：
//  1. tcp    —— 真实 http.createServer 监听 127.0.0.1 随机端口，用 fetch/原生 http 访问；
//  2. inproc —— 当前沙箱禁止任何 listen（TCP/UDP/Unix 全部 EPERM）时的降级通道：
//               仍然调用真实中间件函数，请求侧用真实 http.IncomingMessage（喂入原始 body），
//               响应侧捕获 writeHead/end，按同一套断言校验状态码、响应头和 JSON。
// 两种通道的断言完全一致，测试代码不区分；运行时会在报告里标明实际使用的通道。
//
// 所有写入都限制在本测试目录 .tmp/ 内，绝不触碰 design-library/.local/reviews.json。
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { Duplex } from 'node:stream';
import { fileURLToPath } from 'node:url';

/** 仅供进程内通道使用的假 socket：IncomingMessage 必须绑定一个真实 Stream。 */
class InprocSocket extends Duplex {
  constructor() {
    super();
    this.remoteAddress = '127.0.0.1';
    this.remotePort = 40001;
    this.localAddress = '127.0.0.1';
    this.localPort = 40002;
  }
  _read() {}
  _write(_chunk, _encoding, callback) { callback(); }
  setTimeout() { return this; }
  setNoDelay() { return this; }
  setKeepAlive() { return this; }
  address() { return { address: '127.0.0.1', port: this.localPort, family: 'IPv4' }; }
}

const here = path.dirname(fileURLToPath(import.meta.url));
export const tmpRootBase = path.join(here, '.tmp');

export function makeRoot(t, catalog = [
  { id: 'btn-a', category: 'text-button' },
  { id: 'btn-b', category: 'text-button' },
  { id: 'btn-c', category: 'text-button' },
  { id: 'icon-a', category: 'icon-button' }
]) {
  fs.mkdirSync(tmpRootBase, { recursive: true });
  const root = fs.mkdtempSync(path.join(tmpRootBase, 'root-'));
  for (const item of catalog) {
    const dir = path.join(root, 'candidates', 'ui', item.id);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'source.json'), JSON.stringify({
      id: item.id, name: item.id, category: item.category, family: 'primary', location: '测试',
      sources: [], states: ['default'], size: 'test', sampleData: false, adaptations: [], extractedAt: '2026-10-31'
    }, null, 2));
  }
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, file: path.join(root, '.local', 'reviews.json') };
}

async function loadMiddleware({ root, repository }) {
  const { reviewServer } = await import('./.build/review-server.mjs');
  const plugin = reviewServer(root, repository);
  if (!plugin || typeof plugin.configureServer !== 'function') throw new Error('reviewServer 返回值不是 vite 插件');
  let middleware;
  plugin.configureServer({ middlewares: { use(fn) { middleware = fn; } } });
  if (typeof middleware !== 'function') throw new Error('reviewServer 未注册中间件');
  return middleware;
}

export const transportInfo = { kind: 'unknown', note: '' };

/** 优先真实 TCP；沙箱禁止 listen 时自动降级为 inproc。 */
export async function startServer(t, { root, repository }) {
  const middleware = await loadMiddleware({ root, repository });
  const state = { nextCalls: 0 };
  const next = (response) => {
    state.nextCalls += 1;
    response.writeHead?.(599, { 'Content-Type': 'text/plain' });
    response.end?.('NEXT');
  };

  const tcp = await tryTcp(t, middleware, next);
  if (tcp) {
    transportInfo.kind = 'tcp';
    return { ...tcp, state, kind: 'tcp' };
  }
  transportInfo.kind = 'inproc';
  transportInfo.note = '沙箱禁止 listen(EPERM)，使用真实中间件 + 真实 IncomingMessage + 响应捕获';
  return { ...inproc(middleware, next), state, kind: 'inproc' };
}

async function tryTcp(t, middleware, next) {
  const server = http.createServer((request, response) => { middleware(request, response, () => next(response)); });
  const error = await new Promise((resolve) => {
    server.once('error', resolve);
    server.listen(0, '127.0.0.1', () => resolve(null));
  });
  if (error) {
    await new Promise((resolve) => server.close(resolve));
    if (error.code === 'EPERM') return null;
    throw error;
  }
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const { port } = server.address();
  const base = `http://127.0.0.1:${port}`;
  return {
    base,
    call: (options) => tcpCall(base, options),
    rawRequest: (options) => tcpRaw(base, options)
  };
}

function normalizeHeaders(headers) {
  const out = {};
  for (const [key, value] of Object.entries(headers)) {
    if (value === undefined || value === null) continue;
    out[key.toLowerCase()] = String(value);
  }
  return out;
}

function inproc(middleware, next) {
  const dispatch = ({ method = 'GET', path: requestPath = '/__review', headers = {}, body }) => {
    return new Promise((resolve, reject) => {
      const socket = new InprocSocket();
      const request = new http.IncomingMessage(socket);
      request.method = method;
      request.url = requestPath;
      request.httpVersion = '1.1';
      request.httpVersionMajor = 1;
      request.httpVersionMinor = 1;
      request.headers = normalizeHeaders(headers);

      const captured = { status: 0, headers: {}, chunks: [] };
      const response = {
        writeHead(status, responseHeaders = {}) {
          captured.status = status;
          for (const [key, value] of Object.entries(responseHeaders)) captured.headers[key.toLowerCase()] = String(value);
          return response;
        },
        setHeader(key, value) { captured.headers[key.toLowerCase()] = String(value); },
        write(payload) { if (payload !== undefined) captured.chunks.push(Buffer.from(payload)); return true; },
        end(payload) {
          if (payload !== undefined) captured.chunks.push(Buffer.from(payload));
          const text = Buffer.concat(captured.chunks).toString('utf8');
          let parsed;
          try { parsed = JSON.parse(text); } catch { parsed = text; }
          resolve({
            status: captured.status,
            payload: parsed,
            headers: {
              ...captured.headers,
              get(name) { return captured.headers[String(name).toLowerCase()] ?? null; }
            }
          });
        }
      };

      Promise.resolve(middleware(request, response, () => {
        next(response);
        if (!captured.chunks.length && !captured.status) resolve({ status: 599, payload: 'NEXT', headers: { get: () => null } });
      })).catch(reject);

      queueMicrotask(() => {
        const raw = body === undefined ? '' : typeof body === 'string' ? body : JSON.stringify(body);
        if (raw) request.push(Buffer.from(raw));
        request.push(null);
      });
    });
  };

  return Object.assign(dispatch, {
    call: (options = {}) => dispatch({
      method: options.method,
      headers: buildHeaders(options),
      body: options.body
    }),
    rawRequest: (options = {}) => dispatch({
      method: options.method,
      path: options.path,
      headers: options.headers,
      body: options.body
    })
  });
}

function buildHeaders({ headers = {}, omitOrigin = false, method = 'GET' }, origin = 'http://127.0.0.1:5173') {
  const final = { Host: '127.0.0.1:5173', ...headers };
  if (!omitOrigin && final.Origin === undefined && final.origin === undefined) final.Origin = origin;
  if (final['Content-Type'] === undefined && final['content-type'] === undefined && method !== 'GET' && method !== 'DELETE') {
    final['Content-Type'] = 'application/json';
  }
  if (method === 'PUT' && final['X-Component-Room'] === undefined && final['x-component-room'] === undefined) {
    final['X-Component-Room'] = '1';
  }
  return final;
}

async function tcpCall(base, { method = 'GET', body, headers = {}, omitOrigin = false } = {}) {
  const url = new URL(base);
  const finalHeaders = buildHeaders({ headers, omitOrigin, method }, url.origin);
  finalHeaders.Host = url.host;
  if (body !== undefined && finalHeaders['Content-Type'] === undefined) finalHeaders['Content-Type'] = 'application/json';
  const response = await fetch(`${base}/__review`, {
    method,
    headers: finalHeaders,
    body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body)
  });
  const text = await response.text();
  let payload;
  try { payload = JSON.parse(text); } catch { payload = text; }
  return { status: response.status, payload, headers: response.headers };
}

function tcpRaw(base, { method = 'GET', path: requestPath = '/__review', headers = {}, body } = {}) {
  const url = new URL(base);
  return new Promise((resolve, reject) => {
    const request = http.request({
      host: '127.0.0.1', port: url.port, path: requestPath, method,
      headers: { ...headers, Host: headers.Host ?? url.host }
    }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let payload;
        try { payload = JSON.parse(text); } catch { payload = text; }
        resolve({ status: response.statusCode, payload, headers: response.headers });
      });
    });
    request.on('error', reject);
    if (body !== undefined) request.write(typeof body === 'string' ? body : JSON.stringify(body));
    request.end();
  });
}

export const decision = (status, targetId = null, note = '') => ({ status, targetId, note });

export function readDocument(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

export function fileBytes(file) {
  return fs.existsSync(file) ? fs.readFileSync(file) : null;
}
