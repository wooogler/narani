"""다음 입력 제안 — 표식 떼어 내기(흐르는 조각 경계 포함), 저장·렌더에 안 섞임, 설정 끄기, 창 연결."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, make_wd, scripted_model  # noqa: E402

from narani import config  # noqa: E402
from narani.ui import agent_panel  # noqa: E402
from narani.ui.agent import sessions, suggest  # noqa: E402
from narani.ui.agent.agent import make_agent, run_turn  # noqa: E402
from narani.ui.agent.context import ScreenContext  # noqa: E402
from narani.ui.agent.tools import Deps  # noqa: E402

BODY = "핵심은 **세 가지**입니다.\n\n- 방법\n- 결과"
ANSWER = BODY + "\n\n<<suggest>>\n이 기준으로 나눠 줘\n기준을 방법론 중심으로 바꿔 줘\n"


def _feed_all(chunks: list[str]) -> str:
    f = suggest.StreamFilter()
    out = "".join(f.feed(c) for c in chunks)
    return out + f.flush()


# --- 가르기 -------------------------------------------------------------


def test_split_basic():
    body, items = suggest.split(ANSWER)
    assert body == BODY
    assert items == ["이 기준으로 나눠 줘", "기준을 방법론 중심으로 바꿔 줘"]


def test_split_without_marker_keeps_text():
    assert suggest.split(BODY) == (BODY, [])
    assert suggest.split("") == ("", [])


def test_split_broken_or_messy_blocks():
    # 표식만 있고 제안 없음 → 본문만
    assert suggest.split(BODY + "\n<<suggest>>\n") == (BODY, [])
    # 대소문자·공백·복수형, 기호·번호·따옴표·닫는 표식·중복·@·너무 긴 줄
    messy = (BODY + "\n\n---\n<< Suggestions >>\n- \"방법 섹션 요약해 줘\"\n2) **결과 표로 보여 줘**\n"
             "- 방법 섹션 요약해 줘\n" + "가" * 200 + "\n@이 논문 기여 정리해 줘\n넷째 줄\n<</suggest>>\n")
    body, items = suggest.split(messy)
    assert body == BODY
    assert items == ["방법 섹션 요약해 줘", "결과 표로 보여 줘", "이 논문 기여 정리해 줘"]


def test_split_strips_fence_opened_for_the_block_but_keeps_closed_code():
    fenced = BODY + "\n\n```\n<<suggest>>\n하나 더\n```\n"
    assert suggest.split(fenced) == (BODY, ["하나 더"])
    code = "예:\n```python\nx = 1\n```"
    assert suggest.split(code + "\n<<suggest>>\n다음") == (code, ["다음"])


# --- 흐르는 조각 ----------------------------------------------------------


@pytest.mark.parametrize("text", [
    ANSWER,
    BODY + "\n\n```\n<<suggest>>\n하나\n```",
    BODY + " <<Suggest>>\n하나",
])
def test_stream_filter_any_two_way_split_hides_marker(text):
    body, _ = suggest.split(text)
    for i in range(len(text) + 1):
        out = _feed_all([text[:i], text[i:]])
        assert "<<" not in out and "suggest" not in out.lower(), i
        assert out.rstrip() == body.rstrip(), i


def test_stream_filter_char_by_char_never_shows_partial_marker():
    f = suggest.StreamFilter()
    shown = ""
    for ch in ANSWER:
        shown += f.feed(ch)
        assert "<" not in shown            # 반쪽 표식('<', '<<sug')도 비치지 않는다
    assert (shown + f.flush()).rstrip() == BODY


def test_stream_filter_releases_non_marker_lookalikes():
    text = "a < b 이고 <<x>> 는 표식이 아니다\n```\ncode\n```\n---"
    assert _feed_all(list(text)) == text   # 붙잡았던 꼬리도 끝에는 다 나간다


def test_stream_filter_resets_per_text_part():
    f = suggest.StreamFilter()
    a = f.feed("앞 글\n<<suggest>>\n숨김")
    b = f.new_part("\n\n") + f.feed("뒤 글")
    assert a == "앞 글" and b == "\n\n뒤 글"


# --- 한 턴 ---------------------------------------------------------------


def _turn(tmp_path, steps, q="요약해 줘"):
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    agent = make_agent(scripted_model(steps))
    s = sessions.create()
    deps = Deps(wd_for=reg.wd_for, current_token=tok, register_token=reg.register,
                ctx=ScreenContext.model_validate({"screen": "review", "paper": {"token": tok}}))

    async def go():
        return [ev async for ev in run_turn(agent, s, q, deps)]

    return s, asyncio.run(go())


def test_turn_emits_suggestions_and_keeps_them_out_of_text(tmp_path):
    s, events = _turn(tmp_path, [ANSWER])
    streamed = "".join(e["data"]["delta"] for e in events if e["event"] == "text")
    htmls = [e["data"]["html"] for e in events if e["event"] in ("html", "done")]
    assert "<<" not in streamed and "&lt;&lt;" not in "".join(htmls)
    done = events[-1]["data"]
    assert done["suggestions"] == ["이 기준으로 나눠 줘", "기준을 방법론 중심으로 바꿔 줘"]
    assert done["text"] == BODY and "나눠 줘" not in done["html"]
    saved = sessions.load(s.id).turns[-1]
    assert saved["text"] == BODY and saved["suggestions"] == done["suggestions"]


def test_turn_without_marker_is_normal(tmp_path):
    s, events = _turn(tmp_path, [BODY])
    done = events[-1]["data"]
    assert done["text"] == BODY and done["suggestions"] == []
    assert "suggestions" not in sessions.load(s.id).turns[-1]


def test_setting_off_strips_but_does_not_suggest(tmp_path):
    config.set_section_value("ui", "agent_suggest_next", False)
    s, events = _turn(tmp_path, [ANSWER])
    done = events[-1]["data"]
    assert done["suggestions"] == [] and done["text"] == BODY
    assert "suggestions" not in sessions.load(s.id).turns[-1]


def test_instruction_follows_setting(tmp_path):
    seen: list = []
    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))

    def instr(on: bool) -> str:
        config.set_section_value("ui", "agent_suggest_next", on)
        agent = make_agent(scripted_model([BODY], seen=seen))
        deps = Deps(wd_for=reg.wd_for, current_token=tok, register_token=reg.register,
                    ctx=ScreenContext.model_validate({"screen": "review", "paper": {"token": tok}}))

        async def go():
            return [ev async for ev in run_turn(agent, sessions.create(), "안녕", deps)]

        asyncio.run(go())
        return seen[-1].instructions or ""

    assert suggest.MARK in instr(True)
    assert suggest.MARK not in instr(False)


def test_approval_wait_has_no_suggestions(tmp_path):
    _s, events = _turn(tmp_path, [[("remember", {"text": "한국어로 답해 줘"})], ANSWER], q="기억해 줘")
    names = [e["event"] for e in events]
    assert "approval_needed" in names
    assert not events[-1]["data"].get("suggestions")


# --- 창 ------------------------------------------------------------------


def test_panel_fills_input_without_sending():
    js = agent_panel.JS
    start = js.index("function fillInput(s){")
    body = js[start:js.index("\n  }\n", start)]
    assert "execCommand('insertText'" in body and "ask(" not in body and "send(" not in body
    assert "function suggestionsNow()" in js and "pending" in js[js.index("function suggestionsNow()"):][:200]
    assert 'class="ag-suggest"' in agent_panel.HTML
    assert "suggestions: L.done.suggestions" in js and "L.done.text" in js


def test_suggest_setting_route_saves_to_ui():
    import fastapi
    from fastapi.testclient import TestClient

    from narani.ui.agent.routes import register_routes

    app = fastapi.FastAPI()
    register_routes(app, wd_for=lambda t: None, register_token=lambda wd: "x")
    c = TestClient(app)
    assert c.get("/agent/models").json()["suggest"] is True        # 기본 켜짐
    assert c.put("/agent/prefs", json={"suggest": False}).json()["suggest"] is False
    assert config.load_config()["ui"]["agent_suggest_next"] is False
    assert c.get("/agent/models").json()["suggest"] is False


def test_marker_before_a_tool_call_does_not_swallow_the_final_answer(tmp_path):
    """앞 텍스트 부분(tool 호출 전)에 표식이 섞여도 tool 뒤의 최종 답은 흐른다."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    def n_resp(messages):
        return sum(1 for m in messages if isinstance(m, ModelResponse))

    async def stream(messages, info):  # noqa: ANN001, ANN202
        if n_resp(messages) == 0:
            yield "찾아볼게요\n<<suggest>>\n앞"
            yield {0: DeltaToolCall(name="outline", json_args="{}", tool_call_id="c0")}
            return
        yield ""
        yield "최종 답"

    def plain(messages, info):  # noqa: ANN001, ANN202
        return ModelResponse(parts=[TextPart("최종 답")]) if n_resp(messages) else \
            ModelResponse(parts=[ToolCallPart("outline", {}, tool_call_id="c0")])

    reg = Registry(make_wd(tmp_path))
    tok = next(iter(reg.by_token))
    agent = make_agent(FunctionModel(plain, stream_function=stream, model_name="scripted"))
    deps = Deps(wd_for=reg.wd_for, current_token=tok, register_token=reg.register,
                ctx=ScreenContext.model_validate({"screen": "review", "paper": {"token": tok}}))

    async def go():
        return [ev async for ev in run_turn(agent, sessions.create(), "목차", deps)]

    events = asyncio.run(go())
    streamed = "".join(e["data"]["delta"] for e in events if e["event"] == "text")
    assert "최종 답" in streamed and "<<" not in streamed and "\n앞" not in streamed


