"""원문(en.md) 단어 수 — 마크다운 기준, 인용 표기 빼고.

한 번 세어 (섹션, 종류, 요소)마다 수를 쌓아 두고, '무엇을 셀지'는 보여 줄 때 고른다(`WordCount.total`).
그래서 설정을 바꿔도 다시 셀 필요가 없고, 범위(본문만 / 참고문헌·부록 제외 / 전부)는 합만 다르게 낸다.

- 섹션: 헤더 줄(`#`~`######`, 코드펜스 밖)마다 하나 — 목차(§ui.app.toc_panel)와 같은 순서·같은 수.
  첫 헤더 앞 서문은 `index=None`.
- 종류(kind): front(제목·저자) · abstract · meta(CCS Concepts·Keywords 등) · body · ack(감사의 글) ·
  statements(연구비·이해 상충·저자 기여 등 선언) ·
  references · appendix · footnotes(각주 모음 제목 — 각주 글은 그 각주를 부른 섹션으로 옮긴다).
- 요소(element): heading · text · caption · table · footnote · math(인라인 수식 하나 = 한 단어) · code.
- 단어: 문법을 걷어 낸 뒤 공백으로 나눠, 글자나 숫자가 하나라도 든 토큰. 하이픈 낱말은 하나, `—`·`→`·`·`는 0.
- 항상 빼는 것: 인용 묶음 `[[…](#ref-N); …]`과 날것의 `[12]`·`[3–5]`·`(Smith et al., 2020)`, 본문의 각주 번호,
  링크 주소(링크 글자만 센다), 이미지, HTML 태그·앵커, 디스플레이 수식(`$$…$$`), 표 구분선, 참고문헌 번호.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from narani.translate.display_sync import _CAPTION_FORMS, _GROUP_RE

KINDS = ("front", "abstract", "meta", "body", "ack", "statements", "references", "appendix", "footnotes")
ELEMENTS = ("heading", "text", "caption", "table", "footnote", "math", "code")

# 학회 제출에 흔한 기준 — 초록+본문(+감사의 글), 제목·저자·참고문헌·부록은 뺀다. 코드 블록은 뺀다.
DEFAULT_KINDS = frozenset({"abstract", "body", "ack"})
DEFAULT_ELEMENTS = frozenset({"heading", "text", "caption", "table", "footnote", "math"})

_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_NUM_PREFIX = re.compile(r"^(?:[A-Z]|[0-9IVX]+)(?:\.[0-9]+)*\.?\s+")

_ABSTRACT = re.compile(r"^abstract\b")
_META = re.compile(r"^(?:ccs concepts|keywords|author keywords|index terms|acm reference format|general terms"
                   r"|additional key words|categories and subject descriptors)\b")
_REFS = re.compile(r"^(?:references|bibliography|works cited|literature cited|reference list)\b")
_ACK = re.compile(r"^acknowledg")
_STATEMENTS = re.compile(r"^(?:funding|declarations?\b|competing interests?|conflicts? of interest|author contributions?"
                         r"|data (?:and code )?availability|code availability|ethics|ethical considerations"
                         r"|disclosure|generative ai (?:use|disclosure)|positionality|open science)")
_FOOTNOTES = re.compile(r"^(?:foot)?notes$")
_APPENDIX = re.compile(r"^(?:appendi(?:x|ces)|supplementa)")
_LETTERED = re.compile(r"^[A-Z](?:\.\d+)*\.?\s+\S")  # 'A Appendix', 'B.2 Prompts' — 참고문헌 뒤에서만 부록으로
# 초록 섹션 안의 메타 표시 줄: '*CCS Concepts*', '**Keywords:** …', 'Keywords: …'
_META_LINE = re.compile(r"^[*_]*(?:CCS Concepts|Keywords|KEYWORDS|CCS CONCEPTS|Author Keywords|Index Terms"
                        r"|ACM Reference Format)\b", re.I)

_FN_DEF = re.compile(r'^<a id="fn-([^"]+)"></a>')
_FN_REF = re.compile(r'href="#fn-([^"]+)"')
_FN_SUP = re.compile(r"<sup\b[^>]*>.*?</sup>", re.S)
_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\[\]]*)\]\([^()\s]*(?:\s+\"[^\"]*\")?\)")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_INLINE_MATH = re.compile(r"(?<![\\$])\$(?!\$)([^$\n]+?)(?<!\\)\$(?!\$)")
_NUM_CITE = re.compile(r"\s?\[\d+[a-z]?(?:\s*[-–—,;]\s*\d+[a-z]?)*\]")
_YEAR = r"(?:1[6-9]|20)\d{2}[a-z]?"
_AY_BRACKET = re.compile(rf"\s?\[[A-Z][^\[\]\n]*?\b{_YEAR}\b[^\[\]\n]*\]")  # [Smith et al. 2020]
_PAREN = re.compile(r"\s?\(([^()\n]*)\)")
_PAGE = r"(?:,?\s*(?:pp?\.|ch\.|chap\.)\s*[\dIVXivx]+(?:\s*[-–]\s*\d+)?)?"
_AY_PART = re.compile(rf"^\s*(?:(?:see|e\.g\.|i\.e\.|cf\.|for example|also)[,:]?\s+)*"
                      rf"(?:[A-Z][^;]*?,?\s+)?{_YEAR}(?:\s*,\s*{_YEAR})*{_PAGE}\s*$")
_LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_WORD = re.compile(r"\w")
_PROSE_MIN = 25


@dataclass
class Section:
    index: int | None          # 목차 순서(0부터). 첫 헤더 앞 서문은 None
    level: int
    title: str
    kind: str
    counts: Counter = field(default_factory=Counter)  # (kind, element) → 단어 수

    def total(self, elements=DEFAULT_ELEMENTS, kinds=None) -> int:
        """이 섹션의 수 — kinds=None이면 종류는 가리지 않는다(섹션별 표가 쓰는 값)."""
        return sum(n for (k, e), n in self.counts.items() if e in elements and (kinds is None or k in kinds))


@dataclass
class WordCount:
    sections: list[Section]

    def total(self, kinds=DEFAULT_KINDS, elements=DEFAULT_ELEMENTS) -> int:
        return sum(s.total(elements, kinds) for s in self.sections)

    def by_kind(self, elements=DEFAULT_ELEMENTS) -> dict[str, int]:
        out = dict.fromkeys(KINDS, 0)
        for s in self.sections:
            for (k, e), n in s.counts.items():
                if e in elements:
                    out[k] += n
        return out

    def by_element(self, kinds=DEFAULT_KINDS) -> dict[str, int]:
        out = dict.fromkeys(ELEMENTS, 0)
        for s in self.sections:
            for (k, e), n in s.counts.items():
                if k in kinds:
                    out[e] += n
        return out


def _norm_title(title: str) -> str:
    t = re.sub(r"[*_`]", "", title).strip()
    return _NUM_PREFIX.sub("", t).strip(" .:").lower()


def _own_kind(title: str, back: bool, started: bool) -> str | None:
    """헤더 글만으로 정해지는 종류. 모르면 None(위 섹션을 잇거나 본문).
    back: 참고문헌·부록에 들어섰나. started: 본문이 시작됐나(그 뒤의 'Keywords'는 메타가 아니다)."""
    raw = re.sub(r"[*_`]", "", title).strip()
    t = _norm_title(title)
    if not started and _ABSTRACT.match(t):
        return "abstract"
    if not started and _META.match(t):
        return "meta"
    if _REFS.match(t):
        return "references"
    if back and _FOOTNOTES.match(t):
        return "footnotes"
    if _ACK.match(t):
        return "ack"
    if _STATEMENTS.match(t):
        return "statements"
    if _APPENDIX.match(t) or (back and _LETTERED.match(raw)):
        return "appendix"
    return None


def _split(md: str) -> list[tuple[int, str, list[str]]]:
    """(레벨, 제목, 줄들) — 첫 헤더 앞 서문은 레벨 0. 코드펜스 안의 #은 헤더가 아니다."""
    out: list[tuple[int, str, list[str]]] = [(0, "", [])]
    fence = False
    for line in md.splitlines():
        if _FENCE_RE.match(line):
            fence = not fence
        elif not fence:
            m = _HEAD_RE.match(line)
            if m:
                out.append((len(m.group(1)), m.group(2), []))
                continue
        out[-1][2].append(line)
    return out


