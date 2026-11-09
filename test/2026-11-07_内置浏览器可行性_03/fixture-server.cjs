const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const sass = require('../../node_modules/sass');
const previous = path.resolve(__dirname, '../2026-11-07_内置浏览器可行性');
function createFixtureServer() {
  const requests = [];
  const tokenCss = sass.compile(path.resolve(__dirname, '../../static/src/styles/base/_tokens.scss'), { logger: sass.Logger.silent }).css;
  const server = http.createServer((req, res) => {
    const route = new URL(req.url, 'http://lab.invalid').pathname;
    requests.push({ route, time: Date.now() });
    const files = { '/shell': ['shell.html', 'text/html; charset=utf-8'], '/shell.js': ['shell.js', 'text/javascript'], '/shell.css': ['shell.css', 'text/css'] };
    if (files[route]) {
      res.setHeader('Content-Type', files[route][1]);
      if (route === '/shell') res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'");
      return res.end(fs.readFileSync(path.join(__dirname, files[route][0])));
    }
    if (route === '/tokens.css') { res.setHeader('Content-Type', 'text/css'); return res.end(tokenCss); }
    if (route === '/fixture.js') {
      res.setHeader('Content-Type', 'text/javascript');
      return res.end(fs.readFileSync(path.join(previous, 'fixture.js'), 'utf8') + '\n' +
        `document.getElementById('location-request').onclick=()=>navigator.geolocation.getCurrentPosition(()=>document.getElementById('location-result').textContent='allowed',e=>document.getElementById('location-result').textContent='denied:'+e.code);\n` +
        `document.getElementById('script-popup').onclick=()=>window.open('/popup','scripted-lab-popup');`);
    }
    if (route === '/fixture.css') { res.setHeader('Content-Type', 'text/css'); return res.end(fs.readFileSync(path.join(previous, 'fixture.css'))); }
    if (route === '/download' || route === '/slow-download') {
      res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Disposition': `attachment; filename="${route === '/download' ? 'lab-download.txt' : 'slow-download.bin'}"`,
        ...(route === '/slow-download' ? { 'Content-Length': 8 * 1024 * 1024 } : {}) });
      if (route === '/download') return res.end('browser lab download\n');
      let sent = 0;
      const timer = setInterval(() => { const chunk=Buffer.alloc(32768, 65); res.write(chunk); sent += chunk.length; if (sent >= 8*1024*1024) { clearInterval(timer); res.end(); } }, 30);
      res.once('close', () => clearInterval(timer)); return;
    }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Content-Security-Policy', "frame-ancestors 'none'; object-src 'none'");
    let html = fs.readFileSync(path.join(previous, 'fixture.html'), 'utf8');
    const title = route === '/popup' ? 'Popup fixture' : route === '/second' ? 'Second fixture' : 'Browser fixture';
    html = html.replace('Native browser lab fixture', title).replace('</main>',
      '<a href="/second">Second page</a><a href="/slow-download">Slow download</a><button id="script-popup">Open scripted popup</button><button id="location-request">Request location</button><output id="location-result"></output></main>');
    res.end(html);
  });
  return { server, requests };
}
module.exports = { createFixtureServer };
