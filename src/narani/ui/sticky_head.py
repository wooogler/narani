"""읽기 캔버스 위 '지금 보는 섹션' 띠 — 내려 읽는 동안 그 자리의 섹션 제목(상위 섹션 경로까지)이 위에 붙는다.

예: `4 Zoro › 4.2 Enforcing the Rules`. 원문|번역 두 칸이면 칸마다 제 언어의 경로를 그 칸 폭에 나란히,
한 칸이면 그 언어 하나만. 경로의 낱말을 누르면 그 섹션 머리로 간다(읽던 자리 알약에 자리를 쌓는다).

**왜 CSS `position: sticky`만으로 안 되나.** 정렬 그리드(`.sbs-grid`)는 행마다 셀이 따로라 헤더(h1~h6)는
그 행의 셀 안에 있다. sticky는 제 부모(셀) 안에서만 붙어 있으므로 섹션이 여러 행에 걸치면 첫 행이
지나갈 때 같이 올라가 버린다. 그래서 그리드 맨 앞에 높이 0짜리 sticky 자식(`.sbs-stick`, 모든 칸을
가로지름)을 하나 두고 — 스크롤 상자 기준으로 붙는다 — 그 안의 띠를 스크롤마다 JS로 다시 채운다.

- 지금 섹션 = 그 칸에서 머리가 화면 맨 위(+JUMP_EDGE_PX)를 지난 마지막 헤더. 그 머리가 아직 보이면
  띠를 감춘다(같은 글이 두 번 보이지 않게). 첫 행(논문 제목)은 섹션으로 치지 않는다.
- 경로 = 그 헤더 앞쪽에서 단계가 더 높은(숫자가 작은) 헤더들.
- 그리드마다 따로 — 위아래 두 뷰어(§split_view의 `.sbs-clone`)도 제 띠를 가진다. 복제본에 딸려 온 띠도
  그 칸의 스크롤로 다시 채운다. 숨은 짝 셀(`.sbs-ghost`)·편집 모드(그리드가 없다)는 건드리지 않는다.
- 지금 섹션이 바뀌면 문서에 `md4-cursec`({row}) 이벤트 — 메모 패널의 하이라이트 목록이 그 섹션 묶음을 칠한다.
- 스크롤 이벤트는 문서에 capture로 한 번만 건다(그리드를 다시 지어도 다시 걸 일이 없다).
  읽던 자리 알약·듣기 막대는 화면 아래, 찾기 바는 창 오른쪽 위 fixed라 이 띠와 겹치지 않는다.
"""

from __future__ import annotations

from narani.ui.jump_back import JUMP_BACK_API, JUMP_EDGE_PX

STICK_API = "__md4Sticky"

