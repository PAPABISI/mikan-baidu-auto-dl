// 使用最小 DOM 夹具验证实际日历渲染，不访问网络或真实片库。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function render(series) {
  const nodes = new Map();
  const document = {
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, {value:'', hidden:false, innerHTML:'', addEventListener() {}});
      return nodes.get(selector);
    },
    querySelectorAll() { return []; },
  };
  const context = vm.createContext({document, Intl, Date, URL, setTimeout() {}, clearTimeout() {},
    fetch:() => new Promise(() => {}), input:series});
  vm.runInContext(source, context);
  vm.runInContext('library.series = input; renderCalendar();', context);
  const html = nodes.get('#calendar').innerHTML;
  return {html, day:name => html.split('<h3>').find(part => part.startsWith(name)), pending:nodes.get('#calendar-unscheduled')};
}

function anime(changes={}) {
  return {name:'测试番', bangumi_id:1, broadcast_source:'https://bgm.tv/subject/1', broadcast_weekday:4,
    release_weekdays:[5,6], broadcast_time:'20:30', broadcast_timezone:'Asia/Shanghai', ...changes};
}

test('每周更新只采用 Bangumi 星期，每部番剧只出现一次', () => {
  const result = render([anime()]);
  assert.match(result.day('周四'), /测试番/);
  assert.doesNotMatch(result.day('周五'), /测试番/);
  assert.doesNotMatch(result.day('周六'), /测试番/);
  assert.equal(result.html.split('测试番').length - 1, 1);
  assert.match(result.html, /https:\/\/bgm\.tv\/subject\/1/);
  assert.doesNotMatch(result.html, /字幕发布日推测|20:30/);
  assert.equal(result.pending.hidden, true);
});

test('放送星期缺失时不使用首播日期或字幕发布时间补齐', () => {
  const result = render([anime({broadcast_weekday:null, air_date:'2026-10-01'})]);
  assert.doesNotMatch(result.html, /测试番/);
  assert.equal(result.pending.hidden, false);
  assert.match(result.pending.innerHTML, /测试番.*Bangumi 未提供固定放送星期/);
});

test('旧官网覆盖不作为 Bangumi 放送日，等待同步', () => {
  const result = render([anime({broadcast_source:'https://example.com/onair', broadcast_weekday:2})]);
  assert.doesNotMatch(result.html, /测试番/);
  assert.match(result.pending.innerHTML, /Bangumi 放送资料待同步/);
});

test('未绑定、来源不匹配和非法星期都不会误排入日历', () => {
  for (const changes of [{bangumi_id:null}, {bangumi_id:2}, {broadcast_weekday:0}, {broadcast_weekday:8}, {broadcast_weekday:'4'}]) {
    const result = render([anime(changes)]);
    assert.doesNotMatch(result.html, /测试番/);
    assert.equal(result.pending.hidden, false);
  }
});

test('周日按 Bangumi 原值归类，番名安全转义', () => {
  const result = render([anime({name:'<测试番>', broadcast_weekday:7})]);
  assert.match(result.day('周日'), /&lt;测试番&gt;/);
  assert.doesNotMatch(result.html, /<测试番>/);
});
