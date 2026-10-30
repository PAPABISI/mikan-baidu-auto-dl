const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {READY:'待添加',CLAIMED:'已领取',SUBMITTING:'正在提交',SUBMITTED:'网盘已受理',DOWNLOADING:'下载中',COMPLETED:'已入盘',FAILED:'失败',UNKNOWN:'待核验',SUPERSEDED:'已替换',METADATA:'资料更新',WATCH:'开始追番',FOLDER:'目录已核验',BLOCKED:'执行受阻',SCHEDULE:'定时巡检'};
const days = ['','周一','周二','周三','周四','周五','周六','周日'];
labels.COLLECTION = '发现合集';
let library = {series:[],jobs:[],events:[],stats:{},scheduler:{}};
let selectedId = null, draft = null, editingIndex = null, chosenBangumi = null, searchSequence = 0;
let pollTimer = null, toastTimer = null, fetching = false;
let observationJobs = [], observationCollection = null, selectedJobs = new Set();
let renderedData = '';
let activeView = 'library', availableVariants = [], preferredGroups = [], suggestedBangumi = null;
let collectionSupport = true;

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
function bangumiWeekday(series) {
  if (!series.bangumi_id || series.broadcast_source !== 'https://bgm.tv/subject/' + Number(series.bangumi_id)) return null;
  return Number.isInteger(series.broadcast_weekday) && series.broadcast_weekday >= 1 && series.broadcast_weekday <= 7 ? series.broadcast_weekday : null;
}
function switchView(name) {
  activeView = name;
  for (const view of ['library','calendar','season','history']) $('#view-' + view).hidden = view !== name;
  $('.stats').hidden = name === 'season'; $('#scan-banner').hidden = name === 'season'; $('#refresh').hidden = name === 'season';
  $('#executor-banner').hidden = name === 'season' || library.executor?.status !== 'BLOCKED';
  $$('nav button').forEach(button => button.classList.toggle('active',button.dataset.view === name));
  const titles = {library:'追番片库',calendar:'每周更新',season:'季度新番',history:'操作历史'};
  const subtitles = {library:'新集、字幕组和入盘记录，都在这里。',calendar:'以 Bangumi 条目的官方放送星期为准。',season:'从 Bangumi 发现新番，再到蜜柑挑选喜欢的字幕。',history:'每次添加后更新，把结果留在片架里。'};
  $('#page-title').innerHTML = esc(titles[name]) + '<span class="dot">.</span>';
  $('#page-subtitle').textContent = subtitles[name];
  if (name === 'season') loadSeasonOnce();
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
  $('#executor-banner').hidden = activeView === 'season' || library.executor?.status !== 'BLOCKED';
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
    if (activeView === 'season' && typeof renderSeason === 'function') renderSeason();
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
    const weekday = bangumiWeekday(s);
    return '<article class="series-card" tabindex="0" role="button" aria-label="查看' + esc(s.name) + '" data-series="' + s.mikan_id + '">' +
      '<div class="poster">' + (image ? '<img loading="lazy" src="' + esc(image) + '" alt="' + esc(s.name) + '封面">' : '<div class="poster-placeholder">◒</div>') +
      '<span class="poster-label">' + (s.air_date ? esc(s.air_date.slice(0,7).replace('-',' / ')) : '追番中') + '</span>' +
      (s.score ? '<span class="score">★ ' + esc(s.score) + '</span>' : '') + '</div>' +
      '<div class="card-body"><div class="card-top"><h3>' + esc(s.name) + '</h3></div>' +
      '<p class="original-name">' + esc(s.name_original || '蜜柑 #' + s.mikan_id) + '</p><div class="chips">' +
      '<span class="chip orange">' + esc(s.language || '简体') + (s.embedded_only !== false ? '内嵌' : '字幕') + '</span>' +
      '<span class="chip">' + esc((s.subtitle_groups || []).join(' / ') || '不限字幕组') + '</span></div>' +
      '<div class="card-schedule"><span>◷</span><b>' + (weekday ? '每' + esc(days[weekday]) + '放送 · Bangumi' : 'Bangumi 放送日待补充') + '</b></div>' +
      '<p class="release-note">' + (releaseDays ? '字幕通常' + esc(releaseDays) + ' · 推测' : '字幕发布时间以蜜柑更新为准') + '</p>' +
      '<div class="progress-label"><span>已核验入盘</span><b>' + done + ' <span>/ ' + (s.total_episodes || '—') + ' 话</span></b></div>' +
      '<div class="progress-track"><span style="width:' + percent + '%"></span></div>' +
      '<div class="card-bottom"><span>' + (failed ? '<span class="fail">' + failed + ' 话需处理</span>' : ready ? '<span class="ready">' + ready + ' 话待添加</span>' : pending ? pending + ' 话网盘处理中' : '<span class="done">暂无待添加</span>') +
      '</span><span>' + (s.collections?.length ? s.collections.length + ' 个合集可选 · ' : '') + '查看详情 ↗</span></div></div></article>';
  }).join('') || (library.series.length ? '<p class="muted">没有匹配的番剧。</p>' : '');
}
function renderCalendar() {
  const todayString = new Intl.DateTimeFormat('en-US',{weekday:'short',timeZone:'Asia/Shanghai'}).format(new Date());
  const today = ['','Mon','Tue','Wed','Thu','Fri','Sat','Sun'].indexOf(todayString);
  $('#calendar').innerHTML = days.slice(1).map((day,index) => {
    const number = index + 1;
    const entries = library.series.filter(s => bangumiWeekday(s) === number).map(s =>
      '<div class="calendar-item"><a href="https://bgm.tv/subject/' + Number(s.bangumi_id) + '" target="_blank" rel="noopener"><strong>' +
      esc(s.name) + '</strong><small class="air-label">Bangumi 放送日 ↗</small></a></div>').join('');
    return '<div class="day ' + (today === number ? 'today' : '') + '"><h3>' + day + (today === number ? '<small>今天</small>' : '') + '</h3>' + (entries || '<small class="muted">暂无计划</small>') + '</div>';
  }).join('');
  const pending = library.series.filter(s => bangumiWeekday(s) === null);
  $('#calendar-unscheduled').hidden = pending.length === 0;
  $('#calendar-unscheduled').innerHTML = pending.length ? '<h3>放送日待补充</h3><p class="muted">以下番剧暂不归入星期，请在编辑追番时关联或更新 Bangumi 资料。</p>' +
    pending.map(s => '<div class="calendar-item"><strong>' + esc(s.name) + '</strong><small>' +
      (!s.bangumi_id ? '尚未关联 Bangumi' : s.broadcast_source !== 'https://bgm.tv/subject/' + Number(s.bangumi_id) ? 'Bangumi 放送资料待同步' : 'Bangumi 未提供固定放送星期') + '</small></div>').join('') : '';
}
function renderHistory() {
  $('#history').innerHTML = library.events.length ? library.events.map(e => '<div class="history-row"><time>' + esc(dateText(e.at)) +
    '</time><div>' + badge(e.kind) + '</div><div><strong>' + esc(e.mikan_id ? titleFor(e.mikan_id) : '系统') +
    '</strong><p>' + esc(e.message) + '</p></div></div>').join('') : '<div class="empty">还没有操作记录。发现资源后，从第一条记录开始。</div>';
}
function canObserve(job) {
  return !['COMPLETED','CLAIMED','SUBMITTING','SUPERSEDED'].includes(job.state);
}
function collectionCards(items) {
  if (!items.length) return '';
  return '<p class="muted">合集范围来自资源标题，需核对实际文件与已有记录；合集不自动入队，也不计作单话进度。网盘已能看到文件时，可记录入盘为覆盖的话数留下证据。</p>' + items.map(item =>
    '<article class="collection-card"><div class="collection-heading"><strong>' + esc(item.episode_label) + '</strong><button type="button" class="text-button" data-copy-collection="' +
    esc(item.infohash) + '">复制合集磁力</button></div><p>' + esc(item.title) + '</p><div class="chips"><span class="chip">' + esc(item.group) + '</span><span class="chip">' +
    esc(item.lang) + esc(item.subtitle_type) + ' · ' + esc(item.resolution) + '</span><span class="chip">' + esc(item.size || '大小未知') + '</span></div>' +
    (Number.isInteger(item.variant_index) ? '<p><button type="button" class="text-button" data-collection-variant="' + item.variant_index + '">选用此合集的字幕版本</button></p>' : '') +
    (item.id ? '<p><button type="button" class="text-button" data-action="observe-collection" data-collection="' + esc(item.id) + '">记录该合集入盘</button></p>' : '') +
    (item.matches_preferences === false ? '<p class="collection-note">与当前字幕偏好不符，仅供手动选择。</p>' : '') +
    (item.overlap_episodes?.length ? '<p class="collection-note">包含已有执行中、已入盘或待核验记录的话数：' + esc(item.overlap_episodes.join('、')) + '，请核对重复。</p>' : '') +
    '<details><summary class="muted">磁力文本</summary><code class="collection-magnet">' + esc(item.magnet) + '</code></details></article>').join('');
}
async function copyCollection(button, items) {
  const item = items.find(candidate => candidate.infohash === button.dataset.copyCollection);
  if (!item) return;
  const copied = await copy(item.magnet);
  button.textContent = copied ? '已复制' : '请展开磁力文本手动复制';
}
function updateJobSelection() {
  const choices = $$('#detail-content input[data-job-select]:not(:disabled)');
  const count = choices.filter(input => input.checked).length;
  const all = $('#select-all-jobs');
  all.checked = choices.length > 0 && count === choices.length;
  all.indeterminate = count > 0 && count < choices.length;
  all.disabled = choices.length === 0;
  $('#selected-job-count').textContent = '已选 ' + count + ' 话';
  $('#verify-selected').disabled = count === 0;
}
function openObservation(jobs, state='SUBMITTED') {
  observationJobs = jobs.map(job => job.id);
  observationCollection = null;
  $('#observe-range-row').hidden = true; $('#observe-state').disabled = false;
  $('#observe-heading').textContent = state === 'COMPLETED' ? '核实入盘' : '记录手动添加结果';
  $('#observe-title').textContent = jobs[0].series_name + ' · 共 ' + jobs.length + ' 话';
  $('#observe-jobs').innerHTML = jobs.map(job => '<li><strong>第 ' + esc(job.episode) + ' 话' +
    (job.version > 1 ? ' v' + job.version : '') + '</strong><span>' + esc(job.title) + '</span><code>' + esc(job.save_path) + '</code></li>').join('');
  $('#observe-evidence').value = ''; $('#observe-state').value = state;
  $('#observe-error').hidden = true;
  $('#save-observe').textContent = '确认并保存 ' + jobs.length + ' 话';
  $('#observe-dialog').showModal();
}
function parseEpisodes(text) {
  const episodes = [];
  for (const chunk of (text || '').split(/[,，、\s]+/).filter(Boolean)) {
    const range = chunk.match(/^(\d+)\s*[-~～至]\s*(\d+)$/);
    if (range) {
      const start = Number(range[1]), end = Number(range[2]);
      if (start > end) throw new Error('话数范围起止颠倒：' + chunk);
      for (let value = start; value <= end; value++) episodes.push(value);
    } else if (/^\d+$/.test(chunk)) episodes.push(Number(chunk));
    else throw new Error('无法识别的话数：' + chunk);
  }
  if (!episodes.length) throw new Error('请填写网盘已可见的话数，例如 1-12');
  return episodes;
}
function openCollectionObservation(collection) {
  const s = library.series.find(item => item.mikan_id === selectedId);
  observationJobs = []; observationCollection = collection;
  $('#observe-heading').textContent = '记录合集入盘';
  $('#observe-title').textContent = (s ? s.name + ' · ' : '') + collection.episode_label + ' · ' + collection.group;
  $('#observe-jobs').innerHTML = '<li><strong>' + esc(collection.title) + '</strong><span>合集不生成逐话任务；确认后只为网盘已可见的话数写入入盘记录。</span><code>' +
    esc(collection.save_path) + '</code></li>';
  $('#observe-episodes').value = Number.isInteger(collection.episode_start) ? collection.episode_start + '-' + collection.episode_end : '';
  $('#observe-range-row').hidden = false;
  $('#observe-state').value = 'COMPLETED'; $('#observe-state').disabled = true;
  $('#observe-evidence').value = ''; $('#observe-error').hidden = true;
  $('#save-observe').textContent = '确认合集已入盘';
  $('#observe-dialog').showModal();
}
function renderDetail() {
  const s = library.series.find(item => item.mikan_id === selectedId);
  if (!s) { $('#detail-dialog').close(); return; }
  const image = poster(s.cover_url);
  const weekday = bangumiWeekday(s);
  const jobs = s.jobs || [];
  const eligibleIds = new Set(jobs.filter(canObserve).map(job => job.id));
  selectedJobs = new Set([...selectedJobs].filter(id => eligibleIds.has(id)));
  $('#detail-content').innerHTML =
    '<div class="dialog-head"><span class="eyebrow">ANIME / ' + s.mikan_id + '</span><button class="icon-button" data-action="close-detail" aria-label="关闭详情">×</button></div>' +
    '<div class="detail-hero">' + (image ? '<img src="' + esc(image) + '" alt="番剧封面">' : '<div class="poster-placeholder">◒</div>') +
    '<div><h2>' + esc(s.name) + '</h2><p class="original-name">' + esc(s.name_original || '') + '</p><div class="chips"><span class="chip orange">' +
    esc(s.language) + esc(s.subtitle_type || (s.embedded_only !== false ? '内嵌' : '')) + '</span><span class="chip">' + esc((s.subtitle_groups || []).join(' / ') || '不限字幕组') +
    '</span><span class="chip">' + esc(s.resolution || '不限清晰度') + (s.strict_groups ? ' · 固定字幕组' : '') +
    '</span><span class="chip">共 ' + esc(s.total_episodes || '待核实') + ' 话</span></div><p class="detail-summary">' + esc(s.summary || '尚未关联番剧简介。可在编辑追番时关联 Bangumi 资料。') +
    '</p></div></div><div class="detail-info"><span class="chip">' + (weekday ? '每' + esc(days[weekday]) + '放送 · Bangumi' : 'Bangumi 放送日待补充') + '</span><span class="chip">' +
    esc(s.studio || '制作信息待补充') + '</span></div><p class="muted">每周更新依据 Bangumi 放送星期，字幕资源发布时间可能不同。</p>' +
    '<div class="source-links"><a href="https://mikanani.me/Home/Bangumi/' + s.mikan_id + '" target="_blank" rel="noopener">蜜柑资源 ↗</a>' +
    (s.bangumi_id ? '<a href="https://bgm.tv/subject/' + Number(s.bangumi_id) + '" target="_blank" rel="noopener">Bangumi 资料 ↗</a>' : '') +
    '<a href="https://pan.baidu.com/disk/main" target="_blank" rel="noopener">百度网盘 ↗</a></div>' +
    '<div class="detail-path"><span>保存目录 <code>' + esc(s.save_path) + '</code></span><button class="text-button" data-action="copy-path">复制</button></div>' +
    '<div class="job-selection"><span id="selected-job-count" role="status">已选 0 话</span><span class="muted">全选仅包含本番剧可核实的任务</span>' +
    '<button id="verify-selected" class="button primary" data-action="observe-selected" disabled>核实入盘</button></div>' +
    '<div class="table-wrap"><table class="job-table"><thead><tr><th class="job-check"><label><input id="select-all-jobs" type="checkbox" aria-label="全选可核实的任务">全选</label></th><th>话数</th><th>字幕组 / 资源</th><th>状态与证据</th><th>操作</th></tr></thead><tbody>' +
    jobs.map(j => '<tr><td class="job-check"><input type="checkbox" data-job-select="' + esc(j.id) + '" aria-label="选择第 ' + esc(j.episode) + ' 话" ' +
      (selectedJobs.has(j.id) ? 'checked ' : '') + (canObserve(j) ? '' : 'disabled title="执行中、已入盘或已替换的任务不能勾选"') + '></td><td><b>' + esc(j.episode) + '</b>' + (j.version > 1 ? '<div class="muted">v' + j.version + '</div>' : '') +
      '</td><td>' + esc(j.subtitle_group) + '<div class="muted">' + esc(j.language) + esc(j.subtitle_type || (j.embedded ? '内嵌' : '')) + ' · ' + esc(j.resolution || '未知') + ' · ' + esc(j.size || '') +
      '</div><details><summary class="muted">资源标题</summary><p>' + esc(j.title) + '</p></details></td><td>' + badge(j.state) +
      '<div class="evidence">' + esc(j.evidence || '等待 Agent 在官网添加') + '</div><div class="muted">' + esc(dateText(j.updated_at,true)) +
      (j.observation_source === 'user' ? ' · 用户确认' : '') + '</div></td><td><button class="text-button" data-action="copy-magnet" data-job="' + j.id + '">复制磁力</button>' +
      (canObserve(j) ? '<div><button class="text-button" data-action="observe" data-job="' + j.id + '">记录手动结果</button></div>' : '') +
      (j.state === 'FAILED' ? '<div><button class="text-button" data-action="retry" data-job="' + j.id + '">重试失败任务</button></div>' : '') +
      '</td></tr>').join('') + '</tbody></table>' + (!jobs.length ? '<p class="muted">尚无普通单话任务；已发现的合集会在下方单独展示，网盘已能看到文件时可在合集卡上记录入盘。</p>' : '') + '</div>' +
    '<div class="detail-actions"><span class="muted">网盘已受理 ≠ 文件已入盘；未知结果需先核验。</span><button class="button quiet" data-action="edit">编辑追番偏好</button></div>';
  if (s.collections?.length) $('#detail-content').insertAdjacentHTML('beforeend','<section class="collection-panel"><h3>合集资源 · ' + s.collections.length + ' 个候选</h3>' + collectionCards(s.collections) + '</section>');
  updateJobSelection();
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
function showBangumiChoice() {
  $('#metadata-selected').textContent = chosenBangumi ? '已关联：' + (chosenBangumi.name_cn || chosenBangumi.name_original) + ' · Bangumi #' + chosenBangumi.bangumi_id : '';
  $('#clear-bangumi').hidden = !chosenBangumi;
}
function openAdd(index=null,subject=null) {
  searchSequence++;
  editingIndex = index; suggestedBangumi = subject; chosenBangumi = subject; draft = null; availableVariants = []; preferredGroups = [];
  $('#search-results').innerHTML = ''; $('#bangumi-results').innerHTML = ''; $('#metadata-selected').textContent = '';
  $('#search-status').textContent = ''; $('#search-query').value = ''; $('#search-row').hidden = index !== null;
  $('#watch-form').hidden = true;
  $('#save').disabled = false; $('#lookup-bangumi').disabled = false;
  $('#add-title').textContent = index === null ? '添加追番' : '编辑追番';
  if (index !== null) {
    const s = library.series[index];
    draft = {id:s.mikan_id,name:s.name,cover:s.cover_url};
    fillDraft(s.subtitle_groups || [],s.language,s.embedded_only,s.save_path,s.subtitle_type,s.resolution,s.strict_groups ?? false);
    loadVariants(false);
  }
  showBangumiChoice();
  $('#add-dialog').showModal();
  if (subject) {
    $('#search-query').value = subject.name_cn || subject.name_original;
    searchMikan();
  }
}
function fillDraft(groups,language='简体',embedded=true,path=null,kind=null,resolution='不限',strict=true) {
  $('#selected-title').textContent = draft.name + ' · 蜜柑 #' + draft.id;
  preferredGroups = [...groups]; renderGroupOptions();
  $('#language').value = language || '简体'; $('#embedded').value = kind || (embedded !== false ? '内嵌' : '不限');
  $('#resolution').value = resolution || '不限'; $('#strict-group').checked = strict;
  $('#save-path').value = path || '/Anime/' + draft.name.replace(/[\\/:*?"<>|]/g,'_');
  $('#watch-form').hidden = false;
  renderVariants();
}
function renderGroupOptions() {
  const names = [...new Set([...preferredGroups,...availableVariants.map(v => v.group)])];
  $('#groups').innerHTML = '<option value="">不限字幕组</option>' + names.map(name => {
    const episodes = new Set(availableVariants.filter(v => v.group === name).flatMap(v => v.episodes));
    const collections = availableVariants.filter(v => v.group === name).reduce((count,v) => count + (v.collection_count || 0),0);
    return '<option value="' + esc(name) + '">' + esc(name) + ' · ' + episodes.size + ' 话' + (collections ? ' + ' + collections + ' 个合集' : '') + '</option>';
  }).join('');
  $('#groups').value = preferredGroups[0] || '';
  $('#group-priority-note').textContent = preferredGroups.length > 1 ? '现有优先级：' + preferredGroups.join(' → ') + '。改选字幕组会重新设置此优先级。' : '';
}
function renderVariants() {
  const group = $('#groups').value;
  const variants = availableVariants.map((value,index) => ({...value,index})).filter(v => !group || v.group === group);
  $('#variant-options').innerHTML = variants.map(v => {
    const known = ['简体','繁体','简繁'].includes(v.language) && ['内嵌','外挂','内封'].includes(v.type);
    const active = v.language === $('#language').value && v.type === $('#embedded').value && v.resolution === $('#resolution').value;
    return '<button type="button" class="variant-option ' + (active ? 'selected' : '') + '" data-variant="' + v.index + '" ' + (known ? '' : 'disabled') +
      ' title="' + esc(v.sample || '') + '"><strong>' + (!group ? esc(v.group) + ' · ' : '') + esc(v.language) + ' · ' + esc(v.type) + ' · ' + esc(v.resolution) +
      '</strong><small>' + v.count + ' 话' + (v.episodes.length ? ' · ' + esc(v.episodes.join('、')) : '') +
      (v.collection_count ? ' · ' + v.collection_count + ' 个合集' : '') + (!known ? ' · 字幕标记不明确' : '') + '</small></button>';
  }).join('');
  const matchesSubtitle = v => ($('#language').value === '不限' || v.language === $('#language').value) &&
    ($('#embedded').value === '不限' || v.type === $('#embedded').value) && ($('#resolution').value === '不限' || v.resolution === $('#resolution').value);
  const matched = variants.filter(matchesSubtitle);
  const count = new Set(matched.flatMap(v => v.episodes)).size;
  const collections = matched.reduce((sum,v) => sum + (v.collection_count || 0),0);
  $('#variant-preview').textContent = '当前选择在' + (group ? '所选字幕组' : '全部字幕组') + '中匹配 ' + count + ' 话、' + collections + ' 个合集。' +
    (!count ? collections ? '合集可复制磁力，暂不生成逐话待添加任务。' : '可保存偏好等待后续发布。' : '') + (group && !$('#strict-group').checked ? '未限定字幕组时，检查新集也可使用其他组。' : '');
  const batches = availableVariants.flatMap((v,index) => (v.collections || []).map(item => ({...item,
    variant_index:['简体','繁体','简繁'].includes(v.language) && ['内嵌','外挂','内封'].includes(v.type) ? index : null,
    matches_preferences:matchesSubtitle(v) && (!$('#strict-group').checked || !group || v.group === group)})));
  if (!collectionSupport) {
    $('#variant-preview').textContent = '当前后台版本不支持合集，合集数量未知。请关闭旧 Web 服务，重新运行 start.bat，并打开终端打印的新地址。';
    $('#collection-options').hidden = false;
    $('#collection-options').innerHTML = '<p class="collection-note" role="alert">检测到旧版后台：请重启 Python Web 服务；仅刷新网页无法更新后台。</p>';
  } else {
    const others = batches.filter(item => group && item.group !== group).length;
    if (others) $('#variant-preview').textContent += '其他字幕组还有 ' + others + ' 个合集，见下方。';
    $('#collection-options').hidden = !batches.length;
    $('#collection-options').innerHTML = batches.length ? '<h3>全部字幕组的合集 · ' + batches.length + ' 个</h3>' + collectionCards(batches) : '';
  }
}
function chooseVariant(index) {
  const variant = availableVariants[index];
  if (!variant || !['简体','繁体','简繁'].includes(variant.language) || !['内嵌','外挂','内封'].includes(variant.type)) return;
  preferredGroups = [variant.group]; renderGroupOptions(); $('#strict-group').checked = true;
  $('#language').value = variant.language; $('#embedded').value = variant.type; $('#resolution').value = variant.resolution;
  renderVariants();
}
async function loadVariants(autoSelect=false) {
  if (!draft) return;
  const id = draft.id, sequence = ++searchSequence;
  $('#variant-status').textContent = '正在读取蜜柑的字幕组和资源版本…';
  for (const id of ['groups','language','embedded','resolution','strict-group','save','reload-variants']) $('#' + id).disabled = true;
  try {
    const data = await api('/api/mikan/bangumi/' + id);
    if (sequence !== searchSequence || draft?.id !== id) return;
    availableVariants = data.groups || [];
    collectionSupport = data.collection_support === true || (availableVariants.length > 0 && availableVariants.every(v =>
      Number.isInteger(v.collection_count) && Array.isArray(v.collections)));
    if (autoSelect) {
      const preferred = availableVariants.filter(v => v.language === '简体' && v.type === '内嵌').sort((a,b) => b.count - a.count || (b.resolution === '1080p') - (a.resolution === '1080p'))[0];
      preferredGroups = preferred ? [preferred.group] : [];
      if (preferred) {
        $('#language').value = preferred.language; $('#embedded').value = preferred.type;
        $('#resolution').value = preferred.resolution === '未知' ? '不限' : preferred.resolution;
      }
    }
    renderGroupOptions(); renderVariants();
    $('#variant-status').textContent = !collectionSupport ? '检测到旧版后台，请重启 Web 服务以读取合集。' : availableVariants.length ? '选择下方版本可设置字幕偏好；下方同时展示全部字幕组的合集。' : '尚无可识别的单话或合集资源，可保留偏好等待更新。';
  } catch (error) {
    if (sequence === searchSequence) $('#variant-status').textContent = '读取失败：' + error.message + '。当前偏好已保留，可以重试。';
  } finally {
    if (sequence === searchSequence) for (const id of ['groups','language','embedded','resolution','strict-group','save','reload-variants']) $('#' + id).disabled = false;
  }
}
async function searchMikan() {
  const query = $('#search-query').value.trim();
  if (!query) return;
  const sequence = ++searchSequence;
  draft = null; chosenBangumi = suggestedBangumi; availableVariants = []; preferredGroups = [];
  $('#watch-form').hidden = true; $('#search-results').innerHTML = ''; showBangumiChoice();
  $('#search-status').textContent = '正在搜索蜜柑…'; $('#search').disabled = true;
  try {
    const data = await api('/api/mikan/search?q=' + encodeURIComponent(query));
    if (sequence !== searchSequence) return;
    const results = data.results || [];
    $('#search-status').textContent = results.length ? (suggestedBangumi ? '来自 Bangumi，请选择与上方片名对应的蜜柑条目。' : '选择匹配的番剧') : '没有找到匹配项，请尝试日文名或其他关键词。';
    $('#search-results').innerHTML = results.map((r,i) => '<button class="search-result" data-index="' + i + '">' +
      (poster(r.cover) ? '<img src="' + esc(poster(r.cover)) + '" alt="封面">' : '') + '<span>' + esc(r.name) + '</span></button>').join('');
    $$('#search-results button').forEach(button => button.onclick = async () => {
      const selected = results[Number(button.dataset.index)];
      draft = selected; chosenBangumi = suggestedBangumi; availableVariants = [];
      $('#bangumi-results').innerHTML = ''; showBangumiChoice();
      $('#search-status').textContent = '已选择蜜柑条目，可在下方选择字幕资源。';
      fillDraft([]); await loadVariants(true);
    });
  } catch (error) { $('#search-status').textContent = error.message; }
  finally { $('#search').disabled = false; }
}
async function lookupBangumi() {
  if (!draft) return;
  const id = draft.id;
  $('#lookup-bangumi').disabled = true;
  $('#metadata-selected').textContent = '正在搜索 Bangumi…';
  try {
    const data = await api('/api/bangumi/search?q=' + encodeURIComponent(draft.name));
    if (draft?.id !== id || !$('#add-dialog').open) return;
    const results = data.results || [];
    $('#bangumi-results').innerHTML = results.map((item,index) => '<button type="button" data-index="' + index + '">' +
      esc(item.name_cn || item.name_original) + ' · ' + esc(item.air_date || '日期待核实') + '</button>').join('');
    $('#metadata-selected').textContent = results.length ? '请选择与当前番剧匹配的动画条目。' : '暂无匹配资料。';
    $$('#bangumi-results button').forEach(button => button.onclick = () => {
      chosenBangumi = results[Number(button.dataset.index)];
      showBangumiChoice();
    });
  } catch (error) { $('#metadata-selected').textContent = 'Bangumi 查询失败：' + error.message; }
  finally { $('#lookup-bangumi').disabled = false; }
}
async function saveWatch(event) {
  event.preventDefault();
  if (!draft) return;
  const subject = chosenBangumi;
  const body = {mikan_id:draft.id,name:draft.name,cover_url:draft.cover,subtitle_groups:[...preferredGroups],
    language:$('#language').value,subtitle_type:$('#embedded').value,embedded_only:$('#embedded').value === '内嵌',
    resolution:$('#resolution').value,strict_groups:$('#strict-group').checked,save_path:$('#save-path').value.trim()};
  $('#save').disabled = true;
  try {
    await api(editingIndex === null ? '/api/series' : '/api/series/' + editingIndex,editingIndex === null ? 'POST' : 'PUT',body);
    if (subject) {
      try { await api('/api/metadata/' + body.mikan_id,'POST',{bangumi_id:subject.bangumi_id}); }
      catch (error) { toast('追番已保存，资料关联失败：' + error.message); }
    }
    $('#add-dialog').close(); toast('追番已保存'); await poll();
  } catch (error) { toast(error.message); }
  finally { $('#save').disabled = false; }
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast('已复制'); return true; }
  catch { toast('浏览器未允许复制，请从详情中选中文本复制。'); return false; }
}
$('#detail-content').addEventListener('click',async event => {
  const collectionButton = event.target.closest('[data-copy-collection]');
  if (collectionButton) {
    const series = library.series.find(item => item.mikan_id === selectedId);
    await copyCollection(collectionButton,series?.collections || []); return;
  }
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const s = library.series.find(item => item.mikan_id === selectedId);
  const job = library.jobs.find(item => item.id === button.dataset.job);
  if (button.dataset.action === 'close-detail') $('#detail-dialog').close();
  if (button.dataset.action === 'copy-path') copy(s.save_path);
  if (button.dataset.action === 'copy-magnet' && job) copy(job.magnet);
  if (button.dataset.action === 'observe' && job) {
    openObservation([job]);
  }
  if (button.dataset.action === 'observe-collection') {
    const collection = (s?.collections || []).find(item => item.id === button.dataset.collection);
    if (collection) openCollectionObservation(collection);
  }
  if (button.dataset.action === 'observe-selected') {
    const jobs = (s.jobs || []).filter(job => selectedJobs.has(job.id) && canObserve(job));
    if (jobs.length) openObservation(jobs, 'COMPLETED');
  }
  if (button.dataset.action === 'edit') {
    $('#detail-dialog').close(); openAdd(library.series.findIndex(item => item.mikan_id === selectedId));
  }
  if (button.dataset.action === 'retry' && job && confirm('确认已核验这话没有入盘或正在进行的相同任务，再重试？')) {
    try { await api('/api/agent/retry','POST',{job_id:job.id,evidence:'用户已核验未入盘，要求重试明确失败任务'}); await poll(); }
    catch (error) { toast(error.message); }
  }
});
$('#detail-content').addEventListener('change',event => {
  const input = event.target;
  if (input.id === 'select-all-jobs') {
    $$('#detail-content input[data-job-select]:not(:disabled)').forEach(choice => {
      choice.checked = input.checked;
      if (input.checked) selectedJobs.add(choice.dataset.jobSelect);
      else selectedJobs.delete(choice.dataset.jobSelect);
    });
  } else if (input.matches('input[data-job-select]') && !input.disabled) {
    if (input.checked) selectedJobs.add(input.dataset.jobSelect);
    else selectedJobs.delete(input.dataset.jobSelect);
  } else return;
  updateJobSelection();
});
$('#series-grid').addEventListener('click',event => {
  const card = event.target.closest('[data-series]');
  if (!card) return;
  selectedJobs.clear(); selectedId = Number(card.dataset.series); renderDetail(); $('#detail-dialog').showModal();
});
$('#series-grid').addEventListener('keydown',event => { if (event.key === 'Enter') event.target.closest('[data-series]')?.click(); });
$$('nav button').forEach(button => button.onclick = () => switchView(button.dataset.view));
$('#filter').addEventListener('input',renderCards);
$('#refresh').onclick = refresh; $('#add').onclick = () => openAdd(); $('#empty-add').onclick = () => openAdd();
$('#close-add').onclick = () => { searchSequence++; $('#add-dialog').close(); };
$('#search').onclick = searchMikan;
$('#search-query').addEventListener('keydown',event => { if (event.key === 'Enter') searchMikan(); });
$('#lookup-bangumi').onclick = lookupBangumi; $('#watch-form').onsubmit = saveWatch;
$('#clear-bangumi').onclick = () => { suggestedBangumi = null; chosenBangumi = null; showBangumiChoice(); };
$('#groups').onchange = () => { preferredGroups = $('#groups').value ? [$('#groups').value] : []; $('#strict-group').checked = !!preferredGroups.length; renderGroupOptions(); renderVariants(); };
for (const id of ['language','embedded','resolution','strict-group']) $('#' + id).onchange = renderVariants;
$('#reload-variants').onclick = () => loadVariants(false);
$('#collection-options').onclick = event => {
  const choice = event.target.closest('[data-collection-variant]');
  if (choice) { chooseVariant(Number(choice.dataset.collectionVariant)); return; }
  const button = event.target.closest('[data-copy-collection]');
  if (button) copyCollection(button,availableVariants.flatMap(v => v.collections || []));
};
$('#variant-options').onclick = event => {
  const button = event.target.closest('[data-variant]');
  if (!button || button.disabled) return;
  chooseVariant(Number(button.dataset.variant));
};
$('#close-observe').onclick = () => $('#observe-dialog').close();
$('#observe-dialog').addEventListener('cancel',event => { if ($('#save-observe').disabled) event.preventDefault(); });
$('#observe-form').onsubmit = async event => {
  event.preventDefault(); $('#save-observe').disabled = true; $('#close-observe').disabled = true;
  $('#observe-error').hidden = true;
  const ids = [...observationJobs];
  try {
    if (observationCollection) {
      const collection = observationCollection;
      const result = await api('/api/collections/observe','POST',{
        collection_id:collection.id,episodes:parseEpisodes($('#observe-episodes').value),
        evidence:$('#observe-evidence').value.trim(),source:'user'});
      observationCollection = null;
      $('#observe-dialog').close();
      toast('已记录 ' + result.recorded.length + ' 话入盘' + (result.skipped.length ? '，' + result.skipped.length + ' 话已有记录未覆盖' : ''));
      await poll(); return;
    }
    const batch = ids.length > 1;
    await api(batch ? '/api/agent/observe-batch' : '/api/agent/observe','POST',{
      ...(batch ? {job_ids:ids} : {job_id:ids[0]}),state:$('#observe-state').value,evidence:$('#observe-evidence').value.trim(),source:'user'});
    ids.forEach(id => selectedJobs.delete(id));
    $('#observe-dialog').close(); toast(ids.length + ' 话结果已记录'); await poll();
  } catch (error) {
    $('#observe-error').textContent = '保存未确认：' + error.message + '。请核对最新记录后再操作。';
    $('#observe-error').hidden = false;
  }
  finally { $('#save-observe').disabled = false; $('#close-observe').disabled = false; }
};
$('#export').onclick = async () => {
  try {
    const complete = await api('/api/library/export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(complete,null,2)],{type:'application/json'}));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = '蜜柑片架-历史记录.json'; anchor.click(); URL.revokeObjectURL(url);
  } catch (error) { toast(error.message); }
};
poll();
