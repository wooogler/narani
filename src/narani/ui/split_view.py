"""같은 논문을 위아래 두 뷰어로 — 위 칸은 읽던 자리를 지키고, 아래 칸에 이동한 곳을 띄운다.

뷰어 정렬 그리드(`.sbs-grid`)는 서버가 그린 NiceGUI 요소다. 아래 칸을 서버에서 한 번 더 그리면 렌더 비용과
이벤트 배선이 두 배가 되므로, **클라이언트에서 위 그리드를 복제**(cloneNode)해 같은 `.vwrap` 안 가로
나눔대 아래에 붙인다. 셀 안 상호작용(메모·수식·그림 확대·인용 툴팁·문장 짝)은 모두 document 위임
리스너라 복제본에서도 그대로 돈다. 복제본의 `id`는 지운다(`sec-…`·`pdfpage-…`·NiceGUI 요소 id 중복 방지).
위 그리드가 다시 그려지면(토글·번역 완료) 아래 칸도 다시 복제하고 보던 비율로 되돌린다.

정해 둔 것 (사용자 확인, 2026-10-26):
- **PDF 싱크는 마지막으로 손댄 칸이 이끈다**(휠·터치·클릭·키, 그리고 이동이 옮긴 칸). 이끄는 칸 위에 파란 줄.
  싱크 동작 자체(쪽 단위·33% 읽는 선·손으로 굴리면 4초 쉼)는 app.py `_pdf_sync_js` 그대로다.
- **하이라이트는 두 칸 모두** — 위 칸에 칠한 것을 아래 칸 같은 셀(행·쪽)에 똑같이 칠한다(app.py applyAll).
- **나눔 중의 이동은 아래 칸으로** — 섹션·목차·칩·참조·하이라이트 목록이 `__mdJumpBack.go`를 거치며
  여기 `route`가 위 칸 요소를 아래 칸의 같은 요소로 바꾼다. 위 칸은 읽던 자리 그대로.
- **나눔이 꺼져 있을 때 '같이 보기'** — 이동 뒤 뜨는 '읽던 자리로' 알약의 두 번째 단추(§ui/jump_back.py).
  아래 칸이 지금 본 곳을 띄우고 위 칸은 읽던 자리로 돌아간다.
- 찾기 바는 이끄는 칸에서만 찾는다(§ui/find_bar.py). 본문 폭 핸들은 두 칸이 같이 쓴다(같은 열).
- 나눔 on/off와 아래 칸 위치는 창 안(sessionStorage, 논문별)에만, 나눔 비율은 전역(localStorage)에 기억한다.
- 편집 모드·정렬이 안 된 단일 패널(`.vpane`)에서는 나눌 수 없다(아래 칸이 생기지 않는다).
"""

from __future__ import annotations

import json

SPLIT_API = "__md4Split"
MIN_PCT, MAX_PCT, DEF_PCT = 18, 82, 50   # 위 칸 높이(%) 범위 — JS의 MIN/MAX/DEF와 한 짝

