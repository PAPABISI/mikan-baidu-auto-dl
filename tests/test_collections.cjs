const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const candidate = {title:'[LoliHouse] 测试番 [01-14 合集] [简繁内封]', group:'LoliHouse', lang:'简繁', subtitle_type:'内封',
  episode_label:'01–14 合集', episode_start:1, episode_end:14, resolution:'1080p', size:'6.4GB', infohash:'c'.repeat(40),
  magnet:'magnet:?xt=urn:btih:' + 'c'.repeat(40)};

function setup() {
  const nodes = new Map();
  let copied = null;
  const document = {
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, {value:'', hidden:false, innerHTML:'', checked:false, addEventListener() {},
        classList:{add() {},remove() {}}, insertAdjacentHTML(where, html) { this.innerHTML += html; }});
      return nodes.get(selector);
    }, querySelectorAll() { return []; },
  };
  const context = vm.createContext({document, Intl, Date, URL, setTimeout() {}, clearTimeout() {},
    navigator:{clipboard:{async writeText(value) { copied = value; }}}, fetch:() => new Promise(() => {}),
    input:[{group:'LoliHouse',language:'简繁',type:'内封',resolution:'1080p',count:0,episodes:[],collection_count:1,collections:[candidate]}]});
  vm.runInContext(source, context);
  return {context, nodes, copied:() => copied};
}

test('仅有合集的字幕组可选，数量与字幕形式正确且标注偏好不符', () => {
  const {context,nodes} = setup();
  vm.runInContext("availableVariants=input; preferredGroups=['LoliHouse']; $('#language').value='简体'; $('#embedded').value='内嵌'; $('#resolution').value='不限'; renderGroupOptions(); renderVariants();",context);
  assert.match(nodes.get('#groups').innerHTML, /LoliHouse · 0 话 \+ 1 个合集/);
  assert.match(nodes.get('#variant-options').innerHTML, /简繁 · 内封 · 1080p/);
  assert.match(nodes.get('#collection-options').innerHTML, /01–14 合集/);
  assert.match(nodes.get('#collection-options').innerHTML, /与当前字幕偏好不符/);
  assert.match(nodes.get('#collection-options').innerHTML, /复制合集磁力/);
});

test('手动选择合集版本只设置字幕偏好，不伪造十四话', () => {
  const {context,nodes} = setup();
  vm.runInContext("availableVariants=input; preferredGroups=['LoliHouse']; $('#language').value='简繁'; $('#embedded').value='内封'; $('#resolution').value='1080p'; renderGroupOptions(); renderVariants();",context);
  assert.match(nodes.get('#variant-preview').textContent, /0 话、1 个合集/);
  assert.doesNotMatch(nodes.get('#collection-options').innerHTML, /与当前字幕偏好不符/);
});

test('复制合集保留实际磁力内容并在按钮显示结果', async () => {
  const fixture = setup();
  fixture.context.button = {dataset:{copyCollection:candidate.infohash},textContent:''};
  await vm.runInContext('copyCollection(button,input[0].collections)',fixture.context);
  assert.equal(fixture.copied(),candidate.magnet);
  assert.equal(fixture.context.button.textContent,'已复制');
});

test('详情单列合集候选及重叠话数', () => {
  const {context,nodes} = setup();
  context.seriesInput = {mikan_id:4059,name:'测试番',jobs:[],collections:[{...candidate,matches_preferences:false,overlap_episodes:['01','02']}]};
  vm.runInContext('library.series=[seriesInput]; selectedId=4059; renderDetail();',context);
  assert.match(nodes.get('#detail-content').innerHTML, /合集资源 · 1 个候选/);
  assert.match(nodes.get('#detail-content').innerHTML, /01、02/);
});

test('默认选中单话字幕组时仍显示其他组合集，不自动改变偏好', () => {
  const {context,nodes} = setup();
  vm.runInContext("availableVariants=[{group:'桜都字幕组',language:'简体',type:'内嵌',resolution:'1080p',count:5,episodes:['10','11','12','13','14'],collection_count:0,collections:[]},...input]; preferredGroups=['桜都字幕组']; $('#language').value='简体'; $('#embedded').value='内嵌'; $('#resolution').value='1080p'; $('#strict-group').checked=true; renderGroupOptions(); renderVariants();",context);
  assert.match(nodes.get('#collection-options').innerHTML, /全部字幕组的合集 · 1 个/);
  assert.match(nodes.get('#collection-options').innerHTML, /LoliHouse/);
  assert.match(nodes.get('#variant-preview').textContent, /其他字幕组还有 1 个合集/);
  assert.equal(nodes.get('#groups').value,'桜都字幕组');
  assert.equal(nodes.get('#language').value,'简体');
  vm.runInContext('chooseVariant(1)',context);
  assert.equal(nodes.get('#groups').value,'LoliHouse');
  assert.equal(nodes.get('#language').value,'简繁');
  assert.equal(nodes.get('#embedded').value,'内封');
});

test('旧版后台缺少合集字段时显示未知和重启提示，不误报零个', async () => {
  const {context,nodes} = setup();
  context.reply = {groups:[{group:'桜都字幕组',language:'简体',type:'内嵌',resolution:'1080p',count:5,episodes:['10','11','12','13','14']}]};
  vm.runInContext('api=async()=>reply; draft={id:3898};',context);
  await vm.runInContext('loadVariants(true)',context);
  assert.match(nodes.get('#variant-preview').textContent, /合集数量未知/);
  assert.doesNotMatch(nodes.get('#variant-preview').textContent, /0 个合集/);
  assert.match(nodes.get('#collection-options').innerHTML, /旧版后台/);
  assert.match(nodes.get('#variant-status').textContent, /重启/);
});

test('新版后台正常返回空结果时不会误判为旧版', async () => {
  const {context,nodes} = setup();
  context.reply = {groups:[],collection_support:true};
  vm.runInContext('api=async()=>reply; draft={id:3898};',context);
  await vm.runInContext('loadVariants(true)',context);
  assert.doesNotMatch(nodes.get('#variant-status').textContent, /旧版/);
  assert.equal(nodes.get('#collection-options').hidden,true);
});
