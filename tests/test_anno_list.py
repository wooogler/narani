"""하이라이트 목록을 왼쪽 메모 패널로(§ui/app.py _ANNO_HTML·notes_panel) — 서버 없이 스크립트·전환 상태를 본다."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani import i18n  # noqa: E402
from narani.ui import app, sticky_head  # noqa: E402


def _script() -> str:
    html = i18n.html(app._ANNO_HTML)
    return re.search(r"<script>(.*)</script>", html, re.S).group(1)


def test_no_right_drawer_left():
    """오른쪽 고정 서랍·본문 비우기·여는 함수가 없다 — 목록 상자는 메모 패널 자리에 옮겨 붙는다."""
    js, css = app._ANNO_HTML, app._ANNO_CSS
    assert "__mdAnnoTogglePanel" not in js and "ma-open" not in js and "ap-x" not in js
    assert "md4-annohost" in js and "__mdAnnoReveal" in js
    assert "emitEvent('md4-anno-list'" in js  # 본문 팝오버의 '목록' → 서버가 메모 패널을 연다
    assert "ma-open" not in css and "--ma-top" not in css and "position: fixed; top: var(" not in css


def test_list_groups_by_the_same_headings_as_the_sticky_band():
    js = app._ANNO_HTML
    assert "sectionHeads" in js and "ap-grp" in js and "ap-gh" in js
    assert "md4-cursec" in js and "md4-cursec" in sticky_head.JS  # 지금 섹션 묶음 칠하기


@pytest.mark.skipif(not shutil.which("node"), reason="node 없음")
def test_anno_script_parses(tmp_path):
    f = tmp_path / "anno.js"
    f.write_text(_script(), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_notes_view_default_saved_and_empty_paper():
    assert app.notes_view({}) == "highlights"
    assert app.notes_view({"ui": {"notes_view": "memo"}}) == "memo"
    assert app.notes_view({"ui": {"notes_view": "bogus"}}) == "highlights"
    # 하이라이트가 없는 논문은 내 메모로 — 저장된 쪽이 하이라이트여도
    assert app.notes_view({"ui": {"notes_view": "highlights"}}, n_highlights=0) == "memo"
    assert app.notes_view({"ui": {"notes_view": "highlights"}}, n_highlights=3) == "highlights"