CSS = """
/* 지금 보는 섹션 띠 — 높이 0 sticky 자식(그리드 모든 칸을 가로지름) 안에 절대 위치로 띄운다:
   행 배치·높이를 바꾸지 않고, 스크롤 상자(.sbs-grid) 위끝에 붙는다. */
.sbs-stick { grid-column: 1 / -1; position: sticky; top: 0; height: 0; z-index: 6; pointer-events: none;
  user-select: none; }
.sbs-stick-in { position: absolute; left: 0; right: 0; top: 0; display: none;
  grid-template-columns: var(--sbs-cols, 1fr); background: rgba(255,255,255,.97);
  border-bottom: 1px solid #e6e4e0; box-shadow: 0 2px 6px rgba(0,0,0,.04); pointer-events: auto; }
.sbs-stick.on .sbs-stick-in { display: grid; }
/* 경로가 길면 상위 섹션부터 줄인다(각자 말줄임, 최소 2.5em) — 지금 섹션 이름이 마지막까지 남게 */
.sbs-stick-p { min-width: 0; display: flex; align-items: baseline; padding: 5px 22px 5px; font-size: 12.5px;
  line-height: 1.45; color: #9a968e; white-space: nowrap; overflow: hidden; }
.sbs-stick-p > * { white-space: nowrap; }
.sbs-stick-c { flex: 0 1 auto; min-width: 2.5em; overflow: hidden; text-overflow: ellipsis; }
.sbs-stick-c.cur { flex-shrink: .05; }
.sbs-stick-s { flex: 0 0 auto; }
.sbs-grid:not(.sbs-two) .sbs-stick-p { width: 100%; max-width: var(--md4-maxw, 820px); justify-self: center; }
.sbs-grid.sbs-two .sbs-stick-p + .sbs-stick-p { border-left: 1px solid #e6e4e0; }
.sbs-stick-c { cursor: pointer; border-radius: 4px; padding: 0 2px; }
.sbs-stick-c:hover { color: #2383e2; background: rgba(35,131,226,.08); }
.sbs-stick-c.cur { color: #37352f; font-weight: 600; }
.sbs-stick-s { margin: 0 3px; color: #c4c1ba; }
@media (prefers-color-scheme: dark) {
  .sbs-stick-in { background: rgba(18,18,18,.96); border-bottom-color: #2c2c2c; box-shadow: 0 2px 6px rgba(0,0,0,.3); }
  .sbs-stick-p { color: #8a8a8a; }
  .sbs-grid.sbs-two .sbs-stick-p + .sbs-stick-p { border-left-color: #3a3a3a; }
  .sbs-stick-c.cur { color: #e0e0e0; }
  .sbs-stick-c:hover { color: #7bb7ff; background: rgba(90,150,230,.16); }
  .sbs-stick-s { color: #555; }
}
"""