def _classify(parts: list[tuple[int, str, list[str]]]) -> list[str]:
    heads = parts[1:]
    has_abstract = any(_own_kind(t, False, False) == "abstract" for _, t, _ in heads)
    kinds = ["front"]
    back = started = False
    inherit: tuple[int, str] | None = None  # (레벨, 종류) — 하위 섹션은 위 섹션의 종류를 잇는다
    for i, (level, title, _) in enumerate(heads):
        own = _own_kind(title, back, started)
        kind = own
        if kind is None and inherit and level > inherit[0] and inherit[1] not in ("front", "meta", "footnotes"):
            kind = inherit[1]
        if kind is None and back:
            kind = "appendix"  # 참고문헌·'Appendix' 뒤의 이름 모를 섹션 — 부록으로 본다
        if kind is None and not started and (i == 0 or (has_abstract and kinds[-1] in ("front",))):
            kind = "front"  # 제목 — 초록이 있으면 초록 앞의 이름 모를 헤더(저자 등)까지
        kind = kind or "body"
        if kind in ("body", "appendix", "references"):
            started = True
        if kind in ("appendix", "references"):
            back = True
        if own is not None or inherit is None or level <= inherit[0]:
            inherit = (level, kind)
        kinds.append(kind)
    return kinds


def _strip_cites(s: str) -> str:
    s = _GROUP_RE.sub("", s)
    s = _NUM_CITE.sub("", s)
    s = _AY_BRACKET.sub("", s)

    def paren(m: re.Match) -> str:
        parts = m.group(1).split(";")
        return "" if parts and all(_AY_PART.match(p) for p in parts) else m.group(0)

    return _PAREN.sub(paren, s)


