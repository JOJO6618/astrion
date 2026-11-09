import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import ts from 'typescript';
import { parse, compileScript, compileStyle, registerTS } from '@vue/compiler-sfc';
import { chromium, webkit } from 'playwright';
registerTS(() => ts);

const directory = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(directory, '../..');
const scratch = path.join(root, '_experiments/conversation-switch-benchmark-20261109');
const styles = [];
const special = {
  '@/locales': `import {ref} from 'vue'; export const currentLocale=ref('zh-CN'); export const t=(key)=>key;`,
  '@/stores/personalization': `import {reactive} from 'vue';const store=reactive({form:{minimal_expand_height_limited:true,stacked_hide_borders:false}});export const usePersonalizationStore=()=>store;`,
  '@/stores/model': `export const useModelStore=()=>({currentModelKey:'',allModels:[]});`,
  '@/app/methods/workspaceRequestScope': `export const withWorkspaceScope=(url)=>url;`,
};
const plugin = {
  name: 'isolated-real-vue-components',
  setup(builder) {
    builder.onResolve({filter:/^@\//}, args => {
      if(special[args.path]) return {path:args.path,namespace:'benchmark-stub'};
      const base=path.join(root,'static/src',args.path.slice(2));
      for(const candidate of [base,base+'.ts',base+'.vue',path.join(base,'index.ts')]) {
        if(fs.existsSync(candidate)&&fs.statSync(candidate).isFile())return {path:candidate};
      }
      throw Error('Unresolved local import '+args.path);
    });
    builder.onLoad({filter:/.*/,namespace:'benchmark-stub'}, args=>({contents:special[args.path],loader:'ts',resolveDir:root}));
    builder.onLoad({filter:/\.vue$/}, args => {
      const source=fs.readFileSync(args.path,'utf8');
      const {descriptor}=parse(source,{filename:args.path});
      const id='data-v-'+crypto.createHash('sha1').update(args.path).digest('hex').slice(0,8);
      const script=compileScript(descriptor,{id,inlineTemplate:true,genDefaultAs:'__component',
        fs:{fileExists:fs.existsSync,readFile:(file)=>fs.readFileSync(file,'utf8')},
        templateOptions:{compilerOptions:{scopeId:descriptor.styles.some(s=>s.scoped)?id:undefined}}});
      for(const style of descriptor.styles) {
        const result=compileStyle({source:style.content,filename:args.path,id,scoped:style.scoped,
          preprocessLang:style.lang});
        if(result.errors.length)throw result.errors[0];
        styles.push(result.code);
      }
      const scoped=descriptor.styles.some(s=>s.scoped)?`\n__component.__scopeId=${JSON.stringify(id)};`:'';
      return {contents:script.content+scoped+'\nexport default __component;',loader:'ts',resolveDir:path.dirname(args.path)};
    });
  }
};
await build({entryPoints:[path.join(directory,'browser_entry.ts')],bundle:true,format:'iife',
  platform:'browser',target:'es2022',outfile:path.join(scratch,'browser-bundle.js'),plugins:[plugin],
  define:{'process.env.NODE_ENV':'"production"'},logLevel:'warning'});
// Component styles are compiled from current source. Existing global chat styles supply
// collapse/content-visibility rules; this harness does not mount the complete ChatArea.
const cssFiles=[];
for(const dir of ['static/dist','static/dist/assets']) {
  const absolute=path.join(root,dir);
  if(fs.existsSync(absolute)) for(const file of fs.readdirSync(absolute))
    if(file.endsWith('.css'))cssFiles.push(path.join(absolute,file));
}
const css=cssFiles.filter(p=>!path.basename(p).startsWith('style-')).map(p=>fs.readFileSync(p,'utf8')).join('\n')+'\n'+styles.join('\n');
fs.writeFileSync(path.join(scratch,'browser-styles.css'),css);
const inputs=JSON.parse(fs.readFileSync(path.join(scratch,'browser-input.json'),'utf8'));
const hiddenOnly=process.env.BENCH_HIDDEN_ONLY==='1';
const cases=inputs.map(input=>({id:input.id,...input.cases[hiddenOnly?2:0]}));
if(!hiddenOnly)cases.push({id:inputs[2].id,...inputs[2].cases[1]});
const results={method:'Actual MinimalBlocks/StackedBlocks and descendants mounted standalone, cold page per case. Settings/model/localization/workspace-link adapters are inert. No complete ChatArea/navigation, no actual Electron shell, external network blocked.',engines:{}};
for(const [name,engine] of [['chromium',chromium],['webkit',webkit]]) {
  if(!fs.existsSync(engine.executablePath()))continue;
  const browser=await engine.launch({headless:true});
  const rows=[];
  try {
    for(const input of cases) for(const mode of ['minimal','stacked']) for(const running of [true,false]) {
      const page=await browser.newPage({viewport:{width:1100,height:800}});
      const pageErrors=[];
      page.on('pageerror',error=>pageErrors.push(String(error)));
      await page.route('**/*',route=>route.request().url()==='http://benchmark.invalid/'
        ? route.fulfill({contentType:'text/html',body:'<!doctype html><html><head></head><body><main class="chat-area-shell"><div class="messages-area"><div class="messages-flow"><div class="assistant-message" id="app"></div></div></div></main></body></html>'})
        :route.abort());
      try {
        await page.goto('http://benchmark.invalid/');
        await page.addStyleTag({content:css});
        await page.addScriptTag({path:path.join(scratch,'browser-bundle.js')});
        const measured=await page.evaluate(({input,mode,running})=>window.runComponentBenchmark(input,mode,running),{input,mode,running});
        rows.push({id:input.id,case:input.name,...measured,page_errors:pageErrors});
        console.log(JSON.stringify({engine:name,id:input.id,case:input.name,...measured,page_errors:pageErrors}));
      } finally {await page.close();}
    }
  } finally {await browser.close();}
  results.engines[name]=rows;
  fs.writeFileSync(path.join(scratch,hiddenOnly?'browser-hidden-results.json':'browser-results.json'),JSON.stringify(results,null,2));
}
