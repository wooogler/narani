"""지금 섹션 띠(§ui/sticky_head.py) — 스크립트 틀이 다 채워졌는지, 찾기 바가 띠 글을 세지 않는지 (서버 없이)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from narani.ui import find_bar, sticky_head
from narani.ui.jump_back import JUMP_BACK_API, JUMP_EDGE_PX


def test_script_is_fully_formatted_and_uses_shared_names():
    js = sticky_head.JS
    assert "%(" not in js
    assert f"window.{sticky_head.STICK_API}" in js and f"window.{JUMP_BACK_API}" in js
    assert f"var EDGE = {JUMP_EDGE_PX};" in js  # 읽던 자리·PDF 싱크와 같은 '화면 맨 위' 기준선
    assert ".sbs-ghost" in js  # 숨은 짝 셀의 헤더는 세지 않는다
    assert "data-row') !== '0'" in js  # 첫 행(논문 제목)은 섹션이 아니다


def test_band_is_sticky_zero_height_child_spanning_all_columns():
    css = sticky_head.CSS
    assert ".sbs-stick { grid-column: 1 / -1; position: sticky; top: 0; height: 0;" in css
    assert "var(--sbs-cols" in css  # 원문|번역 칸 폭(드래그)과 같은 비율로 나란히


def test_find_bar_skips_band_text():
    assert ".sbs-stick" in find_bar.HTML


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_script_parses():
    r = subprocess.run(["node", "--check", "-"], input=sticky_head.JS, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
