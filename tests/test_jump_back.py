"""'읽던 자리로' 알약 — 본문을 옮기는 모든 길이 함께 쓰는 자리 쌓기(§ui/jump_back.py).

알약은 순수 클라이언트라 검증 표면은 (1) 셸에 남는 id·전역 API가 쓰는 쪽(app.py·에이전트 창·
그림 참조·찾기 바)과 한 이름이라는 것, (2) 모든 이동이 **스크롤 직전에** 자리를 쌓는다는 것,
(3) 규칙(연장·히스테리시스·상한)의 몇 줄이다. 동작은 실제 논문으로 브라우저에서 확인했다.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani.ui import agent_panel, figrefs, find_bar, jump_back  # noqa: E402

CSS, HTML, JS = jump_back.CSS, jump_back.HTML, jump_back.JS


def _js_block(src: str, head: str) -> str:
    """`function foo(`부터 다음 함수 선언까지."""
    i = src.index(head)
    return src[i:src.index("\n  function ", i + 1)]


def test_constants_are_single_sourced():
    assert f'id="{jump_back.JUMP_BACK_ID}"' in HTML
    assert f"#{jump_back.JUMP_BACK_ID}" in CSS
    assert f"z-index: {jump_back.JUMP_BACK_Z}" in CSS
    assert JS.count(f"window.{jump_back.JUMP_BACK_API} = {{") == 1
    assert f"JB_EDGE = {jump_back.JUMP_EDGE_PX}" in JS
    assert f"JB_HOP_MS = {jump_back.HOP_MS}, JB_DEPTH = {jump_back.DEPTH}" in JS


def test_z_index_between_find_bar_and_drawers():
    """알약은 본문 층 위, 서랍·찾기 바 아래여야 한다."""
    find_z = max(int(v) for v in re.findall(r"z-index:\s*(\d+)", find_bar.CSS))
    assert 100 < jump_back.JUMP_BACK_Z < min(agent_panel.PANEL_Z, find_z)


def test_pill_is_out_of_the_update_banner_corner_and_visible():
    """예전 흰 알약은 본문에 섞였고, 오른쪽 아래는 새 버전 알림 자리다 — 칸 가운데에 파랗게."""
    place = _js_block(JS, "function jbPlace(")
    assert "(r.left + vr) / 2 - w / 2" in place and "style.right" not in place
    rule = CSS.split("#md-jump-back {", 1)[1].split("}", 1)[0]
    assert "background: #2383e2" in rule and "color: #fff" in rule


def test_pill_avoids_overlay_drawer():
    place = _js_block(JS, "function jbPlace(")
    assert "classList.contains('ag-overlay')" in place
    assert "getElementById('md4-agent')" in place


def test_dark_mode_override():
    dark = CSS.split("@media (prefers-color-scheme: dark)")[1]
    assert "#md-jump-back" in dark and "--mjb-ring" in dark


def test_buttons_are_real_and_labelled():
    """돌아가기 · 같이 보기(두 뷰어로) · 닫기 — 셋 다 진짜 단추에 이름이 있다."""
    assert HTML.count('type="button"') == 3
    assert HTML.count("aria-label=") == 3 and HTML.count('aria-hidden="true"') == 2
    assert 'role="status"' in HTML


def test_container_lookup_is_gated():
    """단독 `.sbs-grid`는 번역 탭 결과 그리드까지 잡는다 — [data-row]로 뷰어 것만 가려낸다.
    요소를 받으면 그 요소가 본문 안일 때만 — 편집기·왼쪽 패널로 가는 이동은 쌓지 않는다."""
    sc = _js_block(JS, "function jbScroller(")
    assert "closest('.sbs-grid, .vpane')" in sc and "return c || null;" in sc
    # 요소 없이 찾을 때는 위 칸(복제가 아닌 그리드)만 — 아래 칸 자리는 paneGrid('b')로 따로 찾는다
    assert "'.sbs-grid:not(.sbs-clone), .vpane'" in sc and "matches('[data-row]')" in sc


def test_thresholds_have_hysteresis_grace_and_hop():
    arm = float(re.search(r"JB_ARM = ([\d.]+)", JS).group(1))
    home = float(re.search(r"JB_HOME = ([\d.]+)", JS).group(1))
    arm_min = int(re.search(r"JB_ARM_MIN = (\d+)", JS).group(1))
    home_min = int(re.search(r"JB_HOME_MIN = (\d+)", JS).group(1))
    assert arm > home and arm_min > home_min
    assert int(re.search(r"JB_GRACE = (\d+)", JS).group(1)) >= 500
    assert "Date.now() - jbJumpAt > JB_GRACE" in JS
    # 연장 = 착지에 손대지 않고(touched) HOP_MS 안에 다시 뛰었을 때만
    hop = _js_block(JS, "function hop(")
    assert "!land.touched" in hop and "JB_HOP_MS" in hop


def test_mark_stacks_unless_hop_and_caps_depth():
    """규칙 하나 — 이동마다 떠나기 전 자리를 쌓는다(연장이면 쌓지 않는다). 그래서 '출처를 보던 중
    섹션 클릭'은 출처 자리를 쌓고, 다음 칩이 섹션 착지를 '읽던 자리'로 오해하지 않는다."""
    mark = _js_block(JS, "function jbMark(")
    assert mark.index("if (!hop()){") < mark.index("stack.push(p)")
    assert "stack.length > JB_DEPTH" in mark and "stack.shift()" in mark
    assert "land = {at: Date.now(), touched: false, expect: g.scrollTop + need, arrived: false}" in mark
    back = _js_block(JS, "function jbBack(")
    assert "stack.pop()" in back and "land = null" in back


