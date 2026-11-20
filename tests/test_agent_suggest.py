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
