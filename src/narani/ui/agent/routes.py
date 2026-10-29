"""에이전트 창 HTTP 라우트 — 세션 CRUD, 턴(SSE 스트림), 모델 선택.

서버에는 클라이언트별 상태가 없다. 창은 매 요청에 세션 id와 화면 맥락을 실어 보내고, 서버는 세션
파일을 읽어 턴을 돌린 뒤 다시 저장한다. 그래서 화면을 옮겨도·앱을 다시 켜도 대화가 이어진다.

논문은 요청의 맥락에 든 token으로만 찾는다. 모르는 token은 404 — 뷰어 자산 라우트처럼
`state["current_wd"]`로 폴백하지 않는다(창이 여럿이면 엉뚱한 논문을 읽게 된다). 그러니 app.py는
`wd_for`로 **폴백 없는** 조회(`state["wd_by_token"].get`)를 넘겨야 한다.

재시작: 등록부는 서버 메모리라 다시 켜면 빈다. 그래도 대화 기록에는 앞 턴에서 받은 token이 남아
모델이 그것으로 tool을 부르므로(승인까지 받은 쓰기 재개도), 턴을 시작할 때마다 세션의 `papers`
(token → 작업 폴더)를 `register_token`으로 다시 등록한다. 없어진 폴더는 건너뛴다.

승인: 쓰기 tool을 부른 턴은 `approval_needed`로 끝나고 세션에 대기 호출이 남는다. 창은 같은 턴
라우트에 `approvals: {call_id: true|false}`를 보내 재개한다(빠진 id는 거절). 대기 중인데 approvals
없이 새 질문이 오면 대기 호출을 모두 거절로 닫고 그 질문을 잇는다 — 사용자가 카드를 무시하고
넘어간 것이다(닫지 않으면 Pydantic AI가 새 질문을 받지 않는다).

비용: 세션 누적 비용이 `config.resolve_agent_cost_cap()`(기본 $2)에 닿으면 턴을 시작하지 않고
400 `{"code": "cost_cap"}`. 창이 확인을 받아 `override: true`로 다시 보내면 그 턴만 허용한다.

작업: 비용 tool(jobtools)이 띄운 번역·용어집 작업은 세션 `jobs`에 남는다. `GET …/{sid}/jobs`가
레지스트리와 대조해 끝난 것의 결과(번역: 섹션 수·비용, 용어집: 항목 수·비용)를 채워 돌려준다 — 창은
미완료 작업이 있는 동안만 3초마다 부른다. 끝난 작업 비용은 세션 누적 비용에 들어가 상한 판정에도 쓰인다.

`build_agent`는 인자 없는 공장 함수다(기본: 설정의 [chat] 모델 + 그 키). 테스트는 FunctionModel
에이전트를 돌려주는 람다를 끼운다 — 옛 `chat_panel.register_routes`의 build_provider 주입과 같은 꼴.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from narani import config
from narani.i18n import t
from narani.ui.agent import sessions
from narani.ui.agent.agent import default_agent, run_turn
from narani.ui.agent.context import ScreenContext
from narani.ui.agent.tools import Deps
from narani.workdir import WorkDir

MAX_MESSAGE = 4000     # 질문 길이 상한
DENIED = "사용자가 거절했습니다."
SKIPPED = "사용자가 승인하지 않고 새 질문으로 넘어갔습니다. 이 작업은 실행되지 않았습니다."

# 지금 턴이 도는 세션 → 시작 시각. 한 세션에 턴 둘이 겹치면 대화 기록이 갈라지므로 두 번째는 409.
# 스트림이 시작도 못 하고 끊기면(finally가 안 돎) 표시가 남으므로 BUSY_TTL이 지나면 풀린 것으로 본다.
_BUSY: dict[str, float] = {}
_busy_lock = threading.Lock()
BUSY_TTL = 600.0


def model_options() -> list[dict]:
    """창 드롭다운에 채울 모델 목록 — 제공사별 저렴→비쌈, 키 없는 건 표시만 하고 못 고른다."""
    from narani.llm.base import PRICING

    keys = config.key_status()
    out: list[dict] = []
    for prov in config.PROVIDERS:
        for mid in config.MODEL_TIERS.get(prov, ()):
            pr = PRICING.get(mid)
            out.append({"provider": prov, "model": mid,
                        "price": (f"${pr[0]:g}/${pr[1]:g}" if pr else ""),
                        "has_key": bool(keys.get(prov))})
    return out


MENTION_PAPERS = 8      # @ 후보에 보일 다른 논문 수 (글자를 치면 그 글자로 거른다)


def mention_candidates(wd_for: Callable[[str], WorkDir | None], register_token: Callable[[WorkDir], str],
                       workspace: Path | None, paper: str = "", q: str = "") -> list[dict]:
    """입력창 `@` 후보 — 이 논문(그 섹션·메모 탭) → 다른 논문 → 컬렉션 순.

    값만 싣는다(§context.Mention). 섹션은 retrieval의 헤더 행으로 범위를 정한다 — 다음 같은·윗
    레벨 헤더 앞까지. q는 제목·저자·학회(논문), 이름(섹션·탭·컬렉션)에 든 글자로 거른다.
    """
    from narani import notes, projects
    from narani.ui.agent import retrieval
    from narani.workdir import paper_title, recent_workdirs

    qq = " ".join((q or "").split()).lower()

    def hit(*texts: str) -> bool:
        return not qq or any(qq in (t or "").lower() for t in texts)

    out: list[dict] = []
    wd = wd_for(paper) if paper else None
    if wd is not None:
        title = " ".join(paper_title(wd.root).split())[:120]
        if hit(title, "이 논문", "this paper"):
            out.append({"kind": "paper", "token": paper, "title": title, "current": True})
        try:
            chunks = retrieval.paper(wd).chunks
        except Exception:  # noqa: BLE001 — 본문을 못 읽어도 다른 후보는 보인다
            chunks = []
        heads = [c for c in chunks if c.head_level]
        last = chunks[-1].row if chunks else 0
        for i, c in enumerate(heads):
            nxt = next((h for h in heads[i + 1:] if h.head_level <= c.head_level), None)
            if hit(c.heading):
                out.append({"kind": "section", "token": paper, "title": c.heading[:120],
                            "row": c.row, "end": (nxt.row - 1) if nxt else last,
                            "level": c.head_level})
        try:
            tabs = notes.tab_names(wd)
        except Exception:  # noqa: BLE001
            tabs = []
        out += [{"kind": "note", "token": paper, "title": n, "tab": n} for n in tabs if hit(n)]
    ws = workspace or config.resolve_workspace()
    names = {p["id"]: p["name"] for p in projects.all_projects()}
    shown = 0
    for r in recent_workdirs(Path(ws), limit=100_000):
        if shown >= MENTION_PAPERS:
            break
        t = str(r.get("title") or r.get("name") or "")
        if not hit(t, " ".join(r.get("authors") or []), str(r.get("venue") or "")):
            continue
        tok = register_token(WorkDir(Path(r["root"])))
        if tok == paper:
            continue
        authors = ", ".join((r.get("authors") or [])[:2])
        sub = " · ".join(b for b in (str(r.get("year") or ""), authors) if b)
        out.append({"kind": "paper", "token": tok, "title": " ".join(t.split())[:120], "sub": sub})
        shown += 1
    out += [{"kind": "collection", "id": i, "title": n} for i, n in names.items() if hit(n)]
    return out


TITLE_SYSTEM = (
    "You name a chat session from its first question and answer. Reply with the title only: "
    "one short noun phrase (at most 6 words, or 20 characters in Korean/Japanese/Chinese), "
    "in the same language as the question, no quotes, no trailing period.")
TITLE_INPUT_MAX = 600   # 질문·답에서 제목을 짓는 데 넘기는 글자 수 (각각)
# 답 길이 상한 — 제목은 몇 낱말이지만 추론 모델(gpt-6 등)은 이 예산을 생각하는 데 먼저 쓴다.
# 40으로 두면 생각만 하다 끝나 빈 답이 왔다(실측) → 제목이 첫 질문 앞머리로 남았다. 짧게는 지시문이 맡는다.
TITLE_MAX_TOKENS = 1024


def title_provider():  # noqa: ANN201 — llm.base.Provider
    """제목 짓기용 LLM — 에이전트가 쓰는 서비스의 가장 싼 모델(한 줄짜리 일에 비싼 모델은 아깝다)."""
    prov, _model = config.resolve_chat_choice()
    tiers = config.MODEL_TIERS.get(prov) or ()
    return config.build_provider(prov, tiers[0] if tiers else None)


# 대화 → 메모 탭 'AI로 합치기' — 폰 리더(§phone.web MEMO_MERGE)와 같은 지시
MEMO_MERGE = ("You maintain a researcher's markdown note about one paper. Merge the NEW material into the EXISTING note: "
              "put each point where it belongs, keep everything already in the note (do not drop or shorten information), "
              "keep its headings, structure and language (write in the language the note is written in), keep [rN] "
              "paragraph markers as they are, and avoid repeating what the note already says. Return only the full "
              "updated note in markdown, with no preface.")


def memo_provider():  # noqa: ANN201 — llm.base.Provider
    """메모 합치기용 LLM — 에이전트가 쓰는 서비스·모델(설정 → AI)."""
    prov, model = config.resolve_chat_choice()
    return config.build_provider(prov, model)


def clean_title(raw: str) -> str:
    """모델 답 → 제목 한 줄 (따옴표·'제목:' 머리·끝 마침표를 떼고 세션 제목 길이로 자른다)."""
    line = next((x for x in (raw or "").splitlines() if x.strip()), "")
    wrap = "\"'“”‘’「」『』*#"
    line = line.strip().strip(wrap).strip()
    line = re.sub(r"^(title|제목)\s*[:：]\s*", "", line, flags=re.I)
    line = line.strip(wrap).strip().rstrip(".。").strip(wrap).strip()
    return sessions.default_title(line) if line else ""


def suggest_title(s: sessions.Session, make: Callable[[], object] = title_provider) -> str | None:
    """첫 질문·답으로 지은 제목. 이름을 사용자가 이미 바꿨거나(첫 질문 기본 제목과 다름) 짓지 못하면 None."""
    users = [t for t in s.turns if t.get("role") == "user" and (t.get("text") or "").strip()]
    if not users:
        return None
    first = users[0]["text"]
    if s.title and s.title != sessions.default_title(first):
        return None
    i = s.turns.index(users[0])
    answer = next((t.get("text") or "" for t in s.turns[i + 1:] if t.get("role") == "assistant"), "")
    try:
        llm = make()
        raw = llm.complete(TITLE_SYSTEM, f"Question:\n{first[:TITLE_INPUT_MAX]}\n\n"
                                         f"Answer (start):\n{answer[:TITLE_INPUT_MAX]}",
                           max_tokens=TITLE_MAX_TOKENS)
    except Exception:  # noqa: BLE001 — 키 없음·네트워크 — 질문 앞머리 제목이 남는다
        return None
    title = clean_title(raw)
    if not title:
        return None
    s.usage["cost_usd"] = float(s.usage.get("cost_usd") or 0.0) + float(getattr(llm, "cost", lambda: 0.0)())
    return title


def readiness(make: Callable[[], object] | None = None) -> tuple[bool, str]:
    """에이전트를 만들 수 있는지 (준비됐나?, 안내 메시지). 네트워크는 건드리지 않는다."""
    try:
        (make or default_agent)()
    except RuntimeError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 — 설정 파일 손상 등도 안내로 보여준다
        return False, str(e) or e.__class__.__name__
    return True, ""


def _detail(s: sessions.Session) -> dict:
    """세션 하나를 창에 그릴 형태로."""
    return {**s.meta(), "paper_tokens": s.paper_tokens, "turns": s.turns,
            "pending": s.pending, "usage": s.usage, "cost_cap": config.resolve_agent_cost_cap(),
            "jobs": s.jobs}


def _synced(s: sessions.Session) -> sessions.Session:
    """작업 기록을 레지스트리와 맞추고, 끝난 것이 있으면 저장한다."""
    if sessions.sync_jobs(s):
        sessions.save(s)
    return s


def _reregister(s: sessions.Session, wd_for: Callable[[str], WorkDir | None],
                register_token: Callable[[WorkDir], str]) -> None:
    """세션이 기억하는 논문 중 등록부에 없는 것을 다시 등록한다 (서버 재시작 뒤 대화를 이을 때).

    아직 작업 폴더인지(`structure/sections.yaml`)는 뷰어 라우트(app.review_view)와 같은 판정.
    """
    for tok, path in list(s.papers.items()):
        if wd_for(tok) is not None:
            continue
        wd = WorkDir(Path(path))
        if wd.sections_yaml.exists():
            register_token(wd)


def _sse(ev: dict) -> str:
    return f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"


def register_routes(fastapi_app, wd_for: Callable[[str], WorkDir | None],  # noqa: ANN001
                    register_token: Callable[[WorkDir], str],
                    build_agent: Callable[[], object] | None = None,
                    workspace: Callable[[], Path] | None = None,
                    build_job_provider: Callable | None = None,
                    build_title_provider: Callable[[], object] | None = None,
                    build_memo_provider: Callable[[], object] | None = None) -> None:
    """`/agent/...` 라우트 등록.

    wd_for: 토큰 → WorkDir (모르면 None; 폴백 금지).
    register_token: WorkDir → 토큰 (list_papers가 찾은 논문을 wd_for로 다시 찾을 수 있게 등록).
    build_agent: 인자 없는 에이전트 공장 (기본: `agent.default_agent`).
    workspace: list_papers가 훑을 작업 폴더를 돌려주는 함수 (기본: config 작업 폴더).
    build_job_provider: 비용 tool의 LLM 공장 `(purpose, WorkDir) → Provider`
        (기본: `jobtools.default_job_provider` — 앱의 번역 설정). 테스트는 FakeProvider를 끼운다.
    build_title_provider: 세션 제목 짓기 LLM 공장 (기본: `title_provider` — 에이전트 서비스의 가장 싼 모델).
    """
    from fastapi.responses import JSONResponse, StreamingResponse
    from pydantic import ValidationError
    from pydantic_ai import DeferredToolResults
    from pydantic_ai.tools import ToolDenied
    from starlette.exceptions import HTTPException

    make = build_agent or default_agent

    def _results(ids: list[str], ok: dict[str, bool], pending: dict,
                 why: str = DENIED) -> DeferredToolResults:
        """승인/거절 → 재개 입력. token 없이 부른 쓰기가 승인을 요청한 때의 논문에 쓰도록
        그 논문을 호출별 metadata로 싣는다 (tool은 ctx.tool_call_metadata["token"]으로 받는다)."""
        meta = {"token": pending.get("token")}
        return DeferredToolResults(
            approvals={i: True if ok.get(i) else ToolDenied(why) for i in ids},
            metadata={i: dict(meta) for i in ids})

    def _models_payload() -> dict:
        ok, reason = readiness(make)
        provider, model = config.resolve_chat_choice()
        return {"provider": provider, "model": model, "models": model_options(),
                "ready": ok, "reason": reason}

    def _load_or_404(sid: str) -> sessions.Session:
        s = sessions.load(sid)
        if s is None:
            raise HTTPException(status_code=404)
        return s

    # ---- 대화 → 논문 메모 탭 (창의 '메모로' 단추) — 덧붙이기, 또는 AI로 합친 판을 미리 보고 바꾸기 ----
    def _wd_or_404(tok: str) -> WorkDir:
        wd = wd_for(tok)
        if wd is None:
            raise HTTPException(status_code=404)
        return wd

    @fastapi_app.get("/agent/memo/{tok}")
    def _memo_tabs(tok: str):  # noqa: ANN202 — 메모 탭 목록 (내용이 있는지)
        from narani import notes

        wd = _wd_or_404(tok)
        return {"tabs": [{"name": n, "chars": len(notes.read(wd, n).strip())} for n in notes.tab_names(wd)]}

    @fastapi_app.post("/agent/memo/{tok}/merge")
    def _memo_merge(tok: str, body: dict):  # noqa: ANN202 — AI로 합친 판(저장하지 않는다) + 바탕 지문
        from narani import notes

        wd, tab, text = _wd_or_404(tok), str(body.get("tab") or ""), str(body.get("text") or "").strip()
        old, stamp = notes.read_stamped(wd, tab)
        if not text or not old.strip():
            return JSONResponse({"error": "합칠 메모나 글이 없습니다"}, status_code=400)
        try:
            out = (build_memo_provider or memo_provider)().complete(
                MEMO_MERGE, f"EXISTING NOTE:\n{old}\n\nNEW MATERIAL (from a chat about the paper):\n{text}", max_tokens=8192)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)}, status_code=502)
        return {"merged": out.strip(), "stamp": stamp}

    @fastapi_app.post("/agent/memo/{tok}")
    def _memo_write(tok: str, body: dict):  # noqa: ANN202 — 덧붙이기(append) · 합친 판으로 바꾸기(replace, 바탕 지문 확인)
        from narani import notes

        wd, text, mode = _wd_or_404(tok), str(body.get("text") or "").strip(), body.get("mode") or "append"
        tab = notes.clean_name(str(body.get("tab") or "")) or notes.default_name()
        if not text or mode not in ("append", "replace"):
            return JSONResponse({"error": "쓸 내용이 없습니다"}, status_code=400)
        if tab not in notes.tab_names(wd):
            tab = notes.add(wd, tab)
        old, stamp = notes.read_stamped(wd, tab)
        try:
            if mode == "replace":
                notes.write(wd, tab, text + "\n", expect=body.get("stamp"), keep_old=True)
            else:
                notes.write(wd, tab, (old.rstrip() + "\n\n" if old.strip() else "") + text + "\n", expect=stamp)
        except notes.Conflict:
            return JSONResponse({"error": "그사이 메모가 바뀌었습니다 — 다시 시도하세요"}, status_code=409)
        return {"tab": tab, "refresh": {"kind": "refresh", "what": "notes", "token": tok, "tab": tab}}

    @fastapi_app.get("/agent/sessions")
    def _list():  # noqa: ANN202 — 세션 목록 (최근순)
        return {"sessions": sessions.list_sessions()}

    @fastapi_app.post("/agent/sessions")
    def _create(body: dict | None = None):  # noqa: ANN202 — 새 세션
        title = str((body or {}).get("title") or "").strip() or None
        return _detail(sessions.create(title=title))

    @fastapi_app.get("/agent/sessions/{sid}")
    def _get(sid: str):  # noqa: ANN202 — 세션 본문 (턴·승인 대기·사용량·작업)
        return _detail(_synced(_load_or_404(sid)))

    @fastapi_app.get("/agent/sessions/{sid}/jobs")
    def _jobs(sid: str):  # noqa: ANN202 — 세션이 띄운 백그라운드 작업 (레지스트리와 대조해 갱신)
        s = _synced(_load_or_404(sid))
        return {"jobs": s.jobs, "usage": s.usage, "cost_cap": config.resolve_agent_cost_cap()}

    @fastapi_app.patch("/agent/sessions/{sid}")
    def _rename(sid: str, body: dict):  # noqa: ANN202 — 이름 바꾸기
        s = sessions.rename(sid, str((body or {}).get("title") or ""))
        if s is None:
            raise HTTPException(status_code=404)
        return s.meta()

    @fastapi_app.post("/agent/sessions/{sid}/title")
    def _auto_title(sid: str):  # noqa: ANN202 — 첫 질문·답으로 제목 짓기 (창이 첫 턴 뒤에 부른다)
        s = _load_or_404(sid)
        title = suggest_title(s, build_title_provider or title_provider)
        if title:
            s.title = title
            sessions.save(s)
        return s.meta()

    @fastapi_app.delete("/agent/sessions/{sid}")
    def _delete(sid: str):  # noqa: ANN202 — 세션 지우기
        if not sessions.delete(sid):
            raise HTTPException(status_code=404)
        return {"ok": True}

    @fastapi_app.post("/agent/sessions/{sid}/turns")
    async def _turn(sid: str, body: dict):  # noqa: ANN202 — 질문(또는 승인) → SSE 스트림
        s = await asyncio.to_thread(sessions.load, sid)
        if s is None:
            raise HTTPException(status_code=404)
        await asyncio.to_thread(_synced, s)    # 끝난 작업 비용까지 넣고 상한을 본다
        # 화면 token 검사보다 먼저 — 재시작 직후 화면의 논문도 이 표로 되살아난다
        await asyncio.to_thread(_reregister, s, wd_for, register_token)
        body = body or {}
        try:
            ctx = ScreenContext.model_validate(body.get("context") or {})
        except ValidationError as e:
            return JSONResponse({"error": t("화면 맥락 형식 오류: {err}", err=e.errors()[:1])},
                                status_code=400)
        tok = ctx.token
        if tok and wd_for(tok) is None:
            raise HTTPException(status_code=404, detail="알 수 없는 논문 token")
        # @ 맥락 — 모르는 논문을 가리키는 것은 조용히 뺀다(재시작 뒤 옛 창이 보낸 token 등)
        ctx.mentions = [m for m in ctx.mentions
                        if m.kind == "collection" or (m.token and wd_for(m.token) is not None)]

        approvals = body.get("approvals")
        deferred = None
        message = str(body.get("message") or "").strip()[:MAX_MESSAGE]
        pending = s.pending
        if approvals:
            if pending is None or not isinstance(approvals, dict):
                return JSONResponse({"error": t("승인 대기 중인 작업이 없습니다.")}, status_code=400)
            want = [c["id"] for c in pending["calls"]]
            unknown = set(map(str, approvals)) - set(want)
            if unknown:
                return JSONResponse({"error": t("대기 중이 아닌 작업입니다: {ids}", ids=sorted(unknown))},
                                    status_code=400)
            got = {str(k): v for k, v in approvals.items()}
            # 일부만 와도 된다 — 답이 없는 호출은 거절로 닫는다(Pydantic AI는 전부 답해야 재개한다)
            deferred = _results(want, {i: got.get(i) is True for i in want}, pending)
        elif pending is not None and message:
            # 승인 카드를 두고 새 질문 — 대기 호출을 모두 거절로 닫고 질문을 잇는다
            want = [c["id"] for c in pending["calls"]]
            deferred = _results(want, dict.fromkeys(want, False), pending, why=SKIPPED)
        elif not message:
            return JSONResponse({"error": t("질문이 비어 있습니다.")}, status_code=400)

        cap = config.resolve_agent_cost_cap()
        spent = float(s.usage.get("cost_usd") or 0.0)
        if cap > 0 and spent >= cap and body.get("override") is not True:
            return JSONResponse(
                {"error": t("이 세션의 누적 비용(${spent:.4f})이 상한 ${cap:g}에 닿았습니다. "
                            "계속하면 이번 질문만 상한을 넘겨 진행합니다.", spent=spent, cap=cap),
                 "code": "cost_cap", "cost_usd": round(spent, 6), "cost_cap": cap},
                status_code=400)

        try:
            agent = make()
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        with _busy_lock:
            if time.time() - _BUSY.get(sid, 0.0) < BUSY_TTL:
                return JSONResponse({"error": t("이 세션에서 이미 답을 만드는 중입니다.")},
                                    status_code=409)
            _BUSY[sid] = time.time()

        deps = Deps(wd_for=wd_for, current_token=tok, ctx=ctx, register_token=register_token,
                    workspace=workspace() if workspace else None,
                    build_job_provider=build_job_provider)

        async def stream():  # noqa: ANN202
            try:
                # 승인 재개도 질문과 함께 올 수 있다 (approvals + message, 또는 새 질문이 대기를 닫을 때)
                async for ev in run_turn(agent, s, message or None, deps,
                                         deferred_tool_results=deferred):
                    yield _sse(ev)
            except Exception as e:  # noqa: BLE001 — run_turn 밖 실패도 스트림 안에서 알린다
                yield _sse({"event": "error", "data": {"message": str(e)[:200]}})
            finally:
                with _busy_lock:
                    _BUSY.pop(sid, None)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @fastapi_app.get("/agent/mentions")
    def _mentions(paper: str = "", q: str = ""):  # noqa: ANN202 — 입력창 @ 후보
        return {"items": mention_candidates(wd_for, register_token,
                                            workspace() if workspace else None, paper, q)}

    @fastapi_app.get("/agent/models")
    def _models():  # noqa: ANN202 — 모델 목록 + 지금 선택 + 키 준비 상태
        return _models_payload()

    @fastapi_app.put("/agent/model")
    def _put_model(body: dict):  # noqa: ANN202 — 에이전트가 쓸 모델 고르기 ([chat] 설정 공유)
        prov = str((body or {}).get("provider") or "").strip()
        mid = str((body or {}).get("model") or "").strip()
        try:
            config.set_chat_choice(prov or None, mid or None)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"ok": True, **_models_payload()}
