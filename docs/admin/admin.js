/* Directory admin: the applications (GitHub issues), approve / reject, what is listed (with delete), and the sources with what is said about each (status, rights, topics, platform, note).
   Two ways in. With a password (when the site's function /api/admin is set up) the GitHub token stays on the server: this page only holds a session that
   expires, and every GitHub request goes through that function, which allows a short list of them. Without it, this page talks to the GitHub API
   itself, with the maintainer's own token, in the maintainer's own browser.
   Everything that comes from outside (issue texts, titles, addresses) is put on the page as text, or through DOMPurify; the page's CSP forbids inline script. */
(function(){
'use strict';
const $ = s => document.querySelector(s);
const params = new URLSearchParams(location.search);
const LOCAL = /^(127\.0\.0\.1|localhost)$/.test(location.hostname);       // ?api= and ?repo= exist to try this page against a fake GitHub on this computer
const API = (LOCAL && params.get('api')) || 'https://api.github.com';
const ADMIN_API = '../api/admin';
const OPS_API = '../api/ops';
const BOT = '<!-- shelf-directory-bot -->';
const state = { repo: '', token: '', session: '', mode: 'token', passwordSite: false, pwProblem: '', branch: 'main', directory: null, directoryUrl: '', directoryFrom: '', registry: null, busy: false };

/* ---------- token / session (in this browser only) ---------- */
function keeper(name){
  return {
    get(){ try{ return localStorage.getItem(name) || sessionStorage.getItem(name) || ''; }catch(e){ return ''; } },
    remembered(){ try{ return !!localStorage.getItem(name); }catch(e){ return false; } },
    set(t, remember){ try{ (remember ? localStorage : sessionStorage).setItem(name, t); (remember ? sessionStorage : localStorage).removeItem(name); }catch(e){} },
    clear(){ try{ localStorage.removeItem(name); sessionStorage.removeItem(name); }catch(e){} }
  };
}
const store = keeper('shelf.token'), sessions = keeper('shelf.session');

/* ---------- GitHub ---------- */
async function gh(method, path, body){
  if(state.mode === 'password') return ghViaServer(method, path, body);
  const headers = { Authorization: 'Bearer ' + state.token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' };
  if(body !== undefined) headers['Content-Type'] = 'application/json';
  const r = await fetch(API + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null; try{ data = r.status === 204 ? null : await r.json(); }catch(e){}
  return { status: r.status, ok: r.ok, data };
}
/* The same call, asked of the site's function: it checks the session, allows only what this page does, and adds the token. */
async function ghViaServer(method, path, body){
  let r, j = null;
  try{
    r = await fetch(ADMIN_API, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + state.session }, body: JSON.stringify({ action: 'gh', method, path, body: body === undefined ? null : body }) });
    try{ j = await r.json(); }catch(e){}
  }catch(e){ throw new Error('连不上管理服务，请检查网络后再试'); }
  if(r.status === 401 && j && j.error === 'session'){ signedOut('登录已过期，请重新输入密码'); throw new Error('登录已过期，请重新输入密码'); }
  if(!j || !j.ok) throw new Error((j && j.message) || ('管理服务出错：HTTP ' + r.status));
  return { status: j.status, ok: j.status >= 200 && j.status < 300, data: j.data };
}
/* Operations data has its own API: unlike the source registry it is never
   readable with a browser GitHub token, because it contains editorial choices
   and its database credential must stay on the server. */
async function ops(action, body){
  if(state.mode !== 'password') throw new Error('运营页需要管理密码登录；GitHub 令牌模式只管理审核和来源。');
  let r, j = null;
  try{
    r = await fetch(OPS_API, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + state.session }, body: JSON.stringify({ action, ...(body || {}) }) });
    try{ j = await r.json(); }catch(e){}
  }catch(e){ throw new Error('连不上运营服务，请检查 Vercel 部署后再试'); }
  if(r.status === 401 && j && j.error === 'session'){ signedOut('登录已过期，请重新输入密码'); throw new Error('登录已过期，请重新输入密码'); }
  if(!j || !j.ok) throw new Error((j && j.message) || ('运营服务出错：HTTP ' + r.status));
  return j.data;
}
function signedOut(msg){ sessions.clear(); state.session = ''; showLogin(msg); }
const need = (r, what) => { if(!r.ok) throw new Error(what + '：HTTP ' + r.status + (r.data && r.data.message ? ' ' + r.data.message : '')); return r.data; };

/* ---------- small helpers ---------- */
function el(tag, props, ...kids){
  const e = document.createElement(tag);
  for(const [k, v] of Object.entries(props || {})){
    if(k === 'class') e.className = v; else if(k === 'text') e.textContent = v; else if(k.startsWith('on')) e.addEventListener(k.slice(2), v); else if(v !== false && v != null) e.setAttribute(k, v === true ? '' : v);
  }
  kids.flat().forEach(c => { if(c != null) e.append(c.nodeType ? c : document.createTextNode(String(c))); });
  return e;
}
function say(text, kind){ const s = $('#status'); s.textContent = text || ''; s.className = 'msg' + (kind ? ' ' + kind : ''); }
function ago(iso){
  const s = (Date.now() - Date.parse(iso)) / 1000; if(!isFinite(s)) return '';
  if(s < 90) return '刚刚'; if(s < 5400) return Math.round(s / 60) + ' 分钟前'; if(s < 129600) return Math.round(s / 3600) + ' 小时前'; return Math.round(s / 86400) + ' 天前';
}
const labelsOf = issue => (issue.labels || []).map(l => typeof l === 'string' ? l : l.name);

DOMPurify.addHook('afterSanitizeAttributes', node => { if(node.tagName === 'A'){ node.setAttribute('rel', 'nofollow ugc noopener noreferrer'); node.setAttribute('target', '_blank'); } });
function markdown(md){
  const html = marked.parse(String(md || ''), { async: false, gfm: true, breaks: false });
  return DOMPurify.sanitize(html, { FORBID_TAGS: ['img', 'style', 'form', 'input', 'button', 'iframe'], FORBID_ATTR: ['style'], USE_PROFILES: { html: true } });
}
function doc(md){ const d = el('div', { class: 'doc' }); d.innerHTML = markdown(md); return d; }       // sanitized above

