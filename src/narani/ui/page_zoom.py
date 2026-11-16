"""앱 창 전체 확대·축소 (⌘+ / ⌘− / ⌘0) — 브라우저의 페이지 확대와 같은 동작.

`ui.run(native=True)`의 웹뷰(macOS WKWebView)에는 브라우저 메뉴가 없어 ⌘+를 눌러도 아무 일이
없다. CSS `zoom`으로 흉내 내면 `100vh`로 높이를 잡은 칸(스플리터·뷰어)이 창 밖으로 넘치고 좌표
계산(그림 확대·하이라이트 위치)이 어긋난다. 그래서 WKWebView의 `pageZoom`(macOS 11+)을 직접
바꾼다 — 사파리의 ⌘+와 같아서 CSS px 자체가 커지고 레이아웃은 창 크기에 맞게 다시 짜인다.

이 코드는 **웹뷰 프로세스**에서 돈다: 본 창은 `webview.start(func=...)`(§ui/desktop.py configure),
형제 창은 `python -m narani.ui.window`(§ui/window.py)가 부른다. 키는 앱 단위 NSEvent 로컬 모니터로
받아 페이지보다 먼저 가로챈다(입력칸에 포커스가 있어도 동작). 배율은 설정 폴더의
`page_zoom.json`에 남겨 다음 실행과 새로 여는 창에도 이어진다. macOS가 아니면 아무것도 하지 않는다
(브라우저 모드는 브라우저가 스스로 확대한다).
"""

from __future__ import annotations

import json
import sys

from narani.config import CONFIG_DIR

# 사파리와 같은 단계
STEPS = (0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
_FILE = "page_zoom.json"

# 배율 표시 — 바꿀 때 가운데 위에 잠깐 뜬다
_BADGE_JS = """(function(p){
  var b = document.getElementById('md4-zoom-badge');
  if (!b) {
    b = document.createElement('div'); b.id = 'md4-zoom-badge';
    b.style.cssText = 'position:fixed;top:56px;left:50%;transform:translateX(-50%);z-index:10060;'
      + 'padding:5px 12px;border-radius:8px;background:rgba(30,30,30,.82);color:#fff;'
      + 'font:600 13px -apple-system,system-ui,sans-serif;pointer-events:none;transition:opacity .25s';
    document.body.appendChild(b);
  }
  b.textContent = p + '%'; b.style.opacity = '1';
  clearTimeout(window.__md4ZoomT);
  window.__md4ZoomT = setTimeout(function(){ b.style.opacity = '0'; }, 900);
})(__PCT__);"""


def badge_js(z: float) -> str:
    """배율 표시 JS — CSS에 '%'가 들어 있어 %-서식 대신 자리표시를 바꾼다."""
    return _BADGE_JS.replace("__PCT__", str(round(z * 100)))


def load() -> float:
    try:
        z = float(json.loads((CONFIG_DIR / _FILE).read_text(encoding="utf-8")).get("zoom", 1.0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 1.0
    return min(max(z, STEPS[0]), STEPS[-1])


def save(z: float) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (CONFIG_DIR / _FILE).write_text(json.dumps({"zoom": z}), encoding="utf-8")
    except OSError:
        pass


def step(z: float, key: str) -> float | None:
    """지금 배율 z에서 키 하나('+', '-', '0')를 눌렀을 때의 새 배율. 해당 없는 키면 None."""
    if key == "0":
        return 1.0
    if key == "+":
        return next((s for s in STEPS if s > z + 1e-3), STEPS[-1])
    if key == "-":
        return next((s for s in reversed(STEPS) if s < z - 1e-3), STEPS[0])
    return None


def key_of(chars: str) -> str | None:
    """⌘와 함께 눌린 글자 → '+'·'-'·'0' (⌘= 와 ⌘⇧= 모두 확대)."""
    return {"=": "+", "+": "+", "-": "-", "_": "-", "0": "0"}.get(chars)


def webview_started() -> None:
    """`webview.start(func=...)`로 웹뷰 프로세스에서 불린다(별도 스레드) — 메인 스레드에 설치를 맡긴다."""
    if sys.platform != "darwin":
        return
    try:
        from PyObjCTools import AppHelper
    except ImportError:
        return
    AppHelper.callAfter(_install)


def _webviews(window):  # noqa: ANN001, ANN202 — NSWindow → 그 안의 WKWebView들
    from WebKit import WKWebView

    out, stack = [], [window.contentView()] if window is not None and window.contentView() else []
    while stack:
        v = stack.pop()
        if v.isKindOfClass_(WKWebView):
            out.append(v)
            continue
        stack.extend(v.subviews() or [])
    return out


def _apply(view, z: float, badge: bool = False) -> None:  # noqa: ANN001
    if not view.respondsToSelector_("setPageZoom:"):
        return  # macOS 10.15 이하
    view.setPageZoom_(z)
    if badge:
        view.evaluateJavaScript_completionHandler_(badge_js(z), None)


_STATE: dict = {}


def zoom(key: str, window=None) -> bool:  # noqa: ANN001 — NSWindow
    """key('+'·'-'·'0')대로 그 창(없으면 key 창)의 배율을 바꾼다 — 키 모니터와 메뉴 막대(§ui/menu_bar.py)가
    함께 부른다(메인 스레드에서). 바꿀 웹뷰가 없으면 False."""
    from AppKit import NSApp

    # 다른 창이 바꾼 배율도 따라간다(형제 창은 다른 프로세스라 파일로 맞춘다)
    new = step(load(), key)
    views = []
    for w in (window, NSApp.keyWindow(), NSApp.mainWindow(), *(NSApp.windows() or [])):  # 패널이 key일 수도 있다
        if w is not None and (views := _webviews(w)):
            break
    if new is None or not views:
        return False
    _STATE["zoom"] = new
    save(new)
    for v in views:
        _apply(v, new, badge=True)
    return True


def _install() -> None:
    from AppKit import NSApp, NSEvent, NSEventMaskKeyDown

    if _STATE.get("monitor"):
        return
    z = load()
    _STATE["zoom"] = z
    for w in NSApp.windows() or []:
        for v in _webviews(w):
            _apply(v, z)

    cmd, others = 1 << 20, (1 << 18) | (1 << 19)  # NSEventModifierFlagCommand / Control·Option

    def on_key(event):  # noqa: ANN001, ANN202
        flags = event.modifierFlags()
        if not flags & cmd or flags & others:
            return event
        key = key_of(event.charactersIgnoringModifiers() or "")
        if key is None or not zoom(key, event.window()):
            return event
        return None  # 먹는다 — 페이지·메뉴 막대까지 가지 않게(메뉴의 같은 항목이 한 번 더 돌지 않는다)

    _STATE["monitor"] = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_key)

    # 창이 처음 뜰 때 웹뷰가 아직 없을 수 있다 — 잠깐 뒤 한 번 더 맞춘다
    from PyObjCTools import AppHelper

    def late() -> None:
        for w in NSApp.windows() or []:
            for v in _webviews(w):
                _apply(v, load())

    AppHelper.callLater(1.0, late)
