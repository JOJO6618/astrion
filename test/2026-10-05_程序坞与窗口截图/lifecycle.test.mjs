import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';

const calls = [];
globalThis.__startupCalls = calls;
const mocks = {
  electron: 'export const app = {setName() {}, getVersion: () => "test"}; export const dialog = {};',
  './backend.js': 'export const pickFreePort = async () => 12345; export const resolveRuntime = () => ({python: "python", backendDir: "/backend"}); export const spawnBackend = options => globalThis.__startupCalls.push(["spawn",options]); export const waitBackendReady = async () => {}; export const shutdownBackend = () => {};',
  './bridge.js': 'export const startBridge = async () => 12346;',
  './rundata.js': 'export const hasPendingMigration = async () => false; export const resolveRunDataRoot = async () => "/desktop-data"; export const getMigrationProgress = () => ({phase:"idle"}); export const markMigrationBackendReady = () => {}; export const runPendingMigration = async () => {};',
  './shell_env.js': 'export const loadLoginShellPath = async () => ({status:"unsupported-platform"});',
  './migration-window.js': 'export const createMigrationWindow = () => { throw Error("unexpected migration"); };',
  './window.js': 'export const setMainWindowBackend = port => globalThis.__startupCalls.push(["bind",port]); export const focusMainWindow = () => globalThis.__startupCalls.push(["focus"]); export const getMainView = () => null;',
  './quick/controller.js': 'export const startQuickEntry = async options => globalThis.__startupCalls.push(["quick", options]); export const quickEnabled = () => true;'
};
const built = await build({entryPoints:['desktop-electron/src/lifecycle.js'],bundle:true,write:false,format:'esm',platform:'node',plugins:[{name:'startup-dependencies',setup(builder){
  builder.onResolve({filter:/^electron$|^\.\//},args => Object.hasOwn(mocks,args.path) ? ({path:args.path,namespace:'mock'}) : undefined);
  builder.onLoad({filter:/.*/,namespace:'mock'},args => ({contents:mocks[args.path]}));
}}]});
const { startBackendAndCreateWindow } = await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}`);

test('cold desktop launch opens the main window even with Quick Chat enabled', async () => {
  await startBackendAndCreateWindow();
  assert.deepEqual(calls.map(call => call[0]), ['spawn', 'quick', 'bind', 'focus']);
  assert.equal(calls[1][1].port, 12345);
  assert.equal(calls[1][1].dataRoot, '/desktop-data');
});
