"""Google Drive 저장소 — 가짜 Drive(httpx MockTransport)로: 앱 폴더 찾기·만들기, 읽기·쓰기·덮어쓰기·지우기,
401이면 토큰 새로 받기, push가 Drive에도 그대로 돈다."""

from __future__ import annotations

import json
import re
import time
import urllib.parse

import httpx
import pytest

from narani.phone import gdrive


class FakeDrive:
    def __init__(self) -> None:
        self.files: dict[str, dict] = {}
        self.n = 0
        self.expire_next = False  # 다음 요청 하나를 401로
        self.calls: list[str] = []
        self.sessions: dict[str, dict] = {}  # 재개형 올리기 세션 → {meta} 또는 {fid}

    def _new(self, **f: object) -> str:
        self.n += 1
        fid = f"id{self.n}"
        self.files[fid] = {"id": fid, "modifiedTime": f"2026-10-23T00:00:{self.n:02d}Z", **f}
        return fid

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url, path = req.url, req.url.path
        self.calls.append(f"{req.method} {path}")
        if path == "/token":
            form = dict(urllib.parse.parse_qsl(req.content.decode()))
            assert form["grant_type"] == "refresh_token" and form["refresh_token"] == "rt"
            return httpx.Response(200, json={"access_token": f"at{self.n}", "expires_in": 3600})
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"error": "expired"})
        assert req.headers["Authorization"].startswith("Bearer ")
        if path == "/drive/v3/files" and req.method == "GET":
            q = url.params["q"]
            if "appProperties has" in q:
                hits = [f for f in self.files.values() if f.get("appProperties") == {"narani": "store"}]
            elif " in parents" in q:
                parent = re.match(r"'([^']+)' in parents", q).group(1)
                hits = [f for f in self.files.values() if parent in f.get("parents", [])]
            else:
                name = re.search(r"name = '([^']+)'", q).group(1)
                hits = [f for f in self.files.values() if f.get("name") == name and f.get("mimeType") == gdrive.FOLDER_MIME]
            hits.sort(key=lambda f: f["modifiedTime"], reverse=True)
            return httpx.Response(200, json={"files": [{k: f[k] for k in ("id", "name", "modifiedTime")} for f in hits]})
        if path == "/drive/v3/files" and req.method == "POST":
            meta = json.loads(req.content)
            return httpx.Response(200, json={"id": self._new(**meta)})
        if path.startswith("/upload/session/") and req.method == "PUT":
            sess = self.sessions.pop(path.rsplit("/", 1)[1])
            if "fid" in sess:
                self.files[sess["fid"]]["data"] = req.content
                return httpx.Response(200, json={"id": sess["fid"]})
            return httpx.Response(200, json={"id": self._new(**sess["meta"], data=req.content)})
        if url.params.get("uploadType") == "resumable":
            sid = f"s{len(self.sessions) + self.n}"
            m = re.fullmatch(r"/upload/drive/v3/files(?:/([^/]+))?", path)
            if m.group(1):
                if m.group(1) not in self.files:
                    return httpx.Response(404, json={"error": "notFound"})
                self.sessions[sid] = {"fid": m.group(1)}
            else:
                self.sessions[sid] = {"meta": json.loads(req.content)}
            assert int(req.headers["X-Upload-Content-Length"]) > gdrive.BIG
            return httpx.Response(200, headers={"Location": f"https://www.googleapis.com/upload/session/{sid}"})
        if path == "/upload/drive/v3/files" and req.method == "POST":
            boundary = req.headers["Content-Type"].split("boundary=")[1]
            parts = req.content.split(f"--{boundary}".encode())
            meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
            data = parts[2].split(b"\r\n\r\n", 1)[1][:-2]  # 끝의 \r\n
            return httpx.Response(200, json={"id": self._new(**meta, data=data)})
        m = re.fullmatch(r"/(upload/)?drive/v3/files/([^/]+)", path)
        if m:
            f = self.files.get(m.group(2))
            if f is None:
                return httpx.Response(404, json={"error": "notFound"})
            if req.method == "PATCH":
                f["data"] = req.content
                return httpx.Response(200, json={"id": f["id"]})
            if req.method == "DELETE":
                del self.files[f["id"]]
                return httpx.Response(204)
            if req.method == "GET" and url.params.get("alt") == "media":
                return httpx.Response(200, content=f["data"])
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "me@example.com"}})
        return httpx.Response(400, json={"error": f"unhandled {req.method} {path}"})


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive.config, "CONFIG_DIR", tmp_path / "cfg")
    fake = FakeDrive()
    http = httpx.Client(transport=httpx.MockTransport(fake))
    creds = gdrive.Credentials({"refresh_token": "rt", "access_token": "at0", "expires_at": time.time() + 3600},
                               "cid", "secret", http, persist=False)
    return fake, gdrive.DriveStore(creds, http)


