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


def _sections(items: list[dict]) -> list[dict]:
    return [i for i in items if i["kind"] == "section"]


def test_sections_carry_translated_titles_and_match_either(tmp_path, monkeypatch):
    """번역된 논문 — 섹션에 번역 제목을 싣고, q는 원문·번역 제목 어느 쪽으로도 같은 섹션에 걸린다."""
    monkeypatch.setattr("narani.projects.all_projects", lambda: [])
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    items = mention_candidates(reg.wd_for, reg.register, tmp_path, tok, "")
    here = items[0]
    assert here["current"] and here["title"] == "Bespoke Bots" and here["title_tr"] == "맞춤형 봇"
    # 맨 앞 # 제목(= 논문 제목)은 섹션으로 다시 내지 않는다
    assert [(s["title"], s.get("title_tr")) for s in _sections(items)] == [
        ("2 Method", "2 방법"), ("3 Results", "3 결과")]
    by_ko = _sections(mention_candidates(reg.wd_for, reg.register, tmp_path, tok, "방법"))
    by_en = _sections(mention_candidates(reg.wd_for, reg.register, tmp_path, tok, "METHOD"))
    assert by_ko == by_en and [s["row"] for s in by_ko] == [_sections(items)[0]["row"]]
    # 공백을 빼고 쳐도 · 번역 제목으로 논문도 걸린다
    assert _sections(mention_candidates(reg.wd_for, reg.register, tmp_path, tok, "2방법")) == by_ko
    hit = mention_candidates(reg.wd_for, reg.register, tmp_path, tok, "맞춤")
    assert [i["kind"] for i in hit] == ["paper"] and hit[0]["current"]


def test_translated_title_order_follows_screen_language(tmp_path, monkeypatch):
    """화면 언어가 그 논문의 번역 언어면 tr_first — 창이 번역 제목을 앞에 쓴다."""
    monkeypatch.setattr("narani.projects.all_projects", lambda: [])
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    monkeypatch.setattr("narani.i18n.ui_lang", lambda: "ko")
    assert all(s.get("tr_first") for s in _sections(mention_candidates(reg.wd_for, reg.register, tmp_path, tok)))
    monkeypatch.setattr("narani.i18n.ui_lang", lambda: "en")
    secs = _sections(mention_candidates(reg.wd_for, reg.register, tmp_path, tok))
    assert secs and not any(s.get("tr_first") for s in secs) and all(s.get("title_tr") for s in secs)


def test_untranslated_paper_or_heading_has_no_translated_title(tmp_path, monkeypatch):
    monkeypatch.setattr("narani.projects.all_projects", lambda: [])
    reg = Registry(make_wd(tmp_path, ko=None))
    tok = next(iter(reg.by_token))
    items = mention_candidates(reg.wd_for, reg.register, tmp_path, tok, "")
    assert not any("title_tr" in i for i in items)
    assert mention_candidates(reg.wd_for, reg.register, tmp_path, tok, "방법") == []
    # 헤더를 번역하지 않은 섹션(둘이 같다)은 번역 제목을 싣지 않는다 · 마크다운 표기는 걷는다
    en = "# Bespoke Bots\n\nIntro.\n\n## **2** Method\n\nText.\n\n## 3 Results\n\nMore.\n"
    ko = "# 맞춤형 봇\n\n소개.\n\n## **2** Method\n\n본문.\n\n## 3 결과\n\n더.\n"
    reg2 = Registry(make_wd(tmp_path, "q", en=en, ko=ko))
    tok2 = next(iter(reg2.by_token))
    secs = _sections(mention_candidates(reg2.wd_for, reg2.register, tmp_path, tok2, ""))
    assert [(s["title"], s.get("title_tr")) for s in secs] == [("2 Method", None), ("3 Results", "3 결과")]


def test_other_papers_match_translated_title(tmp_path, monkeypatch):
    monkeypatch.setattr("narani.projects.all_projects", lambda: [])
    make_wd(tmp_path, "other", title="Bespoke Bots")
    reg = Registry()
    got = mention_candidates(reg.wd_for, reg.register, tmp_path, "", "맞춤형")
    assert [(i["kind"], i["title"], i.get("title_tr")) for i in got] == [("paper", "Bespoke Bots", "맞춤형 봇")]


def test_prompt_names_both_titles():
    ctx = _ctx(mentions=[{"kind": "section", "token": "abc", "title": "2 Method", "title_tr": "2 방법",
                          "tr_first": True, "row": 3, "end": 5}])
    block = mentions_prompt(ctx)
    assert "섹션 '2 Method' (번역 제목 '2 방법')" in block and "read_rows(start=3, end=5, token=abc)" in block
