"""듣기 음성 만들기와 웹 리더로 보내기의 분리 — 음성은 이 컴퓨터에서 Drive 없이 만들고, 보낼 때는 올리기만 한다."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani.phone import gdrive, service, sync, tts  # noqa: E402
from narani.phone.store import LocalStore  # noqa: E402
from narani.ui import listen  # noqa: E402
from tests.test_phone_tts import KO, FakeVoice, _wd  # noqa: E402

needs_codec = pytest.mark.skipif(not tts.codec_available(), reason="m4a 인코더(afconvert·PyAV) 필요")

_AUDIO_KEYS = {"file", "tl", "hash", "bytes", "duration", "voice", "src", "sections", "include", "built",
               "captions", "math"}   # 웹 리더 목록 줄의 audio — 웹 리더·_remake_stale_audio가 읽는 꼴


@pytest.fixture
def env(tmp_path, monkeypatch):
    """가짜 Mac 목소리(합성 횟수를 셈) · 시험 설정 폴더 · Drive는 처음엔 연결 안 됨(open_store가 실패)."""
    wd = _wd(tmp_path)
    rows = [{"root": wd.root, "name": wd.root.stem, "pinned": False, "has_ko": True, "hidden": False}]
    monkeypatch.setattr(service.config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(service, "_rows", lambda: rows)
    monkeypatch.setattr(service, "prepare_math", lambda root, captions=True: 0)   # AI 부르지 않게
    calls = {"n": 0}

    class Voice(FakeVoice):
        def __init__(self, name, rate=None):  # noqa: ANN001
            super().__init__()
            self.id = f"mac:{name}"

        def synth(self, texts, on_each=None):  # noqa: ANN001
            calls["n"] += len(texts)
            return super().synth(texts, on_each)
    Voice.available = staticmethod(lambda: True)
    monkeypatch.setattr(tts, "MacVoice", Voice)
    monkeypatch.setattr(tts, "mac_voices", lambda lang: ["Yuna", "Eddy"])
    drive = {"on": False, "store": LocalStore(tmp_path / "store")}

    def open_store():
        if not drive["on"]:
            raise gdrive.NotConnected("연결 안 됨")
        return drive["store"]
    monkeypatch.setattr(gdrive, "open_store", open_store)
    monkeypatch.setattr(service, "connected", lambda: drive["on"])
    monkeypatch.setattr(service, "configured", lambda: True)
    return {"wd": wd, "calls": calls, "drive": drive}


@needs_codec
def test_make_without_drive_and_state(env):
    wd = env["wd"]
    out = service.make_audio([wd.root], send=False)             # Drive 없이
    assert out["made"] == 1 and out["sent"] == 0 and out["send_error"] is None and out["seconds"] > 0
    m4a, tl = listen.local_audio(wd.root)
    assert m4a.read_bytes()[4:8] == b"ftyp" and tl["voice"] == "mac:Yuna"
    rec = listen.local_record(wd.root)
    assert rec["include"] is None and rec["captions"] is True and rec["built"] > 0 and rec["hash"]
    assert service.voices("ko")[0] == "Yuna" and service.on_mobile() == {}   # 고름은 기억, 웹 리더엔 없음
    st = service.audio_state(wd.root)
    assert st["local"]["voice"] == "mac:Yuna" and st["local"]["stale"] is False
    assert st["web"] == {"configured": True, "connected": False, "on": False, "audio": None}
    # 뷰어 막대가 받는 것 — 만든 음성 + 상태
    info = listen.info(wd.root)
    assert info["audio"]["tl"]["voice"] == "mac:Yuna" and info["state"]["local"]["label"].startswith("Mac · Yuna")
    assert info["job"] is None
    # 본문이 바뀌면 '다시 만들어야 함' — 다시 만들면 바뀐 섹션만 합성한다
    wd.ko_md.write_text(KO.replace("면담했다", "만났다"), encoding="utf-8")
    assert service.audio_state(wd.root)["local"]["stale"] is True
    n = env["calls"]["n"]
    service.make_audio([wd.root], send=False)
    assert 0 < env["calls"]["n"] - n < n and service.audio_state(wd.root)["local"]["stale"] is False


@needs_codec
def test_send_uploads_what_was_made_without_resynthesis(env):
    wd, store = env["wd"], env["drive"]["store"]
    service.make_audio([wd.root], include=None, send=False)
    n = env["calls"]["n"]
    env["drive"]["on"] = True
    out = service.send([wd.root])                                # 홈·뷰어의 '웹 리더로 보내기'
    assert out["uploaded"] == 1 and out["audio"] == 1 and env["calls"]["n"] == n
    e = sync.read_index(store)["papers"][0]
    assert set(e["audio"]) == _AUDIO_KEYS and e["audio"]["src"] == e["hash"] and e["audio"]["voice"] == "mac:Yuna"
    m4a, _tl = listen.local_audio(wd.root)
    assert store.read(e["audio"]["file"]) == m4a.read_bytes()
    assert json.loads(store.read(e["audio"]["tl"]))["sents"]
    assert service.audio_state(wd.root)["web"] == {"configured": True, "connected": True, "on": True, "audio": "same"}
    assert service.send([wd.root])["audio"] == 0                 # 같은 음성은 다시 올리지 않는다
    assert service.refresh()["audio"] == 0 and env["calls"]["n"] == n   # 옛 음성이 아니니 다시 만들지도 않는다


@needs_codec
def test_papers_on_web_reader_are_sent_after_making(env, tmp_path):
    wd, store = env["wd"], env["drive"]["store"]
    other = _wd(tmp_path, "2026_Other")
    env["drive"]["on"] = True
    service.send([wd.root])                                      # 웹 리더에 있는 논문
    out = service.make_audio([wd.root], send=False)
    assert out["sent"] == 1 and sync.read_index(store)["papers"][0]["audio"]["voice"] == "mac:Yuna"
    out = service.make_audio([other.root], send=False)           # 없는 논문은 고르지 않으면 보내지 않는다
    assert out["sent"] == 0 and other.root.stem not in service.on_mobile()
    out = service.make_audio([other.root], send=True)            # '웹 리더로도 보내기'
    assert out["sent"] == 1 and service.on_mobile()[other.root.stem]["audio"]["voice"] == "mac:Yuna"


@needs_codec
def test_failed_upload_keeps_local_audio_and_refresh_sends_it(env):
    wd, store_ok = env["wd"], env["drive"]["store"]
    env["drive"]["on"] = True
    service.send([wd.root])

    def offline():
        raise OSError("오프라인")
    gdrive_open = gdrive.open_store
    gdrive.open_store = offline   # 웹 리더에 있다고 기억하지만 지금은 오프라인 (fixture의 monkeypatch가 되돌린다)
    try:
        out = service.make_audio([wd.root], send=False)
    finally:
        gdrive.open_store = gdrive_open
    assert out["made"] == 1 and out["sent"] == 0 and "오프라인" in out["send_error"]
    assert listen.local_audio(wd.root) is not None and "audio" not in sync.read_index(store_ok)["papers"][0]
    n = env["calls"]["n"]
    out = service.refresh()                                      # 켤 때 맞추기가 못 올린 음성을 올린다
    assert out["sent_audio"] == 1 and out["audio"] == 0 and env["calls"]["n"] == n
    assert sync.read_index(store_ok)["papers"][0]["audio"]["voice"] == "mac:Yuna"
    assert service.refresh()["sent_audio"] == 0


@needs_codec
def test_older_local_copy_does_not_overwrite_web_reader_audio(env):
    """웹 리더 음성을 섹션 캐시로 이은 사본(rebuild_local)은 그 음성과 같은 것으로 본다 — 다시 올리지 않는다."""
    wd, store = env["wd"], env["drive"]["store"]
    env["drive"]["on"] = True
    service.make_audio([wd.root], send=True)
    listen.drop_local(wd.root)
    assert listen.info(wd.root)["audio"] is not None             # 웹 리더 기록으로 다시 이음
    assert service.audio_state(wd.root)["web"]["audio"] == "same"
    before = sync.read_index(store)["papers"][0]["audio"]
    assert service.send([wd.root])["audio"] == 0 and sync.read_index(store)["papers"][0]["audio"] == before


def test_job_info_for_listen_bar():
    from narani.ui import jobs

    assert listen.job_info(None) is None
    j = jobs.Job(root="/x", kind="mobile_audio", label="음성 만들기")
    j.progress(3, 12)
    j.detail["section"] = "1 서론"
    assert listen.job_info(j) == {"pct": 25, "section": "1 서론", "queued": False}
    j.status = "done"
    assert listen.job_info(j) is None


def test_listen_bar_menu_hooks():
    # ⋯ 메뉴 — 상태 두 줄, 음성 만들기·멈추기·웹 리더로 보내기는 뷰어(Python)로 이벤트를 보낸다
    for a in ('data-a="more"', 'data-a="make"', 'data-a="send"', 'data-a="stop"', "mdl-st-local", "mdl-st-web",
              "mdl-prog"):
        assert a in listen.HTML
    assert "emitEvent('md4-listen-' + a" in listen.JS
    for api in ("setJob:", "reload:", "refreshState:"):
        assert api in listen.JS
    from narani.ui import app

    src = __import__("inspect").getsource(app._listen_audio_hooks)
    for ev in ("md4-listen-make", "md4-listen-send", "md4-listen-stop"):
        assert ev in src


def test_home_and_viewer_share_one_gate_and_kind():
    from narani.ui import audio_dialog, home

    assert home._AUDIO_GATE is audio_dialog.GATE and audio_dialog.KIND == "mobile_audio"
    assert home.audio_label is audio_dialog.audio_label
    assert audio_dialog.audio_label({"voice": "win:Microsoft Heami Desktop", "duration": 600}).startswith("Windows · Heami")


def test_same_audio_with_new_src_is_still_sent(tmp_path):
    """본문 지문만 바뀌고 읽는 글은 같아 m4a가 바이트까지 같아도 — 시간표의 src가 다르면 올린다(웹 리더가 옛 음성으로
    알지 않게, 켤 때마다 다시 잇지 않게). 같은 음성·같은 src면 건너뛰고, m4a를 읽지 않는다."""
    wd = _wd(tmp_path)
    store = LocalStore(tmp_path / "store")
    sync.push([wd.root], store)
    tl = {"format": 1, "key": wd.root.stem, "src": "old", "voice": "mac:Yuna", "side": "ko", "duration": 3.0,
          "sections": [{"title": "x", "row": 1, "start": 0, "end": 3}], "sents": []}
    listen.keep_local(wd.root, b"same-m4a", tl, {"captions": True})
    assert sync.put_audio(store, [wd.root])["sent"] == 1
    assert sync.put_audio(store, [wd.root])["sent"] == 0
    listen.keep_local(wd.root, b"same-m4a", {**tl, "src": "new"}, {"captions": True})
    assert sync.put_audio(store, [wd.root])["sent"] == 1
    e = sync.read_index(store)["papers"][0]
    assert e["audio"]["src"] == "new" and json.loads(store.read(e["audio"]["tl"]))["src"] == "new"
    assert set(e["audio"]) == _AUDIO_KEYS and e["audio"]["captions"] is True
    # 웹 리더 것보다 먼저 만든 사본은 켤 때 맞추기(newer_only)로 덮지 않는다
    e["audio"]["built"] = 9e12
    idx = sync.read_index(store)
    idx["papers"][0]["audio"]["built"] = 9e12
    store.write(sync.INDEX, json.dumps(idx).encode())
    listen.keep_local(wd.root, b"other-m4a", {**tl, "src": "newer"})
    assert sync.put_audio(store, [wd.root], newer_only=True)["sent"] == 0
    assert sync.put_audio(store, [wd.root])["sent"] == 1
