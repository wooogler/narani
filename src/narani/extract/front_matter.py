"""앞부분(front matter) 정규화 — LLM이 블록에 라벨을 매기고, 코드가 원본 블록만으로 재조립.

첫 페이지는 논문에서 포맷 편차가 가장 크다(ACM 2단·IEEE·SAGE·arXiv…). 다열 저자 그리드를
추출기가 잘못 읽으면 저자 일부가 헤더로, 나머지는 초록 뒤로 흩어지고 저작권/venue/ISBN 조각이
끼어든다. 규칙만으론 새 포맷마다 깨지므로 LLM을 하이브리드로 쓴다:

  LLM은 "어느 블록이 제목/저자/유지섹션/본문시작인지"만 판단(라벨) → 코드가 **원본 블록만으로**
  재조립. 출력 텍스트가 전부 원본에서 오므로 포맷에 강하고 환각(내용 조작) 위험이 없다.

키가 없거나 LLM 결과가 검증을 통과 못 하면 보수적 규칙(normalize_heuristic)으로 폴백한다.
어느 경로든 본문(첫 번호형/Introduction 섹션 이후)은 절대 건드리지 않는다.
"""

from __future__ import annotations

import re

from narani.extract.docling_backend import (
    _ADDR_HINT_RE,
    _AUTHOR_SEG_RE,
    _EMAIL_RE,
    AUTHOR_NOTE_ANCHOR,
    _split_blocks,
)
from narani.ir import AuthorEntry, FrontMatterLayout
from narani.llm.base import Provider

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)\s*$")
_PERSON_RE = re.compile(r"^[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,3}$")
_SECTION_WORDS = frozenset({
    "abstract", "introduction", "keywords", "index terms", "references", "acknowledgments",
    "acknowledgements", "ccs concepts", "ccs", "acm reference format", "acm reference format:",
    "background", "related work", "conclusion", "appendix", "contents",
})
_ISBN_RE = re.compile(r"^\s*97[89][\d\s\-/]+\s*$")
_JUNK_RE = re.compile(
    r"^\s*(ACM|ISBN|ACM\s+ISBN.*|Permission to make digital|This work is licensed|"
    r"©.*|Copyright held.*|.*\bhttps?://doi\.org\b.*)\s*$",
    re.I,
)
_VENUE_RE = re.compile(r"\b(CHI|UIST|CSCW|IEEE|Proceedings)\b.*\b(ACM|pages|\d{4})\b")
# 서지 상용구 헤딩 — 추출기가 공백을 잃어 "ACMReference Format:"으로 내보내는 일이 있어(단 경계에서
# 붙는다) 공백 유무를 가리지 않는다. 이걸 못 알아보면 헤딩만 남고 본문은 junk로 지워져
# **빈 섹션**이 되고, 콜론 없는 변형("ACM Reference Format")은 _is_person을 통과해 저자로 둔갑한다.
_JUNK_HEADING_RE = re.compile(r"^\s*acm\s*reference\s*format\s*:?\s*$", re.I)
_MAX_FRONT = 50  # 이 안에서 첫 본문 섹션을 못 찾으면 front matter 판단 불가 → 손대지 않음
# 2단 조판 첫 페이지는 추출기가 좌우 단을 뒤집어 읽는 일이 있다 — Abstract 블록이 "1 Introduction"
# 뒤로 밀려난다. 본문 시작 추정 지점보다 이만큼 더 보여줘야 LLM이 그 블록을 지목할 수 있다.
_BODY_LOOKAHEAD = 8
_MAX_PULLED = 6  # 본문 뒤에서 앞으로 끌어올릴 수 있는 **총** 블록 수 상한 — 오판했을 때 피해를 제한
_MAX_RUN = 3     # 한 구간(front matter 헤딩 + 뒤따르는 블록)의 상한
# 문장이 끝난 표시 — 단이 잘려 생긴 조각은 정의상 문장 중간에서 끊기므로, 종결부호로 끝나는
# 블록은 "여기까지가 그 섹션"이라는 뜻이다. 이게 끌어올리기 구간의 끝 닻이다.
_TERMINAL_RE = re.compile(r"[.!?…。？！][\"'”’)\]]*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+•]\s|\(?\d+[.)]\s)")


# --- 공통 판별 -------------------------------------------------------------
def _heading(block: str) -> tuple[int, str] | None:
    m = _HEADING_RE.match(block.strip())
    return (len(m.group(1)), m.group(2).strip()) if m else None


