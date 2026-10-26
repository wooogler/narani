"""위아래 두 뷰어 — 복제 아래 칸·이끄는 칸·이동 보내기·하이라이트 두 칸·찾기 바 한 칸 (§ui/split_view.py).

순수 클라이언트라 문자열 계약을 본다. 동작은 실제 논문(Maru, 사본)으로 브라우저에서 확인했다:
같이 보기 · 나눔 중 목차 이동은 아래 칸만 · 아래 칸에서 만든 하이라이트가 두 칸에 · 이끄는 칸 바꾸면 PDF가 그 칸의 쪽 ·
번역 칸 토글(재렌더) 뒤에도 아래 칸 비율 유지 · 다른 논문 탭에 갔다 오면 그 논문의 나눔 상태로.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani.ui import app, find_bar, scroll_memory, split_view  # noqa: E402

JS, CSS = split_view.JS, split_view.CSS
APP = Path(app.__file__).read_text(encoding="utf-8")


def _js_block(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n  function ", i + 1)]


def test_clone_drops_ids_and_sync_flag():
    """복제본에 id가 남으면 sec-·pdfpage-·NiceGUI 요소 id가 둘이 된다. 싱크 표시도 지워야 아래 칸에 새로 붙는다."""
    cl = _js_block(JS, "function clean(")
    assert "removeAttribute('id')" in cl and "querySelectorAll('[id]')" in cl
    assert "delete c.dataset.pdfsync" in cl and "classList.add('sbs-clone')" in cl


def test_lower_pane_rebuilds_when_top_grid_is_redrawn_keeping_ratio():
    loop = JS[JS.index("  setInterval(function(){"):JS.index("  // 마지막으로 손댄 칸이 이끈다")]
    assert "orig.isConnected" in loop and "build({ratio: ratioB})" in loop
    assert "stable < 1" in loop                         # 반쯤 그려진 그리드를 복제하지 않는다
    b = _js_block(JS, "function build(")
    assert "where.ratio * maxTop(clone)" in b and "if (clone === c0)" in b


def test_last_touched_pane_leads_pdf_sync():
    assert "['wheel', 'touchstart', 'mousedown', 'keydown'].forEach" in JS
    lead = _js_block(JS, "function setLead(")
    assert "g.__md4PdfMove" in lead
    sync = APP[APP.index("    def _pdf_sync_js("):APP.index("    _PDF_SYNC_JS = ")]
    assert "window.__md4Split.leads(sc)" in sync and "if(tick || !leads()) return;" in sync
    assert "sc.__md4PdfMove=" in sync and "if(leads()) move(true);" in sync
    # 손으로 굴린 PDF의 쉼은 두 칸이 함께 쓴다 — 칸마다 따로면 다른 칸이 곧바로 PDF를 끌고 간다
    assert "window.__md4PdfS" in sync and "S.manualUntil" in sync and "var ours=0" not in sync
    # 정의가 첫 부름보다 앞서야 한다 (그리드가 이미 붙어 있으면 바로 부른다)
    assert sync.index("window.__md4PdfSyncBind = function(sc)") < sync.index("(function bind(attempt){")
    assert '_pdf_sync_js(".sbs-grid:not(.sbs-clone)")' in APP
    assert "window.__md4PdfSyncBind(clone)" in _js_block(JS, "function build(")


def test_jumps_route_to_lower_pane_while_split():
    tw = JS[JS.index("function twin("):JS.index("var api = {")]
    assert "path.unshift(" in tw and "t.children[path[i]]" in tw
    assert "route: function(el){ return (clone && twin(el)) || el; }" in JS


def test_highlights_paint_in_both_panes():
    """앵커는 위 칸에서 한 번만 풀고 같은 결과를 아래 칸 같은 셀에 칠한다 — 아래 칸을 세면 같은 앵커가 두 번 '놓였다'."""
    assert "!c.closest('.sbs-clone')" in APP[APP.index("  function scopes(){"):APP.index("  function twinOf(")]
    assert "twinScopes().forEach(unwrap);" in APP
    assert "var tw = twinOf(sc[i]);" in APP and "if (tw) paint(tw, hits);" in APP


def test_find_bar_searches_only_leading_pane_and_scroll_memory_skips_clone():
    assert "g !== sp.activeGrid()" in Path(find_bar.__file__).read_text(encoding="utf-8")
    assert ".sbs-grid:not(.sbs-clone)" in scroll_memory.SELECTORS


def test_toolbar_toggle_and_state_memory():
    assert "md4-split-btn" in APP and 'icon="horizontal_split"' in APP
    assert "body.md4-split-on .md4-split-btn" in CSS
    assert f"var MIN = {split_view.MIN_PCT}, MAX = {split_view.MAX_PCT}, DEF = {split_view.DEF_PCT};" in JS
    assert "sessionStorage.setItem(SS + paper" in JS and "lsSet(LS_PCT" in JS
    # 하위 화면보다 먼저 설치해야 첫 화면의 setPaper가 닿는다
    shell = APP[APP.index("    def shell() -> None:"):]
    assert shell.index("split_view.install()") < shell.index("ui.sub_pages(")
    assert shell.index("jump_back.install()") < shell.index("ui.sub_pages(")


def test_column_handle_applies_to_both_grids():
    enko = APP[APP.index("    _ENKO_DRAG_JS = "):APP.index("    _BODY_DRAG_JS = ")]
    assert "querySelectorAll('.sbs-grid.sbs-two')" in enko
