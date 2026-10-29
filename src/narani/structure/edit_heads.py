"""편집기에서 고친 헤더를 구조(섹션 매니페스트)로 — 추출이 놓친 제목을 사용자가 `#`로 세우면 섹션이 된다.

en.md는 raw.md + sections.yaml로 만드는 파생물이라, 편집기에서 `## 제목`을 붙여도 구조 탭·목차의 섹션 짝·
번역 단위(sections.map)는 그 제목을 모른다. 그래서 저장할 때 '지금 구조로 조립했을 문서(base)'와 고친 문서
(mine)를 줄 단위로 견주어 헤더가 바뀐 자리를 찾고, base 줄의 raw.md 출처(§render.render_markdown origins)로
되짚어 구조에 옮긴다.

  · 본문 줄에 `#`를 붙였다 → raw.md의 그 줄을 헤더 줄로 고쳐 쓰고 새 섹션(added)으로 등록한다.
    제목이 문단 앞머리면 '제목 줄 + 나머지 본문'으로 쪼갠다(run-in과 같은 모양, 빈 줄은 사용자가 쓴 대로).
  · 섹션 줄의 `#` 수를 바꿨다 / 본문·기울임으로 둔 섹션에 `#`를 붙였다 → 그 섹션의 레벨만 바꾼다.
  · 헤더 줄의 `#`를 뗐다 → 그 섹션을 본문(skip)으로, `*제목*`으로 바꿨으면 기울임(italic)으로.
헤더 줄을 통째로 지운 것은 건드리지 않는다(재조립의 3-way 합치기가 지운 채로 둔다). raw.md에서 짝을 못 찾은
헤더는 수동 편집으로만 남기고 orphans로 알린다 — 짐작으로 엉뚱한 줄을 헤더로 만들지 않는다.

raw.md에 헤더 줄을 써 두는 까닭: 매니페스트에만 '이 본문 줄은 헤더'라고 적어 두면 레이아웃 수정(raw.md를 고쳐
구조를 처음부터 다시 잡는다)에서 사라진다. raw.md에 있으면 추출이 잡은 헤더와 똑같이 취급된다. 다시 변환은
raw.md를 PDF에서 새로 뽑으므로, 매니페스트의 added 표시로 같은 글의 줄을 찾아 다시 헤더로 세운다(promote).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

_HEAD_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_JUNK = r"(?:<[^>]+>|[*_`])*"  # 글자 사이에 끼어도 되는 표기 — 강조 표시·페이지 앵커 태그
_ITALIC_RE = re.compile(r"^\s*([*_])(?!\1)\S.*\S?\1\s*$")


def norm(text: str) -> str:
    """견주기용 글 — 헤더 표시·태그·링크 주소·강조 표시·공백 모양을 걷은 소문자."""
    t = _TAG_RE.sub("", text)
    t = _LINK_RE.sub(r"\1", t)
    t = re.sub(r"^\s*#{1,6}\s+", "", t)
    t = re.sub(r"[*_`]+", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def headings(lines: list[str]) -> dict[int, tuple[int, str]]:
    """줄 번호 → (레벨, 제목 글). 코드 펜스 안은 뺀다."""
    out: dict[int, tuple[int, str]] = {}
    fence = False
    for i, ln in enumerate(lines):
        if _FENCE_RE.match(ln):
            fence = not fence
            continue
        if fence:
            continue
        m = _HEAD_RE.match(ln)
        if m:
            out[i] = (len(m.group(1)), m.group(2).strip())
    return out


def signature(text: str) -> list[tuple[int, str]]:
    """헤더 모양 지문 — 저장할 때마다 견주어, 본문만 고쳤으면 구조 맞추기를 건너뛴다."""
    return [(lv, norm(t)) for _, (lv, t) in sorted(headings(text.splitlines()).items())]


def split_prefix(line: str, head: str) -> str | None:
    """line이 head 글로 시작하면 그 뒤에 남는 본문(원래 표기 그대로, 앞 구두점·닫는 강조 표시는 걷음).

    줄 전체가 제목이면 '', head로 시작하지 않으면(낱말 가운데서 끊겨도) None. 강조 표시·태그·`#`는 건너뛰며 맞춘다.
    """
    nt = norm(head)
    if not nt:
        return None
    pat = [r"^\s*(?:#{1,6}\s+)?", _JUNK]
    for ch in nt:
        pat.append(r"(?:\s|<[^>]+>|[*_`])+" if ch == " " else re.escape(ch) + _JUNK)
    m = re.compile("".join(pat), re.IGNORECASE).match(line)
    if not m:
        return None
    rest = line[m.end():]
    body = re.sub(r"^[*_`]*\s*[.:—–-]?[*_`]*\s*", "", rest).rstrip()
    if rest and body == rest and not rest[0].isspace():
        return None  # "Result" ↔ "Results …" — 낱말 가운데서 끊겼다
    return body


@dataclass
class Anchor:
    """raw.md에서 헤더로 세울 줄 하나."""

    line: int          # raw.md 줄 번호
    level: int
    text: str          # 제목 글 (사용자가 쓴 그대로)
    body: str = ""     # 쪼개고 남은 본문(raw 표기) — ''면 줄 전체가 제목
    gap: bool = True   # 제목과 본문 사이에 빈 줄 (사용자가 쓴 모양대로)
    sid: str | None = None  # 이미 섹션인 줄(긴 제목을 다시 쪼갬)이면 그 id

    def lines(self) -> list[str]:
        head = f"{'#' * self.level} {self.text}"
        if not self.body:
            return [head]
        return [head, "", self.body] if self.gap else [head, self.body]


@dataclass
class Plan:
    levels: dict[str, object] = field(default_factory=dict)  # 섹션 id → 새 레벨(1-6 | skip | italic)
    anchors: list[Anchor] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)  # raw.md에서 짝을 못 찾은 제목


def _next_text_line(lines: list[str], j: int) -> int | None:
    return next((k for k in range(j + 1, min(len(lines), j + 4)) if lines[k].strip()), None)


def _continues(mine: list[str], j: int, body: str | None) -> bool:
    """j번째 제목 줄 다음 글 줄이 body(쪼개고 남을 본문)로 시작하나 — 첫 낱말로 본다."""
    if not body:
        return False
    k = _next_text_line(mine, j)
    if k is None:
        return False
    a, b = norm(mine[k]).split(), norm(body).split()
    return bool(a and b) and a[0] == b[0]


def plan(base: list[str], origins: list, mine: list[str], sections: list, raw: list[str]) -> Plan:
    """base(지금 구조의 조립본)·mine(고친 en.md)을 견주어 구조에 옮길 변경을 고른다. 아무것도 쓰지 않는다.

    origins는 base 줄 → raw.md 줄(§render), sections는 매니페스트 섹션 목록, raw는 raw.md 줄들.
    """
    out = Plan()
    by_line = {s.line: s for s in sections}
    bh, mh = headings(base), headings(mine)

    def origin(i: int):  # noqa: ANN202
        return origins[i] if 0 <= i < len(origins) else None

    sm = difflib.SequenceMatcher(None, base, mine, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        used_b: set[int] = set()
        used_m: set[int] = set()
        for j in range(j1, j2):
            if j not in mh:
                continue
            lvl, text = mh[j]
            nt = norm(text)
            whole = [i for i in range(i1, i2) if i not in used_b and norm(base[i]) == nt]
            # 문단 앞머리를 제목으로 뗐으면 나머지 본문이 다음 줄로 이어진다 — 안 이어지면 제목 글을 줄인 것뿐이다
            prefix = [] if whole else [i for i in range(i1, i2) if i not in used_b
                                       and _continues(mine, j, split_prefix(base[i], text))]
            if not (whole or prefix):
                hb = next((i for i in range(i1, i2) if i in bh and i not in used_b), None)
                if hb is not None:  # 제목 글만 고친 헤더 — 글은 3-way 합치기가 살리고, 여기선 레벨만 본다
                    used_b.add(hb)
                    used_m.add(j)
                    sec = by_line.get(origin(hb))
                    if sec is not None and sec.level != lvl:
                        out.levels[sec.id] = lvl
                    continue
                _fallback(out, sections, text, lvl, origin(i1 - 1) if i1 else None)
                continue
            i = (whole or prefix)[0]
            used_b.add(i)
            used_m.add(j)
            r = origin(i)
            sec = by_line.get(r) if r is not None else None
            if r is None or r >= len(raw):
                out.orphans.append(text)
            elif whole:
                if sec is not None:
                    if sec.level != lvl:
                        out.levels[sec.id] = lvl
                elif not _HEAD_RE.match(raw[r]):
                    out.anchors.append(Anchor(r, lvl, text))
                else:
                    out.orphans.append(text)  # 섹션이 아닌 헤더 줄 — 생길 수 없는 모양이라 손대지 않는다
            elif sec is not None and sec.runin:
                # 문단 앞머리의 run-in 제목 — 레벨만 주면 렌더가 '제목 + 빈 줄 + 본문'으로 쪼갠다
                if sec.level != lvl:
                    out.levels[sec.id] = lvl
            else:
                body = split_prefix(raw[r], text)
                if not body:
                    out.orphans.append(text)  # raw.md 줄은 다른 글로 시작한다(인용 표기 …) — 손대지 않는다
                    continue
                k = _next_text_line(mine, j)
                out.anchors.append(Anchor(r, lvl, text, body, gap=k is None or k > j + 1,
                                          sid=sec.id if sec is not None else None))
        # `#`를 뗀 헤더 → 본문·기울임
        for i in range(i1, i2):
            if i not in bh or i in used_b:
                continue
            sec = by_line.get(origin(i))
            if sec is None or not isinstance(sec.level, int):
                continue
            nb = norm(bh[i][1])
            for j in range(j1, j2):
                if j in mh or j in used_m or norm(mine[j]) != nb:
                    continue
                used_m.add(j)
                out.levels[sec.id] = "italic" if _ITALIC_RE.match(mine[j]) else "skip"
                break
    return out


def _fallback(out: Plan, sections: list, text: str, lvl: int, near) -> None:  # noqa: ANN001
    """조립본에 줄이 없는 제목 — '위에 합침'으로 지웠던 섹션의 제목을 다시 쓴 것이면 그 섹션을 되살린다."""
    nt = norm(text)
    cands = [s for s in sections if s.level == "merge-up" and norm(s.text) == nt]
    if not cands:
        out.orphans.append(text)
        return
    pos = near if isinstance(near, int) else 0
    best = min(cands, key=lambda s: abs(s.line - pos))
    out.levels[best.id] = lvl


def promote(raw: str, sections: list) -> tuple[str, int]:
    """다시 뽑은 raw.md에 사용자가 세웠던 제목(added)을 다시 헤더 줄로. (새 raw, 세운 수).

    이미 헤더로 잡혔으면(추출이 나아졌다) 그대로 둔다. 찾는 범위는 앞뒤 섹션 제목 사이로 좁힌다 — 짧은 제목
    ("Results")이 엉뚱한 문단 첫머리에 붙지 않게. 줄 전체가 같은 글인 줄을 먼저, 없으면 문단 앞머리를 본다.
    """
    added = [s for s in sections if s.added and isinstance(s.level, int)]
    if not added:
        return raw, 0
    lines = raw.splitlines()
    n = 0
    order = list(sections)
    for s in added:
        heads = headings(lines)
        if any(norm(t) == norm(s.text) for _lv, t in heads.values()):
            continue
        k = order.index(s)
        lo, hi = 0, len(lines)
        for o in reversed(order[:k]):
            hit = _find_head(heads, o) if not o.added else None
            if hit is not None:
                lo = hit + 1
                break
        for o in order[k + 1:]:
            hit = _find_head(heads, o, lo) if not o.added else None
            if hit is not None:
                hi = hit
                break
        spot = _find_body(lines, s.text, lo, hi)
        if spot is None:
            continue
        i, body = spot
        lines[i:i + 1] = Anchor(i, int(s.level), s.text, body).lines()
        n += 1
    if not n:
        return raw, 0
    return "\n".join(lines) + ("\n" if raw.endswith("\n") else ""), n


def _find_head(heads: dict[int, tuple[int, str]], sec, start: int = 0) -> int | None:  # noqa: ANN001
    nt = norm(sec.text)
    return next((i for i in sorted(heads) if i >= start and norm(heads[i][1]) == nt), None)


def _find_body(lines: list[str], text: str, lo: int, hi: int) -> tuple[int, str] | None:
    fence = False
    whole, prefix = None, None
    for i in range(len(lines)):
        if _FENCE_RE.match(lines[i]):
            fence = not fence
            continue
        if fence or i < lo or i >= hi or _HEAD_RE.match(lines[i]):
            continue
        body = split_prefix(lines[i], text)
        if body == "" and whole is None:
            whole = (i, "")
        elif body and prefix is None:
            prefix = (i, body)
    return whole or prefix