def test_auto_release_waits_for_arrival():
    """서버를 왕복한 smooth 스크롤은 늦게 출발한다 — 착지에 닿기 전(아직 제자리)을 '스스로 돌아왔다'로
    오해해 자리를 내려놓지 않게, 닿거나 손대거나 끝내 안 올 때부터 유예를 잰다."""
    tick = _js_block(JS, "function jbTick(")
    assert "near(g, g.scrollTop, land.expect)" in tick and "land.arrived = true" in tick
    assert "(!land || land.arrived) && Date.now() - jbJumpAt > JB_GRACE" in tick


def test_touch_marks_the_landing_as_read():
    assert "['wheel', 'touchmove', 'mousedown'].forEach" in JS
    assert "land.touched = true" in _js_block(JS, "function touched(")


def test_flash_is_not_the_citation_flash():
    """"여기가 근거다"(파랑)와 "여기로 돌아왔다"(무채색)는 다른 말이다."""
    assert ".mjb-flash" in CSS and "@keyframes mdJumpFlash" in CSS and "'mjb-flash'" in JS
    assert "'wheel'" in JS and "{passive: false}" in JS


def test_keyboard_back_skips_editable_targets():
    seg = JS[JS.index("document.addEventListener('keydown'"):]
    assert "ev.altKey" in seg and "'ArrowLeft'" in seg and "!editable(ev.target)" in seg


def test_helpers_are_guarded_for_pages_without_the_pill():
    """홈·단독 창(/agent)에는 알약이 없다 — 부르는 쪽 코드가 그 부재를 견뎌야 한다."""
    assert jump_back.mark_js("e", "start") == \
        "window.__mdJumpBack&&window.__mdJumpBack.mark(e,\"start\");"
    assert jump_back.init_js(None) == "window.__mdJumpBack&&window.__mdJumpBack.setPaper(null);"
    assert "setPaper: function(tok){ if (tok !== jbPaper){ jbPaper = tok; jbDismiss(); } }" in JS