def _words(s: str) -> tuple[int, int]:
    """(단어 수, 인라인 수식 수) — 마크다운 한 줄(또는 몇 줄)."""
    s = _FN_SUP.sub("", s)
    s = _IMG.sub(" ", s)
    s = _strip_cites(s)
    s = _LINK.sub(r"\1", s)
    s = _AUTOLINK.sub(r"\1", s)
    s = _TAG.sub(" ", s)
    s, n_math = _INLINE_MATH.subn(" ", s)
    s = _strip_cites(s)  # 링크를 걷어 낸 뒤 드러난 날것의 인용
    s = re.sub(r"[*_`~]+", " ", s)
    return sum(1 for tok in s.split() if _WORD.search(tok)), n_math


def _line_element(line: str, prev_image: bool) -> str:
    st = line.strip()
    if st.startswith("|"):
        return "table"
    if any(f.match(st) for f in _CAPTION_FORMS) or (prev_image and re.match(r"^\*\([a-z0-9]+\)", st)):
        return "caption"
    return "text"


def count_words(md: str) -> WordCount:
    """원문 마크다운의 단어 수를 (섹션, 종류, 요소)별로."""
    parts = _split(md)
    kinds = _classify(parts)
    has_abstract = "abstract" in kinds
    sections: list[Section] = []
    fn_text: dict[str, str] = {}
    fn_owner: dict[str, int] = {}
    for idx, ((level, title, lines), kind) in enumerate(zip(parts, kinds)):
        sec = Section(index=idx - 1 if idx else None, level=level, title=title, kind=kind)
        sections.append(sec)
        if title:
            w, m = _words(title)
            sec.counts[(kind, "heading")] += w
            sec.counts[(kind, "math")] += m
        line_kind = kind
        fence = display = prev_image = False
        for line in lines:
            st = line.strip()
            if _FENCE_RE.match(line):
                fence = not fence
                continue
            if fence:
                sec.counts[(line_kind, "code")] += sum(1 for tok in st.split() if _WORD.search(tok))
                continue
            if st.startswith("$$"):
                if not (len(st) > 2 and st.endswith("$$")):
                    display = not display
                continue
            if display or not st:
                continue
            fm = _FN_DEF.match(st)
            if fm:
                fn_text[fm.group(1)] = re.sub(r"^\*\*[^*]+\*\*", "", st[fm.end():])
                continue
            for ref in _FN_REF.findall(st):
                fn_owner.setdefault(ref, idx)
            if kind == "abstract" and _META_LINE.match(st):
                line_kind = "meta"
            elem = _line_element(st, prev_image)
            prev_image = bool(_IMG.fullmatch(st))
            if re.fullmatch(r"\|?[\s:|-]+\|?", st) and "-" in st:
                continue  # 표 구분선
            # 제목 블록에 실린 그림·표(티저)는 본문 캡션으로 센다 — 제목·저자만 front
            k = "body" if (kind == "front" and elem in ("caption", "table")) else line_kind
            if elem == "text":
                st = re.sub(r"^(?:>\s*)+", "", st)
                st = re.sub(r"^#{1,6}\s+", "", st)
                st = _LIST_MARK.sub("", st)
                if kind == "references":
                    st = re.sub(r"^\*\*\[?\d+\]?\.?\*\*", "", _TAG.sub("", st).strip())
            elif elem == "table":
                st = st.replace("|", " ")
            w, m = _words(st)
            if kind == "front" and elem == "text" and not has_abstract and w >= _PROSE_MIN:
                k = "body"  # 초록 헤더 없는 옛 논문 — 제목 블록의 긴 문단은 저자 줄이 아니라 본문(초록·서론)이다
            sec.counts[(k, elem)] += w
            sec.counts[(k, "math")] += m
    # 각주 글은 그 각주를 부른 섹션으로 — 어디서도 안 불렀으면 각주 모음(있으면) 아니면 마지막 섹션
    fallback = next((i for i, s in enumerate(sections) if s.kind == "footnotes"), len(sections) - 1)
    for fid, text in fn_text.items():
        sec = sections[fn_owner.get(fid, fallback)]
        k = "body" if sec.kind in ("front", "footnotes", "meta") else sec.kind
        w, m = _words(text)
        sec.counts[(k, "footnote")] += w
        sec.counts[(k, "math")] += m
    if not parts[0][2] or not any(x.strip() for x in parts[0][2]):
        sections = [s for s in sections if s.index is not None or s.counts]
    return WordCount(sections=sections)