def test_folder_created_once_and_files_round_trip(drive):
    fake, store = drive
    assert store.read("index.json") is None
    folders = [f for f in fake.files.values() if f.get("mimeType") == gdrive.FOLDER_MIME]
    assert len(folders) == 1 and folders[0]["name"] == "Narani" and folders[0]["appProperties"] == {"narani": "store"}
    store.write("papers/a.json", b'{"x": 1}')
    store.write("index.json", b"[]")
    assert store.read("papers/a.json") == b'{"x": 1}'
    store.write("papers/a.json", b'{"x": 2}')  # 같은 이름은 덮어쓴다 (새 파일을 만들지 않는다)
    assert len([f for f in fake.files.values() if f.get("name") == "papers/a.json"]) == 1
    assert store.read("papers/a.json") == b'{"x": 2}'
    store.delete("papers/a.json")
    assert store.read("papers/a.json") is None
    # 새 어댑터(다음 실행)도 같은 폴더를 찾는다
    again = gdrive.DriveStore(store.creds, store.http)
    assert again.read("index.json") == b"[]"
    assert len([f for f in fake.files.values() if f.get("mimeType") == gdrive.FOLDER_MIME]) == 1


def test_expired_token_is_refreshed_once(drive):
    fake, store = drive
    store.write("index.json", b"{}")
    fake.expire_next = True
    assert store.read("index.json") == b"{}"
    assert "POST /token" in fake.calls and store.creds.tok["access_token"].startswith("at")


def test_push_to_drive(drive, tmp_path):
    pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
    from test_phone import _wd

    from narani.phone import bundle, sync

    fake, store = drive
    wd = _wd(tmp_path, "bots")
    s = sync.push([wd.root], store)
    assert s["uploaded"] == 1
    ix = json.loads(store.read("index.json"))
    assert json.loads(store.read(ix["papers"][0]["file"]))["key"] == "bots"
    assert ix["papers"][0]["file"] == bundle.file_name("bots")
    assert sync.push([wd.root], gdrive.DriveStore(store.creds, store.http))["skipped"] == 1
    assert store.account() == "me@example.com"


def test_open_store_without_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive.config, "CONFIG_DIR", tmp_path / "cfg")
    with pytest.raises(gdrive.NotConnected):
        gdrive.open_store()


def test_client_ids_config_beats_built_in_defaults(tmp_path, monkeypatch):
    import base64

    monkeypatch.setattr(gdrive.config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(gdrive.config, "CONFIG_PATH", tmp_path / "config.toml")
    assert set(gdrive.client_ids()) == {"google_desktop_client_id", "google_desktop_client_secret",
                                        "google_web_client_id"}
    fake = {"google_desktop_client_id": "d.apps", "google_desktop_client_secret": "s", "google_web_client_id": "w.apps"}
    monkeypatch.setattr(gdrive, "_DEFAULTS", base64.b64encode(json.dumps(fake).encode()).decode())
    assert gdrive.client_ids() == fake  # 설정이 없으면 앱에 실린 기본값
    (tmp_path / "config.toml").write_text('[phone]\ngoogle_web_client_id = "mine.apps"\n', encoding="utf-8")
    assert gdrive.client_ids() == {**fake, "google_web_client_id": "mine.apps"}  # 설정이 이긴다
    monkeypatch.setattr(gdrive, "_DEFAULTS", "not base64 json")
    assert gdrive.client_ids()["google_desktop_client_id"] == ""  # 망가진 기본값은 없는 셈


def test_big_files_use_resumable_upload(drive):
    fake, store = drive
    big = b"\x01" * (gdrive.BIG + 10)
    store.write("audio/x.m4a", big, "audio/mp4")
    f = next(f for f in fake.files.values() if f.get("name") == "audio/x.m4a")
    assert f["data"] == big and f["mimeType"] == "audio/mp4"
    store.write("audio/x.m4a", big[::-1] + b"!", "audio/mp4")      # 덮어쓰기도 재개형으로
    assert store.read("audio/x.m4a") == big[::-1] + b"!"
    assert any("PUT /upload/session/" in c for c in fake.calls)
    assert not any(c.startswith("POST /upload/drive/v3/files") and "multipart" in c for c in fake.calls)
