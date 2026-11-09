const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '_experiments/native-browser-feasibility-20261107/mixed-views');
const relative = value => path.relative(root, value);
const runs = fs.readdirSync(lab).filter(name => name.startsWith('run-')).sort().flatMap(name => {
  const file = path.join(lab, name, 'results.json');
  if (!fs.existsSync(file)) return [];
  const result = JSON.parse(fs.readFileSync(file, 'utf8'));
  const observations = result.observations || [];
  const initial = observations.find(item => item.name === 'mixed_views_initial_geometry')?.detail;
  return [{
    evidence: relative(file), status: result.status,
    options: { directChrome: result.directChrome ?? null, explicitNavigation: result.explicitNavigation ?? null,
      finishLaunch: result.finishLaunch ?? null, globalContext: result.globalContext ?? null,
      lateAttach: result.lateAttach ?? null, args: result.args ?? null },
    observations: observations.map(item => item.name),
    httpRequests: result.requests ? result.requests.length : null,
    initial: initial ? { leftUrl: initial.leftUrl ?? null, rightUrl: initial.rightUrl ?? null,
      toolbarVisible: initial.toolbar?.visible ?? null, toolbarBounds: initial.toolbar?.relative ?? null,
      toolbarParentId: initial.toolbar?.parentId ?? null,
      sameWindow: !!(initial.left?.sameWindow && initial.right?.sameWindow) } : null,
    error: result.error || null,
  }];
});
const summary = { generated: new Date().toISOString(), cef: '156.0.15+gcf671c0+chromium-156.0.8078.80',
  note: 'Historical development attempts differ in source and options. Counts are not a reliability estimate. Null means the historical runner did not record this field.',
  fullRunnerSuccesses: runs.filter(run => run.status === 'completed').map(run => run.evidence),
  visibleToolbarRuns: runs.filter(run => run.initial?.toolbarVisible === 1).map(run => run.evidence),
  productionReady: false, runs };
const file = path.join(lab, 'evidence-summary.json');
fs.writeFileSync(file, JSON.stringify(summary, null, 2));
console.log(JSON.stringify({ evidence: relative(file), attempts: runs.length,
  fullRunnerSuccesses: summary.fullRunnerSuccesses, visibleToolbarRuns: summary.visibleToolbarRuns,
  productionReady: false }, null, 2));
