const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {READY:'待添加',CLAIMED:'已领取',SUBMITTING:'正在提交',SUBMITTED:'网盘已受理',DOWNLOADING:'下载中',COMPLETED:'已入盘',FAILED:'失败',UNKNOWN:'待核验',SUPERSEDED:'已替换',METADATA:'资料更新',WATCH:'开始追番',FOLDER:'目录已核验',BLOCKED:'执行受阻',SCHEDULE:'定时巡检'};
const days = ['','周一','周二','周三','周四','周五','周六','周日'];
let library = {series:[],jobs:[],events:[],stats:{},scheduler:{}};
let selectedId = null, draft = null, editingIndex = null, chosenBangumi = null, searchSequence = 0;
let pollTimer = null, toastTimer = null, fetching = false;
let observationJob = null;
let renderedData = '';

async function api(path, method='GET', body=null) {
  const response = await fetch(path,{method,headers:{'Content-Type':'application/json'},
    ...(body === null ? {} : {body:JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.msg || '请求失败');
  return data;
}
function toast(message) {
  $('#toast').textContent = message;
  $('#toast').classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').classList.remove('show'),5500);
}
function dateText(value, short=false) {
  if (!value) return '尚无记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',...(short ? {} : {year:'numeric'})}).format(date);
}
function badge(state) {
  return '<span class="status ' + (Object.hasOwn(labels,state) ? state : '') + '">' + esc(labels[state] || state) + '</span>';
}
function poster(url) {
  if (!url) return '';
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || !['mikanani.me','lain.bgm.tv'].includes(parsed.hostname)) return '';
  } catch { return ''; }
  return '/api/cover?url=' + encodeURIComponent(url);
}
function titleFor(sid) {
  return library.series.find(s => s.mikan_id === sid)?.name || library.jobs.find(j => j.mikan_id === sid)?.series_name || ('蜜柑 #' + sid);
}
function switchView(name) {
  for (const view of ['library','calendar','history']) $('#view-' + view).hidden = view !== name;
  $$('nav button').forEach(button => button.classList.toggle('active',button.dataset.view === name));
  const titles = {library:'追番片库',calendar:'每周更新',history:'操作历史'};
  const subtitles = {library:'新集、字幕组和入盘记录，都在这里。',calendar:'看清放送时间，也留意字幕组的发布节奏。',history:'每次添加后更新，把结果留在片架里。'};
  $('#page-title').innerHTML = esc(titles[name]) + '<span class="dot">.</span>';
  $('#page-subtitle').textContent = subtitles[name];
}
function render() {
  const stats = library.stats || {};
  $('#stat-series').textContent = library.series.length;
  $('#stat-ready').textContent = (stats.READY || 0) + (stats.CLAIMED || 0);
  $('#stat-pending').textContent = (stats.SUBMITTING || 0) + (stats.SUBMITTED || 0) + (stats.DOWNLOADING || 0);
  $('#stat-completed').textContent = stats.COMPLETED || 0;
  $('#nav-count').textContent = library.series.length;
  $('#library-count').textContent = library.series.length;
  const schedule = library.scheduler || {};
  $('#schedule-label').textContent = schedule.status === 'ACTIVE' ? '每天 10:00 · ' + (schedule.mode === 'prepare-only' ? '检查资源' : '已启用') : '尚未启用';
  $('#executor-banner').hidden = library.executor?.status !== 'BLOCKED';
  $('#executor-banner').textContent = library.executor?.message || '';
  $('#footer-status').textContent = '本地数据 · ' + dateText(library.updated_at,true) + ' 同步';
  const today = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(new Date());
  const signature = JSON.stringify([library.series,library.jobs,library.events,library.scheduler,library.executor,today]);
  if (signature !== renderedData) {
    renderedData = signature;
    renderCards();
    renderCalendar();
    renderHistory();
    if ($('#detail-dialog').open && selectedId) renderDetail();
  }
}
function renderCards() {
  const term = $('#filter').value.toLowerCase().trim();
  const series = library.series.filter(s => (s.name + ' ' + (s.name_original || '') + ' ' + (s.subtitle_groups || []).join(' ')).toLowerCase().includes(term));
  $('#empty').hidden = library.series.length !== 0;
  $('#series-grid').innerHTML = series.map(s => {
    const image = poster(s.cover_url), st = s.stats || {};
    const done = st.COMPLETED || 0, total = s.total_episodes || s.available_episodes || 0;
    const percent = total ? Math.min(100,done / total * 100) : 0;
    const ready = (st.READY || 0) + (st.CLAIMED || 0);
    const pending = (st.SUBMITTING || 0) + (st.SUBMITTED || 0) + (st.DOWNLOADING || 0);
    const failed = (st.FAILED || 0) + (st.UNKNOWN || 0);
    const releaseDays = (s.release_weekdays || []).map(day => days[day]).join(' / ');
    return '<article class="series-card" tabindex="0" role="button" aria-label="查看' + esc(s.name) + '" data-series="' + s.mikan_id + '">' +
      '<div class="poster">' + (image ? '<img loading="lazy" src="' + esc(image) + '" alt="' + esc(s.name) + '封面">' : '<div class="poster-placeholder">◒</div>') +
      '<span class="poster-label">' + (s.air_date ? esc(s.air_date.slice(0,7).replace('-',' / ')) : '追番中') + '</span>' +
      (s.score ? '<span class="score">★ ' + esc(s.score) + '</span>' : '') + '</div>' +
      '<div class="card-body"><div class="card-top"><h3>' + esc(s.name) + '</h3></div>' +
      '<p class="original-name">' + esc(s.name_original || '蜜柑 #' + s.mikan_id) + '</p><div class="chips">' +
      '<span class="chip orange">' + esc(s.language || '简体') + (s.embedded_only !== false ? '内嵌' : '字幕') + '</span>' +
      '<span class="chip">' + esc((s.subtitle_groups || []).join(' / ') || '不限字幕组') + '</span></div>' +
      '<div class="card-schedule"><span>◷</span><b>' + (s.broadcast_weekday ? '每' + esc(days[s.broadcast_weekday]) + '放送' : '放送日待核实') + '</b>' +
      (s.broadcast_time ? ' · ' + esc(s.broadcast_time) : '') + '</div>' +
      '<p class="release-note">' + (releaseDays ? '字幕通常' + esc(releaseDays) + ' · 推测' : '字幕发布时间以蜜柑更新为准') + '</p>' +
      '<div class="progress-label"><span>已核验入盘</span><b>' + done + ' <span>/ ' + (s.total_episodes || '—') + ' 话</span></b></div>' +
      '<div class="progress-track"><span style="width:' + percent + '%"></span></div>' +
      '<div class="card-bottom"><span>' + (failed ? '<span class="fail">' + failed + ' 话需处理</span>' : ready ? '<span class="ready">' + ready + ' 话待添加</span>' : pending ? pending + ' 话网盘处理中' : '<span class="done">暂无待添加</span>') +
      '</span><span>查看详情 ↗</span></div></div></article>';
  }).join('') || (library.series.length ? '<p class="muted">没有匹配的番剧。</p>' : '');
}
function renderCalendar() {
  const todayString = new Intl.DateTimeFormat('en-US',{weekday:'short',timeZone:'Asia/Shanghai'}).format(new Date());
  const today = ['','Mon','Tue','Wed','Thu','Fri','Sat','Sun'].indexOf(todayString);
  $('#calendar').innerHTML = days.slice(1).map((day,index) => {
    const number = index + 1;
    const entries = library.series.flatMap(s => {
      const out = [];
      if (s.broadcast_weekday === number) out.push('<div class="calendar-item"><strong>' + esc(s.name) + '</strong><small class="air-label">放送 · ' + esc(s.broadcast_time || '时间待核实') + '</small></div>');
      if ((s.release_weekdays || []).includes(number)) out.push('<div class="calendar-item"><strong>' + esc(s.name) + '</strong><small>字幕发布日推测</small></div>');
      return out;
    }).join('');
    return '<div class="day ' + (today === number ? 'today' : '') + '"><h3>' + day + (today === number ? '<small>今天</small>' : '') + '</h3>' + (entries || '<small class="muted">暂无计划</small>') + '</div>';
  }).join('');
}
function renderHistory() {
  $('#history').innerHTML = library.events.length ? library.events.map(e => '<div class="history-row"><time>' + esc(dateText(e.at)) +
    '</time><div>' + badge(e.kind) + '</div><div><strong>' + esc(e.mikan_id ? titleFor(e.mikan_id) : '系统') +
    '</strong><p>' + esc(e.message) + '</p></div></div>').join('') : '<div class="empty">还没有操作记录。发现资源后，从第一条记录开始。</div>';
}
function renderDetail() {
  const s = library.series.find(item => item.mikan_id === selectedId);
  if (!s) { $('#detail-dialog').close(); return; }
  const image = poster(s.cover_url);
  const jobs = s.jobs || [];
  $('#detail-content').innerHTML =
    '<div class="dialog-head"><span class="eyebrow">ANIME / ' + s.mikan_id + '</span><button class="icon-button" data-action="close-detail" aria-label="关闭详情">×</button></div>' +
    '<div class="detail-hero">' + (image ? '<img src="' + esc(image) + '" alt="番剧封面">' : '<div class="poster-placeholder">◒</div>') +
    '<div><h2>' + esc(s.name) + '</h2><p class="original-name">' + esc(s.name_original || '') + '</p><div class="chips"><span class="chip orange">' +
    esc(s.language) + (s.embedded_only !== false ? '内嵌' : '') + '</span><span class="chip">' + esc((s.subtitle_groups || []).join(' / ') || '不限字幕组') +
    '</span><span class="chip">共 ' + esc(s.total_episodes || '待核实') + ' 话</span></div><p class="detail-summary">' + esc(s.summary || '尚未关联番剧简介。可在编辑追番时关联 Bangumi 资料。') +
    '</p></div></div><div class="detail-info"><span class="chip">' + (s.broadcast_weekday ? '每' + esc(days[s.broadcast_weekday]) + '放送' : '放送日待核实') +
    (s.broadcast_time ? ' · ' + esc(s.broadcast_time) + ' ' + esc(s.broadcast_timezone || '') : '') + '</span><span class="chip">' +
    esc(s.studio || '制作信息待补充') + '</span></div><p class="muted">' + esc(s.broadcast_note || '') + '</p>' +
    '<div class="source-links"><a href="https://mikanani.me/Home/Bangumi/' + s.mikan_id + '" target="_blank" rel="noopener">蜜柑资源 ↗</a>' +
    (s.bangumi_id ? '<a href="https://bgm.tv/subject/' + Number(s.bangumi_id) + '" target="_blank" rel="noopener">Bangumi 资料 ↗</a>' : '') +
    '<a href="https://pan.baidu.com/disk/main" target="_blank" rel="noopener">百度网盘 ↗</a></div>' +
    '<div class="detail-path"><span>保存目录 <code>' + esc(s.save_path) + '</code></span><button class="text-button" data-action="copy-path">复制</button></div>' +
    '<div class="table-wrap"><table class="job-table"><thead><tr><th>话数</th><th>字幕组 / 资源</th><th>状态与证据</th><th>操作</th></tr></thead><tbody>' +
    jobs.map(j => '<tr><td><b>' + esc(j.episode) + '</b>' + (j.version > 1 ? '<div class="muted">v' + j.version + '</div>' : '') +
      '</td><td>' + esc(j.subtitle_group) + '<div class="muted">' + esc(j.language) + (j.embedded ? '内嵌' : '') + ' · ' + esc(j.size || '') +
      '</div><details><summary class="muted">资源标题</summary><p>' + esc(j.title) + '</p></details></td><td>' + badge(j.state) +
      '<div class="evidence">' + esc(j.evidence || '等待 Agent 在官网添加') + '</div><div class="muted">' + esc(dateText(j.updated_at,true)) +
      (j.observation_source === 'user' ? ' · 用户确认' : '') + '</div></td><td><button class="text-button" data-action="copy-magnet" data-job="' + j.id + '">复制磁力</button>' +
      (!['COMPLETED','CLAIMED','SUBMITTING','SUPERSEDED'].includes(j.state) ? '<div><button class="text-button" data-action="observe" data-job="' + j.id + '">记录手动结果</button></div>' : '') +
      (j.state === 'FAILED' ? '<div><button class="text-button" data-action="retry" data-job="' + j.id + '">重试失败任务</button></div>' : '') +
      '</td></tr>').join('') + '</tbody></table>' + (!jobs.length ? '<p class="muted">尚无资源记录，点击检查新集开始准备。</p>' : '') + '</div>' +
    '<div class="detail-actions"><span class="muted">网盘已受理 ≠ 文件已入盘；未知结果需先核验。</span><button class="button quiet" data-action="edit">编辑追番偏好</button></div>';
}
async function poll() {
  clearTimeout(pollTimer);
  if (fetching) return;
  fetching = true;
  let running = false;
  try {
    const [data,state] = await Promise.all([api('/api/library'),api('/api/state')]);
    library = data;
    render();
    running = state.status === 'RUNNING';
    $('#refresh').disabled = running;
    $('#refresh').textContent = running ? '检查中…' : '↻ 检查新集';
    $('#scan-banner').className = 'scan-banner ' + (running ? 'running' : ['GENERIC','NET_ERROR'].includes(state.status) ? 'error' : '');
    $('#scan-message').textContent = state.status === 'NEVER' ? '网页模式已就绪 · 等待第一次资源检查' : state.msg || '片库已同步';
    $('#last-sync').textContent = dateText(state.time || data.updated_at,true);
  } catch (error) { $('#scan-message').textContent = '暂时无法同步：' + error.message; $('#scan-banner').className = 'scan-banner error'; }
  finally { fetching = false; pollTimer = setTimeout(poll,running ? 1800 : 5000); }
}
async function refresh() {
  $('#refresh').disabled = true;
  try { await api('/api/agent/prepare','POST',{}); toast('开始检查蜜柑，准备待添加清单'); await poll(); }
  catch (error) { toast(error.message); $('#refresh').disabled = false; }
}
function openAdd(index=null) {
  searchSequence++;
  editingIndex = index; chosenBangumi = null; draft = null;
  $('#search-results').innerHTML = ''; $('#bangumi-results').innerHTML = ''; $('#metadata-selected').textContent = '';
  $('#search-status').textContent = ''; $('#search-query').value = ''; $('#search-row').hidden = index !== null;
  $('#watch-form').hidden = true;
  $('#add-title').textContent = index === null ? '添加追番' : '编辑追番';
  if (index !== null) {
    const s = library.series[index];
    draft = {id:s.mikan_id,name:s.name,cover:s.cover_url};
    fillDraft(s.subtitle_groups || [],s.language,s.embedded_only,s.save_path);
  }
  $('#add-dialog').showModal();
}
function fillDraft(groups,language='简体',embedded=true,path=null) {
  $('#selected-title').textContent = draft.name + ' · 蜜柑 #' + draft.id;
  $('#groups').value = groups.join('，'); $('#language').value = language || '简体'; $('#embedded').value = String(embedded !== false);
  $('#save-path').value = path || '/Anime/' + draft.name.replace(/[\\/:*?"<>|]/g,'_');
  $('#watch-form').hidden = false;
}
async function searchMikan() {
  const query = $('#search-query').value.trim();
  if (!query) return;
  const sequence = ++searchSequence;
  draft = null; chosenBangumi = null; $('#watch-form').hidden = true; $('#search-results').innerHTML = '';
  $('#search-status').textContent = '正在搜索蜜柑…'; $('#search').disabled = true;
  try {
    const data = await api('/api/mikan/search?q=' + encodeURIComponent(query));
    if (sequence !== searchSequence) return;
    const results = data.results || [];
    $('#search-status').textContent = results.length ? '选择匹配的番剧' : '没有找到匹配项，请尝试日文名或其他关键词。';
    $('#search-results').innerHTML = results.map((r,i) => '<button class="search-result" data-index="' + i + '">' +
      (poster(r.cover) ? '<img src="' + esc(poster(r.cover)) + '" alt="封面">' : '') + '<span>' + esc(r.name) + '</span></button>').join('');
    $$('#search-results button').forEach(button => button.onclick = async () => {
      const selected = results[Number(button.dataset.index)];
      const selectionSequence = ++searchSequence;
      draft = selected; chosenBangumi = null; $('#bangumi-results').innerHTML = ''; $('#metadata-selected').textContent = '';
      $('#search-status').textContent = '正在解析可用字幕组…';
      $('#watch-form').hidden = true;
      try {
        const data = await api('/api/mikan/bangumi/' + selected.id);
        if (selectionSequence !== searchSequence) return;
        const preferred = (data.groups || []).filter(group => group.language === '简体' && group.type === '内嵌').sort((a,b) => b.count - a.count)[0];
        fillDraft(preferred ? [preferred.group] : []);
        $('#search-status').textContent = preferred ? '已优先选择简体内嵌资源较多的字幕组，可修改偏好。' : '暂无明确的简体内嵌资源，可先保存追番。';
      } catch (error) { $('#search-status').textContent = error.message; }
    });
  } catch (error) { $('#search-status').textContent = error.message; }
  finally { $('#search').disabled = false; }
}
async function lookupBangumi() {
  if (!draft) return;
  $('#lookup-bangumi').disabled = true;
  $('#metadata-selected').textContent = '正在搜索 Bangumi…';
  try {
    const data = await api('/api/bangumi/search?q=' + encodeURIComponent(draft.name));
    const results = data.results || [];
    $('#bangumi-results').innerHTML = results.map((item,index) => '<button type="button" data-index="' + index + '">' +
      esc(item.name_cn || item.name_original) + ' · ' + esc(item.air_date || '日期待核实') + '</button>').join('');
    $('#metadata-selected').textContent = results.length ? '请选择与当前番剧匹配的动画条目。' : '暂无匹配资料。';
    $$('#bangumi-results button').forEach(button => button.onclick = () => {
      chosenBangumi = results[Number(button.dataset.index)];
      $('#metadata-selected').textContent = '已关联：' + (chosenBangumi.name_cn || chosenBangumi.name_original) +
        ' · ' + (chosenBangumi.total_episodes || '待核实') + ' 话';
    });
  } catch (error) { $('#metadata-selected').textContent = 'Bangumi 查询失败：' + error.message; }
  finally { $('#lookup-bangumi').disabled = false; }
}
async function saveWatch(event) {
  event.preventDefault();
  if (!draft) return;
  const body = {mikan_id:draft.id,name:draft.name,cover_url:draft.cover,subtitle_groups:$('#groups').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    language:$('#language').value,embedded_only:$('#embedded').value === 'true',save_path:$('#save-path').value.trim()};
  $('#save').disabled = true;
  try {
    await api(editingIndex === null ? '/api/series' : '/api/series/' + editingIndex,editingIndex === null ? 'POST' : 'PUT',body);
    if (chosenBangumi) {
      try { await api('/api/metadata/' + draft.id,'POST',{bangumi_id:chosenBangumi.bangumi_id}); }
      catch (error) { toast('追番已保存，资料关联失败：' + error.message); }
    }
    $('#add-dialog').close(); toast('追番已保存'); await poll();
  } catch (error) { toast(error.message); }
  finally { $('#save').disabled = false; }
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast('已复制'); }
  catch { toast('浏览器未允许复制，请从详情中选中文本复制。'); }
}
$('#detail-content').addEventListener('click',async event => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const s = library.series.find(item => item.mikan_id === selectedId);
  const job = library.jobs.find(item => item.id === button.dataset.job);
  if (button.dataset.action === 'close-detail') $('#detail-dialog').close();
  if (button.dataset.action === 'copy-path') copy(s.save_path);
  if (button.dataset.action === 'copy-magnet' && job) copy(job.magnet);
  if (button.dataset.action === 'observe' && job) {
    observationJob = job.id;
    $('#observe-title').textContent = job.series_name + ' · 第 ' + job.episode + ' 话 · ' + job.save_path;
    $('#observe-evidence').value = ''; $('#observe-state').value = 'SUBMITTED';
    $('#observe-dialog').showModal();
  }
  if (button.dataset.action === 'edit') {
    $('#detail-dialog').close(); openAdd(library.series.findIndex(item => item.mikan_id === selectedId));
  }
  if (button.dataset.action === 'retry' && job && confirm('确认已核验这话没有入盘或正在进行的相同任务，再重试？')) {
    try { await api('/api/agent/retry','POST',{job_id:job.id,evidence:'用户已核验未入盘，要求重试明确失败任务'}); await poll(); }
    catch (error) { toast(error.message); }
  }
});
$('#series-grid').addEventListener('click',event => {
  const card = event.target.closest('[data-series]');
  if (!card) return;
  selectedId = Number(card.dataset.series); renderDetail(); $('#detail-dialog').showModal();
});
$('#series-grid').addEventListener('keydown',event => { if (event.key === 'Enter') event.target.closest('[data-series]')?.click(); });
$$('nav button').forEach(button => button.onclick = () => switchView(button.dataset.view));
$('#filter').addEventListener('input',renderCards);
$('#refresh').onclick = refresh; $('#add').onclick = () => openAdd(); $('#empty-add').onclick = () => openAdd();
$('#close-add').onclick = () => { searchSequence++; $('#add-dialog').close(); };
$('#search').onclick = searchMikan;
$('#search-query').addEventListener('keydown',event => { if (event.key === 'Enter') searchMikan(); });
$('#lookup-bangumi').onclick = lookupBangumi; $('#watch-form').onsubmit = saveWatch;
$('#close-observe').onclick = () => $('#observe-dialog').close();
$('#observe-form').onsubmit = async event => {
  event.preventDefault(); $('#save-observe').disabled = true;
  try {
    await api('/api/agent/observe','POST',{job_id:observationJob,state:$('#observe-state').value,evidence:$('#observe-evidence').value.trim(),source:'user'});
    $('#observe-dialog').close(); toast('结果已记录'); await poll();
  } catch (error) { toast(error.message); }
  finally { $('#save-observe').disabled = false; }
};
$('#export').onclick = async () => {
  try {
    const complete = await api('/api/library/export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(complete,null,2)],{type:'application/json'}));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = '蜜柑片架-历史记录.json'; anchor.click(); URL.revokeObjectURL(url);
  } catch (error) { toast(error.message); }
};
poll();
