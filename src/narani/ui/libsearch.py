"""서재 검색 — 서재 전체(모든 논문의 제목·컬렉션·본문 문단·내 하이라이트와 메모)를 한 번에 찾는다.

홈 도구 줄의 돋보기·⌘K·메뉴 '서재 검색…'이 여는 창(§ui/libsearch_ui.py)이 `GET /libsearch?q=`로 부른다.

**에이전트 검색을 그대로 쓴다.** 검색 단위는 에이전트와 같은 뷰어 정렬 행(`retrieval._read_chunks` —
DOM `data-row`)이고 토크나이저도 같다(`retrieval.tokenize` — 영문 단어 + 한·중·일 바이그램). 그래서 원문으로도
번역으로도 찾고, 고른 문단은 에이전트 인용 칩과 같은 길(`__md4Agent.goto`)로 그 행에 간다.

**에이전트 캐시와 따로 둔다.** 에이전트의 논문별 캐시(`retrieval._cache`, 16편)를 서재 전체로 돌리면 대화 중인
논문들이 밀려난다. 여기서는 서재 전체를 담는 역색인 하나를 따로 들고, `retrieval.paper()`는 부르지 않는다 —
그 함수는 그림 설명을 짝지어 **논문 폴더에 쓴다**(figure_alt.ensure). 이 색인은 읽기만 한다.

**무효화는 같은 mtime 기준.** 본문은 (원문·번역·그림 설명 파일 mtime), 메모는 (annotations.json mtime)이
키다 — 하이라이트 하나 그었다고 논문 전체를 다시 정렬하지 않게 둘을 나눴다. 논문 목록(작업 폴더 훑기)은
`LIST_TTL`마다 한 번만 다시 훑고, 검색은 그사이 지금 색인으로 곧장 답한다(첫 글자가 빨라야 한다).
다시 만들기는 뒤 스레드가 한다 — 검색 요청은 기다리지 않고 `indexing: true`를 실어 창이 잠시 뒤 다시 묻게 한다.

BM25의 idf·평균 길이는 서재 전체 기준이다(논문 한 편 안이 아니라). 여러 낱말을 물으면 더 많이 맞춘 문단이
앞서도록 덮은 비율(coverage)을 곱한다 — 바이그램으로 쪼갠 한국어 질문이 '조사' 하나만 걸린 문단으로 흩어지지 않게.
"""

from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from narani.ui.agent import retrieval
from narani.workdir import WorkDir

LIST_TTL = 5.0          # 작업 폴더를 다시 훑는 최소 간격(초) — 그사이 검색은 지금 목록으로
CHECK_TTL = 1.0         # 색인에 든 논문의 mtime을 다시 보는 최소 간격(초)
MAX_PAPERS = 8          # 논문(제목·저자·학회·연도) 결과 수
MAX_COLLECTIONS = 6     # 컬렉션 결과 수
MAX_BODY_PAPERS = 8     # 본문 결과를 묶을 논문 수
MAX_ROWS_PER_PAPER = 3  # 논문 하나에 보일 문단 수
MAX_NOTES = 10          # 하이라이트·메모 결과 수
SNIPPET = 220           # 문단 미리보기 길이(글자)
K1, B = 1.5, 0.75


# --- 텍스트 정리·강조 ---------------------------------------------------

_IMG_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)")
_TAG_RE = re.compile(r"<[^>]{1,200}>")
_HEAD_RE = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
_MARK_RE = re.compile(r"\*+|__|`+|^\s*>\s?|\|", re.MULTILINE)   # 강조·코드·인용·표 기호


def plain(md: str) -> str:
    """마크다운 블록 → 미리보기용 평문 (그림 줄·링크 꼬리·태그·강조 기호를 걷고 공백을 하나로)."""
    s = _IMG_RE.sub(" ", md or "")
    s = _LINK_RE.sub(r"\1", s)
    s = _TAG_RE.sub(" ", s)
    s = _HEAD_RE.sub("", s)
    s = _MARK_RE.sub(" ", s)
    return " ".join(s.split())