# --- 블록이 빠졌을 때: 따로 짓기 ------------------------------------------------


def test_parse_items_and_generate_tolerate_marker_and_numbering():
    from narani.llm.base import FakeProvider

    assert suggest.parse_items("1. 하나\n- \"둘\"\n하나\n") == ["하나", "둘"]
    seen = {}

    def fn(system, user):
        seen["system"], seen["user"] = system, user
        return "<<suggest>>\n1) Summarize the results too\n- @Compare with paper B\n"

    got = suggest.generate(FakeProvider(fn), "What is the method?", "x" * 5000, 'paper "Wink"')
    assert got == ["Summarize the results too", "Compare with paper B"]
    assert "same language as the question" in seen["system"]
    assert 'On screen: paper "Wink"' in seen["user"] and "x" * 1501 not in seen["user"]


def _route_client(tmp_path, provider_fn=None, *, boom=False):
    import fastapi
    from fastapi.testclient import TestClient

    from narani.llm.base import FakeProvider
    from narani.ui.agent.routes import register_routes

    calls: list = []

    def make():
        if boom:
            raise RuntimeError("키 없음")
        calls.append(1)
        return FakeProvider(provider_fn or (lambda a, b: "방법 섹션도 요약해 줘\n한계도 알려 줘"))

    reg = Registry(make_wd(tmp_path))
    app = fastapi.FastAPI()
    register_routes(app, wd_for=reg.wd_for, register_token=reg.register, build_title_provider=make)
    return TestClient(app), calls


