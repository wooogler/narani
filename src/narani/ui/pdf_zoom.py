"""PDF 칸 핀치 줌 — 트랙패드 핀치·Ctrl(⌘)+휠·두 손가락 터치로 원본 PDF 쪽을 확대한다.

PDF 칸(`.vpdf`)은 쪽마다 PNG를 칸 폭에 맞춰 그린다. 작은 글씨·그림 속 숫자를 보려면 확대가 필요한데,
브라우저 확대(⌘+)는 화면 전체가 커지고 앱 창(WKWebView)에는 그 기능도 없다. 그래서 PDF 칸만 따로 키운다.

- 이벤트: Chromium은 트랙패드 핀치를 `ctrlKey`가 붙은 `wheel`로, WebKit(Safari·앱 창)은 `gesture*`로
  보낸다. 터치 화면은 손가락 두 개의 거리로 잰다. 셋 다 PDF 칸 위에서만 가로채고 나머지 화면은 그대로.
- 배율은 칸의 `--pdf-zoom`(쪽 묶음 폭·쪽 최대 폭에 곱함)이다. 손가락(커서) 아래 지점이 그 자리에
  남도록 스크롤을 함께 옮긴다 — 안 그러면 확대할 때마다 보던 곳이 화면 밖으로 밀려난다.
- 쪽 그림은 기본 2배(144dpi)로 그려 확대하면 흐려진다. 1.4배를 넘으면 그 칸의 쪽을 4배 그림
  (`/pdfpage/…?s=4`)으로 바꾼다(이미 받은 쪽만 다시 받고, 나머지는 원래대로 lazy).
- 배율은 칸마다 따로다. 화면을 다시 그리면(토글 등) 새 칸은 100%부터.
- 칸 오른쪽 위에 늘 `− 100% +` 막대가 있다 — 핀치가 없는 마우스로도 확대하고, 배율을 누르면 100%로.
  평소엔 옅게, 칸에 마우스를 올리면 또렷하게. ⌘/Ctrl + `+`/`-`/`0`도 PDF 칸 위에서는 같은 동작.
"""

from __future__ import annotations

CSS = """
.vpdf { overflow: auto !important; }
.vpdf #pdf-pages { width: calc(100% * var(--pdf-zoom, 1)) !important; }
.vpdf img { max-width: calc(var(--md4-maxw, 900px) * var(--pdf-zoom, 1)) !important; }
.vpdf .md4-pdfzoom { position: sticky; top: 0; left: 0; height: 0; z-index: 6; display: flex;
  justify-content: flex-end; align-items: flex-start; overflow: visible; pointer-events: none; }
.vpdf .md4-pdfzoom .bar { pointer-events: auto; margin: 4px 4px 0 0; display: flex; align-items: center;
  border-radius: 999px; background: rgba(40,40,40,.72); opacity: .7; transition: opacity .15s ease; }
.vpdf:hover .md4-pdfzoom .bar, .vpdf .md4-pdfzoom .bar.zoomed { opacity: 1; }
.vpdf .md4-pdfzoom button { border: 0; background: transparent; color: #fff; cursor: pointer;
  font: 13px/1 system-ui, sans-serif; padding: 5px 9px; border-radius: 999px; }
.vpdf .md4-pdfzoom button.pct { min-width: 46px; padding: 5px 2px; font-variant-numeric: tabular-nums; }
.vpdf .md4-pdfzoom button:hover:not(:disabled) { background: rgba(35,131,226,.9); }
.vpdf .md4-pdfzoom button:disabled { opacity: .35; cursor: default; }
"""

