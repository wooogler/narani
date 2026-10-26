"""입력창 @ 맥락 — 가리키는 값만 실어 보내고(§ui/agent/context.py Mention), 질문 메시지 앞에 붙여
기록에 남긴다. 후보는 이 논문(섹션·메모 탭) → 다른 논문 → 컬렉션 순(§routes.mention_candidates)."""

from __future__ import annotations

import asyncio
import shutil

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, make_wd, scripted_model  # noqa: E402
from pydantic_ai.messages import ModelRequest, UserPromptPart  # noqa: E402

from narani import notes  # noqa: E402
from narani.ui.agent import retrieval, sessions  # noqa: E402
from narani.ui.agent.agent import make_agent, run_turn  # noqa: E402
from narani.ui.agent.context import (MENTIONS_MAX, ScreenContext, mentions_prompt,  # noqa: E402
                                     to_prompt)
from narani.ui.agent.routes import mention_candidates  # noqa: E402
from narani.ui.agent.tools import Deps  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()


def _ctx(**kw) -> ScreenContext:
    return ScreenContext.model_validate({"screen": "review", "paper": {"token": "abc"}, **kw})


def test_mentions_are_pointers_with_tool_hints():
    ctx = _ctx(mentions=[
        {"kind": "section", "token": "abc", "title": "Method", "row": 12, "end": 30},
        {"kind": "note", "token": "abc", "tab": "요약"},
        {"kind": "paper", "token": "def", "title": "Maru"},
        {"kind": "collection", "id": "p1", "title": "HCI"},
        {"kind": "bogus", "title": "x"}])
    assert [m.kind for m in ctx.mentions] == ["section", "note", "paper", "collection"]
    block = mentions_prompt(ctx)
    assert "read_rows(start=12, end=30, token=abc)" in block
    assert "read_notes(name='요약', token=abc)" in block
    assert "token=def" in block and "list_papers(project='p1')" in block
    # 지시문(화면 블록)에는 싣지 않는다 — 지시문은 기록에 남지 않는다
    assert "@로 지정한" not in to_prompt(ctx) and "현재 화면" in to_prompt(ctx)


def test_mentions_are_capped():
    ctx = _ctx(mentions=[{"kind": "paper", "token": f"t{i}", "title": "p"} for i in range(20)])
    assert len(ctx.mentions) == MENTIONS_MAX
    assert mentions_prompt(_ctx()) == ""


def test_run_turn_puts_mentions_before_question_and_on_the_turn(tmp_path):
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    received: list = []
    agent = make_agent(scripted_model(["네."], received=received))
    ctx = ScreenContext.model_validate({
        "screen": "review", "paper": {"token": tok},
        "mentions": [{"kind": "section", "token": tok, "title": "2 Method", "row": 1, "end": 2}]})
    deps = Deps(wd_for=reg.wd_for, current_token=tok, ctx=ctx, register_token=reg.register)
    s = sessions.create()

    async def go():
        return [ev async for ev in run_turn(agent, s, "이 섹션 요약해 줘", deps)]
    asyncio.run(go())
    req = [m for m in received[0] if isinstance(m, ModelRequest)][-1]
    texts = [p.content for p in req.parts if isinstance(p, UserPromptPart)]
    flat = "\n".join(c if isinstance(c, str) else "\n".join(map(str, c)) for c in texts)
    assert flat.index("@로 지정한 맥락") < flat.index("이 섹션 요약해 줘")
    user = [t for t in s.turns if t.get("role") == "user"][-1]
    assert user["text"] == "이 섹션 요약해 줘"
    assert user["mentions"] == [{"kind": "section", "token": tok, "title": "2 Method", "row": 1, "end": 2}]


def test_candidates_start_with_this_paper_then_sections_notes_papers(tmp_path, monkeypatch):
    here = make_wd(tmp_path, "here")
    other = make_wd(tmp_path, "other", title="Other Paper")
    reg = Registry(here)
    tok = next(iter(reg.by_token))
    notes.write(here, "요약", "메모")
    monkeypatch.setattr("narani.projects.all_projects", lambda: [{"id": "p1", "name": "HCI"}])
    items = mention_candidates(reg.wd_for, reg.register, tmp_path, tok, "")
    kinds = [i["kind"] for i in items]
    assert items[0] == {**items[0], "kind": "paper", "token": tok, "current": True}
    assert kinds.index("section") < kinds.index("note") < kinds.index("collection")
    secs = [i for i in items if i["kind"] == "section"]
    assert secs and all(i["token"] == tok and i["end"] >= i["row"] for i in secs)
    others = [i for i in items if i["kind"] == "paper" and not i.get("current")]
    assert [o["title"] for o in others] == ["Other Paper"]
    assert reg.wd_for(others[0]["token"]).root == other.root     # 고르면 바로 쓸 수 있게 등록된다
    # 글자로 거른다
    only = mention_candidates(reg.wd_for, reg.register, tmp_path, tok, "other")
    assert [i["title"] for i in only] == ["Other Paper"]


def test_what_you_are_viewing_is_default_context_but_not_saved_on_the_turn(tmp_path):
    """보고 있는 논문·컬렉션은 창이 기본으로 붙인다(auto) — 지시는 '따로 말이 없으면 이것', 질문 아래 칩으로는 남기지 않는다."""
    ctx = _ctx(mentions=[{"kind": "paper", "token": "abc", "title": "Maru", "auto": True},
                         {"kind": "collection", "id": "p1", "title": "HCI", "auto": True}])
    block = mentions_prompt(ctx)
    assert "지금 보고 있는 논문 'Maru'" in block and "지금 보고 있는 컬렉션 'HCI'" in block

    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    agent = make_agent(scripted_model(["네."]))
    ctx = ScreenContext.model_validate({"screen": "review", "paper": {"token": tok}, "mentions": [
        {"kind": "paper", "token": tok, "title": "Bespoke Bots", "auto": True},
        {"kind": "note", "token": tok, "tab": "요약"}]})
    s = sessions.create()

    async def go():
        return [ev async for ev in run_turn(agent, s, "요약해 줘", Deps(
            wd_for=reg.wd_for, current_token=tok, ctx=ctx, register_token=reg.register))]
    asyncio.run(go())
    assert [t for t in s.turns if t["role"] == "user"][-1]["mentions"] == [
        {"kind": "note", "token": tok, "tab": "요약"}]
