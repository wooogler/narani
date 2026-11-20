"""서재 검색 창 — 홈의 돋보기 단추·⌘K(다른 OS는 Ctrl+K)·메뉴 '서재 검색…'이 연다. 홈에서도 논문 탭에서도.

셸에 한 번 붙는다(§app.shell) — 화면을 갈아 끼워도 창과 서버 이벤트가 하나뿐이다(화면마다 `ui.on`을 달면
탭을 오갈수록 쌓인다). 검색은 `GET /libsearch`(§ui/libsearch.py)가 하고, 이 창은 입력 → 잠깐 기다렸다
(debounce) 묻기 → 네 묶음(논문·컬렉션·본문 문단·내 하이라이트와 메모)을 그리기만 한다.

고르면:
- 논문 → 그 논문 탭을 연다 (`__md4Agent.openUrl` — 에이전트 창의 화면 이동과 같은 서버 이벤트).
- 문단·메모 → 에이전트 인용 칩과 같은 길(`__md4Agent.goto`): 지금 그 논문이면 그 행(메모면 그 하이라이트)으로,
  아니면 논문을 열고 도착한 뷰어가 이어서 간다.
- 컬렉션 → 서버가 그 컬렉션을 홈의 범위로 정하고 홈을 다시 짓는다(다른 탭이면 홈으로 옮긴다).

⌘K는 편집기에서 '링크'다(§ui/md_keys.py). 페이지 keydown은 편집기 안이면 손대지 않고, 메뉴 막대가 먼저 먹은
⌘K(`menu()`)는 편집기에 같은 키를 다시 보내 링크 넣기가 그대로 돈다.

결과 글은 DOM의 textContent로만 넣는다(논문 글을 innerHTML로 넣지 않는다) — 강조는 서버가 준 자리로 <mark>를 만든다.
"""

from __future__ import annotations

SCOPE_EVENT = "md4-libsearch-scope"   # JS → 서버: 컬렉션을 골랐다 {id, home}
DEBOUNCE_MS = 140
RETRY_MS = 700                        # 색인을 채우는 중이면 이만큼 뒤 같은 질문을 다시