CSS = """
/* 위아래 두 뷰어 — .vwrap을 세로 flex로 바꾸고 위 그리드 | 나눔대 | 아래 그리드(복제)를 쌓는다.
   본문 폭 핸들(.vbody-handle)·원문|번역 핸들(.vcol-handle)은 .vwrap 전체 높이라 두 칸에 함께 걸친다. */
.vwrap.vsplit-on { display: flex; flex-direction: column; }
.vwrap.vsplit-on > .sbs-grid:not(.sbs-clone) { height: auto; min-height: 60px;
  flex: 0 0 calc(var(--vsplit, 50%) - 5px); }
.vwrap.vsplit-on > .sbs-clone { height: auto; min-height: 60px; flex: 1 1 0; }
.vsplit-bar { position: relative; flex: 0 0 10px; cursor: row-resize; z-index: 4; }
.vsplit-bar::after { content: ''; position: absolute; left: 0; right: 0; top: 4px; height: 2px;
  background: #e6e4e0; transition: background .12s ease; }
.vsplit-bar:hover::after, .vsplit-bar.dragging::after { background: #2383e2; }
.vsplit-x { position: absolute; right: 14px; top: -7px; z-index: 5; display: inline-flex; align-items: center;
  gap: 3px; height: 22px; padding: 0 8px; border: 1px solid #e6e4e0; border-radius: 11px; background: #fff;
  color: #6b6862; font: inherit; font-size: 11px; cursor: pointer; box-shadow: 0 2px 6px rgba(0,0,0,.08); }
.vsplit-x:hover { color: #2383e2; border-color: #2383e2; }
/* 이끄는 칸(PDF 싱크·찾기 바가 따르는 칸) — 위끝에 얇은 파란 줄. 스크롤 상자의 inset 그림자는 따라 움직이지 않는다 */
.vwrap.vsplit-on > .sbs-grid.vlead { box-shadow: inset 0 2px 0 rgba(35,131,226,.75); }
/* 툴바 나눔 단추 — 켜져 있으면 파랗게 (상태는 클라이언트만 안다) */
body.md4-split-on .md4-split-btn { color: #2383e2 !important; background: rgba(35,131,226,.1); }
@media (prefers-color-scheme: dark) {
  .vsplit-bar::after { background: #3a3a3a; }
  .vsplit-x { background: #262626; border-color: #3a3a3a; color: #a0a0a0; }
}
"""

