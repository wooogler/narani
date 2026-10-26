"""새 세션 제목 — 첫 질문·답을 보고 싼 모델이 한 줄로 짓는다. 사용자가 이미 이름을 바꿨으면 손대지 않는다."""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from narani.llm.base import FakeProvider  # noqa: E402
from narani.ui.agent import sessions  # noqa: E402
from narani.ui.agent.routes import clean_title, suggest_title  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)


def _session(q: str = "이 논문의 방법 섹션 핵심만 정리해 줄래? 표본 크기도 알려 줘") -> sessions.Session:
    s = sessions.create()
    s.turns = [{"id": "t1", "role": "user", "text": q},
               {"id": "t2", "role": "assistant", "text": "교수자 18명을 인터뷰했습니다."}]
    s.title = sessions.default_title(q)
    return s


def test_first_question_and_answer_make_a_short_title():
    seen = {}

    def fn(system, user):
        seen["user"] = user
        return '제목: "교수자 인터뷰 방법 요약".'
    s = _session()
    assert suggest_title(s, lambda: FakeProvider(fn)) == "교수자 인터뷰 방법 요약"
    assert "방법 섹션" in seen["user"] and "18명" in seen["user"]   # 질문·답 앞머리를 함께 본다


def test_user_renamed_session_is_left_alone():
    s = _session()
    s.title = "내가 붙인 이름"
    assert suggest_title(s, lambda: FakeProvider(lambda a, b: "다른 제목")) is None


def test_failure_keeps_question_title():
    def boom():
        raise RuntimeError("키 없음")
    assert suggest_title(_session(), boom) is None
    assert suggest_title(sessions.create(), lambda: FakeProvider(lambda a, b: "x")) is None   # 질문 없음


def test_clean_title_strips_wrapping_and_caps_length():
    assert clean_title("\n\n**Title: Cognitive offloading overview.**\nmore") == "Cognitive offloading overview"
    assert len(clean_title("가" * 80)) <= sessions.TITLE_CHARS + 1
