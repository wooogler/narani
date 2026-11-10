"""내보내기 묶음(§export_bundle) — 고른 항목만, 예전 zip 구조 그대로."""

import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from narani import config, export_bundle, notes, pipeline
from narani.ui import annotations, export_dialog, listen
from narani.ui.controller import UIController, bulk_zip
from narani.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"
_META = {"title": "Attention Is All You Need", "authors": ["Ashish Vaswani"], "year": 2017,
         "venue": "Advances in Neural Information Processing Systems"}


def _paper(tmp_path: Path, name: str, full: bool = True) -> WorkDir:
    """변환된 논문 하나. full이면 번역·PDF·메모·서지·음성까지 다 갖춘다."""
    wd = WorkDir(tmp_path / name / f"{name}.md4")
    pipeline.convert(CORPUS / "sample_arxiv.md", wd)
    ctrl = UIController(wd)
    wd.out_images.mkdir(parents=True, exist_ok=True)
    (wd.out_images / "figure-1.png").write_bytes(b"\x89PNG\r\n\x1a\n0")
    ctrl.save_en_markdown(ctrl.en_markdown() + "\n\n![Figure 1](images/figure-1.png)\n")
    if not full:
        return wd
    wd.ko_md.write_text("# 번역\n\n![그림 1](images/figure-1.png)\n", encoding="utf-8")
    pdf = tmp_path / name / f"{name}.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    meta = json.loads(wd.meta_json.read_text(encoding="utf-8")) if wd.meta_json.exists() else {}
    wd.meta_json.write_text(json.dumps({**meta, "source": str(pdf)}), encoding="utf-8")
    notes.write(wd, notes.default_name(), "자유 메모 한 줄\n")
    wd.paper_meta_json.write_text(json.dumps(_META), encoding="utf-8")
    listen.keep_local(wd.root, b"m4a-bytes", {"sections": []})
    return wd


def _names(data: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def test_available_reports_what_the_paper_has(tmp_path):
    full = _paper(tmp_path, "full")
    bare = _paper(tmp_path, "bare", full=False)
    assert all(export_bundle.available(full.root).values())
    got = export_bundle.available(bare.root)
    assert got["en"] and not any(got[k] for k in ("ko", "pdf", "notes", "bib", "audio"))
    assert export_bundle.counts([full.root, bare.root]) == {
        "en": 2, "ko": 1, "pdf": 1, "notes": 1, "bib": 1, "audio": 1}


@pytest.mark.parametrize("which", ["en", "ko"])
def test_single_markdown_pick_matches_the_old_zip(tmp_path, which):
    wd = _paper(tmp_path, "p")
    old_name, old_data = UIController(wd).export_zip(which)
    name, data, media = export_bundle.bundle([wd.root], [which])
    assert (name, media) == (old_name, "application/zip")
    assert _names(data) == _names(old_data)


def test_full_pick_puts_extras_beside_markdown_folders(tmp_path):
    wd = _paper(tmp_path, "2017_Attention_Vaswani")
    stem = wd.root.stem
    name, data, _ = export_bundle.bundle([wd.root], export_bundle.ITEMS)
    assert name == f"{stem}.zip"
    names = set(_names(data))
    assert {f"{stem}-en/{stem}.en.md", f"{stem}-en/images/figure-1.png",
            f"{stem}-ko/{stem}.ko.md", f"{stem}-ko/images/figure-1.png",
            f"{stem}/{stem}.pdf", f"{stem}/{stem}.notes.md", f"{stem}/{stem}.bib",
            f"{stem}/{stem}.m4a"} == names
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.read(f"{stem}/{stem}.pdf") == b"%PDF-1.4 fake"
    assert "자유 메모 한 줄" in z.read(f"{stem}/{stem}.notes.md").decode()
    assert z.read(f"{stem}/{stem}.bib").decode().startswith("@")
    assert z.read(f"{stem}/{stem}.m4a") == b"m4a-bytes"


def test_missing_items_are_skipped(tmp_path):
    wd = _paper(tmp_path, "bare", full=False)
    _name, data, _ = export_bundle.bundle([wd.root], ["en", "ko", "pdf", "notes"])
    assert all(n.startswith("bare-en/") for n in _names(data))


def test_nothing_to_export_raises(tmp_path):
    wd = _paper(tmp_path, "bare", full=False)
    with pytest.raises(FileNotFoundError):
        export_bundle.bundle([wd.root], [])
    with pytest.raises(FileNotFoundError):
        export_bundle.bundle([wd.root], ["ko", "pdf"])
    with pytest.raises(FileNotFoundError):
        export_bundle.bundle([wd.root], ["pdf"])


@pytest.mark.parametrize("item,suffix,media", [
    ("pdf", ".pdf", "application/pdf"), ("notes", ".notes.md", "text/markdown"), ("audio", ".m4a", "audio/mp4")])
def test_one_file_item_comes_unzipped(tmp_path, item, suffix, media):
    wd = _paper(tmp_path, "p")
    name, data, got_media = export_bundle.bundle([wd.root], [item])
    assert name == f"p{suffix}" and got_media == media
    assert not data.startswith(b"PK")


def test_notes_bundle_includes_highlights(tmp_path):
    wd = _paper(tmp_path, "p")
    annotations.save(wd, [{"id": "a1", "side": "en", "row": 1, "start": 0, "quote": "a quoted sentence", "note": "내 메모"}])
    _name, data, _ = export_bundle.bundle([wd.root], ["notes"])
    text = data.decode()
    assert "자유 메모 한 줄" in text and "a quoted sentence" in text and "내 메모" in text


def test_many_papers_each_get_their_own_folders(tmp_path):
    a, b = _paper(tmp_path, "a"), _paper(tmp_path, "b", full=False)
    name, data, _ = export_bundle.bundle([a.root, b.root], ["en", "pdf"])
    assert name.endswith(".zip") and "2" in name
    names = _names(data)
    assert {n.split("/")[0] for n in names} == {"a-en", "a", "b-en"}
    assert "a/a.pdf" in names


def test_bulk_zip_keeps_old_name_and_layout(tmp_path):
    a, b = _paper(tmp_path, "a"), _paper(tmp_path, "b")
    name, data = bulk_zip([a.root, b.root], "ko")
    assert name == "narani-2편-ko.zip"
    assert {n.split("/")[0] for n in _names(data)} == {"a-ko", "b-ko"}


def test_obsidian_embeds_point_at_real_zip_paths(tmp_path):
    wd = _paper(tmp_path, "p")
    _name, data, _ = export_bundle.bundle([wd.root], export_bundle.ITEMS, "obsidian")
    z = zipfile.ZipFile(io.BytesIO(data))
    md = z.read("p-en/p.en.md").decode()
    embeds = re.findall(r"!\[\[([^\]|]+)", md)
    # 시험 원고(.md)가 부르는 figure-1.jpeg는 원래 파일이 없다 — 심어 둔 그림의 임베드만 본다
    assert "p-en/images/figure-1.png" in embeds and "p-en/images/figure-1.png" in z.namelist()


def test_dialog_prefs_default_and_validate():
    config.set_section_value("ui", export_dialog.PREF_KEY, None)
    assert export_dialog.prefs() == ["en", "ko", "pdf"]
    config.set_section_value("ui", export_dialog.PREF_KEY, ["audio", "bogus", "en"])
    assert export_dialog.prefs() == ["en", "audio"]
    config.set_section_value("ui", export_dialog.PREF_KEY, None)
