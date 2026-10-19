/* Directory admin: the applications (GitHub issues), approve / reject, and what is listed (with delete).
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
const BOT = '<!-- shelf-directory-bot -->';
const state = { repo: '', token: '', session: '', mode: 'token', passwordSite: false, pwProblem: '', branch: 'main', directory: null, directoryUrl: '', directoryFrom: '', busy: false };

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
  const badges = [el('span', { class: 'badge ' + (p.kind === 'shelf' ? '' : 'post'), text: { post: '个人文章', record: '资料卡', shelf: '网址' }[p.kind] }), el('span', { class: 'badge', text: viaWeb ? '网页提交' : 'GitHub 表单' })];
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
  return el('article', { class: 'card' }, el('h3', { text: head }), el('p', { class: 'meta' }, ...badges, '#' + issue.number + ' · ' + ago(issue.created_at)), body, actions, slot, status);
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
  if(!issues.length) box.append(el('p', { class: 'empty', text: '没有待审的申请。' }));
  issues.forEach(i => box.append(pendingCard(i)));
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
  try{ await loadDirectory(); await Promise.all([renderPending(), renderDone()]); renderListed(); say(''); }
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
  for(const id of ['pending', 'listed', 'done']) $('#' + id).hidden = id !== b.dataset.tab;
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
