"""'읽던 자리로' — 뷰어 본문을 프로그램이 옮길 때 떠나기 전 자리를 쌓아 두고 알약 하나로 되돌린다.

옛 챗봇 서랍(에이전트 창)의 인용 칩 전용이던 것을 뷰어 전체로 넓혔다. 칩만 자리를 집고 섹션·목차
클릭은 집지 않아서, "출처를 보던 중 섹션을 누르면" 돌아올 자리가 칩 전의 자리로 남고(보던 출처를
잃는다), 그 뒤 칩을 또 누르면 섹션 착지를 '사용자가 옮겨 가 읽던 자리'로 오해해 원래 자리마저 잃었다.

**규칙은 하나다.** 본문을 옮기는 모든 길(섹션 트리·목차·인용 칩·메모 근거·그림/섹션 참조·찾기 바·
하이라이트 목록·용어 예문)은 스크롤을 시작하기 **직전**에 `window.__mdJumpBack.mark(el, block)`을
부른다. mark는 지금 화면 맨 위 자리를 구조(행 → 쪽 → 블록 → 블록 안 비율)로 집어 **쌓는다**.
알약을 누르면 맨 위 것으로 돌아가고, 더 쌓여 있으면 알약이 남아 그 이전 자리를 가리킨다
(브라우저 '뒤로'와 같은 감각). 예외 하나 — 지난 이동의 착지에 손대지 않고(휠·키·터치 없이)
`HOP_MS` 안에 다시 뛰면 같은 이동의 연장(칩을 연달아 누르기·찾기 바 다음 결과)으로 보고 쌓지 않는다.

- 이미 화면 안으로 가는 이동(이동량 < max(ARM_MIN, 화면 × ARM))은 자리를 잃지 않으니 쌓지 않는다.
- 사용자가 스스로 맨 위 자리 가까이(HOME) 돌아오면 그 자리는 할 일이 없어 내려놓는다.
- 논문이 바뀌면(`setPaper`) 다 비운다 — 자리는 그 논문의 것이다.
- 본문 스크롤 상자(`.sbs-grid`·`.vpane`) 밖으로 가는 이동(편집기·왼쪽 패널)은 쌓지 않는다.

셸에 한 번 설치한다(app.py `shell()`). 알약은 body 직속이라 서랍을 닫아도, 뷰어가 다시 그려져도 남는다.
"""

from __future__ import annotations

import json

JUMP_BACK_ID = "md-jump-back"    # body 직속 오버레이 id
JUMP_BACK_Z = 8500               # 본문 위, 서랍(9000)·메모 카드(10003)·찾기 바 아래
JUMP_BACK_API = "__mdJumpBack"   # 본문을 옮기는 모든 길이 부르는 전역 이름
JUMP_EDGE_PX = 6                 # 기준선 = 컨테이너 위끝 + 6px. app.py PDF 싱크(top+6)와 같은 정의라
                                 # "화면 맨 위가 읽던 자리"가 뷰어 전체에서 한 뜻이 된다
HOP_MS = 4000                    # 착지에 손대지 않고 이 안에 다시 뛰면 같은 이동의 연장 (쌓지 않는다)
DEPTH = 6                        # 쌓아 두는 자리 상한 — 넘치면 가장 오래된 것부터 버린다

