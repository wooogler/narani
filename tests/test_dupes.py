"""같은 PDF 다시 올리기 — 내용 해시로 라이브러리의 논문 찾기(§dupes)."""

import json
import os
from pathlib import Path

from narani import dupes, library, projects, workdir
from narani.ui import app
from narani.workdir import WorkDir, hash_bytes


def _paper(ws: Path, name: str, data: bytes) -> WorkDir:
    """업로드 → 변환이 끝난 것처럼: <ws>/<이름>/<이름>.pdf + <이름>.md4 (sections.yaml·meta.json source)."""
    src = app.save_source(data, f"{name}.pdf", ws)
    wd = WorkDir(src.parent / f"{src.stem}.md4")
    wd.ensure()
    wd.structure.mkdir(parents=True, exist_ok=True)
    wd.sections_yaml.write_text(f"title: {name}\n", encoding="utf-8")
    wd.extract.mkdir(parents=True, exist_ok=True)
    wd.meta_json.write_text(json.dumps({"source": str(src)}), encoding="utf-8")
    return wd


def test_same_bytes_under_another_name_is_found_and_other_bytes_are_not(tmp_path):
    ws = tmp_path / "ws"
    a = _paper(ws, "attention", b"%PDF-A")
    _paper(ws, "bert", b"%PDF-B")
    assert dupes.find(hash_bytes(b"%PDF-A"), ws) == [a.root]  # 'renamed.pdf'로 올려도 내용으로 찾는다
    assert dupes.find(hash_bytes(b"%PDF-C"), ws) == []


def test_reconverted_copies_all_match(tmp_path):
    """'그래도 새로 변환'으로 만든 '(2)' 논문도 같은 내용 — 둘 다 나온다."""
    ws = tmp_path / "ws"
    a = _paper(ws, "attention", b"%PDF-A")
    b = _paper(ws, "attention", b"%PDF-A")
    assert b.root.parent.name == "attention (2)"
    assert set(dupes.find(hash_bytes(b"%PDF-A"), ws)) == {a.root, b.root}


def test_hashes_are_cached_by_size_and_mtime(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    wd = _paper(ws, "attention", b"%PDF-A")
    dupes.cache_path().unlink(missing_ok=True)  # save_source가 적어 둔 해시를 지워 '옛 논문'처럼
    calls: list[Path] = []
    real = workdir.hash_file
    monkeypatch.setattr(workdir, "hash_file", lambda p: calls.append(Path(p)) or real(p))

    dupes.library_index(ws)
    assert len(calls) == 1  # 처음 한 번은 읽는다
    dupes.library_index(ws)
    assert len(calls) == 1  # 크기·mtime이 같으면 캐시

    pdf = wd.root.with_suffix(".pdf")
    pdf.write_bytes(b"%PDF-A-changed")
    os.utime(pdf, ns=(1, 1))
    assert dupes.find(hash_bytes(b"%PDF-A-changed"), ws) == [wd.root]
    assert len(calls) == 2  # 바뀐 파일은 다시 읽는다


def test_upload_records_hash_so_index_does_not_reread(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _paper(ws, "attention", b"%PDF-A")
    monkeypatch.setattr(workdir, "hash_file", lambda p: (_ for _ in ()).throw(AssertionError("read")))
    assert dupes.find(hash_bytes(b"%PDF-A"), ws)


def test_collection_copy_counts_as_the_same_paper(tmp_path):
    """컬렉션 폴더의 PDF 사본은 따로 세지 않는다 — 원본이 사라져도 사본으로 원래 논문 하나를 찾는다."""
    ws = tmp_path / "ws"
    wd = _paper(ws, "attention", b"%PDF-A")
    proj = projects.create("SCALE", root=str(ws / "scale-folder"))  # 작업 폴더 안의 컬렉션 폴더여도
    library.set_memberships(wd, [proj["id"]])
    copy = ws / "scale-folder" / "pdf" / "attention.pdf"
    assert copy.read_bytes() == b"%PDF-A"
    digest = hash_bytes(b"%PDF-A")
    assert dupes.find(digest, ws) == [wd.root]

    wd.root.with_suffix(".pdf").unlink()  # 작업 폴더의 원본을 잃었다
    assert dupes.source_pdf(wd.root) == copy
    assert dupes.find(digest, ws) == [wd.root]


def test_markdown_only_paper_is_skipped(tmp_path):
    ws = tmp_path / "ws"
    wd = WorkDir(ws / "notes" / "notes.md4")
    wd.ensure()
    wd.structure.mkdir(parents=True, exist_ok=True)
    wd.sections_yaml.write_text("title: notes\n", encoding="utf-8")
    assert dupes.source_pdf(wd.root) is None
    assert dupes.library_index(ws) == {}


def test_stale_cache_entries_are_pruned(tmp_path):
    ws = tmp_path / "ws"
    wd = _paper(ws, "attention", b"%PDF-A")
    pdf = wd.root.with_suffix(".pdf")
    key = str(pdf.resolve())
    assert key in json.loads(dupes.cache_path().read_text(encoding="utf-8"))
    pdf.unlink()  # 논문을 지웠거나 옮겼다
    dupes.library_index(ws)
    assert key not in json.loads(dupes.cache_path().read_text(encoding="utf-8"))


def test_adding_the_scope_collection_keeps_other_memberships(tmp_path):
    """'이 컬렉션에 넣기'는 옮기기가 아니라 더하기 — N:M 소속 모델."""
    ws = tmp_path / "ws"
    wd = _paper(ws, "attention", b"%PDF-A")
    a, b = projects.create("A"), projects.create("B")
    library.set_memberships(wd, [a["id"]])
    library.set_memberships(wd, library.projects_of(wd) + [projects.assign_target(b["id"])])
    assert library.projects_of(wd) == [a["id"], b["id"]]
    assert projects.assign_target(projects.ALL) == ""  # 모든 논문·미분류에서 올리면 '넣기'는 없다
    assert projects.assign_target(projects.NONE) == ""