def _answered(text=BODY, **extra) -> sessions.Session:
    s = sessions.create()
    s.turns += [{"id": "u1", "role": "user", "text": "요약해 줘"},
                {"id": "a1", "role": "assistant", "text": text, "html": "", "citations": [], **extra}]
    return sessions.save(s)


def test_fallback_route_fills_missing_suggestions(tmp_path):
    c, calls = _route_client(tmp_path)
    s = _answered()
    r = c.post(f"/agent/sessions/{s.id}/suggest",
               json={"turn_id": "a1", "context": {"screen": "home", "project": {"name": "튜터링"}}})
    assert r.json()["suggestions"] == ["방법 섹션도 요약해 줘", "한계도 알려 줘"]
    assert sessions.load(s.id).turns[-1]["suggestions"] == r.json()["suggestions"]
    # 이미 있으면 다시 묻지 않는다
    assert c.post(f"/agent/sessions/{s.id}/suggest", json={"turn_id": "a1"}).json()["suggestions"] == []
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["error", "empty", "stale", "pending", "off", "boom", "junk"])
def test_fallback_route_skips_and_fails_silently(tmp_path, monkeypatch, case):
    c, calls = _route_client(tmp_path, (lambda a, b: "   \n```\n") if case == "junk" else None,
                             boom=case == "boom")
    s = _answered(**({"error": "실패"} if case == "error" else {}), text="" if case == "empty" else BODY)
    if case == "pending":
        monkeypatch.setattr(sessions.Session, "pending", property(lambda self: {"calls": [{"id": "c1"}]}))
    if case == "off":
        config.set_section_value("ui", "agent_suggest_next", False)
    turn = "older" if case == "stale" else "a1"
    r = c.post(f"/agent/sessions/{s.id}/suggest", json={"turn_id": turn, "context": {"bogus": 1}})
    assert r.status_code == 200 and r.json() == {"suggestions": []}
    assert "suggestions" not in sessions.load(s.id).turns[-1]
    if case in ("error", "empty", "stale", "pending", "off"):
        assert calls == []           # 돈 드는 호출 자체를 안 한다


