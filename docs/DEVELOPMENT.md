# Narani 개발 문서

Narani를 고치거나, 명령줄로 쓰거나, 구조를 알고 싶은 사람을 위한 문서입니다. 앱 사용법은 [README](../README.md)에 있습니다.

- [개발 환경](#개발-환경)
- [개발 서버 띄우기](#개발-서버-띄우기)
- [명령줄(CLI)](#명령줄cli)
- [AI 키와 모델](#ai-키와-모델)
- [파일이 저장되는 곳](#파일이-저장되는-곳)
- [구조 — 변환 파이프라인](#구조--변환-파이프라인)
- [테스트와 린트](#테스트와-린트)
- [화면 문구(i18n) 규칙](#화면-문구i18n-규칙)
- [모바일 웹앱 배포](#모바일-웹앱-배포)
- [업데이트와 런처](#업데이트와-런처)
- [README 스크린샷 다시 찍기](#readme-스크린샷-다시-찍기)

---

## 개발 환경

패키지 관리자 [uv](https://docs.astral.sh/uv/)만 있으면 파이썬(3.11 이상)까지 uv가 받습니다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh        # macOS/Linux (Windows는 아래)
git clone https://github.com/wooogler/narani.git
cd narani
uv sync --extra ui --extra native --extra agent --extra enrich
uv run narani doctor                                     # 환경 점검
```

Windows PowerShell에서 uv 설치: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` — 설치 뒤 창을 새로 열어야 `uv`가 잡힙니다.

| extra | 내용 |
|---|---|
| `ui` | 웹 UI (NiceGUI) |
| `native` | 독립 앱 창 (pywebview). Linux는 WebKit2GTK가 필요하고, 없으면 브라우저로 엽니다 |
| `agent` | 에이전트 창 (pydantic-ai-slim + OpenAI·Anthropic·Gemini SDK). `ui`가 함께 끌어옵니다 |
| `enrich` | 서지 보강 (httpx) |

> `uv sync --extra X`는 **지정하지 않은 extra를 지웁니다.** 필요한 것을 한 번에 모두 주세요.

클론 없이 한 번 실행해 보기:
```bash
uvx --from 'narani[ui] @ git+https://github.com/wooogler/narani' narani ui
```

---

## 개발 서버 띄우기

```bash
uv run narani ui                       # 브라우저로 http://127.0.0.1:8080
uv run narani ui --native              # 앱 창으로
uv run narani ui --no-show --port 8091 --upload-dir output
uv run narani ui output/<논문>/<논문>.md4/   # 그 논문을 바로 열기
```

- 서버는 **127.0.0.1에만** 붙습니다. 포트가 쓰이고 있으면 빈 포트를 골라 주소를 출력합니다.
- 실제 설정(`~/.config/narani`)을 건드리지 않으려면 `NARANI_CONFIG_DIR=<임시 폴더>`를 주세요. 키·컬렉션·탭·에이전트 세션이 모두 그 폴더에 쌓입니다.
- 설정의 `[library]`(저장 위치) 자동 내보내기가 켜져 있으면 개발 서버도 실제 폴더에 씁니다. 임시 설정에서는 빼 두세요.
- 메인 체크아웃의 `.venv`에는 패키지가 **편집 가능(editable)** 으로 설치돼 있습니다. git 워크트리에서 그 `.venv`로 서버를 띄울 때는 `PYTHONPATH=<워크트리>/src`를 붙여야 워크트리 코드가 돕니다.
- 헤드리스 서버에서는 `--no-show`로 띄우고 SSH 포트 포워딩으로 접속합니다(`ssh -L 8080:127.0.0.1:8080 <서버>`). 인증이 없으니 외부에 열지 마세요.

| 환경 변수 | 뜻 |
|---|---|
| `NARANI_CONFIG_DIR` | 설정 폴더 위치 (기본 `~/.config/narani`) |
| `NARANI_NO_UPDATE_CHECK=1` | 켤 때 업데이트 확인 끄기 |
| `NARANI_SPEC` | 설치 스크립트가 깔 대상 (예: `'narani[ui,native] @ git+https://github.com/나/narani@브랜치'`) |
| `NARANI_NO_NATIVE_DIALOG=1` | OS 폴더 선택 대화상자를 끄고 경로를 직접 입력 (테스트·헤드리스용) |
| `OPENAI_API_KEY` · `ANTHROPIC_API_KEY` · `GEMINI_API_KEY` | AI 키 (설정 파일보다 우선) |

---

## 명령줄(CLI)

웹 UI로 하는 일은 대부분 명령줄에서도 됩니다. 저장소 안에서는 `uv run narani …`, 한 줄 설치본은 `narani …`입니다.

```bash
narani doctor                    # 환경 점검 (의존성·추출 백엔드·AI 키)
narani convert paper.pdf         # PDF(또는 .md) → paper.md4/out/paper.en.md
narani review paper.md4/         # 섹션 매니페스트를 $EDITOR로 고친 뒤 다시 조립
narani cite paper.md4/           # 참고문헌 파싱 + 본문 인용 링크 (AI)
narani glossary paper.md4/       # 번역 전 용어집 만들기 (AI)
narani translate paper.md4/      # 번역 → out/paper.<언어>.md (한국어는 paper.ko.md) (AI)
narani figures paper.md4/        # 예전 논문의 그림을 고해상도(288dpi)로 다시 자르기
narani ui [paper.md4/]           # 웹 UI
narani app [--remove]            # 앱 아이콘 등록 / 제거
narani update                    # GitHub에서 새 버전 받기
narani language [ja]             # 번역 언어(모국어) 조회 / 설정
narani keys set openai           # AI 키 저장 (config.toml, 0600) · keys list
narani workspace [<경로>]        # 작업 폴더 조회 / 변경
narani collection …              # 컬렉션: list · add · use · assign · dir · set · glossary · export · rm
narani library …                 # 저장 위치: --en/--ko/--pdf, --root, --export, --off
narani naming […]                # 파일 이름 규칙 조회 / 변경 / --apply
narani bib --all                 # references.bib 다시 쓰기 (받아 둔 기록만, 네트워크 안 씀)
narani enrich --all              # 서지를 온라인에서 보강 (제목만 전송)
narani prefs list                # 헤더 이름별로 기억한 처리
narani phone …                   # 모바일: connect · push [--drive] · disconnect · site · preview
```

주요 옵션:

- `convert --ocr` — 스캔 PDF용 OCR (느립니다)
- `convert --flavor standard|obsidian|notion` — 작업 폴더의 파일 자체를 그 형식으로 저장
- `translate --lang ja` — 이 논문을 다른 언어로 번역 / `--style 합니다체` 또는 `--style 'custom:…'` / `--model` · `--provider` / `--yes`(용어집 확인 건너뛰기)
- `cite --style keep|authoryear|short` — 인용 표기
- `collection set <이름> export_target obsidian` · `--unset` — 컬렉션별 설정
- `enrich --collection <이름>` · `--mailto you@example.com`(polite pool)

컬렉션·저장 위치 예:
```bash
narani collection add "튜터 챗봇 서베이" --dir ~/Vault/Tutor
narani collection use "튜터 챗봇 서베이"              # 새 논문이 들어갈 컬렉션
narani collection assign "튜터 챗봇" output/2017_…/2017_….md4
narani library --root ~/Papers                        # 공통 저장 위치를 폴더 하나로
narani naming "{author}{year}_{title}" && narani naming --apply
```

---

## AI 키와 모델

| 서비스 | 환경 변수 | 기본 모델 |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `gpt-6-luna` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` |
| Google Gemini | `GEMINI_API_KEY` | `gemini-3.5-flash-lite` |

기본 모델은 각 회사 최신 세대에서 가장 싼 등급입니다. 가격표는 [`src/narani/llm/base.py`](../src/narani/llm/base.py)에 있고, 실행이 끝나면 이 표로 계산한 실제 비용을 출력합니다. 테스트는 가짜 프로바이더로 돌아 AI를 부르지 않습니다.

---

## 파일이 저장되는 곳

| 항목 | 경로 |
|---|---|
| 작업 폴더 (기본) | 저장소에서 실행: `<저장소>/output/` · 설치본: `~/narani/output/` |
| 논문별 작업 폴더 | `<작업 폴더>/<이름>/<이름>.md4/` — 추출 원문·구조·번역·로그 |
| 최종 마크다운 | `<이름>.md4/out/paper.en.md`, `paper.<언어>.md`, `out/images/` |
| 하이라이트·메모 | `<이름>.md4/annotations.json`, `notes.md`, `notes/` |
| 논문 상태 | `<이름>.md4/status.json` — 단계별 해시, 번역 언어 `lang`, 소속 컬렉션 `projects` |
| 설정·AI 키 | `~/.config/narani/config.toml` (Windows `C:\Users\<사용자>\.config\narani\`) |
| 컬렉션 | `~/.config/narani/projects.json`, 컬렉션 용어집 `glossary/<id>.yaml` |
| 열린 탭 · 헤더 처리 기억 | `open_tabs.json` · `heading_prefs.json` (설정 폴더) |
| 에이전트 세션 | `~/.config/narani/agent/sessions/` |
| Google Drive 연결 | `~/.config/narani/google_drive.json` (0600) |
| Docling 모델 | `~/.cache/huggingface` |
| 앱 런처 | macOS `~/Applications/Narani.app` · Windows 시작 메뉴 · Linux `~/.local/share/applications/Narani.desktop` |
| 실행 로그 | macOS `~/Library/Logs/narani.log`, 업데이트 `narani-update.log` |

작업 폴더는 UI를 처음 켤 때의 값이 설정에 기록됩니다. 그래서 클론으로 쓰다가 설치본으로 바꿔도 같은 폴더를 계속 봅니다. 바꾸려면 `narani workspace <경로>`나 `narani ui --upload-dir <경로>`.

논문 폴더 확장자 `.md4`와 폰 쪽 localStorage 키(`narani:*`, `md4m:*`)는 옛 이름(md4paper)의 흔적이지만 호환을 위해 그대로 둡니다.

---

## 구조 — 변환 파이프라인

각 단계는 다시 시작할 수 있는 순수 함수이고, `status.json`의 해시로 바뀌지 않은 단계는 건너뜁니다([`pipeline.py`](../src/narani/pipeline.py)). 어느 단계가 실패해도 그 전 산출물은 유효합니다.

| 단계 | 모듈 | 하는 일 | AI |
|---|---|---|---|
| 1. 추출 | `extract/` | Docling으로 PDF → 날 마크다운(`raw.md`). 읽는 순서·표·인라인 수식·각주·러닝 헤더·줄 번호 정리 | 없음 |
| 수식·첫 쪽 | `formula_fix.py`, `extract/front_matter.py` | 빈 수식 자리를 PDF에서 잘라 LaTeX로 읽기, 저자·소속 정리 | 선택 |
| 2. 구조 | `structure/` | 헤더 레벨(번호 체계·run-in 소제목), 캡션 짝짓기 → 섹션 매니페스트 | 없음 |
| 3. 조립 | `assemble/`, `review/manifest.py` | 매니페스트대로 `out/paper.en.md`와 그림 조립. 사람이 고친 레벨·제외를 반영 | 없음 |
| 4. 인용 | `cite/` | 참고문헌 파싱, 본문 인용을 링크·표기로 | 사용 |
| 5. 번역 | `translate/` | 용어집 → 청크 → 번역 → 구조 검증 → `paper.<언어>.md`. 청크별 캐시 | 사용 |

그 밖의 폴더:

- `ui/` — NiceGUI 화면. `home.py`(홈), `app.py`(논문 화면), `controller.py`(화면 ↔ 파이프라인), `split_view.py`(위아래 두 뷰어), `jump_back.py`(읽던 자리), `pdf_zoom.py`, `formula_pop.py`, `figure_ko.py`(그림 글자 번역 메뉴), `live_preview.py`(마크다운 편집기), `find_bar.py`
- `ui/agent/` — 에이전트: 검색(BM25), 도구, 세션, SSE 라우트. 창 UI는 `ui/agent_panel.py`
- `phone/` — 모바일: 번들 굽기(`bundle.py`), 저장소(`store.py`·`gdrive.py`), 동기화(`sync.py`·`service.py`), 웹앱 파일(`web.py`)
- `llm/` — OpenAI·Anthropic·Gemini 어댑터와 가격표
- `figure_translate.py`·`figure_alt.py`·`figure_fix.py` — 그림 글자 번역, PDF 대체 텍스트 짝짓기, 영역 다시 자르기
- `langs.py`·`i18n.py`·`locales/` — 번역 언어 프리셋과 화면 언어 카탈로그
- `projects.py`·`library.py`·`bibtex.py`·`enrich.py` — 컬렉션, 저장 위치, BibTeX, 서지 보강

설계 기록과 마일스톤은 [PLAN.md](../PLAN.md)에 있습니다.

### 번역 언어

번역 언어는 논문마다 하나입니다(`status.json`의 `lang`). 한국어는 옛 파일 이름(`paper.ko.md`, `translate/cache.json`, `glossary.yaml`)을 그대로 쓰고, 다른 언어는 `paper.ja.md`, `cache.ja.json`처럼 언어 접미사를 붙입니다. 코드의 `ko`는 대개 "번역 쪽"이라는 뜻입니다(`wd.ko_md`, annotations의 `side`).

한국어 시스템 프롬프트·용어집 줄·문체 문자열은 **바이트 하나도 바꾸면 안 됩니다.** 캐시 키가 이 글자에서 나오므로, 바뀌면 기존 번역 캐시를 못 써 전체를 다시 번역합니다. 바꿔야 하면 다른 언어용 빌더를 옆에 두세요.

---

## 테스트와 린트

```bash
uv run pytest -q          # 전부 가짜 프로바이더로 돌아 AI를 부르지 않습니다
uv run ruff check .
```

`tests/corpus/`에 실제 논문 조각으로 만든 회귀 테스트가 있습니다.

---

## 화면 문구(i18n) 규칙

화면 문구는 **한국어 원문을 키로 쓰는 사전 방식**입니다([`i18n.py`](../src/narani/i18n.py)). 화면 언어가 한국어면 원문이 그대로 나오고, 다른 언어는 `src/narani/locales/<언어>.json`에서 찾습니다(없으면 한국어로 보입니다). 지금 카탈로그는 `en`·`ja`·`zh-Hans`·`es`입니다.

- 파이썬: `from narani.i18n import t` 후 `t("저장했습니다")`. 값이 들어가면 `t("{n}편", n=n)` — **f-string은 키가 될 수 없습니다.**
- 모듈 상수처럼 미리 정해 두는 문구는 `N_("…")`, HTML 덩어리 안에서는 `⦃문구⦄` + `i18n.html()`.
- JS 문자열 안: `md4T('문구')`, `md4T('{n}편', {n: n})`.
- `home.py`·`app.py`에서 `t`를 지역 변수 이름으로 쓰지 마세요(가져온 `t`를 가립니다).
- 에이전트 도구 설명·결과·프롬프트, CLI 출력, 로그는 LLM이나 개발자가 읽는 글이라 감싸지 않습니다.

새 문구를 감쌌으면 카탈로그를 맞춥니다. 안 하면 `tests/test_i18n.py`가 실패합니다(빈 값은 괜찮습니다).

```bash
uv run python tools/i18n_catalog.py sync en        # 코드의 문구를 en.json에 빈 항목으로 더하기 (ja·zh-Hans·es도)
uv run python tools/i18n_catalog.py fill en        # 빈 항목을 기본 모델로 초벌 번역 (비용 발생)
uv run python tools/i18n_catalog.py check          # 자리표시 불일치 검사
uv run python tools/i18n_wrap.py …                 # 화면 호출 인자를 t()로 자동 감싸기 · 남은 문구 목록
```

---

## 모바일 웹앱 배포

폰 웹앱은 정적 파일 몇 개입니다. 저장소의 **`gh-pages` 브랜치**를 GitHub Pages가 https://narani.sangwooklee.info/ 로 내보냅니다(`CNAME` 파일 포함).

웹앱(`src/narani/phone/web.py`, `ui/mobile.py`의 공용 JS·CSS)을 고친 뒤 다시 올리기:

```bash
git worktree add ../narani-gh-pages gh-pages
uv run narani phone site ../narani-gh-pages     # 웹앱 파일(+CNAME)을 그 폴더에 쓴다
cd ../narani-gh-pages && git add -A && git commit -m "웹앱 새 판" && git push
```

- 서비스 워커의 캐시 이름에 파일 내용 지문이 들어가므로, 껍데기가 바뀌면 폰이 다음에 앱을 열 때 새 판을 받습니다.
- GitHub이 도메인을 다시 설정할 때 `gh-pages`에 CNAME 커밋을 만들 수 있습니다. push가 거절되면 rebase 뒤 다시 push하세요.
- 로컬에서 웹앱 보기: `narani phone push --to <폴더>` 뒤 `narani phone preview --dir <폴더>`(기본 http://127.0.0.1:8765). `--host 0.0.0.0`이면 같은 Wi-Fi의 폰에서 열 수 있지만 http라서 서비스 워커·오프라인은 안 됩니다.

### Google 클라이언트

데스크톱(OAuth PKCE, 루프백)과 웹앱(리디렉트)은 **같은 Google Cloud 프로젝트**의 클라이언트 둘을 씁니다. `drive.file` 권한은 같은 프로젝트의 클라이언트끼리 공유되므로, 데스크톱이 올린 파일을 웹앱이 봅니다. 기본값은 앱에 실려 있고, 자기 프로젝트를 쓰려면 `config.toml`에 넣습니다.

```toml
[phone]
google_desktop_client_id = "…"
google_desktop_client_secret = "…"
google_web_client_id = "…"
```

웹 클라이언트에는 승인된 자바스크립트 원본 `https://narani.sangwooklee.info`와 리디렉션 URI `https://narani.sangwooklee.info/`를 등록합니다. 로컬 미리보기를 Drive와 함께 쓰려면(`phone preview --drive`) `http://localhost:<포트>`도 등록합니다.

### Drive 안의 파일

| 경로 | 쓰는 쪽 | 내용 |
|---|---|---|
| `Narani/index.json` | 데스크톱 | 논문 목록 |
| `Narani/papers/<id>…` | 데스크톱 | 논문 번들(렌더된 행, 줄인 그림, 인용·각주) |
| `Narani/notes/<id>.json` | 데스크톱 | 데스크톱의 하이라이트·메모 |
| `Narani/mobile/<id>.json` | 폰 | 폰에서 단 표시의 기록(추가·수정·삭제) |

파일마다 쓰는 쪽이 하나라 동시 쓰기 충돌이 없습니다. 데스크톱은 앱을 켤 때와 `phone push` 때 폰 기록을 `id`·`updated` 기준으로 `annotations.json`에 합치고, 합친 지점을 논문 폴더의 `phone.json`에 둡니다.

---

## 업데이트와 런처

- 설치 스크립트는 `uv tool install --force "narani[ui,native] @ git+https://github.com/wooogler/narani"`로 깔고 `narani app`으로 런처를 만듭니다. 설치본에는 받은 커밋이 기록됩니다(PEP 610 `direct_url.json`).
- 앱은 켤 때 GitHub 저장소 기본 브랜치(설치할 때 브랜치를 정했으면 그 브랜치)의 끝 커밋과 설치된 커밋을 비교해 다르면 알립니다. 업데이트는 떼어 낸 스크립트가 앱이 끝나기를 기다렸다가 다시 깔고, 런처를 새로 만들고, 앱을 다시 엽니다([`updater.py`](../src/narani/updater.py)).
- 저장소에서 `uv run`으로 띄운 개발본은 확인하지 않습니다(`git pull`이 업데이트입니다).
- 런처에는 파이썬 경로가 박혀 있습니다. 저장소를 옮기거나 `.venv`를 다시 만들었다면 `narani app`을 다시 실행하세요.
- 앱 아이콘은 [`tools/make_icon.py`](../tools/make_icon.py)가 도형으로 그립니다(`ui/assets/icon.png`, 폰용 `icon-maskable.png`).
- 옛 이름(md4paper)에서 넘어오는 경우 [`legacy.py`](../src/narani/legacy.py)가 처음 켤 때 `~/.config/md4paper` → `~/.config/narani`, `~/md4paper` → `~/narani`로 옮깁니다.

---

## README 스크린샷 다시 찍기

`docs/images/ko/`와 `docs/images/en/`의 그림은 화면 언어만 다른 두 벌입니다. 2026-10-26에 아래처럼 찍었습니다. 촬영 스크립트는 저장소에 넣지 않았으므로, 다시 찍을 때는 같은 방식으로 새로 만들어야 합니다.

- 실제 논문 12편을 임시 작업 폴더로 복사하고, `NARANI_CONFIG_DIR`도 임시 폴더로 둡니다(실제 설정·논문을 건드리지 않게). 임시 설정에는 AI 키와 `[ui].language`만 넣고 `[library]`는 뺍니다.
- 개발 서버를 띄우고, Playwright(Chrome, 1440×900)로 장면마다 상태를 만든 뒤 찍습니다. 홈의 작업 폴더 경로는 `~/narani/output`으로 바꿔 보입니다.
- 모바일은 `narani phone push`로 로컬 저장소에 올린 뒤 `narani phone preview`를 390×844(2배)로 찍습니다. Google 로그인이 필요한 화면은 공개 웹앱의 첫 화면만 찍습니다.
- 화면 언어는 `[ui].language`를 `ko`/`en`으로 바꿔 두 번 찍습니다. 번역 칸은 두 벌 모두 한국어입니다.
- 저장 전에 256색 PNG로 줄입니다.
- **AI 키가 보이는 화면(설정의 AI 탭)은 찍지 않습니다.**
