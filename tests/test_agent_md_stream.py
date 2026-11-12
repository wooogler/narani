"""에이전트 답의 표·취소선과 스트리밍 중 렌더 — 끝난 뒤와 같은 렌더러로 흐르는 동안에도 그린다."""

from __future__ import annotations

import asyncio
import shutil

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, make_wd, scripted_model  # noqa: E402

from narani.ui import agent_panel  # noqa: E402
from narani.ui.agent import agent as agent_mod  # noqa: E402
from narani.ui.agent import retrieval, sessions  # noqa: E402
from narani.ui.agent.agent import make_agent, run_turn  # noqa: E402
from narani.ui.agent.cites import _hold_back, render_html, render_partial  # noqa: E402
from narani.ui.agent.context import ScreenContext  # noqa: E402
from narani.ui.agent.tools import Deps  # noqa: E402

TABLE = "비교:\n\n| 항목 | A |\n|---|---|\n| 속도 | **빠름**이 |\n| 비용 | ~~높음~~ 낮음 |\n"


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()


def test_tables_and_strikethrough_render_with_cjk_emphasis():
    html = render_html(TABLE, [])
    assert "<table>" in html and "<th>항목</th>" in html
    assert "<strong>빠름</strong>이" in html          # 한글 조사 앞 굵게는 그대로
    assert "<s>높음</s>" in html
    assert "&lt;script&gt;" in render_html("<script>x</script>", [])   # raw HTML은 여전히 막는다


def test_hold_back_hides_unsettled_tail():
    head_only = "비교:\n\n| 항목 | A |"
    assert _hold_back(head_only) == "비교:\n"          # 구분 줄 전 표 머리는 문단으로 보이지 않게 미룬다
    with_sep = head_only + "\n|---|---|\n| 속도 | 빠"
    assert _hold_back(with_sep).endswith("|---|---|")   # 쓰는 중인 마지막 행만 미룬다
    assert _hold_back("근거입니다 [[r1") == "근거입니다 "  # 반쯤 온 인용 표시
    assert _hold_back("근거 [[r1]] 다음") == "근거 [[r1]] 다음"
    # 잠정 렌더는 표 머리줄이 문단으로 깜빡이지 않는다
    assert "| 항목" not in render_partial(head_only, set())


def test_partial_render_matches_final_for_the_same_text():
    rows = {("t" * 12, 3)}
    text = "핵심은 이것입니다 [[r3]].\n\n" + TABLE
    final_clean, refs = agent_mod.resolve_citations(text, rows, current="t" * 12)
    assert render_partial(text, rows, current="t" * 12) == render_html(final_clean, refs,
                                                                         current="t" * 12)


def test_stream_sends_rendered_html_before_done(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod, "HTML_EVERY", 0.0)
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    agent = make_agent(scripted_model([TABLE]))
    s = sessions.create()
    deps = Deps(wd_for=reg.wd_for, current_token=tok, register_token=reg.register,
                ctx=ScreenContext.model_validate({"screen": "review", "paper": {"token": tok}}))

    async def go():
        return [ev async for ev in run_turn(agent, s, "표로 비교해 줘", deps)]

    events = asyncio.run(go())
    names = [e["event"] for e in events]
    htmls = [e["data"]["html"] for e in events if e["event"] == "html"]
    assert htmls and names.index("html") < names.index("done")
    assert "<table>" in htmls[-1]                       # 흐르는 동안에도 표로
    assert "<table>" in events[-1]["data"]["html"]


def test_panel_swaps_in_streamed_html_and_styles_tables():
    js = agent_panel.HTML if "function paintLive" in agent_panel.HTML else agent_panel.JS
    assert "} else if (name === 'html'){" in js
    assert "liveText.innerHTML = live.html" in js
    assert "if (stuck) list.scrollTop = list.scrollHeight;" in js   # 바닥에 붙어 있을 때만 따라감
    css = agent_panel.CSS
    assert ".ag-a table { display: block; max-width: 100%; overflow-x: auto;" in css
    assert ".ag-a th, .ag-a td { border-color: #3a3a3a; }" in css   # 다크 모드
    assert ".ag-streaming a.chat-cite { pointer-events: none;" in css
