"""맥락 세션 — 세션이 어느 논문·컬렉션을 두고 쓰였는지 기록하고, 창이 그 화면에서 처음 열 때 고른다."""

from __future__ import annotations

import asyncio
import shutil

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, make_wd, scripted_model  # noqa: E402

from narani.ui import agent_panel  # noqa: E402
from narani.ui.agent import retrieval, sessions  # noqa: E402
from narani.ui.agent.agent import make_agent, run_turn  # noqa: E402
from narani.ui.agent.context import ScreenContext, context_keys  # noqa: E402
from narani.ui.agent.tools import Deps  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()


def test_context_keys_cover_screen_paper_mentions_and_collection():
    ctx = ScreenContext.model_validate({
        "screen": "review", "paper": {"token": "aaa"},
        "mentions": [{"kind": "paper", "token": "aaa", "auto": True},   # 보는 중 칩 — 화면 논문과 같다
                     {"kind": "section", "token": "bbb", "row": 3, "end": 5},
                     {"kind": "note", "token": "ccc", "tab": "정리"},
                     {"kind": "collection", "id": "nirvana"}],
    })
    assert context_keys(ctx) == ["p:aaa", "p:bbb", "p:ccc", "c:nirvana"]
    home = ScreenContext.model_validate({"screen": "home",
                                         "mentions": [{"kind": "collection", "id": "scale", "auto": True}]})
    assert context_keys(home) == ["c:scale"]
    assert context_keys(ScreenContext()) == []


def test_contexts_roundtrip_cap_and_legacy_fallback(monkeypatch):
    s = sessions.create()
    sessions.touch_contexts(s, ["p:a", "c:x"], ts=100.0)
    sessions.touch_contexts(s, ["p:a"], ts=200.0)          # 같은 맥락은 마지막 시각으로
    sessions.save(s)
    back = sessions.load(s.id)
    assert back.contexts == {"p:a": 200.0, "c:x": 100.0}
    meta = next(m for m in sessions.list_sessions() if m["id"] == s.id)
    assert meta["ctx"] == {"p:a": 200.0, "c:x": 100.0}     # 창이 목록 요약만으로 고른다

    monkeypatch.setattr(sessions, "CONTEXTS_MAX", 3)
    for i in range(5):
        sessions.touch_contexts(back, [f"p:t{i}"], ts=300.0 + i)
    assert set(back.contexts) == {"p:t2", "p:t3", "p:t4"}  # 오래 안 쓴 것부터 버린다

    # contexts가 생기기 전 세션 — 화면에서 다룬 논문 목록(뒤가 최근)으로 갈음
    old = sessions.Session(id="s-old", updated=1000.0, paper_tokens=["p1", "p2"])
    ctx = sessions.contexts_for_meta(old)
    assert ctx["p:p2"] == 1000.0 and ctx["p:p1"] < ctx["p:p2"]


def test_question_records_its_contexts_but_approval_resume_does_not(tmp_path):
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    agent = make_agent(scripted_model(["첫 답", "둘째 답"]))
    s = sessions.create()
    ctx = ScreenContext.model_validate({"screen": "review", "paper": {"token": tok},
                                        "mentions": [{"kind": "collection", "id": "scale"}]})
    deps = Deps(wd_for=reg.wd_for, current_token=tok, ctx=ctx, register_token=reg.register)

    async def go(text):
        return [ev async for ev in run_turn(agent, s, text, deps)]

    asyncio.run(go("이 논문 요약"))
    back = sessions.load(s.id)
    assert set(back.contexts) == {f"p:{tok}", "c:scale"}
    t0 = back.contexts[f"p:{tok}"]
    asyncio.run(go(None))                                   # 질문 없는 재개는 맥락을 새로 쓰지 않는다
    assert sessions.load(s.id).contexts[f"p:{tok}"] == t0


def test_panel_picks_context_session_on_first_open_and_only_hints_while_open():
    js = agent_panel.HTML if "function pickForContext" in agent_panel.HTML else agent_panel.JS
    # 열쇠는 서버 context_keys와 같은 꼴
    assert "'p:' + a.token" in js and "'c:' + a.id" in js
    # 처음 열 때(목록을 읽은 뒤) 맥락 세션부터 — 같은 맥락에서 다시 열면 사용자가 고른 세션을 존중
    assert "var picked = pickForContext();" in js
    assert "pickForContext() || refreshSession()" in js
    assert "key === lsGet(LS_CTXKEY)" in js
    # 이 맥락의 대화가 없으면 지금 세션이 비었을 때만 그대로, 아니면 빈 세션(없으면 새로)
    assert "if (pm && !pm.turns) return null;" in js
    # 열린 채 화면이 바뀌면 세션을 바꾸지 않고 안내만 (보는 중 칩을 다시 그리는 자리에서)
    assert "ctxHint();\n  }" in js
    # 메뉴에서 고르기·새 세션·질문 보내기 = 이 맥락에서 이 세션을 쓴다
    assert "closeMenu(); markCtx();" in js
    assert "if (q){ markCtx(); ctxNote = null; }" in js
    assert "function(){ markCtx(); newSession(); }" in js
    assert ".ag-ctxnote" in agent_panel.CSS