def _disp_url(text: str) -> tuple[str, str | None]:
    m = _LINK_RE.match(text.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (text.strip(), None)


def _is_person(text: str) -> bool:
    disp, _ = _disp_url(text)
    disp = re.sub(r"[\*∗\d,]+$", "", disp).strip()  # 위첨자 소속번호·별표 제거
    if disp.lower().rstrip(":") in _SECTION_WORDS or _JUNK_HEADING_RE.match(disp):
        return False
    return bool(_PERSON_RE.match(disp))


# 저자 이름 끝의 각주/소속 마커 — 추출·LLM 판단에 따라 붙었다 떨어졌다 하므로 코드에서 항상 뗀다.
# (표기 결정: 마커는 **버리고**, 그 마커가 가리키는 저자 주석 본문은 저자 블록 뒤에 그대로 보여준다.)
_AUTHOR_MARK_RE = re.compile(r"[\s,]*(?:[*∗＊†‡§¶‖¹²³⁴⁵⁶⁷⁸⁹⁰\d]+[\s,]*)+$")


def strip_author_mark(name: str) -> str:
    """저자 이름 끝의 각주 마커(∗ * † ‡ § ¶, 위첨자·소속 번호)를 뗀다. 결과가 비면 원본 유지."""
    stripped = _AUTHOR_MARK_RE.sub("", name).strip()
    return stripped or name.strip()


# 추출 단계가 저자 주석에 심어 둔 앵커 — 라벨이 없어도 이 블록만은 버리지 않고 저자 뒤에 붙인다.
_AUTHOR_NOTE_RE = re.compile(rf'^\s*<a id="{AUTHOR_NOTE_ANCHOR}-\d+">')


_AUTHOR_NOTE_ANCHOR_RE = re.compile(rf'<a id="{AUTHOR_NOTE_ANCHOR}-\d+"></a>\s*')


# 앵커 없이도 저자 주석을 알아보는 표시 — 각주 기호로 시작하는 한 문단.
# (정규화 출력에서는 앵커를 떼므로, 이 표시가 없으면 **두 번째 정규화가 주석을 통째로 지운다**:
#  앵커가 사라진 블록은 _is_author_note를 통과 못 하고 _is_junk의 별표 규칙에 먹힌다.)
# 마크다운 강조(**굵게**)·글머리표(* 항목)와 구별한다: 별표 뒤에 또 별표나 공백이 오면 주석이 아니다.
_NOTE_MARK_RE = re.compile(
    r"^(?:[∗＊†‡§¶‖]\s*\S|\*(?![\*\s])\S|(?:Corresponding author|Equal contribution))", re.I)
# 블록 맨 앞의 각주 기호 — 성격 판정 전에 떼어 낸다(기호는 위치의 흔적이지 내용이 아니다).
_LEAD_MARK_RE = re.compile(r"^[∗＊*†‡§¶‖]\s*")


def _is_author_note(block: str) -> bool:
    """저자 주석 블록인지 — 추출 단계의 앵커, 또는 (앵커가 떨어진 뒤엔) 각주 기호로 판단."""
    s = block.strip()
    if _AUTHOR_NOTE_RE.match(s):
        return True
    return bool(_NOTE_MARK_RE.match(s)) and not _heading(s) and not _is_biblio_junk(s)


def _note_text(block: str) -> str:
    """저자 주석 블록에서 앵커를 뗀 본문만 — 앵커는 raw.md 안에서만 쓰는 표시다.

    렌더링에는 안 보이지만 번역·인용 등 뒤 단계가 다루는 텍스트에 남으면 잡음이 되므로,
    정규화 출력에는 내보내지 않는다.
    """
    return _AUTHOR_NOTE_ANCHOR_RE.sub("", block.strip(), count=1).strip()


def _is_affiliation(block: str) -> bool:
    s = block.strip()
    if "\n" in s or _heading(block):
        return False
    return bool(_EMAIL_RE.search(s)) and ("," in s or bool(_ADDR_HINT_RE.search(s)))


# --- 본문 뒤로 밀려난 저자 조각 되찾기 -------------------------------------
# 다열 저자 그리드를 추출기가 열 우선으로 읽으면 마지막 열(이름·소속·이메일)이 초록·CCS를 지나
# **본문 첫 문단 뒤**까지 밀려난다. LLM은 그 블록을 저자로 지목할 수 있지만, 지목만 믿고 통째로
# 옮기면 본문 문단을 도둑질할 수 있다. 그래서 코드가 블록 내용으로 한 번 더 판정한다:
# 사람 이름 / 소속·주소 한 줄 / 이메일 줄만 되찾고, 나머지는 본문에 그대로 둔다.
_CONTACT_MAX = 160   # 저자 칸 한 줄의 현실적 상한 — 이보다 길면 본문이 이어 붙은 것이다
_PROSE_TAIL_MIN = 40  # 이만큼 되면 "꼬리"가 아니라 본문 문장이다 (이메일 뒤 잔여분 판정)
_MAX_AUTHOR_PULL = 12  # 본문 뒤에서 되찾을 저자 조각 수 상한 — 오판했을 때 피해를 제한
_AUTHOR_SCAN = 24      # 본문 시작 뒤로 저자 조각을 찾아볼 최대 거리(블록 수)


def _is_author_cell(text: str) -> bool:
    """저자 그리드의 한 칸인지 — 이름·소속·이메일만 있고 **문장이 아니다**.

    이메일이 들어 있다는 것만으로는 부족하다("참가자는 study@x.edu로 연락했다"도 통과한다).
    이메일을 지우고 남은 것이 문장이면 본문이고, 소속 표지나 사람 이름이면 저자 칸이다.
    """
    disp, _ = _disp_url(text.strip())  # 마크다운 링크는 표시 텍스트로
    rest = _EMAIL_RE.sub(" ", disp).strip(" ,;·")
    if _TERMINAL_RE.search(rest) or ". " in rest:
        return False
    if not rest:
        return True  # 이메일만 있는 줄
    return bool(_ADDR_HINT_RE.search(rest) or _is_person(rest))


def _author_meta_span(block: str) -> tuple[str, str] | None:
    """본문 뒤 블록을 (되찾을 저자 메타, 본문에 남길 나머지)로 가른다. 저자 메타가 아니면 None.

    단 경계에서 저자 칸과 본문 문단이 **한 블록으로 이어 붙는** 일이 있다
    ("University of X City, ST, USA a@x.edu for designing more effective …").
    그때는 마지막 이메일까지를 저자 메타로 떼고 뒤의 산문은 본문에 되돌린다 —
    통째로 끌어오면 본문 문장이 앞부분으로 사라지고, 통째로 두면 소속 조각이 서론에 남는다.
    """
    s = block.strip()
    if "\n" in s:
        return None  # 여러 줄 블록은 저자 칸이 아니다
    h = _heading(s)
    if h:
        return (s, "") if _is_person(h[1]) else None
    if _is_person(s):  # 이름만 있는 줄 (ORCID 링크 포함)
        return (s, "")
    if len(s) <= _CONTACT_MAX:
        return (s, "") if _is_author_cell(s) else None
    # 길다 = 본문 문단이 이어 붙었다. 마지막 이메일 뒤를 본문으로 되돌린다.
    marks = list(_EMAIL_RE.finditer(s))
    if not marks:
        return None
    head, rest = s[:marks[-1].end()].strip(), s[marks[-1].end():].strip()
    if len(head) > _CONTACT_MAX or not _is_author_cell(head):
        return None
    if len(rest) < _PROSE_TAIL_MIN or _EMAIL_RE.search(rest):
        return None  # 짧은 꼬리는 메타의 일부(괄호·구두점)이지 본문 문장이 아니다
    return head, rest


def _author_tail_end(blocks: list[str], body_start: int) -> int:
    """본문 시작 뒤에 흩어진 저자 조각의 마지막 위치+1 — LLM에 보여줄 창을 여기까지 넓힌다.

    창이 좁으면 마지막 저자가 LLM에 **아예 보이지 않아** 조용히 누락된다(지목할 수 없는 건
    되찾을 수도 없다). 저자처럼 읽히는 블록이 없으면 body_start를 그대로 돌려주므로,
    정상 논문에서는 창이 넓어지지 않고 이 경로가 통째로 no-op이 된다.
    """
    last = body_start
    for i in range(body_start, min(len(blocks), body_start + _AUTHOR_SCAN)):
        if _author_meta_span(blocks[i]) is not None:
            last = i + 1
    return last


# 앞부분에 실리는 인용 상용구("A, B, and C. 2026. 제목. In …") — 저자 **순서**의 유일한 평문 근거다.
# 다열 그리드를 열 우선으로 읽힌 페이지에서는 블록 순서도 LLM 판단도 순서를 틀리는데, 이 줄은
# 논문이 스스로 적어 둔 순서라 맞다. 모든 저자 이름이 한 블록에 다 있을 때만 쓴다(부분 근거는 안 쓴다).
def _citation_order(entries: list[AuthorEntry], blocks: list[str]) -> list[AuthorEntry] | None:
    """저자 전원이 한 블록에 나열된 인용 줄을 찾아 그 등장 순서로 세운다. 없으면 None."""
    if len(entries) < 2:
        return None
    keys = [_alnum(e.name) for e in entries]
    if len(set(keys)) != len(keys) or any(not k for k in keys):
        return None
    for block in blocks:
        flat = _alnum(block)
        at = [flat.find(k) for k in keys]
        if any(p < 0 for p in at) or len(set(at)) != len(at):
            continue
        return [entries[i] for i in sorted(range(len(entries)), key=lambda i: at[i])]
    return None


def _is_biblio_junk(block: str) -> bool:
    """서지 상용구(허가문·저작권·ISBN·DOI·venue) — 각주 기호로 시작해도 주석이 아니라 상용구다.

    첫 페이지 아래 각주 영역에는 저자 주석과 출판사 상용구가 나란히 놓이고, 추출기가 둘 다
    같은 각주 기호를 달아 내보내는 조판이 있다. 기호는 그 블록이 각주 영역에 있었다는 표시일 뿐
    성격을 가르지 못하므로, 떼어 내고 내용으로 판정한다(안 그러면 허가문·ISBN이 저자 주석으로
    둔갑해 저자 블록 뒤에 실린다).
    """
    s = _LEAD_MARK_RE.sub("", block.strip(), count=1).strip()
    return bool(_JUNK_RE.match(s) or _ISBN_RE.match(s) or _VENUE_RE.search(s)) or s in ("ACM", "ISBN")


# 별표로 시작하는 짧은 블록을 통째로 버리던 규칙은 없앴다: 저자 각주는 _is_author_note가 주석으로
# 살려 두고(내용이다), 남는 건 마크다운 강등 표기뿐이었다 — **Sang Won Lee** 같은 저자 줄이
# 두 번째 정규화에서 junk로 사라졌다(정규화가 멱등이 아니게 되는 원인).


def _is_body_start(block: str) -> bool:
    h = _heading(block)
    if not h:
        return False
    t = h[1].lstrip("·* ").strip()
    return bool(re.match(r"^\d", t)) or t.lower().startswith("introduction")


def _find_body_start(blocks: list[str]) -> int | None:
    idx = next((i for i in range(1, len(blocks)) if _is_body_start(blocks[i])), None)
    return idx if (idx is not None and idx <= _MAX_FRONT) else None


def _render_author(block: str) -> str:
    """저자 헤더 블록 렌더 — 굵게(더는 섹션 아님)."""
    disp, url = _disp_url(_heading(block)[1])
    disp = strip_author_mark(disp)
    return f"**[{disp}]({url})**" if url else f"**{disp}**"


def _author_lines(block: str) -> list[str]:
    """저자 블록 → 표시 줄들. 헤더는 굵게, 이메일 2개 이상 뭉친 줄은 저자별로 분리."""
    h = _heading(block)
    if h and _is_person(h[1]):
        return [_render_author(block)]
    if len(_EMAIL_RE.findall(block)) >= 2:
        return [s.strip() for s in _AUTHOR_SEG_RE.findall(block) if s.strip()]
    return [block.strip()]


# --- 규칙 기반(폴백) -------------------------------------------------------
def _author_band_end(blocks: list[str], end: int) -> int:
    """저자 밴드(제목 다음 ~ 첫 비인명 헤딩)의 끝 인덱스.

    저자 그리드는 제목과 첫 섹션(Abstract·CCS·ACM Reference Format…) 사이에 통째로 놓인다.
    그 구간 안은 **추출기가 준 순서가 곧 읽기 순서**이므로 종류별로 모으지 않고 그대로 내보낸다.
    (모으면 이메일만 든 줄·소속만 든 줄이 저자로 인식되지 않아 뒤로 밀리고, 다열 그리드의
     행 우선 순서가 무너진다 — 저자 순서는 논문에서 사실이지 표기가 아니다.)
    """
    return next((i for i in range(1, end)
                 if _heading(blocks[i]) and not _is_person(_heading(blocks[i])[1])), end)


def normalize_heuristic(md: str) -> str:
    """규칙만으로 앞부분 정리 — 저자 헤더 강등 + 흩어진 소속줄 수집 + boilerplate 제거.

    파편화 신호(저자 헤더 있거나 소속줄 ≥2)가 있을 때만 재구성한다(정상 논문 보호).
    저자 밴드 안은 원문 순서 그대로 두고, 밴드 **밖에** 흩어진 저자 줄만 밴드 끝에 모은다.
    """
    blocks = _split_blocks(md)
    if not blocks or not _heading(blocks[0]):
        return md
    end = _find_body_start(blocks)
    if end is None:
        return md

    band = _author_band_end(blocks, end)
    authors: list[str] = []  # 밴드 안: 원문 순서 그대로 / 밴드 밖: 흩어진 저자 줄
    notes: list[str] = []    # 저자 주석 — 저자 밴드 바로 뒤에 붙인다
    keep: list[str] = []
    n_head = n_affil = 0
    skip_body = False
    i = 1
    while i < end:
        b = blocks[i]
        h = _heading(b)
        if h:
            skip_body = bool(_JUNK_HEADING_RE.match(h[1]))
            if _is_person(h[1]):
                line = _render_author(b)
                if i + 1 < end and _is_affiliation(blocks[i + 1]):
                    line = f"{line} · {blocks[i + 1].strip()}"
                    i += 1
                authors.append(line)
                n_head += 1
            elif not skip_body:
                keep.append(b)
            i += 1
            continue
        if _is_author_note(b):
            notes.append(_note_text(b))
        elif _is_affiliation(b):
            for seg in (s.strip() for s in _AUTHOR_SEG_RE.findall(b) if s.strip()):
                authors.append(seg)
                n_affil += 1
        elif _is_biblio_junk(b) or skip_body:
            pass
        elif i < band:
            authors.append(b.strip())  # 밴드 안이면 저자 그리드의 조각 — 순서를 지킨다
        else:
            keep.append(b)
        i += 1

    if n_head == 0 and n_affil <= 1:
        return md
    out = [blocks[0]]
    if authors:
        out.append("\n\n".join(authors))
    out.extend(notes)
    out.extend(keep)
    out.extend(blocks[end:])
    text = "\n\n".join(out)
    return text + "\n" if md.endswith("\n") else text


# --- LLM 라벨 + 코드 재조립 ------------------------------------------------
_SYSTEM = """You label the LEADING blocks of an academic paper's extracted markdown so the front
matter can be cleaned. A multi-column author grid was often mis-read, scattering authors and mixing in
copyright/venue/ISBN boilerplate.

You receive numbered blocks. Return, using ONLY the given indices:
- title: the index of the block holding the paper title.
- authors: indices of blocks that are author names or their affiliation/email lines, in natural reading
  order (top-to-bottom, left-to-right of the original author grid). Include every author block, even ones
  that appear far down, interleaved with boilerplate.
- sections: indices of LEGITIMATE front-matter section blocks to keep — Abstract, CCS Concepts, Keywords
  and the body blocks that belong to them — listed in the order they should be READ. Do NOT include
  copyright, venue, ISBN, DOI, "ACM Reference Format", or the author-note footnote here.
  A two-column first page is often read column-out-of-order, so these blocks may be scrambled: the
  "Abstract" heading and its text can land AFTER body_start, and a stray tail fragment of the abstract
  (a block starting mid-sentence, before body_start) can land before it. List them anyway, and list them
  in reading order — heading first, then its text, then any continuation fragment — even when that means
  an index after body_start comes before one that precedes it. Blocks you list from after body_start must
  be a CONTIGUOUS run starting at the section's own heading ("Abstract", "CCS Concepts", "Keywords"):
  never list an Introduction paragraph, and never skip over one to reach a later block.
- body_start: the index of the first MAIN body section (usually "Introduction" or "1 ...").
- authors_detail: the SAME authors, re-grouped one entry per person. {order_rule} For each author give
  name, their email(s), and their affiliation line(s). A multi-column grid interleaves people, so attach each
  email/affiliation to the correct person. Copy text EXACTLY as it appears — never invent, correct, translate,
  or complete an email or affiliation. If you cannot tell whom an affiliation belongs to, omit authors_detail
  entirely (leave it empty); a partial or guessed grouping is worse than none.

Everything before body_start that you did not list is treated as boilerplate and dropped; everything
after body_start that you did not list stays where it is, untouched. Never invent
indices. If unsure whether a block is an author or boilerplate, and it contains an email, treat it as an
author."""

# authors_detail의 순서 지시는 추출기 출력을 믿을 수 있느냐에 따라 갈린다 — 코드의 재정렬
# (_in_source_order)과 반드시 같은 전제를 써야 한다. 둘이 어긋나면 둘 다 순서를 흔든다.
_ORDER_RULE = {
    True: ("The blocks are given in correct reading order, so KEEP that order: an author whose name "
           "appears earlier in the blocks must come earlier in authors_detail. Never re-sort by column."),
    False: ("The blocks may be OUT of reading order (a mis-read multi-column grid), so do NOT follow the "
            "block order: list authors_detail in the paper's own reading order (top-to-bottom, "
            "left-to-right of the original grid) as printed on the page."),
}


def _layout_max_tokens(n_front: int) -> int:
    """출력 토큰 상한 자동 산정 — authors_detail(저자별 이름·이메일·소속)이 출력 대부분을 차지한다.

    1024 고정이면 저자가 많은 논문(8명 이상)에서 JSON이 중간에 잘리고, 파싱 실패는
    조용한 규칙 폴백으로 이어져 저자 줄이 원문 그대로 남는다. 앞부분 블록 수에 비례시킨다.
    """
    return min(8192, max(2048, n_front * 320))


# 프롬프트에 넣을 블록당 최대 길이. 2단 조판 논문은 저자 여러 명이 한 블록에 흘러 나오는데
# (예: "A ∗ Google llion@… B ∗ Google nikip@… C ∗ …"), 여기서 자르면 뒤쪽 저자가 LLM에
# 아예 보이지 않아 조용히 누락되고 이메일도 중간에 잘린다. 앞부분 블록은 원래 짧아 비용 영향은 작다.
_BLOCK_CHARS = 800


def _llm_layout(provider: Provider, blocks: list[str], n_front: int,
                trust_order: bool = True) -> FrontMatterLayout:
    numbered = "\n".join(f"[{i}] {blocks[i].strip()[:_BLOCK_CHARS]}" for i in range(n_front))
    user = f"{numbered}\n\n(There are {n_front} leading blocks, indices 0..{n_front - 1}.)"
    return provider.parse(_SYSTEM.format(order_rule=_ORDER_RULE[bool(trust_order)]), user,
                          FrontMatterLayout, max_tokens=_layout_max_tokens(n_front))


def _alnum(s: str) -> str:
    """영숫자만 남기고 소문자화 — 구두점·공백·줄바꿈 차이를 무시한 근거 대조용."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _grounded_authors(entries: list[AuthorEntry], source: str,
                      trust_order: bool = True) -> list[AuthorEntry] | None:
    """구조화 저자를 원문(저자 블록)에 대조 — 이름·이메일·소속이 전부 원문에 있어야 채택.

    하나라도 원문에 없으면(환각·오타·추정) None을 돌려 호출부가 규칙 재조립으로 폴백한다.
    이메일은 대소문자 무시 완전 포함, 이름·소속은 영숫자 정규화 후 부분 포함(구두점 차이 허용).
    trust_order가 거짓이면 순서는 LLM 답 그대로 둔다(원문 순서를 믿을 수 없는 페이지).
    """
    if not entries:
        return None
    src_alnum, src_low = _alnum(source), source.lower()
    clean: list[AuthorEntry] = []
    for e in entries:
        name = (e.name or "").strip()
        if not name or _alnum(name) not in src_alnum:
            return None
        emails = [em.strip() for em in e.emails if em.strip()]
        if any(em.lower() not in src_low for em in emails):
            return None
        affils = [a.strip() for a in e.affiliations if a.strip()]
        if any(_alnum(a) not in src_alnum for a in affils):
            return None
        clean.append(AuthorEntry(name=strip_author_mark(name), emails=emails, affiliations=affils))
    return _in_source_order(clean, src_alnum) if trust_order else clean


def _in_source_order(entries: list[AuthorEntry], src_alnum: str) -> list[AuthorEntry]:
    """저자 순서를 원문 등장 순서로 세운다 — LLM이 다열 그리드를 열 우선으로 읽는 일이 있다.

    **원문 순서를 믿을 수 있을 때만** 부른다(trust_order): 추출 단계가 첫 페이지의 읽기 순서를
    확인·복구했다는 뜻이다. 되돌리지 못한 뒤집힘이 남은 페이지에서는 원문 순서 자체가 틀렸으므로,
    이 정렬은 틀린 순서를 고치는 게 아니라 **맞은 답을 틀리게 만든다**.
    순서만 바로잡고 항목을 버리지는 않는다(내용 검증은 위에서 끝났다).
    """
    at = [src_alnum.find(_alnum(e.name)) for e in entries]
    if all(a <= b for a, b in zip(at, at[1:])):
        return entries
    return [entries[i] for i in sorted(range(len(entries)), key=lambda i: (at[i], i))]


def _render_authors_detail(entries: list[AuthorEntry], parts: list[str] | None = None) -> str:
    """검증된 구조화 저자를 일관된 형식으로 — 저자마다 굵은 이름 / (선택)이메일 / (선택)소속.

    parts로 무엇을 표시할지 고른다(이름은 항상): "email", "affiliation". None이면 둘 다.
    한 저자 안은 마크다운 하드 브레이크('  \\n')로 줄을 나누고, 저자 사이는 빈 줄로 문단 분리.
    이름은 굵은 문단(헤더 아님)이라 섹션 트리·목차에 저자가 끼지 않는다.
    """
    show = ("email", "affiliation") if parts is None else parts
    out: list[str] = []
    for e in entries:
        lines = [f"**{e.name}**"]
        if "email" in show and e.emails:
            lines.append(" · ".join(e.emails))
        if "affiliation" in show:
            lines.extend(e.affiliations)
        out.append("  \n".join(lines))
    return "\n\n".join(out)


def _valid(layout: FrontMatterLayout, n_front: int, n_blocks: int) -> bool:
    def ok(i: int) -> bool:
        return isinstance(i, int) and 0 <= i < n_blocks
    if not ok(layout.title) or not ok(layout.body_start):
        return False
    if not (layout.title < layout.body_start <= n_blocks):
        return False
    if layout.body_start > _MAX_FRONT:
        return False
    # 저자도 본문 시작 **뒤**에 있을 수 있다 — 다열 저자 그리드의 마지막 칸(이름·소속·이메일)이
    # 본문 첫 문단 뒤로 밀려나는 조판이 흔하다. 그걸 이유로 레이아웃 전체를 버리면 제목·저자 라벨까지
    # 함께 버려져 규칙 폴백으로 떨어졌고, 첫 페이지가 통째로 깨졌다(authors.json도 안 생겼다).
    # 섹션과 같은 규칙으로 본다: LLM에 보여준 창(n_front) 안이고, 본문 시작 블록 자체는 아닐 것.
    # 실제로 앞으로 되찾을지는 _author_meta_span이 블록 내용으로 다시 판정한다(거부가 아니라 절단).
    if not layout.authors or not all(ok(i) and i < n_front and i != layout.body_start
                                     for i in layout.authors):
        return False
    # 섹션은 본문 시작 뒤에 있어도 된다(단 뒤집힘) — 단, LLM에 보여준 창(n_front) 안이어야 하고
    # 본문 시작 블록 자체는 옮길 수 없다.
    # 개수 상한은 여기서 보지 않는다: 지목이 과하다는 이유로 레이아웃 전체를 버리면 제목·저자
    # 라벨까지 함께 버려지고 규칙 폴백(저자 뒤섞임)으로 떨어진다 — 받아들이는 쪽보다 나쁜 실패였다.
    # 과한 지목은 _pullable이 닻과 상한으로 **잘라서** 처리한다(거부가 아니라 절단).
    return all(ok(i) and i < n_front and i != layout.body_start for i in layout.sections)


# front matter 안의 그림(teaser)·캡션 블록 — boilerplate가 아니라 콘텐츠라 버리면 안 된다.
_FM_IMG_RE = re.compile(r"^\s*(?:<[^>]+>\s*)?!\[")  # 이미지 블록(앞에 <span> 앵커 허용)
_FM_CAP_RE = re.compile(r"^\s*(?:<[^>]+>\s*)?\**\s*(?:Figure|Fig\.?|Table|Tab\.?)\s+\d", re.I)


def _is_figure_block(block: str) -> bool:
    """이미지 참조 또는 Figure/Table 캡션 블록인지 (teaser 유실 방지용)."""
    first = block.strip().split("\n", 1)[0] if block.strip() else ""
    return bool(_FM_IMG_RE.match(first) or _FM_CAP_RE.match(first))


# 본문보다 앞에 올 수 있는 섹션 헤딩 — 뒤집힌 단을 되돌릴 때 "여기서부터 앞으로" 기준점이 된다.
_FRONT_SECTION_RE = re.compile(
    r"^(abstract|keywords?|author keywords|index terms|ccs concepts|ccs|general terms|"
    r"categories and subject descriptors)\b", re.I)


def _is_front_heading(block: str) -> bool:
    h = _heading(block)
    return bool(h and _FRONT_SECTION_RE.match(h[1].strip().lstrip("·* ")))


def _blocks_pull_stop(block: str) -> bool:
    """구간에 넣을 수 없는 블록 — 리스트·그림/표·(자기 헤딩이 아닌) 헤딩.

    단이 잘려 밀려난 초록은 "헤딩 + 이어지는 문단"이지 목록도 그림도 다음 섹션도 아니다.
    NIRVANA에서 기여 목록(4항목)까지 초록으로 딸려 올라간 자리를 여기서 끊는다.
    """
    s = block.strip()
    return bool(_heading(block)) or _is_figure_block(block) or bool(_LIST_RE.match(s))


def _pullable(blocks: list[str], sections: list[int], body_start: int,
              allow: bool = False) -> set[int]:
    """본문 뒤에 놓인 섹션 블록 중 실제로 앞으로 되돌릴 것만 고른다.

    이 복구는 **단이 뒤집혔다고 측정된 논문에서만** 켠다(allow). 뒤집히지 않은 논문에서는
    빈 집합이라 경로 전체가 no-op이다 — 정상 논문의 출력이 이 복구 이전과 같아진다.

    켜졌을 때도 구간에는 끝 닻이 있다. 단이 뒤집히면 왼쪽 단이 한 덩어리로 밀리므로 대상은
    "Abstract 같은 front matter 헤딩 + 뒤따르는 연속 블록"인데, 끝을 정하는 것이 없으면
    바로 뒤에 이어붙은 **다음 쪽 본문**까지 통째로 딸려 온다(그 블록들은 정의상 인접해 있다).
    닻은 넷이다.
      R1 종결부호로 끝나는 블록에서 멈춘다 — 단이 잘린 조각은 문장 중간에서 끊긴다.
      R2 LLM이 본문 앞의 '이어지는 꼬리 조각'을 지목하면 그 지점에서 구간을 닫는다.
      R3 리스트·그림·다음 헤딩은 삼키지 않는다.
      R4 구간은 _MAX_RUN, 전체는 _MAX_PULLED에서 자른다(거부가 아니라 절단).

    구간은 **인덱스 인접성**으로 묶는다(나열 순서가 아니라). sections는 LLM이 준 읽기 순서라
    오름차순이 아닐 수 있는데, 나열 순서로만 이으면 [10, 4, 11]이 한 구간(10,11)을 둘로 쪼개
    뒤쪽 조각(11)이 헤딩으로 시작하지 않는다는 이유로 버려진다.
    """
    if not allow:
        return set()
    runs: list[list[int]] = []
    cur: list[int] = []
    for i in sections:
        if not (isinstance(i, int) and 0 <= i < len(blocks)):
            continue
        if i <= body_start:
            cur = []  # R2: 꼬리 조각이 나오면 여기까지가 그 섹션이다
            continue
        prev = cur if (cur and i == cur[-1] + 1) else next(
            (r for r in runs if r[-1] + 1 == i), None)
        if prev is not None and not _is_front_heading(blocks[i]):
            prev.append(i)
            cur = prev
        else:
            cur = [i]
            runs.append(cur)

    pulled: list[int] = []
    for run in runs:
        if not _is_front_heading(blocks[run[0]]):
            continue  # front matter 헤딩으로 시작하지 않는 지목은 본문에 그대로 둔다
        keep = [run[0]]
        for i in run[1:]:
            if len(keep) >= _MAX_RUN or _blocks_pull_stop(blocks[i]):  # R3/R4
                break
            keep.append(i)
            if _TERMINAL_RE.search(blocks[i].strip()):  # R1
                break
        pulled.extend(keep)
        if len(pulled) >= _MAX_PULLED:
            break
    return set(pulled[:_MAX_PULLED])


def _ordered_sections(sections: list[int], extra_fig: list[int], pulled: set[int]) -> list[int]:
    """유지할 front matter 블록의 출력 순서.

    끌어올린 블록이 있을 때만 LLM이 준 읽기 순서를 쓴다 — 단이 뒤집힌 첫 페이지는 Abstract가
    "1 Introduction" 뒤에 놓여 인덱스 정렬로는 복구되지 않기 때문이다. 끌어올린 게 없으면
    인덱스 오름차순으로 되돌려, 이 복구가 없던 때와 출력이 바이트 단위로 같아지게 한다.
    라벨이 없는 그림(teaser) 블록은 인덱스 위치에 맞춰 끼워 넣는다.
    """
    out = list(dict.fromkeys(sections)) if pulled else sorted(dict.fromkeys(sections))
    for i in sorted(set(extra_fig)):
        pos = next((k for k, j in enumerate(out) if j > i), len(out))
        out.insert(pos, i)
    return out


def _build_llm(provider: Provider, md: str, parts: list[str] | None,
               allow_pull: bool = False, ops: dict | None = None,
               trust_order: bool = True, vision=None) -> tuple[str, list[AuthorEntry]] | None:  # noqa: ANN001
    """LLM 라벨 → 코드 재조립. (정규화 md, 검증된 구조화 저자) 반환. 검증 실패 시 None.

    vision(검증 통과한 첫 페이지 비전 결과)이 있으면 제목 글자·저자·저자 주석은 그것으로 쓴다 —
    블록 배치(본문 시작·유지 섹션·뒤집힌 단 끌어올리기)만 라벨에 맡긴다. 비전 직접 조립이
    블록 경계를 확신하지 못한 논문(비전 결과는 맞는데 docling 블록이 낯선 모양)이 이 길로 온다.
    """
    blocks = _split_blocks(md)
    # 제목이 첫 블록이 아닐 수 있다 — 저작권 고지·arXiv 스탬프·학회 배너가 제목 앞에 오는 논문이 흔하다.
    # (blocks[0]만 검사하면 그런 논문에서 LLM 경로가 통째로 건너뛰어져 저자 줄이 원문 그대로 남는다.)
    # LLM이 title 인덱스를 짚어주므로, 앞부분 어딘가에 헤더가 하나라도 있으면 진행한다.
    if not blocks or not any(_heading(b) for b in blocks[:_MAX_FRONT]):
        return None
    # LLM에 보낼 앞부분 범위 (본문 시작 추정 + 여유, 상한 캡). 본문 시작 뒤로 흩어진 저자
    # 조각이 있으면 거기까지 창을 넓힌다 — 창 밖의 저자는 지목될 수 없고, 지목되지 않으면
    # 되찾을 수도 없다(마지막 열 저자가 통째로 누락되던 원인).
    approx = _find_body_start(blocks) or _MAX_FRONT
    tail = _author_tail_end(blocks, approx)
    n_front = min(len(blocks), max(approx + _BODY_LOOKAHEAD, tail, 8),
                  _MAX_FRONT + _BODY_LOOKAHEAD + _AUTHOR_SCAN)
    try:
        layout = _llm_layout(provider, blocks, n_front, trust_order)
    except Exception:  # noqa: BLE001 — 실패 시 폴백
        return None
    if not _valid(layout, n_front, len(blocks)):
        return None

    out = [_title_heading(blocks[layout.title], vision.title) if vision else blocks[layout.title]]
    # 본문 뒤로 밀려난 저자 조각 — LLM 지목 + 코드 판정을 **둘 다** 통과한 것만 되찾는다.
    # 본문 문단이 이어 붙은 블록은 저자 메타만 떼고 산문은 본문에 되돌린다(author_rest).
    author_pull: list[int] = []
    author_rest: dict[int, str] = {}
    author_text: dict[int, str] = {}
    for i in sorted(set(layout.authors)):
        if i <= layout.body_start:
            continue
        span = _author_meta_span(blocks[i])
        if span is None or len(author_pull) >= _MAX_AUTHOR_PULL:
            continue
        author_pull.append(i)
        author_text[i] = span[0]
        if span[1]:
            author_rest[i] = span[1]
    pulled_authors = set(author_pull)
    author_idx = [i for i in layout.authors if i < layout.body_start or i in pulled_authors]
    # 저자: LLM이 저자별로 재구성한 authors_detail이 원문 근거를 통과하면 일관된 형식으로 렌더,
    # 아니면(검증 실패·미제공) 기존처럼 원본 블록을 그대로 내보낸다(환각 없는 보수적 폴백).
    author_src = "\n\n".join(author_text.get(i, blocks[i]) for i in author_idx)
    if vision is not None:
        grounded = list(vision.authors)  # 쪽 이미지로 읽고 텍스트 층에 대조한 것 — 순서도 그 쪽이 맞다
    else:
        grounded = _grounded_authors(layout.authors_detail, author_src, trust_order) or []
    if grounded and vision is None:
        # 열 우선으로 읽힌 그리드에서는 블록 순서도 LLM 판단도 순서를 틀린다. 앞부분에 인용
        # 상용구가 있으면 논문이 스스로 적어 둔 저자 순서가 거기 있다 — 그게 유일한 평문 근거다.
        ordered = _citation_order(
            grounded, [b for j, b in enumerate(blocks[:n_front]) if j not in set(author_idx)])
        if ordered is not None:
            grounded = ordered
    if grounded:
        out.append(_render_authors_detail(grounded, parts))
    else:
        author_lines: list[str] = []
        for i in author_idx:
            author_lines.extend(_author_lines(author_text.get(i, blocks[i])))
        if author_lines:
            out.append("\n\n".join(author_lines))
    seen = {layout.title, *(i for i in layout.authors if i < layout.body_start), *pulled_authors}
    # 저자 주석(∗ …)은 라벨 유무와 무관하게 저자 바로 뒤에 붙인다 — 추출 단계가 앵커로 표시해 둔다.
    # 비전 주석이 있으면 그것을 쓰고 docling 주석 블록은 자리에서만 뺀다(같은 주석이 두 번 나오지 않게).
    if vision is not None:  # 비전 저자·주석에 통째로 덮이는 앞부분 블록은 라벨이 빠뜨려도 버린다
        keys = _vision_keys(vision)
        for i in range(min(layout.body_start, len(blocks))):
            if i not in seen and not _is_figure_block(blocks[i]) and _covered(blocks[i], keys):
                seen.add(i)
        out.extend(_vision_notes(vision))
    for i in range(min(layout.body_start, len(blocks))):
        if i not in seen and _is_author_note(blocks[i]):
            if vision is None or not vision.notes:
                out.append(_note_text(blocks[i]))
            seen.add(i)
    # front matter의 그림(teaser)·캡션 블록은 라벨이 없어도 콘텐츠이므로 유지한다(유실 방지).
    # LLM이 sections에 안 넣은 첫 페이지 teaser가 boilerplate처럼 버려지던 문제를 막는다.
    extra_fig = [i for i in range(min(layout.body_start, len(blocks)))
                 if i not in seen and _is_figure_block(blocks[i])]
    pulled = _pullable(blocks, layout.sections, layout.body_start, allow_pull)
    for i in _ordered_sections(layout.sections, extra_fig, pulled):
        if i not in seen and (i < layout.body_start or i in pulled):
            out.append(blocks[i])
            seen.add(i)
    # 본문은 원본 순서 그대로. 단이 뒤집혀 본문 뒤로 밀려났던 front matter 블록(위에서 이미
    # 앞으로 옮긴 것)만 제외한다 — 그대로 두면 Abstract가 Introduction 중간에 또 나온다.
    # 저자 조각도 마찬가지로 빼되, 본문 문단이 이어 붙어 있던 블록은 그 산문만 되돌린다.
    akeys = _vision_keys(vision, notes=False) if vision is not None else []
    for j in range(layout.body_start, len(blocks)):
        if j in pulled:
            continue
        if j in pulled_authors:
            if j in author_rest:
                out.append(author_rest[j])
            continue
        if akeys and j < layout.body_start + _AUTHOR_SCAN:
            rest = _stray_author_rest(blocks[j], akeys)
            if rest is not None:
                if rest:
                    out.append(rest)
                continue
        out.append(blocks[j])
    if ops is not None:  # 조용한 이동을 남기지 않는다 — 무엇을 몇 개 끌어올렸는지 기록
        order = sorted(pulled)
        runs: list[int] = []
        for k, i in enumerate(order):
            if k and i == order[k - 1] + 1:
                runs[-1] += 1
            else:
                runs.append(1)
        ops["allow_pull"] = allow_pull
        ops["pulled"] = order
        ops["pull_runs"] = runs
        ops["pulled_heads"] = [blocks[i].strip()[:60] for i in order]
        ops["sections_after_body"] = sum(1 for i in layout.sections if i > layout.body_start)
        ops["authors_after_body"] = sum(1 for i in layout.authors if i > layout.body_start)
        ops["authors_pulled"] = author_pull
        ops["authors_split"] = sorted(author_rest)
    text = "\n\n".join(out)
    return (text + "\n" if md.endswith("\n") else text), grounded


# --- 비전 결과로 직접 조립 -------------------------------------------------------
# 비전 경로는 제목·저자·주석 글자를 이미 (텍스트 층에 대조해) 갖고 있다. docling 블록에서 할 일은
# "어느 블록이 그 글자들의 (오염된) 사본인가"를 가려 빼는 것뿐이다. 이걸 **위치가 아니라 덮임**으로 판정한다:
# 블록에서 비전 문자열을 모두 지우고 남는 게 잡음(낱글자·표 테두리)뿐이면 사본이다. 덮이지 않는 블록이
# 앞부분에 하나라도 있으면(머리 없는 초록·학술지 머리말 등 낯선 모양) 직접 조립을 포기하고 라벨 경로로 간다
# — 지우는 쪽으로 틀리면 내용이 사라지기 때문이다.
_TAG_RE = re.compile(r"<[^>]+>")
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_LEAD_LABEL_RE = re.compile(
    r"^[\s*_#]*(?:abstract|a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|keywords?|index\s+terms|author\s+keywords)\b", re.I)
_COVER_SLACK = 0.12  # 블록 글자 중 이만큼까지는 비전 문자열 밖이어도 잡음으로 본다(낱글자·기호)
_COVER_MIN = 6       # 짧은 블록은 이 글자 수까지 잡음 허용


def _vision_keys(vision, notes: bool = True, title: bool = True) -> list[str]:  # noqa: ANN001
    """블록 덮임 판정에 쓸 정규화 키 — 긴 것부터(짧은 키가 긴 키의 일부를 먼저 먹지 않게)."""
    from narani.extract.front_vision import norm

    keys: list[str] = []
    if title:
        keys.append(norm(vision.title))
    for a in vision.authors:
        keys.append(norm(a.name))
        for em in a.emails:
            keys.append(norm(em))
            local, _, domain = em.partition("@")
            keys += [norm(local), norm(domain)]  # {a, b}@x.com 묶음 표기
        keys += [norm(x) for x in a.affiliations]
    if notes:
        keys += list(vision.note_keys)
    return sorted({k for k in keys if k}, key=len, reverse=True)


def _block_key(block: str) -> str:
    from narani.extract.front_vision import norm

    s = _AUTHOR_NOTE_ANCHOR_RE.sub("", block)
    s = _MD_LINK_RE.sub(r"\1", s)
    return norm(_TAG_RE.sub(" ", s))


def _leftover(block: str, keys: list[str]) -> tuple[int, int]:
    """(원래 글자 수, 비전 문자열을 다 지우고 남은 글자 수)."""
    s = _block_key(block)
    n = len(s)
    for k in keys:
        if k in s:
            s = s.replace(k, "")
    if len(s) >= 8 and any(k.startswith(s) or k.endswith(s) for k in keys):
        # 남은 것이 비전 문자열의 앞/뒤 조각 — 러닝 헤더에 앞이 잘린 제목, 두 블록으로 쪼개진 소속.
        # 가운데 조각은 아니다: 제목 한가운데 낱말("Generative AI")은 키워드 같은 진짜 내용일 수 있다.
        s = ""
    # 숫자만 남은 건 소속 번호·위첨자("Zhou1,2,∗")다 — 앞부분에서 숫자만으로 된 내용은 없다
    return n, len(re.sub(r"\d", "", s))


def _covered(block: str, keys: list[str]) -> bool:
    """블록이 비전 문자열의 사본인가 — 지우고 남은 게 잡음 수준이고, 실제로 지운 게 있어야 한다."""
    n, left = _leftover(block, keys)
    return n > 0 and left < n and left <= max(_COVER_MIN, _COVER_SLACK * n)


def _is_furniture(block: str, fkeys: list[str]) -> bool:
    """비전이 쪽 장식(venue 줄·저작권·러닝 헤더)으로 지목한 글로 시작하는 블록인가."""
    k = _block_key(block)
    return len(k) >= 8 and any(k.startswith(f) or k in f for f in fkeys)


def _stray_author_rest(block: str, akeys: list[str]) -> str | None:
    """본문 뒤로 밀려난 저자 칸이면 본문에 남길 나머지('' = 통째로 저자), 아니면 None."""
    span = _author_meta_span(block)
    if span is None or not _covered(span[0], akeys):
        return None
    return span[1]


def _vision_notes(vision) -> list[str]:  # noqa: ANN001
    """렌더할 비전 주석 — 저자 이메일만 담은 주석("∗ e-mail: a@x.edu")은 저자 블록과 겹치므로 뺀다."""
    akeys = _vision_keys(vision, notes=False, title=False)
    return [n for n in vision.notes if not _covered(n, akeys)]


def _title_heading(block: str, title: str) -> str:
    """제목 블록을 비전 제목 글자로 — 헤딩 수준은 원래 블록 것을 따른다(없으면 ##)."""
    h = _HEADING_RE.match(block.strip())
    return f"{h.group(1) if h else '##'} {title}"


def _find_title_block(blocks: list[str], title: str, end: int) -> int | None:
    from narani.extract.front_vision import norm

    key = norm(title)
    best, best_score = None, 0.0
    for i in range(min(end, len(blocks))):
        k = _block_key(blocks[i])
        if not k:
            continue
        score = len(k) / len(key) if k in key else (len(key) / len(k) if key in k else 0.0)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 0.3 else None


def _locate(blocks: list[str], snippet: str, end: int) -> int | None:
    """정규화한 글 조각(앞 40자)이 처음 나오는 블록 — 비전이 알려 준 초록 시작을 블록에서 찾는다."""
    from narani.extract.front_vision import norm

    key = norm(snippet)[:40]
    if len(key) < 15:
        return None
    return next((i for i in range(1, min(end, len(blocks))) if key in _block_key(blocks[i])), None)


def _build_vision(vision, md: str, parts: list[str] | None,  # noqa: ANN001 — front_vision.Verified
                  ops: dict | None = None,
                  allow_pull: bool = False) -> tuple[str, list[AuthorEntry]] | None:
    """검증된 비전 결과로 앞부분 재조립 (LLM 추가 호출 없음). 블록 경계를 확신 못 하면 None.

    출력: 제목 헤딩 / 저자 블록 / 저자 주석 / 앞부분의 그림(teaser)·캡션 / 초록부터는 원래 블록.
    초록부터 본문 시작 사이에서는 서지 상용구(ACM Reference Format …)·저자 주석·흩어진 저자 칸만 뺀다.
    """
    blocks = _split_blocks(md)
    if not blocks:
        return None
    body = _find_body_start(blocks)
    limit = body if body is not None else min(len(blocks), _MAX_FRONT)

    def why(reason: str) -> None:
        if ops is not None:
            ops["vision_direct"] = reason
        return None

    # 앞부분(제목~초록 직전)의 끝 r: 앞부분 섹션 헤딩 / "Abstract—…" 같은 머리글 문단 / 비전이 읽은
    # 초록 첫머리가 든 블록 / 본문 시작 중 가장 이른 것
    cands = [limit]
    cands += [i for i in range(1, limit) if _is_front_heading(blocks[i])
              or (not _heading(blocks[i]) and _LEAD_LABEL_RE.match(blocks[i]))][:1]
    at = _locate(blocks, vision.abstract_start, limit) if vision.abstract_start else None
    if at is not None:
        # 초록 바로 앞 헤딩은 (모양이 낯설어도 — "$\mathbf{A}_{\mathbf{BSTRACT}}$") 초록의 머리다
        cands.append(at - 1 if at > 1 and _heading(blocks[at - 1]) else at)
    r = min(cands)
    if r < 1:
        return why("no front region")
    if body is None and at is None and r == limit:
        return why("no abstract or body boundary")
    if allow_pull and body is not None and any(
            _is_front_heading(b) for b in blocks[body + 1:body + 1 + _BODY_LOOKAHEAD]):
        return why("front section after body start (column flip)")

    keys = _vision_keys(vision)
    akeys = _vision_keys(vision, notes=False, title=False)
    from narani.extract.front_vision import norm

    fkeys = [norm(f) for f in getattr(vision, "furniture", [])]
    t = _find_title_block(blocks, vision.title, r)
    kept: list[str] = []          # 앞부분에서 살리는 블록(그림·캡션)
    dn_notes: list[str] = []      # docling 저자 주석 (비전 주석이 없을 때만 쓴다)
    for i in range(r):
        b = blocks[i]
        if _is_figure_block(b) or (kept and _is_figure_block(kept[-1]) and _FM_CAP_RE.match(kept[-1])
                                   and b.strip()[:1].islower()):
            kept.append(b)  # teaser 그림·캡션(과 소문자로 이어지는 캡션 꼬리)은 콘텐츠다
            continue
        if _covered(b, keys):  # 각주 기호로 시작해도 저자 칸의 사본이면 사본이다("†University of …")
            continue
        if _is_author_note(b):
            dn_notes.append(_note_text(b))
            continue
        h = _heading(b)
        if _is_biblio_junk(b) or (h and _JUNK_HEADING_RE.match(h[1])) or _is_furniture(b, fkeys):
            continue
        n, left = _leftover(b, keys)
        if left <= _COVER_MIN:  # 낱글자·기호만 있는 블록 (여백 세로 글자, 아이콘 대체 글자)
            continue
        return why(f"uncovered front block {i}: {b.strip()[:50]}")

    # 초록 ~ 본문 시작: 원래 순서대로 두되 상용구·주석·흩어진 저자 칸은 뺀다
    mid: list[str] = []
    junk_tail = 0  # 0: 아님, 1: 상용구 헤딩 바로 뒤(인용 문단 자리), 2: 그다음
    for i in range(r, limit):
        b = blocks[i]
        h = _heading(b)
        if h:
            junk_tail = 1 if _JUNK_HEADING_RE.match(h[1]) else 0
            if not junk_tail:
                mid.append(b)
            continue
        if _covered(b, akeys):
            continue
        if _is_author_note(b):
            dn_notes.append(_note_text(b))
            continue
        if _is_biblio_junk(b) or _is_furniture(b, fkeys):
            continue
        rest = _stray_author_rest(b, akeys)
        if rest is not None:
            if rest:
                mid.append(rest)
            continue
        if junk_tail == 1:  # "ACM Reference Format:" 아래 인용 문단
            junk_tail = 2
            continue
        if junk_tail == 2:
            return why(f"unexplained block under reference-format heading {i}: {b.strip()[:50]}")
        mid.append(b)

    out = [_title_heading(blocks[t] if t is not None else "", vision.title)]
    entries = list(vision.authors)
    out.append(_render_authors_detail(entries, parts))
    # 비전이 주석을 읽었으면 그것만(docling 사본은 위에서 뺐다), 못 읽었으면 docling 주석을 살린다
    out.extend(_vision_notes(vision) if vision.notes else dn_notes)
    out.extend(kept)
    out.extend(mid)
    for j in range(limit, len(blocks)):
        b = blocks[j]
        if body is not None and j < body + _AUTHOR_SCAN:
            rest = _stray_author_rest(b, akeys)
            if rest is not None:
                if rest:
                    out.append(rest)
                continue
        out.append(b)
    if ops is not None:
        ops["vision_direct"] = "ok"
    text = "\n\n".join(x for x in out if x.strip())
    return (text + "\n" if md.endswith("\n") else text), entries


# --- 서지 고정 (LLM 없음) ---------------------------------------------------------
# 서지 정보(paper_meta.json: OpenAlex 등)는 추출과 독립된 두 번째 근거다. 추출된 제목이 서지 제목의
# 일부만 남았거나 엉뚱한 글이면 서지 제목을 쓴다 — 단 서지도 틀릴 수 있으므로 **그 제목이 PDF 첫 쪽
# 텍스트 층에 실제로 있을 때만**. 저자도 같은 원리로 순서·누락을 서지 목록에 맞춘다.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _words(s: str) -> set[str]:
    return {w.casefold() for w in _WORD_RE.findall(s)}


def _title_block_index(blocks: list[str], bib_title: str) -> int | None:
    """서지 제목과 단어가 가장 많이 겹치는 앞부분 헤딩 — 정규화 결과의 제목 자리."""
    want = _words(bib_title)
    best, best_score = None, 0.0
    for i, b in enumerate(blocks[:12]):
        h = _heading(b)
        if not h:
            continue
        have = _words(h[1])
        shared = len(have & want)
        if shared < 2 or not have:
            continue
        score = shared / len(have)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 0.5 else None


def pin_title(md: str, bib_title: str | None, page_text: str | None) -> tuple[str, bool]:
    """추출 제목이 서지 제목과 다르고, 서지 제목이 텍스트 층에 있으면 서지 제목으로. (md, 바꿨나)."""
    from narani.extract.front_vision import norm

    title = (bib_title or "").strip()
    if not title or not page_text:
        return md, False
    want = norm(title)
    if len(want) < 15 or len(title.split()) < 3 or want not in norm(page_text):
        return md, False
    blocks = _split_blocks(md)
    i = _title_block_index(blocks, title)
    if i is None:
        return md, False
    h = _heading(blocks[i])
    if want in norm(h[1]):
        return md, False  # 같은 제목(따옴표·하이픈 차이) 또는 부제까지 더 담은 제목 — 그대로 둔다
    blocks[i] = f"{_HEADING_RE.match(blocks[i].strip()).group(1)} {title}"
    text = "\n\n".join(blocks)
    return (text + "\n" if md.endswith("\n") else text), True


def _name_key(name: str) -> tuple[str, str]:
    """(성, 이름 첫 글자) — 'Garreth W. Tigwell'과 'Garreth Tigwell'을 같은 사람으로 본다."""
    from narani.extract.front_vision import norm

    toks = [t for t in re.split(r"[\s.]+", strip_author_mark(name)) if t]
    if not toks:
        return "", ""
    return norm(toks[-1]), norm(toks[0])[:1]


def pin_authors(entries: list[AuthorEntry], bib_authors: list[str] | None,
                page_text: str | None) -> tuple[list[AuthorEntry], str]:
    """구조화 저자를 서지 저자 목록에 맞춘다. (새 목록, 한 일: ''|'reordered'|'added'|'reordered+added').

    - 추출된 저자가 **모두** 서지 목록의 서로 다른 사람에 짝지어질 때만 손댄다(서지가 다른 논문이거나
      불완전하면 손대지 않는다). 이름 글자는 추출(쪽에 인쇄된) 것을 그대로 쓴다.
    - 순서: 서지 순서로. 누락: 서지에만 있는 저자는 그 이름이 첫 쪽 텍스트 층에 있을 때만 이름만으로 넣는다.
    """
    from narani.extract.front_vision import norm

    bib = [b.strip() for b in (bib_authors or []) if b and b.strip()]
    if not entries or len(bib) < 2:
        return entries, ""
    bkeys = [_name_key(b) for b in bib]
    match: dict[int, int] = {}  # 서지 위치 → 추출 위치
    for ei, e in enumerate(entries):
        ek = _name_key(e.name)
        full = norm(e.name)
        cands = [bi for bi, b in enumerate(bib) if bi not in match and norm(b) == full]
        cands = cands or [bi for bi, bk in enumerate(bkeys) if bi not in match and bk == ek and bk[0]]
        if len(cands) != 1:
            return entries, ""
        match[cands[0]] = ei
    src = norm(page_text or "")
    order: list[int | str] = []  # 추출 위치(int) 또는 넣을 서지 이름(str)
    for bi, name in enumerate(bib):
        if bi in match:
            order.append(match[bi])
        elif src and len(norm(name)) >= 5 and norm(name) in src:
            order.append(name)
    idx = [o for o in order if isinstance(o, int)]
    did = []
    if idx != sorted(idx):
        did.append("reordered")
    if len(idx) != len(order):
        did.append("added")
    if not did:
        return entries, ""
    out = [entries[o] if isinstance(o, int) else AuthorEntry(name=o) for o in order]
    return out, "+".join(did)


def pin_to_bib(md: str, entries: list[AuthorEntry], bib: dict | None, page_text: str | None,
               parts: list[str] | None, ops: dict | None = None) -> tuple[str, list[AuthorEntry]]:
    """서지 고정 — 제목 교체, 구조화 저자 순서·누락 맞춤(저자 블록을 다시 그려 authors.json과 맞춘다)."""
    if not bib or not page_text:
        return md, entries
    md, changed = pin_title(md, bib.get("title"), page_text)
    if ops is not None:
        ops["bib_title"] = changed
    if entries:
        new, did = pin_authors(entries, bib.get("authors"), page_text)
        old_block = _render_authors_detail(entries, parts)
        if did and old_block and old_block in md:
            md = md.replace(old_block, _render_authors_detail(new, parts), 1)
            entries = new
            if ops is not None:
                ops["bib_authors"] = did
    return md, entries


def _strip_note_anchors(md: str) -> str:
    """저자 주석 앵커를 최종 출력에서 뗀다 — 어느 경로로 나가든 문서에 남지 않게.

    앵커는 추출 → front_matter 사이의 분류 신호일 뿐 링크 대상이 아니다(문서 어디에도 href가
    없다). 규칙 경로가 일찍 반환하는 갈래도 있으므로 마지막 관문에서 한 번 더 지운다.
    """
    return _AUTHOR_NOTE_ANCHOR_RE.sub("", md)


def normalize_llm(provider: Provider, md: str, allow_pull: bool = False,
                  trust_order: bool = True) -> str | None:
    """LLM 라벨 → 코드 재조립 (문자열만). 검증 실패 시 None(호출부가 규칙 폴백)."""
    from narani import config

    r = _build_llm(provider, md, config.resolve_author_parts(), allow_pull,
                   trust_order=trust_order)
    return _strip_note_anchors(r[0]) if r is not None else None


def normalize_authors(provider: Provider | None, md: str,
                      parts: list[str] | None = None, allow_pull: bool = False,
                      ops: dict | None = None,
                      trust_order: bool = True, *, vision=None,  # noqa: ANN001 — front_vision.Verified
                      bib: dict | None = None,
                      page_text: str | None = None) -> tuple[str, list[AuthorEntry]]:
    """정규화 + 구조화 저자 반환. LLM 성공 시 (md, authors), 실패/없으면 (규칙 md, []).

    경로는 위에서부터: ① 비전 직접 조립(vision 있음, 추가 호출 없음) ② 블록 라벨 LLM(비전 결과가
    있으면 제목·저자·주석은 비전 것) ③ 규칙. 어느 경로든 끝에 서지 고정(bib + 텍스트 층)을 한 번 건다.

    allow_pull은 "추출 단계가 이 논문에서 되돌리지 못한 단 뒤집힘을 실제로 쟀다"는 뜻이다.
    끄면 본문 뒤 블록을 앞으로 끌어올리는 복구가 통째로 no-op이 된다. 뒤집힘을 잰 논문에서 초록 같은
    앞부분 섹션이 실제로 본문 뒤에 놓여 있으면 비전 직접 조립을 포기하고 라벨 경로(끌어올리기)로 간다.
    trust_order는 그 반대다 — "첫 페이지 읽기 순서를 확인·복구했다". 끄면 저자 순서를 원문
    블록 순서로 되세우지 않고 LLM 판단을 그대로 둔다(원문 순서 자체가 틀린 페이지).
    """
    r = None
    path = "heuristic"
    if vision is not None:
        r = _build_vision(vision, md, parts, ops, allow_pull)
        path = "vision" if r is not None else path
    if r is None and provider is not None:
        r = _build_llm(provider, md, parts, allow_pull, ops, trust_order, vision=vision)
        path = ("label+vision" if vision is not None else "label") if r is not None else path
    text, entries = (r[0], r[1]) if r is not None else (normalize_heuristic(md), [])
    text, entries = pin_to_bib(text, entries, bib, page_text, parts, ops)
    if ops is not None:
        ops["path"] = path
    return _strip_note_anchors(text), entries


def normalize(provider: Provider | None, md: str, allow_pull: bool = False,
              trust_order: bool = True) -> str:
    """앞부분 정규화 진입점(문자열) — provider 있으면 LLM(검증), 실패/없으면 규칙 폴백."""
    from narani import config

    return normalize_authors(provider, md, config.resolve_author_parts(), allow_pull,
                             trust_order=trust_order)[0]
