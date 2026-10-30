const seasonState = {data:null,loading:false,page:1,sequence:0};
const seasonParts = Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'numeric'}).formatToParts(new Date()).filter(p => p.type !== 'literal').map(p => [p.type,p.value]));
$('#season-year').value = seasonParts.year;
$('#season-quarter').value = Math.floor((Number(seasonParts.month) - 1) / 3) + 1;

function loadSeasonOnce() {
  if (!seasonState.data && !seasonState.loading) loadSeason(1);
}
function seasonName(item) { return item.name_cn || item.name_original || '未命名动画'; }
function seasonRating(item) {
  return item.score && item.score_votes ? Number(item.score).toFixed(1) : '—';
}
function seasonWatched(id) { return library.series.some(s => s.bangumi_id === id); }
function seasonFilterKey() { return JSON.stringify(['season-year','season-quarter','season-sort','season-query'].map(id => $('#' + id).value.trim())); }
function seasonNavigation() {
  const data = seasonState.data, dirty = !!data && seasonState.filterKey !== seasonFilterKey();
  const pages = data ? Math.max(1,Math.ceil(data.total / data.page_size)) : 1;
  $('#season-prev').disabled = !data || dirty || seasonState.loading || data.page <= 1;
  $('#season-next').disabled = !data || dirty || seasonState.loading || data.page >= pages;
  $('#season-submit').textContent = dirty ? '应用筛选' : '浏览新番';
}
function renderSeason() {
  const data = seasonState.data;
  if (!data) return;
  const pages = Math.max(1,Math.ceil(data.total / data.page_size));
  $('#season-page').textContent = '第 ' + data.page + ' / ' + pages + ' 页 · 共 ' + data.total + ' 部';
  seasonNavigation();
  $('#season-info').textContent = data.warning || (data.year + ' 年 · ' + ['','冬','春','夏','秋'][data.quarter] + '季 · 共 ' + data.total + ' 部动画 · 更新于 ' + dateText(data.fetched_at,true) + (data.cached ? ' · 缓存数据' : ''));
  $('#season-info').classList.toggle('error',!!data.stale);
  $('#season-scope').textContent = data.scope + '。评分人数较少时，分数可能波动较大。';
  $('#season-grid').innerHTML = data.items.map(item => {
    const name = seasonName(item), cover = poster(item.cover_url), watching = seasonWatched(item.bangumi_id);
    return '<article class="season-card"><button class="season-cover" data-season-open="' + item.bangumi_id + '" aria-label="查看' + esc(name) + '资料">' +
      (cover ? '<img loading="lazy" src="' + esc(cover) + '" alt="' + esc(name) + '封面">' : '<span class="poster-placeholder">◒</span>') +
      '<span class="season-platform">' + esc(item.platform || '动画') + '</span></button><div class="season-card-body"><button class="season-title" data-season-open="' + item.bangumi_id + '">' +
      esc(name) + '</button><p class="original-name">' + esc(item.name_original || '') + '</p><div class="season-rating"><strong>★ ' + seasonRating(item) +
      '</strong><span>' + (item.score_votes ? Number(item.score_votes).toLocaleString('zh-CN') + ' 人评分' : '暂无评分') + '</span></div><p class="season-date">' +
      esc(item.air_date || '日期待公布') + ' 首播 / 发行</p><p class="season-summary">' + esc((item.summary || '尚无中文简介，可查看 Bangumi 条目。').slice(0,140)) +
      '</p><div class="season-card-actions"><button class="text-button" data-season-open="' + item.bangumi_id + '">查看资料</button><button class="button ' + (watching ? 'quiet' : 'primary') +
      '" data-season-watch="' + item.bangumi_id + '" ' + (watching ? 'disabled' : '') + '>' + (watching ? '已在追番' : '找字幕资源') + '</button></div></div></article>';
  }).join('') || '<div class="empty"><h2>没有找到匹配的新番</h2><p>尝试其他季度或清空片名关键词。</p></div>';
}
async function loadSeason(page=1,refresh=false) {
  if (!$('#season-controls').reportValidity()) return;
  const sequence = ++seasonState.sequence;
  seasonState.filterKey = seasonFilterKey();
  seasonState.loading = true; seasonState.data = null;
  $('#season-submit').disabled = true; $('#season-reload').disabled = true;
  $('#season-prev').disabled = true; $('#season-next').disabled = true;
  $('#season-info').classList.remove('error'); $('#season-info').textContent = '正在从 Bangumi 读取本季度动画与评分…';
  $('#season-grid').innerHTML = '<div class="season-loading"><span class="pulse"></span> 正在读取第 ' + page + ' 页…</div>';
  $('#season-page').textContent = ''; $('#season-scope').textContent = '';
  const params = new URLSearchParams({year:$('#season-year').value,quarter:$('#season-quarter').value,sort:$('#season-sort').value,page,
    q:$('#season-query').value.trim(),...(refresh ? {refresh:'1'} : {})});
  try {
    const data = await api('/api/bangumi/season?' + params);
    if (sequence !== seasonState.sequence) return;
    seasonState.data = data; seasonState.page = data.page;
  } catch (error) {
    if (sequence !== seasonState.sequence) return;
    $('#season-info').textContent = '读取失败：' + error.message + '。请检查代理后重试。';
    $('#season-info').classList.add('error');
    $('#season-grid').innerHTML = '<div class="empty"><h2>本次未能读取季度数据</h2><p>可点击“浏览新番”重试，未改动你的追番列表。</p></div>';
  } finally {
    if (sequence === seasonState.sequence) {
      seasonState.loading = false; $('#season-submit').disabled = false; $('#season-reload').disabled = false;
      renderSeason();
    }
  }
}
function showSeasonDetail(item) {
  const cover = poster(item.cover_url), name = seasonName(item), watching = seasonWatched(item.bangumi_id);
  $('#season-detail-content').innerHTML = '<div class="dialog-head"><span class="eyebrow">BANGUMI / ' + item.bangumi_id +
    '</span><button class="icon-button" data-close-season aria-label="关闭新番资料">×</button></div><div class="detail-hero">' +
    (cover ? '<img src="' + esc(cover) + '" alt="' + esc(name) + '封面">' : '<div class="poster-placeholder">◒</div>') +
    '<div><h2>' + esc(name) + '</h2><p class="original-name">' + esc(item.name_original || '') + '</p><div class="season-rating"><strong>★ ' + seasonRating(item) +
    '</strong><span>' + (item.score_votes || 0) + ' 人评分</span></div><div class="chips"><span class="chip">' + esc(item.platform || '动画') + '</span><span class="chip">' +
    esc(item.air_date || '日期待公布') + '</span><span class="chip">' + (item.total_episodes ? '共 ' + esc(item.total_episodes) + ' 话' : '总话数待公布') +
    '</span></div></div></div><p class="season-detail-summary">' + esc(item.summary || '该条目暂无简介。') + '</p><div class="detail-actions"><a class="button quiet" href="https://bgm.tv/subject/' +
    item.bangumi_id + '" target="_blank" rel="noopener">Bangumi 完整资料 ↗</a><button class="button primary" data-season-watch="' + item.bangumi_id + '" ' +
    (watching ? 'disabled' : '') + '>' + (watching ? '已在追番' : '到蜜柑选择字幕资源') + '</button></div>';
  $('#season-detail-dialog').showModal();
}
function handleSeasonClick(event) {
  if (event.target.closest('[data-close-season]')) { $('#season-detail-dialog').close(); return; }
  const button = event.target.closest('[data-season-open],[data-season-watch]');
  if (!button || button.disabled) return;
  const id = Number(button.dataset.seasonOpen || button.dataset.seasonWatch);
  const item = seasonState.data?.items.find(s => s.bangumi_id === id);
  if (!item) return;
  if (button.dataset.seasonOpen) showSeasonDetail(item);
  else { $('#season-detail-dialog').close(); openAdd(null,item); }
}
$('#season-grid').onclick = handleSeasonClick;
$('#season-detail-content').onclick = handleSeasonClick;
$('#season-controls').onsubmit = event => { event.preventDefault(); loadSeason(1); };
$('#season-controls').addEventListener('input',seasonNavigation);
$('#season-reload').onclick = () => loadSeason(seasonState.filterKey === seasonFilterKey() ? seasonState.page : 1,true);
$('#season-prev').onclick = () => loadSeason(seasonState.page - 1);
$('#season-next').onclick = () => loadSeason(seasonState.page + 1);