JS = r"""
(function(){
  if (window.__md4Split) return;
  var MIN = 18, MAX = 82, DEF = 50;
  var LS_PCT = 'md4:vsplit', SS = 'md4:split:';
  var paper = null, on = false, orig = null, clone = null, bar = null, wrap = null;
  var lead = 'a', ratioB = 0, timer = 0;

  function lsGet(k){ try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v){ try { localStorage.setItem(k, v); } catch (e) {} }
  function ssGet(){ try { return JSON.parse(sessionStorage.getItem(SS + paper) || 'null'); } catch (e) { return null; } }
  function ssSet(){
    if (!paper) return;
    try { sessionStorage.setItem(SS + paper, JSON.stringify({on: on, r: ratioB})); } catch (e) {}
  }
  function pct(){
    var v = parseFloat(lsGet(LS_PCT));
    return isNaN(v) ? DEF : Math.max(MIN, Math.min(MAX, v));
  }
  // 위 그리드 — 뷰어 정렬 그리드만(같은 클래스의 다른 상자는 data-row가 없다)
  function original(){
    var all = document.querySelectorAll('.sbs-grid:not(.sbs-clone)');
    for (var i = 0; i < all.length; i++)
      if (all[i].querySelector('[data-row]') && all[i].parentElement &&
          all[i].parentElement.classList.contains('vwrap')) return all[i];
    return null;
  }
  function maxTop(g){ return Math.max(0, g.scrollHeight - g.clientHeight); }
  function clean(c){
    c.classList.add('sbs-clone'); c.classList.remove('vlead');
    c.setAttribute('data-pane', 'b'); c.removeAttribute('id');
    [].forEach.call(c.querySelectorAll('[id]'), function(e){ e.removeAttribute('id'); });
    delete c.dataset.pdfsync;
    c.style.removeProperty('flex');
  }
  // where: {top: px} 이면 그 자리, {ratio: 0~1} 이면 아래 칸 높이에 맞춘 비율, 없으면 위 칸과 같은 자리
  function build(where){
    where = where || {};
    orig = original();
    if (!orig) return false;
    wrap = orig.parentElement;
    clone = orig.cloneNode(true);
    clean(clone);
    bar = document.createElement('div'); bar.className = 'vsplit-bar';
    bar.title = md4T('끌어서 위아래 칸 높이 조절 · 더블클릭하면 반반');
    var x = document.createElement('button'); x.type = 'button'; x.className = 'vsplit-x';
    x.innerHTML = '&#10005; ' + md4T('아래 칸 닫기');
    x.addEventListener('mousedown', function(ev){ ev.stopPropagation(); });
    x.addEventListener('click', function(){ api.close(); });
    bar.appendChild(x);
    orig.after(bar); bar.after(clone);
    wrap.style.setProperty('--vsplit', pct() + '%');
    wrap.classList.add('vsplit-on');
    document.body.classList.add('md4-split-on');
    function want(){
      if (where.ratio != null) return where.ratio * maxTop(clone);
      return where.top != null ? where.top : orig.scrollTop;
    }
    clone.scrollTop = want();
    // 레이아웃이 자리 잡은 뒤 한 번 더 — 처음엔 높이가 덜 잡혀 끝까지 못 내려갈 수 있다
    var c0 = clone;
    requestAnimationFrame(function(){ if (clone === c0) clone.scrollTop = want(); });
    clone.addEventListener('scroll', function(){
      var m = maxTop(clone); ratioB = m > 0 ? clone.scrollTop / m : 0;
      clearTimeout(timer); timer = setTimeout(ssSet, 300);
    }, {passive: true});
    if (orig.dataset.pdfsync && window.__md4PdfSyncBind) window.__md4PdfSyncBind(clone);
    setLead(lead, true);
    if (window.__mdAnnoApply) window.__mdAnnoApply();   // 하이라이트를 두 칸 모두에
    if (window.__md4BodyPlace) window.__md4BodyPlace();
    return true;
  }
  function teardown(){
    if (clone) clone.remove();
    if (bar) bar.remove();
    if (wrap) wrap.classList.remove('vsplit-on');
    if (orig) orig.classList.remove('vlead');
    var had = !!clone;
    clone = bar = null;
    document.body.classList.remove('md4-split-on');
    // 아래 칸에 쌓인 읽던 자리는 돌아갈 칸이 없다 — 알약에서 뺀다 (다시 복제하는 재렌더 땐 남는다)
    if (had && !on && window.__mdJumpBack && window.__mdJumpBack.dropPane) window.__mdJumpBack.dropPane('b');
  }
  function setLead(x, force){
    if (!clone) x = 'a';
    var changed = x !== lead;
    lead = x;
    if (orig) orig.classList.toggle('vlead', !!clone && lead === 'a');
    if (clone) clone.classList.toggle('vlead', lead === 'b');
    if ((changed || force) && clone){       // 이끄는 칸이 바뀌었다 — PDF를 그 칸의 쪽으로
      var g = lead === 'b' ? clone : orig;
      if (g && g.__md4PdfMove) g.__md4PdfMove();
    }
  }
  // 위 그리드가 다시 그려졌는지(토글·번역 완료·편집 끄기) 지켜보다가 아래 칸을 다시 복제한다
  var stable = 0, lastRows = -1;
  setInterval(function(){
    if (!on) return;
    if (clone && orig && orig.isConnected && clone.isConnected) return;
    var g = original();
    if (!g){ if (clone) teardown(); return; }   // 편집 모드·PDF만 — 칸이 돌아오면 다시
    var n = g.querySelectorAll('[data-row]').length;
    stable = n === lastRows ? stable + 1 : 0; lastRows = n;
    if (stable < 1) return;                    // 반쯤 그려진 그리드를 복제하지 않게 한 번 더 본다
    teardown();
    build({ratio: ratioB});
  }, 350);
  // 마지막으로 손댄 칸이 이끈다
  function touch(ev){
    if (!clone || !ev.target || !ev.target.closest) return;
    var g = ev.target.closest('.sbs-grid');
    if (g === clone) setLead('b'); else if (g === orig) setLead('a');
  }
  ['wheel', 'touchstart', 'mousedown', 'keydown'].forEach(function(n){
    window.addEventListener(n, touch, {capture: true, passive: true});
  });
  // 나눔대 끌기 — 위 칸 높이(%)
  var drag = null;
  document.addEventListener('mousedown', function(ev){
    if (!bar || ev.target !== bar || ev.button !== 0) return;
    ev.preventDefault(); drag = {r: wrap.getBoundingClientRect()};
    bar.classList.add('dragging'); document.body.style.userSelect = 'none';
  });
  window.addEventListener('mousemove', function(ev){
    if (!drag || !wrap) return;
    var p = Math.max(MIN, Math.min(MAX, (ev.clientY - drag.r.top) / drag.r.height * 100));
    wrap.style.setProperty('--vsplit', p.toFixed(1) + '%'); drag.p = p;
  });
  window.addEventListener('mouseup', function(){
    if (!drag) return;
    if (drag.p) lsSet(LS_PCT, drag.p.toFixed(1));
    drag = null; if (bar) bar.classList.remove('dragging'); document.body.style.userSelect = '';
  });
  document.addEventListener('dblclick', function(ev){
    if (!bar || ev.target !== bar) return;
    lsSet(LS_PCT, String(DEF)); wrap.style.setProperty('--vsplit', DEF + '%');
  });
  // 위 칸 요소 → 아래 칸의 같은 요소 (그리드에서 내려가는 자식 순번 경로를 따라간다)
  function twin(el){
    if (!clone || !orig || !el || !orig.contains(el)) return null;
    var path = [];
    for (var n = el; n && n !== orig; n = n.parentElement)
      path.unshift([].indexOf.call(n.parentElement.children, n));
    var t = clone;
    for (var i = 0; i < path.length && t; i++) t = t.children[path[i]];
    return t || null;
  }

  var api = {
    isOn: function(){ return !!clone; },
    available: function(){ return !!original(); },
    // open({top, lead}) — top: 아래 칸 scrollTop(없으면 위 칸과 같은 자리)
    open: function(o){
      o = o || {};
      on = true; teardown();
      if (o.lead) lead = o.lead;
      if (!build({top: o.top != null ? o.top : null})){ ssSet(); return false; }
      var m = maxTop(clone); ratioB = m > 0 ? clone.scrollTop / m : 0;
      ssSet(); return true;
    },
    close: function(){ on = false; lead = 'a'; teardown(); ssSet(); },
    toggle: function(){ return clone ? api.close() : api.open({lead: 'b'}); },
    // 이끄는 칸 — PDF 싱크가 묻는다. 나눔이 꺼져 있으면 위 칸(복제가 아닌 것)만
    leads: function(sc){
      if (!clone) return !(sc.classList && sc.classList.contains('sbs-clone'));
      return sc === (lead === 'b' ? clone : orig);
    },
    activeGrid: function(){ return clone && lead === 'b' ? clone : original(); },
    grid: function(p){ return p === 'b' ? clone : original(); },
    paneOf: function(el){ return clone && el && clone.contains(el) ? 'b' : 'a'; },
    // 나눔 중의 이동은 아래 칸으로 — 위 칸 요소면 아래 칸의 같은 요소, 그 밖엔 그대로
    route: function(el){ return (clone && twin(el)) || el; },
    touched: function(el){ if (clone && el) setLead(clone.contains(el) ? 'b' : 'a'); },
    setPaper: function(tok){
      if (tok === paper) return;
      teardown(); paper = tok; lead = 'a'; ratioB = 0;
      var st = tok ? ssGet() : null;
      on = !!(st && st.on); ratioB = (st && st.r) || 0;
      stable = 0; lastRows = -1;               // 지켜보기가 그리드가 붙는 대로 다시 만든다
    }
  };
  window.__md4Split = api;
})();
"""


def init_js(paper_key: str | None) -> str:
    """화면이 바뀔 때 — 나눔은 그 논문의 것(창 안 기억)이라 논문이 바뀌면 그 논문의 상태로."""
    return f"window.{SPLIT_API}&&window.{SPLIT_API}.setPaper({json.dumps(paper_key)});"


def install() -> None:
    """셸 페이지가 한 번 부른다."""
    from narani.ui import spa

    spa.add_css(CSS)
    spa.add_body_html(f"<script>{JS}</script>")