CSS = """
/* '읽던 자리로' 알약 — 본문 칸 아래 가운데에 파랗게 뜬다. 예전엔 오른쪽 아래 흰 알약이라 본문과
   섞여 안 보였고, 그 구석은 새 버전 알림(update_banner)과도 겹친다.
   left/bottom은 JS가 본문 스크롤 컨테이너를 재서 넣는다 — CSS는 모양만 정한다. */
:root { --mjb-ring: #8a8780; }
#md-jump-back { position: fixed; z-index: 8500; left: 50%; bottom: 24px; display: none;
  align-items: stretch; max-width: 420px; background: #2383e2; color: #fff;
  border-radius: 999px; box-shadow: 0 8px 24px rgba(35,131,226,.38), 0 2px 6px rgba(0,0,0,.14);
  font-size: 13px; overflow: hidden; }
#md-jump-back.on { display: inline-flex; animation: mdJumpIn .2s ease-out; }
body:has(.q-dialog) #md-jump-back { z-index: 5990; }   /* 대화상자 밑으로 (에이전트 서랍과 같은 규칙) */
#md-jump-back button { border: none; background: none; color: inherit; font: inherit;
  cursor: pointer; padding: 8px 14px; line-height: 1.4; white-space: nowrap; }
#md-jump-back .mjb-go { display: inline-flex; align-items: center; gap: 7px; min-width: 0;
  font-weight: 600; padding-left: 12px; }
#md-jump-back .mjb-go:hover { background: #1b74cf; }
#md-jump-back .mjb-i { display: inline-flex; width: 20px; height: 20px; align-items: center;
  justify-content: center; border-radius: 50%; background: rgba(255,255,255,.22); font-size: 13px; }
#md-jump-back .mjb-w { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-weight: 400; color: rgba(255,255,255,.82); min-width: 0; }
/* 더 이전 자리가 남아 있으면 몇 곳인지 — 한 번 더 누르면 그리로 */
#md-jump-back .mjb-n { flex: 0 0 auto; min-width: 18px; height: 18px; padding: 0 5px;
  border-radius: 9px; background: #fff; color: #2383e2; font-size: 11px; font-weight: 700;
  line-height: 18px; text-align: center; }
#md-jump-back .mjb-n:empty { display: none; }
/* 같이 보기 — 나눔이 꺼져 있을 때만(can-split, JS가 붙인다). 알약 안 두 번째 동작 */
#md-jump-back .mjb-split { display: none; font-weight: 600; padding-left: 11px; padding-right: 12px;
  border-left: 1px solid rgba(255,255,255,.28); }
#md-jump-back.can-split .mjb-split { display: inline-flex; align-items: center; gap: 4px; }
#md-jump-back .mjb-split:hover { background: #1b74cf; }
#md-jump-back .mjb-x { font-size: 12px; padding: 8px 12px 8px 10px; color: rgba(255,255,255,.85);
  border-left: 1px solid rgba(255,255,255,.28); }
#md-jump-back .mjb-x:hover { background: #1b74cf; color: #fff; }
#md-jump-back button:focus-visible { outline: 2px solid #fff; outline-offset: -3px; }
@keyframes mdJumpIn { 0% { opacity: 0; transform: translateY(10px) scale(.96); }
  70% { opacity: 1; transform: translateY(-2px) scale(1.02); }
  100% { opacity: 1; transform: translateY(0) scale(1); } }
/* 도착 표시 — 근거 문단의 파란 chat-cite-flash와 다른 무채색 테두리.
   "여기가 근거다"와 "여기로 돌아왔다"는 다른 말이다. flash()가 1400ms에 클래스를 떼므로 1.3s. */
.mjb-flash { animation: mdJumpFlash 1.3s ease-out; }
@keyframes mdJumpFlash { 0%, 55% { outline: 2px solid var(--mjb-ring, #8a8780);
    outline-offset: 2px; }
  100% { outline-color: transparent; } }
@media (prefers-reduced-motion: reduce) { #md-jump-back.on { animation: none; } }
@media (prefers-color-scheme: dark) {
  :root { --mjb-ring: #a9a69f; }
  #md-jump-back { background: #2f7fd6; color: #fff; box-shadow: 0 8px 24px rgba(0,0,0,.55); }
  #md-jump-back .mjb-go:hover, #md-jump-back .mjb-x:hover, #md-jump-back .mjb-split:hover { background: #2a71bf; }
  #md-jump-back .mjb-n { background: #e8f1fc; color: #1f63b0; }
}
"""

HTML = """
<div id="md-jump-back" role="status" aria-live="polite">
  <button type="button" class="mjb-go" aria-label="⦃읽던 자리로 돌아가기⦄"
    title="⦃이동하기 전 읽던 자리로 돌아갑니다⦄"><span class="mjb-i"
    aria-hidden="true">&#8593;</span><span>⦃읽던 자리로⦄</span><span class="mjb-w"></span><span
    class="mjb-n"></span></button>
  <button type="button" class="mjb-split" aria-label="⦃같이 보기 — 아래 칸에 이 자리, 위 칸은 읽던 자리⦄"
    title="⦃같이 보기 — 아래 칸에 지금 본 곳을 띄우고 위 칸은 읽던 자리로⦄"><span
    aria-hidden="true">&#8693;</span> ⦃같이 보기⦄</button>
  <button type="button" class="mjb-x" aria-label="⦃되돌아가기 버튼 닫기⦄"
    title="⦃닫기 (Esc)⦄">&#10005;</button>
</div>
"""