def test_go_marks_then_routes_then_scrolls():
    """모든 이동의 입구 — 두 뷰어면 아래 칸의 같은 요소로 바꾸고, 그 칸에서 자리를 쌓은 뒤 스크롤한다."""
    go = _js_block(JS, "function jbGo(")
    assert go.index("sp.route(el)") < go.index("jbMark(el") < go.index("scrollIntoView(")
    assert "sp.touched(el)" in go and "return el;" in go
    assert "go: jbGo" in JS and "split: jbSplit" in JS
    cap = _js_block(JS, "function jbCapture(")
    assert "pane: g.classList.contains('sbs-clone') ? 'b' : 'a'" in cap
    assert "paneGrid(p.pane)" in _js_block(JS, "function jbBack(")


def test_split_button_opens_lower_pane_then_returns_top():
    """'같이 보기' — 아래 칸이 지금 본 곳(top: 위 칸의 지금 scrollTop)을 띄운 **뒤** 위 칸이 읽던 자리로."""
    sp = _js_block(JS, "function jbSplit(")
    assert sp.index("sp.open({top: g.scrollTop, lead: 'b'})") < sp.index("jbBack()")
    can = _js_block(JS, "function canSplit(")
    assert "!sp.isOn()" in can and "p.pane !== 'b'" in can
    assert "#md-jump-back.can-split .mjb-split" in CSS


@pytest.mark.parametrize("src, head, needle", [
    (figrefs.__file__, "  function jump(h){", "window.__mdJumpBack.go(h, 'start', 'smooth')"),
    (find_bar.__file__, "  function scrollToCur(){", "window.__mdJumpBack.mark(el, 'center')"),
])
def test_other_jumps_go_through_the_pill(src, head, needle):
    """그림·섹션 참조는 go(두 뷰어면 아래 칸으로), 찾기 바는 이끄는 칸 안에서만 찾으므로 mark만."""
    from pathlib import Path

    piece = _js_block(Path(src).read_text(encoding="utf-8"), head)
    assert needle in piece
    if "mark(" in needle:
        assert piece.index(needle) < piece.index("e.scrollTop +=")


def test_viewer_jumps_go_through_the_pill():
    """섹션 트리·목차·하이라이트 목록·용어 예문 — app.py가 만드는 이동도 같은 입구를 탄다."""
    from pathlib import Path

    from narani.ui import app

    js = app._jump_js("document.getElementById('sec-x')", "instant")
    assert "J.go(e,\"start\",\"instant\")" in js
    assert js.index("J.go(") < js.index("scrollIntoView(")   # 알약이 없는 화면(홈)에서만 직접 스크롤
    text = Path(app.__file__).read_text(encoding="utf-8")
    for head in ("def _row_scroll_js(", "def _sec_scroll_js("):
        body = text[text.index(head):].split("\n\n", 1)[0]
        assert "_jump_js(" in body and "scrollIntoView" not in body
    goto = text[text.index("    async def goto_md("):text.index("    # ---------- 왼쪽 작업 패널")]
    assert "_row_scroll_js(k, \"instant\")" in goto and ".sbs-grid [data-row" not in goto
    ex = text[text.index("    def goto_example_row("):text.index("    def example_toggle(")]
    assert "jump_back.go_js(" in ex and "scrollIntoView" not in ex
    anno = text[text.index("    if (!ms.length){ openPop(id); return; }"):]
    assert anno.index("__mdJumpBack.go(ms[0]") < anno.index("scrollIntoView(")


def test_agent_button_lives_at_header_right_only():
    """에이전트는 앱 전체의 기능 — 홈·뷰어 헤더 맨 오른쪽 한 자리. 툴바·탭 줄의 옛 forum 단추는 없다."""
    from pathlib import Path

    from narani.ui import app, home

    a = Path(app.__file__).read_text(encoding="utf-8")
    h = Path(home.__file__).read_text(encoding="utf-8")
    assert 'icon="forum"' not in a and 'icon="forum"' not in h
    assert "chip_slot = ui.row()" in a and a.index("chip_slot = ui.row()") < a.index("agent_button()  #")
    assert "tabs_bar = tab_bar(state, None)" in h and "agent_button()  #" in h
    assert "body.ag-open .md4-agentbtn" in app._CHROME_CSS
