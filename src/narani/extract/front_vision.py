"""첫 페이지 비전 구조화 — 쪽 이미지 + PDF 텍스트 층 → 제목·저자·저자 주석, 그리고 **환각 0 검증**.

docling 블록에 라벨만 붙이는 경로(front_matter._build_llm)는 블록 텍스트 자체가 오염돼 있으면
고칠 수 없다(러닝 헤더가 제목 앞부분을 먹음, 여백 세로 글자가 소속 앞에 낌, 저자 그리드가 표로 뭉개짐).
여기서는 비전 LLM이 **쪽을 직접 보고** 구조를 읽고, 같은 쪽의 PDF 텍스트 층을 함께 받아 글자를 베낀다.

  LLM은 판단과 베끼기만 한다 → 코드가 낸 문자열 **하나하나를** PDF 텍스트 층과 대조한다.
  하나라도 텍스트 층에 없으면(지어냄·교정·번역) 결과 전체를 버리고 기존 경로(블록 라벨 → 규칙)로 간다.

대조는 정규화(NFKD·대소문자·영숫자만 — 공백·하이픈·합자·악센트·각주 기호 차이를 지운다) 뒤
부분 문자열 일치로 본다. 낱글자 잡음 같은 것은 LLM이 빼면 자연히 사라진다(빼는 건 지어내는 게 아니다).

결과(검증 전 원본 답)는 extract/front_vision.json에 캐시한다 — 키는 쪽 텍스트·모델·프롬프트 판.
다시 변환해도(raw.md가 새로 생겨도) 같은 PDF·모델이면 재호출하지 않고, 검증·조립 규칙이 바뀌어도
같은 답으로 다시 판정한다.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from narani.llm.base import Provider

# 프롬프트·스키마를 바꾸면 올린다 — 캐시 키에 들어가 옛 답을 다시 쓰지 않게 한다.
PROMPT_VERSION = 4
_ZOOM = 1.5            # 72dpi 기준 배율 — 레터 한 쪽이 약 918×1188px, 소속(8pt)도 읽힌다
_PAGE_TEXT_CHARS = 9000  # 프롬프트에 넣을 텍스트 층 상한 — 첫 쪽은 보통 4~6천 자
_MAX_TOKENS = 4096


# --- 스키마 -------------------------------------------------------------------
class VisionAuthor(BaseModel):
    name: str = Field(description="Author's name exactly as printed (keep its capitalization), without "
                                  "footnote marks or affiliation numbers")
    emails: list[str] = Field(default_factory=list, description="This author's email address(es)")
    affiliation_lines: list[str] = Field(
        default_factory=list,
        description="This author's affiliation, one entry per printed line in printed order (department / "
                    "institution / city, country), copied exactly, without marks or affiliation numbers")
    marks: list[str] = Field(default_factory=list,
                             description="Footnote marks printed next to the name (e.g. ∗, †, 1, a)")


class VisionNote(BaseModel):
    mark: str = Field(default="", description="The note's footnote mark (∗, †, ‡, 1 …) or empty")
    text: str = Field(description="The note text exactly as printed, without the mark")


class FrontPageRead(BaseModel):
    """비전 LLM이 첫 페이지에서 읽은 앞부분. 문자열은 전부 PDF 텍스트 층 대조 대상이다."""

    is_first_page: bool = Field(description="False if this page is a cover sheet / not the paper's first page")
    title: str = Field(description="The paper title exactly as printed (all title lines joined, subtitle "
                                   "included), without footnote marks")
    authors: list[VisionAuthor] = Field(default_factory=list,
                                        description="Every author in the paper's own reading order")
    author_notes: list[VisionNote] = Field(
        default_factory=list,
        description="Footnotes attached to authors or the title (equal contribution, work done at …, "
                    "corresponding author, also with …). NOT copyright/permission/venue/DOI text.")
    has_teaser_figure: bool = Field(default=False,
                                    description="True if a figure sits between the authors and the abstract")
    abstract_start: str = Field(
        default="",
        description="The first 8-12 words of the abstract BODY exactly as printed (without the 'Abstract' "
                    "label), or empty if the page has no abstract")
    furniture: list[str] = Field(
        default_factory=list,
        description="Page furniture that is NOT paper content: conference/venue line, running header/footer, "
                    "journal banner, copyright/permission/license text, ISBN, DOI, 'ACM Reference Format' "
                    "citation. One entry per item: its first 8 words exactly as printed (the whole item if "
                    "shorter)")


_SYSTEM = """You read the FIRST PAGE of an academic paper (an image of the page plus the page's PDF text
layer) and return its front matter as structured data.