JS = r"""
(function(){
  if (window.__mdJumpBack) return;
  var jbEl = document.getElementById('md-jump-back');
  if (!jbEl) return;
  var JB_EDGE = 6;            // 기준선 = 컨테이너 위끝 + 6 (app.py PDF 싱크 top+6과 같은 정의)
  var JB_ARM = 0.30, JB_ARM_MIN = 120;   // 이만큼 움직일 이동에만 자리를 쌓는다
  var JB_HOME = 0.22, JB_HOME_MIN = 80;  // 이만큼 가까워지면 그 자리는 할 일이 없다 (쌓는 임계보다
                                         //  작다 = 히스테리시스. 뜨자마자 사라지지 않는다)
  var JB_GRACE = 700;         // 나가는 이동이 멎고 이만큼 지날 때까지 자동 내려놓기를 미룬다
                              //  (긴 논문의 smooth 스크롤은 2초를 넘는다 — 움직이는 동안은 늘 연장)
  var JB_HOP_MS = 4000, JB_DEPTH = 6;
  var JB_BLOCKS = 'p,li,h1,h2,h3,h4,h5,h6,pre,blockquote,table,figure,img';
  var MAC = /Mac|iPhone|iPad/.test(navigator.platform || '');
  // stack: 쌓인 자리(맨 끝이 다음에 돌아갈 곳). land: 지난 이동의 착지 — 손댔는지(touched)로 연장을 가른다
  var stack = [], land = null, jbG = null, jbRaf = 0, jbFrames = 0, jbKey = '', jbJumpAt = 0;
  var jbDir = '', jbLastY = -1;

  function jbScroller(from){
    if (from && from.closest){
      var c = from.closest('.sbs-grid, .vpane');     // 실측: computed overflow 조상 탐색과 동일
      return c || null;                              // 본문 밖(편집기·왼쪽 패널)으로 가는 이동은 null
    }
    if (jbG && jbG.isConnected && jbG.clientHeight > 0) return jbG;  // 재렌더까지는 그대로 쓴다
    jbG = null;
    var all = document.querySelectorAll('.sbs-grid:not(.sbs-clone), .vpane');
    for (var i = 0; i < all.length; i++){
      var r = all[i].getBoundingClientRect();
      if (r.width < 40 || r.height < 40) continue;
      // [data-row]가 캔버스의 정렬 그리드를 가려낸다 — 같은 클래스를 쓰는 다른 상자(data-row 없음)를
      // 잡지 않게. app.py가 바뀌어도 버티게 남긴다.
      if (all[i].matches('[data-row]') || all[i].querySelector('[data-row]')){
        jbG = all[i]; break;
      }
    }
    return jbG;
  }
  // 자리가 든 칸의 스크롤 상자 — 위아래 두 뷰어(§ui/split_view.py)면 아래 칸('b')일 수 있다
  function paneGrid(pane){
    var sp = window.__md4Split;
    if (pane === 'b') return (sp && sp.grid('b')) || null;
    return jbScroller(null);
  }
  function topGrid(){ var p = stack[stack.length - 1]; return paneGrid(p ? p.pane : 'a'); }
  function jbNorm(s){ return (s || '').replace(/\s+/g, ' ').trim(); }
  function jbCells(g, row){
    var sel = '[data-row="' + row + '"]';
    var self = (g.matches && g.matches(sel)) ? [g] : [];   // .vpane은 g.querySelector로 안 잡힌다
    return self.concat([].slice.call(g.querySelectorAll(sel)));
  }
  function headingOf(g, row){            // 그 행 앞쪽에서 가장 가까운 헤더 (알약 라벨용)
    var cells = [].slice.call(g.querySelectorAll('[data-row]'));
    var i = -1;
    for (var k = 0; k < cells.length; k++)
      if (cells[k].getAttribute('data-row') === String(row)) { i = k; break; }
    for (var j = i; j >= 0; j--){
      var h = cells[j].querySelector('h1,h2,h3,h4,h5,h6');
      if (h) return jbNorm(h.textContent);
    }
    return '';
  }
  // 지금 화면 맨 위에 걸린 자리를 구조로 집는다. 참조(노드)는 담지 않는다 — 뷰어는 패널을 토글할
  // 때마다 그리드를 통째로 다시 만들므로, 붙들어 둔 노드는 조용히 죽은 노드가 된다.
  function jbCapture(g){
    var edge = g.getBoundingClientRect().top + JB_EDGE;
    var max = Math.max(0, g.scrollHeight - g.clientHeight);
    var els = (g.matches && g.matches('[data-row]') ? [g] : [])
      .concat([].slice.call(g.querySelectorAll('[data-row]')));
    var band = null, i, r;
    for (i = 0; i < els.length; i++){
      r = els[i].getBoundingClientRect();
      if (r.top > edge) break;                  // 아직 기준선 아래 — 문서 순서라 여기서 끝
      var key = els[i].getAttribute('data-row');
      // 한 행의 두 셀은 같은 grid row라 top이 같고 DOM에서도 붙어 있다 → 연속 묶음이면 한 밴드
      if (!band || band.row !== key) band = {row: key, cs: [], t: r.top, b: r.bottom};
      band.cs.push(els[i]);
      band.t = Math.min(band.t, r.top); band.b = Math.max(band.b, r.bottom);
    }
    if (!band) return null;
    // 쪽 고르기: 기준선 아래로 내용이 이어지는 셀만, 그중 원문(en) 우선. 짧은 쪽 셀을 잡아
    // 비율이 늘 1이 되는 사고를 막고, 재번역에도 원문 텍스트가 더 잘 버틴다.
    var lv = [];
    for (i = 0; i < band.cs.length; i++)
      if (band.cs[i].getBoundingClientRect().bottom > edge) lv.push(band.cs[i]);
    var pool = lv.length ? lv : band.cs, cell = pool[0];
    for (i = 0; i < pool.length; i++)
      if (pool[i].getAttribute('data-side') === 'en'){ cell = pool[i]; break; }
    // 블록 하위 앵커 — 실측으로 한 셀이 2440px·17블록까지 간다. 행 단위로만 집으면 최대 3화면
    // 어긋난다. 폭이 바뀌어 줄바꿈이 전부 달라져도 블록 안 비율은 버틴다.
    var bs = [].slice.call(cell.querySelectorAll(JB_BLOCKS));
    var bi = 0, bf = 0, head = '';
    for (i = 0; i < bs.length; i++)
      if (bs[i].getBoundingClientRect().top <= edge) bi = i;
    if (bs.length){
      var br = bs[bi].getBoundingClientRect();
      bf = br.height > 0 ? Math.max(0, Math.min(1, (edge - br.top) / br.height)) : 0;
      head = jbNorm(bs[bi].textContent).slice(0, 72);
    }
    var span = band.b - band.t;
    var rf = span > 0 ? Math.max(0, Math.min(1, (edge - band.t) / span)) : 0;
    var row = parseInt(band.row, 10) || 0;
    var pv = document.querySelector('.vpdf');
    return {pane: g.classList.contains('sbs-clone') ? 'b' : 'a', row: row, side: cell.getAttribute('data-side') || '', bi: bi, bf: bf, rf: rf,
            ratio: max > 0 ? g.scrollTop / max : 0,   // app.py 복원과 같은 통화 (최후 폴백)
            head: head, sec: headingOf(g, row), at: Date.now(), y: g.scrollTop,
            pdf: pv ? pv.scrollTop : null, pdfH: pv ? pv.scrollHeight : null};
  }
  // scrollIntoView({block})가 만들 이동량 (범위 클램프). 섹션·목차는 'start', 칩·찾기는 'center'.
  function jbNeed(g, el, block){
    var gr = g.getBoundingClientRect(), er = el.getBoundingClientRect();
    var want = block === 'start' ? (er.top - gr.top)
      : (er.top - gr.top) - Math.max(0, (g.clientHeight - er.height) / 2);
    var lo = -g.scrollTop, hi = (g.scrollHeight - g.clientHeight) - g.scrollTop;
    return Math.max(lo, Math.min(hi, want));
  }
  function near(g, a, b){ return Math.abs(a - b) < Math.max(JB_HOME_MIN, g.clientHeight * JB_HOME); }
  function hop(){                 // 지난 착지에 손대지 않고 곧바로 다시 뛰었나 — 같은 이동의 연장
    return !!(land && stack.length && !land.touched && Date.now() - land.at < JB_HOP_MS);
  }
  function jbMark(target, block){
    if (!target) return false;
    var g = jbScroller(target);
    if (!g) return false;
    var need = jbNeed(g, target, block || 'center');
    // 이미 화면에 있는 곳으로 가는 이동은 자리를 잃지 않았으니 쌓지 않는다 — 소음이다
    if (Math.abs(need) < Math.max(JB_ARM_MIN, g.clientHeight * JB_ARM)) return false;
    if (!hop()){
      var p = jbCapture(g);
      if (!p) return false;
      var top = stack[stack.length - 1];
      // 같은 자리를 두 번 쌓지 않는다 (돌아왔다 다시 떠나는 경우)
      if (top && near(g, jbTop(g, top), g.scrollTop)) stack[stack.length - 1] = p;
      else stack.push(p);
      if (stack.length > JB_DEPTH) stack.shift();
    }
    // expect: 이 이동이 놓아둘 자리. 서버를 왕복한 smooth 스크롤은 1초 넘게 늦게 출발하기도 해서,
    // 시간만 재면 출발 전 '아직 제자리'를 '스스로 돌아왔다'로 오해해 자리를 내려놓는다
    land = {at: Date.now(), touched: false, expect: g.scrollTop + need, arrived: false};
    jbJumpAt = Date.now();
    jbLabel(g, g.scrollTop + need);      // 화살표는 착지 기준 — 스크롤은 아직 시작 전이다
    if (!jbRaf) jbRaf = requestAnimationFrame(jbTick);
    return true;
  }
  function canSplit(p){
    var sp = window.__md4Split;
    return !!(p && sp && !sp.isOn() && sp.available() && p.pane !== 'b');
  }
  function jbArrow(dir){                 // 읽던 자리가 위인지 아래인지
    if (dir === jbDir) return;
    jbDir = dir;
    jbEl.querySelector('.mjb-i').innerHTML = dir === 'down' ? '&#8595;' : '&#8593;';
  }
  function jbLabel(g, yNow){
    var p = stack[stack.length - 1];
    if (!p) return;
    if (g) jbArrow(jbTop(g, p) > yNow ? 'down' : 'up');
    var go = jbEl.querySelector('.mjb-go');
    jbEl.querySelector('.mjb-w').textContent = p.sec ? '· ' + p.sec.slice(0, 34) : '';
    jbEl.querySelector('.mjb-n').textContent = stack.length > 1 ? String(stack.length) : '';
    // '같이 보기' — 나눔이 꺼져 있고 위 칸의 자리일 때만: 아래 칸에 지금 본 곳, 위 칸은 읽던 자리로
    var sp = window.__md4Split;
    jbEl.classList.toggle('can-split', canSplit(p));
    var key = MAC ? '⌥←' : 'Alt+←';
    go.title = md4T('이동하기 전 읽던 자리로 돌아갑니다') + ' (' + key + ')'
      + (p.sec ? '\n' + p.sec : '') + (p.head ? '\n“' + p.head.slice(0, 60) + '”' : '')
      + (stack.length > 1 ? '\n' + md4T('그 전 자리 {n}곳이 더 남아 있습니다', {n: stack.length - 1}) : '');
    go.setAttribute('aria-label', md4T('읽던 자리로 돌아가기') + (p.sec ? ' — ' + p.sec : ''));
    jbKey = '';                          // 다음 프레임에 좌표를 새로 쓰게
  }
  function jbTarget(g, p){
    var cs = jbCells(g, p.row), cell = null, i;
    for (i = 0; i < cs.length; i++) if (cs[i].getAttribute('data-side') === p.side) cell = cs[i];
    cell = cell || cs[0] || null;
    if (cell){
      var bs = [].slice.call(cell.querySelectorAll(JB_BLOCKS));
      if (bs.length){
        var el = bs[Math.min(p.bi, bs.length - 1)];
        var same = cell.getAttribute('data-side') === p.side;
        // 같은 쪽이면 텍스트로 검증한다 — 재번역으로 행 인덱스가 밀렸을 때 조용히 틀린 자리로
        // 가는 것이 비율 폴백보다 더 나쁘다(티가 안 난다). 다른 쪽 셀이면 검증하지 않는다:
        // 그 짝은 같은 문장의 번역이라 텍스트가 당연히 다르다.
        if (!same || !p.head || jbNorm(el.textContent).slice(0, 40) === p.head.slice(0, 40))
          return {el: el, f: p.bf};
      }
    }
    if (p.head){                       // 행 인덱스가 밀렸다 → 텍스트로 다시 찾는다
      var pre = p.head.slice(0, 40), hit = null;
      var all = g.querySelectorAll(JB_BLOCKS);
      for (i = 0; i < all.length; i++){
        if (jbNorm(all[i].textContent).indexOf(pre) === 0){ hit = all[i]; break; }
      }
      if (hit) return {el: hit, f: p.bf};
    }
    if (cell && cell !== g) return {el: cell, f: p.rf};   // 빈 셀·표만 있는 셀 → 행 밴드 비율
    return null;                                          // → 호출자가 p.ratio로 떨어진다
  }
  // 앵커의 위끝이 edge - f*height에 오면 그 화면 한 장이 재현된다. offsetTop은 쓰지 않는다 —
  // 셀은 grid item이고 마크다운은 래퍼 한 겹 안이라 offset 부모 사슬이 렌더마다 같지 않다.
  function jbTop(g, p, t){
    var max = Math.max(0, g.scrollHeight - g.clientHeight);
    t = t || jbTarget(g, p);
    if (!t) return max > 0 ? p.ratio * max : 0;
    var er = t.el.getBoundingClientRect();
    var y = g.scrollTop + (er.top - g.getBoundingClientRect().top) - JB_EDGE + t.f * er.height;
    return Math.max(0, Math.min(max, y));
  }
  function jbPlace(){
    var g = topGrid(), r = g && g.getBoundingClientRect();
    if (!g || !r || r.width < 160 || r.height < 120){ jbEl.classList.remove('on'); return null; }
    // 컨테이너 rect를 재기 때문에 서랍 폭·본문 padding·목차 너비·텍스트|PDF 분할을 하나도 몰라도
    // 언제나 '지금 읽는 칸'의 아래 가운데에 온다.
    // 예외 하나 — 좁은 창의 오버레이 모드(body.ag-overlay)에선 서랍이 본문 위에 떠 있어 컨테이너가
    // 서랍 밑까지 뻗는다. 그때는 서랍 왼끝을 보이는 오른끝으로 삼아 알약이 서랍에 가리지 않게 한다.
    var vr = r.right, ag = document.getElementById('md4-agent');
    if (document.body.classList.contains('ag-overlay') && ag && ag.classList.contains('open'))
      vr = Math.min(vr, ag.getBoundingClientRect().left);
    jbEl.style.maxWidth = Math.max(200, Math.min(420, vr - r.left - 28)) + 'px';
    if (!jbEl.classList.contains('on')) jbEl.classList.add('on');
    var cs = canSplit(stack[stack.length - 1]);        // 툴바에서 나눔을 켜고 껐을 수 있다
    if (cs !== jbEl.classList.contains('can-split')){ jbEl.classList.toggle('can-split', cs); jbKey = ''; }
    var w = jbEl.offsetWidth;
    var left = Math.max(r.left + 8, Math.min(vr - w - 8, (r.left + vr) / 2 - w / 2));
    var bottom = Math.max(14, window.innerHeight - r.bottom + 22);
    var key = Math.round(left) + '|' + Math.round(bottom);
    if (key !== jbKey){                 // 값이 바뀔 때만 써서 레이아웃을 흔들지 않는다
      jbKey = key;
      jbEl.style.left = left + 'px'; jbEl.style.bottom = bottom + 'px';
    }
    return g;
  }
  // rAF 하나가 배치와 내려놓기를 다 맡는다 — 재렌더·splitter·서랍 폭·목차·리사이즈·탭 이동을 전부
  // 흡수하고, 탭이 숨으면 저절로 멈춘다. resize/scroll 리스너나 MutationObserver를 늘리지 않는다.
  function jbTick(){
    jbRaf = 0;
    if (!stack.length) return;
    var g = jbPlace();
    if (g && Math.abs(g.scrollTop - jbLastY) > 1){   // 아직 움직인다 → 유예를 늘린다
      if (Date.now() - jbJumpAt < JB_GRACE) jbJumpAt = Date.now();
      jbLastY = g.scrollTop;
    }
    if (g && land && !land.arrived &&    // 착지에 닿거나, 손을 댔거나, 끝내 안 오면 그때부터 유예를 잰다
        (near(g, g.scrollTop, land.expect) || land.touched || Date.now() - land.at > JB_HOP_MS)){
      land.arrived = true; jbJumpAt = Date.now();
    }
    // 프레임마다 rect 1~2회. 무거운 일(앵커 재해석)은 12프레임(≈200ms)마다 한 번.
    if (g && (++jbFrames % 12 === 0) && (!land || land.arrived) && Date.now() - jbJumpAt > JB_GRACE){
      var y = jbTop(g, stack[stack.length - 1]);
      // 스스로 그 자리로 돌아왔으면 그 자리는 할 일이 없다 ("읽던 자리로"가 거짓말이 된다)
      if (near(g, g.scrollTop, y)){
        stack.pop();
        if (!stack.length) return jbDismiss();
        jbLabel(g, g.scrollTop);
      } else jbArrow(y > g.scrollTop ? 'down' : 'up');
    }
    jbRaf = requestAnimationFrame(jbTick);
  }
  function jbAfter(g, p){
    g.dispatchEvent(new Event('scroll'));   // PDF 싱크 프라이밍 (app.py의 복원과 같은 손짓)
    var pv = document.querySelector('.vpdf');
    if (!pv || p.pdf == null || pv.scrollHeight !== p.pdfH) return;   // lazy 이미지로 좌표계가 변했다
    // 싱크는 페이지 위끝까지만 맞춘다 — 페이지 안에서 보던 위치까지 되돌린다.
    // 싱크의 rAF 콜백이 지난 뒤에 써야 덮이지 않는다.
    requestAnimationFrame(function(){
      requestAnimationFrame(function(){ pv.scrollTop = p.pdf; });
    });
  }
  function jbBack(){
    var p = stack[stack.length - 1], g = p ? paneGrid(p.pane) : null;
    if (!p) return;
    if (!g) return;                    // 본문이 없다(다른 탭·PDF 전용) → 아무것도 하지 않고 자리를 지킨다
    stack.pop();
    var t = jbTarget(g, p), y = jbTop(g, p, t);
    var far = Math.abs(y - g.scrollTop) > g.clientHeight * 4;
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    var soft = !far && !reduce;        // 먼 거리 smooth는 느리고 멀미난다
    g.scrollTo({top: y, behavior: soft ? 'smooth' : 'auto'});
    if (t && t.el !== g && t.el.classList){
      var el = t.el;
      el.classList.remove('mjb-flash'); void el.offsetWidth; el.classList.add('mjb-flash');
      setTimeout(function(){ el.classList.remove('mjb-flash'); }, 1400);
    }
    var n = 0, last = -1;
    setTimeout(function nudge(){       // 늦게 뜬 그림·KaTeX가 위쪽을 밀면 좌표가 달라진다
      if (last >= 0 && Math.abs(g.scrollTop - last) > 24) return jbAfter(g, p);  // 사용자가 굴렸다
      var y2 = jbTop(g, p);
      if (Math.abs(g.scrollTop - y2) > 4) g.scrollTop = y2;
      last = g.scrollTop;
      if (++n < 3) setTimeout(nudge, 180); else jbAfter(g, p);
    }, soft ? 420 : 60);
    land = null;                       // 돌아온 자리는 '읽던 자리' — 다음 이동은 여기를 쌓는다
    jbJumpAt = Date.now();             // 되돌아가는 스크롤이 멎기 전에 다음 자리를 내려놓지 않게
    if (!stack.length) return jbDismiss();
    jbLabel(g, y);
  }
  // 본문을 옮기는 모든 길의 입구 — 나눔 중이면 아래 칸의 같은 요소로 바꿔(위 칸은 읽던 자리 그대로)
  // 자리를 쌓고 그리로 스크롤한다. 실제로 옮긴 요소를 돌려준다(깜빡임을 그 칸에 주려고).
  function jbGo(el, block, behavior){
    if (!el) return null;
    var sp = window.__md4Split;
    if (sp) el = sp.route(el);
    jbMark(el, block || 'center');
    if (sp) sp.touched(el);              // 옮긴 칸이 이끈다(PDF가 그 칸의 쪽으로)
    el.scrollIntoView({behavior: behavior || 'smooth', block: block || 'center'});
    return el;
  }
  // '같이 보기' — 아래 칸이 지금 본 곳(이동한 곳)을 띄우고 위 칸은 읽던 자리로 돌아간다
  function jbSplit(){
    var sp = window.__md4Split, p = stack[stack.length - 1], g = paneGrid('a');
    if (!sp || !p || !g) return;
    if (!sp.open({top: g.scrollTop, lead: 'b'})) return;
    jbBack();
  }
  function jbDismiss(){
    stack = []; land = null; jbKey = ''; jbFrames = 0; jbDir = '';
    jbEl.classList.remove('on');
    jbEl.querySelector('.mjb-i').innerHTML = '&#8593;';
    if (jbRaf){ cancelAnimationFrame(jbRaf); jbRaf = 0; }
  }
  jbEl.querySelector('.mjb-go').addEventListener('click', jbBack);
  jbEl.querySelector('.mjb-x').addEventListener('click', jbDismiss);
  jbEl.querySelector('.mjb-split').addEventListener('click', jbSplit);
  // fixed 알약 위에서 굴리면 밑의 그리드가 안 움직인다(가장 가까운 스크롤 조상이 body다).
  // 넘겨주지 않으면 "읽다가 알약 위에서 휠이 먹통"이라는 새 짜증을 만든다.
  jbEl.addEventListener('wheel', function(ev){
    var g = topGrid();
    if (!g) return;
    var unit = ev.deltaMode === 1 ? 16 : (ev.deltaMode === 2 ? g.clientHeight : 1);
    g.scrollTop += ev.deltaY * unit;
    if (land) land.touched = true;     // 알약 위에서 굴려도 착지를 읽은 것이다
    ev.preventDefault();
  }, {passive: false});
  // 착지에 손을 댔나 — 휠·터치·스크롤바·읽기 키. 손댄 착지는 '읽던 자리'라 다음 이동이 쌓는다.
  function touched(ev){
    if (!land || land.touched) return;
    var t = ev.target;
    if (t && t.nodeType === 1 && t.closest && t.closest('.sbs-grid, .vpane')) land.touched = true;
  }
  ['wheel', 'touchmove', 'mousedown'].forEach(function(n){
    window.addEventListener(n, touched, {capture: true, passive: true});
  });
  function editable(t){
    return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ''));
  }
  document.addEventListener('keydown', function(ev){
    if (ev.altKey && !ev.metaKey && !ev.ctrlKey && ev.key === 'ArrowLeft' && stack.length
        && !editable(ev.target)){
      ev.preventDefault(); jbBack(); return;
    }
    if (land && !land.touched && !editable(ev.target)
        && /^(ArrowUp|ArrowDown|PageUp|PageDown|Home|End| )$/.test(ev.key)) land.touched = true;
  }, true);
  var jbPaper = null;
  // mark(el, block)은 **프로그래매틱 스크롤을 시작하기 직전**에 부른다. block = 그 스크롤의
  // scrollIntoView block('start'|'center'). 반환값 = 자리를 쌓았거나 연장으로 이었나.
  window.__mdJumpBack = {
    mark: jbMark, go: jbGo, back: jbBack, dismiss: jbDismiss, split: jbSplit,
    state: function(){ return stack.length ? stack[stack.length - 1] : null; },
    depth: function(){ return stack.length; },
    setPaper: function(tok){ if (tok !== jbPaper){ jbPaper = tok; jbDismiss(); } },
    // 아래 칸이 닫혔다 — 그 칸에 쌓인 자리는 돌아갈 곳이 없다 (§ui/split_view.py teardown)
    dropPane: function(pane){
      stack = stack.filter(function(p){ return p.pane !== pane; });
      if (!stack.length) return jbDismiss();
      jbLabel(paneGrid('a'), (paneGrid('a') || {}).scrollTop || 0);
      if (!jbRaf) jbRaf = requestAnimationFrame(jbTick);
    }
  };
})();
"""