CSS = """
#md4-ls { position: fixed; inset: 0; z-index: 10060; display: none; align-items: flex-start; justify-content: center;
  background: rgba(15,15,15,.28); padding-top: 9vh; }
#md4-ls.on { display: flex; }
#md4-ls .ls-box { width: min(1080px, calc(100vw - 32px)); height: min(640px, 80vh); display: flex; flex-direction: column;
  background: #fff; color: #37352f; border-radius: 12px; box-shadow: 0 18px 60px rgba(0,0,0,.28);
  border: 1px solid rgba(0,0,0,.1); overflow: hidden; font-size: 14px; }
#md4-ls .ls-top { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-bottom: 1px solid #ececea; }
#md4-ls .ls-top .material-icons { color: #9a968e; font-size: 20px; }
#md4-ls input { flex: 1 1 auto; border: 0; outline: none; font-size: 16px; background: transparent; color: inherit;
  padding: 4px 0; }
#md4-ls .ls-status { font-size: 12px; color: #9a968e; white-space: nowrap; }
#md4-ls .ls-body { flex: 1 1 auto; min-height: 0; display: flex; }
#md4-ls .ls-list { flex: 0 0 44%; min-width: 0; overflow-y: auto; padding: 4px 0 8px; border-right: 1px solid #ececea; }
#md4-ls .ls-prev { position: relative; flex: 1 1 auto; min-width: 0; overflow-y: auto; padding: 0 20px 20px; line-height: 1.6; }
#md4-ls .lp-top { position: sticky; top: 0; z-index: 1; background: #fff; padding: 14px 0 6px; border-bottom: 1px solid #f0efed; }
#md4-ls .lp-pad { height: 14px; }
#md4-ls .lp-paper { font-size: 12px; color: #8a867e; }
#md4-ls .lp-title { font-size: 17px; font-weight: 600; line-height: 1.35; margin: 2px 0 4px; }
#md4-ls .lp-sub { font-size: 13px; color: #6b6862; }
#md4-ls .lp-head { font-size: 13px; font-weight: 600; margin: 4px 0 8px; }
#md4-ls .lp-lab { font-size: 11px; font-weight: 600; color: #9a968e; letter-spacing: .03em; margin: 12px 0 2px; }
#md4-ls .lp-text { font-size: 13.5px; white-space: pre-wrap; }
#md4-ls .lp-note { display: flex; gap: 8px; margin: 10px 0; padding: 8px 10px; border-radius: 8px; background: rgba(0,0,0,.035); font-size: 13px; }
#md4-ls .lp-chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 8px 0; }
#md4-ls .lp-chip { font-size: 11.5px; padding: 1px 8px; border-radius: 10px; background: rgba(35,131,226,.1); color: #2383e2; }
#md4-ls .lp-li { font-size: 13px; padding: 3px 0; border-bottom: 1px solid #f0efed; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 760px) { #md4-ls .ls-prev { display: none; } #md4-ls .ls-list { flex: 1 1 auto; border-right: 0; } }
#md4-ls .ls-sec { font-size: 11.5px; font-weight: 600; color: #9a968e; padding: 10px 16px 4px; letter-spacing: .02em; }
#md4-ls .ls-item { padding: 6px 16px; cursor: pointer; display: flex; gap: 10px; align-items: flex-start; }
#md4-ls .ls-item.on { background: rgba(35,131,226,.11); }
#md4-ls .ls-item .ls-i { flex: 0 0 auto; color: #9a968e; font-size: 18px; margin-top: 1px; }
#md4-ls .ls-item.on .ls-i { color: #2383e2; }
#md4-ls .ls-t { min-width: 0; flex: 1 1 auto; }
#md4-ls .ls-title { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#md4-ls .ls-sub { font-size: 12px; color: #8a867e; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 1px; }
#md4-ls .ls-snip { font-size: 13px; color: #55524b; line-height: 1.45; display: -webkit-box; -webkit-line-clamp: 3;
  -webkit-box-orient: vertical; overflow: hidden; margin-top: 1px; }
#md4-ls .ls-row { padding-left: 44px; }
#md4-ls .ls-head { font-size: 11.5px; color: #9a968e; }
#md4-ls .ls-more { font-size: 12px; color: #9a968e; padding: 0 16px 2px 44px; }
#md4-ls .ls-dot { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 auto; margin: 5px 4px 0 4px; }
#md4-ls .ls-note { font-size: 12.5px; color: #37352f; margin-top: 2px; }
#md4-ls mark { background: #ffe9a3; color: inherit; border-radius: 2px; padding: 0 1px; }
#md4-ls .ls-empty { padding: 18px 16px; color: #9a968e; font-size: 13px; }
#md4-ls .ls-foot { display: flex; gap: 14px; padding: 7px 14px; border-top: 1px solid #ececea; font-size: 11.5px; color: #9a968e; }
#md4-ls kbd { font-family: inherit; font-size: 11px; border: 1px solid #dcdad5; border-bottom-width: 2px; border-radius: 4px;
  padding: 0 4px; margin-right: 3px; }
/* 홈 왼쪽 칸 맨 위 — 노션의 '검색'처럼 줄 하나 */
.md4-ls-side { display: flex; align-items: center; gap: 10px; width: calc(100% - 16px); margin: 8px 8px 0; padding: 6px 10px;
  border: 1px solid #e3e1dc; border-radius: 8px; background: #fff; color: #8a867e; font-size: 13.5px; cursor: pointer;
  text-align: left; font-family: inherit; }
.md4-ls-side:hover { border-color: #c9c6bf; color: #37352f; }
.md4-ls-side:focus-visible { outline: 2px solid #2383e2; outline-offset: 1px; }
.md4-ls-side .md4-ls-lab { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.md4-ls-side .md4-ls-kbd { font-family: inherit; font-size: 11px; color: #9a968e; border: 1px solid #e3e1dc; border-radius: 4px; padding: 0 4px; }
@media (prefers-color-scheme: dark) {
  #md4-ls { background: rgba(0,0,0,.45); }
  #md4-ls .ls-box { background: #252525; color: #e6e6e6; border-color: rgba(255,255,255,.12); }
  #md4-ls .ls-top, #md4-ls .ls-foot, #md4-ls .ls-list { border-color: #383838; }
  #md4-ls .lp-sub { color: #a8a8a8; }
  #md4-ls .lp-note { background: rgba(255,255,255,.06); }
  #md4-ls .lp-li, #md4-ls .lp-top { border-color: #333; }
  #md4-ls .lp-top { background: #252525; }
  #md4-ls .lp-chip { background: rgba(90,150,230,.2); color: #7bb7ff; }
  #md4-ls .ls-item.on { background: rgba(90,150,230,.22); }
  #md4-ls .ls-item.on .ls-i { color: #7bb7ff; }
  #md4-ls .ls-snip { color: #bdbdbd; }
  #md4-ls .ls-note { color: #e6e6e6; }
  #md4-ls mark { background: #7a5c00; color: #fff; }
  #md4-ls kbd { border-color: #4a4a4a; }
  .md4-ls-side { background: #232323; border-color: #3a3a3a; color: #9a9a9a; }
  .md4-ls-side:hover { border-color: #555; color: #e6e6e6; }
  .md4-ls-side .md4-ls-kbd { border-color: #444; color: #8a8a8a; }
}
"""