JS = """
(function(){
  if (window.%(api)s) return;
  var EDGE = %(edge)d;
  var LV = ['h1','h2','h3','h4','h5','h6'];
  function sideSel(side){
    var c = '.sbs-cell[data-side="' + side + '"]:not(.sbs-ghost) > .sbs-md > ';
    return LV.map(function(h){ return c + h; }).join(',');
  }
  // 헤더 끝의 '✎ 섹션 구조' 점프 단추 글은 뺀다
  function tx(h){
    var t = h.textContent, a = h.querySelector('.md-sec-jump');
    if (a) t = t.replace(a.textContent, '');
    return t.replace(/\\s+/g, ' ').trim();
  }
  function lvl(h){ return +h.tagName.charAt(1); }
  function heads(g, side){
    return [].filter.call(g.querySelectorAll(sideSel(side)), function(h){
      var c = h.closest('.sbs-cell');
      return c && c.getAttribute('data-row') !== '0';   // 첫 행 = 논문 제목
    });
  }
  function sides(g){
    return ['en', 'ko'].filter(function(s){
      return g.querySelector('.sbs-cell[data-side="' + s + '"]:not(.sbs-ghost)'); });
  }
  // 머리가 기준선(top)을 지난 마지막 헤더 — 문서 순서라 위치가 늘어나므로 이분 탐색
  function lastAbove(hs, top){
    var lo = 0, hi = hs.length - 1, k = -1;
    while (lo <= hi){
      var m = (lo + hi) >> 1;
      if (hs[m].getBoundingClientRect().top <= top){ k = m; lo = m + 1; } else hi = m - 1;
    }
    return k;
  }
  function pathOf(hs, k){
    var out = [hs[k]], cur = lvl(hs[k]);
    for (var j = k - 1; j >= 0 && cur > 1; j--){
      var l = lvl(hs[j]);
      if (l < cur){ out.unshift(hs[j]); cur = l; }
    }
    return out;
  }
  function band(g){
    var b = g.querySelector(':scope > .sbs-stick');
    if (!b){
      b = document.createElement('div');
      b.className = 'sbs-stick';
      b.setAttribute('aria-hidden', 'true');
      b.innerHTML = '<div class="sbs-stick-in"></div>';
      g.insertBefore(b, g.firstChild);
    }
    return b;
  }
  function esc(s){ return s.replace(/[&<>"]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function update(g){
    if (!g.isConnected || !g.querySelector('.sbs-cell[data-row]')) return;
    var b = band(g), top = g.getBoundingClientRect().top + EDGE;
    var ss = sides(g), parts = [], key = [], show = false, curRow = null;
    b.__refs = [];
    ss.forEach(function(side, si){
      var hs = heads(g, side), k = hs.length ? lastAbove(hs, top) : -1;
      if (k < 0){ parts.push(''); key.push(side + ':-'); b.__refs.push([]); return; }
      if (hs[k].getBoundingClientRect().bottom <= top) show = true;  // 그 머리가 이미 위로 지나갔다
      var p = pathOf(hs, k);
      if (si === 0) curRow = +hs[k].closest('.sbs-cell').getAttribute('data-row');
      b.__refs.push(p);
      key.push(side + ':' + k);
      parts.push(p.map(function(h, i){
        return '<span class="sbs-stick-c' + (i === p.length - 1 ? ' cur' : '') + '" data-s="' + si +
          '" data-i="' + i + '" title="' + esc(tx(h)) + '">' + esc(tx(h)) + '</span>';
      }).join('<span class="sbs-stick-s">›</span>'));
    });
    var k2 = key.join('|') + '|' + ss.join(',');
    if (b.__key !== k2){
      b.__key = k2;
      // 지금 섹션이 바뀌었다 — 하이라이트 목록이 그 섹션 묶음을 칠한다(§app._ANNO_HTML). 아래 칸(복제)은 빼고.
      if (!g.closest('.sbs-clone'))
        document.dispatchEvent(new CustomEvent('md4-cursec', {detail: {row: curRow}}));
      b.firstChild.innerHTML = parts.map(function(h, i){
        return '<div class="sbs-stick-p" lang="' + (ss[i] === 'ko' ? (window.__md4TrLang || 'ko') : 'en') +
          '">' + h + '</div>'; }).join('');
    }
    b.classList.toggle('on', show);
  }
  var pending = new Set(), raf = 0;
  function queue(g){
    pending.add(g);
    if (!raf) raf = requestAnimationFrame(function(){
      raf = 0;
      var gs = Array.from(pending); pending.clear();
      gs.forEach(update);
    });
  }
  function all(){ [].forEach.call(document.querySelectorAll('.sbs-grid'), queue); }
  document.addEventListener('scroll', function(ev){
    var g = ev.target;
    if (g && g.classList && g.classList.contains('sbs-grid')) queue(g);
  }, true);
  window.addEventListener('resize', all);
  // 경로 낱말 → 그 섹션 머리로 (읽던 자리를 쌓고, 그 칸 안에서만 옮긴다)
  document.addEventListener('click', function(ev){
    var c = ev.target.closest ? ev.target.closest('.sbs-stick-c') : null;
    if (!c) return;
    var b = c.closest('.sbs-stick'), g = b && b.parentElement;
    var h = b && b.__refs && (b.__refs[+c.getAttribute('data-s')] || [])[+c.getAttribute('data-i')];
    if (!g || !h || !h.isConnected) return;
    ev.preventDefault(); ev.stopPropagation();
    var J = window.%(jb)s;
    if (J) J.mark(h, 'start');
    var y = g.scrollTop + h.getBoundingClientRect().top - g.getBoundingClientRect().top - 4;
    g.scrollTo({top: Math.max(0, y), behavior: 'smooth'});
  }, true);
  window.%(api)s = {refresh: all};
})();
""" % {"api": STICK_API, "edge": JUMP_EDGE_PX, "jb": JUMP_BACK_API}


def refresh_js() -> str:
    """그리드를 새로 그린 뒤 — 스크롤 전에도 (복원된 자리에서) 띠를 맞춘다."""
    return f"setTimeout(function(){{window.{STICK_API}&&window.{STICK_API}.refresh();}}, 60)"


def install() -> None:
    """셸 페이지가 한 번 부른다 — 띠 CSS·스크립트 (그리드는 스크립트가 찾아 붙는다)."""
    from narani.ui import spa

    spa.add_css(CSS)
    spa.add_body_html(f"<script>{JS}</script>")
