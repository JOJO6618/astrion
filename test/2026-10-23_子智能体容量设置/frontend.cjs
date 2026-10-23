const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { compile, deferred, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const root = path.resolve(__dirname, '../..');
const calls = [];
let stored = { sub_agent_max_instances: 20, sub_agent_compress_threshold_tokens: 250000 };
let respond = async (_url, options = {}) => {
  if (options.method === 'PUT') stored = JSON.parse(options.body).settings;
  return { ok: true, json: async () => ({ success: true, settings: { ...stored } }) };
};
const { useSubAgentSettings } = compile(
  'static/src/components/personalization/useSubAgentSettings.ts', {
    vue: { ref: value => ({ value }) }, '@/locales': locale
  }, { fetch: async (...args) => { calls.push(args); return respond(...args); } }
);

(async () => {
  const settings = useSubAgentSettings();
  assert.equal(settings.subAgentMaxInstances.value, 20);
  await settings.saveSubAgentSettings();
  assert.equal(calls.length, 0, 'Do not overwrite unknown settings before loading.');
  await settings.loadSubAgentSettings();
  assert.equal(settings.subAgentSettingsLoaded.value, true);
  assert.equal(settings.subAgentSettingsLoading.value, false);
  settings.subAgentMaxInstances.value = 31;
  settings.subAgentCompressThreshold.value = 300000;
  await settings.saveSubAgentSettings();
  const sent = JSON.parse(calls.at(-1)[1].body).settings;
  assert.equal(sent.sub_agent_max_instances, 31);
  assert.equal(sent.sub_agent_compress_threshold_tokens, 300000);
  assert.equal(settings.subAgentSettingsSaving.value, false);
  assert.equal(settings.subAgentSettingsError.value, '');
  const reopened = useSubAgentSettings();
  await reopened.loadSubAgentSettings();
  assert.equal(reopened.subAgentMaxInstances.value, 31);

  const countBeforeInvalid = calls.length;
  for (const value of [0, -1, 1.5, NaN, Infinity, 2 ** 53, '2', '']) {
    settings.subAgentMaxInstances.value = value;
    await settings.saveSubAgentSettings();
    assert.equal(settings.subAgentSettingsError.value, 'personalization.subAgentMaxInstancesInvalid');
  }
  assert.equal(calls.length, countBeforeInvalid);
  settings.subAgentMaxInstances.value = 1;
  settings.subAgentCompressThreshold.value = 9999;
  await settings.saveSubAgentSettings();
  assert.equal(settings.subAgentSettingsError.value, 'personalization.subAgentCompressThresholdInvalid');
  assert.equal(calls.length, countBeforeInvalid);
  settings.subAgentCompressThreshold.value = 300000;

  respond = async () => ({ ok: false, json: async () => ({ success: false, error: 'validation rejected' }) });
  await settings.saveSubAgentSettings();
  assert.equal(settings.subAgentSettingsError.value, 'validation rejected');
  assert.equal(settings.subAgentSettingsSaving.value, false);
  assert.equal(settings.subAgentMaxInstances.value, 1);
  const failedLoad = useSubAgentSettings();
  await failedLoad.loadSubAgentSettings();
  assert.equal(failedLoad.subAgentSettingsLoaded.value, false);
  assert.equal(failedLoad.subAgentSettingsLoading.value, false);
  assert.equal(failedLoad.subAgentSettingsError.value, 'validation rejected');
  const callsAfterLoadFailure = calls.length;
  await failedLoad.saveSubAgentSettings();
  assert.equal(calls.length, callsAfterLoadFailure);

  respond = async () => { throw new Error('Network unavailable'); };
  await settings.saveSubAgentSettings();
  assert.ok(settings.subAgentSettingsError.value);
  assert.equal(settings.subAgentSettingsSaving.value, false);

  const pending = deferred();
  respond = () => pending.promise;
  const firstSave = settings.saveSubAgentSettings();
  assert.equal(settings.subAgentSettingsSaving.value, true);
  const callsDuringSave = calls.length;
  await settings.saveSubAgentSettings();
  assert.equal(calls.length, callsDuringSave, 'Repeated clicks must not duplicate the save.');
  pending.resolve({ ok: true, json: async () => ({ success: true, settings: {
    sub_agent_max_instances: 1, sub_agent_compress_threshold_tokens: 300000
  } }) });
  await firstSave;
  assert.equal(settings.subAgentSettingsSaving.value, false);
  assert.equal(settings.subAgentSettingsError.value, '');

  const tab = fs.readFileSync(path.join(root, 'static/src/components/settings/tabs/SubAgentsTab.vue'), 'utf8');
  const context = fs.readFileSync(path.join(root, 'static/src/components/personalization/usePersonalizationContext.ts'), 'utf8');
  assert.ok(tab.includes('v-model.number="subAgentMaxInstances"'));
  assert.ok(tab.includes("$t('personalization.subAgentMaxInstancesTitle')"));
  assert.ok(tab.includes('@click="saveSubAgentSettings"'));
  assert.ok(context.includes('} = useSubAgentSettings();'));
  console.log('Passed: settings load/save/reopen, positive integers, errors, duplicate saves and UI wiring.');
})().catch(error => { console.error(error); process.exitCode = 1; });