def mark_js(el_expr: str, block: str = "center") -> str:
    """본문을 옮기는 JS 앞에 붙이는 한 줄 — `el_expr`(요소 식)로 가기 전 읽던 자리를 쌓는다."""
    return f"window.{JUMP_BACK_API}&&window.{JUMP_BACK_API}.mark({el_expr},{json.dumps(block)});"


def go_js(el_expr: str, block: str = "center", behavior: str = "smooth") -> str:
    """`el_expr`(요소 식)로 옮기는 JS 식 — 자리 쌓기·나눔 중 아래 칸으로 보내기를 다 거친다.
    알약이 없는 화면이면 그냥 그 요소로 스크롤한다. 값은 실제로 옮긴 요소(없으면 null)."""
    b, h = json.dumps(block), json.dumps(behavior)
    return (f"(function(e){{if(!e)return null;var J=window.{JUMP_BACK_API};if(J)return J.go(e,{b},{h});"
            f"e.scrollIntoView({{behavior:{h},block:{b}}});return e;}})({el_expr})")


def init_js(paper_key: str | None) -> str:
    """화면이 바뀔 때 — 자리는 그 논문의 것이라 다른 논문(또는 홈)으로 오면 비운다."""
    return f"window.{JUMP_BACK_API}&&window.{JUMP_BACK_API}.setPaper({json.dumps(paper_key)});"


def install() -> None:
    """셸 페이지가 한 번 부른다 — 알약 CSS·마크업·스크립트."""
    from narani import i18n
    from narani.ui import spa

    spa.add_css(CSS)
    spa.add_body_html(i18n.html(HTML))
    spa.add_body_html(f"<script>{JS}</script>")
