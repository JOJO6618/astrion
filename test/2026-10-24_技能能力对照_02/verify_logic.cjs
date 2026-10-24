'use strict';
// Both trials use the same independent data/filter checks. No browser DOM is emulated.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ROOT = __dirname;
function loadData(filename) {
  const context = vm.createContext({window: {}});
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, {timeout: 2000});
  return JSON.parse(vm.runInContext('JSON.stringify(window.PAPERS)', context));
}
const original = loadData(path.join(ROOT, 'fixtures/papertrail/js/data.js'));
const report = {scope: 'Pure data/filter/status logic in isolated Node VM; rendering functions disabled for status checks. No DOM, focus, visual or accessibility acceptance.', trials: {}};
for (const name of ['ui-copper', 'ui-silver']) {
  const result = {passed: false};
  try {
    const folder = path.join(ROOT, 'runs', name);
    const data = loadData(path.join(folder, 'js/data.js'));
    assert.deepStrictEqual(data, original, 'Preserve all 9 records and every original field');
    const context = vm.createContext({window: {PAPERS: structuredClone(data)}, document: {addEventListener() {}}, console});
    vm.runInContext(fs.readFileSync(path.join(folder, 'js/app.js'), 'utf8'), context, {timeout: 2000});
    if (name === 'ui-copper') vm.runInContext('state.papers = window.PAPERS.map(p => ({...p}));', context);
    let combinations = 0;
    for (const status of ['all', 'unread', 'reading', 'read']) {
      for (const topic of ['all', '视觉与图形', '人机交互', '科研方法']) {
        for (const query of ['', '陈悦', '噪声', '不存在的查询', '林知远']) {
          const input = JSON.stringify({status, topic, query});
          const setup = name === 'ui-copper'
            ? `state.selectedStatus = input.status; state.selectedTopic = input.topic === 'all' ? null : input.topic; state.query = input.query;`
            : 'currentStatus = input.status; currentTopic = input.topic; searchQuery = input.query;';
          vm.runInContext(`{ const input = ${input}; ${setup} }`, context);
          const actual = JSON.parse(vm.runInContext('JSON.stringify(filterPapers().map(p => p.id))', context));
          const expected = data.filter(p => (status === 'all' || p.status === status)
            && (topic === 'all' || p.topic === topic)
            && (!query || p.title.toLowerCase().includes(query) || p.authors.toLowerCase().includes(query))).map(p => p.id);
          assert.deepStrictEqual(actual, expected, `${name} filter ${input}`);
          combinations++;
        }
      }
    }
    // Bypass rendering only: exercise real state mutation helpers without pretending a DOM test ran.
    if (name === 'ui-copper') {
      vm.runInContext('render = () => {};', context);
      for (const from of ['unread', 'reading', 'read']) {
        for (const to of ['unread', 'reading', 'read']) {
          const actual = vm.runInContext(`state.papers[0].status = ${JSON.stringify(from)}; setPaperStatus('p01', ${JSON.stringify(to)}); state.papers[0].status;`, context);
          assert.equal(actual, to);
        }
      }
      result.status_cases = 9;
    } else {
      vm.runInContext('updateCounts = () => {}; renderPapers = () => {};', context);
      for (const [from, to] of [['unread', 'reading'], ['reading', 'read'], ['read', 'unread']]) {
        const actual = vm.runInContext(`window.PAPERS[0].status = ${JSON.stringify(from)}; togglePaperStatus('p01'); window.PAPERS[0].status;`, context);
        assert.equal(actual, to);
      }
      result.status_cases = 3;
    }
    result.passed = true;
    result.records_preserved = data.length;
    result.filter_combinations = combinations;
  } catch (error) {
    result.error = error.stack;
  }
  report.trials[name] = result;
}
const beforeStatus = 'reading';
report.original_status_rule = {expression: "status === 'read' ? 'unread' : 'read'", input: beforeStatus, output: beforeStatus === 'read' ? 'unread' : 'read', conclusion: 'The original already allows reading -> read. Both reviews incorrectly claimed this transition was impossible; adding a way to enter reading is a separate valid improvement.'};
console.log(JSON.stringify(report, null, 2));
if (Object.values(report.trials).some(result => !result.passed)) process.exitCode = 1;
