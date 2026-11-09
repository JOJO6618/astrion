// 站点粒度：普通域名按可注册域名（含全部子域名）；localhost 与 IP 按主机＋端口。
// 不内置完整公共后缀表，常见的二级公共后缀单独列出。
const SECOND_LEVEL = new Set([
  'com.cn', 'net.cn', 'org.cn', 'gov.cn', 'edu.cn', 'ac.cn', 'com.hk', 'org.hk', 'edu.hk', 'gov.hk', 'com.tw', 'org.tw', 'edu.tw', 'gov.tw', 'com.mo',
  'co.uk', 'org.uk', 'ac.uk', 'gov.uk', 'me.uk', 'co.jp', 'ne.jp', 'or.jp', 'ac.jp', 'go.jp', 'co.kr', 'or.kr', 'ac.kr', 'go.kr',
  'com.au', 'net.au', 'org.au', 'edu.au', 'gov.au', 'co.nz', 'org.nz', 'com.sg', 'edu.sg', 'gov.sg', 'com.my', 'co.in', 'net.in', 'org.in',
  'com.br', 'com.mx', 'com.ar', 'co.za', 'com.tr', 'com.ru', 'com.ua', 'co.id', 'co.th', 'com.vn', 'com.ph', 'com.pk', 'co.il',
  'github.io', 'gitlab.io', 'vercel.app', 'netlify.app', 'pages.dev', 'workers.dev', 'herokuapp.com', 'appspot.com', 'web.app', 'firebaseapp.com',
  'blogspot.com', 'azurewebsites.net', 'cloudfront.net', 'amazonaws.com', 'onrender.com', 'fly.dev', 'glitch.me', 'repl.co', 'ngrok.io', 'ngrok-free.app'
]);

export function siteOf(value) {
  let url;
  try { url = new URL(String(value || '')); } catch { return ''; }
  if (!['http:', 'https:'].includes(url.protocol)) return '';
  const host = url.hostname.toLowerCase().replace(/\.$/, '');
  if (!host) return '';
  const port = url.port || (url.protocol === 'https:' ? '443' : '80');
  if (host === 'localhost' || host.endsWith('.localhost') || /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host) || host.startsWith('[')) return `${host}:${port}`;
  const labels = host.split('.');
  if (labels.length <= 2) return host;
  const lastTwo = labels.slice(-2).join('.');
  return SECOND_LEVEL.has(lastTwo) ? labels.slice(-3).join('.') : lastTwo;
}