def test_fallback_does_not_overwrite_a_newer_save(tmp_path):
    """LLM을 기다리는 사이 다른 저장(새 턴)이 있으면 옛 사본으로 덮지 않고, 제안도 붙이지 않는다."""
    s = _answered()

    def slow(system, user):
        fresh = sessions.load(s.id)
        fresh.turns.append({"id": "u2", "role": "user", "text": "다음 질문"})
        sessions.save(fresh)
        return "하나\n둘"

    c, _ = _route_client(tmp_path, slow)
    assert c.post(f"/agent/sessions/{s.id}/suggest", json={"turn_id": "a1"}).json()["suggestions"] == []
    turns = sessions.load(s.id).turns
    assert turns[-1]["id"] == "u2" and "suggestions" not in turns[-2]


def test_title_save_keeps_suggestions_saved_meanwhile(tmp_path):
    """첫 턴 뒤 제목 짓기와 제안 짓기가 함께 돈다 — 늦게 끝난 쪽이 다른 쪽 저장을 지우지 않는다."""
    s = sessions.create()
    s.title = sessions.default_title("요약해 줘")
    sessions.save(s)
    s.turns += [{"id": "u1", "role": "user", "text": "요약해 줘"},
                {"id": "a1", "role": "assistant", "text": BODY, "html": "", "citations": []}]
    sessions.save(s)

    def titler(system, user):
        fresh = sessions.load(s.id)
        fresh.turns[-1]["suggestions"] = ["하나"]
        sessions.save(fresh)
        return "논문 요약"

    c, _ = _route_client(tmp_path, titler)
    assert c.post(f"/agent/sessions/{s.id}/title").json()["title"] == "논문 요약"
    saved = sessions.load(s.id)
    assert saved.title == "논문 요약" and saved.turns[-1]["suggestions"] == ["하나"]


def test_panel_asks_fallback_and_shows_starter_chips():
    js = agent_panel.JS
    assert "if (suggestOn && !(L.done.suggestions || []).length) moreSuggest(at, L.done.turn_id);" in js
    body = js[js.index("function moreSuggest(id, tid){"):][:600]
    assert "'/suggest'" in body and "turns[i].id === tid" in body and ".catch(function(){})" in body
    empty = js[js.index("function emptyNode(){"):js.index("function moreSuggest(")]
    assert "if (!suggestOn)" in empty and "suggestNode(items)" in empty
    assert "md4T('이 컬렉션을 하위 컬렉션으로 나눠 줘')" in empty and "a.kind === 'collection'" in empty
    assert "답 속 숫자 칩을 누르면" in empty                 # 인용 칩 안내는 그대로
    assert "em.replaceWith(emptyNode())" in js               # 컬렉션을 바꾸면 시작 칩도
