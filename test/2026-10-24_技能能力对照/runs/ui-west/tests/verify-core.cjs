'use strict';

/*
 * Local, dependency-free verification for the pure data/state layer.
 * Run with: node tests/verify-core.cjs
 */
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const FIXTURE = path.resolve(ROOT, '../../fixtures/papertrail/js/data.js');

function loadUmd(file) {
  const source = fs.readFileSync(file, 'utf8');
  const sandbox = { module: { exports: {} } };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: file });
  return sandbox.module.exports;
}

const appData = loadUmd(path.join(ROOT, 'js/data.js'));
const core = loadUmd(path.join(ROOT, 'js/core.js'));

/* 1. The supplied records must survive byte-for-byte (field for field). */
const fixtureSource = fs.readFileSync(FIXTURE, 'utf8');
const sandbox = { window: {} };
vm.runInNewContext(fixtureSource, sandbox, { filename: FIXTURE });
const fixturePapers = sandbox.window.PAPERS;
assert.ok(Array.isArray(fixturePapers), 'fixture records load');
assert.equal(fixturePapers.length, 9, 'fixture still has 9 records');
assert.equal(JSON.stringify(appData.papers), JSON.stringify(fixturePapers),
  'all records, field order and values preserved in original order');
assert.equal(JSON.stringify(appData.topics), JSON.stringify(['视觉与图形', '人机交互', '科研方法']));

const papers = () => appData.papers.map((paper) => ({ ...paper }));

/* 2. Facet counts at rest. */
const all = { query: '', status: 'all', topic: 'all' };
assert.deepEqual(core.statusCounts(papers(), all), { all: 9, unread: 4, reading: 2, read: 3 });
const topicsAll = core.topicCounts(papers(), all);
assert.equal(topicsAll.all, 9);
assert.equal(topicsAll['视觉与图形'], 3);
assert.equal(topicsAll['人机交互'], 3);
assert.equal(topicsAll['科研方法'], 3);

/* 3. Search matches title + author, requires every whitespace-separated token. */
assert.equal(core.filterPapers(papers(), { query: '噪声', status: 'all', topic: 'all' }).length, 1);
assert.equal(core.filterPapers(papers(), { query: '陈悦', status: 'all', topic: 'all' }).length, 3);
assert.equal(core.filterPapers(papers(), { query: '林知远 噪声', status: 'all', topic: 'all' })[0].id, 'p01');
assert.equal(core.filterPapers(papers(), { query: '林知远 不存在', status: 'all', topic: 'all' }).length, 0);
assert.equal(core.filterPapers(papers(), { query: '  陈悦   ', status: 'all', topic: 'all' }).length, 3);

/* 4. Status + topic filters compose. */
const unreadViz = { query: '', status: 'unread', topic: '视觉与图形' };
assert.equal(core.filterPapers(papers(), unreadViz).length, 0);
const unreadHci = { query: '', status: 'unread', topic: '人机交互' };
assert.equal(core.filterPapers(papers(), unreadHci).length, 1);
const methodUnread = { query: '', status: 'unread', topic: '科研方法' };
assert.equal(core.filterPapers(papers(), methodUnread).length, 3);

/* 5. Invariant: the active status count and active topic count equal the list. */
for (const status of ['all', 'unread', 'reading', 'read']) {
  for (const topic of ['all', ...appData.topics]) {
    for (const query of ['', '陈悦', '林知远 噪声', '不存在']) {
      const state = { query, status, topic };
      const visible = core.filterPapers(papers(), state);
      const statuses = core.statusCounts(papers(), state);
      const topics = core.topicCounts(papers(), state);
      assert.equal(statuses[status], visible.length,
        `status facet equals visible (${status}/${topic}/"${query}")`);
      assert.equal(topics[topic], visible.length,
        `topic facet equals visible (${status}/${topic}/"${query}")`);
    }
  }
}

/* 6. Progress scope ignores status but follows search + topic. */
const progressAll = core.progress(papers(), all);
assert.deepEqual(
  { total: progressAll.total, unread: progressAll.unread, reading: progressAll.reading, read: progressAll.read, percent: progressAll.readPercent },
  { total: 9, unread: 4, reading: 2, read: 3, percent: 33 }
);
const progressUnread = core.progress(papers(), { query: '', status: 'unread', topic: 'all' });
assert.equal(progressUnread.total, 9, 'status filter does not shrink the progress scope');
assert.equal(progressUnread.read, 3);
const progressViz = core.progress(papers(), { query: '', status: 'all', topic: '视觉与图形' });
assert.deepEqual(
  { total: progressViz.total, unread: progressViz.unread, reading: progressViz.reading, read: progressViz.read },
  { total: 3, unread: 0, reading: 1, read: 2 }
);

/* 7. Sorting. */
const sorted = core.sortPapers(papers(), 'year-desc');
assert.equal(sorted[0].year, 2026);
assert.equal(sorted[sorted.length - 1].year, 2023);
const asc = core.sortPapers(papers(), 'year-asc');
assert.equal(asc[0].year, 2023);
const byTitle = core.sortPapers(papers(), 'title');
assert.equal(byTitle.length, 9);

/* 8. Reading toggles and the "continue reading" pick. */
const one = papers();
const p01 = one.find((paper) => paper.id === 'p01');
assert.equal(core.nextStatusForToggle(p01), 'read');
p01.status = core.nextStatusForToggle(p01);
assert.equal(p01.status, 'read');
assert.equal(core.nextStatusForToggle(p01), 'unread');
assert.equal(core.continuePaper(papers()).id, 'p01');
const noReading = papers().map((paper) => paper.status === 'reading' ? { ...paper, status: 'read' } : paper);
assert.equal(core.continuePaper(noReading).status, 'unread');

/* 9. Every status value in the data is a known status, every topic known. */
for (const paper of appData.papers) {
  assert.ok(core.STATUSES.includes(paper.status), `${paper.id} status known`);
  assert.ok(appData.topics.includes(paper.topic), `${paper.id} topic known`);
  for (const field of ['id', 'title', 'authors', 'year', 'venue', 'topic', 'status', 'pages', 'abstract', 'note']) {
    assert.ok(Object.prototype.hasOwnProperty.call(paper, field), `${paper.id} has ${field}`);
  }
}

console.log('verify-core: PASS');
console.log('  records preserved:', appData.papers.length);
console.log('  status counts:', core.statusCounts(papers(), all));
console.log('  topic counts:', core.topicCounts(papers(), all));
console.log('  invariants checked:', 2 * 4 * 4 * 4, 'combinations');
