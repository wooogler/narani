"""내보내기 묶음 — 논문(들)에서 고른 것만 zip 하나로 (§export_bundle).

고를 수 있는 것(ITEMS): 영어 마크다운(+이미지) · 번역 마크다운(+이미지) · 원본 PDF · 하이라이트·메모 ·
BibTeX · 듣기 음성. 한 편 내보내기(논문 화면)와 여러 편 일괄 내보내기(홈)가 같은 함수를 쓴다.

zip 구조 — 마크다운 폴더는 예전 그대로 zip 맨 위의 `<이름>-en/`·`<이름>-<언어>/`(`controller._zip_add_paper`).
Notion은 이 모양이어야 내용이 들어가고, 옵시디언 임베드는 그 폴더 이름을 경로 앞에 달아 두었기 때문에
다른 폴더 안으로 넣지 않는다. 나머지(PDF·메모·bib·음성)는 그 옆 `<이름>/` 폴더에:

    <이름>-en/<이름>.en.md, <이름>-en/images/…
    <이름>-ko/<이름>.ko.md, <이름>-ko/images/…
    <이름>/<이름>.pdf · <이름>/<이름>.notes.md · <이름>/<이름>.bib · <이름>/<이름>.m4a

영어나 번역 하나만 고르면 zip은 예전 '영어 zip'·'번역 zip'과 이름까지 같다. 한 편에서 파일 하나짜리
항목(PDF·메모·bib·음성) 하나만 고르면 zip 없이 그 파일을 그대로 준다.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from pathlib import Path

from narani.i18n import t
from narani.workdir import WorkDir

ITEMS = ("en", "ko", "pdf", "notes", "bib", "audio")
MARKDOWN = ("en", "ko")
DEFAULT_ITEMS = ("en", "ko", "pdf")
_MEDIA = {"pdf": "application/pdf", "notes": "text/markdown", "bib": "application/x-bibtex", "audio": "audio/mp4"}


def _source_pdf(wd: WorkDir) -> Path | None:
    from narani.library import _source_pdf as src

    return src(wd)


def _audio(wd: WorkDir) -> Path | None:
    from narani.ui import listen

    got = listen.local_audio(wd.root)
    return got[0] if got else None


def _notes_text(wd: WorkDir) -> str | None:
    from narani import paper_meta
    from narani.ui import annotations

    if not annotations.has_any(wd):
        return None
    title = str((paper_meta.load(wd) or {}).get("title") or "").strip()
    return annotations.to_markdown(annotations.load(wd), title, annotations.load_notes(wd))


def _bib_text(wd: WorkDir) -> str | None:
    from narani import bibtex

    made = bibtex.entry_for(wd)
    return made[1].rstrip("\n") + "\n" if made else None


def available(root: Path | str) -> dict[str, bool]:
    """이 논문에 무엇이 있는지 {항목: 있음} — 대화상자가 없는 항목을 흐리게 할 때."""
    wd = WorkDir(Path(root))
    from narani.ui import annotations

    return {
        "en": wd.en_md.exists(),
        "ko": wd.ko_md.exists(),
        "pdf": _source_pdf(wd) is not None,
        "notes": annotations.has_any(wd),
        "bib": _bib_text(wd) is not None,
        "audio": _audio(wd) is not None,
    }


def counts(roots: Iterable[Path | str]) -> dict[str, int]:
    """항목마다 그것을 가진 논문 수 — 여러 편일 때 'n/N편'으로 보인다."""
    out = dict.fromkeys(ITEMS, 0)
    for root in roots:
        for k, ok in available(root).items():
            out[k] += int(ok)
    return out


def _file_item(wd: WorkDir, item: str) -> tuple[str, bytes] | None:
    """파일 하나짜리 항목의 (파일 이름, 내용). 그 논문에 없으면 None."""
    stem = wd.root.stem
    if item == "pdf":
        p = _source_pdf(wd)
        return (f"{stem}.pdf", p.read_bytes()) if p else None
    if item == "audio":
        p = _audio(wd)
        return (f"{stem}{p.suffix}", p.read_bytes()) if p else None
    if item == "notes":
        from narani.library import notes_file_name

        text = _notes_text(wd)
        return (notes_file_name(stem), text.encode("utf-8")) if text else None
    if item == "bib":
        text = _bib_text(wd)
        return (f"{stem}.bib", text.encode("utf-8")) if text else None
    raise ValueError(item)


def _add_paper(z: zipfile.ZipFile, wd: WorkDir, items: list[str], target: str) -> bool:
    """한 논문의 고른 항목을 z에. 하나라도 담았으면 True."""
    from narani.ui.controller import _zip_add_paper

    added = False
    for which in MARKDOWN:
        if which in items and _zip_add_paper(z, wd, which, target):
            added = True
    stem = wd.root.stem
    for item in items:
        if item in MARKDOWN:
            continue
        got = _file_item(wd, item)
        if got:
            z.writestr(f"{stem}/{got[0]}", got[1])
            added = True
    return added


def normalize(items: Iterable[str]) -> list[str]:
    """알려진 항목만, ITEMS 순서로 (중복 없이)."""
    chosen = set(items)
    return [k for k in ITEMS if k in chosen]


def bundle(roots: Iterable[Path | str], items: Iterable[str],
           target: str = "universal") -> tuple[str, bytes, str]:
    """고른 항목을 묶는다. 반환: (파일 이름, 내용, media type).

    담을 것이 하나도 없으면 FileNotFoundError(화면 문구) — 대화상자가 알림으로 보여 준다.
    """
    wds = [WorkDir(Path(r)) for r in roots]
    items = normalize(items)
    if not wds or not items:
        raise FileNotFoundError(t("내보낼 것을 고르세요."))
    if len(wds) == 1 and len(items) == 1 and items[0] not in MARKDOWN:  # 파일 하나는 zip 없이
        got = _file_item(wds[0], items[0])
        if not got:
            raise FileNotFoundError(t("이 논문에는 고른 파일이 없습니다."))
        return got[0], got[1], _MEDIA[items[0]]
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for wd in wds:
            n += int(_add_paper(z, wd, items, target))
    if not n:
        raise FileNotFoundError(t("고른 항목이 있는 논문이 없습니다."))
    one_md = items in (["en"], ["ko"])  # 예전 '영어 zip'·'번역 zip'과 같은 이름
    if len(wds) == 1:
        name = f"{wds[0].root.stem}-{items[0]}.zip" if one_md else f"{wds[0].root.stem}.zip"
    elif one_md:
        name = t("narani-{n}편-{which}.zip", n=n, which=items[0])
    else:
        name = t("narani-{n}편.zip", n=n)
    return name, buf.getvalue(), "application/zip"