Rules:
- Use the IMAGE to decide structure: which text is the title, who the authors are, which email and
  affiliation lines belong to which author (multi-column author grids, superscript affiliation numbers,
  shared affiliations), which footnotes are author notes, and where the abstract starts.
- COPY every string from the PDF TEXT LAYER, character for character. Never invent, correct a typo,
  expand an abbreviation, translate, reorder words, or complete text. If the text layer spells something
  oddly, keep that spelling. Leave out stray single letters, icons, ORCID marks and page furniture.
- authors: in the paper's reading order (row by row, left to right). name without footnote marks or
  affiliation numbers. affiliation_lines: one entry per printed line, in printed order (department,
  institution, city/state/country) — never split a printed line at its commas, never merge two printed
  lines. One exception: when a name is broken onto the next line only because the column is narrow
  ("Allen Institute for Artificial" + "Intelligence"), join those two lines into one entry. If affiliations are numbered and listed below the names, give each
  author the full lines of their affiliation(s). If an author's affiliation appears only inside a
  footnote, leave affiliation_lines empty and put that footnote in author_notes.
- emails: the author's address(es). For a grouped form like {alice, bob}@x.com, write each address in
  full (alice@x.com).
- author_notes: footnotes tied to authors or the title only (equal contribution, work done during an
  internship, corresponding author, also affiliated with …). Never copyright, permission, ACM reference,
  venue, ISBN or DOI text.
- abstract_start: the first 8-12 words of the abstract body, exactly as printed, without a leading
  "Abstract"/"ABSTRACT"/"Abstract—" label. Empty if the page has no abstract.
- furniture: every piece of page furniture that is not paper content (venue line such as "CHI '25,
  Yokohama, Japan", running header/footer, journal banner/homepage line, copyright/permission/license
  box, ISBN, DOI, the "ACM Reference Format" citation). One entry per item: its first 8 words exactly
  as printed, or the whole item if shorter.
- If the page is a cover sheet or clearly not the paper's first page, set is_first_page=false."""


# --- 첫 페이지 --------------------------------------------------------------------
@dataclass
class FrontPage:
    """첫 페이지 이미지(PNG)와 텍스트 층."""

    png: bytes
    text: str
    extra: dict = field(default_factory=dict)


def load_front_page(pdf: str, image: bool = True) -> FrontPage | None:
    """PDF 1쪽을 렌더하고 텍스트 층을 읽는다. 텍스트 층이 비었으면(스캔) None — 대조할 근거가 없다.

    image=False면 렌더를 건너뛴다(키가 없어 서지 고정만 할 때 — 텍스트 층만 필요하다)."""
    from narani import pdfio

    try:
        text = pdfio.first_page_text(pdf)
        if len(norm(text)) < 200:
            return None
        png = pdfio.render_page_png(pdf, 0, zoom=_ZOOM) if image else b""
    except Exception:  # noqa: BLE001 — 손상 PDF·pypdfium2 없음 → 비전 경로 끔
        return None
    return FrontPage(png=png, text=text) if (png or not image) else None


# --- 정규화·대조 -----------------------------------------------------------------
_MARK_CHARS = "∗*＊†‡§¶‖⋆★"


