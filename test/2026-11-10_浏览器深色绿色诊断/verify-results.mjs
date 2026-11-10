import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const directory = process.argv[2];
assert.ok(directory, 'Usage: node verify-results.mjs <diagnostic output directory>');
const results = JSON.parse(fs.readFileSync(path.join(directory, 'results.json'), 'utf8'));
assert.equal(results.completed, true);
assert.equal(results.samples.length, 24);
for (const theme of ['dark', 'light', 'classic']) {
  for (const phase of ['loaded-0', 'loaded-1', 'loaded-2', 'internal-held', 'main-repaint', 'internal-moving', 'internal-released']) {
    const sample = results.samples.find(row => row.label === `${theme}-${phase}`);
    assert.ok(sample, `Missing ${theme}-${phase}`);
    assert.equal(sample.visible, true, `${sample.label}: native page must remain visible`);
    assert.equal(sample.renderer.html, 'rgba(0, 0, 0, 0)', `${sample.label}: root must remain transparent`);
    assert.equal(sample.pagePixels.green, 0, `${sample.label}: local fixture contains no green`);
    assert.ok(sample.mainPixels.green < 500, `${sample.label}: unexpected large green region`);
    if (['internal-held', 'main-repaint', 'internal-moving'].includes(phase)) {
      assert.equal(sample.raised, true, `${sample.label}: must exercise the raised overlay`);
      assert.equal(sample.renderer.resizing, true, `${sample.label}: must exercise real internal resize`);
    }
    if (phase === 'internal-released') assert.equal(sample.renderer.resizing, false);
  }
}
const dark = results.samples.find(row => row.label === 'dark-internal-held');
assert.equal(dark.rootRules.matches.some(rule => rule.selector.includes('data-theme="dark"')), false,
  'Dark banner backgrounds must not match the page root');
const recording = JSON.parse(fs.readFileSync(path.join(directory, 'recording.json'), 'utf8'));
assert.ok(recording.frames.length > 0, 'Continuous capture must contain frames');
assert.equal(recording.greenFrames.length, 0, 'No captured frame should contain the green covering');
console.log(JSON.stringify({ passed: true, themes: 3, samples: results.samples.length,
  recordedFrames: recording.frames.length, greenFrames: recording.greenFrames.length }, null, 2));