/* the same reading of an issue form as aggregate.py: only the heading of a known field that has not come yet starts a field */
const FIELDS = [['url', /^(网址|url|address|link)/i], ['title', /^(标题|title)/i], ['author', /^(作者|author)/i], ['tags', /^(标签|tags?)/i], ['summary', /^(摘要|summary)/i], ['body', /^(正文|body|content)/i], ['rights', /^(权利|rights?)/i]];
function parseIssue(body){
  const f = {}; let cur = null; const seen = new Set();
  for(const line of String(body || '').replace(/\r/g, '').split('\n')){
    const m = line.match(/^###\s+(.+?)\s*$/); const key = m ? (FIELDS.find(([k, re]) => !seen.has(k) && (cur !== 'body' || k === 'rights') && re.test(m[1])) || [])[0] : null;   // in a post's text only the confirmation ends the text
    if(key){ cur = key; seen.add(key); f[key] = []; } else if(cur) f[cur].push(line);
  }
  const out = {}; for(const k in f){ const v = f[k].join('\n').trim().replace(/​/g, ''); out[k] = /^_?no response_?$/i.test(v) ? '' : v; }
  out.kind = out.title && out.body ? 'post' : out.title && out.summary ? 'record' : 'shelf';       // a card: a title and a summary, no text
  if(out.kind === 'shelf'){ const m = (out.url || body || '').match(/https?:\/\/[^\s<>"')\]]+/); out.url = m ? m[0].replace(/[.,;:!?，。；]+$/, '') : (out.url || ''); }
  else if(out.url){ const m = out.url.match(/https?:\/\/[^\s<>"')\]]+/); out.url = m ? m[0] : ''; }
  return out;
}

/* ---------- pending ---------- */
async function listIssues(stateName, labels){
  const all = [];
  for(const label of labels){
    const data = need(await gh('GET', `/repos/${state.repo}/issues?state=${stateName}&labels=${encodeURIComponent(label)}&per_page=${stateName === 'open' ? 100 : 30}`), '读取申请');
    all.push(...(data || []).filter(i => !i.pull_request));
  }
  const seen = new Set();
  return all.filter(i => !seen.has(i.number) && seen.add(i.number)).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}
async function botComment(n){
  const list = need(await gh('GET', `/repos/${state.repo}/issues/${n}/comments?per_page=100`), '读取检查结果') || [];
  return list.filter(c => (c.body || '').includes(BOT)).pop() || null;
}
function pendingCard(issue){
  const p = parseIssue(issue.body), labels = labelsOf(issue), status = el('p', { class: 'msg', role: 'status' });
  const head = p.kind === 'shelf' ? (p.url || issue.title) : (p.title || issue.title);
  const viaWeb = /<!--\s*via:web/.test(issue.body || '');                      // written by the submit function (a visitor without GitHub) or made with the GitHub form
  const badges = [el('span', { class: 'badge ' + (p.kind === 'shelf' ? '' : 'post'), text: { post: '个人文章', record: '书籍', shelf: '网址' }[p.kind] }), el('span', { class: 'badge', text: viaWeb ? '网页提交' : 'GitHub 表单' })];
  for(const l of ['checked', 'invalid', 'duplicate', 'similar']) if(labels.includes(l)) badges.push(el('span', { class: 'badge ' + (l === 'similar' ? 'duplicate' : l), title: l === 'similar' ? '和目录里已有的很像（标题、作者或摘要）。如果确实不同，仍然可以通过。' : '', text: { checked: '检查通过', invalid: '检查未通过', duplicate: '重复', similar: '疑似重复' }[l] }));
  if(!['checked', 'invalid', 'duplicate', 'similar', 'approved'].some(l => labels.includes(l))) badges.push(el('span', { class: 'badge', title: '机器人还没有检查这个申请（Action 还在排队，或没有触发：看仓库的 Actions 页）', text: '尚未检查' }));
  if(labels.includes('approved')) badges.push(el('span', { class: 'badge', text: '已批准，处理中' }));
  const body = el('div');
  if(p.kind === 'shelf'){
    body.append(el('p', { class: 'url' }, p.url ? el('a', { href: p.url, target: '_blank', rel: 'nofollow noopener noreferrer', text: p.url }) : '（没有找到网址）'));
    if(p.tags) body.append(el('p', { class: 'muted', text: '标签：' + p.tags }));
  }else if(p.kind === 'record'){
    body.append(el('p', { class: 'muted', text: [p.author ? '作者：' + p.author : '作者：（未填）', p.tags ? '标签：' + p.tags : ''].filter(Boolean).join(' · ') }));
    body.append(el('p', { text: p.summary }));
    if(p.url) body.append(el('p', { class: 'url' }, el('a', { href: p.url, target: '_blank', rel: 'nofollow noopener noreferrer', text: p.url })));
  }else{
    body.append(el('p', { class: 'muted', text: [p.author ? '作者：' + p.author : '', p.tags ? '标签：' + p.tags : '', p.body.length + ' 个字符'].filter(Boolean).join(' · ') }));
    body.append(el('p', { text: p.body.replace(/\s+/g, ' ').slice(0, 220) + (p.body.length > 220 ? '…' : '') }));
  }
  const slot = el('div');
  const toggle = (label, make) => { let open = false; const b = el('button', { type: 'button', text: label }); b.addEventListener('click', async () => {
    open = !open; slot.textContent = ''; b.textContent = (open ? '收起' : '') + label; if(!open) return;
    try{ const node = await make(); if(node) slot.append(node); }catch(e){ slot.append(el('p', { class: 'msg bad', text: e.message })); } }); return b; };
  const reason = el('input', { type: 'text', class: 'reason', placeholder: '拒绝的原因（可不填，只记在这个申请里，不会告诉申请人）', hidden: true, maxlength: '300' });
  const confirmReject = el('button', { type: 'button', class: 'danger', hidden: true, text: '确认拒绝' });
  const cancel = el('button', { type: 'button', hidden: true, text: '取消' });
  const approveBtn = el('button', { type: 'button', class: 'ok', text: '通过' });
  const rejectBtn = el('button', { type: 'button', class: 'danger', text: '拒绝…' });
  const isDuplicate = labels.includes('duplicate');                           // the bot found it in the directory already: nothing to approve, only to close
  if(isDuplicate){ approveBtn.disabled = true; approveBtn.title = '目录里已经有这个了，不能再通过；用“拒绝”把它关掉'; }
  const lock = on => [approveBtn, rejectBtn, confirmReject].forEach(b => { b.disabled = on || (b === approveBtn && isDuplicate); });
  rejectBtn.addEventListener('click', () => { [reason, confirmReject, cancel].forEach(x => { x.hidden = false; }); rejectBtn.hidden = true; reason.focus(); });
  cancel.addEventListener('click', () => { [reason, confirmReject, cancel].forEach(x => { x.hidden = true; }); rejectBtn.hidden = false; });
  approveBtn.addEventListener('click', async () => {
    lock(true);
    try{
      const before = await botComment(issue.number);
      need(await gh('POST', `/repos/${state.repo}/issues/${issue.number}/labels`, { labels: ['approved'] }), '批准');
      status.textContent = '已批准，等待 GitHub Action 登记（通常 1 分钟内）…'; status.className = 'msg';
      await watch(issue.number, before && before.updated_at, status, () => lock(false));
    }catch(e){ status.textContent = e.message; status.className = 'msg bad'; lock(false); }
  });
  confirmReject.addEventListener('click', async () => {
    lock(true);
    try{
      if(reason.value.trim()) need(await gh('POST', `/repos/${state.repo}/issues/${issue.number}/comments`, { body: '已拒绝：' + reason.value.trim() }), '写入原因');
      need(await gh('POST', `/repos/${state.repo}/issues/${issue.number}/labels`, { labels: ['rejected'] }), '拒绝');
      status.textContent = '已拒绝，等待 GitHub Action 关闭这个申请…'; status.className = 'msg';
      await watch(issue.number, null, status);
    }catch(e){ status.textContent = e.message; status.className = 'msg bad'; lock(false); }
  });
  const actions = el('div', { class: 'actions' }, approveBtn, rejectBtn, reason, confirmReject, cancel,
    toggle('检查结果', async () => { const c = await botComment(issue.number); return c ? doc(c.body.replace(BOT, '')) : el('p', { class: 'muted', text: '机器人还没有回复（Action 可能还在运行，或没有触发：看仓库的 Actions 页）。' }); }),
    p.kind === 'post' ? toggle('全文预览', async () => doc(p.body)) : null,
    el('a', { href: issue.html_url, target: '_blank', rel: 'noopener noreferrer', text: 'GitHub ↗' }));
  const pick = el('input', { type: 'checkbox', class: 'pick', 'aria-label': '选择这个申请', 'data-n': String(issue.number) });
  if(!handable(labels)){ pick.disabled = true; pick.title = '这个申请现在不能通过（重复、检查未通过或正在处理）'; }
  pick.addEventListener('change', updateBatchBar);
  const card = el('article', { class: 'card' }, el('h3', {}, pick, el('span', { text: head })), el('p', { class: 'meta' }, ...badges, '#' + issue.number + ' · ' + ago(issue.created_at)), body, actions, slot, status);
  card.dataset.n = String(issue.number); card.dataset.passed = passed(labels) ? '1' : '';
  return card;
}
async function watch(n, botBefore, status, unlock){
  for(let i = 0; i < 45; i++){
    await new Promise(r => setTimeout(r, 4000));
    const issue = need(await gh('GET', `/repos/${state.repo}/issues/${n}`), '读取申请');
    const labels = labelsOf(issue);
    if(issue.state === 'closed'){
      status.textContent = labels.includes('listed') ? '✅ 已收录 / 已发布。' : labels.includes('rejected') ? '🚫 已拒绝并关闭。' : '已关闭。'; status.className = 'msg good';
      setTimeout(refresh, 800); return;
    }
    if(botBefore !== null){
      const c = await botComment(n);
      if(c && c.updated_at !== botBefore){ status.textContent = '没有通过：' + (c.body.split('\n').find(l => l.includes('暂时无法收录')) || '请点“检查结果”看原因。').replace(/[*\\]/g, ''); status.className = 'msg bad'; if(unlock) unlock(); return; }
    }
  }
  status.textContent = '还没有处理完。到仓库的 Actions 页看看运行情况，稍后点“刷新”。'; status.className = 'msg'; if(unlock) unlock();
}
async function renderPending(){
  const box = $('#pending'); box.textContent = '';
  const issues = await listIssues('open', ['shelf-submission', 'post-submission']);
  $('#nPending').textContent = issues.length;
  if(!issues.length){ box.append(el('p', { class: 'empty', text: '没有待审的申请。' })); return; }
  box.append(batchBar());
  issues.forEach(i => box.append(pendingCard(i)));
  updateBatchBar();
}

/* ---------- several at once ----------
   One run of the "Batch review" workflow decides them all (labels added to many issues at once would start as many runs, and GitHub keeps only the first and the last).
   "Handable": the maintainer can approve it (not a duplicate, not refused by the check, not on its way already). "Passed": the check found nothing to say against it. */
const BATCH_MAX = 60;
const handable = labels => !['duplicate', 'invalid', 'approved'].some(l => labels.includes(l));
const passed = labels => labels.includes('checked') && !['duplicate', 'invalid', 'similar', 'approved'].some(l => labels.includes(l));
const cardsOf = () => [...document.querySelectorAll('#pending article.card')];
const ticked = () => cardsOf().filter(c => c.querySelector('input.pick:checked')).map(c => +c.dataset.n);
const passedNumbers = () => cardsOf().filter(c => c.dataset.passed === '1' && !c.querySelector('input.pick:disabled')).map(c => +c.dataset.n);
let batchBusy = false;
function batchBar(){
  const mk = (id, cls, text, extra) => el('button', { type: 'button', id, class: cls || '', text, ...(extra || {}) });
  const reason = el('input', { type: 'text', id: 'batchReason', class: 'reason', placeholder: '拒绝的原因（可不填；会写进每个被拒绝的申请里）', hidden: true, maxlength: '300' });
  const bar = el('div', { class: 'batchbar', id: 'batchBar' },
    el('span', { class: 'muted', id: 'batchSum' }),
    mk('batchPassed', '', ''), mk('batchClear', '', '清除选择'),
    el('span', { class: 'grow' }),
    mk('batchApprove', 'ok', ''), mk('batchReject', 'danger', ''), reason, mk('batchRejectGo', 'danger', '确认拒绝', { hidden: true }), mk('batchRejectNo', '', '取消', { hidden: true }),
    mk('batchAll', 'primary', ''));
  const setReject = on => { ['batchReason', 'batchRejectGo', 'batchRejectNo'].forEach(id => { document.getElementById(id).hidden = !on; }); document.getElementById('batchReject').hidden = on; if(on) reason.focus(); };
  bar.addEventListener('click', e => {
    const id = e.target.id; if(!id || batchBusy) return;
    if(id === 'batchPassed'){ cardsOf().forEach(c => { const b = c.querySelector('input.pick'); if(b && !b.disabled) b.checked = c.dataset.passed === '1'; }); updateBatchBar(); }
    else if(id === 'batchClear'){ cardsOf().forEach(c => { c.querySelector('input.pick').checked = false; }); updateBatchBar(); }
    else if(id === 'batchApprove') runBatch('approve', ticked());
    else if(id === 'batchAll'){ const n = passedNumbers(); if(n.length && confirm('通过这 ' + Math.min(n.length, BATCH_MAX) + ' 个“检查通过”的申请，并把它们收录进目录？')) runBatch('approve', n); }
    else if(id === 'batchReject') setReject(true);
    else if(id === 'batchRejectNo') setReject(false);
    else if(id === 'batchRejectGo'){ const n = ticked(); if(n.length && confirm('拒绝这 ' + Math.min(n.length, BATCH_MAX) + ' 个申请？拒绝以后它们不会再被收录。')){ runBatch('reject', n, reason.value.trim()); setReject(false); } }
  });
  return bar;
}
function updateBatchBar(){
  const bar = document.getElementById('batchBar'); if(!bar) return;
  const sel = ticked().length, ok = passedNumbers().length;
  const set = (id, text, off) => { const b = document.getElementById(id); b.textContent = text; b.disabled = batchBusy || !!off; };
  document.getElementById('batchSum').textContent = '已选 ' + sel + ' 个';
  set('batchPassed', '选中检查通过的（' + ok + '）', !ok);
  set('batchClear', '清除选择', !sel);
  set('batchApprove', '通过所选（' + sel + '）', !sel);
  set('batchReject', '拒绝所选…', !sel);
  set('batchRejectGo', '确认拒绝', !sel);
  set('batchAll', '一键通过全部检查通过的（' + ok + '）', !ok);
}
async function runBatch(mode, numbers, reason){
  numbers = numbers.slice(0, BATCH_MAX);
  if(!numbers.length || batchBusy) return;
  batchBusy = true; updateBatchBar(); cardsOf().forEach(c => { c.querySelector('input.pick').disabled = true; });
  const verb = mode === 'approve' ? '通过' : '拒绝', before = new Map();
  try{
    (await listIssues('open', ['shelf-submission', 'post-submission'])).forEach(i => before.set(i.number, i.updated_at));
    await dispatch('batch.yml', { mode, numbers: numbers.join(','), ...(reason ? { reason } : {}) });
    say('已提交：' + verb + ' ' + numbers.length + ' 个申请，GitHub Action 在一次运行里处理（通常 1–3 分钟）…', '');
    let done = 0;
    for(let i = 0; i < 45; i++){
      await new Promise(r => setTimeout(r, 6000));
      const open = await listIssues('open', ['shelf-submission', 'post-submission']), now = new Map(open.map(x => [x.number, x.updated_at]));
      done = numbers.filter(n => !now.has(n) || now.get(n) !== before.get(n)).length;           // closed, or answered again (the check refused it)
      say('已处理 ' + done + ' / ' + numbers.length + ' …', '');
      if(done >= numbers.length) break;
    }
    const open = await listIssues('open', ['shelf-submission', 'post-submission']), left = numbers.filter(n => open.some(x => x.number === n)).length;
    if(done < numbers.length) say('还没有处理完（' + done + ' / ' + numbers.length + '）。到仓库的 Actions 页看看运行情况，稍后点“刷新”。', 'bad');
    else say(left ? ('完成：' + (numbers.length - left) + ' 个已' + (mode === 'approve' ? '收录' : '拒绝') + '；' + left + ' 个没有成功，还在待审里，点它的“检查结果”看原因。') : ('✅ 完成：' + numbers.length + ' 个都已' + (mode === 'approve' ? '收录' : '拒绝') + '。'), left ? 'bad' : 'good');
  }catch(e){ say(e.message, 'bad'); }
  batchBusy = false;
  setTimeout(refresh, 600);
}

/* ---------- what is listed ---------- */
async function dispatch(workflow, inputs){
  need(await gh('POST', `/repos/${state.repo}/actions/workflows/${workflow}/dispatches`, inputs ? { ref: state.branch, inputs } : { ref: state.branch }), '触发 ' + workflow);
}
const KIND = { feed: '订阅源', repo: 'GitHub 仓库', link: '单条', post: '读者投稿' };
function renderListed(){
  const box = $('#listed'); box.textContent = '';
  const d = state.directory;
  if(!d){ box.append(el('p', { class: 'empty', text: '读不到 directory.json（网站刚发布时要等一两分钟）。' })); return; }
  $('#nListed').textContent = (d.shelves || []).length;
  box.append(el('p', { class: 'muted', text: '数据日期 ' + (d.generated || '?') + ' · 来自' + (state.directoryFrom === 'repo' ? '仓库里最新提交的文件' : '已发布的网页（可能比仓库晚一两分钟）') }));
  const askRemove = async (target, label) => {
    if(!confirm('从目录里删除「' + label + '」？\n删除由 GitHub Action 执行，约 1 分钟生效。')) return;
    try{ await dispatch('remove.yml', { target }); say('已提交删除「' + label + '」，约 1 分钟后点“刷新”。', 'good'); }catch(e){ say(e.message, 'bad'); }
  };
  for(const s of d.shelves || []){
    box.append(el('article', { class: 'card' }, el('h3', { text: s.name }), el('p', { class: 'meta' }, el('span', { class: 'badge', text: KIND[s.kind] || s.kind }), s.n + ' 条', s.updated ? '最近：' + s.updated : '', s.lang || ''),
      s.home ? el('p', { class: 'url' }, el('a', { href: s.home, target: '_blank', rel: 'nofollow noopener noreferrer', text: s.home })) : null,
      el('div', { class: 'actions' }, s.kind === 'post' ? el('span', { class: 'muted', text: '下面单独删除每一篇。' }) : el('button', { type: 'button', class: 'danger', text: '删除这个书架', onclick: () => askRemove(s.id, s.name) }))));
    if(s.kind === 'post'){
      const rows = el('div', { class: 'card' });
      for(const it of (d.items || []).filter(i => i.shelf === s.id)){
        const seg = (() => { try{ return new URL(it.url).pathname.split('/').filter(Boolean); }catch(e){ return []; } })(); const slug = seg[seg.indexOf('p') + 1];
        rows.append(el('div', { class: 'row' }, el('span', { class: 'name' }, el('a', { href: it.url, target: '_blank', rel: 'noopener noreferrer', text: it.title }), el('span', { class: 'sub', text: it.date + ' · ' + (slug || '') })),
          slug ? el('button', { type: 'button', class: 'danger', text: '删除', onclick: () => askRemove('post:' + slug, it.title) }) : null));
      }
      box.append(rows);
    }
  }
  if(!(d.shelves || []).length) box.append(el('p', { class: 'empty', text: '目录里还没有内容。' }));
}
/* ---------- sources: what is said about each (registry/shelves.json, read with the same rights as the rest; saved by the "Set source" workflow, which also rebuilds docs/) ---------- */
const STATUS_NAME = { fixed: '固定', dynamic: '动态', trial: '试读', paused: '暂停' };
const STATUS_HELP = { fixed: '长期保留', dynamic: '按热点进出', trial: '新来源，小范围观察', paused: '保留登记和历史，不更新、不展示' };
const RIGHTS_NAME = { 'official-link': '官方链接', licensed: '已授权', unknown: '未确认' };
const RIGHTS_HELP = { 'official-link': '官方订阅源或接口，只用标题、摘要和链接', licensed: '作者授权', unknown: '还没有确认' };
async function loadRegistry(){
  state.registry = null;
  try{
    if(state.mode === 'password' && state.session && state.repo){
      const r = await gh('GET', `/repos/${state.repo}/contents/registry/shelves.json`);
      if(r.ok && r.data && Array.isArray(r.data.shelves)) state.registry = r.data.shelves;
    }else if(state.token && state.repo){
      const r = await fetch(API + `/repos/${state.repo}/contents/registry/shelves.json`, { cache: 'no-store', headers: { Authorization: 'Bearer ' + state.token, Accept: 'application/vnd.github.raw+json', 'X-GitHub-Api-Version': '2022-11-28' } });
      if(r.ok){ const d = await r.json(); if(d && Array.isArray(d.shelves)) state.registry = d.shelves; }
    }
  }catch(e){}
}
const PLATFORM_OK = /^([a-z0-9][a-z0-9-]{0,29})?$/;
function sourceCard(e, topicList){
  const status = STATUS_NAME[e.status] ? e.status : 'fixed', rights = RIGHTS_NAME[e.rights] ? e.rights : 'unknown', mine = Array.isArray(e.topics) ? e.topics.filter(x => typeof x === 'string') : [];
  const select = (names, help, value, label) => el('select', { 'aria-label': label }, Object.keys(names).map(k => el('option', { value: k, title: help[k], selected: k === value, text: names[k] + '（' + help[k] + '）' })));
  const statusSel = select(STATUS_NAME, STATUS_HELP, status, '状态'), rightsSel = select(RIGHTS_NAME, RIGHTS_HELP, rights, '版权');
  const known = new Map(topicList); mine.forEach(id => { if(!known.has(id)) known.set(id, id); });
  const boxes = [...known].map(([id, name]) => el('label', { class: 'check' }, el('input', { type: 'checkbox', value: id, checked: mine.includes(id) }), name));
  const platform = el('input', { type: 'text', maxlength: '30', placeholder: 'zhihu / youtube / wechat …（不写则按网址）', value: typeof e.platform === 'string' ? e.platform : '', 'aria-label': '平台' });
  const note = el('input', { type: 'text', maxlength: '200', placeholder: '给自己看的备注（不会公开到目录里）', value: typeof e.note === 'string' ? e.note : '', 'aria-label': '备注' });
  const topicsSum = () => { const n = boxes.filter(b => b.querySelector('input').checked).length; return '领域（' + (n ? '已选 ' + n + ' 个' : '不写：按标签判断') + '）'; };
  const summary = el('summary', { text: topicsSum() });
  boxes.forEach(b => b.querySelector('input').addEventListener('change', () => { summary.textContent = topicsSum(); }));
  const msg = el('p', { class: 'msg', role: 'status' }), tell = (text, kind) => { msg.textContent = text; msg.className = 'msg' + (kind ? ' ' + kind : ''); };
  const send = async (over, done) => {
    const v = { id: e.id, status: statusSel.value, rights: rightsSel.value, topics: boxes.map(b => b.querySelector('input')).filter(i => i.checked).map(i => i.value).join(','), platform: platform.value.trim().toLowerCase(), note: note.value.replace(/\s+/g, ' ').trim(), ...over };
    if(!PLATFORM_OK.test(v.platform)){ tell('平台要写成小写字母、数字和短横线（如 zhihu），最多 30 个字符。', 'bad'); return; }
    tell('提交中…');
    try{ await dispatch('set.yml', v); tell(done + ' 约 1 分钟后点“刷新”。', 'good'); }catch(err){ tell(err.message, 'bad'); }
  };
  const paused = status === 'paused';
  const save = el('button', { type: 'button', class: 'ok', text: '保存', onclick: () => send({}, '已提交「' + e.name + '」的来源设置。') });
  const toggle = el('button', { type: 'button', class: paused ? '' : 'danger', title: paused ? '恢复为“固定”，并重新读取' : STATUS_HELP.paused, text: paused ? '恢复' : '暂停…',
    onclick: () => {
      if(paused) return send({ status: 'fixed' }, '已提交：恢复「' + e.name + '」。');
      if(confirm('暂停「' + e.name + '」？\n暂停后不再更新，目录里也不再显示它，但登记和已读到的内容都保留，随时可以恢复。')) send({ status: 'paused' }, '已提交：暂停「' + e.name + '」。');
    } });
  return el('article', { class: 'card' + (paused ? ' paused' : '') }, el('h3', { text: e.name || e.id }),
    el('p', { class: 'meta' }, el('span', { class: 'badge ' + status, text: STATUS_NAME[status] }), el('span', { class: 'badge', text: KIND[e.kind] || e.kind }), e.id),
    e.url ? el('p', { class: 'url' }, el('a', { href: e.url, target: '_blank', rel: 'nofollow noopener noreferrer', text: e.url })) : null,
    el('div', { class: 'src' }, el('div', {}, el('label', { text: '状态' }), statusSel), el('div', {}, el('label', { text: '版权' }), rightsSel),
      el('div', {}, el('label', { text: '平台' }), platform), el('details', { class: 'full topics' }, summary, el('div', { class: 'topics-box' }, boxes)),
      el('div', { class: 'full' }, el('label', { text: '备注' }), note)),
    el('div', { class: 'actions' }, save, toggle), msg);
}
function renderSources(){
  const box = $('#sources'); box.textContent = '';
  const list = state.registry;
  $('#nSources').textContent = list ? list.length : 0;
  if(!list){ box.append(el('p', { class: 'empty', text: '读不到 registry/shelves.json（令牌需要“Contents: Read-only”）。' })); return; }
  box.append(el('p', { class: 'muted', text: '每个来源的状态、版权方式、领域和平台，在这里改。暂停的来源保留登记和历史，不再更新、不在目录里显示；恢复后重新读取。备注只给你自己看，不会写进目录数据（但它在仓库的 registry/shelves.json 里，仓库是公开的就谁都看得到）。' }));
  const topicList = ((state.directory && state.directory.topics) || []).filter(t => t && typeof t.id === 'string').map(t => [t.id, t.zh || t.id]);
  list.forEach(e => { if(e && typeof e.id === 'string') box.append(sourceCard(e, topicList)); });
  if(!list.length) box.append(el('p', { class: 'empty', text: '还没有登记任何来源。' }));
}

/* ---------- operations: public placement rather than source ingestion ---------- */
function num(v){ return Number(v || 0).toLocaleString('zh-CN'); }
function saveButton(label, action, get, after){
  const msg = el('p', { class: 'msg', role: 'status' });
  const b = el('button', { type: 'button', class: 'ok', text: label, onclick: async () => {
    b.disabled = true; msg.textContent = '保存中…'; msg.className = 'msg';
    try{ await ops(action, get()); msg.textContent = '已保存；公共空间刷新后会读取新设置。'; msg.className = 'msg good'; if(after) await after(); }
    catch(e){ msg.textContent = e.message; msg.className = 'msg bad'; }
    b.disabled = false;
  }});
  return { b, msg };
}
const MODULE_DEFAULTS={
  featured:{ranking:'balanced',fresh_hours:72,max_per_program:1},
  selected:{ranking:'balanced',fresh_hours:168,max_per_program:2},
  personalized:{ranking:'balanced',fresh_hours:0,max_per_program:2},
  trending:{ranking:'hot',fresh_hours:24,max_per_program:1},
  latest:{ranking:'fresh',fresh_hours:72,max_per_program:3}
};
function programOpsCard(p){
  const pub = el('input', { type: 'checkbox', checked: !!p.public_enabled }), recommend = el('input', { type: 'checkbox', checked: !!p.recommend_enabled });
  const weight = el('input', { type: 'number', min: '-30', max: '30', value: String(p.editorial_weight || 0), 'aria-label': '权重' });
  const save = saveButton('保存展示设置', 'save_program', () => ({ id:p.id, public_enabled:pub.checked, recommend_enabled:recommend.checked, editorial_weight:weight.value }));
  const lifecycle=({fixed:'固定',dynamic:'动态',trial:'试运行',paused:'暂停'})[p.status] || p.status;
  return el('details', { class: 'card ops-card' }, el('summary', { class:'ops-card-summary' }, el('strong', { text:p.name }), el('span', { class:'badge ' + p.status, text:lifecycle }), el('span', { class:'muted', text:'打开 '+num(p.opens)+' · 收藏 '+num(p.saves) })),
    el('div', { class:'ops-card-body' }, el('p', { class: 'meta' }, p.platform || '未标平台', p.directory_id || ''),
      el('p', { class:'muted', text:'近 30 天：打开 ' + num(p.opens) + ' · 收藏 ' + num(p.saves) + ' · 点赞 ' + num(p.likes) + (p.consecutive_failures ? ' · 连续失败 ' + p.consecutive_failures : '') }),
      el('p', { class:'muted', text:'采集状态在“来源”页维护；这里仅控制公共空间曝光与推荐。' }),
      el('div', { class:'ops-grid' }, el('label', { class:'check', text:'进入公共空间' }, pub), el('label', { class:'check', text:'允许推荐' }, recommend), el('div', {}, el('label', { text:'人工权重（-30 ~ 30）' }), weight)),
      el('div', { class:'actions' }, save.b), save.msg));
}
function moduleOpsCard(m){
  const on = el('input', { type:'checkbox', checked:!!m.enabled });
  const device = el('select', {}, [['all','全部设备'],['desktop','仅桌面'],['mobile','仅手机']].map(x => el('option',{value:x[0],selected:x[0]===m.device,text:x[1]})));
  const limit = el('input', { type:'number', min:'1', max:'24', value:String(m.limit_count || 6) });
  const c=m.config&&typeof m.config==='object'?m.config:{}, defaults=MODULE_DEFAULTS[m.slug]||MODULE_DEFAULTS.selected;
  const ranking=el('select',{},[['balanced','平衡推荐'],['hot','热度优先'],['fresh','最新优先'],['saved','收藏优先']].map(x=>el('option',{value:x[0],selected:(c.ranking||defaults.ranking)===x[0],text:x[1]})));
  const fresh=el('select',{},[['0','不限时间'],['24','仅近 24 小时'],['72','仅近 3 天'],['168','仅近 7 天']].map(x=>el('option',{value:x[0],selected:String(c.fresh_hours===undefined?defaults.fresh_hours:c.fresh_hours)===x[0],text:x[1]})));
  const perSource=el('select',{},[['1','每个节目最多 1 篇'],['2','每个节目最多 2 篇'],['3','每个节目最多 3 篇']].map(x=>el('option',{value:x[0],selected:String(c.max_per_program===undefined?defaults.max_per_program:c.max_per_program)===x[0],text:x[1]})));
  const save = saveButton('保存栏目', 'save_module', () => ({slug:m.slug, enabled:on.checked, device:device.value, limit_count:limit.value, config:{ranking:ranking.value,fresh_hours:fresh.value,max_per_program:perSource.value}}));
  return el('article',{class:'card ops-card'},el('h3',{text:m.name || m.slug}),el('p',{class:'muted',text:'控制公共空间里这个区块的显示与推荐方式；设置会在读者刷新公共空间时生效。'}),
    el('div',{class:'ops-grid'},el('label',{class:'check',text:'启用'},on),el('div',{},el('label',{text:'设备'}),device),el('div',{},el('label',{text:'条数'}),limit),el('div',{},el('label',{text:'排序'}),ranking),el('div',{},el('label',{text:'内容时间范围'}),fresh),el('div',{},el('label',{text:'单节目上限'}),perSource)),el('div',{class:'actions'},save.b),save.msg);
}
function adOpsCard(a){
  const on=el('input',{type:'checkbox',checked:!!a.enabled});
  const device=el('select',{},[['all','全部设备'],['desktop','仅桌面'],['mobile','仅手机']].map(x=>el('option',{value:x[0],selected:x[0]===a.device,text:x[1]})));
  const placement=el('select',{},[['sidebar','右侧栏'],['infeed','信息流'],['reader-bottom','阅读页底部']].map(x=>el('option',{value:x[0],selected:x[0]===a.placement,text:x[1]})));
  const after=el('input',{type:'number',min:'0',max:'30',value:String(a.after_item || 0)});
  const save=saveButton('保存广告位','save_ad',()=>({slug:a.slug,enabled:on.checked,device:device.value,placement:placement.value,after_item:after.value,config:a.config||{}}));
  return el('article',{class:'card ops-card'},el('h3',{text:a.name || a.slug}),el('p',{class:'muted',text:'广告只在普通内容流中显示，并需在前端保留“广告”标识；不会进入「今日值得看」。'}),el('div',{class:'ops-grid'},el('label',{class:'check',text:'启用'},on),el('div',{},el('label',{text:'设备'}),device),el('div',{},el('label',{text:'位置'}),placement),el('div',{},el('label',{text:'第 N 条后（右栏为 0）'}),after)),el('div',{class:'actions'},save.b),save.msg);
}
function coverOpsCard(c){
  const name=el('input',{type:'text',maxlength:'80',value:c.name || '',placeholder:'封面名称'});
  const active=el('input',{type:'checkbox',checked:!!c.is_active});
  const config=el('textarea',{rows:'5',spellcheck:'false',text:JSON.stringify(c.render_config || {},null,2),'aria-label':'动态封面 JSON'});
  const save=saveButton('保存 Cover','save_cover',()=>{let v={};try{v=config.value.trim()?JSON.parse(config.value):{};}catch(e){throw new Error('Cover 配置必须是 JSON');}return {slug:c.slug,name:name.value,render_engine:c.render_engine || 'canvas',render_config:v,is_active:active.checked};});
  return el('article',{class:'card ops-card'},el('h3',{text:c.slug}),el('p',{class:'muted',text:'Canvas 粒子/科技风封面由公共空间前端按此配置渲染。'}),el('div',{class:'ops-grid'},el('div',{class:'full'},el('label',{text:'名称'}),name),el('label',{class:'check',text:'可选用'},active)),el('details',{class:'ops-advanced'},el('summary',{text:'渲染配置（JSON）'}),config),el('div',{class:'actions'},save.b),save.msg);
}
function addParticleCover(){
  return {slug:'particle-network',name:'粒子网络',render_engine:'canvas',is_active:true,render_config:{palette:['#0B4F3B','#1F7A6D','#6FB98F','#E8C778'],particle_count_desktop:42,particle_count_mobile:18,speed:0.22,connect_distance:110,text_safe_area:'bottom',reduced_motion:'static-svg'}};
}
function contentOpsRow(c, covers){
  const weight=el('input',{type:'number',min:'-30',max:'30',value:String(c.editorial_weight||0)}), hidden=el('input',{type:'checkbox',checked:!!c.hidden});
  const mode=el('select',{},[['auto','自动'],['source','原图'],['preset','动态 Cover'],['none','无封面']].map(x=>el('option',{value:x[0],selected:x[0]===c.cover_mode,text:x[1]})));
  const preset=el('select',{},[el('option',{value:'',text:'选择动态 Cover'}),...covers.filter(x=>x.is_active).map(x=>el('option',{value:x.id,selected:x.id===c.cover_preset_id,text:x.name || x.slug}))]);
  const placementNames={featured:'精选',selected:'首页推荐',personalized:'为你精选',trending:'正在发生',latest:'最新更新'};
  const chosen=new Set(Array.isArray(c.placement_slugs)?c.placement_slugs:[]);
  const placements=Object.entries(placementNames).map(([slug,name])=>el('label',{class:'check'},el('input',{type:'checkbox',value:slug,checked:chosen.has(slug)}),name));
  const reason=el('input',{type:'text',maxlength:'160',value:c.editorial_note || '',placeholder:'例如：本周热读 / 正在发生'});
  const save=saveButton('保存','save_content',()=>({id:c.id,editorial_weight:weight.value,hidden:hidden.checked,cover_mode:mode.value,cover_preset_id:preset.value,editorial_note:reason.value,placement_modules:placements.filter(x=>x.querySelector('input').checked).map(x=>x.querySelector('input').value)}));
  return el('details',{class:'card ops-card compact'},el('summary',{class:'ops-card-summary'},el('strong',{text:c.title}),el('span',{class:'muted',text:c.program_name+' · 打开 '+num(c.opens)+' · 收藏 '+num(c.saves)}),c.hidden ? el('span',{class:'badge paused',text:'隐藏'}) : null),el('div',{class:'ops-card-body'},el('p',{class:'meta'},c.language || '', c.kind || ''),el('div',{class:'ops-grid'},el('div',{},el('label',{text:'权重'}),weight),el('label',{class:'check',text:'隐藏'},hidden),el('div',{},el('label',{text:'封面'}),mode),el('div',{},el('label',{text:'动态 Cover'}),preset),el('div',{class:'full'},el('label',{text:'投放到栏目'}),el('div',{class:'topics-box'},placements)),el('div',{class:'full'},el('label',{text:'推荐理由'}),reason)),el('div',{class:'actions'},save.b),save.msg));
}
const SUB_CATEGORY={hot:'热榜与趋势',tech:'AI 与科技',study:'学习与研究',business:'财经与商业',world:'全球正在发生',audio:'播客与创作者',books:'读书与深度',other:'其他'};
const SUB_TYPE={native:'原生 RSS / Atom',rsshub:'RSSHub 转换',rssapp:'RSS.app 转换'};
const SUB_STATUS={trial:'试运行',active:'稳定使用',paused:'暂停'};
function subscriptionEditor(item, topicList, options){
  const x=item||{}, input=(label,value,placeholder,max)=>el('div',{},el('label',{text:label}),el('input',{type:'text',maxlength:String(max||500),value:value||'',placeholder:placeholder||''}));
  const name=input('名称',x.name,'例如：GitHub Blog',120), nameEn=input('英文名',x.name_en,'可选',120), home=input('来源主页',x.homepage_url,'https://…',1500), feed=input('订阅地址',x.feed_url,'https://…/feed.xml',1500), platform=input('平台',x.platform,'github / zhihu / youtube …',30), description=input('中文说明',x.description,'给读者看的简短说明',500), descriptionEn=input('英文说明',x.description_en,'Optional',500);
  const type=el('select',{},Object.entries(SUB_TYPE).map(([id,label])=>el('option',{value:id,selected:(x.source_type||'native')===id,text:label})));
  const category=el('select',{},Object.entries(SUB_CATEGORY).map(([id,label])=>el('option',{value:id,selected:(x.category||'other')===id,text:label})));
  const language=el('select',{},[['zh','中文'],['en','英文'],['multi','多语言']].map(([id,label])=>el('option',{value:id,selected:(x.language||'multi')===id,text:label})));
  const status=el('select',{},Object.entries(SUB_STATUS).map(([id,label])=>el('option',{value:id,selected:(x.status||'trial')===id,text:label})));
  const recommend=el('input',{type:'checkbox',checked:!!x.recommend_enabled}), publicSpace=el('input',{type:'checkbox',checked:!!x.public_enabled});
  const selected=new Set(Array.isArray(x.topics)?x.topics:[]), topics=topicList.map(([id,label])=>el('label',{class:'check'},el('input',{type:'checkbox',value:id,checked:selected.has(id)}),label));
  const msg=el('p',{class:'msg',role:'status'}), payload=()=>({id:x.id||'',name:name.querySelector('input').value,name_en:nameEn.querySelector('input').value,homepage_url:home.querySelector('input').value,feed_url:feed.querySelector('input').value,platform:platform.querySelector('input').value,description:description.querySelector('input').value,description_en:descriptionEn.querySelector('input').value,source_type:type.value,category:category.value,language:language.value,status:status.value,topics:topics.filter(t=>t.querySelector('input').checked).map(t=>t.querySelector('input').value),recommend_enabled:recommend.checked,public_enabled:publicSpace.checked});
  const save=el('button',{type:'button',class:'ok',text:x.id?'保存订阅设置':'收集到订阅列表',onclick:async()=>{save.disabled=true;msg.textContent='保存中…';msg.className='msg';try{await ops('save_subscription',payload());msg.textContent='已保存。推荐订阅会通过运营配置同步；公共空间请再点“推送”。';msg.className='msg good';if(options&&options.after) await options.after();}catch(e){msg.textContent=e.message;msg.className='msg bad';}save.disabled=false;}});
  const discover=el('button',{type:'button',text:'检测 / 自动填入',onclick:async()=>{const url=feed.querySelector('input').value.trim()||home.querySelector('input').value.trim();if(!url){msg.textContent='先填写网站或订阅地址。';msg.className='msg bad';return;}discover.disabled=true;msg.textContent='正在读取公开订阅…';msg.className='msg';try{const d=await ops('discover_subscription',{url});feed.querySelector('input').value=d.feed_url||url;home.querySelector('input').value=d.homepage_url||home.querySelector('input').value;name.querySelector('input').value=d.name||name.querySelector('input').value;description.querySelector('input').value=d.description||description.querySelector('input').value;if(['zh','en','multi'].includes(d.language))language.value=d.language;msg.textContent='找到 '+(d.found||0)+' 条内容'+(d.warnings&&d.warnings.length?'；'+d.warnings.join('；'):'' );msg.className='msg good';}catch(e){msg.textContent=e.message;msg.className='msg bad';}discover.disabled=false;}});
  const publish=el('button',{type:'button',class:'primary',text:'推送到公共空间',onclick:async()=>{if(!publicSpace.checked){msg.textContent='先勾选“推送到公共空间”，再保存。';msg.className='msg bad';return;}const p=payload();if(!p.feed_url){msg.textContent='需要有效的订阅地址。';msg.className='msg bad';return;}publish.disabled=true;msg.textContent='正在提交目录收录工作流…';msg.className='msg';try{await dispatch('collect.yml',{url:p.feed_url,status:p.status==='active'?'fixed':p.status,rights:p.source_type==='native'?'official-link':'unknown',topics:p.topics.join(','),platform:p.platform});msg.textContent='已提交。GitHub Action 会读取订阅并重建公共目录，约 1–3 分钟后在“来源”页和公共空间出现。';msg.className='msg good';}catch(e){msg.textContent=e.message;msg.className='msg bad';}publish.disabled=false;}});
  const fields=el('div',{class:'ops-grid subscription-fields'},el('div',{class:'full'},name),el('div',{class:'full'},nameEn),el('div',{class:'full'},home),el('div',{class:'full'},feed),el('div',{},el('label',{text:'接入方式'}),type),el('div',{},el('label',{text:'分类'}),category),el('div',{},el('label',{text:'语言'}),language),el('div',{},el('label',{text:'状态'}),status),el('div',{},platform),el('label',{class:'check',text:'推送到推荐订阅'},recommend),el('label',{class:'check',text:'推送到公共空间'},publicSpace),el('div',{class:'full'},description),el('div',{class:'full'},descriptionEn),el('div',{class:'full'},el('label',{text:'领域标签'}),el('div',{class:'topics-box'},topics)));
  return el('div',{class:'subscription-editor'},fields,el('div',{class:'actions'},discover,save,publish),msg);
}
function subscriptionsOpsPanel(panel, subscriptions){
  const topicList=((state.directory&&state.directory.topics)||[]).filter(t=>t&&typeof t.id==='string').map(t=>[t.id,t.zh||t.id]);
  panel.append(el('h2',{text:'订阅目录与收集'}),el('p',{class:'muted',text:'这是总订阅池：一键导入主站的推荐订阅、热榜和已收录公共节目，再统一决定是否推送到推荐订阅或公共空间。之后也可手动收集原生 RSS，或粘贴你在 RSSHub / RSS.app 生成的公开 feed。转换服务只处理你提供的公开 feed，不绕过登录、付费或平台限制。'}));
  const importMsg=el('p',{class:'msg',role:'status'});
  const importPool=el('button',{type:'button',class:'primary',text:'同步推荐订阅与热榜到总池',onclick:async()=>{importPool.disabled=true;importPool.textContent='同步中…';importMsg.className='msg';importMsg.textContent='正在读取主站推荐库、热榜和公共节目…';try{const r=await ops('import_subscription_pool');importMsg.textContent='已同步推荐订阅 '+(r.recommended||0)+' 个、目录节目 '+(r.registry||0)+' 个、已同步公共节目 '+(r.programs||0)+' 个'+(r.skipped?'；跳过 '+r.skipped+' 个无有效 RSS 的条目':'')+'。';importMsg.className='msg good';await renderOps();}catch(e){importMsg.textContent=e.message;importMsg.className='msg bad';importPool.disabled=false;importPool.textContent='同步推荐订阅与热榜到总池';}}});
  panel.append(el('div',{class:'actions'},importPool),importMsg);
  const collect=el('details',{class:'card ops-card',open:true},el('summary',{class:'ops-card-summary'},el('strong',{text:'收集新的订阅'}),el('span',{class:'muted',text:'网站 / RSS / RSSHub / RSS.app'})),el('div',{class:'ops-card-body'},subscriptionEditor(null,topicList,{after:renderOps})));
  panel.append(collect);
  const query=el('input',{type:'search',placeholder:'搜索名称、平台、订阅地址或领域'}), category=el('select',{},[['all','全部分类'],...Object.entries(SUB_CATEGORY)].map(([id,label])=>el('option',{value:id,text:label}))), target=el('select',{},[['all','全部去向'],['recommend','已推送推荐订阅'],['public','已标记公共空间'],['paused','已暂停']].map(([id,label])=>el('option',{value:id,text:label})));
  const count=el('p',{class:'muted'}), list=el('div',{class:'ops-list'});
  const draw=()=>{
    const q=query.value.trim().toLowerCase(), rows=subscriptions.filter(s=>{
      const text=[s.name,s.name_en,s.platform,s.feed_url,s.homepage_url,(s.topics||[]).join(' ')].join(' ').toLowerCase();
      if(q&&!text.includes(q))return false;
      if(category.value!=='all'&&s.category!==category.value)return false;
      if(target.value==='recommend'&&!s.recommend_enabled)return false;
      if(target.value==='public'&&!s.public_enabled)return false;
      if(target.value==='paused'&&s.status!=='paused')return false;
      return true;
    });
    count.textContent='显示 '+rows.length+' / '+subscriptions.length+' 个已收集订阅。'; list.textContent='';
    rows.forEach(s=>{
      const card=el('details',{class:'card ops-card'},
        el('summary',{class:'ops-card-summary'},el('strong',{text:s.name}),el('span',{class:'badge '+(s.status||'trial'),text:SUB_STATUS[s.status]||s.status}),s.recommend_enabled?el('span',{class:'badge',text:'推荐订阅'}):null,s.public_enabled?el('span',{class:'badge',text:'公共空间'}):null,el('span',{class:'muted',text:SUB_CATEGORY[s.category]||'其他'})),
        el('div',{class:'ops-card-body'},subscriptionEditor(s,topicList,{after:renderOps})));
      list.append(card);
    });
    if(!rows.length)list.append(el('p',{class:'empty',text:'还没有符合条件的订阅。'}));
  };
  query.addEventListener('input',draw);category.addEventListener('change',draw);target.addEventListener('change',draw);panel.append(el('div',{class:'ops-filters'},query,category,target),count,list);draw();
}
let opsRenderVersion=0;
async function renderOps(){
  const renderVersion=++opsRenderVersion;
  const box=$('#ops'); box.textContent='';
  if(state.mode !== 'password') { box.append(el('p',{class:'empty',text:'运营页需要用管理密码登录。这样数据库凭据不会被放入浏览器；来源、审核仍可使用 GitHub 令牌模式。'})); return; }
  box.append(el('p',{class:'muted',text:'来源页管理采集与基础分类；这里管理公共空间的展示结果。同步只导入目录内容，不会把人工的推荐、隐藏、权重和广告设置覆盖掉。分享模板暂未接入。'}));
  let schema, data;
  try{ schema=await ops('schema_status'); }
  catch(e){ if(renderVersion===opsRenderVersion) box.append(el('p',{class:'msg bad',text:e.message})); return; }
  if(renderVersion!==opsRenderVersion) return;
  if(!schema.ready){
    const missing=Object.entries(schema.missing||{}).map(([table, columns])=>table + ': ' + columns.join(', '));
    box.append(el('section',{class:'card'},el('h2',{text:'运营数据库尚未完成迁移'}),el('p',{class:'msg bad',text:'当前函数连接的数据库缺少以下字段。请先执行 004_ops_complete_repair.sql，再执行 005_ops_subscription_catalog.sql；执行成功后刷新本页。'}),el('pre',{class:'doc',text:missing.join('\n')})));
    return;
  }
  try{ data=await Promise.all([ops('dashboard'),ops('programs'),ops('subscriptions'),ops('modules'),ops('covers'),ops('ads'),ops('contents')]); }
  catch(e){ if(renderVersion===opsRenderVersion) box.append(el('p',{class:'msg bad',text:e.message})); return; }
  if(renderVersion!==opsRenderVersion) return;
  const [dash, programs, subscriptions, modules, covers, ads, contents]=data;
  const sync=el('button',{type:'button',class:'primary',text:'同步目录到运营库',onclick:async()=>{sync.disabled=true; sync.textContent='同步中…'; try{const r=await ops('sync_directory'); sync.textContent='已同步 '+r.programs+' 个节目 / '+r.contents+' 篇内容'; await renderOps();}catch(e){sync.textContent=e.message;sync.className='danger';} sync.disabled=false;}});
  const metric=dash.metrics||{};
  const tabDefs=[['overview','概览'],['programs','节目'],['subscriptions','订阅目录'],['content','内容'],['modules','展示模块'],['assets','封面与广告']];
  const tabs=el('nav',{class:'tabs ops-tabs',role:'tablist','aria-label':'运营功能'});
  const panels={}; let active='overview';
  const activate=id=>{
    active=id;
    tabDefs.forEach(([key])=>{ panels[key].hidden=key!==id; tabs.querySelector('[data-ops-tab="'+key+'"]').setAttribute('aria-selected',String(key===id)); });
  };
  tabDefs.forEach(([id,label])=>{
    const button=el('button',{type:'button',role:'tab','data-ops-tab':id,'aria-selected':id===active,text:label,onclick:()=>activate(id)});
    tabs.append(button); panels[id]=el('section',{class:'ops-panel',role:'tabpanel',hidden:id!==active});
  });

  const overview=panels.overview;
  overview.append(el('section',{class:'ops-summary'},el('div',{class:'metric'},el('b',{text:String(dash.pending||0)}),el('span',{text:'待处理内容'})),el('div',{class:'metric'},el('b',{text:num(metric.opens)}),el('span',{text:'近 7 天打开'})),el('div',{class:'metric'},el('b',{text:num(metric.saves)}),el('span',{text:'近 7 天收藏'})),el('div',{class:'metric'},el('b',{text:String(metric.save_rate||0)+'%'}),el('span',{text:'收藏转化'})),el('div',{class:'metric'},el('b',{text:String(metric.like_rate||0)+'%'}),el('span',{text:'点赞转化'}))),el('div',{class:'actions'},sync));
  if((dash.alerts||[]).length) overview.append(el('section',{class:'card'},el('h2',{text:'需要留意'}),...(dash.alerts||[]).map(x=>el('p',{class:'muted',text:(x.name||x.directory_id)+' · '+(x.status==='paused'?'已暂停':'连续采集失败 '+x.consecutive_failures+' 次')}))));
  else overview.append(el('p',{class:'empty',text:'没有需要处理的采集异常。'}));

  const programPanel=panels.programs;
  programPanel.append(el('h2',{text:'节目表现与曝光'}),el('p',{class:'muted',text:'“来源”页负责采集、暂停和分类；这里仅决定节目是否进入公共空间、是否可被推荐，以及人工权重。'}));
  const programQuery=el('input',{type:'search',placeholder:'搜索节目、平台或编号'});
  const programState=el('select',{'aria-label':'筛选节目'},[['all','全部节目'],['public','已进入公共空间'],['recommend','允许推荐'],['paused','已暂停'],['off','未公开或未推荐']].map(x=>el('option',{value:x[0],text:x[1]})));
  const programCount=el('p',{class:'muted'}), programList=el('div',{class:'ops-list'});
  const drawPrograms=()=>{
    const q=programQuery.value.trim().toLowerCase(), mode=programState.value;
    const rows=programs.filter(p=>{
      const text=[p.name,p.platform,p.directory_id].join(' ').toLowerCase();
      if(q && !text.includes(q)) return false;
      if(mode==='public') return !!p.public_enabled;
      if(mode==='recommend') return !!p.recommend_enabled;
      if(mode==='paused') return p.status==='paused';
      if(mode==='off') return !p.public_enabled && !p.recommend_enabled;
      return true;
    });
    programCount.textContent='显示 '+rows.length+' / '+programs.length+' 个节目（点击节目展开设置）';
    programList.textContent=''; rows.forEach(p=>programList.append(programOpsCard(p)));
    if(!rows.length) programList.append(el('p',{class:'empty',text:'没有符合筛选条件的节目。'}));
  };
  programQuery.addEventListener('input',drawPrograms); programState.addEventListener('change',drawPrograms);
  programPanel.append(el('div',{class:'ops-filters'},programQuery,programState),programCount,programList); drawPrograms();

  subscriptionsOpsPanel(panels.subscriptions,subscriptions);

  const contentPanel=panels.content;
  contentPanel.append(el('h2',{text:'内容编辑'}),el('p',{class:'muted',text:'只在需要时展开一篇内容，设置人工权重、隐藏、动态封面与栏目投放。'}));
  const contentQuery=el('input',{type:'search',placeholder:'搜索标题或节目'});
  const languages=['',...Array.from(new Set(contents.map(c=>c.language).filter(Boolean))).sort()];
  const contentLanguage=el('select',{'aria-label':'筛选语言'},languages.map(v=>el('option',{value:v,text:v||'全部语言'})));
  const contentState=el('select',{'aria-label':'筛选内容状态'},[['all','全部内容'],['visible','正常显示'],['hidden','已隐藏'],['placed','已投放栏目']].map(x=>el('option',{value:x[0],text:x[1]})));
  const contentCount=el('p',{class:'muted'}), contentList=el('div',{class:'ops-list'}); let contentLimit=20;
  const drawContents=()=>{
    const q=contentQuery.value.trim().toLowerCase(), lang=contentLanguage.value, mode=contentState.value;
    const rows=contents.filter(c=>{
      if(q && ![c.title,c.program_name,c.directory_id].join(' ').toLowerCase().includes(q)) return false;
      if(lang && c.language!==lang) return false;
      if(mode==='visible') return !c.hidden;
      if(mode==='hidden') return !!c.hidden;
      if(mode==='placed') return Array.isArray(c.placement_slugs) && c.placement_slugs.length;
      return true;
    });
    const shown=rows.slice(0,contentLimit); contentCount.textContent='显示 '+shown.length+' / '+rows.length+' 篇内容；默认按近 30 天打开量排序。';
    contentList.textContent=''; shown.forEach(c=>contentList.append(contentOpsRow(c,covers)));
    if(!rows.length) contentList.append(el('p',{class:'empty',text:'没有符合筛选条件的内容。'}));
    if(rows.length>shown.length) contentList.append(el('button',{type:'button',text:'再显示 20 篇',onclick:()=>{contentLimit+=20;drawContents();}}));
  };
  contentQuery.addEventListener('input',()=>{contentLimit=20;drawContents();}); contentLanguage.addEventListener('change',()=>{contentLimit=20;drawContents();}); contentState.addEventListener('change',()=>{contentLimit=20;drawContents();});
  contentPanel.append(el('div',{class:'ops-filters'},contentQuery,contentLanguage,contentState),contentCount,contentList); drawContents();

  const modulePanel=panels.modules;
  modulePanel.append(el('h2',{text:'公共空间模块'}),el('p',{class:'muted',text:'控制栏目是否显示、设备范围、数量和推荐方式。每个节目可设置上限，避免一个来源占满首页。'}),...modules.map(moduleOpsCard));

  const assetPanel=panels.assets;
  const addCover=el('button',{type:'button',text:'加入粒子网络预设',onclick:async()=>{try{await ops('save_cover',addParticleCover());await renderOps();}catch(e){say(e.message,'bad');}}});
  assetPanel.append(el('h2',{text:'动态 Cover'}),el('p',{class:'muted',text:'封面配置只保存风格规则，标题由公共空间前端实时叠加。'}),el('div',{class:'actions'},addCover),...covers.map(coverOpsCard),el('h2',{text:'广告位'}),el('p',{class:'muted',text:'只管理位置和设备；广告代码继续留在前端配置。'}),...ads.map(adOpsCard));
  box.append(tabs,...tabDefs.map(([id])=>panels[id]));
}
async function renderDone(){
  const box = $('#done'); box.textContent = '';
  const issues = (await listIssues('closed', ['listed', 'rejected'])).sort((a, b) => Date.parse(b.closed_at || 0) - Date.parse(a.closed_at || 0)).slice(0, 40);
  if(!issues.length) box.append(el('p', { class: 'empty', text: '还没有处理过的申请。' }));
  for(const i of issues){
    const l = labelsOf(i);
    box.append(el('div', { class: 'row' }, el('span', { class: 'name' }, el('a', { href: i.html_url, target: '_blank', rel: 'noopener noreferrer', text: i.title }), el('span', { class: 'sub', text: '#' + i.number + ' · ' + ago(i.closed_at) })),
      el('span', { class: 'badge ' + (l.includes('listed') ? 'checked' : 'invalid'), text: l.includes('listed') ? '已收录' : '已拒绝' })));
  }
}

/* ---------- start ---------- */
/* Where the data lives: on GitHub Pages (next to this page) it is ../directory.json. On the submit function's own site (Vercel) that file is only a copy from the last deploy,
   so the function says (GET ../api/submit) which repository and which published address to use. */
const WHY = {
  short: '服务器上已经有 ADMIN_PASSWORD，但不到 12 个字符，所以密码登录没有启用。换一个至少 12 个字符的，再重新部署。',
  password: '服务器上还没有 ADMIN_PASSWORD（要加在 Production 环境里；加完要重新部署一次才生效）。',
  token: '服务器上还没有 ADMIN_GITHUB_TOKEN（要加在 Production 环境里；加完要重新部署一次才生效）。',
  repo: '服务器上的 SHELF_REPO 不对：要写成 用户名/仓库名。',
  absent: '这个站点上没有找到 /api/admin：管理密码功能还没有部署到这里（GitHub Pages 上的管理页没有它，只能用令牌）。',
  unknown: '服务器上的管理密码还没有配置好。'
};
async function discover(){
  let password = false, problem = 'absent';
  try{                                                                      // is there a password on this site? (only the site that serves the function answers)
    const r = await fetch(ADMIN_API, { cache: 'no-store' });
    if(r.ok){ const d = await r.json(); if(d && d.service === 'shelf-admin'){ password = !!d.configured; problem = d.configured ? '' : (d.problem || 'unknown'); } }
  }catch(e){}
  if(LOCAL && params.get('repo')) return { repo: params.get('repo'), directory: '', password, problem };
  try{
    const r = await fetch('../api/submit', { cache: 'no-store' });
    if(r.ok){ const d = await r.json(); if(d && d.repo) return { repo: d.repo, directory: /^https:\/\/[^\s]+\/$/.test(d.directory || '') ? d.directory : '', password, problem }; }
  }catch(e){}
  return { repo: '', directory: '', password, problem };
}
/* With a token the list of what is listed is read from the repository itself (the committed docs/directory.json): it is right the moment an approval has been committed,
   without waiting for GitHub Pages to rebuild. Without one (or if that fails) the published copy is used. */
async function loadDirectory(){
  state.directory = null; state.directoryFrom = '';
  if(state.mode === 'password' && state.session && state.repo){
    try{
      const r = await gh('GET', `/repos/${state.repo}/contents/docs/directory.json`);
      if(r.ok && r.data && typeof r.data === 'object'){ state.directory = r.data; state.directoryFrom = 'repo'; return; }
    }catch(e){}
  }else if(state.token && state.repo){
    try{
      const r = await fetch(API + `/repos/${state.repo}/contents/docs/directory.json`, { cache: 'no-store', headers: { Authorization: 'Bearer ' + state.token, Accept: 'application/vnd.github.raw+json', 'X-GitHub-Api-Version': '2022-11-28' } });
      if(r.ok){ state.directory = await r.json(); state.directoryFrom = 'repo'; return; }
    }catch(e){}
  }
  try{ const r = await fetch((state.directoryUrl || '../') + 'directory.json', { cache: 'no-cache' }); state.directory = r.ok ? await r.json() : null; state.directoryFrom = 'pages'; }catch(e){ state.directory = null; }
}
async function refresh(){
  if(state.busy) return; state.busy = true; say('正在读取…');
  try{ await loadDirectory(); await Promise.all([loadRegistry(), renderPending(), renderDone()]); renderListed(); renderSources(); if(!$('#ops').hidden) await renderOps(); say(''); }
  catch(e){ say(e.message, 'bad'); }
  state.busy = false;
}
async function enter(info, weak){
  state.branch = info.default_branch || 'main';
  if(info.permissions && !info.permissions.push) throw new Error(weak);
  $('#login').hidden = true; $('#app').hidden = false;
  ['#refreshBtn', '#dailyBtn', '#logoutBtn'].forEach(s => { $(s).hidden = false; });
  await refresh();
}
async function connect(token, remember){                                    // the maintainer's own GitHub token, used from this browser
  state.mode = 'token'; state.token = token;
  const info = need(await gh('GET', `/repos/${state.repo}`), '连接仓库 ' + state.repo + '（令牌是否选了这个仓库？）');
  if(info.permissions && !info.permissions.push) throw new Error('这个令牌对仓库没有写权限（Issues 和 Actions 要选 Read and write）');
  if(!state.passwordSite) store.set(token, remember);                       // where a password exists no token is ever kept in the browser
  await enter(info);
}
async function connectSession(session, remember){                          // a session from the password form (or kept from an earlier visit)
  state.mode = 'password'; state.session = session;
  const info = need(await gh('GET', `/repos/${state.repo}`), '连接仓库 ' + state.repo);
  sessions.set(session, remember);
  await enter(info, '服务器上的 GitHub 令牌对仓库没有写权限（Issues 和 Actions 要选 Read and write）');
}
function showLogin(msg){
  state.session = ''; state.token = ''; state.mode = state.passwordSite ? 'password' : 'token';
  $('#app').hidden = true; $('#login').hidden = false; ['#refreshBtn', '#dailyBtn', '#logoutBtn'].forEach(s => { $(s).hidden = true; });
  $('#pwBox').hidden = !state.passwordSite; $('#setupPw').hidden = state.passwordSite;
  if(!state.passwordSite){ $('#setupWhy').textContent = WHY[state.pwProblem] || ''; $('#setupPw').open = !!WHY[state.pwProblem]; }          // say what is missing, instead of leaving a guess
  $('#tokenBox').open = !state.passwordSite; $('#rememberRow').hidden = state.passwordSite;
  $('#tokenSummary').textContent = state.passwordSite ? '改用 GitHub 令牌登录（高级）' : '用 GitHub 令牌登录';
  $('#loginMsg').textContent = msg || ''; $('#loginMsg').className = 'msg' + (msg ? ' bad' : '');
}

$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault(); const t = $('#token').value.trim(); if(!t) return;
  $('#loginMsg').textContent = '连接中…'; $('#loginMsg').className = 'msg';
  try{ await connect(t, $('#remember').checked); $('#token').value = ''; }catch(err){ showLogin(err.message); }
});
$('#pwForm').addEventListener('submit', async e => {
  e.preventDefault(); const pw = $('#pw').value; if(!pw) return;
  $('#loginMsg').textContent = '登录中…'; $('#loginMsg').className = 'msg';
  const remember = $('#pwRemember').checked;
  try{
    let r, j = null;
    try{
      r = await fetch(ADMIN_API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'login', password: pw, remember }) });
      try{ j = await r.json(); }catch(x){}
    }catch(x){ throw new Error('连不上管理服务，请检查网络后再试'); }
    if(!j || !j.ok || !j.session) throw new Error((j && j.message) || ('登录失败：HTTP ' + r.status));
    $('#pw').value = '';
    await connectSession(j.session, remember);
  }catch(err){ showLogin(err.message); }
});
$('#refreshBtn').addEventListener('click', refresh);
$('#logoutBtn').addEventListener('click', () => { store.clear(); sessions.clear(); state.token = ''; state.session = ''; showLogin(''); });
$('#dailyBtn').addEventListener('click', async () => { try{ await dispatch('daily.yml'); say('已触发更新，约 1–2 分钟后点“刷新”。', 'good'); }catch(e){ say(e.message, 'bad'); } });
document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('[data-tab]').forEach(x => x.setAttribute('aria-selected', String(x === b)));
  for(const id of ['pending', 'listed', 'sources', 'ops', 'done']) $('#' + id).hidden = id !== b.dataset.tab;
  $('#hint').hidden = b.dataset.tab !== 'pending';                          // the line about applications belongs to the first tab
  if(b.dataset.tab === 'ops') renderOps();
}));

(async function boot(){
  const found = await discover();
  state.directoryUrl = found.directory;
  state.passwordSite = found.password; state.pwProblem = found.problem; state.mode = found.password ? 'password' : 'token';
  await loadDirectory();
  state.repo = found.repo || (state.directory && state.directory.repo) || '';
  $('#repoName').textContent = state.repo;
  if(!state.repo){ showLogin('读不到目录信息（directory.json 里没有仓库名）。确认目录已经发布过。'); return; }
  if(state.passwordSite){
    store.clear();                                                          // a GitHub token kept here by the old way of signing in is not needed any more: do not leave it lying around
    const kept = sessions.get();
    if(kept){ try{ await connectSession(kept, sessions.remembered()); return; }catch(e){ sessions.clear(); showLogin(e.message); return; } }
    showLogin(''); return;
  }
  const saved = store.get();
  if(saved){ try{ await connect(saved, !!localStorage.getItem('shelf.token')); return; }catch(e){ store.clear(); showLogin(e.message); return; } }
  showLogin('');
})();
})();