def norm(s: str) -> str:
    """대조용 정규화 — NFKD(합자·악센트 분해) + casefold + 유니코드 영숫자만.

    공백·줄바꿈·하이픈(줄끝 하이픈 \\ufffe 포함)·구두점·각주 기호·악센트 차이를 모두 지운다.
    `[a-z0-9]`만 남기면 한글·한자 이름이 빈 문자열이 되어 대조를 공짜로 통과하므로 유니코드로 본다.
    """
    s = unicodedata.normalize("NFKD", s).casefold()
    return "".join(c for c in s if c.isalnum())


def _compact(s: str) -> str:
    """이메일 대조용 — 공백·줄끝 하이픈 표시만 지우고 소문자. 이메일은 구두점이 내용이다."""
    s = unicodedata.normalize("NFKC", s).replace("￾", "").replace("­", "")
    return re.sub(r"\s+", "", s).lower()


def _email_ok(email: str, compact: str) -> bool:
    e = _compact(email)
    if "@" not in e or e.count("@") != 1:
        return False
    if e in compact:
        return True
    # {alice, bob}@x.com 묶음 — 펼쳐 쓴 주소는 텍스트 층에 그대로 없지만, 지역부·도메인은 각각 있다
    local, domain = e.split("@")
    if not local or not domain:
        return False
    group = re.compile(r"[{(\[]([^})\]]*)[})\]]@" + re.escape(domain))
    return any(local in re.split(r"[,;|/]", m.group(1)) for m in group.finditer(compact))


@dataclass
class Verified:
    """검증을 통과한 비전 결과 — 문자열은 전부 텍스트 층에 있다."""

    title: str
    authors: list  # list[AuthorEntry]
    notes: list[str]            # 렌더할 저자 주석 줄 ("∗ Work done …")
    note_keys: list[str]        # 주석 본문 정규화 키 (블록 덮임 판정용)
    abstract_start: str
    has_teaser: bool
    furniture: list[str] = field(default_factory=list)  # 지우기 판정에만 쓴다(출력하지 않음)


def _clean(s: str) -> str:
    s = (s or "").replace("￾", "").replace("­", "")
    return re.sub(r"\s+", " ", s).strip()


def _strip_marks(s: str) -> str:
    return _clean(s).strip(_MARK_CHARS + " ,;")


# 소속 앞에 붙은 소속 번호("1Department of …") — 지시해도 남기는 일이 있다. "3M"·"2nd"는 건드리지 않는다.
_AFFIL_NUM_RE = re.compile(r"^\d{1,2}(?=[^\W\d_]{2})(?!st\b|nd\b|rd\b|th\b)")


def _strip_affil(s: str) -> str:
    return _AFFIL_NUM_RE.sub("", _strip_marks(s)).strip()


