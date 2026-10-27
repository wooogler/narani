"""처음 설정 마법사 — 언제 뜨나, 사용자 언어 추정, 저장 (화면 없이 시험할 수 있는 부분)."""

import pytest

from narani import config, i18n, library
from narani.ui import setup_wizard as sw


@pytest.fixture(autouse=True)
def _empty_config():
    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()
    yield
    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()


@pytest.mark.parametrize("cfg, want", [
    ({}, True),
    ({"output": {"workspace": "/tmp/ws"}}, True),  # 켤 때마다 pin_workspace가 적는 값은 흔적이 아니다
    ({"keys": {"openai": "x"}}, False),
    ({"ui": {"language": "en"}}, False),
    ({"translate": {"target_lang": "ja"}}, False),
    ({"default_provider": "gemini"}, False),
    ({"library": {"en_dir": "/tmp/lib"}}, False),
    ({"setup": {"done": True}}, False),
    ({"setup": {"step": "ai"}, "ui": {"language": "en"}}, True),  # 마법사 도중이면 언어가 있어도 계속
    ({"setup": {"step": "ai", "done": True}}, False),
])
def test_needs_setup(cfg, want):
    assert sw.needs_setup(cfg) is want


def test_needs_setup_reads_config_file():
    assert sw.needs_setup() is True
    sw.finish()
    assert sw.needs_setup() is False
    assert "step" not in config.load_config()["setup"]


@pytest.mark.parametrize("header, want", [
    ("ko-KR,ko;q=0.9,en-US;q=0.8", "ko"),
    ("ja", "ja"),
    ("zh-CN,zh;q=0.9", "zh-Hans"),
    ("zh-TW", "zh-Hant"),
    ("zh-Hant-HK", "zh-Hant"),
    ("es-419,es;q=0.9", "es"),
    ("pt-BR", "pt"),
    ("en-US,en;q=0.9", "en"),
    ("*, de;q=0.5", "de"),
])
def test_guess_lang(header, want):
    assert sw.guess_lang(header) == want


def test_guess_lang_falls_back_to_os_locale(monkeypatch):
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    assert sw.guess_lang(None) == "ja"
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setattr(sw, "_os_locale", lambda: None)
    assert sw.guess_lang("") == "en"


def test_ui_and_target_language_for_guess():
    assert sw.ui_lang_for("ja") == "ja"
    assert sw.ui_lang_for("fr") == "en"  # 카탈로그가 없는 화면 언어는 영어
    assert sw.target_for("fr") == "fr"  # 번역은 그 언어로
    assert sw.target_for("en") == "ko"  # 영어 사용자는 지금 값(기본 한국어)을 둔다


def test_set_ui_lang_switches_immediately():
    sw._set_ui_lang("en")
    assert i18n.ui_lang() == "en"
    sw._set_ui_lang("ko")  # 같은 크기 파일을 곧바로 다시 써도 캐시가 옛 값을 주지 않는다
    assert i18n.ui_lang() == "ko"


def test_save_ai_uses_settings_keys():
    sw.save_ai("gemini", "  AIza-test  ")
    cfg = config.load_config()
    assert cfg["default_provider"] == "gemini"
    assert cfg["models"]["gemini"] == config.DEFAULT_MODELS["gemini"]
    assert cfg["keys"]["gemini"] == "AIza-test"
    sw.save_ai("openai", None)  # 키 없이 서비스만
    assert config.load_config()["default_provider"] == "openai"
    assert "openai" not in config.load_config()["keys"]


def test_apply_library_lays_out_one_folder(tmp_path):
    base = sw.apply_library(str(tmp_path / "Narani"))
    assert library.dir_for("en") == base
    assert library.dir_for("ko") == base / "ko"
    assert library.dir_for("pdf") == base / "pdf"
    assert (base / "ko").is_dir() and (base / "pdf").is_dir()
    assert sw.default_library_dir() == base


def test_preselect_language_on_first_run_only():
    assert sw.preselect_language("ja-JP,ja;q=0.9") is True
    cfg = config.load_config()
    assert cfg["ui"]["language"] == "ja" and cfg["translate"]["target_lang"] == "ja"
    assert sw.needs_setup() is True  # 언어를 적어도 마법사는 이어서 뜬다
    assert sw.preselect_language("es") is False  # 이미 골랐으면 다시 고르지 않는다
    assert i18n.ui_lang() == "ja"


def test_preselect_language_leaves_existing_users_alone():
    config.set_key("openai", "x")
    assert sw.preselect_language("ja") is False
    assert "ui" not in config.load_config()


def test_friendly_error():
    assert "키가 맞지" in sw.friendly_error("Error code: 401 - {'error': {'code': 'invalid_api_key'}}")
    assert "크레딧" in sw.friendly_error("Error code: 429 - insufficient_quota")
    assert "인터넷" in sw.friendly_error("Connection error.")
    assert sw.friendly_error("model not found") == ""
