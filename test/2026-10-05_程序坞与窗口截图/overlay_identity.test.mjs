import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';

const options = [];
class Overlay {
  constructor(config) {
    options.push(config);
    this.destroyed = false;
    this.webContents = { send() {} };
  }
  setVisibleOnAllWorkspaces() {}
  setAlwaysOnTop() {}
  async loadFile() {}
  showInactive() {}
  isDestroyed() { return this.destroyed; }
  destroy() { this.destroyed = true; }
}
globalThis.__overlayIdentity = { Overlay };
const bundled = await build({entryPoints:['desktop-electron/src/quick/capture.js'],bundle:true,write:false,format:'esm',platform:'node',
  define:{'import.meta.url':JSON.stringify(new URL('../../desktop-electron/src/quick/capture.js',import.meta.url).href)},
  plugins:[{name:'overlay-dependencies',setup(builder){
    builder.onResolve({filter:/^electron$|^\.\/screenshot\.js$/},args=>({path:args.path,namespace:'mock'}));
    builder.onLoad({filter:/.*/,namespace:'mock'},args=>({contents:args.path==='electron'
      ? 'export const BrowserWindow = globalThis.__overlayIdentity.Overlay; export const ipcMain={on(){}}; export const screen={getAllDisplays:()=>[{bounds:{x:0,y:0,width:1470,height:956}}]};'
      : 'export const prepareNativeCapture=async()=>"helper"; export const capturePermission=async()=>"granted"; export const captureRegion=async()=>"image"; export const captureWindow=async()=>"image"; export const listCaptureWindows=async()=>[];'}));
  }}]});
const { CaptureController } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);

test('creating screenshot overlays does not request macOS accessory application status', async context => {
  const controller = new CaptureController({isDestroyed:()=>false,getBounds:()=>({x:0,y:0,width:620,height:440})},()=>{});
  context.after(()=>controller.close());
  await controller.prepare();
  assert.equal(options.length,1);
  assert.equal(options[0].skipTaskbar,process.platform!=='darwin');
  assert.equal(options[0].transparent,true);
});