HTML = """
<div id="md4-ls" role="dialog" aria-modal="true" aria-label="⦃서재 검색⦄">
  <div class="ls-box">
    <div class="ls-top">
      <i class="material-icons" aria-hidden="true">search</i>
      <input type="text" spellcheck="false" autocomplete="off" placeholder="⦃제목·저자·컬렉션·본문·메모 검색…⦄"
             aria-label="⦃서재 검색⦄" role="combobox" aria-expanded="true" aria-controls="md4-ls-list">
      <span class="ls-status"></span>
    </div>
    <div class="ls-body">
      <div class="ls-list" id="md4-ls-list" role="listbox"></div>
      <div class="ls-prev" aria-live="polite"></div>
    </div>
    <div class="ls-foot"><span><kbd>↑</kbd><kbd>↓</kbd>⦃옮기기⦄</span><span><kbd>Enter</kbd>⦃열기⦄</span>
      <span><kbd>Esc</kbd>⦃닫기⦄</span></div>
  </div>
</div>
<script>
(function(){
  if (window.__md4LsInit) return; window.__md4LsInit = true;
  var root = document.getElementById('md4-ls');
  if (!root) return;
  var box = root.querySelector('.ls-box'), input = root.querySelector('input');
  var list = root.querySelector('.ls-list'), status = root.querySelector('.ls-status');
  var prev = root.querySelector('.ls-prev'), pvTimer = null, pvSeq = 0, pvCache = {};
  var MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
  var DEBOUNCE = %(debounce)d, RETRY = %(retry)d, SCOPE = '%(scope)s';
  var items = [], cur = -1, seq = 0, timer = null, retry = null, lastQ = null;
  var COLORS = {yellow: '#f5c518', green: '#4caf50', blue: '#42a5f5', pink: '#f06292', purple: '#ab47bc'};

  function isOpen(){ return root.classList.contains('on'); }
  function open(){
    if (!isOpen()){
      root.classList.add('on');
      pvCache = {};                                                     // 열 때마다 새로 (그사이 메모·번역이 바뀌었을 수 있다)
      fetch('/libsearch/warm', {method: 'POST'}).catch(function(){});   // 색인을 미리 (바뀐 논문만)
    }
    input.focus(); input.select();
    if (input.value.trim()) run(true); else empty();
  }
  function close(){
    if (!isOpen()) return;
    root.classList.remove('on');
    clearTimeout(timer); clearTimeout(retry); clearTimeout(pvTimer); seq++; pvSeq++;
  }
  function inEditor(el){ return !!(el && el.closest && el.closest('.cm-editor, .ProseMirror')); }
  // 메뉴 막대의 ⌘K — 메뉴가 키를 먼저 먹는다. 편집기 안이면 같은 키를 편집기로 돌려 '링크'가 그대로 돈다.
  function menu(){
    var ae = document.activeElement;
    if (inEditor(ae) && !isOpen()){
      ae.dispatchEvent(new KeyboardEvent('keydown', {key: 'k', code: 'KeyK', metaKey: MAC, ctrlKey: !MAC,
                                                     bubbles: true, cancelable: true}));
      return 'ok';
    }
    if (isOpen()) close(); else open();
    return 'ok';
  }
  window.__md4LibSearch = {open: open, close: close, menu: menu, isOpen: isOpen};

  function el(tag, cls, text){
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function marked(cls, snip){            // {text, hits:[[s,e]]} → 글 + <mark> (textContent로만)
    var e = el('div', cls), t = (snip && snip.text) || '', at = 0;
    ((snip && snip.hits) || []).forEach(function(h){
      if (h[0] < at) return;
      if (h[0] > at) e.appendChild(document.createTextNode(t.slice(at, h[0])));
      e.appendChild(el('mark', null, t.slice(h[0], h[1])));
      at = h[1];
    });
    if (at < t.length) e.appendChild(document.createTextNode(t.slice(at)));
    return e;
  }
  function empty(msg){
    items = []; cur = -1; list.textContent = ''; status.textContent = ''; prev.textContent = '';
    list.appendChild(el('div', 'ls-empty', msg || md4T('논문 제목·저자·컬렉션 이름, 그리고 모든 논문의 본문(원문·번역)과 내 하이라이트·메모에서 찾습니다.')));
  }
  function setCur(i, scroll){
    if (!items.length) return;
    i = (i + items.length) %% items.length;
    if (items[cur]) items[cur].node.classList.remove('on');
    cur = i;
    items[cur].node.classList.add('on');
    input.setAttribute('aria-activedescendant', items[cur].node.id);
    if (scroll) items[cur].node.scrollIntoView({block: 'nearest'});
    showPreview(items[cur].act);
  }
  function add(node, act){
    node.id = 'md4-ls-it' + items.length;
    node.setAttribute('role', 'option');
    var i = items.length;
    items.push({node: node, act: act});
    node.addEventListener('mousemove', function(){ if (cur !== i) setCur(i, false); });
    node.addEventListener('click', function(){ choose(i); });
    list.appendChild(node);
  }
  function item(icon, title, sub, extraCls){
    var n = el('div', 'ls-item' + (extraCls ? ' ' + extraCls : ''));
    n.appendChild(el('i', 'material-icons ls-i', icon));
    var t = el('div', 'ls-t');
    t.appendChild(el('div', 'ls-title', title));
    if (sub) t.appendChild(el('div', 'ls-sub', sub));
    n.appendChild(t);
    return n;
  }
  // ---- 오른쪽 칸: 고른 결과의 자세한 모습 (문단은 원문·번역 통째, 논문은 서지·앞머리, 컬렉션은 논문들) ----
  function pvKey(a){ return [a.kind, a.token || '', a.row == null ? '' : a.row, a.id || '', lastQ || ''].join('|'); }
  function pvUrl(a){
    var k = a.kind === 'note' ? 'row' : a.kind;
    return '/libsearch/preview?kind=' + k + '&token=' + encodeURIComponent(a.token || '') + '&row=' +
      (a.row == null ? -1 : a.row) + '&id=' + encodeURIComponent(a.id || '') + '&q=' + encodeURIComponent(lastQ || '');
  }
  function showPreview(a){
    clearTimeout(pvTimer);
    var key = pvKey(a), my = ++pvSeq;
    if (pvCache[key]){ drawPreview(a, pvCache[key]); return; }
    pvTimer = setTimeout(function(){
      fetch(pvUrl(a)).then(function(r){ return r.json(); }).then(function(d){
        pvCache[key] = d;
        if (my === pvSeq && isOpen()) drawPreview(a, d);
      }).catch(function(){});
    }, 70);
  }
  function lab(t){ prev.appendChild(el('div', 'lp-lab', t)); }
  function noteBox(n, quote, note){
    var b = el('div', 'lp-note'), dot = el('span', 'ls-dot'), t = el('div', 'ls-t');
    dot.style.background = COLORS[n.color] || COLORS.yellow;
    b.appendChild(dot);
    t.appendChild(quote && quote.text ? marked('', quote) : el('div', '', '“' + (n.quote || '') + '”'));
    if (note && note.text) t.appendChild(marked('ls-note', note));
    else if (n.note) t.appendChild(el('div', 'ls-note', n.note));
    b.appendChild(t);
    return b;
  }
  function drawPreview(a, d){
    prev.textContent = '';
    if ((!d || d.kind === 'none') && a.kind === 'note'){       // 헤더뿐인 행의 메모 — 문단은 없어도 메모는 보인다
      prev.appendChild(el('div', 'lp-pad'));
      prev.appendChild(noteBox(a, a.quote, a.note));
      return;
    }
    if (!d || d.kind === 'none') return;
    if (d.kind === 'row'){
      var top = el('div', 'lp-top');               // 맞은 자리로 내려도 어느 논문·섹션인지 보이게
      top.appendChild(el('div', 'lp-paper', d.title));
      if (d.heading) top.appendChild(el('div', 'lp-head', d.heading));
      prev.appendChild(top);
      if (a.kind === 'note') prev.appendChild(noteBox(a, a.quote, a.note));
      if (d.en){ lab(md4T('원문')); prev.appendChild(marked('lp-text', d.en)); }
      if (d.ko){ lab(md4T('번역')); prev.appendChild(marked('lp-text', d.ko)); }
      var others = (d.notes || []).filter(function(n){ return n.id !== a.id; });
      if (others.length){
        lab(md4T('내 하이라이트·메모'));
        others.forEach(function(n){ prev.appendChild(noteBox(n)); });
      }
    } else if (d.kind === 'paper'){
      prev.appendChild(el('div', 'lp-pad'));
      prev.appendChild(el('div', 'lp-title', d.title));
      if (d.title_tr) prev.appendChild(el('div', 'lp-sub', d.title_tr));
      var sub = [(d.authors || []).join(', '), [d.year, d.venue].filter(Boolean).join(' · ')].filter(Boolean);
      sub.forEach(function(s){ prev.appendChild(el('div', 'lp-sub', s)); });
      if ((d.collections || []).length){
        var cs = el('div', 'lp-chips');
        d.collections.forEach(function(c){ cs.appendChild(el('span', 'lp-chip', c)); });
        prev.appendChild(cs);
      }
      if (d.notes) prev.appendChild(el('div', 'lp-sub', md4T('하이라이트·메모 {n}개', {n: d.notes})));
      (d.rows || []).forEach(function(r){
        lab(r.heading || md4T('원문'));
        prev.appendChild(marked('lp-text', r.en));
        if (r.ko && r.ko.text) prev.appendChild(marked('lp-text lp-sub', r.ko));
      });
    } else if (d.kind === 'collection'){
      prev.appendChild(el('div', 'lp-pad'));
      prev.appendChild(el('div', 'lp-title', d.path));
      prev.appendChild(el('div', 'lp-sub', md4T('논문 {n}편', {n: d.count})));
      lab(md4T('논문'));
      (d.titles || []).forEach(function(t){ prev.appendChild(el('div', 'lp-li', t)); });
    }
    prev.scrollTop = 0;
    var m = d.kind === 'row' && prev.querySelector('.lp-text mark');   // 문단은 맞은 자리가 보이게 (논문은 제목부터)
    if (m && m.offsetTop > prev.clientHeight * 0.7) prev.scrollTop = m.offsetTop - prev.clientHeight * 0.45;
  }
  function render(r){
    items = []; cur = -1; list.textContent = ''; prev.textContent = '';
    if (r.papers.length){
      list.appendChild(el('div', 'ls-sec', md4T('논문')));
      r.papers.forEach(function(p){
        var sub = [p.year, (p.authors || []).join(', '), p.venue].filter(Boolean).join(' · ');
        var n = item('description', p.title, p.title_tr ? p.title_tr + (sub ? ' · ' + sub : '') : sub);
        add(n, {kind: 'paper', token: p.token, url: p.url});
      });
    }
    if (r.collections.length){
      list.appendChild(el('div', 'ls-sec', md4T('컬렉션')));
      r.collections.forEach(function(c){
        add(item('folder', c.path, md4T('논문 {n}편', {n: c.count})), {kind: 'collection', id: c.id});
      });
    }
    if (r.passages.length){
      list.appendChild(el('div', 'ls-sec', md4T('본문')));
      r.passages.forEach(function(g){
        add(item('article', g.title, g.title_tr || null), {kind: 'paper', token: g.token, url: g.url});
        g.rows.forEach(function(row){
          var n = el('div', 'ls-item ls-row'), t = el('div', 'ls-t');
          if (row.heading) t.appendChild(el('div', 'ls-head', row.heading + (row.side === 'ko' ? ' · ' + md4T('번역') : '')));
          t.appendChild(marked('ls-snip', row));
          n.appendChild(t);
          add(n, {kind: 'row', token: g.token, row: row.row, url: g.url});
        });
        if (g.more) list.appendChild(el('div', 'ls-more', md4T('이 논문에서 {n}곳 더', {n: g.more})));
      });
    }
    if (r.notes.length){
      list.appendChild(el('div', 'ls-sec', md4T('내 하이라이트·메모')));
      r.notes.forEach(function(nt){
        var n = el('div', 'ls-item'), t = el('div', 'ls-t'), dot = el('span', 'ls-dot');
        dot.style.background = COLORS[nt.color] || COLORS.yellow;
        n.appendChild(dot);
        t.appendChild(marked('ls-snip', nt.quote));
        if (nt.note) t.appendChild(marked('ls-note', nt.note));
        t.appendChild(el('div', 'ls-sub', nt.title));
        n.appendChild(t);
        add(n, {kind: 'note', token: nt.token, row: nt.row, id: nt.id, url: nt.url, color: nt.color,
                quote: nt.quote, note: nt.note});
      });
    }
    if (!items.length) empty(md4T('찾은 것이 없습니다.'));
    else setCur(0, false);
  }
  function run(now){
    clearTimeout(timer); clearTimeout(retry);
    var q = input.value.trim();
    if (!q){ seq++; lastQ = null; empty(); return; }
    timer = setTimeout(function(){
      var my = ++seq;
      fetch('/libsearch?q=' + encodeURIComponent(q)).then(function(r){ return r.json(); }).then(function(r){
        if (my !== seq || !isOpen()) return;         // 늦게 온 옛 질문의 답은 버린다
        var keep = (q === lastQ && cur >= 0) ? cur : -1;
        lastQ = q;
        render(r);
        if (keep >= 0 && keep < items.length) setCur(keep, false);   // 색인이 차서 다시 물었을 때 고른 자리 유지
        status.textContent = r.indexing ? md4T('색인 만드는 중…') : '';
        if (r.indexing) retry = setTimeout(function(){ if (input.value.trim() === q) run(true); }, RETRY);
      }).catch(function(){ if (my === seq) empty(md4T('검색하지 못했습니다.')); });
    }, now ? 0 : DEBOUNCE);
  }
  function onHome(){ var p = location.pathname; return p === '/' || p === '/home'; }
  function choose(i){
    var it = items[i];
    if (!it) return;
    var a = it.act, A = window.__md4Agent;
    close();
    if (a.kind === 'collection'){
      if (window.emitEvent) emitEvent(SCOPE, {id: a.id, home: onHome()});
      return;
    }
    if (a.kind === 'paper'){
      if (A && A.openUrl) A.openUrl(a.url); else if (window.emitEvent) emitEvent('md4-agent-nav', {url: a.url});
      return;
    }
    if (A && A.goto) A.goto(a.token, a.row, a.kind === 'note' ? a.id : null, a.url);
  }

  input.addEventListener('input', function(){ run(false); });
  input.addEventListener('keydown', function(ev){
    if (ev.isComposing || ev.keyCode === 229) return;     // 한글 조합 중의 Enter·화살표는 IME 몫
    if (ev.key === 'ArrowDown'){ ev.preventDefault(); setCur(cur + 1, true); }
    else if (ev.key === 'ArrowUp'){ ev.preventDefault(); setCur(cur - 1, true); }
    else if (ev.key === 'Enter'){ ev.preventDefault(); if (cur >= 0) choose(cur); }
  });
  root.addEventListener('mousedown', function(ev){ if (!box.contains(ev.target)) close(); });
  document.addEventListener('keydown', function(ev){
    var mod = MAC ? ev.metaKey : ev.ctrlKey;
    if (mod && !ev.shiftKey && !ev.altKey && (ev.key === 'k' || ev.key === 'K')){
      if (inEditor(ev.target) && !isOpen()) return;       // 편집기의 ⌘K는 '링크'
      ev.preventDefault(); ev.stopPropagation();
      if (isOpen()) close(); else open();
      return;
    }
    if (ev.key === 'Escape' && isOpen()){ ev.preventDefault(); ev.stopPropagation(); close(); }
  }, true);
})();
</script>
"""