def verify(read: FrontPageRead, page_text: str) -> tuple[Verified | None, str]:
    """비전 답을 텍스트 층에 대조. (통과 결과, '') 또는 (None, 거부 이유).

    제목·이름·이메일·소속·주석 중 **하나라도** 텍스트 층에 없으면 통째로 거부한다 — 부분 채택은
    어느 문자열이 지어낸 것인지 모르는 채로 나머지를 믿는 것이라 하지 않는다.
    """
    from narani.ir import AuthorEntry

    if not read.is_first_page:
        return None, "not first page"
    src = norm(page_text)
    compact = _compact(page_text)

    title = _strip_marks(read.title)
    if len(norm(title)) < 8:
        return None, "title too short"
    if norm(title) not in src:
        return None, f"title not in text layer: {title[:60]}"
    if not read.authors:
        return None, "no authors"

    entries = []
    seen: set[str] = set()
    for a in read.authors:
        name = _strip_marks(a.name)
        key = norm(name)
        if len(key) < 3:
            return None, f"name too short: {name!r}"
        if key not in src:
            return None, f"name not in text layer: {name}"
        if key in seen:
            return None, f"duplicate author: {name}"
        seen.add(key)
        emails = []
        for em in a.emails:
            em = _clean(em).strip(" ,;<>")
            if not em:
                continue
            if not _email_ok(em, compact):
                return None, f"email not in text layer: {em}"
            emails.append(em)
        affils: list[str] = []
        for line in a.affiliation_lines:
            line = _strip_affil(line)
            if not line:
                continue
            if len(norm(line)) < 2:
                return None, f"affiliation too short: {line!r}"
            if norm(line) not in src:
                return None, f"affiliation not in text layer: {line[:60]}"
            affils.append(line)
        entries.append(AuthorEntry(name=name, emails=emails, affiliations=affils))

    notes, note_keys = [], []
    for n in read.author_notes:
        text = _clean(n.text).lstrip(_MARK_CHARS + " ")
        if not text:
            continue
        if len(norm(text)) < 4 or norm(text) not in src:
            return None, f"note not in text layer: {text[:60]}"
        mark = _clean(n.mark)
        notes.append(f"{mark} {text}" if mark else text)
        note_keys.append(norm(text))

    # 위치를 찾는 데만 쓰는 표지 — 텍스트 층에 없으면 버리기만 한다(출력에 나가지 않는다)
    abstract = _clean(read.abstract_start)
    if abstract and norm(abstract) not in src:
        abstract = ""
    # 상용구 표지도 지우기 판정에만 쓴다 — 텍스트 층에 없는 것은 버린다(지어낸 표지로 내용을 지우지 않게)
    furniture = [f for f in (_clean(x) for x in read.furniture) if len(norm(f)) >= 8 and norm(f) in src]
    return Verified(title=title, authors=entries, notes=notes, note_keys=note_keys,
                    abstract_start=abstract, has_teaser=bool(read.has_teaser_figure),
                    furniture=furniture), ""


# --- 호출·캐시 --------------------------------------------------------------------
def cache_key(page: FrontPage, model: str) -> str:
    from narani.workdir import hash_text

    return hash_text(f"v{PROMPT_VERSION}:{model}:{page.text}")


def ask(provider: Provider, page: FrontPage) -> FrontPageRead:
    """비전 LLM 한 번 — 이미지 1장 + 텍스트 층."""
    text = page.text.replace("\r\n", "\n")[:_PAGE_TEXT_CHARS]
    user = ("PDF TEXT LAYER OF THIS PAGE (copy strings from here):\n<<<\n" + text + "\n>>>\n\n"
            "Return the front matter of the page shown in the image.")
    return provider.parse_image(_SYSTEM, user, [page.png], FrontPageRead, max_tokens=_MAX_TOKENS)


def read_cached(provider: Provider, page: FrontPage, cache_path) -> tuple[Verified | None, dict]:  # noqa: ANN001 — Path
    """캐시가 있으면 그 답으로, 없으면 한 번 물어 저장하고 검증한다. (통과 결과|None, 기록)."""
    model = getattr(provider, "model", "")
    key = cache_key(page, model)
    info: dict = {"model": model}
    data = None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            data = cached.get("result")
            info["cached"] = True
    except (OSError, ValueError, AttributeError):
        pass
    if data is None:
        try:
            read = ask(provider, page)
        except NotImplementedError:
            return None, {**info, "status": "skipped", "reason": "provider has no image input"}
        except Exception as e:  # noqa: BLE001 — 실패하면 기존 경로
            return None, {**info, "status": "error", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
        data = read.model_dump()
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"key": key, "model": model, "result": data},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    try:
        read = FrontPageRead(**data)
    except Exception as e:  # noqa: BLE001 — 깨진 캐시
        return None, {**info, "status": "error", "reason": f"bad cache: {e}"[:120]}
    verified, why = verify(read, page.text)
    if verified is None:
        return None, {**info, "status": "rejected", "reason": why}
    return verified, {**info, "status": "verified", "authors": len(verified.authors)}
