"""번역본(ko.md)에 원문의 '표기'만 따라 입히기 — 재번역 없이.

뷰어 설정의 인용 표기·참고문헌 링크·캡션 표기는 원문(en.md)을 다시 만들며 바뀌는데, 번역본은 번역할 때의
원문을 옮긴 결과물이라 그대로 남았다(원문만 `[Xie et al. 2024]`, 번역은 `[42, Xie et al. 2024, OSWorld]`).
이 셋은 번역문이 아니라 코드가 그린 표기라서 같은 규칙으로 번역본에도 다시 그린다:

- 인용 묶음 `[[…](#ref-N); …]`: 링크는 번역 때 보호돼 그대로 옮겨지므로, 번호(N)마다 원문이 지금 보이는
  링크 글자로 다시 짓는다.
- 참고문헌 항목(`<a id="ref-N">`): 번역하지 않고 옮긴 항목만(링크를 걷어낸 글이 원문 항목과 같을 때) 원문
  줄로 바꾼다 — 참고문헌까지 번역한 논문의 번역문은 건드리지 않는다.
- 그림·표 캡션: 원문에 있는 캡션 라벨(`Figure 3` 등)로 시작하는 줄만, 지금 표기로 다시 감싼다. 라벨은 번역
  때도 영어 그대로다.

모두 몇 번을 돌려도 같은 결과다(이미 맞는 줄은 그대로).
"""

from __future__ import annotations

import re

from narani.assemble.figures import _caption_line

_GROUP_RE = re.compile(r"\[((?:\[[^\[\]\n]*\]\(#ref-\d+\)(?:\s*[;,]\s*)?)+)\]")
_TOKEN_RE = re.compile(r"\]\(#ref-(\d+)\)")
_ANCHOR_RE = re.compile(r'<a id="ref-([^"]+)"></a>')
_MDLINK_RE = re.compile(r"\[([^\[\]]*)\]\([^()\s]*\)")
_LABEL = r"(?:Figure|Fig\.?|Table|Tab\.?|Algorithm|Listing|Scheme|Box|Chart)\s*[0-9IVXLC]+[a-z]?"
# 세 표기(§assemble.figures._caption_line) 중 무엇으로 감싸였든 (라벨, 설명)을 꺼낸다
_CAPTION_FORMS = [
    re.compile(rf"^> \*\*(?P<label>{_LABEL}):\*\* (?P<text>.+)$"),
    re.compile(rf"^> \*\*(?P<label>{_LABEL})\*\*$"),
    re.compile(rf"^\*\*(?P<label>{_LABEL}):\*\* \*(?P<text>.+)\*$"),
    re.compile(rf"^\*\*(?P<label>{_LABEL})\*\*$"),
    re.compile(rf"^\*(?P<label>{_LABEL}): (?P<text>.+)\*$"),
    re.compile(rf"^\*(?P<label>{_LABEL})\*$"),
]


def relabel_citations(text: str, en: str, parts: list[str]) -> tuple[str, int]:
    """인용 묶음을 원문(en.md)이 지금 보이는 표기로. 번호마다 원문의 링크 글자를 그대로 쓴다 — 참고문헌 정보가
    그 뒤에 보강돼도(연도 추가 등) 번역본이 원문보다 앞서 나가지 않게. 원문에 없는 번호가 끼면 그 묶음은 그대로.
    반환: (글, 바꾼 묶음 수)."""
    shown: dict[int, str] = {}
    for g in _GROUP_RE.finditer(en):
        for m in re.finditer(r"\[([^\[\]\n]*)\]\(#ref-(\d+)\)", g.group(1)):
            shown.setdefault(int(m.group(2)), m.group(1))
    sep = ", " if (parts or ["number"]) == ["number"] else "; "  # §cite.link._replacement와 같은 구분자
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        idx = [int(i) for i in _TOKEN_RE.findall(m.group(1))]
        if not idx or any(i not in shown for i in idx):
            return m.group(0)
        new = "[" + sep.join(f"[{shown[i]}](#ref-{i})" for i in idx) + "]"
        if new != m.group(0):
            n += 1
        return new

    return _GROUP_RE.sub(repl, text), n


def _plain(line: str) -> str:
    return re.sub(r"\s+", " ", _MDLINK_RE.sub(r"\1", line)).strip()


def sync_reference_entries(text: str, en: str) -> tuple[str, int]:
    """옮겨만 둔(번역 안 한) 참고문헌 항목을 원문 항목 줄로 — 링크 표기 설정이 따라간다."""
    src = {}
    for ln in en.splitlines():
        m = _ANCHOR_RE.search(ln)
        if m:
            src.setdefault(m.group(1), ln)
    out, n = [], 0
    for ln in text.split("\n"):
        m = _ANCHOR_RE.search(ln)
        new = src.get(m.group(1)) if m else None
        if new is not None and new != ln and _plain(new) == _plain(ln):
            out.append(new)
            n += 1
        else:
            out.append(ln)
    return "\n".join(out), n


def _parse_caption(line: str) -> tuple[str, str | None] | None:
    for rx in _CAPTION_FORMS:
        m = rx.match(line.strip())
        if m:
            return m.group("label"), m.groupdict().get("text")
    return None


def restyle_captions(text: str, en: str, style: str) -> tuple[str, int]:
    """원문 캡션 라벨로 시작하는 번역본 캡션 줄을 지금 표기로 다시 감싼다."""
    labels = {p[0] for p in map(_parse_caption, en.splitlines()) if p}
    if not labels:
        return text, 0
    out, n = [], 0
    for ln in text.split("\n"):
        p = _parse_caption(ln)
        if p and p[0] in labels:
            new = _caption_line(p[0], p[1], style)
            if new and new != ln.strip():
                out.append(new)
                n += 1
                continue
        out.append(ln)
    return "\n".join(out), n


def sync_author_blocks(text: str, entries: list, parts: list[str],
                       kept: dict[str, list[str]]) -> tuple[str, int]:
    """번역본 저자 문단을 지금 저자 표기(이메일·소속)로 — 이름 줄로 저자를 찾아 그 문단만 다시 짓는다.

    통째로 바꿀 수 없다: 번역본은 소속을 옮겨 두었다('Harvard University' → '하버드대학교'). 그래서 소속 줄은
    번역본 것을 kept(이름 → 번역된 소속 줄)에 남겨 두고 다시 켤 때 그것을 쓴다(없으면 원문 소속).
    kept는 그 자리에서 채워진다 — 호출한 쪽이 저장한다. 반환: (글, 바꾼 저자 수).
    """
    lines = text.split("\n")
    n = 0
    for e in entries:
        head = f"**{e.name}**"
        i = next((k for k, ln in enumerate(lines) if ln.rstrip() == head), None)
        if i is None:
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip():
            j += 1
        rest = [ln.rstrip() for ln in lines[i + 1:j]]
        email = " · ".join(e.emails)
        affs = [ln for ln in rest if ln != email]
        if affs:
            kept[e.name] = affs
        new = [head]
        if "email" in parts and e.emails:
            new.append(email)
        if "affiliation" in parts:
            new.extend(kept.get(e.name) or e.affiliations)
        block = "  \n".join(new).split("\n")
        if block != lines[i:j]:
            lines[i:j] = block
            n += 1
    return "\n".join(lines), n