def page_html() -> str:
    """창 마크업+스크립트 (화면 언어 문구를 채워서)."""
    from narani import i18n

    code = HTML % {"debounce": DEBOUNCE_MS, "retry": RETRY_MS, "scope": SCOPE_EVENT}
    return i18n.html(code)


def shortcut_label() -> str:
    """단추에 보일 단축키 — 앱은 이 컴퓨터에서 돈다(서버 = 창의 OS)."""
    import sys

    return "⌘K" if sys.platform == "darwin" else "Ctrl+K"


def side_button() -> None:
    """홈 왼쪽 칸 맨 위의 '서재 검색' 줄 — 누르면 창을 연다 (진짜 <button>이라 Tab·Enter로도)."""
    from nicegui import ui

    from narani.i18n import t

    keys = "Meta+K" if shortcut_label() == "⌘K" else "Control+K"
    btn = ui.element("button").classes("md4-ls-side").props(f'type=button aria-keyshortcuts="{keys}"')
    btn.on("click", js_handler="() => window.__md4LibSearch && window.__md4LibSearch.open()")
    with btn:
        ui.icon("search", size="18px")
        ui.label(t("서재 검색")).classes("md4-ls-lab")
        ui.label(shortcut_label()).classes("md4-ls-kbd")


def install(state: dict | None = None) -> None:  # noqa: ARG001 — 셸 설치 훅들과 같은 꼴
    """셸이 한 번 부른다 — 창 CSS·마크업·스크립트와 컬렉션 고르기 이벤트."""
    from nicegui import ui

    from narani.ui import spa

    spa.add_css(CSS)
    spa.add_body_html(page_html())
    ui.on(SCOPE_EVENT, lambda e: pick_collection(e.args or {}))


def pick_collection(args: dict) -> None:
    """고른 컬렉션을 홈의 범위로 — 홈이면 홈을 다시 짓고(범위는 projects.active에서 읽는다), 아니면 홈으로 간다."""
    from nicegui import ui

    from narani import projects
    from narani.ui import agent_panel

    pid = str(args.get("id") or "")
    if not projects.exists(pid):
        return
    projects.set_active(pid)
    if args.get("home"):
        agent_panel._nav("/home", refresh=True)
    else:
        ui.navigate.to("/home")