def norm(text: str) -> str:
    """제목·이름 비교용 — NFKC·대소문자·공백을 걷는다 (에이전트 @ 후보와 같은 규칙)."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "").casefold())


def hit_spans(text: str, qtoks: set[str]) -> list[list[int]]:
    """평문에서 질문 토큰에 맞는 자리들 [[시작, 끝], …] — tokenize와 같은 규칙(단어 스테밍·바이그램)으로 찾는다."""
    if not text or not qtoks:
        return []
    low = text.lower()
    if len(low) != len(text):   # 소문자로 바꾸며 길이가 변하는 글자(드묾) — 자리가 어긋나니 원문 그대로
        low = text
    spans: list[tuple[int, int]] = []
    for m in retrieval._WORD_RE.finditer(low):
        w = m.group(0).rstrip(".-")
        if w and retrieval._stem(w) in qtoks:
            spans.append((m.start(), m.start() + len(w)))
    for m in retrieval._BIGRAM_RE.finditer(low):
        s, base = m.group(0), m.start()
        if len(s) == 1:
            if s in qtoks:
                spans.append((base, base + 1))
            continue
        for i in range(len(s) - 1):
            if s[i:i + 2] in qtoks:
                spans.append((base + i, base + i + 2))
    spans.sort()
    out: list[list[int]] = []
    for a, b in spans:          # 겹치거나 붙은 자리는 하나로 ('설문'+'문조'+'조사' → '설문조사')
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def snippet(text: str, qtoks: set[str], width: int = SNIPPET) -> dict:
    """첫 맞은 자리 둘레를 width 글자쯤 잘라 {text, hits}. 맞은 데가 없으면 앞머리."""
    spans = hit_spans(text, qtoks)
    if len(text) <= width:
        return {"text": text, "hits": spans}
    first = spans[0][0] if spans else 0
    start = max(0, first - width // 3)
    if start:
        sp = text.rfind(" ", 0, start)
        start = sp + 1 if sp >= 0 and start - sp < 20 else start
    end = min(len(text), start + width)
    if end < len(text):
        sp = text.find(" ", end)
        end = sp if 0 <= sp - end < 20 else end
    cut = text[start:end]
    hits = [[max(a, start) - start, min(b, end) - start] for a, b in spans if b > start and a < end]
    pre, post = ("…" if start else ""), ("…" if end < len(text) else "")
    if pre:
        hits = [[a + 1, b + 1] for a, b in hits]
    return {"text": pre + cut + post, "hits": hits}


# --- 역색인 -------------------------------------------------------------


@dataclass
class _Postings:
    """문서 묶음 하나(논문 한 편의 본문 행들, 또는 메모들)의 역색인 — 서재 전체 통계에 더하고 뺀다."""

    post: dict[str, list[tuple[int, int]]] = field(default_factory=dict)   # 토큰 → [(문서 번호, tf)]
    length: list[int] = field(default_factory=list)

    @classmethod
    def build(cls, texts: list[str]) -> _Postings:
        p = cls()
        for i, text in enumerate(texts):
            toks = retrieval.tokenize(text)
            p.length.append(len(toks))
            for tok, f in Counter(toks).items():
                p.post.setdefault(tok, []).append((i, f))
        return p


class _Stats:
    """서재 전체 BM25 통계 — 문서 수·총 길이·토큰별 문서 빈도. 논문 판이 바뀌면 옛 것을 빼고 새 것을 더한다."""

    def __init__(self) -> None:
        self.n = 0
        self.total = 0
        self.df: Counter = Counter()

    def add(self, p: _Postings, sign: int = 1) -> None:
        self.n += sign * len(p.length)
        self.total += sign * sum(p.length)
        for tok, lst in p.post.items():
            self.df[tok] += sign * len(lst)
            if self.df[tok] <= 0:
                del self.df[tok]

    def idf(self, tok: str) -> float:
        d = self.df.get(tok, 0)
        return math.log(1 + (self.n - d + 0.5) / (d + 0.5)) if d else 0.0

    @property
    def avgdl(self) -> float:
        return (self.total / self.n) if self.n else 1.0


def _score(groups: list[tuple[object, _Postings]], stats: _Stats, qtoks: list[str]) -> list[tuple[float, object, int]]:
    """여러 묶음을 한 번에 BM25로 — [(점수, 묶음 주인, 문서 번호)] 높은 순. 덮은 비율을 곱한다."""
    wanted = Counter(qtoks)
    if not wanted:
        return []
    idf = {t: stats.idf(t) for t in wanted}
    avg = stats.avgdl
    out: list[tuple[float, object, int]] = []
    for owner, p in groups:
        acc: dict[int, float] = {}
        cover: dict[int, int] = {}
        for t, qn in wanted.items():
            lst = p.post.get(t)
            if not lst or not idf[t]:
                continue
            w = idf[t] * (1 + 0.15 * (qn - 1))
            for i, f in lst:
                dl = p.length[i] or 1
                acc[i] = acc.get(i, 0.0) + w * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / avg))
                cover[i] = cover.get(i, 0) + 1
        n_q = len(wanted)
        for i, s in acc.items():
            c = cover[i] / n_q
            out.append((s * c * c, owner, i))
    out.sort(key=lambda x: -x[0])
    return out


# --- 논문 한 편 ---------------------------------------------------------


@dataclass
class _Row:
    row: int
    heading: str
    en: str      # 평문
    ko: str      # 평문 (번역이 없으면 "")


@dataclass
class _Note:
    id: str
    row: int
    side: str
    color: str
    quote: str
    note: str


@dataclass
class _Entry:
    """색인에 든 논문 하나."""

    root: str
    meta: dict                              # 목록 줄 (제목·저자·학회·연도·소속·번역 제목)
    body_key: tuple = ()
    rows: list[_Row] = field(default_factory=list)
    body: _Postings = field(default_factory=_Postings)
    notes_key: tuple = ()
    notes: list[_Note] = field(default_factory=list)
    note_post: _Postings = field(default_factory=_Postings)


def _mt(path: Path) -> int | None:
    return retrieval._mtime(path)


def body_key(wd: WorkDir) -> tuple:
    return (_mt(wd.en_md), _mt(wd.ko_md), _mt(wd.figure_alts_json))


def notes_key(wd: WorkDir) -> tuple:
    return (_mt(wd.annotations_json),)


def _build_body(wd: WorkDir) -> tuple[list[_Row], _Postings]:
    """본문 행 — 에이전트와 같은 청크(메모 없이, 읽기만). 헤더뿐인 행·빈 행은 뺀다."""
    chunks = retrieval._read_chunks(wd, include_notes=False)
    rows: list[_Row] = []
    texts: list[str] = []
    for c in chunks:
        if not c.searchable:
            continue
        en, ko = plain(retrieval._body_of(c.en)), plain(retrieval._body_of(c.ko or ""))
        figs = " ".join(c.figures)
        if not (en or ko or figs):
            continue
        rows.append(_Row(c.row, retrieval._flat(c.heading), en or plain(figs), ko))
        texts.append(" ".join(x for x in (c.en, c.ko or "", figs) if x))
    return rows, _Postings.build(texts)


def _build_notes(wd: WorkDir, rows: list[_Row]) -> tuple[list[_Note], _Postings]:
    from narani.ui import annotations

    have = {r.row for r in rows}
    out: list[_Note] = []
    for raw in annotations.load(wd):
        n = retrieval._note_of(raw)
        if n is None or not (n["quote"] or n["note"]):
            continue
        row = n["row"]
        if row not in have and n["quote"]:        # 재조립으로 밀린 행 — 인용문으로 다시 찾는다
            probe = n["quote"][:40]
            row = next((r.row for r in rows if probe in r.en or probe in r.ko), row)
        out.append(_Note(n["id"], row, n["side"], n["color"], n["quote"], n["note"]))
    return out, _Postings.build([f"{n.quote} {n.note}" for n in out])


# --- 서재 색인 ----------------------------------------------------------


def _list_papers(workspace: Path) -> list[dict]:
    """작업 폴더의 논문들 (숨긴 논문 제외) + 번역 제목·비교용 문자열."""
    from narani.ui.agent.routes import _title_tr
    from narani.workdir import recent_workdirs

    out = []
    for r in recent_workdirs(Path(workspace), limit=100_000):
        title = " ".join(str(r.get("title") or r.get("name") or "").split())
        wd = WorkDir(Path(r["root"]))
        tr = (_title_tr(wd, title[:120]) if r.get("has_ko") else None) or ""
        authors = [str(a) for a in (r.get("authors") or [])]
        year = str(r.get("year") or "")
        venue = str(r.get("venue") or "")
        out.append({"root": str(Path(r["root"])), "title": title, "title_tr": tr, "authors": authors,
                    "year": year, "venue": venue, "pids": list(r.get("projects") or []),
                    "mtime": float(r.get("mtime") or 0.0),
                    "n_title": norm(title) + "\n" + norm(tr),
                    "n_all": "\n".join(norm(x) for x in (title, tr, " ".join(authors), venue, year))})
    return out


class LibraryIndex:
    """서재 전체 검색 색인 (프로세스에 하나 — `shared()`). 뒤 스레드가 채우고 검색은 기다리지 않는다."""

    def __init__(self, workspace: Callable[[], Path]) -> None:
        self.workspace = workspace
        self._lock = threading.Lock()          # 색인 구조를 바꾸거나 읽을 때
        self._refresh_lock = threading.Lock()  # 다시 만들기는 한 번에 하나
        self._entries: dict[str, _Entry] = {}
        self._body = _Stats()
        self._notes = _Stats()
        self._listed_at = 0.0
        self._listed_ws: str | None = None
        self._ready = False                    # 한 번이라도 끝까지 채웠는지
        self._checked_at = 0.0                 # 마지막으로 mtime을 본 때
        self._rebuilding = False               # 지금 바뀐 논문을 다시 만드는 중
        self._thread: threading.Thread | None = None
        self.last_build = 0.0                  # 마지막으로 끝까지 채우는 데 걸린 시간(초)

    # ---- 채우기 ----
    def refresh(self, force: bool = False) -> int:
        """작업 폴더를 (TTL이 지났으면) 다시 훑고 바뀐 논문만 다시 만든다. 다시 만든 논문 수를 돌려준다."""
        with self._refresh_lock:
            t0 = time.perf_counter()
            ws = str(self.workspace())
            now = time.monotonic()
            listed = None
            if force or ws != self._listed_ws or now - self._listed_at >= LIST_TTL:
                listed = _list_papers(Path(ws))
                self._listed_at, self._listed_ws = time.monotonic(), ws
            with self._lock:
                metas = listed if listed is not None else [e.meta for e in self._entries.values()]
            rebuilt = 0
            keep = set()
            for meta in metas:
                root = meta["root"]
                keep.add(root)
                wd = WorkDir(Path(root))
                with self._lock:
                    old = self._entries.get(root)
                bk, nk = body_key(wd), notes_key(wd)
                if old is not None and old.body_key == bk and old.notes_key == nk:
                    if listed is not None:
                        with self._lock:
                            old.meta = meta
                    continue
                self._rebuilding = True
                try:
                    if old is not None and old.body_key == bk:
                        rows, body = old.rows, old.body
                    else:
                        rows, body = _build_body(wd)
                    notes, note_post = _build_notes(wd, rows)
                except Exception:  # noqa: BLE001 — 깨진 논문 하나가 서재 검색 전체를 막지 않게
                    rows, body, notes, note_post = [], _Postings(), [], _Postings()
                new = _Entry(root, meta, bk, rows, body, nk, notes, note_post)
                with self._lock:
                    self._swap(old, new)
                rebuilt += 1
            if listed is not None:
                with self._lock:
                    for root in [r for r in self._entries if r not in keep]:   # 지웠거나 숨긴 논문
                        self._swap(self._entries[root], None)
            self._ready, self._rebuilding = True, False
            self._checked_at = time.monotonic()
            if rebuilt:
                self.last_build = time.perf_counter() - t0
            return rebuilt

    def _swap(self, old: _Entry | None, new: _Entry | None) -> None:
        if old is not None:
            self._body.add(old.body, -1)
            self._notes.add(old.note_post, -1)
            del self._entries[old.root]
        if new is not None:
            self._body.add(new.body)
            self._notes.add(new.note_post)
            self._entries[new.root] = new

    def kick(self) -> bool:
        """뒤에서 새로 고치기를 띄운다(이미 돌고 있으면 그대로). 지금 돌고 있는지를 돌려준다."""
        th = self._thread
        if th is not None and th.is_alive():
            return True
        if self._ready and time.monotonic() - self._checked_at < CHECK_TTL:
            return False
        self._thread = threading.Thread(target=self._bg, daemon=True, name="libsearch-index")
        self._thread.start()
        return True

    def _bg(self) -> None:
        try:
            self.refresh()
        except Exception:  # noqa: BLE001 — 다음 검색이 다시 띄운다
            pass

    def busy(self) -> bool:
        """아직 다 채우지 못했거나 바뀐 논문을 다시 만드는 중 — 창이 잠시 뒤 다시 묻는다."""
        th = self._thread
        alive = bool(th is not None and th.is_alive())
        return alive and (not self._ready or self._rebuilding)

    # ---- 찾기 ----
    def search(self, q: str, *, collections: list[dict] | None = None) -> dict:
        """네 묶음 — papers · collections · passages(논문별로 묶은 본문 문단) · notes. 각 묶음은 상한까지만."""
        q = " ".join((q or "").split())[:200]
        out: dict = {"q": q, "papers": [], "collections": [], "passages": [], "notes": []}
        if not q:
            return out
        terms = [norm(w) for w in q.split() if norm(w)]
        qtoks = retrieval.tokenize(q)
        qset = set(qtoks)
        with self._lock:
            entries = list(self._entries.values())
            out["papers"] = _match_papers(entries, q, terms)
            out["collections"] = _match_collections(collections or [], entries, terms)
            if qtoks:
                out["passages"] = self._passages(qtoks, qset)
                out["notes"] = self._match_notes(qtoks, qset)
        return out

    def _passages(self, qtoks: list[str], qset: set[str]) -> list[dict]:
        hits = _score([(e, e.body) for e in self._entries.values()], self._body, qtoks)
        groups: dict[str, dict] = {}
        for score, e, i in hits:
            g = groups.get(e.root)
            if g is None:
                if len(groups) >= MAX_BODY_PAPERS:
                    continue
                g = groups[e.root] = {"root": e.root, "title": e.meta["title"], "title_tr": e.meta["title_tr"],
                                      "score": score, "rows": [], "more": 0}
            if len(g["rows"]) >= MAX_ROWS_PER_PAPER:
                g["more"] += 1
                continue
            r = e.rows[i]
            en, ko = snippet(r.en, qset), snippet(r.ko, qset) if r.ko else None
            # 더 많이 맞은 쪽을 앞에 — 한국어로 물으면 번역 문단이, 영어로 물으면 원문이
            side = "ko" if ko and len(ko["hits"]) > len(en["hits"]) else "en"
            g["rows"].append({"row": r.row, "heading": r.heading[:120], "side": side,
                              "text": (ko if side == "ko" else en)["text"],
                              "hits": (ko if side == "ko" else en)["hits"]})
        return list(groups.values())

    def _match_notes(self, qtoks: list[str], qset: set[str]) -> list[dict]:
        hits = _score([(e, e.note_post) for e in self._entries.values()], self._notes, qtoks)
        out = []
        for _score_, e, i in hits[:MAX_NOTES]:
            n = e.notes[i]
            out.append({"root": e.root, "title": e.meta["title"], "id": n.id, "row": n.row, "side": n.side,
                        "color": n.color, "quote": snippet(n.quote, qset, 160),
                        "note": snippet(n.note, qset, 160) if n.note else None})
        return out

    def stats(self) -> dict:
        with self._lock:
            return {"papers": len(self._entries), "rows": self._body.n, "notes": self._notes.n,
                    "ready": self._ready, "last_build": round(self.last_build, 3)}


def _match_papers(entries: list[_Entry], q: str, terms: list[str]) -> list[dict]:
    """제목·번역 제목·저자·학회·연도에 낱말이 모두 든 논문 — 제목 통째 > 제목에 모두 > 나머지, 그다음 최근순."""
    if not terms:
        return []
    whole = norm(q)
    found = []
    for e in entries:
        m = e.meta
        if not all(t in m["n_all"] for t in terms):
            continue
        rank = 0 if whole in m["n_title"] else 1 if all(t in m["n_title"] for t in terms) else 2
        found.append((rank, -m["mtime"], e))
    found.sort(key=lambda x: (x[0], x[1]))
    return [{"root": e.root, "title": e.meta["title"], "title_tr": e.meta["title_tr"],
             "authors": e.meta["authors"][:3], "year": e.meta["year"], "venue": e.meta["venue"]}
            for _r, _m, e in found[:MAX_PAPERS]]


def _match_collections(projs: list[dict], entries: list[_Entry], terms: list[str]) -> list[dict]:
    """이름(또는 상위 경로)에 낱말이 모두 든 컬렉션 — 편수는 하위 컬렉션 논문까지."""
    from narani import projects

    if not terms or not projs:
        return []
    out = []
    for p in projs:
        name = str(p.get("name") or "")
        path = projects.path_name(p["id"], projs)
        if not all(t in norm(path) for t in terms):
            continue
        ids = {p["id"], *projects.descendants(p["id"], projs)}
        n = sum(1 for e in entries if ids & set(e.meta["pids"]))
        out.append({"id": p["id"], "name": name, "path": path, "count": n,
                    "_rank": 0 if all(t in norm(name) for t in terms) else 1})
    out.sort(key=lambda c: (c.pop("_rank"), c["path"].casefold()))
    return out[:MAX_COLLECTIONS]


_SHARED: dict = {}
_shared_lock = threading.Lock()


def shared(workspace: Callable[[], Path]) -> LibraryIndex:
    """프로세스에 하나뿐인 색인 (서버가 라우트를 등록할 때 만든다)."""
    with _shared_lock:
        idx = _SHARED.get("idx")
        if idx is None:
            idx = _SHARED["idx"] = LibraryIndex(workspace)
        return idx


def register_routes(fastapi_app, workspace: Callable[[], Path],  # noqa: ANN001
                    register_token: Callable[[WorkDir], str],
                    index: LibraryIndex | None = None) -> LibraryIndex:
    """`GET /libsearch?q=` 검색 · `POST /libsearch/warm` 미리 채우기 · `POST /libsearch/scope` 컬렉션 고르기.

    register_token: WorkDir → 토큰 — 결과 논문을 뷰어·에이전트 이동이 찾을 수 있게 등록한다(에이전트 라우트와 같은 것).
    라우트는 동기 함수다(FastAPI가 스레드에서 돌린다) — 색인을 읽는 동안 이벤트 루프를 막지 않는다.
    """
    from narani import projects
    from narani.ui.agent.tools import review_url

    idx = index or shared(workspace)

    def link(root: str) -> dict:
        wd = WorkDir(Path(root))
        return {"token": register_token(wd), "url": review_url(wd.root)}

    @fastapi_app.get("/libsearch")
    def _search(q: str = ""):  # noqa: ANN202
        t0 = time.perf_counter()
        idx.kick()
        res = idx.search(q, collections=projects.all_projects())
        for sec in ("papers", "passages", "notes"):
            for item in res[sec]:
                item.update(link(item.pop("root")))
        res["indexing"] = idx.busy()
        res["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return res

    @fastapi_app.post("/libsearch/warm")
    def _warm():  # noqa: ANN202 — 창을 열 때 (뒤에서 새로 고치기만 띄운다)
        idx.kick()
        return {"indexing": idx.busy(), **idx.stats()}

    @fastapi_app.post("/libsearch/scope")
    def _scope(body: dict):  # noqa: ANN202 — 다른 화면에서 고른 컬렉션 → 홈이 그 범위로 열리게
        pid = str((body or {}).get("id") or "")
        if not projects.exists(pid):
            return {"ok": False}
        projects.set_active(pid)
        return {"ok": True}

    return idx