JS = r"""
(function(){
  if (window.__md4PdfZoom) return; window.__md4PdfZoom = true;
  var MIN = 1, MAX = 5, HI = 1.4;
  var STEPS = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5];  // −/+ 단추가 오가는 배율
  function stepFrom(z, dir){
    if (dir > 0){ for (var i = 0; i < STEPS.length; i++) if (STEPS[i] > z + 0.01) return STEPS[i]; return MAX; }
    for (var j = STEPS.length - 1; j >= 0; j--) if (STEPS[j] < z - 0.01) return STEPS[j]; return MIN;
  }
  function paneOf(t){ return t && t.closest ? t.closest('.vpdf') : null; }
  function zoomOf(pv){ return parseFloat(pv.dataset.zoom || '1') || 1; }
  // 칸 오른쪽 위 배율 막대 [− 100% +] — 칸이 생길 때마다 하나(아래 MutationObserver)
  function bar(pv){
    var p = pv.querySelector(':scope > .md4-pdfzoom');
    if (!p){
      p = document.createElement('div'); p.className = 'md4-pdfzoom';
      p.innerHTML = '<div class="bar"><button type="button" class="out">−</button>'
        + '<button type="button" class="pct"></button><button type="button" class="in">+</button></div>';
      var b = p.querySelectorAll('button');
      b[0].title = md4T('축소') + ' — ⌘/Ctrl −';
      b[1].title = md4T('100%로 — ⌘/Ctrl+0');
      b[2].title = md4T('확대 — 핀치·⌘/Ctrl+휠도 됩니다');
      b[0].addEventListener('click', function(e){ e.stopPropagation(); setZoom(pv, stepFrom(zoomOf(pv), -1)); });
      b[1].addEventListener('click', function(e){ e.stopPropagation(); setZoom(pv, 1); });
      b[2].addEventListener('click', function(e){ e.stopPropagation(); setZoom(pv, stepFrom(zoomOf(pv), 1)); });
      pv.insertBefore(p, pv.firstChild);
    }
    var z = zoomOf(pv), btn = p.querySelectorAll('button');
    btn[1].textContent = Math.round(z * 100) + '%';
    btn[0].disabled = z <= MIN + 0.001; btn[2].disabled = z >= MAX - 0.001;
    p.firstChild.classList.toggle('zoomed', z > 1.001);
  }
  function bars(root){
    if (!root || !root.querySelectorAll) return;
    if (root.classList && root.classList.contains('vpdf')) bar(root);
    [].forEach.call(root.querySelectorAll('.vpdf'), bar);
  }
  new MutationObserver(function(muts){
    muts.forEach(function(m){ [].forEach.call(m.addedNodes, function(n){ if (n.nodeType === 1) bars(n); }); });
  }).observe(document.body, { childList: true, subtree: true });
  bars(document.body);
  function sharpen(pv){
    if (pv.dataset.hi) return; pv.dataset.hi = '1';
    [].forEach.call(pv.querySelectorAll('img[id^="pdfpage-"]'), function(img){
      var src = img.getAttribute('src') || '';
      if (src.indexOf('?s=') < 0) img.setAttribute('src', src + '?s=4');
    });
  }
  // (cx, cy) = 화면 좌표의 고정점. 그 아래 내용이 확대 뒤에도 같은 화면 자리에 오게 스크롤을 옮긴다.
  function setZoom(pv, nz, cx, cy){
    nz = Math.min(MAX, Math.max(MIN, nz));
    var z = zoomOf(pv); if (Math.abs(nz - z) < 0.002) return;
    var r = pv.getBoundingClientRect();
    var ox = (cx == null ? r.width / 2 : cx - r.left), oy = (cy == null ? r.height / 2 : cy - r.top);
    var x = pv.scrollLeft + ox, y = pv.scrollTop + oy, k = nz / z;
    pv.dataset.zoom = String(nz);
    pv.style.setProperty('--pdf-zoom', String(nz));
    pv.scrollLeft = Math.max(0, x * k - ox); pv.scrollTop = Math.max(0, y * k - oy);
    bar(pv);
    if (nz > HI) sharpen(pv);
  }
  window.__md4PdfSetZoom = setZoom;
  // Chromium: 트랙패드 핀치 = ctrlKey 붙은 wheel. 마우스 Ctrl(⌘)+휠도 같은 길.
  document.addEventListener('wheel', function(e){
    if (!(e.ctrlKey || e.metaKey)) return;
    var pv = paneOf(e.target); if (!pv) return;
    e.preventDefault();
    var dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    setZoom(pv, zoomOf(pv) * Math.exp(-dy * 0.01), e.clientX, e.clientY);
  }, { passive: false, capture: true });
  // WebKit(Safari·macOS 앱 창): gesture 이벤트. 막지 않으면 웹뷰가 페이지 전체를 확대한다.
  var g = null;
  document.addEventListener('gesturestart', function(e){
    var pv = paneOf(e.target); if (!pv) return;
    e.preventDefault(); g = { pv: pv, z0: zoomOf(pv) };
  }, { passive: false, capture: true });
  document.addEventListener('gesturechange', function(e){
    if (!g) return; e.preventDefault(); setZoom(g.pv, g.z0 * e.scale, e.clientX, e.clientY);
  }, { passive: false, capture: true });
  document.addEventListener('gestureend', function(e){ if (g){ e.preventDefault(); g = null; } },
    { passive: false, capture: true });
  // 터치 화면: 두 손가락 거리
  var t = null;
  function dist(a, b){ return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); }
  document.addEventListener('touchstart', function(e){
    if (e.touches.length !== 2) return;
    var pv = paneOf(e.target); if (!pv) return;
    t = { pv: pv, z0: zoomOf(pv), d0: dist(e.touches[0], e.touches[1]) };
  }, { passive: true, capture: true });
  document.addEventListener('touchmove', function(e){
    if (!t || e.touches.length !== 2) return;
    e.preventDefault();
    var a = e.touches[0], b = e.touches[1];
    setZoom(t.pv, t.z0 * dist(a, b) / (t.d0 || 1), (a.clientX + b.clientX) / 2, (a.clientY + b.clientY) / 2);
  }, { passive: false, capture: true });
  document.addEventListener('touchend', function(e){ if (e.touches.length < 2) t = null; }, { capture: true });
  // ⌘/Ctrl + 0 / + / −: 마우스가 PDF 칸 위면 그 칸만 (아니면 웹뷰 기본 동작 그대로)
  var hover = null;
  document.addEventListener('mousemove', function(e){ hover = paneOf(e.target); }, { passive: true });
  document.addEventListener('keydown', function(e){
    if (!(e.metaKey || e.ctrlKey) || !hover || !hover.isConnected) return;
    var z = zoomOf(hover), nz = null;
    if (e.key === '0') nz = 1;
    else if (e.key === '=' || e.key === '+') nz = stepFrom(z, 1);
    else if (e.key === '-' || e.key === '_') nz = stepFrom(z, -1);
    if (nz === null) return;
    e.preventDefault(); setZoom(hover, nz);
  });
})();
"""


def install() -> None:
    """현재 페이지에 PDF 칸 핀치 줌을 얹는다 (CSS + 문서 단위 리스너 한 벌)."""
    from narani.ui import spa

    spa.add_css(CSS)
    spa.add_body_html(f"<script>{JS}</script>")
