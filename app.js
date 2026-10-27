
(function(){
  var CFG = window.NARANI_CFG || {}, CATS = CFG.catalogs || {};
  function known(l){ return !!l && (l in CATS || l === CFG.source_lang); }
  function pick(){
    var saved = null;
    try { saved = localStorage.getItem('narani:ui_lang'); } catch (e) {}
    if (known(saved)) return saved;
    var prefs = navigator.languages || [navigator.language || ''], i, l;
    for (i = 0; i < prefs.length; i++){
      l = String(prefs[i]);
      if (known(l)) return l;
      if (/^zh-(TW|HK|MO|Hant)/i.test(l) && known('zh-Hant')) return 'zh-Hant';
      if (/^zh/i.test(l) && known('zh-Hans')) return 'zh-Hans';
      if (known(l.split('-')[0])) return l.split('-')[0];
    }
    return known(CFG.fallback_lang) ? CFG.fallback_lang : CFG.source_lang;
  }
  window.md4Lang = pick();
  window.md4T = function(s, kw){
    var r = (CATS[window.md4Lang] || {})[s] || s;
    if (kw) r = r.replace(/\{(\w+)\}/g, function(m, k){ return (k in kw) ? kw[k] : m; });
    return r;
  };
})();
(function(){

  function textNodes(root){
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null), out = [], n;
    while ((n = w.nextNode())) out.push(n);
    return out;
  }
  // 평문 오프셋 구간들을 텍스트 노드를 쪼개 감싼다 (구간끼리 겹치지 않아야 한다).
  // 자를 지점을 먼저 다 모은 뒤 노드별로 뒤에서 앞으로 자른다 — 한 노드를 쪼개도 다른 노드의
  // 오프셋은 그대로고, 같은 노드 안에서는 뒷쪽부터 잘라야 앞 오프셋이 살아남는다.
  // 감싸도 textContent는 그대로라 하이라이트 앵커(오프셋)와 문장 span이 서로를 망가뜨리지 않는다.
  function wrapRanges(scope, segs, make){
    var ns = textNodes(scope), nodes = [], groups = [], t = 0, i, k, z;
    for (i = 0; i < ns.length; i++){ var L = ns[i].nodeValue.length; nodes.push([ns[i], t, t + L]); t += L; }
    for (i = 0; i < segs.length; i++){
      for (k = 0; k < nodes.length; k++){
        var s0 = nodes[k][1], e0 = nodes[k][2];
        if (e0 <= segs[i].s || s0 >= segs[i].e) continue;
        var a0 = Math.max(segs[i].s - s0, 0), b0 = Math.min(segs[i].e - s0, e0 - s0);
        if (b0 <= a0) continue;
        var g = null;
        for (z = 0; z < groups.length; z++) if (groups[z][0] === nodes[k][0]) { g = groups[z]; break; }
        if (!g){ g = [nodes[k][0], []]; groups.push(g); }
        g[1].push({a: a0, b: b0, seg: segs[i]});
      }
    }
    for (i = 0; i < groups.length; i++){
      var node = groups[i][0], hs = groups[i][1];
      // MathML 안의 글자는 감싸지 않는다 — <mi> 안에 HTML span이 들어가면 수식 조판이 풀려
      // 글자가 띄엄띄엄 흩어진다. 오프셋 계산엔 그대로 들어가므로 앞뒤 구간은 어긋나지 않는다.
      if (node.parentElement && node.parentElement.closest('math')) continue;
      hs.sort(function(x, y){ return y.a - x.a; });
      for (k = 0; k < hs.length; k++){
        var h = hs[k];
        if (h.b < node.nodeValue.length) node.splitText(h.b);
        var target = h.a > 0 ? node.splitText(h.a) : node;
        var el = make(h.seg);
        target.parentNode.replaceChild(el, target);
        el.appendChild(target);
      }
    }
  }

  // 블록 경계 — <p>a</p><p>b</p>의 textContent는 "ab"라 경계 문자가 없다. 블록별로 나눠
  // 문장을 쪼개지 않으면 앞 문단의 끝과 뒤 문단의 첫 문장이 한 문장으로 붙는다.
  var BLOCK = /^(P|LI|H1|H2|H3|H4|H5|H6|BLOCKQUOTE|PRE|TD|TH|FIGCAPTION|DT|DD)$/;
  function blockRanges(scope){
    var ns = textNodes(scope), out = [], t = 0, cur = null;
    for (var i = 0; i < ns.length; i++){
      var el = ns[i].parentElement, L = ns[i].nodeValue.length;
      while (el && el !== scope && !BLOCK.test(el.tagName)) el = el.parentElement;
      if (!cur || cur.el !== el){ cur = {el: el, s: t, e: t + L}; out.push(cur); }
      else cur.e = t + L;
      t += L;
    }
    return out;
  }

  // 문장 끝처럼 보이지만 아닌 것들: et al. · e.g. · 0.5 · 3.1 · 이니셜(A. Jelson) ·
  // 괄호 안(인용 덩어리 [3, Adams et al., 2022]) · 마침표 뒤가 소문자로 이어지는 경우.
  // ABBR 앞의 "줄머리 또는 공백·여는괄호" 조건은 꼭 필요하다 — 없으면 "critical." "systems." 처럼
  // al·ch·ms로 끝나는 흔한 낱말이 죄다 약어로 잡혀 문장이 안 잘린다.
  var ABBR = /(?:^|[\s(\[\u201c"'])(?:e\.g|i\.e|cf|vs|etc|al|Fig|Figs|Eq|Eqs|Sec|Secs|Tab|No|Dr|Mr|Mrs|Ms|Prof|St|approx|resp|Ref|Refs|pp|Vol|Ch|Inc|Ltd|Univ)\.$/i;
  var CLOSERS = '.!?\u2026"\u2019\u201d\')]';
  function trimRange(txt, s, e){
    while (s < e && /\s/.test(txt.charAt(s))) s++;
    while (e > s && /\s/.test(txt.charAt(e - 1))) e--;
    return e > s ? {s: s, e: e} : null;
  }
  // 짝이 맞는 괄호 안쪽만 표시한다. 짝 없이 열린 괄호 하나가 남은 문단(추출이 흔히 그렇다)에서
  // 그 뒤 전체가 "괄호 안"이 돼 한 문장으로 뭉치던 것을 막는다.
  function bracketMask(txt, s0, e0){
    var n = e0 - s0, delta = [], stack = [], mask = [], run = 0, i;
    for (i = 0; i <= n; i++) delta[i] = 0;
    for (i = s0; i < e0; i++){
      var c = txt.charAt(i);
      if (c === '(' || c === '[' || c === '{') stack.push(i);
      else if (c === ')' || c === ']' || c === '}'){
        if (stack.length){ var o = stack.pop(); delta[o - s0]++; delta[i - s0 + 1]--; }
      }
    }
    for (i = 0; i < n; i++){ run += delta[i]; mask[i] = run > 0; }
    return mask;
  }
  function sentences(txt, s0, e0){
    var out = [], start = s0, i, r, inBracket = bracketMask(txt, s0, e0);
    for (i = s0; i < e0; i++){
      // 일본어·중국어 문장 끝(。！？)은 뒤에 공백 없이 다음 문장이 붙는다 — 약어·소수점 걱정도 없다
      var cjk = '\u3002\uff01\uff1f'.indexOf(txt.charAt(i)) >= 0;
      if (inBracket[i - s0] || ('.!?\u2026'.indexOf(txt.charAt(i)) < 0 && !cjk)) continue;
      var j = i + 1;
      while (j < e0 && (CLOSERS + '\u300d\u300f\uff09').indexOf(txt.charAt(j)) >= 0) j++;   // 닫는 따옴표·괄호까지 문장에 포함
      if (!cjk && j < e0 && !/\s/.test(txt.charAt(j))) continue;  // 0.5 · 3.1 처럼 붙어 있으면 문장 끝이 아님
      var head = txt.slice(start, i + 1);
      if (!cjk && (ABBR.test(head) || /(^|[\s(\[])[A-Z]\.$/.test(head))) continue;
      var k = j; while (k < e0 && /\s/.test(txt.charAt(k))) k++;
      if (!cjk && k < e0 && /[a-z,;]/.test(txt.charAt(k))) continue;  // 소문자로 이어지면 아직 한 문장
      r = trimRange(txt, start, j); if (r) out.push(r);
      start = k; i = k - 1;
    }
    r = trimRange(txt, start, e0); if (r) out.push(r);
    return out;
  }
  function sentsOf(scope){
    var txt = scope.textContent, bs = blockRanges(scope), out = [], i;
    for (i = 0; i < bs.length; i++) out = out.concat(sentences(txt, bs[i].s, bs[i].e));
    return out;
  }

  // 문장 수가 같으면 순서대로 1:1 (번역이 문장을 지킨 흔한 경우 — 추측이 아니다).
  // 다르면 길이비 DP로 1:2·2:1·누락까지 허용해 맞춘다. 어림짐작이므로 절반 넘게 짝을 못 지으면
  // 아예 포기하고 문단 표시로 물러난다 — 틀린 짝을 보여 주느니 안 보여 주는 게 낫다.
  var MOVES = [[1, 1, 0], [1, 2, 1.4], [2, 1, 1.4], [1, 0, 4], [0, 1, 4]];
  function pairUp(E, K){
    var m = E.length, n = K.length, i, j, a, out = [];
    if (!m || !n) return null;
    if (m === n){ for (i = 0; i < m; i++) out.push([[i], [i]]); return out; }
    var eL = [], kL = [], totE = 0, totK = 0;
    for (i = 0; i < m; i++){ eL.push(E[i].e - E[i].s); totE += eL[i]; }
    for (j = 0; j < n; j++){ kL.push(K[j].e - K[j].s); totK += kL[j]; }
    var r = totK / Math.max(1, totE), INF = 1e18, d = [], bk = [];
    for (i = 0; i <= m; i++){
      d.push([]); bk.push([]);
      for (j = 0; j <= n; j++){ d[i].push(INF); bk[i].push(null); }
    }
    d[0][0] = 0;
    for (i = 0; i <= m; i++) for (j = 0; j <= n; j++){
      if (d[i][j] >= INF) continue;
      for (var t = 0; t < MOVES.length; t++){
        var di = MOVES[t][0], dj = MOVES[t][1];
        if (i + di > m || j + dj > n) continue;
        var x = 0, y = 0;
        for (a = 0; a < di; a++) x += eL[i + a];
        for (a = 0; a < dj; a++) y += kL[j + a];
        var c = d[i][j] + Math.abs(x * r - y) / Math.sqrt(Math.max(1, x * r + y)) + MOVES[t][2];
        if (c < d[i + di][j + dj]){ d[i + di][j + dj] = c; bk[i + di][j + dj] = [i, j, di, dj]; }
      }
    }
    if (d[m][n] >= INF) return null;
    var path = [], p = [m, n];
    while (p[0] || p[1]){
      var b = bk[p[0]][p[1]]; if (!b) return null;
      path.push(b); p = [b[0], b[1]];
    }
    path.reverse();
    var solo = 0;
    for (i = 0; i < path.length; i++){
      var es = [], ks = [];
      for (a = 0; a < path[i][2]; a++) es.push(path[i][0] + a);
      for (a = 0; a < path[i][3]; a++) ks.push(path[i][1] + a);
      if (!es.length || !ks.length) solo++;
      out.push([es, ks]);
    }
    return solo * 2 > out.length ? null : out;
  }

  var doc = document, body = doc.body;
  body.classList.add('js');
  var sheet = doc.getElementById('sheet');
  var shLabel = sheet.querySelector('.sh-label'), shText = sheet.querySelector('.sh-text'),
      shActs = sheet.querySelector('.sh-acts');
  var lib = doc.getElementById('lib'), topTitle = doc.getElementById('top-title');
  var cur = null;          // 지금 연 논문 article
  var tapped = null;       // 시트를 연 문장 짝 (같은 문장을 다시 누르면 닫는다)

  // ---- 저장 (이 브라우저에만 — 없거나 막혀 있어도 동작은 한다) ----
  function load(k, d){ try { var v = localStorage.getItem('md4m:' + k); return v === null ? d : v; } catch (e) { return d; } }
  function store(k, v){ try { localStorage.setItem('md4m:' + k, v); } catch (e) {} }

  // ---- 보기 모드 · 글자 크기 ----
  var mode = load('mode', 'ko');
  if (doc.getElementById('m-' + mode)) doc.getElementById('m-' + mode).checked = true;
  // 모드를 바꿔도 읽던 문단을 지킨다. change 때는 CSS가 이미 바뀌어 문단 위치가 달라져 있으므로
  // 라벨을 누르는 순간(바뀌기 전) 화면 맨 위 문단을 잡아 둔다.
  var modeAnchor = null;
  [].forEach.call(doc.querySelectorAll('.seg label'), function(l){
    l.addEventListener('pointerdown', function(){ modeAnchor = topRow(); });
  });
  [].forEach.call(doc.querySelectorAll('input.mode'), function(r){
    r.addEventListener('change', function(){
      var anchor = modeAnchor || topRow(); modeAnchor = null;
      clearSent(); closeSheet();
      store('mode', r.id.slice(2));
      if (anchor) anchor.scrollIntoView({block: 'start'});
    });
  });
  var fs = +load('fs', 17) || 17;
  function setFs(v){ fs = Math.max(13, Math.min(26, v)); doc.documentElement.style.setProperty('--fs', fs + 'px'); store('fs', fs); }
  setFs(fs);
  doc.getElementById('fs-dn').onclick = function(){ var a = topRow(); setFs(fs - 1); if (a) a.scrollIntoView({block: 'start'}); };
  doc.getElementById('fs-up').onclick = function(){ var a = topRow(); setFs(fs + 1); if (a) a.scrollIntoView({block: 'start'}); };

  // ---- 화면 맨 위 문단 ----
  function topRow(){
    if (!cur) return null;
    var rows = cur.querySelectorAll('.row'), y = 70, i;
    for (i = 0; i < rows.length; i++){
      var r = rows[i].getBoundingClientRect();
      if (r.height && r.bottom > y) return rows[i];
    }
    return null;
  }
  // ---- 아래 시트 ----
  // 쓰는 쪽이 시트가 닫히기 직전에 할 일을 둘 수 있다(폰 리더: 쓰던 메모 저장 — beforeSheetClose)
  function closeSheet(){ if (typeof beforeSheetClose === 'function') beforeSheetClose(); sheet.classList.remove('open'); tapped = null; }
  function openSheet(label, nodes, acts){
    shLabel.textContent = label; shText.innerHTML = ''; shActs.innerHTML = '';
    nodes.forEach(function(n){ shText.appendChild(n); });
    acts.forEach(function(a){ shActs.appendChild(a); });
    var close = doc.createElement('button'); close.textContent = md4T('닫기'); close.onclick = function(){ clearSent(); closeSheet(); };
    shActs.appendChild(close);
    sheet.scrollTop = 0; sheet.classList.add('open');
  }
  function para(text, cls){ var p = doc.createElement('div'); if (cls) p.className = cls; p.textContent = text; return p; }
  function button(text, fn){ var b = doc.createElement('button'); b.textContent = text; b.onclick = fn; return b; }

  // ---- 문장 짝 (누른 행만 그때 맞춘다 — 한 편에 행이 수백 개라 미리 다 하면 무겁다) ----
  function prepare(row){
    if (row.__pair !== undefined) return row.__pair;
    var en = row.querySelector('.cell.en'), ko = row.querySelector('.cell.ko');
    row.__pair = null;
    if (!en || !ko) return null;
    var E = sentsOf(en), K = sentsOf(ko), pairs = pairUp(E, K);
    if (!pairs) return null;
    var segE = [], segK = [], i, z;
    for (i = 0; i < pairs.length; i++){
      for (z = 0; z < pairs[i][0].length; z++) segE.push({s: E[pairs[i][0][z]].s, e: E[pairs[i][0][z]].e, pi: i});
      for (z = 0; z < pairs[i][1].length; z++) segK.push({s: K[pairs[i][1][z]].s, e: K[pairs[i][1][z]].e, pi: i});
    }
    var txtE = en.textContent, txtK = ko.textContent;   // 감싸도 textContent는 그대로다
    function make(seg){ var sp = doc.createElement('span'); sp.className = 'sent'; sp.setAttribute('data-pi', seg.pi); return sp; }
    wrapRanges(en, segE, make); wrapRanges(ko, segK, make);
    row.__pair = {pairs: pairs, en: E, ko: K, txt: {en: txtE, ko: txtK}};
    return row.__pair;
  }
  function clearSent(){
    [].forEach.call(doc.querySelectorAll('.sent.on'), function(s){ s.classList.remove('on'); });
  }
  function sentText(pr, side, pi){
    var idx = pr.pairs[pi][side === 'en' ? 0 : 1], src = pr[side], out = [];
    for (var i = 0; i < idx.length; i++) out.push(pr.txt[side].slice(src[idx[i]].s, src[idx[i]].e));
    return out.join(' ');
  }
  function bothVisible(row){
    return doc.getElementById('m-both').checked || row.classList.contains('peek');
  }
  function peekButton(row){
    return button(row.classList.contains('peek') ? md4T('문단 대조 닫기') : md4T('이 문단 대조해서 보기'), function(){
      row.classList.toggle('peek'); clearSent(); closeSheet();
    });
  }
  function onSentence(e, cell){
    var row = cell.parentElement, side = cell.classList.contains('ko') ? 'ko' : 'en';
    if (!row.querySelector('.cell.en') || !row.querySelector('.cell.ko')){ clearSent(); closeSheet(); return; }  // 짝이 될 쪽이 없는 행
    var other = side === 'ko' ? 'en' : 'ko', label = other === 'en' ? md4T('원문') : md4T('번역');
    var pr = prepare(row);
    if (!pr){
      if (bothVisible(row)) return;
      openSheet(label, [para(md4T('이 문단은 문장 짝을 맞추지 못했어요. 문단째로 대조해 보세요.'), 'more')], [peekButton(row)]);
      return;
    }
    var pi = sentAt(e, cell, pr, side);
    if (pi === null){ clearSent(); closeSheet(); return; }
    var key = row.id + ':' + pi;
    clearSent();
    if (tapped === key){ closeSheet(); return; }
    [].forEach.call(row.querySelectorAll('.sent[data-pi="' + pi + '"]'), function(s){ s.classList.add('on'); });
    // 쓰는 쪽이 문장에 붙일 동작(폰 리더의 하이라이트·메모)을 줄 수 있다 — {nodes, buttons}
    var acts = typeof sentenceActs === 'function' ? sentenceActs(row, +pi, pr, side) : null;
    if (bothVisible(row) && !acts){ tapped = null; closeSheet(); return; }   // 양쪽이 다 보이면 짝 표시로 충분
    var nodes = [];
    if (!bothVisible(row)){
      var txt = sentText(pr, other, +pi);
      nodes.push(para(txt || md4T('(짝이 되는 문장이 없어요 — 번역에서 합쳐지거나 빠진 문장)'), txt ? '' : 'more'));
    }
    openSheet(bothVisible(row) ? '' : label, nodes.concat(acts ? acts.nodes : []), acts ? acts.buttons : []);
    tapped = key;
  }
  function sentAt(e, cell, pr, side){
    // 방금 감쌌으면 e.target은 감싸기 전 노드다 — 손가락 아래를 다시 본다
    var under = doc.elementFromPoint(e.clientX, e.clientY);
    var sp = under && under.closest ? under.closest('.sent') : null;
    if (sp && cell.contains(sp)) return sp.getAttribute('data-pi');
    // 못 찾으면 글자 위치로 — 누른 자리의 오프셋을 품은 문장
    var r = doc.caretRangeFromPoint ? doc.caretRangeFromPoint(e.clientX, e.clientY) : null;
    if (!r || !cell.contains(r.startContainer)) return null;
    var pre = doc.createRange(); pre.selectNodeContents(cell);
    try { pre.setEnd(r.startContainer, r.startOffset); } catch (err) { return null; }
    var off = pre.toString().length, list = pr[side], i, k;
    for (i = 0; i < pr.pairs.length; i++){
      var idx = pr.pairs[i][side === 'en' ? 0 : 1];
      for (k = 0; k < idx.length; k++) if (list[idx[k]].s <= off && off < list[idx[k]].e) return String(i);
    }
    return null;
  }

  // ---- 인용 · 각주 ----
  function visibleTarget(id){
    // 같은 앵커가 원문(-en-)·번역(-ko-) 셀에 하나씩 있다 — 지금 보이는 쪽을 고른다
    var m = /^(p\d+)-(?:en|ko)-(.*)$/.exec(id), cands = [id];
    if (m) cands.push(m[1] + '-ko-' + m[2], m[1] + '-en-' + m[2]);
    for (var i = 0; i < cands.length; i++){
      var el = doc.getElementById(cands[i]);
      if (el && el.getClientRects().length) return el;
    }
    return doc.getElementById(id);
  }
  function blockText(el){
    var b = el && el.closest ? el.closest('p, li, td, div.cell') : null;
    return b ? b.textContent.replace(/\s+/g, ' ').trim() : '';
  }
  function onLink(a, e){
    var href = a.getAttribute('href') || '';
    if (href.charAt(0) !== '#') return;               // 바깥 링크는 그대로 (새 창)
    var id = href.slice(1), m = /^(p\d+)-(?:en|ko)-(ref|fn)-(.+)$/.exec(id);
    e.preventDefault();
    if (m){
      var info = (DATA[m[1]] || {})[m[2] === 'ref' ? 'cites' : 'fns'] || {}, tip = info[m[3]];
      var text = (m[2] === 'ref' ? (tip && tip.text) : tip) || blockText(visibleTarget(id));
      var acts = [];
      if (m[2] === 'ref' && tip && tip.url){
        var go = doc.createElement('a'); go.href = tip.url; go.target = '_blank'; go.rel = 'noopener';
        go.textContent = md4T('논문 링크 열기'); acts.push(go);
      }
      acts.push(button(m[2] === 'ref' ? md4T('참고문헌 목록에서 보기') : md4T('각주로 이동'), function(){
        closeSheet(); var t = visibleTarget(id); if (t) t.scrollIntoView({block: 'start'});
      }));
      openSheet((m[2] === 'ref' ? md4T('참고문헌 {n}', {n: m[3]}) : md4T('각주 {n}', {n: m[3]})), [para(text || md4T('(서지 정보를 찾지 못했어요)'))], acts);
      return;
    }
    if (id === 'lib'){ showLib(true); return; }
    var pm = /^(p\d+)$/.exec(id);
    if (pm){ openPaper(pm[1], true); return; }
    var t = visibleTarget(id);
    if (t) t.scrollIntoView({block: 'start'});
  }

  doc.addEventListener('click', function(e){
    // 시트 단추가 시트 내용을 갈아 끼우면(예: '메모 달기' → 입력칸) 눌린 단추는 문서에서 떨어져 나간 뒤 여기 닿는다 —
    // 그걸 '시트 바깥을 눌렀다'로 보면 방금 연 시트를 곧바로 닫아 버린다
    if (!e.target.isConnected) return;
    var a = e.target.closest ? e.target.closest('a[href]') : null;
    if (a){ if (!sheet.contains(a)) onLink(a, e); return; }
    if (sheet.contains(e.target) || e.target.closest('.top')) return;
    var sel = window.getSelection && window.getSelection();
    if (sel && !sel.isCollapsed) return;               // 길게 눌러 고르는 중이면 건드리지 않는다
    var cell = e.target.closest('.row .cell.ko, .row .cell.en');
    if (!cell || e.target.closest('img, table, math, pre')){ clearSent(); closeSheet(); return; }
    onSentence(e, cell);
  });
  doc.addEventListener('keydown', function(e){ if (e.key === 'Escape'){ clearSent(); closeSheet(); } });


  var CFG = window.NARANI_CFG || {};
  var PID = 'p0', DC = 'narani-data';
  var main = doc.querySelector('main'), libList = doc.getElementById('lib-list'),
      libBar = doc.getElementById('lib-bar'), loading = doc.getElementById('loading');
  var DATA = {}, INDEX = null, loadSeq = 0, routed = false;
  var APP = CFG.app_name || 'Narani';

  // ---- 화면 문구 ----
  function setText(id, s){ var el = doc.getElementById(id); if (el) el.textContent = s; }
  function labels(){
    doc.documentElement.lang = md4Lang;
    langLabel();
    setText('fs-dn', md4T('가') + '−'); setText('fs-up', md4T('가') + '+'); setText('fs-btn', md4T('가'));
    doc.getElementById('fs-btn').setAttribute('aria-label', md4T('글자 크기'));
    doc.getElementById('top-title').setAttribute('aria-label', md4T('목차'));
    doc.getElementById('fab').setAttribute('aria-label', md4T('이 논문에 대해 묻기'));
    setText('lnk-about', md4T('Narani 소개')); setText('lnk-privacy', md4T('개인정보처리방침'));
    doc.getElementById('fs-dn').setAttribute('aria-label', md4T('글자 작게'));
    doc.getElementById('fs-up').setAttribute('aria-label', md4T('글자 크게'));
    doc.getElementById('back').setAttribute('aria-label', md4T('논문 목록'));
    audioLabels();
  }
  function setUiLang(l){
    var cats = CFG.catalogs || {};
    if (!l || l === md4Lang || !(l in cats || l === CFG.source_lang)) return;
    try { localStorage.setItem('narani:ui_lang', l); } catch (e) {}
    window.md4Lang = l; labels();
  }

  // ---- 저장소 ----
  function localSource(){
    return {raw: function(name){
      return fetch('data/' + name, {cache: 'no-store'}).then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r; });
    }, get: function(name){
      return fetch('data/' + name, {cache: 'no-store'}).then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
    }, put: function(name, obj){   // 미리보기 서버는 mobile/ 아래만 받아 준다
      return fetch('data/' + name, {method: 'PUT', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify(obj)}).then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status);
      });
    }};
  }
  // Google Drive — 데스크톱이 만든 `Narani` 폴더(같은 Cloud 프로젝트라 drive.file로 보인다)를 읽는다.
  // 로그인은 팝업 대신 **리디렉트**(OAuth 클라이언트 측 흐름) — iOS 홈 화면 앱에선 팝업이 막히거나
  // 다른 창으로 열린다. 돌아온 주소의 #access_token을 읽고 원래 화면(#!…)으로 되돌린다.
  // access token은 한 시간짜리다. 끊기면 읽던 것(캐시)은 그대로 열리고, 새로 받을 때만 다시 연결한다.
  var GSCOPE = 'https://www.googleapis.com/auth/drive.file', GAPI = 'https://www.googleapis.com/drive/v3',
      GUP = 'https://www.googleapis.com/upload/drive/v3';
  var authError = '';
  function qs(o){ return Object.keys(o).map(function(k){ return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); }
  function takeRedirect(){
    var h = location.hash.replace(/^#/, '');
    if (!/(^|&)(access_token|error)=/.test(h)) return;
    var p = {}, saved = null;
    h.split('&').forEach(function(kv){ var i = kv.indexOf('='); if (i > 0) p[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); });
    try { saved = JSON.parse(sessionStorage.getItem('narani:gstate') || 'null'); sessionStorage.removeItem('narani:gstate'); } catch (e) {}
    history.replaceState(null, '', location.pathname + location.search + ((saved && saved.back) || ''));
    if (!saved || saved.s !== p.state) return;
    if (p.access_token){
      try {
        localStorage.setItem('narani:gtok', JSON.stringify({at: p.access_token, exp: Date.now() + (+p.expires_in || 3600) * 1000}));
        localStorage.setItem('narani:gconn', '1');
      } catch (e) {}
    } else authError = p.error || 'error';
  }
  function gToken(){
    try { var t = JSON.parse(localStorage.getItem('narani:gtok') || 'null'); return t && t.exp > Date.now() + 60000 ? t.at : null; }
    catch (e) { return null; }
  }
  function gSignIn(){
    var st = Math.random().toString(36).slice(2) + Date.now().toString(36);
    try { sessionStorage.setItem('narani:gstate', JSON.stringify({s: st, back: location.hash})); } catch (e) {}
    var o = {client_id: CFG.google_client_id, redirect_uri: location.origin + location.pathname.replace(/index\.html$/, ''), response_type: 'token',
             scope: GSCOPE, include_granted_scopes: 'true', state: st};
    location.href = 'https://accounts.google.com/o/oauth2/v2/auth?' + qs(o);
  }
  function driveSource(){
    var folder = null, ids = null;
    function fail(kind){ var e = new Error(kind); e.kind = kind; return e; }
    function api(url, opt){
      var t = gToken();
      if (!t) return Promise.reject(fail('auth'));
      opt = opt || {};
      var h = opt.headers || {}; h.Authorization = 'Bearer ' + t; opt.headers = h;
      return fetch(url, opt).then(function(r){
        if (r.status === 401){ try { localStorage.removeItem('narani:gtok'); } catch (e) {} throw fail('auth'); }
        if (!r.ok) throw fail('http ' + r.status);
        return r;
      });
    }
    function list(q){
      var out = [];
      function page(tok){
        var u = GAPI + '/files?' + qs({q: q, fields: 'nextPageToken,files(id,name,modifiedTime)', pageSize: 1000,
                                       spaces: 'drive', orderBy: 'modifiedTime desc'}) + (tok ? '&pageToken=' + encodeURIComponent(tok) : '');
        return api(u).then(function(r){ return r.json(); }).then(function(d){
          out = out.concat(d.files || []);
          return d.nextPageToken ? page(d.nextPageToken) : out;
        });
      }
      return page(null);
    }
    function findFolder(){
      if (folder) return Promise.resolve(folder);
      return list("mimeType = 'application/vnd.google-apps.folder' and trashed = false and appProperties has { key='narani' and value='store' }")
        .then(function(fs){
          if (!fs.length) throw fail('nofolder');
          folder = fs[0].id; return folder;
        });
    }
    function loadIds(){
      return findFolder().then(function(f){ return list("'" + f + "' in parents and trashed = false"); }).then(function(fs){
        ids = {}; fs.forEach(function(x){ if (!(x.name in ids)) ids[x.name] = x.id; }); return ids;
      });
    }
    return {
      drive: true,
      raw: function(name){
        var p = ids && ids[name] ? Promise.resolve(ids) : loadIds();
        return p.then(function(m){
          if (!m[name]) throw fail('missing');
          return api(GAPI + '/files/' + m[name] + '?alt=media');
        });
      },
      get: function(name){
        // 목록을 받을 때마다 이름표를 새로 받는다 — 데스크톱이 그사이 새 번들을 올렸을 수 있다
        var p = (name === 'index.json' || !ids) ? loadIds() : Promise.resolve(ids);
        return p.then(function(m){
          if (!m[name]) throw fail(name === 'index.json' ? 'nofolder' : 'missing');
          return api(GAPI + '/files/' + m[name] + '?alt=media').then(function(r){ return r.json(); });
        });
      },
      // 폰이 쓰는 파일(mobile/…)만 — 있으면 내용만 바꾸고, 없으면 앱 폴더에 새로 만든다
      put: function(name, obj){
        var body = JSON.stringify(obj);
        return (ids ? Promise.resolve(ids) : loadIds()).then(function(m){
          if (m[name]) return api(GUP + '/files/' + m[name] + '?uploadType=media',
                                  {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: body});
          var b = 'narani' + Math.random().toString(36).slice(2);
          var meta = JSON.stringify({name: name, parents: [folder], mimeType: 'application/json'});
          return api(GUP + '/files?uploadType=multipart&fields=id', {method: 'POST',
            headers: {'Content-Type': 'multipart/related; boundary=' + b},
            body: '--' + b + '\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n' + meta +
                  '\r\n--' + b + '\r\nContent-Type: application/json\r\n\r\n' + body + '\r\n--' + b + '--'})
            .then(function(r){ return r.json(); }).then(function(d){ ids[name] = d.id; });
        });
      }
    };
  }
  takeRedirect();
  var source = CFG.local ? localSource() : (CFG.google_client_id ? driveSource() : null);
  function sourceProblem(err){
    // 저장소 오류 → 목록 위에 보일 안내와 단추
    var kind = err && err.kind;
    // 꺼진 망에서는 다시 연결도 못 한다 — 승인이 만료됐어도 오프라인 안내만
    if (navigator.onLine === false) return {text: md4T('오프라인 — 받아 둔 논문만 열려요')};
    if (source && source.drive && (kind === 'auth' || authError)){
      var first = true;
      try { first = !localStorage.getItem('narani:gconn'); } catch (e) {}
      return {text: first ? md4T('Google Drive에 연결해 데스크톱에서 보낸 논문을 받아요.') : md4T('Google Drive 연결이 만료됐어요.'),
              button: first ? md4T('Google Drive 연결') : md4T('다시 연결'), act: function(){ gSignIn(); }};
    }
    if (kind === 'nofolder') return {text: md4T('Drive에 Narani 폴더가 없어요. 데스크톱 앱에서 먼저 모바일로 보내 주세요.')};
    return {text: md4T('목록을 불러오지 못했어요. 인터넷 연결을 확인하세요.')};
  }

  // ---- 오프라인 캐시 (받은 목록·번들) ----
  function cacheGet(name){
    if (!window.caches) return Promise.resolve(null);
    return caches.open(DC).then(function(c){ return c.match('__data/' + name); })
      .then(function(r){ return r ? r.json() : null; }).catch(function(){ return null; });
  }
  function cachePut(name, obj){
    if (!window.caches) return Promise.resolve();
    return caches.open(DC).then(function(c){
      return c.put('__data/' + name, new Response(JSON.stringify(obj), {headers: {'Content-Type': 'application/json'}}));
    }).catch(function(){});
  }
  function haveMap(){ try { return JSON.parse(localStorage.getItem('narani:have') || '{}') || {}; } catch (e) { return {}; } }
  function setHave(file, hash){
    var m = haveMap(); if (hash) m[file] = hash; else delete m[file];
    try { localStorage.setItem('narani:have', JSON.stringify(m)); } catch (e) {}
  }
  function pruneCache(ix){
    // 목록에서 빠진 논문의 캐시는 지운다 (폰 저장 공간)
    var keep = {}, keys = {}, m = haveMap(), k;
    var keepA = {};
    ix.papers.forEach(function(e){
      keep[e.file] = 1; keep['notes/' + fileId(e) + '.json'] = 1; keys[e.key] = 1;
      if (audioOf(e)){ keep[e.audio.tl] = 1; keepA[audioKey(e.audio)] = 1; }
    });
    if (window.caches) caches.open(AC).then(function(c){
      return c.keys().then(function(reqs){
        reqs.forEach(function(q){ var u = new URL(q.url), k = decodeURIComponent(u.pathname + u.search).replace(/^.*?__audio\//, '__audio/'); if (!keepA[k]) c.delete(q); });
      });
    }).catch(function(){});
    for (k in m) if (!keep[k]) setHave(k, null);
    // 데스크톱이 모바일에서 뺀 논문 — 그 논문의 폰 기록도 지운다(다시 올리면 Drive에 주인 없는 파일이 남는다).
    // 데스크톱은 빼기 전에 올라와 있던 폰 기록을 합쳐 두었다.
    try {
      var ok = JSON.parse(localStorage.getItem('narani:opskeys') || '{}') || {}, changed = false;
      for (k in ok) if (!keys[k]){
        delete ok[k]; changed = true;
        localStorage.removeItem('narani:ops:' + k); localStorage.removeItem('narani:opsup:' + k);
      }
      if (changed) localStorage.setItem('narani:opskeys', JSON.stringify(ok));
    } catch (e) {}
    if (!window.caches) return;
    caches.open(DC).then(function(c){
      return c.keys().then(function(reqs){
        reqs.forEach(function(q){
          var name = decodeURIComponent(new URL(q.url).pathname).replace(/^.*?__data\//, '');
          if (name !== 'index.json' && !keep[name]) c.delete(q);
        });
      });
    }).catch(function(){});
  }
  function getPaper(e){
    return cacheGet(e.file).then(function(c){
      if (c && c.hash === e.hash) return c;
      if (!source){ if (c) return c; throw new Error('no source'); }
      return source.get(e.file).then(function(b){
        cachePut(e.file, b); setHave(e.file, b.hash); return b;
      }, function(err){ if (c) return c; throw err; });
    });
  }
  function getNotes(e){   // 데스크톱이 올린 하이라이트·메모 — 받아 둔 것과 지문(__h)이 같으면 캐시로
    if (!e.notes_hash) return Promise.resolve(null);
    var name = 'notes/' + fileId(e) + '.json';
    return cacheGet(name).then(function(c){
      if (c && c.__h === e.notes_hash) return c;
      if (!source) return c;
      return source.get(name).then(function(n){ n.__h = e.notes_hash; cachePut(name, n); return n; },
                                   function(){ return c; });
    });
  }

  // ---- 목록 ----
  function fileId(e){ return String(e.file || '').replace(/^papers\//, '').replace(/\.json$/, ''); }
  function entryOf(id){
    if (!INDEX) return null;
    for (var i = 0; i < INDEX.papers.length; i++) if (fileId(INDEX.papers[i]) === id) return INDEX.papers[i];
    return null;
  }
  function fmtDate(sec){
    var d = new Date(sec * 1000);
    try { return d.toLocaleString(md4Lang, {dateStyle: 'medium', timeStyle: 'short'}); } catch (e) { return d.toLocaleString(); }
  }
  function el(tag, cls, text){ var n = doc.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function renderLib(note){
    var have = haveMap(), n = 0;
    libList.innerHTML = '';
    doc.getElementById('lib-h').textContent = INDEX ? md4T('논문 {n}편', {n: INDEX.papers.length}) : APP;
    (INDEX ? INDEX.papers : []).forEach(function(e){
      var off = have[e.file] === e.hash; if (off) n++;
      var li = el('li', OFFLINE && !have[e.file] ? 'na' : ''), a = el('a');
      a.href = '#!' + fileId(e); a.setAttribute('data-open', fileId(e));
      var title = el('span', 'li-title', e.title_ko || e.title);
      if (off) title.appendChild(el('span', 'dl', '⤓'));
      a.appendChild(title);
      if (e.title_ko) a.appendChild(el('span', 'li-en', e.title));
      var meta = el('span', 'li-meta', [e.authors, e.year, e.venue].filter(function(x){ return x; }).join(' · '));
      meta.appendChild(el('span', 'badge', e.has_ko ? md4T('번역') : md4T('원문만')));
      if (li.className === 'na') meta.appendChild(el('span', 'na-t', md4T('받아 두지 않음')));
      if (audioOf(e)) meta.appendChild(el('span', 'au', '🎧 ' + fmtT(e.audio.duration)));
      a.appendChild(meta); li.appendChild(a); libList.appendChild(li);
    });
    if (INDEX && !INDEX.papers.length) libList.appendChild(el('li', 'empty', md4T('올린 논문이 없어요. 데스크톱 앱에서 모바일로 보내 주세요.')));
    libBar.innerHTML = '';
    var info = [];
    if (INDEX && INDEX.updated) info.push(md4T('{date}에 올린 목록', {date: fmtDate(INDEX.updated)}));
    if (SAVING) info.push(md4T('저장 중… {n}/{m}', {n: SAVING.done, m: SAVING.total}));
    else if (INDEX && INDEX.papers.length) info.push(md4T('오프라인 {n}/{m}편', {n: n, m: INDEX.papers.length}));
    if (OFFLINE && !note) note = md4T('오프라인 — 받아 둔 논문만 열려요');
    if (note) info.push(note);
    libBar.appendChild(el('span', 'grow', info.join(' · ')));
    if (source){
      var re = el('button', '', md4T('새로 고침')); re.onclick = function(){ loadIndex(true); };
      libBar.appendChild(re);
    }
    if (INDEX && INDEX.papers.length && source){
      // 오프라인 모드 — 켜 두면 모든 논문(과 데스크톱의 하이라이트·메모)을 받아 두고, 목록을 새로 받을 때마다
      // 새로 올라온·바뀐 것만 더 받는다. 끄면 더 받지 않을 뿐 받아 둔 것은 그대로.
      var lab = el('label', 'auto'), cb = el('input');
      cb.type = 'checkbox'; cb.checked = autoSave();
      cb.onchange = function(){
        try { localStorage.setItem('narani:auto', cb.checked ? '1' : '0'); } catch (e) {}
        renderLib();   // '음성도 받아 두기'가 바로 보이게
        if (cb.checked) saveOffline();
      };
      lab.appendChild(cb); lab.appendChild(doc.createTextNode(md4T('오프라인 모드 — 모두 받아 두기')));
      libBar.appendChild(lab);
      var withAudio = INDEX.papers.filter(function(e){ return audioOf(e); });
      if (withAudio.length && cb.checked){   // 음성은 편당 십수 MB라 따로 고른다
        var mb = withAudio.reduce(function(n, e){ return n + (e.audio.bytes || 0); }, 0) / 1e6;
        var la = el('label', 'auto'), ca = el('input');
        ca.type = 'checkbox'; ca.checked = autoAudio();
        ca.onchange = function(){
          try { localStorage.setItem('narani:autoaudio', ca.checked ? '1' : '0'); } catch (e) {}
          if (ca.checked) saveOffline();
        };
        la.appendChild(ca); la.appendChild(doc.createTextNode(md4T('음성도 받아 두기 ({n}편 · {mb}MB — 와이파이에서)', {n: withAudio.length, mb: Math.round(mb)})));
        libBar.appendChild(la);
      }
    }
  }
  function autoAudio(){ try { return localStorage.getItem('narani:autoaudio') === '1'; } catch (e) { return false; } }
  function autoSave(){ try { return localStorage.getItem('narani:auto') === '1'; } catch (e) { return false; } }
  var SAVING = null, OFFLINE = false;
  function saveOffline(){
    if (!INDEX || !source || SAVING || OFFLINE) return;
    var have = haveMap(), todo = INDEX.papers.filter(function(e){ return have[e.file] !== e.hash; }), failed = 0;
    var all = INDEX.papers.slice();
    try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (e) {}
    SAVING = {done: 0, total: todo.length};
    if (todo.length) renderLib();
    (function next(){
      if (!todo.length){   // 논문을 다 받았으면 하이라이트·메모 파일 — 바뀐 것만 받는다
        Promise.all(all.map(function(e){ return getNotes(e).catch(function(){}); })).then(function(){
          return autoAudio() ? saveAudio(all) : 0;
        }).then(function(n){
          var had = SAVING.total || n; SAVING = null;
          if (had) renderLib(failed ? md4T('{n}편은 받지 못했어요', {n: failed}) : '');
        });
        return;
      }
      var e = todo.shift();
      getPaper(e).catch(function(){ failed++; }).then(function(){
        SAVING.done++; if (lib && !lib.classList.contains('off')) renderLib(); next();
      });
    })();
  }
  function saveAudio(list){   // 받아 두지 않은 음성과 시간표 — 한 편씩
    var todo = list.filter(function(e){ return audioOf(e); }), got = 0;
    return (function next(){
      if (!todo.length) return Promise.resolve(got);
      var e = todo.shift(), a = audioOf(e);
      return audioCached(a).then(function(yes){
        if (yes) return;
        SAVING.total++; if (lib && !lib.classList.contains('off')) renderLib();
        return audioTl(a).then(function(){ return audioBlob(a); }).then(function(){ got++; }, function(){});
      }).then(function(){ SAVING.done++; return next(); });
    })();
  }
  function addAct(pr){
    if (!pr || !pr.button) return;
    var b = el('button', '', pr.button); b.onclick = pr.act; libBar.appendChild(b);
  }
  function showIndex(ix, note, pr){
    INDEX = ix; renderLib(note); addAct(pr);
    if (!routed){ routed = true; route(); }
  }
  function loadIndex(force){
    if (!force) libBar.textContent = md4T('불러오는 중…');
    return cacheGet('index.json').then(function(cached){
      if (cached && !force) showIndex(cached);
      if (!source){
        if (!cached) libBar.textContent = md4T('연결된 저장소가 없어요.');
        return;
      }
      return source.get('index.json').then(function(ix){
        OFFLINE = false;
        cachePut('index.json', ix); setUiLang(ix.ui_lang); pruneCache(ix); showIndex(ix); flushPending();
        if (autoSave()) saveOffline();
      }, function(err){
        var pr = sourceProblem(err);
        OFFLINE = navigator.onLine === false || (!pr.button && !!err && !err.kind);   // 망 오류(인증·폴더 문제가 아니라)
        if (cached) showIndex(cached, OFFLINE ? '' : pr.text, pr);
        else { libBar.innerHTML = ''; libBar.appendChild(el('span', 'grow', pr.text)); addAct(pr); }
      });
    });
  }

  // ---- 목록 ↔ 논문 ----
  function unmount(){ if (cur){ cur.parentNode.removeChild(cur); cur = null; } }
  function showLib(push){
    loadSeq++; unmount(); loading.style.display = 'none'; readerClosed();
    lib.classList.remove('off'); topTitle.textContent = APP;
    closeSheet(); window.scrollTo(0, 0);
    if (push) history.pushState(null, '', '#!lib');
  }
  function mount(b, target){
    main.insertAdjacentHTML('beforeend', b.html);
    cur = doc.getElementById(PID); cur.classList.add('on');
    DATA[PID] = {key: b.key, title: b.title_ko || b.title, cites: b.cites || {}, fns: b.fns || {}};
    readerOpened(b);
    var t = target ? doc.getElementById(target) : null, off = 0;
    if (!t){
      var saved = load('pos:' + b.key, '').split(':');
      t = saved[0] ? doc.getElementById(PID + '-' + saved[0]) : null; off = +saved[1] || 0;
    }
    if (t){ t.scrollIntoView({block: 'start'}); if (off) window.scrollBy(0, off); } else window.scrollTo(0, 0);
  }
  function openPaper(id, push, target){
    if (id === PID){ if (cur) window.scrollTo(0, 0); return !!cur; }
    var e = entryOf(id);
    if (!e) return false;
    var seq = ++loadSeq;
    unmount(); closeSheet(); readerClosed();
    lib.classList.add('off'); topTitle.textContent = e.title_ko || e.title;
    loading.style.display = ''; loading.textContent = md4T('불러오는 중…');
    window.scrollTo(0, 0);
    if (push) history.pushState(null, '', '#!' + id);
    getPaper(e).then(function(b){
      if (seq !== loadSeq) return;
      loading.style.display = 'none'; mount(b, target);
      notesOpen(e, b);
      audioOpened(e);
      if (INDEX) renderLib();
    }, function(err){
      if (seq !== loadSeq) return;
      if (err && err.kind === 'auth'){
        loading.textContent = md4T('Google Drive 연결이 만료됐어요.') + ' ';
        var b = el('button', '', md4T('다시 연결')); b.onclick = function(){ gSignIn(); };
        loading.appendChild(b);
      } else loading.textContent = md4T('이 논문을 받지 못했어요. 인터넷에 연결된 뒤 다시 열어 보세요.');
    });
    return true;
  }
  try { history.scrollRestoration = 'manual'; } catch (e) {}
  function route(){
    var h = location.hash.replace(/^#!?/, '');
    if (h && h !== 'lib' && openPaper(h, false)) return;
    showLib(false);
  }
  doc.getElementById('back').onclick = function(){ showLib(true); };
  window.addEventListener('popstate', route);
  libList.addEventListener('click', function(e){
    var a = e.target.closest ? e.target.closest('a[data-open]') : null;
    if (!a) return;
    e.preventDefault(); e.stopPropagation();
    if (a.parentNode.classList.contains('na')) return;
    openPaper(a.getAttribute('data-open'), true);
  });
  var saveTimer = null;
  window.addEventListener('scroll', function(){
    if (!cur || saveTimer) return;
    saveTimer = setTimeout(function(){
      saveTimer = null;
      var r = topRow(); if (!r || !cur) return;
      var into = Math.max(0, Math.round(64 - r.getBoundingClientRect().top));
      store('pos:' + cur.getAttribute('data-key'), r.id.replace(/^p\d+-/, '') + ':' + into);
    }, 400);
  }, {passive: true});

  // 망이 끊기면 목록에 바로 표시하고, 다시 붙으면 목록을 새로 받는다(적어 둔 기록도 이때 올라간다)
  window.addEventListener('offline', function(){ OFFLINE = true; if (INDEX) renderLib(); });
  window.addEventListener('online', function(){ loadIndex(true); });

  labels(); topTitle.textContent = APP;
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(function(){});
  loadIndex(false);

  var COLORS = ['yellow', 'green', 'blue', 'pink', 'purple'], ACTX = 40;
  var NOTE = {key: null, fid: null, base: [], applied: 0, lost: []};
  function lsGet(k, d){ try { var v = JSON.parse(localStorage.getItem(k) || 'null'); return v == null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function ops(key){ return lsGet('narani:ops:' + key, []); }
  function upTs(key){ return lsGet('narani:opsup:' + key, 0); }
  function lastTs(list){ return list.length ? list[list.length - 1].ts : 0; }
  function pendingCount(key){ var t = upTs(key); return ops(key).filter(function(o){ return o.ts > t; }).length; }

  function items(){   // 데스크톱 것 위에 아직 합쳐지지 않은 폰 기록을 얹는다
    var map = {}, order = [];
    NOTE.base.forEach(function(it){ map[it.id] = it; order.push(it.id); });
    ops(NOTE.key).forEach(function(o){
      if (o.ts <= NOTE.applied) return;
      if (o.op === 'del'){ delete map[o.id]; return; }
      if (!(o.item.id in map)) order.push(o.item.id);
      map[o.item.id] = o.item;
    });
    return order.filter(function(id){ return map[id]; }).map(function(id){ return map[id]; });
  }

  // ---- 위치 찾기 (데스크톱 뷰어의 resolve와 같은 순서: 제자리 → 앞뒤 문맥째 → 가장 가까운 같은 문구) ----
  function resolve(txt, a){
    var q = a.quote || '';
    if (!q) return null;
    if (txt.substr(a.start, q.length) === q) return [a.start, a.start + q.length];
    var probe = (a.prefix || '') + q + (a.suffix || ''), i = txt.indexOf(probe);
    if (i >= 0) return [i + (a.prefix || '').length, i + (a.prefix || '').length + q.length];
    var best = -1, bd = Infinity, from = 0, j;
    while ((j = txt.indexOf(q, from)) >= 0){ var d = Math.abs(j - a.start); if (d < bd){ bd = d; best = j; } from = j + 1; }
    return best >= 0 ? [best, best + q.length] : null;
  }
  function cellAt(row, side){
    var r = doc.getElementById(PID + '-r' + row);
    return r ? (r.querySelector('.cell.' + side) || r.querySelector('.cell.same')) : null;
  }
  var TXT = null;   // 칠하는 동안만 쓰는 셀 평문 표 (셀마다 textContent를 거듭 세지 않게)
  function textOf(c){
    if (!TXT) return c.textContent;
    for (var i = 0; i < TXT.length; i++) if (TXT[i].c === c) return TXT[i].t;
    return c.textContent;
  }
  function locate(a){
    for (var d = 0; d <= 3; d++){        // 행이 조금 밀렸으면 이웃 행에서
      var rows = d ? [a.row - d, a.row + d] : [a.row];
      for (var k = 0; k < rows.length; k++){
        var c = cellAt(rows[k], a.side);
        if (!c) continue;
        var hit = resolve(textOf(c), a);
        if (hit) return {cell: c, s: hit[0], e: hit[1]};
      }
    }
    // 그래도 없으면 같은 쪽 셀 전체에서 문구로 — 다시 번역·조립해 행이 크게 밀렸거나, 원문·번역 절 수가
    // 달라 나란히 맞추지 못한 논문(번역 셀이 원문 뒤에 따로 붙는다). 원래 행에 가장 가까운 것.
    if (!cur || !a.quote) return null;
    var cells = cur.querySelectorAll('.cell.' + a.side + ', .cell.same'), best = null, bd = Infinity;
    for (var i = 0; i < cells.length; i++){
      var t = textOf(cells[i]), probe = (a.prefix || '') + a.quote + (a.suffix || ''), j = t.indexOf(probe), s;
      if (j >= 0) s = j + (a.prefix || '').length;
      else if ((j = t.indexOf(a.quote)) >= 0) s = j;
      else continue;
      var rn = +cells[i].parentElement.id.replace(/^p\d+-r/, ''), dd = Math.abs(rn - a.row) + (j === s ? 0 : 0.5);
      if (dd < bd){ bd = dd; best = {cell: cells[i], s: s, e: s + a.quote.length}; }
    }
    return best;
  }

  // ---- 칠하기 ----
  function unpaint(){
    if (!cur) return;
    var ms = cur.querySelectorAll('mark.anno'), parents = [];
    for (var i = 0; i < ms.length; i++){
      var m = ms[i], p = m.parentNode;
      while (m.firstChild) p.insertBefore(m.firstChild, m);
      p.removeChild(m);
      if (parents.indexOf(p) < 0) parents.push(p);
    }
    parents.forEach(function(p){ p.normalize(); });
  }
  function paint(){
    if (!cur || !NOTE.key) return;
    unpaint();
    TXT = [].map.call(cur.querySelectorAll('.cell'), function(c){ return {c: c, t: c.textContent}; });
    var list = items(), groups = [], lost = [];
    list.forEach(function(it){
      var placed = false;
      (it.anchors || []).forEach(function(a){
        var h = locate(a);
        if (!h) return;
        var g = null;
        for (var i = 0; i < groups.length; i++) if (groups[i].cell === h.cell){ g = groups[i]; break; }
        if (!g){ g = {cell: h.cell, segs: []}; groups.push(g); }
        // 겹치는 표시는 먼저 온 것만 칠한다 (겹쳐 감싸면 마크가 엉킨다)
        for (i = 0; i < g.segs.length; i++) if (g.segs[i].s < h.e && h.s < g.segs[i].e) return;
        g.segs.push({s: h.s, e: h.e, it: it});
        placed = true;
      });
      if (!placed) lost.push(it);
    });
    TXT = null;
    groups.forEach(function(g){
      wrapRanges(g.cell, g.segs, function(seg){
        var m = doc.createElement('mark');
        m.className = 'anno c-' + (COLORS.indexOf(seg.it.color) >= 0 ? seg.it.color : 'yellow');
        m.setAttribute('data-aid', seg.it.id);
        return m;
      });
    });
    list.forEach(function(it){    // 메모가 있으면 셀(원문·번역)마다 마지막 조각 뒤에 ✎ — 보기 모드에 따라 한쪽만 보이므로
      if (!it.note) return;
      var ms = cur.querySelectorAll('mark.anno[data-aid="' + it.id + '"]'), last = [];
      for (var i = 0; i < ms.length; i++){
        var c = ms[i].closest('.cell'), k;
        for (k = 0; k < last.length; k++) if (last[k].c === c) break;
        if (k < last.length) last[k].m = ms[i]; else last.push({c: c, m: ms[i]});
      }
      last.forEach(function(x){ x.m.classList.add('note-end'); });
    });
    NOTE.lost = lost.map(function(it){ return it.id; });
    notesChanged();
  }

  // ---- 내 메모 목록 — 떠 있는 단추의 '메모' 탭 (본문 순서로; 누르면 그 자리로) ----
  function rowOf(it){ var a = (it.anchors || [])[0]; return a ? +a.row || 0 : 0; }
  function notesChanged(){   // 표시가 바뀌면: 단추의 개수 · 열려 있는 메모 탭
    var n = NOTE.key ? items().length : 0, b = doc.getElementById('fab-n');
    b.textContent = n > 99 ? '99+' : String(n); b.classList.toggle('on', n > 0);
    if (agent.classList.contains('open') && AG.mode === 'notes') showNotes(); else setTabs();
  }
  function jumpTo(id){
    closeDrawers();
    var ms = cur ? cur.querySelectorAll('mark.anno[data-aid="' + id + '"]') : [], m = null;
    for (var i = 0; i < ms.length; i++) if (ms[i].offsetParent){ m = ms[i]; break; }   // 지금 보기(번역·원문)에 보이는 쪽
    m = m || ms[0];
    if (!m) return;
    m.scrollIntoView({block: 'center'});
    var r = m.closest('.row'); if (r){ r.classList.remove('flash'); void r.offsetWidth; r.classList.add('flash'); }
  }
  function showNotes(){
    AG.mode = 'notes'; setTabs(); agBody.innerHTML = '';
    doc.getElementById('ag-form').style.display = 'none';
    var list = items().slice().sort(function(a, b){ return rowOf(a) - rowOf(b); });
    var pend = pendingCount(NOTE.key), only = lsGet('narani:notesonly', false);
    var withNote = list.filter(function(it){ return it.note; }).length;
    var bar = el('div', 'nl-bar'), info = [md4T('하이라이트·메모 {n}개', {n: list.length})];
    if (pend) info.push(md4T('올릴 기록 {n}개', {n: pend}));
    bar.appendChild(el('span', 'grow', info.join(' · ')));
    if (pend && source && source.drive && !gToken() && navigator.onLine !== false)   // 연결이 끊겨 못 올린 기록
      bar.appendChild(button(md4T('다시 연결'), function(){ gSignIn(); }));
    if (withNote && withNote < list.length){
      var f = button(md4T('메모만'), function(){ lsSet('narani:notesonly', !only); showNotes(); });
      if (only) f.className = 'on';
      bar.appendChild(f);
    } else only = false;
    agBody.appendChild(bar);
    if (only) list = list.filter(function(it){ return it.note; });
    if (!list.length){
      agBody.appendChild(el('p', 'hint', source && source.put
        ? md4T('아직 표시한 문장이 없어요. 본문의 문장을 누르면 색을 칠하거나 메모를 달 수 있어요.')
        : md4T('저장소에 연결해야 하이라이트·메모를 남길 수 있어요.')));
      return;
    }
    var side = doc.getElementById('m-en').checked ? 'en' : 'ko', ol = el('ol', 'nl');
    list.forEach(function(it){
      var an = it.anchors || [], a = an.filter(function(x){ return x.side === side; })[0] || an[0] || {};
      var q = a.quote || '', li = el('li');
      li.appendChild(el('span', 'aq c-' + (COLORS.indexOf(it.color) >= 0 ? it.color : 'yellow'),
                        q.length > 160 ? q.slice(0, 160) + '…' : q));
      if (it.note) li.appendChild(el('span', 'an', it.note));
      if (NOTE.lost.indexOf(it.id) >= 0) li.appendChild(el('span', 'al', md4T('(본문에서 위치를 찾지 못했어요)')));
      else li.onclick = function(){ jumpTo(it.id); };
      ol.appendChild(li);
    });
    agBody.appendChild(ol);
  }

  // ---- 열기 · 받기 ----
  function notesOpen(e, b){
    NOTE = {key: b.key, fid: fileId(e), base: [], applied: 0, lost: []};
    paint();
    var key = b.key;
    getNotes(e).then(function(n){
      if (!n || NOTE.key !== key) return;
      NOTE.base = n.items || []; NOTE.applied = +n.applied || 0;
      var mine = ops(key), left = mine.filter(function(o){ return o.ts > NOTE.applied; });
      if (left.length !== mine.length) lsSet('narani:ops:' + key, left);   // 데스크톱이 합친 기록은 지운다
      paint();
    });
  }

  // ---- 기록 남기기 · 올리기 ----
  function record(op){
    var list = ops(NOTE.key), t = Date.now() / 1000;
    op.ts = Math.max(t, lastTs(list) + 0.001);
    list.push(op);
    lsSet('narani:ops:' + NOTE.key, list);
    var keys = lsGet('narani:opskeys', {}); keys[NOTE.key] = NOTE.fid; lsSet('narani:opskeys', keys);
    paint();
    upload(NOTE.key, NOTE.fid);
  }
  var uploading = {};
  function upload(key, fid){
    if (!source || !source.put || uploading[key]) return;
    var list = ops(key);
    if (!list.length || lastTs(list) <= upTs(key)) return;
    uploading[key] = true;
    var top = lastTs(list);
    source.put('mobile/' + fid + '.json', {format: 1, key: key, ops: list}).then(function(){
      lsSet('narani:opsup:' + key, top);
    }, function(){}).then(function(){
      uploading[key] = false;
      if (NOTE.key === key && cur) notesChanged();
      if (lastTs(ops(key)) > upTs(key) && lastTs(ops(key)) > top) upload(key, fid);   // 올리는 사이 새로 적은 것
    });
  }
  function flushPending(){   // 오프라인일 때 적어 둔 기록 — 목록을 새로 받으면(연결되면) 올린다
    var keys = lsGet('narani:opskeys', {});
    for (var k in keys) upload(k, keys[k]);
  }
  window.addEventListener('online', flushPending);

  // ---- 문장 시트의 동작 (CORE_JS의 onSentence가 부른다) ----
  function spanOf(list, idx){
    if (!idx || !idx.length) return null;
    var s = list[idx[0]].s, e = list[idx[0]].e;
    for (var i = 1; i < idx.length; i++){ s = Math.min(s, list[idx[i]].s); e = Math.max(e, list[idx[i]].e); }
    return [s, e];
  }
  function anchorOf(cell, row, side, sp){
    var txt = cell.textContent;
    return {side: side, row: row, start: sp[0], end: sp[1], quote: txt.slice(sp[0], sp[1]),
            prefix: txt.slice(Math.max(0, sp[0] - ACTX), sp[0]), suffix: txt.slice(sp[1], sp[1] + ACTX)};
  }
  function itemAt(row, side, sp){
    var list = items();
    for (var i = 0; i < list.length; i++){
      var an = list[i].anchors || [];
      for (var k = 0; k < an.length; k++){
        if (an[k].side !== side) continue;
        var h = locate(an[k]);
        if (h && h.cell === cellAt(row, side) && h.s < sp[1] && sp[0] < h.e) return list[i];
      }
    }
    return null;
  }
  function sentenceActs(rowEl, pi, pr, side){
    var acts = notesActs(rowEl, pi, pr, side), a = audioOf(AU.e);
    if (!a) return acts;
    var sp = spanOf(pr[side], pr.pairs[pi][side === 'en' ? 0 : 1]), row = +rowEl.id.replace(/^p\d+-r/, '');
    var aside = AU.tl ? AU.tl.side : (AU.e.has_ko ? 'ko' : 'en');   // 음성이 읽는 쪽 — 다른 쪽을 눌렀으면 그 행 처음부터
    var off = sp && side === aside ? sp[0] : null;
    acts = acts || {nodes: [], buttons: []};
    acts.buttons = [button(md4T('여기부터 듣기'), function(){ clearSent(); listenFrom(row, off); })].concat(acts.buttons);
    return acts;
  }
  function notesActs(rowEl, pi, pr, side){
    if (!source || !source.put || !NOTE.key) return null;
    var row = +rowEl.id.replace(/^p\d+-r/, '');
    var spans = {en: spanOf(pr.en, pr.pairs[pi][0]), ko: spanOf(pr.ko, pr.pairs[pi][1])};
    if (!spans[side]) return null;
    var it = itemAt(row, side, spans[side]);
    function fresh(color, note){
      var an = [];
      ['en', 'ko'].forEach(function(s){
        var c = rowEl.querySelector('.cell.' + s);
        if (c && spans[s]) an.push(anchorOf(c, row, s, spans[s]));
      });
      return {id: 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), color: color,
              note: note || '', anchors: an, created: Date.now() / 1000};
    }
    var nodes = [], dots = el('div', 'anno-dots');
    if (it && it.note) nodes.push(para(it.note, 'anno-note'));
    COLORS.forEach(function(c){
      var b = el('button', 'dot c-' + c + (it && it.color === c ? ' on' : ''));
      b.setAttribute('aria-label', c);
      b.onclick = function(){
        record({op: 'put', item: it ? Object.assign({}, it, {color: c}) : fresh(c)});
        clearSent(); closeSheet();
      };
      dots.appendChild(b);
    });
    nodes.push(dots);
    var buttons = [button(it && it.note ? md4T('메모 고치기') : md4T('메모 달기'), function(){ editNote(it, fresh); })];
    if (it) buttons.push(button(md4T('지우기'), function(){ record({op: 'del', id: it.id}); clearSent(); closeSheet(); }));
    return {nodes: nodes, buttons: buttons};
  }
  // 쓰는 중인 메모 — 시트가 어떻게 닫히든('저장'·'닫기'·바깥 누르기) 바뀐 글은 저장한다. 폰에서는 키보드가
  // 시트 아래쪽을 가려 '저장'을 못 누르고 바깥을 눌러 닫는 일이 흔하다 — 그때 쓴 글을 잃지 않게.
  var noteDraft = null;
  function beforeSheetClose(){
    var d = noteDraft; noteDraft = null;
    if (!d) return;
    var note = d.ta.value.replace(/\s+$/, '');
    if (note === d.orig) return;
    if (d.it) record({op: 'put', item: Object.assign({}, d.it, {note: note})});
    else if (note) record({op: 'put', item: d.fresh('yellow', note)});
  }
  function editNote(it, fresh){
    var ta = el('textarea', 'anno-ta');
    ta.value = (it && it.note) || ''; ta.rows = 4; ta.placeholder = md4T('메모를 적으세요');
    var save = button(md4T('저장'), function(){ clearSent(); closeSheet(); });   // 저장은 beforeSheetClose가
    openSheet(md4T('메모'), [ta], [save]);
    noteDraft = {it: it, fresh: fresh, ta: ta, orig: (it && it.note) || ''};
    setTimeout(function(){ ta.focus(); }, 50);
  }

  // ---- 글자 크기 팝오버 ----
  var fsPop = doc.getElementById('fs-pop');
  doc.getElementById('fs-btn').onclick = function(e){ e.stopPropagation(); fsPop.classList.toggle('open'); };
  doc.addEventListener('click', function(e){
    if (!e.target.closest || !e.target.closest('#fs-pop, #fs-btn')) fsPop.classList.remove('open');
  }, true);

  // ---- 서랍 공통 ----
  var scrim = doc.getElementById('scrim'), tocd = doc.getElementById('tocd'), agent = doc.getElementById('agent');
  function closeDrawers(){
    tocd.classList.remove('open'); agent.classList.remove('open'); scrim.classList.remove('open');
    try { doc.getElementById('ag-in').blur(); } catch (e) {}
  }
  scrim.onclick = closeDrawers;
  doc.getElementById('tocd-x').onclick = closeDrawers;
  doc.getElementById('ag-x').onclick = closeDrawers;
  [tocd, agent].forEach(function(d){ d.addEventListener('click', function(e){ e.stopPropagation(); }); });
  function readerOpened(b){ body.classList.add('reading'); AG.key = b.key; AG.lang = b.lang || 'ko'; AG.title = b.title_ko || b.title; AG.msgs = null; }
  function readerClosed(){ body.classList.remove('reading'); closeDrawers(); stopAudio(); }
  function flashRow(row){
    if (!row) return;
    row.scrollIntoView({block: 'start'});
    row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
  }

  // ---- 목차 서랍 — 논문 머리의 목차를 그대로, 지금 읽는 절을 표시 ----
  function currentSection(){
    var r = topRow(); if (!r || !cur) return null;
    var rows = [].slice.call(cur.querySelectorAll('.row')), idx = rows.indexOf(r), best = null;
    [].forEach.call(cur.querySelectorAll('.toc a'), function(a){
      var id = (a.getAttribute('href') || '').slice(1), t = doc.getElementById(id);
      if (t && rows.indexOf(t) <= idx) best = id;
    });
    return best;
  }
  function openToc(){
    if (!cur) return;
    var list = doc.getElementById('tocd-list'), here = currentSection();
    list.innerHTML = '';
    doc.getElementById('tocd-title').textContent = (DATA[PID] || {}).title || md4T('목차');
    [].forEach.call(cur.querySelectorAll('.toc li'), function(li){
      var a = li.querySelector('a'); if (!a) return;
      var id = (a.getAttribute('href') || '').slice(1), item = el('li', li.className), b = el('a', '', a.textContent);
      b.href = '#';
      if (id === here) item.classList.add('here');
      b.onclick = function(ev){ ev.preventDefault(); closeDrawers(); flashRow(doc.getElementById(id)); };
      item.appendChild(b); list.appendChild(item);
    });
    if (!list.children.length) list.appendChild(el('li', 'empty', md4T('목차가 없는 논문이에요.')));
    tocd.classList.add('open'); scrim.classList.add('open');
    var h = list.querySelector('.here'); if (h) h.scrollIntoView({block: 'center'});
  }
  topTitle.onclick = function(e){ if (!cur) return; e.stopPropagation(); openToc(); };

  // ---- 번역 ⇄ 원문 토글 (대조는 화면에서 뺐다 — 문장을 누르면 짝 문장이 뜬다) ----
  if (doc.getElementById('m-both').checked){ doc.getElementById('m-ko').checked = true; store('mode', 'ko'); }
  function langLabel(){
    var en = doc.getElementById('m-en').checked;
    setText('lang-btn', (en ? md4T('원문') : md4T('번역')) + ' ⇄');
    doc.getElementById('lang-btn').setAttribute('aria-label', en ? md4T('번역으로 보기') : md4T('원문으로 보기'));
  }
  doc.getElementById('lang-btn').onclick = function(e){
    e.stopPropagation();
    modeAnchor = topRow();   // 바꾸기 전 화면 맨 위 문단 — 바꾼 뒤 그 문단으로 (CORE_JS의 change 처리)
    var to = doc.getElementById('m-en').checked ? 'm-ko' : 'm-en', r = doc.getElementById(to);
    r.checked = true; r.dispatchEvent(new Event('change'));
    langLabel();
  };

  // ---- 키보드 높이 — iOS는 키보드가 올라와도 아래에 붙은 창을 밀어 올리지 않는다 ----
  function fitKeyboard(){
    var vv = window.visualViewport; if (!vv) return;
    var kb = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
    doc.documentElement.style.setProperty('--kb', kb + 'px');
  }
  if (window.visualViewport){ visualViewport.addEventListener('resize', fitKeyboard); visualViewport.addEventListener('scroll', fitKeyboard); }

  // ---- 에이전트 ----
  var AG = {key: null, lang: 'ko', title: '', msgs: null, busy: false, mode: 'chat'};
  var agBody = doc.getElementById('ag-body'), agIn = doc.getElementById('ag-in'), agSend = doc.getElementById('ag-send');
  var PNAME = {openai: 'OpenAI', anthropic: 'Anthropic', gemini: 'Google Gemini'};
  function aiCfg(){
    var c = lsGet('narani:ai', null) || {}, d = (INDEX && INDEX.agent) || {}, tiers = CFG.ai_models || {};
    c.keys = c.keys || {}; c.models = c.models || {};
    c.provider = c.provider || d.provider || 'openai';
    c.model = c.models[c.provider] || (d.provider === c.provider && d.model) || (tiers[c.provider] || [])[0] || '';
    return c;
  }
  function chat(){
    if (!AG.msgs) AG.msgs = lsGet('narani:chat:' + AG.key, []) || [];
    return AG.msgs;
  }
  function saveChat(){ lsSet('narani:chat:' + AG.key, chat().slice(-30)); }
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function md(text){   // 답 — 굵게·코드·목록·문단, [r12]·[r12, r15] 근거 링크
    var h = esc(text).replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/`([^`\n]+)`/g, '<code>$1</code>');
    h = h.replace(/\[((?:r\d+\s*,?\s*)+)\]/g, function(m, ids){
      return ids.split(/\s*,\s*/).filter(Boolean).map(function(r){
        return '<a href="#" class="rr" data-row="' + r.replace(/\D/g, '') + '">[' + r + ']</a>';
      }).join(' ');
    });
    var out = [], list = false;
    h.split('\n').forEach(function(line){
      var m = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
      if (m){ if (!list){ out.push('<ul>'); list = true; } out.push('<li>' + m[1] + '</li>'); return; }
      if (list){ out.push('</ul>'); list = false; }
      if (line.trim()) out.push('<p>' + line + '</p>');
    });
    if (list) out.push('</ul>');
    return out.join('');
  }
  agBody.addEventListener('click', function(e){
    var a = e.target.closest ? e.target.closest('a.rr') : null;
    if (!a) return;
    e.preventDefault(); closeDrawers();
    flashRow(doc.getElementById(PID + '-r' + a.getAttribute('data-row')));
  });
  function msgEl(m){
    var d = el('div', 'm ' + (m.role === 'user' ? 'u' : (m.err ? 'a err' : 'a')));
    if (m.role === 'user' || m.err) d.textContent = m.content; else d.innerHTML = md(m.content);
    return d;
  }
  function setTabs(){
    var n = NOTE.key ? items().length : 0, notes = AG.mode === 'notes';
    setText('tab-notes', md4T('메모') + (n ? ' ' + n : ''));
    setText('tab-chat', md4T('질문하기'));
    doc.getElementById('tab-notes').classList.toggle('on', notes);
    doc.getElementById('tab-chat').classList.toggle('on', !notes);
    agent.classList.toggle('notes', notes);
  }
  doc.getElementById('tab-notes').onclick = function(){ lsSet('narani:agtab', 'notes'); showNotes(); };
  doc.getElementById('tab-chat').onclick = function(){ lsSet('narani:agtab', 'chat'); showChat(); };
  function showChat(){
    AG.mode = 'chat'; setTabs(); agBody.innerHTML = '';
    doc.getElementById('ag-form').style.display = '';
    var c = aiCfg();
    if (!c.keys[c.provider]){ showSettings(md4T('질문하려면 이 기기에 AI 서비스와 API 키를 한 번 넣어 주세요.')); return; }
    var msgs = chat();
    if (!msgs.length) agBody.appendChild(el('p', 'hint', md4T('이 논문에 대해 무엇이든 물어보세요 — 답의 [r12] 같은 표시를 누르면 그 문단으로 갑니다. ({p} · {m})', {p: PNAME[c.provider] || c.provider, m: c.model})));
    msgs.forEach(function(m){ agBody.appendChild(msgEl(m)); });
    agBody.scrollTop = agBody.scrollHeight;
  }
  function showSettings(note){
    AG.mode = 'settings'; setTabs(); agBody.innerHTML = '';
    doc.getElementById('ag-form').style.display = 'none';
    var c = aiCfg(), tiers = CFG.ai_models || {};
    if (note) agBody.appendChild(el('p', 'hint', note));
    agBody.appendChild(el('label', '', md4T('AI 서비스')));
    var sel = el('select'); Object.keys(PNAME).forEach(function(p){ var o = el('option', '', PNAME[p]); o.value = p; if (p === c.provider) o.selected = true; sel.appendChild(o); });
    agBody.appendChild(sel);
    agBody.appendChild(el('label', '', md4T('모델')));
    var model = el('input'); model.setAttribute('list', 'ag-models'); model.value = c.model; agBody.appendChild(model);
    var dl = el('datalist'); dl.id = 'ag-models'; agBody.appendChild(dl);
    function fillModels(){ dl.innerHTML = ''; (tiers[sel.value] || []).forEach(function(m){ var o = el('option'); o.value = m; dl.appendChild(o); }); }
    fillModels();
    agBody.appendChild(el('label', '', md4T('API 키')));
    var key = el('input'); key.type = 'password'; key.autocomplete = 'off'; key.spellcheck = false; agBody.appendChild(key);
    function keyHint(){ key.value = ''; key.placeholder = c.keys[sel.value] ? md4T('저장됨 — 바꾸려면 새로 입력') : md4T('키를 붙여 넣으세요'); }
    keyHint();
    sel.onchange = function(){ fillModels(); model.value = c.models[sel.value] || ((INDEX && INDEX.agent && INDEX.agent.provider === sel.value) ? INDEX.agent.model : (tiers[sel.value] || [])[0] || ''); keyHint(); };
    agBody.appendChild(el('p', 'hint', md4T('키는 이 기기의 브라우저에만 저장됩니다. 질문하면 이 논문의 본문과 내 하이라이트·메모, 대화가 고른 AI 서비스로 직접 보내집니다. 요금은 그 키의 계정에 청구됩니다.')));
    var acts = el('div', 'acts'), save = el('button', 'pri', md4T('저장')), del = el('button', '', md4T('이 서비스 키 지우기'));
    save.onclick = function(){
      c.provider = sel.value; c.models[c.provider] = model.value.trim();
      if (key.value.trim()) c.keys[c.provider] = key.value.trim();
      lsSet('narani:ai', {provider: c.provider, models: c.models, keys: c.keys});
      try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (e) {}
      showChat();
    };
    del.onclick = function(){ delete c.keys[sel.value]; lsSet('narani:ai', {provider: c.provider, models: c.models, keys: c.keys}); keyHint(); };
    acts.appendChild(save); if (c.keys[c.provider]) acts.appendChild(del);
    if (c.keys[c.provider]){ var back = el('button', '', md4T('취소')); back.onclick = showChat; acts.appendChild(back); }
    agBody.appendChild(acts);
  }
  doc.getElementById('ag-set').onclick = function(){ if (AG.mode === 'settings') showChat(); else showSettings(); };
  doc.getElementById('ag-clear').onclick = function(){ AG.msgs = []; saveChat(); showChat(); };
  doc.getElementById('fab').onclick = function(e){
    e.stopPropagation();
    closeSheet();
    setText('ag-clear', '⟲'); doc.getElementById('ag-clear').setAttribute('aria-label', md4T('대화 지우기'));
    agIn.placeholder = md4T('이 논문에 대해 물어보세요');
    agent.classList.add('open'); scrim.classList.add('open');
    var tab = lsGet('narani:agtab', null) || (items().length ? 'notes' : 'chat');   // 지난번 탭 (처음엔 표시가 있으면 메모)
    if (tab === 'notes') showNotes(); else showChat();
  };

  function paperText(){
    var out = [], n = 0;
    [].forEach.call(cur.querySelectorAll('.row'), function(r){
      var k = r.id.replace(/^p\d+-r/, ''), c = r.querySelector('.cell.en') || r.querySelector('.cell.same') || r.querySelector('.cell.ko');
      var t = c ? c.textContent.replace(/\s+/g, ' ').trim() : '';
      if (t && n < 300000){ out.push('[r' + k + '] ' + t); n += t.length; }
    });
    return out.join('\n');
  }
  function notesText(){
    if (NOTE.key !== AG.key) return '';
    return items().map(function(it){
      var a = (it.anchors || [])[0] || {};
      return '- [r' + a.row + '] "' + String(a.quote || '').slice(0, 300) + '"' + (it.note ? ' — ' + it.note : '');
    }).join('\n');
  }
  function systemPrompt(){
    var notes = notesText();
    return 'You are the reading assistant inside Narani, on the user\'s phone. The user is reading the paper below. ' +
      'Reply in the language whose BCP-47 code is "' + AG.lang + '". Be concise and concrete. Base every claim on the paper ' +
      'and cite the paragraphs you used with their ids, like [r12] or [r12, r15]. If the paper does not say, say so first; ' +
      'you may then add general knowledge, clearly marked as such.\n\nTITLE: ' + AG.title +
      (notes ? '\n\nTHE USER\'S HIGHLIGHTS AND NOTES:\n' + notes : '') +
      '\n\nPAPER (original text; one paragraph per line, each starting with its id):\n' + paperText();
  }
  function sse(resp, onData){
    var reader = resp.body.getReader(), dec = new TextDecoder(), buf = '';
    function pump(){
      return reader.read().then(function(r){
        if (r.done) return;
        buf += dec.decode(r.value, {stream: true});
        var lines = buf.split('\n'); buf = lines.pop();
        lines.forEach(function(l){
          l = l.trim();
          if (l.indexOf('data:') !== 0) return;
          var d = l.slice(5).trim();
          if (d && d !== '[DONE]'){ try { onData(JSON.parse(d)); } catch (e) {} }
        });
        return pump();
      });
    }
    return pump();
  }
  function failed(resp){
    return resp.text().then(function(t){
      var m = t; try { var j = JSON.parse(t); m = (j.error && (j.error.message || j.error)) || t; } catch (e) {}
      throw new Error('HTTP ' + resp.status + ': ' + String(m).slice(0, 300));
    });
  }
  function askAI(c, system, msgs, onText){
    var key = c.keys[c.provider], req;
    if (c.provider === 'anthropic'){
      req = fetch('https://api.anthropic.com/v1/messages', {method: 'POST', headers: {'x-api-key': key,
        'anthropic-version': '2023-06-01', 'anthropic-dangerous-direct-browser-access': 'true', 'content-type': 'application/json'},
        body: JSON.stringify({model: c.model, max_tokens: 2048, stream: true, system: system, messages: msgs})});
      return req.then(function(r){ if (!r.ok) return failed(r);
        return sse(r, function(d){ if (d.type === 'content_block_delta' && d.delta && d.delta.text) onText(d.delta.text); }); });
    }
    if (c.provider === 'gemini'){
      req = fetch('https://generativelanguage.googleapis.com/v1beta/models/' + encodeURIComponent(c.model) +
        ':streamGenerateContent?alt=sse', {method: 'POST', headers: {'x-goog-api-key': key, 'content-type': 'application/json'},
        body: JSON.stringify({systemInstruction: {parts: [{text: system}]}, contents: msgs.map(function(m){
          return {role: m.role === 'assistant' ? 'model' : 'user', parts: [{text: m.content}]}; })})});
      return req.then(function(r){ if (!r.ok) return failed(r);
        return sse(r, function(d){
          var p = d.candidates && d.candidates[0] && d.candidates[0].content && d.candidates[0].content.parts;
          if (p) onText(p.map(function(x){ return x.text || ''; }).join(''));
        }); });
    }
    req = fetch('https://api.openai.com/v1/chat/completions', {method: 'POST',
      headers: {'Authorization': 'Bearer ' + key, 'content-type': 'application/json'},
      body: JSON.stringify({model: c.model, stream: true, messages: [{role: 'system', content: system}].concat(msgs)})});
    return req.then(function(r){ if (!r.ok) return failed(r);
      return sse(r, function(d){ var t = d.choices && d.choices[0] && d.choices[0].delta && d.choices[0].delta.content; if (t) onText(t); }); });
  }
  function send(){
    var q = agIn.value.trim(), c = aiCfg();
    if (!q || AG.busy || !cur) return;
    if (!c.keys[c.provider]){ showSettings(); return; }
    if (navigator.onLine === false){ agBody.appendChild(msgEl({role: 'assistant', err: true, content: md4T('오프라인이라 질문할 수 없어요.')})); return; }
    var msgs = chat();
    msgs.push({role: 'user', content: q});
    agIn.value = ''; agIn.style.height = '';
    showChat();
    var ans = {role: 'assistant', content: ''}, box = msgEl(ans);
    box.innerHTML = '<p class="hint">' + esc(md4T('읽는 중…')) + '</p>';
    agBody.appendChild(box); agBody.scrollTop = agBody.scrollHeight;
    AG.busy = true; agSend.disabled = true;
    var hist = msgs.slice(-12).map(function(m){ return {role: m.role, content: m.content}; });
    askAI(c, systemPrompt(), hist, function(t){
      ans.content += t; box.innerHTML = md(ans.content);
      var near = agBody.scrollHeight - agBody.scrollTop - agBody.clientHeight < 120;
      if (near) agBody.scrollTop = agBody.scrollHeight;
    }).then(function(){
      if (!ans.content) throw new Error(md4T('빈 답이 왔어요'));
      msgs.push(ans);
    }).catch(function(e){
      msgs.push({role: 'assistant', err: true, content: md4T('답을 받지 못했어요: {e}', {e: e.message || e})});
      msgs.splice(msgs.length - 2, 1);   // 실패한 질문은 대화에서 빼고(다시 물을 수 있게) 오류만 남긴다
      agIn.value = q;
    }).then(function(){
      AG.busy = false; agSend.disabled = false;
      AG.msgs = msgs.filter(function(m){ return !m.err; }); saveChat();
      var shown = msgs.slice(); showChat(); if (shown.length && shown[shown.length - 1].err) agBody.appendChild(msgEl(shown[shown.length - 1]));
      agBody.scrollTop = agBody.scrollHeight;
    });
  }
  doc.getElementById('ag-form').addEventListener('submit', function(e){ e.preventDefault(); send(); });
  agIn.addEventListener('input', function(){ agIn.style.height = ''; agIn.style.height = Math.min(agIn.scrollHeight, 200) + 'px'; });
  agIn.addEventListener('keydown', function(e){ if (e.key === 'Enter' && !e.shiftKey && !e.isComposing){ e.preventDefault(); send(); } });

  // ---- 듣기 (§phone.tts) — 데스크톱이 만든 음성 파일 하나 + 문장 시간표 [t, 행, s, e] ----
  // 화면이 꺼져도·잠금 화면·CarPlay 단추로 듣는다(실기기 시험 tts-test.html로 확인한 방식: <audio> 하나 +
  // Media Session, 섹션 이동은 파일 안에서 seek). 파일은 처음 들을 때 받아 Cache Storage에 두고 blob URL로 꽂는다.
  var AC = 'narani-audio', au = doc.getElementById('pl-au'), player = doc.getElementById('player');
  var AU = {e: null, key: null, tl: null, url: null, ready: false, cur: -1, sec: -1, lastSave: 0, lastPS: 0,
            lastTU: 0, userScroll: 0, want: null, busy: false};
  var HL = !!(window.CSS && CSS.highlights && window.Highlight);
  function audioOf(e){ return e && e.audio && e.audio.file ? e.audio : null; }
  function fmtT(s){ s = Math.max(0, Math.floor(s || 0)); var h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
    return (h ? h + ':' + ('0' + m).slice(-2) : m) + ':' + ('0' + s % 60).slice(-2); }
  function audioKey(a){ return '__audio/' + a.file + '?h=' + a.hash; }
  function audioCached(a){
    if (!a || !window.caches) return Promise.resolve(false);
    return caches.open(AC).then(function(c){ return c.match(audioKey(a)); }).then(function(r){ return !!r; }, function(){ return false; });
  }
  function readAll(r, total, onp){
    if (!r.body || !r.body.getReader) return r.blob();
    var rd = r.body.getReader(), parts = [], got = 0;
    return (function pump(){
      return rd.read().then(function(x){
        if (x.done) return new Blob(parts, {type: 'audio/mp4'});
        parts.push(x.value); got += x.value.length; if (onp && total) onp(Math.min(1, got / total));
        return pump();
      });
    })();
  }
  function audioBlob(a, onp){
    function fetchIt(){
      if (!source || !source.raw) return Promise.reject(new Error('no source'));
      return source.raw(a.file).then(function(r){ return readAll(r, a.bytes, onp); });
    }
    if (!window.caches) return fetchIt();
    return caches.open(AC).then(function(c){
      return c.match(audioKey(a)).then(function(hit){
        if (hit) return hit.blob().then(function(b){ return b.type ? b : new Blob([b], {type: 'audio/mp4'}); });
        return fetchIt().then(function(b){
          return c.put(audioKey(a), new Response(b, {headers: {'Content-Type': 'audio/mp4'}})).then(function(){ return b; }, function(){ return b; });
        });
      });
    });
  }
  function audioTl(a){
    return cacheGet(a.tl).then(function(c){
      if (c && c.__h === a.hash) return c;
      if (!source) { if (c) return c; throw new Error('no source'); }
      return source.get(a.tl).then(function(t){ t.__h = a.hash; cachePut(a.tl, t); return t; }, function(err){ if (c) return c; throw err; });
    });
  }

  // ---- 열고 닫기 ----
  function audioOpened(e){
    stopAudio();
    AU.e = e; AU.key = e.key; AU.tl = null; AU.ready = false;
    var a = audioOf(e);
    body.classList.toggle('has-audio', !!a);
    if (!a) return;
    audioCached(a).then(function(yes){ if (yes && AU.e === e) prime(false); });   // 받아 둔 음성 — 바로 들을 수 있게 꽂아 둔다
  }
  function stopAudio(){
    if (!AU.e) return;
    try { au.pause(); } catch (x) {}
    savePos();
    body.classList.remove('listening', 'has-audio');
    unmarkNow();
    if (AU.url){ try { URL.revokeObjectURL(AU.url); } catch (x) {} AU.url = null; }
    au.removeAttribute('src'); try { au.load(); } catch (x) {}
    AU.e = null; AU.tl = null; AU.ready = false; AU.cur = AU.sec = -1;
    if ('mediaSession' in navigator) try { navigator.mediaSession.metadata = null; navigator.mediaSession.playbackState = 'none'; } catch (x) {}
  }
  // 시간표와 파일을 준비해 <audio>에 꽂는다. 처음 받는 것이면 받는 동안 막대에 진행을 보인다.
  function prime(show){
    var e = AU.e, a = audioOf(e);
    if (!a) return Promise.reject(new Error('no audio'));
    if (AU.ready) return Promise.resolve();
    if (AU.busy) return AU.busy;
    if (show) setText('pl-sec', md4T('음성 받는 중…'));
    AU.busy = audioTl(a).then(function(tl){
      if (AU.e !== e) throw new Error('stale');
      AU.tl = tl;
      return audioBlob(a, function(f){ if (show && AU.e === e) setText('pl-sec', md4T('음성 받는 중… {p}%', {p: Math.round(f * 100)})); });
    }).then(function(blob){
      if (AU.e !== e) throw new Error('stale');
      if (AU.url) try { URL.revokeObjectURL(AU.url); } catch (x) {}
      var keep = au.currentTime || 0;   // 다시 꽂을 때(앱을 다시 열었을 때) 듣던 자리를 잃지 않게
      AU.url = URL.createObjectURL(blob);
      au.src = AU.url;
      if (keep) au.addEventListener('loadedmetadata', function f(){ au.removeEventListener('loadedmetadata', f); au.currentTime = keep; });   // iOS 26: 다시 연 홈 화면 앱의 <audio>가 소리 없이 재생되는 버그 — 재생 직전에 새로 꽂는 것으로 피한다
      AU.ready = true;
      doc.getElementById('pl-seek').max = Math.floor(AU.tl.duration);
      setText('pl-dur', fmtT(AU.tl.duration));
      var stale = a.src && AU.e.hash && a.src !== AU.e.hash;
      setText('pl-sec', stale ? md4T('본문이 바뀐 뒤의 옛 음성이에요 — 하이라이트가 어긋날 수 있어요') : (AU.tl.sections[0] || {}).title || '');
    });
    AU.busy.then(function(){ AU.busy = false; }, function(err){
      AU.busy = false;
      if (AU.e === e && show) setText('pl-sec', (err && err.kind === 'auth') ? md4T('Google Drive 연결이 만료됐어요.') : md4T('음성을 받지 못했어요. 인터넷에 연결된 뒤 다시 눌러 보세요.'));
    });
    return AU.busy;
  }

  // ---- 시간표 ----
  function SEC(){ return (AU.tl && AU.tl.sections) || []; }
  function secAt(t){ var S = SEC(), k = 0; for (var i = 0; i < S.length; i++) if (S[i].start <= t + 0.05) k = i; return k; }
  function sentAtT(t){
    var S = AU.tl.sents, lo = 0, hi = S.length - 1;
    while (lo < hi){ var m = (lo + hi + 1) >> 1; if (S[m][0] <= t + 0.05) lo = m; else hi = m - 1; }
    return lo;
  }
  function timeAtRow(row, off){   // 그 행(과 셀 안 위치)에서 가장 가까운, 그 뒤의 첫 문장
    var S = AU.tl.sents;
    for (var i = 0; i < S.length; i++){
      if (S[i][1] > row) return S[i][0];
      if (S[i][1] === row && (off == null || S[i][3] > off)) return S[i][0];
    }
    return 0;
  }

  // ---- 듣기 범위 ----
  function RANGE(){ return lsGet('narani:arange:' + AU.key, {mode: 'all'}); }
  function allowed(i){
    var R = RANGE(), s = SEC()[i];
    if (!s) return false;
    if (R.mode === 'sec') return s.row === R.row;
    if (R.mode === 'here') return s.start >= (R.from || 0) - 0.05 || s.row === R.row;
    if (R.mode === 'pick') return (R.rows || []).indexOf(s.row) >= 0;
    return true;
  }
  function rangeSheet(){
    var R = RANGE(), k = Math.max(0, secAt(au.currentTime || 0)), S = SEC(), here = S[k] || {};
    var box = el('div', 'rng'), picks = (R.rows || S.map(function(s){ return s.row; })).slice(), list = el('div', 'rng-secs');
    function radio(v, label){
      var l = el('label'), r = el('input'); r.type = 'radio'; r.name = 'rng'; r.value = v; r.checked = (R.mode || 'all') === v;
      r.onchange = function(){ list.style.display = v === 'pick' ? '' : 'none'; };
      l.appendChild(r); l.appendChild(doc.createTextNode(' ' + label)); box.appendChild(l);
    }
    radio('all', md4T('처음부터 끝까지'));
    radio('sec', md4T('이 섹션만 — {s}', {s: here.title || ''}));
    radio('here', md4T('여기부터 끝까지'));
    radio('pick', md4T('섹션 골라 듣기'));
    S.forEach(function(s){
      var l = el('label'), c = el('input'); c.type = 'checkbox'; c.checked = picks.indexOf(s.row) >= 0;
      c.onchange = function(){ var i = picks.indexOf(s.row); if (c.checked && i < 0) picks.push(s.row); if (!c.checked && i >= 0) picks.splice(i, 1); };
      l.appendChild(c); l.appendChild(doc.createTextNode(' ' + s.title + ' · ' + fmtT(s.end - s.start))); list.appendChild(l);
    });
    list.style.display = R.mode === 'pick' ? '' : 'none';
    box.appendChild(list);
    var ok = button(md4T('적용'), function(){
      var v = (box.querySelector('input[name=rng]:checked') || {}).value || 'all', t = au.currentTime || 0;
      var N = {mode: v, row: here.row, from: v === 'here' ? t : here.start, rows: picks};
      lsSet('narani:arange:' + AU.key, N);
      closeSheet();
      if (!allowed(secAt(t))) jumpSec(1, md4T('범위'));
    });
    openSheet(md4T('듣기 범위'), [box], [ok]);
  }

  // ---- 재생 ----
  function startAt(t){
    if (t != null){ try { au.currentTime = Math.max(0, Math.min(AU.tl.duration - 0.3, t)); } catch (x) {} }
    var k = secAt(au.currentTime || 0), S = SEC(), i, to = null;
    if (!allowed(k)){   // 범위 밖에서 재생 — 그 뒤의 첫 고른 섹션, 없으면 범위 처음부터
      for (i = k + 1; i < S.length && to == null; i++) if (allowed(i)) to = i;
      for (i = 0; i < S.length && to == null; i++) if (allowed(i)) to = i;
      if (to != null) au.currentTime = S[to].start;
    }
    var p = au.play();
    if (p && p.then) p.then(null, function(){ setText('pl-sec', md4T('▶을 눌러 들으세요')); });
  }
  function listenFrom(row, off){   // 🎧·'여기부터 듣기' — 화면을 보고 있던 자리에서
    if (!audioOf(AU.e)) return;
    body.classList.add('listening'); closeSheet(); closeDrawers();
    var go = function(){
      var t = row != null ? timeAtRow(row, off) : null;
      // 콕 집어 '여기부터' 들으면 그게 먼저다 — 고른 범위 밖이면 범위를 '처음부터 끝까지'로 되돌린다
      if (t != null && !allowed(secAt(t))) lsSet('narani:arange:' + AU.key, {mode: 'all'});
      startAt(t);
    };
    if (AU.ready) go(); else prime(true).then(go, function(){});
  }
  function jumpSec(dir, why){
    if (!AU.tl) return;
    var t = au.currentTime, k = secAt(t), S = SEC(), target = null, i;
    if (dir > 0){ for (i = k + 1; i < S.length; i++) if (allowed(i)){ target = i; break; } }
    else {
      if (t - S[k].start > 3 && allowed(k)) target = k;
      else for (i = k - 1; i >= 0; i--) if (allowed(i)){ target = i; break; }
      if (target == null) target = k;
    }
    if (target == null){ au.pause(); return; }
    au.currentTime = S[target].start;
  }
  function savePos(){
    if (!AU.tl || !AU.key || !au.currentTime) return;
    var s = AU.tl.sents[sentAtT(au.currentTime)];
    if (s) store('pos:' + AU.key, 'r' + s[1] + ':0');   // 다음에 열면 리더가 들은 자리로 — 🎧도 거기서
  }
  doc.getElementById('lis').onclick = function(e){
    e.stopPropagation();
    var r = topRow(); listenFrom(r ? +r.id.replace(/^p\d+-r/, '') : null, null);
  };
  doc.getElementById('pl-pp').onclick = function(){
    if (!AU.ready){ prime(true).then(function(){ startAt(null); }, function(){}); return; }
    if (au.paused) startAt(null); else au.pause();
  };
  doc.getElementById('pl-back').onclick = function(){ au.currentTime = Math.max(0, au.currentTime - 15); };
  doc.getElementById('pl-fwd').onclick = function(){ au.currentTime = Math.min(au.duration || 1e9, au.currentTime + 15); };
  doc.getElementById('pl-prev').onclick = function(){ jumpSec(-1); };
  doc.getElementById('pl-next').onclick = function(){ jumpSec(1); };
  doc.getElementById('pl-range').onclick = function(){ if (AU.tl) rangeSheet(); };
  doc.getElementById('pl-x').onclick = function(){ au.pause(); savePos(); body.classList.remove('listening'); unmarkNow(); };
  function audioLabels(){
    setText('pl-range', md4T('범위'));
    [['lis', md4T('여기부터 듣기')], ['pl-range', md4T('듣기 범위')], ['pl-prev', md4T('이전 섹션')], ['pl-back', md4T('15초 뒤로')],
     ['pl-pp', md4T('재생')], ['pl-fwd', md4T('15초 앞으로')], ['pl-next', md4T('다음 섹션')], ['pl-spd', md4T('속도')],
     ['pl-x', md4T('듣기 닫기')], ['pl-seek', md4T('재생 위치')]].forEach(function(x){
      var n = doc.getElementById(x[0]); if (n) n.setAttribute('aria-label', x[1]);
    });
  }
  var SPEEDS = [1, 1.25, 1.5, 1.75, 2, 0.8];
  function setSpd(v){ au.playbackRate = au.defaultPlaybackRate = v; lsSet('narani:aspd', v); setText('pl-spd', v + '×'); }
  setSpd(lsGet('narani:aspd', 1));
  doc.getElementById('pl-spd').onclick = function(){ var v = au.playbackRate, i = SPEEDS.indexOf(v); setSpd(SPEEDS[(i + 1) % SPEEDS.length]); };
  var seekEl = doc.getElementById('pl-seek');
  seekEl.oninput = function(){ setText('pl-cur', fmtT(+seekEl.value)); };
  seekEl.onchange = function(){ au.currentTime = +seekEl.value; };

  function meta(k){
    if (!('mediaSession' in navigator) || !AU.tl) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({title: (DATA[PID] || {}).title || APP, artist: (SEC()[k] || {}).title || '',
        album: APP, artwork: [{src: 'icon-512.png', sizes: '512x512', type: 'image/png'}, {src: 'icon-192.png', sizes: '192x192', type: 'image/png'}]});
    } catch (x) {}
  }
  if ('mediaSession' in navigator){
    var MH = {
      play: function(){ startAt(null); }, pause: function(){ au.pause(); }, stop: function(){ au.pause(); },
      seekbackward: function(d){ au.currentTime = Math.max(0, au.currentTime - ((d && d.seekOffset) || 15)); },
      seekforward: function(d){ au.currentTime = au.currentTime + ((d && d.seekOffset) || 15); },
      previoustrack: function(){ jumpSec(-1); }, nexttrack: function(){ jumpSec(1); },
      seekto: function(d){ if (d && d.seekTime != null) au.currentTime = d.seekTime; }
    };
    Object.keys(MH).forEach(function(k){ try { navigator.mediaSession.setActionHandler(k, MH[k]); } catch (x) {} });
  }
  if ('audioSession' in navigator) try { navigator.audioSession.type = 'playback'; } catch (x) {}
  ['play', 'pause'].forEach(function(ev){
    au.addEventListener(ev, function(){
      setText('pl-pp', ev === 'play' ? '⏸' : '▶');
      doc.getElementById('pl-pp').setAttribute('aria-label', ev === 'play' ? md4T('일시정지') : md4T('재생'));
      if ('mediaSession' in navigator) try { navigator.mediaSession.playbackState = ev === 'play' ? 'playing' : 'paused'; } catch (x) {}
      if (ev === 'pause') savePos();
    });
  });
  au.addEventListener('ended', function(){ savePos(); });

  // ---- 지금 문장 칠하기 (CSS Custom Highlight — DOM을 바꾸지 않는다; 없으면 행째로) ----
  function cellRange(cell, s, e){
    var w = doc.createTreeWalker(cell, NodeFilter.SHOW_TEXT), n, pos = 0, r = doc.createRange(), started = false;
    while ((n = w.nextNode())){
      var len = n.data.length;
      if (!started && s < pos + len){ r.setStart(n, s - pos); started = true; }
      if (started && e <= pos + len){ r.setEnd(n, e - pos); return r; }
      pos += len;
    }
    return started ? (r.setEnd(cell, cell.childNodes.length), r) : null;
  }
  var nowRow = null;
  function unmarkNow(){
    if (HL) try { CSS.highlights.delete('tts'); } catch (x) {}
    if (nowRow){ nowRow.classList.remove('tts-row'); nowRow = null; }
  }
  function markNow(i){
    var s = AU.tl.sents[i], row = doc.getElementById(PID + '-r' + s[1]);
    unmarkNow();
    if (!row) return;
    var cell = row.querySelector('.cell.' + AU.tl.side) || row.querySelector('.cell.same'), rect = null;
    if (HL && cell && cell.offsetParent){
      var r = cellRange(cell, s[2], s[3]);
      if (r){ CSS.highlights.set('tts', new Highlight(r)); rect = r.getBoundingClientRect(); }
    }
    if (!rect){ nowRow = row; row.classList.add('tts-row'); rect = row.getBoundingClientRect(); }
    // 화면을 보고 있으면 따라 내려간다 — 손으로 스크롤한 뒤 몇 초는 두고, 이미 보이면 움직이지 않는다
    var bottom = window.innerHeight - player.offsetHeight - 16;
    if (doc.visibilityState === 'visible' && Date.now() - AU.userScroll > 4000 && (rect.top < 70 || rect.bottom > bottom))
      window.scrollBy({top: rect.top - window.innerHeight * 0.3, behavior: 'smooth'});
  }
  ['touchmove', 'wheel'].forEach(function(ev){ window.addEventListener(ev, function(){ AU.userScroll = Date.now(); }, {passive: true}); });
  au.addEventListener('timeupdate', function(){
    if (!AU.tl) return;
    var t = au.currentTime, now = Date.now(), k = secAt(t), S = SEC();
    if (!allowed(k)){   // 고른 범위 밖 — 다음 고른 섹션으로, 없으면 멈춤
      if (au.paused) return;
      var nx = null; for (var i = k + 1; i < S.length; i++) if (allowed(i)){ nx = i; break; }
      if (nx == null){ au.pause(); setText('pl-sec', md4T('고른 범위를 다 들었어요')); }
      else au.currentTime = S[nx].start;
      return;
    }
    if (k !== AU.sec){ AU.sec = k; meta(k); setText('pl-sec', (S[k] || {}).title || ''); }
    var si = sentAtT(t);
    if (si !== AU.cur && body.classList.contains('listening')){ AU.cur = si; markNow(si); }
    if (doc.activeElement !== seekEl){ seekEl.value = Math.floor(t); setText('pl-cur', fmtT(t)); }
    if (now - AU.lastSave > 5000){ AU.lastSave = now; savePos(); }
    if ('mediaSession' in navigator && navigator.mediaSession.setPositionState && now - AU.lastPS > 1000){
      AU.lastPS = now;
      try { navigator.mediaSession.setPositionState({duration: AU.tl.duration, playbackRate: au.playbackRate || 1, position: Math.min(t, AU.tl.duration)}); } catch (x) {}
    }
  });
  doc.addEventListener('visibilitychange', function(){
    if (doc.visibilityState !== 'visible' || !AU.e) return;
    // 멈춘 채 다시 보이면 다음 ▶ 때 캐시에서 새로 꽂는다 — 실기기 시험에서 통과한 방식(재생 직전에 src)과 같게
    if (au.paused) AU.ready = false;
    if (AU.tl && AU.cur >= 0 && body.classList.contains('listening')) markNow(AU.cur);
  });
  window.addEventListener('pageshow', function(e){ if (e.persisted && au.paused) AU.ready = false; });
})();
