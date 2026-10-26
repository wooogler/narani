"""에이전트 창 — 홈·뷰어 어디서나 열리는 오른쪽 서랍 (CSS/HTML/JS 문자열 + 셸 설치).

옛 뷰어 챗봇 서랍(`chat_panel`)을 이었다. 바뀐 점:

- **셸에 한 번 설치**한다(`install`, app.py `shell()`). body 직속 `#md4-agent`라 화면을 갈아 끼워도
  (§ui/spa.py) 창·대화·진행 중인 스트림이 그대로 남는다. 화면은 `window.__md4Agent.setPaper(tok|null)`
  로 지금 논문만 알린다(`init_js`).
- 대화는 논문이 아니라 **전역 세션**이다(`/agent/sessions`, §ui/agent/routes.py). 활성 세션 id는
  브라우저(localStorage)에 두고 서버는 클라이언트별 상태를 갖지 않는다.
- 답은 SSE로 흘러온다 — 글 조각(text), tool 호출 한 줄, 화면 동작(ui_action), 승인 대기, 끝(done).
  끝에 서버가 렌더한 HTML(raw HTML 비활성 마크다운 + 인용 칩)로 말풍선을 바꿔 끼운다.
- 매 질문에 **화면 맥락**(`window.__md4Context()` — 보이는 행·선택한 글·메모 탭 등)을 싣는다.
- 인용 칩은 여러 논문을 가리킬 수 있다. 지금 논문이면 그 행으로, 다른 논문이면 그 논문을 연 뒤
  행으로 간다(가는 길은 sessionStorage에 남기고 도착한 뷰어의 setPaper가 소비한다).

- 비용 tool(재번역·용어집)이 띄운 백그라운드 작업은 그 턴 뒤에 ⏳ 진행 줄로 그리고, 미완료가 있는
  동안만 `GET /agent/sessions/{sid}/jobs`를 3초마다 불러 ✅/❌ 완료 줄로 바꾼다. ko.md 다시 그리기와
  완료 알림(토스트)은 이 창이 하지 않는다 — 작업을 띄울 때 `watch_job` 동작으로 뷰어 번역 패널에
  붙여 두면 뷰어가 자기 작업처럼 다시 그리고 알린다(다른 화면에 있으면 헤더 탭 줄이 알린다).

메모 서랍(`#md-anno-panel`)과 같은 자리·같은 시각 언어를 쓰고 둘은 번갈아 열린다. 칩이 본문을 옮기기
직전에 `window.__mdJumpBack.mark`로 읽던 자리를 쌓는다 — 알약은 뷰어 전체가 함께 쓴다(§ui/jump_back.py).

- **새 창으로**(머리의 ⧉): 같은 창 UI를 단독 페이지 `/agent?session=…&paper=…`(`standalone_page`)로
  띄운다. 앱 창 모드면 서버가 형제 웹뷰 프로세스를(`POPOUT_EVENT` → app.py가 넘긴 `popout`),
  브라우저 모드면 진짜 `<a target="_blank">` 링크가 새 탭을 연다(서버를 왕복한 window.open은 팝업
  차단에 걸린다). 같은 세션을 두 창이 함께 쓰면 마지막 저장이 이기므로 기본은 **넘겨주기** — 새 창을
  열면 이 창의 서랍은 닫는다. 단독 창(body.ag-standalone)에는 뷰어가 없어 화면 이동(칩·open_paper·
  goto_row)은 새 탭(사용자 클릭일 때) 또는 "메인 창에서 열어 주세요" 안내 줄로 대신하고, 턴이 없을 때
  `SA_POLL_MS`마다 세션을 다시 읽어 다른 창에서 쌓인 턴을 반영한다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

from narani import i18n

PANEL_ID = "md4-agent"           # body 직속 서랍 id — spa.add_body_html이 같은 id를 다시 넣지 않는다
PANEL_Z = 9000                   # 메모 서랍(#md-anno-panel)과 같은 층 — 둘은 번갈아 열린다
NAV_EVENT = "md4-agent-nav"      # JS → 서버: SPA 화면 이동 요청 (JS는 ui.sub_pages 이동을 직접 못 한다)
NOTES_REFRESH_EVENT = "md4-notes-refresh"   # JS → 서버: 에이전트가 메모 파일을 고쳤다 — 뷰어 메모 패널
GLOSSARY_REFRESH_EVENT = "md4-glossary-refresh"   # JS → 서버: 컬렉션 용어집이 바뀌었다 — 뷰어 용어집·원문 바뀜
                                            # (app.py notes_panel)이 파일을 다시 읽어 편집기를 맞춘다
WATCH_JOB_EVENT = "md4-agent-watch-job"     # JS → 서버: 에이전트가 이 논문에 번역·용어집 작업을 띄웠다 —
                                            # 뷰어 번역 패널(app.py)이 자기 작업처럼 진행 바를 붙인다
JOB_POLL_MS = 3000                          # 미완료 작업이 있는 동안 GET …/jobs 간격 (JS의 JOB_POLL_MS)
ANNO_RELOAD_API = "__mdAnnoReload"          # 뷰어 하이라이트 스크립트(app.py)가 내놓는 다시 칠하기 함수

# '새 창으로' — 단독 페이지 주소·이벤트 (JS의 POPOUT_PATH·emitEvent 이름과 한 짝, 테스트가 대조한다)
STANDALONE_PATH = "/agent"                  # app.py run()의 @ui.page — 셸(shell)이 아닌 단독 페이지
POPOUT_EVENT = "md4-agent-popout"           # JS → 서버(앱 창 모드만): 이 세션을 형제 웹뷰 창으로 띄워 달라
STANDALONE_CLASS = "ag-standalone"          # 단독 페이지 body 클래스 — 서랍을 화면 전체로 편다
SA_POLL_MS = 10000                          # 단독 창이 턴 없을 때 세션을 다시 읽는 간격 (JS의 SA_POLL_MS)
WINDOW_TITLE = i18n.N_("Narani 도우미")  # 창 제목 — 쓰는 자리에서 i18n.t
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # 쿼리로 받은 세션 id·논문 token — 스크립트에 싣기 전 거른다

# 좁은 창 대응 — JS syncLayout()의 `var AG_… = …` 값과 한 짝이다(테스트가 대조한다).
# 서랍 폭은 사용자가 끈 값(localStorage)을 그대로 쓰지 않고 열 때·창 크기가 바뀔 때마다 뷰포트에 맞춘다.
AG_MIN_PX = 320                  # 서랍 최소 폭 — 머리줄·입력창이 버티는 하한
AG_MAX_PX = 760                  # 서랍 최대 폭 (드래그 상한)
AG_KEEP_PX = 360                 # 서랍이 본문 몫으로 늘 남겨 두는 폭 — 창이 본문을 통째로 덮지 않게
AG_OVERLAY_BELOW = 760           # 남는 본문 폭이 이보다 좁으면 밀지 않고 덮는다(body.ag-overlay).
                                 # 뷰어(레일+패널+캔버스)·홈(사이드바+목록+미리보기)은 이보다 좁게
                                 # 밀리면 열이 짓눌려 카드·본문이 세로로 깨진다 → 본문은 제 폭에 두고
                                 # 서랍만 그 위에 띄운다
AG_NARROW_BELOW = 1100           # 밀기 모드에서 남는 폭이 이보다 좁으면 홈 미리보기 열을 접는다(ag-narrow).
                                 # home.py의 @media(max-width:1100px)와 같은 경계 — 그 미디어 쿼리는
                                 # 뷰포트만 봐서 서랍이 민 폭을 모른다
AG_SLIM_BELOW = 380              # 서랍 자체가 이보다 좁으면 머리줄 단추를 조인다

CSS = """
/* 오른쪽 에이전트 컬럼 — 메모 서랍과 같은 자리·같은 시각 언어 (동시에 열리지 않는다).
   본문을 덮지 않고 **오른쪽을 차지한다**: 열리면 본문 틀(.md4-body — 홈·뷰어 공통)이 그만큼
   좁아진다(body.ag-open). 위치는 fixed — 서랍이 body에 붙어 있어야 화면을 갈아 끼워도 대화와
   진행 중인 답이 살아남는다. 자리는 padding으로 비운다. 좁은 창에서는 예외로 덮는다(아래 ag-overlay).
   --ag-w는 JS(syncLayout)가 뷰포트에 맞춰 다시 쓴다 — 여기 값은 스크립트가 돌기 전 기본값이다. */
:root { --ag-w: 400px; --ag-top: 50px; }
/* --ag-top은 헤더를 실제로 재서 넣는다(아래 syncTop) — 헤더 높이가 고정이 아니라 상수로 두면
   서랍이 헤더 위로 올라와 탭 라벨을 가린다. */
#md4-agent { position: fixed; top: var(--ag-top); right: 0; bottom: 0; width: var(--ag-w); z-index: 9000;
  display: none; flex-direction: column; background: #fff; border-left: 1px solid #e6e4e0;
  color: #37352f; }
#md4-agent.open { display: flex; }
/* 대화상자(설정 등)가 뜨면 서랍은 그 밑으로 — 서랍(9000)이 Quasar 대화상자(6000)보다 높아 덮고 있었다.
   대화상자 안 드롭다운(q-menu 6000)까지 가리지 않게 대화상자를 올리지 않고 서랍을 내린다. */
body:has(.q-dialog) #md4-agent { z-index: 5990; }
body.ag-open .md4-body { padding-right: var(--ag-w); }
/* 좁은 창 — 남는 본문 폭이 모자라면(JS syncLayout이 붙인다) 밀지 않고 덮는다. 본문은 제 폭 그대로
   두고 서랍만 위에 띄우므로 뷰어·홈의 열이 짓눌리지 않는다. 떠 있다는 걸 그림자로 분명히 한다. */
body.ag-open.ag-overlay .md4-body { padding-right: 0; }
body.ag-open.ag-overlay #md4-agent { box-shadow: -12px 0 36px rgba(0,0,0,.22); }
/* 밀기 모드인데 홈 세 열이 다 안 들어갈 때 — 미리보기 열을 접는다(더 좁으면 이미 덮기로 넘어간다).
   home.py의 @media(max-width:1100px)는 뷰포트만 보므로 서랍이 민 폭은 여기서 반영한다. */
body.ag-open.ag-narrow .md4-hprev { display: none; }
/* 서랍 자체가 좁을 때 — 머리줄(세션·새 세션·새 창·닫기)이 한 줄에 버티게 단추를 조인다 */
#md4-agent.ag-slim .ag-new { padding: 3px 5px; }
/* 너비 드래그 핸들 — 목차·컬럼 핸들과 같은 감각 */
.ag-handle { position: absolute; left: 0; top: 0; bottom: 0; width: 9px; margin-left: -4px;
  cursor: col-resize; z-index: 1; }
.ag-handle::after { content: ''; position: absolute; left: 4px; top: 0; bottom: 0; width: 1px;
  background: transparent; }
.ag-handle:hover::after { background: #2383e2; width: 2px; }
.ag-head { position: relative; display: flex; align-items: center; gap: 6px; padding: 8px 10px;
  flex-wrap: nowrap; border-bottom: 1px solid #ecebe8; font-size: 13px; }
.ag-head .ag-btn { white-space: nowrap; flex: 0 0 auto; }
/* 세션 고르기 — 제목이 길면 말줄임, 남는 폭은 이것이 먼저 쓴다 */
.ag-sess { display: inline-flex; align-items: center; gap: 3px; min-width: 0; flex: 1 1 auto;
  border: none; background: none; font: inherit; font-size: 12.5px; font-weight: 600;
  color: inherit; cursor: pointer; padding: 3px 6px; border-radius: 7px; text-align: left; }
.ag-sess:hover { background: #f0efec; }
.ag-sess:disabled { cursor: default; opacity: .7; }
.ag-sess-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.ag-sess-c { flex: 0 0 auto; color: #97948d; font-size: 10px; }
/* 이름 바꾸는 중 — 제목 자리에 같은 크기의 입력 칸 (메모 탭과 같은 손짓: 더블클릭 → Enter·바깥 클릭 저장, Esc 취소) */
.ag-name-in { flex: 1 1 auto; min-width: 0; border: none; outline: none; background: #fff;
  box-shadow: 0 0 0 1.5px #2383e2; border-radius: 6px; padding: 2px 6px; font: inherit;
  font-size: 12.5px; font-weight: 600; color: inherit; }
.ag-menu .ag-name-in { font-weight: 400; font-size: 12px; }
.ag-btn { border: none; background: none; font-size: 11.5px; color: #8a8780; cursor: pointer;
  padding: 3px 6px; border-radius: 7px; }
.ag-btn:hover { background: #f0efec; }
.ag-btn:disabled { cursor: default; opacity: .5; }
.ag-x { font-size: 13px; }
/* '새 창으로' — 진짜 링크(<a>)다: 브라우저 모드에서는 이 링크가 새 탭을 연다 */
a.ag-pop { display: inline-flex; align-items: center; text-decoration: none; padding: 3px 4px; }
a.ag-pop .material-icons { font-size: 15px; }

/* 단독 창(/agent) — 셸의 헤더·본문 없이 서랍만 화면 전체로. 닫기·너비 드래그·새 창 단추는
   할 일이 없어 숨긴다('읽던 자리로' 알약은 셸에만 있다). 넓은 창에서는 대화 줄을 가운데 폭으로 모은다. */
body.ag-standalone { --ag-top: 0px; overflow: hidden; }
body.ag-standalone #md4-agent { top: 0; left: 0; width: auto; border-left: none; }
body.ag-standalone .ag-handle, body.ag-standalone .ag-x,
body.ag-standalone .ag-pop { display: none !important; }
body.ag-standalone .ag-list, body.ag-standalone .ag-foot {
  padding-left: max(11px, calc((100% - 860px) / 2)); padding-right: max(11px, calc((100% - 860px) / 2)); }
/* 단독 창의 화면 이동 안내 — 뷰어가 없어 칩·이동 요청을 여기서 할 수 없다 */
.ag-note { font-size: 11.5px; line-height: 1.55; margin: 4px 0 8px; padding: 6px 10px; border-radius: 8px;
  border: 1px solid #e6e4e0; background: #f7f6f3; color: #6b6862; }
.ag-note a { color: #2383e2; margin-left: 4px; }

/* 세션 목록 — 머리 아래로 펼친다 */
.ag-menu { position: absolute; left: 8px; right: 8px; top: calc(100% + 2px); z-index: 2;
  display: none; max-height: 60vh; overflow-y: auto; background: #fff; border: 1px solid #e6e4e0;
  border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.14); padding: 5px; font-size: 12px; }
.ag-menu.open { display: block; }
.ag-mi { display: flex; align-items: center; gap: 6px; width: 100%; border: none; background: none;
  font: inherit; color: inherit; text-align: left; padding: 6px 8px; border-radius: 7px; cursor: pointer; }
.ag-mi:hover { background: #f0efec; }
.ag-mi.on { background: #eef4fd; }
.ag-mi-t { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ag-mi-s { flex: 0 0 auto; color: #97948d; font-size: 10.5px; }
.ag-mi-s + .ag-mi-x { margin-left: -2px; }
/* 세션마다 지우기 — 줄에 올리면 보인다. 한 번 누르면 빨갛게 '지우기?'로 바뀌고 한 번 더 눌러야 지운다 */
.ag-mi-x { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 2px; border: none;
  background: none; color: #97948d; cursor: pointer; padding: 1px 3px; border-radius: 5px;
  font: inherit; font-size: 10.5px; opacity: 0; }
.ag-mi-x .material-icons { font-size: 15px; }
.ag-mi:hover .ag-mi-x, .ag-mi-x:focus-visible, .ag-mi-x.arm { opacity: 1; }
.ag-mi-x:hover { background: rgba(0,0,0,.07); color: #b3261e; }
.ag-mi-x.arm { background: #b3261e; color: #fff; }
.ag-mi-hint { padding: 5px 8px 2px; color: #97948d; font-size: 10.5px; border-top: 1px solid #ecebe8;
  margin-top: 4px; }

.ag-list { flex: 1 1 auto; overflow-y: auto; padding: 10px 11px; }
.ag-empty { padding: 26px 14px; font-size: 12px; color: #97948d; text-align: center;
  line-height: 1.75; white-space: pre-line; }
.ag-q { margin: 4px 0 8px 30px; padding: 7px 10px; border-radius: 12px 12px 3px 12px;
  background: #eef4fd; font-size: 12.5px; line-height: 1.6; white-space: pre-wrap;
  overflow-wrap: break-word; }
.ag-a { font-size: 12.5px; line-height: 1.68; overflow-wrap: break-word; }
.ag-a p, .ag-a ul, .ag-a ol, .ag-a blockquote { margin: 0 0 7px; }
/* 앱의 Tailwind 리셋이 목록 기호와 제목 굵기를 지운다 — 답 안에서는 되살린다 */
.ag-a ul, .ag-a ol { padding-left: 19px; }
.ag-a ul { list-style: disc; }
.ag-a ol { list-style: decimal; }
.ag-a ul ul { list-style: circle; }
.ag-a li { margin: 1px 0; }
.ag-a li > p { margin: 0; }
.ag-a li::marker { color: #9a968e; }
.ag-a code { background: #f4f3f0; border-radius: 4px; padding: 0 3px; font-size: 11.5px; }
.ag-a pre { background: #f4f3f0; border-radius: 8px; padding: 7px 9px; overflow-x: auto; }
.ag-a h1, .ag-a h2, .ag-a h3, .ag-a h4 { font-weight: 700; line-height: 1.4; margin: 12px 0 5px; }
.ag-a h1, .ag-a h2 { font-size: 14px; }
.ag-a h3, .ag-a h4 { font-size: 13px; }
.ag-a > :first-child { margin-top: 0; }
.ag-a blockquote { border-left: 3px solid #e0ded9; padding-left: 9px; color: #6b6862; }
.ag-a hr { border: none; border-top: 1px solid #ecebe8; margin: 10px 0; }
.ag-live { white-space: pre-wrap; }      /* 흐르는 동안은 날것 — done에서 렌더된 HTML로 바뀐다 */
.ag-turn + .ag-turn { margin-top: 10px; }
.ag-turn.ag-user + .ag-turn.ag-user, .ag-turn.ag-bot + .ag-turn.ag-user { border-top: 1px solid #f0efec;
  padding-top: 10px; }

/* tool 호출 한 줄 — 접혀 있다가 누르면 펼친다 */
.ag-tool { font-size: 10.5px; color: #8a8780; line-height: 1.5; margin: 0 0 4px; padding: 2px 6px;
  border-radius: 6px; background: #f7f6f3; cursor: pointer; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.ag-tool.full { white-space: pre-wrap; overflow-wrap: anywhere; }
.ag-tool b { color: #6b6862; font-weight: 600; }
.ag-tool .ag-tres { color: #5b8f5b; }
/* 한 답의 tool 묶음 — 최근 한 줄만, 앞선 호출은 이 단추로 펼친다 */
.ag-tools-more { display: block; border: none; background: none; padding: 0 2px; margin: 0 0 3px;
  font-size: 10.5px; color: #9a968e; cursor: pointer; }
.ag-tools-more:hover { color: #2383e2; }

/* 인용 칩 — 작은 원형 숫자 (마크업은 서버 cites.render_html) */
a.chat-cite { display: inline-block; min-width: 15px; height: 15px; padding: 0 3px; margin: 0 2px;
  border-radius: 8px; background: #2383e2; color: #fff; font-size: 9.5px; font-weight: 600;
  line-height: 15px; text-align: center; cursor: pointer; text-decoration: none;
  vertical-align: 1px; }
a.chat-cite:hover { background: #1668c4; }
a.chat-cite.ag-far { background: #fff; color: #2383e2; border: 1px solid #2383e2; line-height: 13px; }
a.chat-cite.ag-far:hover { background: #eef4fd; }
/* 독자 메모 근거 칩 — 논문 문단 칩과 한눈에 구별되게 형광펜 색으로 */
a.chat-cite-note { background: #ffe08a; color: #4a3b00; border: 1px solid #d9a406;
  line-height: 13px; }
a.chat-cite-note:hover { background: #f7cf60; }

.ag-meta { font-size: 10.5px; color: #97948d; margin: 3px 0 4px; }
.ag-err { border: 1px solid rgba(179,38,30,.35); background: rgba(179,38,30,.08); color: #b3261e;
  border-radius: 10px; padding: 8px 10px; font-size: 11.5px; line-height: 1.6; margin: 4px 0 8px; }
/* 승인 카드 — 쓰기 tool이 실행 전에 묻는다. 답을 보내면 결과(승인됨/거절됨/건너뜀)로 잠긴다(.done) */
.ag-appr { border: 1px solid #e8c66a; background: #fffaf0; border-radius: 10px; padding: 8px 10px;
  font-size: 11.5px; line-height: 1.55; margin: 4px 0 8px; }
.ag-appr.done { border-color: #e6e4e0; background: #f9f8f6; color: #6b6862; }
.ag-appr-h { font-weight: 600; margin-bottom: 4px; }
.ag-appr-c { padding: 3px 0; }
.ag-appr-c + .ag-appr-c { border-top: 1px dashed #ecdcae; }
.ag-appr.done .ag-appr-c + .ag-appr-c { border-top-color: #ecebe8; }
.ag-appr-r { display: flex; align-items: center; gap: 6px; }
.ag-appr-i { flex: 0 0 auto; font-size: 15px; color: #a07b10; }
.ag-appr.done .ag-appr-i { color: #97948d; }
.ag-appr-d { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
.ag-appr button { border: 1px solid #e0ded9; background: #fff; border-radius: 7px; font: inherit;
  font-size: 11px; padding: 2px 8px; cursor: pointer; color: inherit; flex: 0 0 auto; }
.ag-appr button:disabled { cursor: default; opacity: .55; }
.ag-appr button.on { border-color: #2383e2; background: #eef4fd; color: #1668c4; }
.ag-appr button.ag-appr-tg { border: none; background: none; padding: 0 2px; color: #97948d;
  font-size: 10.5px; }
.ag-appr button.ag-appr-tg:hover { color: #6b6862; }
.ag-appr-args { display: none; margin: 4px 0 2px 21px; padding: 5px 7px; border-radius: 6px;
  background: rgba(0,0,0,.04); font-size: 10.5px; white-space: pre-wrap; overflow-wrap: anywhere;
  max-height: 180px; overflow-y: auto; }
.ag-appr-c.open .ag-appr-args { display: block; }
.ag-appr-s { flex: 0 0 auto; font-size: 10.5px; font-weight: 600; padding: 1px 7px; border-radius: 9px; }
.ag-appr-s.ok { background: #e3f1e3; color: #2f6f2f; }
.ag-appr-s.no { background: #f6e0de; color: #9c2a20; }
.ag-appr-s.skip { background: #ecebe8; color: #6b6862; }
.ag-appr-all { display: flex; justify-content: flex-end; gap: 6px; margin-top: 6px; padding-top: 6px;
  border-top: 1px solid #ecdcae; }
/* 백그라운드 작업 줄 — 비용 tool이 띄운 번역·용어집. 도는 동안 ⏳, 끝나면 ✅/❌ */
.ag-job { font-size: 11.5px; line-height: 1.55; margin: 2px 0 8px; padding: 5px 9px; border-radius: 8px;
  border: 1px solid #dfe8f3; background: #f5f9fe; color: #3d5a7a; overflow-wrap: anywhere; }
.ag-job.ok { border-color: #d3e7d3; background: #f3faf3; color: #2f6f2f; }
.ag-job.fail { border-color: rgba(179,38,30,.3); background: rgba(179,38,30,.06); color: #b3261e; }
/* 비용 상한 경고 — 턴 시작 전에 서버가 막았다. '그래도 계속'은 같은 요청을 override로 다시 보낸다 */
.ag-cap { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; border: 1px solid #e8c66a;
  background: #fffaf0; border-radius: 10px; padding: 7px 10px; font-size: 11.5px; line-height: 1.55;
  margin: 4px 0 8px; }
.ag-cap-t { flex: 1 1 180px; min-width: 0; }
.ag-cap button { border: 1px solid #e0ded9; background: #fff; border-radius: 7px; font: inherit;
  font-size: 11px; padding: 2px 8px; cursor: pointer; color: inherit; }
.ag-cap button.ag-cap-go { border-color: #2383e2; background: #eef4fd; color: #1668c4; }

.ag-foot { position: relative; border-top: 1px solid #ecebe8; padding: 8px 9px; }
/* @ 맥락 — 입력창 위 칩 줄(이번 질문에 붙여 보낼 것)과 '@'를 치면 뜨는 고르기 목록 */
.ag-ctx { display: none; flex-wrap: wrap; gap: 4px; margin: 0 0 6px; }
.ag-ctx.on { display: flex; }
.ag-chip { display: inline-flex; align-items: center; gap: 3px; max-width: 100%; height: 22px;
  padding: 0 3px 0 6px; border-radius: 6px; background: #eef4fd; color: #1f5fa8; font-size: 11px;
  border: 1px solid #d5e5f8; }
.ag-chip .material-icons { font-size: 13px; opacity: .8; }
.ag-chip-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; max-width: 220px; }
.ag-chip-x { border: none; background: none; color: inherit; cursor: pointer; font-size: 10px;
  padding: 1px 3px; border-radius: 4px; opacity: .7; }
.ag-chip-x:hover { opacity: 1; background: rgba(35,131,226,.14); }
.ag-q-ctx { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.ag-q-ctx .ag-chip { height: 20px; padding-right: 6px; }
.ag-at-btn { flex: 0 0 26px; height: 30px; border: none; background: none; color: #8a8780;
  font: inherit; font-size: 15px; font-weight: 600; cursor: pointer; border-radius: 8px; padding: 0; }
.ag-at-btn:hover { background: #f0efec; color: #2383e2; }
.ag-at-btn:disabled { opacity: .4; cursor: default; }
.ag-at { position: absolute; left: 9px; right: 9px; bottom: calc(100% - 4px); z-index: 3; display: none;
  max-height: min(340px, 50vh); overflow-y: auto; background: #fff; border: 1px solid #e6e4e0;
  border-radius: 10px; box-shadow: 0 -6px 24px rgba(0,0,0,.12); padding: 4px; font-size: 12px; }
.ag-at.open { display: block; }
.ag-at-g { padding: 6px 8px 2px; color: #97948d; font-size: 10.5px; font-weight: 600; }
.ag-at-i { display: flex; align-items: center; gap: 7px; width: 100%; border: none; background: none;
  font: inherit; color: inherit; text-align: left; padding: 5px 8px; border-radius: 7px; cursor: pointer; }
.ag-at-i .material-icons { flex: 0 0 auto; font-size: 15px; color: #97948d; }
.ag-at-i.on { background: #eef4fd; }
.ag-at-i.on .material-icons { color: #2383e2; }
.ag-at-t { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ag-at-s { flex: 0 0 auto; color: #97948d; font-size: 10.5px; max-width: 45%; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.ag-at-none { padding: 8px; color: #97948d; }
.ag-sel { display: none; align-items: center; gap: 5px; font-size: 10.5px; color: #8a8780;
  margin: 0 0 6px; }
.ag-sel.on { display: flex; }
.ag-sel-t { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ag-row { display: flex; gap: 6px; align-items: flex-end; }
.ag-input { flex: 1 1 auto; resize: none; max-height: 120px; box-sizing: border-box;
  border: 1px solid #e0ded9; border-radius: 10px; padding: 7px 9px; font: inherit;
  font-size: 12.5px; line-height: 1.5; background: #fff; color: inherit; outline: none; }
.ag-input:focus { border-color: #2383e2; }
.ag-input:disabled { background: #f4f3f0; color: #97948d; }
.ag-send { flex: 0 0 30px; width: 30px; height: 30px; border: none; border-radius: 50%;
  background: #2383e2; color: #fff; font-size: 13px; cursor: pointer; }
.ag-send:hover { background: #1668c4; }
.ag-send:disabled { background: #d8d6d1; cursor: default; }

.ag-wait { display: flex; align-items: center; gap: 7px; font-size: 11.5px; color: #97948d;
  margin: 0 0 9px; }
.ag-dots span { display: inline-block; width: 5px; height: 5px; margin-right: 3px;
  border-radius: 50%; background: #a9a69f; animation: mdAgentDot 1.1s infinite ease-in-out; }
.ag-dots span:nth-child(2) { animation-delay: .18s; }
.ag-dots span:nth-child(3) { animation-delay: .36s; }
@keyframes mdAgentDot { 0%, 60%, 100% { opacity: .25; } 30% { opacity: 1; } }

/* 인용 칩 클릭 → 본문 해당 행 깜빡임 (원문·번역 셀 모두) */
.chat-cite-flash { animation: mdChatFlash 1.3s ease-out; }
@keyframes mdChatFlash { 0%, 55% { outline: 2px solid #2383e2; outline-offset: 2px; }
  100% { outline-color: transparent; } }

@media (prefers-color-scheme: dark) {
  #md4-agent { background: #1f1f1f; border-color: #3a3a3a; color: #d4d4d4; }
  body.ag-open.ag-overlay #md4-agent { box-shadow: -12px 0 36px rgba(0,0,0,.6); }
  .ag-head, .ag-foot { border-color: #2c2c2c; }
  .ag-chip { background: #1e2733; border-color: #2f4560; color: #8fbcf0; }
  .ag-at { background: #262626; border-color: #3a3a3a; box-shadow: 0 -6px 24px rgba(0,0,0,.5); }
  .ag-at-i.on { background: #24303d; }
  .ag-at-btn:hover { background: #2c2c2c; }
  .ag-a code, .ag-a pre, .ag-tool { background: #2c2c2c; color: #b8b8b8; }
  .ag-a blockquote { border-left-color: #3a3a3a; color: #9a9a9a; }
  .ag-a hr { border-top-color: #333; }
  .ag-tool b { color: #d0d0d0; }
  .ag-btn:hover, .ag-sess:hover, .ag-mi:hover { background: #2c2c2c; }
  .ag-menu { background: #262626; border-color: #3a3a3a; box-shadow: 0 8px 24px rgba(0,0,0,.5); }
  .ag-mi.on, .ag-q { background: #24303d; }
  .ag-msep { background: #333; }
  .ag-empty, .ag-meta, .ag-sess-c, .ag-mi-s, .ag-sel, .ag-mi-hint { color: #8f8f8f; }
  .ag-input { background: #262626; border-color: #3a3a3a; }
  .ag-name-in { background: #262626; }
  .ag-mi-hint { border-top-color: #333; }
  .ag-input:disabled { background: #232323; color: #7d7d7d; }
  .ag-turn.ag-user + .ag-turn.ag-user, .ag-turn.ag-bot + .ag-turn.ag-user { border-color: #2c2c2c; }
  .ag-appr, .ag-cap { background: #2b2618; border-color: #6b5a24; }
  .ag-appr.done { background: #242424; border-color: #3a3a3a; color: #a0a0a0; }
  .ag-appr-c + .ag-appr-c, .ag-appr-all { border-color: #4a3f1c; }
  .ag-appr.done .ag-appr-c + .ag-appr-c { border-top-color: #333; }
  .ag-appr-i { color: #d9b44a; }
  .ag-appr button, .ag-cap button { background: #262626; border-color: #3a3a3a; }
  .ag-appr button.on, .ag-cap button.ag-cap-go { background: #24303d; border-color: #5a96e6;
    color: #8fbcf0; }
  .ag-appr button.ag-appr-tg { background: none; color: #8f8f8f; }
  .ag-appr-args { background: rgba(255,255,255,.06); }
  .ag-appr-s.ok { background: #1f3322; color: #8fd18f; }
  .ag-appr-s.no { background: #3a2220; color: #f0a39b; }
  .ag-appr-s.skip { background: #2c2c2c; color: #a0a0a0; }
  .ag-job { background: #1e2733; border-color: #2f4560; color: #8fbcf0; }
  .ag-job.ok { background: #1f2a20; border-color: #34503a; color: #8fd18f; }
  .ag-job.fail { background: #2e1f1e; border-color: #5a2f2b; color: #f0a39b; }
  .ag-note { background: #262626; border-color: #3a3a3a; color: #a0a0a0; }
  .ag-note a { color: #5a96e6; }
  a.chat-cite.ag-far { background: #1f1f1f; color: #5a96e6; border-color: #5a96e6; }
}
"""

# 서랍 마크업. body에 고정돼 있어 화면을 갈아 끼워도 대화·진행 중인 답이 그대로 남는다.
HTML = """
<div id="md4-agent" role="complementary" aria-label="⦃에이전트⦄">
  <div class="ag-handle" title="⦃드래그해서 너비 조절⦄"></div>
  <div class="ag-head">
    <button type="button" class="ag-sess" title="⦃세션 목록 — 더블클릭하면 이름 바꾸기⦄"><span
      class="ag-sess-t">⦃에이전트⦄</span><span class="ag-sess-c">&#9662;</span></button>
    <button type="button" class="ag-btn ag-new" title="⦃새 세션⦄">+ ⦃새 세션⦄</button>
    <a class="ag-btn ag-pop" href="/agent" target="_blank" rel="noopener"
      title="⦃새 창으로 열기 — 이 창의 서랍은 닫힙니다⦄"><i class="material-icons"
      aria-hidden="true">open_in_new</i></a>
    <button type="button" class="ag-btn ag-x" title="⦃닫기 (Esc)⦄">&#10005;</button>
    <div class="ag-menu" role="menu"></div>
  </div>
  <div class="ag-list"></div>
  <div class="ag-foot">
    <div class="ag-sel"><span>⦃선택한 글 함께 보냄:⦄</span><span class="ag-sel-t"></span>
      <button type="button" class="ag-btn ag-sel-x" title="⦃선택한 글 빼기⦄">&#10005;</button></div>
    <div class="ag-ctx" aria-label="⦃이 질문에 붙일 맥락⦄"></div>
    <div class="ag-at" role="listbox" aria-label="⦃맥락 고르기⦄"></div>
    <div class="ag-row">
      <button type="button" class="ag-at-btn" title="⦃맥락 넣기 — 논문·섹션·메모 탭·컬렉션 (@)⦄"
        aria-label="⦃맥락 넣기⦄">@</button>
      <textarea class="ag-input" rows="1"
        placeholder="⦃무엇이든 물어보세요 — @로 맥락 넣기 (Shift+Enter 줄바꿈)⦄"></textarea>
      <button type="button" class="ag-send" title="⦃전송 (Enter)⦄">&#10148;</button>
    </div>
  </div>
</div>
"""

JS = r"""
(function(){
  if (window.__md4AgentInit) return; window.__md4AgentInit = true;
  var panel = document.getElementById('md4-agent');
  if (!panel) return;
  var list = panel.querySelector('.ag-list');
  var input = panel.querySelector('.ag-input');
  var sendBtn = panel.querySelector('.ag-send');
  var sessBtn = panel.querySelector('.ag-sess');
  var sessT = panel.querySelector('.ag-sess-t');
  var menu = panel.querySelector('.ag-menu');
  var newBtn = panel.querySelector('.ag-new');
  var selBar = panel.querySelector('.ag-sel');
  var selT = panel.querySelector('.ag-sel-t');
  var popBtn = panel.querySelector('.ag-pop');
  var ctxBar = panel.querySelector('.ag-ctx'), atBox = panel.querySelector('.ag-at');
  var atBtn = panel.querySelector('.ag-at-btn');
  // @ 맥락 — 다음 질문에 붙여 보낼 것(atPicked)과 고르기 목록 상태. 보내면 비운다(그 질문의 것이다)
  var atPicked = [], atItems = [], atSel = 0, atFrom = -1, atTimer = null, atSeq = 0;
  // 단독 창(/agent)이면 서버가 이 스크립트 앞에 {session, paper, title}을 심는다 — 셸에서는 null
  var SA = window.__md4AgentStandalone || null;
  var POPOUT_PATH = '/agent', SA_POLL_MS = 10000;

  // 브라우저에 두는 것 — 활성 세션·서랍 폭·열림 여부(localStorage), 다른 논문으로 가는 행(sessionStorage)
  var LS_SESSION = 'md4agent.session', LS_W = 'md4agent.w', LS_OPEN = 'md4agent.open';
  var SS_GOTO = 'md4agent.goto';
  function lsGet(k){ try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v){
    try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) {}
  }

  // 지금 화면의 논문 토큰 (홈이면 null). 단독 창은 띄운 쪽이 넘겨 준 논문을 끝까지 쓴다
  var paper = SA ? (SA.paper || null) : (window.__md4AgentTok || null);
  var sid = null, sess = null, sessList = [], turns = [], pending = null;
  var live = null, busy = false, errMsg = null, loaded = false, menuMode = '';
  var toolsByTurn = {};      // 턴 id → 그 답을 만들며 부른 tool 줄 (이 창이 떠 있는 동안만)
  var decisions = {};        // 승인 카드 — tool_call id → true/false (모든 호출에 답이 모이면 보낸다)
  var apprByTurn = {};       // 턴 id → 그 턴 뒤에서 끝난 승인 카드들 [{calls, decisions, skipped}] (잠김)
  var jobs = [];             // 세션의 백그라운드 작업 기록 (서버 sessions.jobs 그대로)
  var jobTimer = null;       // 미완료 작업 폴링 타이머 — 미완료가 없으면 멈춘다
  var JOB_POLL_MS = 3000;
  var capRetry = null;       // 비용 상한으로 막힌 요청 {body, msg} — '그래도 계속'이 override를 더해 다시 보낸다
  var notice = null;         // 단독 창의 화면 이동 안내 {url} — 다음 질문을 보내면 걷힌다
  var PH_IDLE = input.placeholder;
  var PH_WAIT = md4T('승인을 기다리는 중 — 그냥 보내면 건너뜁니다');
  var models = [], modelInfo = {ready: true, reason: '', provider: '', model: ''};

  function esc(s){ var d = document.createElement('div'); d.textContent = s == null ? '' : s;
    return d.innerHTML; }
  function onReview(){ return location.pathname.indexOf('/review') === 0; }
  function api(method, url, body){
    var opt = {method: method, headers: {}};
    if (body !== undefined){
      opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body);
    }
    return fetch(url, opt).then(function(r){
      return r.json().then(function(j){
        if (!r.ok) throw new Error(j.error || j.detail || ('HTTP ' + r.status));
        return j;
      }, function(){ if (!r.ok) throw new Error('HTTP ' + r.status); return {}; });
    });
  }
  // SPA 이동은 서버가 한다(ui.navigate.to) — 셸의 ui.on('md4-agent-nav')가 받는다
  // gesture: 사용자가 방금 누른 칩에서 왔다 (단독 창에서만 뜻이 있다)
  function nav(url, gesture){
    if (!url) return;
    if (SA){
      // 단독 창에는 뷰어가 없다(그 이벤트를 받을 셸도 없다). 브라우저에서 칩을 눌렀으면 새 탭으로 연다.
      // 답이 흐르다 온 이동(open_paper·goto_row)은 열지 않고 안내 줄(+ 진짜 링크)만 — 팝업 차단 여부가
      // 브라우저·타이밍마다 달라 열렸다 말았다 하는 것보다 낫다. 앱 창은 window.open을 쓰지 않는다.
      var w = (gesture && !window.__md4AgentNative) ? window.open(url, '_blank') : null;
      if (!w){ notice = {url: url}; render(); }
      return;
    }
    if (typeof emitEvent === 'function') emitEvent('md4-agent-nav', {url: url});
    else location.href = url;
  }

  // ---- 화면 맥락 스냅숏 (매 질문에 싣는다 — 서버는 run 도중 브라우저에 물을 수 없다) ----
  var lastSel = null;          // 서랍 밖에서 마지막으로 고른 글 — 입력창을 누르면 선택이 풀리므로 붙들어 둔다
  function selNow(){
    var s = window.getSelection && window.getSelection();
    if (!s || !s.rangeCount || s.isCollapsed) return null;
    var n = s.anchorNode, el = n && (n.nodeType === 1 ? n : n.parentElement);
    if (!el || panel.contains(el)) return null;
    var q = String(s.toString() || '').replace(/\s+/g, ' ').trim();
    if (!q) return null;
    var sc = el.closest('.anno-scope') || el.closest('[data-side]');
    var rc = el.closest('[data-row]');
    var row = rc ? parseInt(rc.getAttribute('data-row'), 10) : null;
    return {side: sc ? (sc.getAttribute('data-side') || null) : null,
            row: isNaN(row) ? null : row, quote: q.slice(0, 300)};
  }
  function paintSel(){
    selBar.classList.toggle('on', !!lastSel);
    selT.textContent = lastSel ? '“' + lastSel.quote.slice(0, 80) + '”' : '';
  }
  document.addEventListener('selectionchange', function(){
    var got = selNow();
    if (got){ lastSel = got; paintSel(); return; }
    var a = document.activeElement;
    // 서랍 안(입력창·단추)으로 초점이 옮겨 가며 풀린 선택은 지키고, 본문을 다시 눌러 푼 선택만 지운다
    if (a && panel.contains(a)) return;
    var s = window.getSelection && window.getSelection();
    var n = s && s.anchorNode, el = n && (n.nodeType === 1 ? n : n.parentElement);
    if (el && panel.contains(el)) return;
    if (lastSel){ lastSel = null; paintSel(); }
  });
  panel.querySelector('.ag-sel-x').addEventListener('click', function(){ lastSel = null; paintSel(); });

  function paperTitle(){
    var t = document.querySelector('.md4-tab.on .md4-tab-t');
    return ((t && t.textContent) || document.title || '').trim().slice(0, 200);
  }
  function visibleRows(){
    var g = gridNow(), out = [];
    if (!g) return out;
    var gr = g.getBoundingClientRect();
    var els = g.querySelectorAll('.sbs-cell[data-row]');
    if (!els.length) els = g.querySelectorAll('[data-row]');
    for (var i = 0; i < els.length && out.length < 60; i++){
      var r = els[i].getBoundingClientRect();
      if (r.width <= 0 || r.bottom <= gr.top || r.top >= gr.bottom) continue;
      var n = parseInt(els[i].getAttribute('data-row'), 10);
      if (!isNaN(n) && out.indexOf(n) < 0) out.push(n);
    }
    return out;
  }
  function langNow(){
    var g = document.querySelector('.sbs-grid');
    if (!g) return null;
    if (g.classList.contains('sbs-two')) return 'both';
    var cs = g.querySelectorAll('.sbs-cell[data-side]');
    for (var i = 0; i < cs.length && i < 40; i++)
      if (cs[i].getBoundingClientRect().width > 0) return cs[i].getAttribute('data-side');
    return null;
  }
  function noteTabs(){
    var ts = document.querySelectorAll('.md4-notetab[data-tab]');
    if (!ts.length) return null;
    var open = [], focus = null;
    for (var i = 0; i < ts.length; i++){
      var name = ts[i].getAttribute('data-tab');
      open.push(name);
      if (ts[i].classList.contains('on')) focus = name;
    }
    return {open: open, focus: focus};
  }
  function leftPanel(){
    var b = document.querySelector('.md4-railbtn.on');
    if (!b) return null;
    var l = b.querySelector('div');            // 아이콘(<i>, 글자가 아이콘 이름이다) 다음의 라벨
    return ((l ? l.textContent : '') || '').trim() || null;
  }
  function projectNow(){
    var p = document.querySelector('.md4-prow.on .md4-prow-name');
    return p ? {id: null, name: (p.textContent || '').trim()} : null;
  }
  function ctxNow(){
    if (SA)       // 단독 창 — 화면(DOM) 맥락이 없다. 넘겨받은 논문만 싣고 제목은 서버가 채운다
      return {screen: 'window', paper: paper ? {token: paper, title: SA.title || null} : null,
              lang: null, visible_rows: [], top_row: null, selection: lastSel || selNow(),
              note_tabs: null, left_panel: null, project: null, mentions: atPicked.slice()};
    var review = onReview();
    var rows = review ? visibleRows() : [];
    return {
      screen: review ? 'review' : 'home',
      paper: (review && paper) ? {token: paper, title: paperTitle()} : null,
      lang: review ? langNow() : null,
      visible_rows: rows,
      top_row: rows.length ? rows[0] : null,
      selection: lastSel || selNow(),
      note_tabs: review ? noteTabs() : null,
      left_panel: review ? leftPanel() : null,
      project: review ? null : projectNow(),
      mentions: atPicked.slice()             // 입력창에서 @로 집어넣은 맥락 (§ui/agent/context.py Mention)
    };
  }
  window.__md4Context = ctxNow;

  // ---- 본문(뷰어 정렬 행)으로 이동 + 깜빡임 ----
  function cellsOf(row){
    var els = document.querySelectorAll('.sbs-grid .sbs-cell[data-row="' + row + '"]');
    if (!els.length) els = document.querySelectorAll('[data-row="' + row + '"]');
    var out = [];
    for (var i = 0; i < els.length; i++){
      if (panel.contains(els[i])) continue;
      out.push(els[i]);
      // 정렬 그리드는 첫 표시 셀에만 data-row가 붙는다 — 짝 셀(번역)도 함께 깜빡이게
      var sib = els[i].nextElementSibling;
      if (sib && sib.classList.contains('sbs-cell') && !sib.hasAttribute('data-row')) out.push(sib);
    }
    return out;
  }
  function flash(els, cls){
    els.forEach(function(el){
      el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
      setTimeout(function(){ el.classList.remove(cls); }, 1400);
    });
  }
  function gotoRow(row){
    var els = cellsOf(row);
    if (!els.length) return false;
    jbMark(els[0]);        // 스크롤을 시작하기 **전에** 읽던 자리를 쌓는다(중간값을 집으면 밀린다)
    els[0].scrollIntoView({behavior: 'smooth', block: 'center'});
    flash(els, 'chat-cite-flash');
    return true;
  }
  // 메모 근거 → 그 하이라이트(mark)로. 뷰어의 md-anno-flash를 그대로 재사용한다.
  function marksOf(id){
    var ms = document.querySelectorAll('mark.md-anno'), out = [];
    for (var i = 0; i < ms.length; i++){
      if ((ms[i].getAttribute('data-ids') || '').split(' ').indexOf(id) >= 0) out.push(ms[i]);
    }
    return out;
  }
  function gotoNote(id, row){
    var ms = marksOf(id);
    if (!ms.length) return gotoRow(row);      // 아직 안 칠해졌거나 위치를 못 찾은 메모
    jbMark(ms[0]);                            // gotoRow로 빠진 경우엔 거기서 쌓는다(이중으로 쌓지 않음)
    ms[0].scrollIntoView({behavior: 'smooth', block: 'center'});
    flash(ms, 'md-anno-flash');
    return true;
  }
  function gridNow(){                    // 뷰어 정렬 그리드 — 같은 클래스의 다른 상자(data-row 없음)는 거른다
    var all = document.querySelectorAll('.sbs-grid, .vpane');
    for (var i = 0; i < all.length; i++){
      var r = all[i].getBoundingClientRect();
      if (r.width < 40 || r.height < 40) continue;
      if (all[i].matches('[data-row]') || all[i].querySelector('[data-row]')) return all[i];
    }
    return null;
  }
  function jbMark(el){                   // 읽던 자리 쌓기 — 단독 창(/agent)에는 알약이 없다
    var jb = window.__mdJumpBack;
    return !!(jb && jb.mark(el, 'center'));
  }
  // 다른 논문의 행 — 그 논문을 연 뒤 도착한 뷰어(setPaper)가 이어서 간다
  function gotoElsewhere(tok, row, id, url, gesture){
    if (!url) return false;
    try {
      sessionStorage.setItem(SS_GOTO, JSON.stringify({token: tok, row: row, id: id || null,
                                                      at: Date.now()}));
    } catch (e) {}
    nav(url, gesture);
    return true;
  }
  function consumeGoto(){
    var g = null;
    try { g = JSON.parse(sessionStorage.getItem(SS_GOTO) || 'null'); } catch (e) {}
    if (!g) return;
    if (Date.now() - (g.at || 0) > 30000){ try { sessionStorage.removeItem(SS_GOTO); } catch (e) {} return; }
    if (!paper || g.token !== paper) return;     // 아직 그 논문에 닿지 않았다
    try { sessionStorage.removeItem(SS_GOTO); } catch (e) {}
    var tries = 0;
    (function wait(){
      if (paper !== g.token) return;              // 기다리는 사이 다른 화면으로 갔다
      if (!cellsOf(g.row).length && ++tries < 60) return setTimeout(wait, 150);
      // 읽던 자리 복원(scroll_memory)이 상자가 붙는 순간 돈다 — 그 뒤에 움직여야 덮이지 않는다
      setTimeout(function(){ if (g.id) gotoNote(g.id, g.row); else gotoRow(g.row); }, 350);
    })();
  }

  // ---- 모델 (에이전트 전용 — 번역 설정과 별개). 고르는 곳은 설정 창(AI 탭)이고 여기서는 준비 상태만 안다 ----
  function modelLabel(){                    // 서버 model_label과 같은 '제공사:모델'
    return modelInfo.model ? (modelInfo.provider ? modelInfo.provider + ':' : '') + modelInfo.model : '';
  }
  function takeModels(j){
    models = j.models || models;
    modelInfo = {ready: !!j.ready, reason: j.reason || '', provider: j.provider || '',
                 model: j.model || ''};
  }
  function loadModels(){
    return api('GET', '/agent/models').then(function(j){ takeModels(j); render(); })
      .catch(function(){});
  }

  // ---- 세션 ----
  function shortTime(ts){
    if (!ts) return '';
    var d = new Date(ts * 1000), now = new Date();
    if (d.toDateString() === now.toDateString())
      return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    return (d.getMonth() + 1) + '/' + d.getDate();
  }
  function setSession(d){
    if (d.id !== sid){ apprByTurn = {}; capRetry = null; }
    sess = d; sid = d.id; lsSet(LS_SESSION, sid);
    turns = d.turns || []; pending = d.pending || null; decisions = {};   // 다시 열면 대기 카드도 되살아난다
    live = null; errMsg = null;
    setJobs(d.jobs || []);
    render();
  }

  // ---- 백그라운드 작업 (비용 tool) ----
  // 서버가 레지스트리와 대조해 채운 기록을 받아 그리기만 한다. 미완료가 있는 동안만 폴링한다.
  function jobsPending(){ return jobs.some(function(j){ return !j.done; }); }
  function setJobs(js){
    jobs = js || [];
    if (jobsPending()){ if (!jobTimer) jobTimer = setTimeout(pollJobs, JOB_POLL_MS); }
    else if (jobTimer){ clearTimeout(jobTimer); jobTimer = null; }
  }
  function pollJobs(){
    jobTimer = null;
    if (!sid || !jobsPending()) return;
    var at = sid;
    api('GET', '/agent/sessions/' + encodeURIComponent(at) + '/jobs').then(function(j){
      if (at !== sid) return;
      var before = {};
      jobs.forEach(function(x){ before[x.id] = x.done; });
      if (sess && j.usage) sess.usage = j.usage;          // 끝난 작업 비용이 세션 누적에 들어온다
      setJobs(j.jobs || []);
      var changed = jobs.some(function(x){ return x.done !== before[x.id]; });
      if (changed){ if (!live) render(); else renderHead(); }   // 답이 흐르는 중이면 머리(비용)만
      else if (!live) jobs.forEach(function(x){ if (!x.done) paintJob(x); });  // 진행 숫자만
    }).catch(function(){ if (!jobTimer && at === sid) jobTimer = setTimeout(pollJobs, JOB_POLL_MS * 2); });
  }
  // 완료 줄에 작업 비용을 붙이지 않는다 — 끝난 작업 비용은 세션 누적(머리)에 더해져 거기서만 보인다
  function jobText(j){
    var gl = j.kind === 'glossary';
    if (!j.done){
      var p = j.progress || [0, 0], prog = p[1] ? ' ' + p[0] + '/' + p[1] : '';
      return '⏳ ' + (gl ? md4T('용어집 만드는 중…') : md4T('번역 중 — {detail}…', {detail: j.detail || ''})) + prog;
    }
    var err = j.error || md4T('알 수 없는 오류');
    if (j.status !== 'done') return '❌ ' + (gl ? md4T('용어집 실패: {e}', {e: err}) : md4T('번역 실패: {e}', {e: err}));
    var r = j.result || {};
    if (gl) return '✅ ' + md4T('용어집 완료: {n}개 항목', {n: r.entries || 0});
    return '✅ ' + md4T('번역 완료: {n}개 섹션', {n: r.sections || 0})
      + (r.failed ? ' · ' + md4T('{n}개 구간은 영어 유지(구조 검증 실패)', {n: r.failed}) : '');
  }
  function jobNode(j){
    var d = document.createElement('div');
    d.className = 'ag-job' + (j.done ? (j.status === 'done' ? ' ok' : ' fail') : '');
    d.setAttribute('data-job', j.id);
    d.textContent = jobText(j);
    // 작업 message는 쓰지 않는다 — jobtools가 '(≈$…)' 비용을 붙여 둔 뷰어 토스트용 문구다
    d.title = (j.title ? '‘' + j.title + '’ — ' : '') + (j.label || '')
      + (j.model ? '\n' + md4T('모델 {m}', {m: j.model}) : '');
    return d;
  }
  function paintJob(j){
    var el = list.querySelector('.ag-job[data-job="' + j.id + '"]');
    if (el) el.textContent = jobText(j);
  }
  function jobsAfter(id, used){            // 그 턴 뒤에 그릴 작업 줄
    var frag = document.createDocumentFragment();
    jobs.forEach(function(j){
      if (j.turn_id === id && !used[j.id]){ used[j.id] = true; frag.appendChild(jobNode(j)); }
    });
    return frag;
  }
  function loadList(){
    return api('GET', '/agent/sessions').then(function(j){ sessList = j.sessions || []; return sessList; });
  }
  function openSession(id){
    return api('GET', '/agent/sessions/' + encodeURIComponent(id)).then(setSession);
  }
  function newSession(){
    if (busy) return Promise.resolve();
    closeMenu();
    return api('POST', '/agent/sessions', {}).then(function(d){
      setSession(d);
      return loadList();
    }).then(function(){ renderHead(); setTimeout(function(){ input.focus(); }, 30); })
      .catch(function(e){ errMsg = md4T('세션을 만들지 못했습니다: {e}', {e: e.message}); render(); });
  }
  // 활성 세션을 정한다 — 브라우저가 기억한 것 → 목록 첫 번째(최근) → 없으면 새로 만든다
  function ensureSession(){
    loaded = true;
    return loadList().then(function(ss){
      var want = sid || (SA && SA.session) || lsGet(LS_SESSION), hit = null;
      for (var i = 0; i < ss.length; i++) if (ss[i].id === want) hit = ss[i];
      hit = hit || ss[0];
      if (hit) return openSession(hit.id).catch(function(){ return newSession(); });
      return newSession();
    }).catch(function(e){ errMsg = md4T('세션 목록을 읽지 못했습니다: {e}', {e: e.message}); render(); });
  }
  function refreshSession(){
    if (!sid) return Promise.resolve();
    var keep = sid;
    return openSession(keep).then(loadList).then(renderHead).catch(function(){});
  }

  // 세션 메뉴 — 목록뿐이다. 이름은 메모 탭처럼 제목을 더블클릭해 그 자리에서 바꾸고(머리줄·목록 줄 모두),
  // 지우기는 줄마다 휴지통(한 번 더 눌러야 지운다). 앱 창(웹뷰)에서 prompt/confirm이 막힐 수 있어 다 제자리에서 한다.
  function closeMenu(){ menu.classList.remove('open'); menuMode = ''; }
  function sessTitle(id){
    if (sess && sess.id === id && sess.title) return sess.title;
    for (var i = 0; i < sessList.length; i++) if (sessList[i].id === id) return sessList[i].title || '';
    return '';
  }
  // 글자 자리(span)를 같은 크기의 입력 칸으로 바꾼다 — Enter·바깥 클릭 = 저장, Esc = 그대로
  function renameInline(span, id, after){
    if (!span || !id || busy) return;
    var inp = document.createElement('input');
    inp.className = 'ag-name-in'; inp.value = sessTitle(id); inp.placeholder = md4T('세션 이름');
    inp.setAttribute('aria-label', md4T('세션 이름'));
    var done = false;
    function finish(save){
      if (done) return; done = true;
      var t = inp.value.trim(), old = sessTitle(id);
      inp.replaceWith(span);
      if (!save || !t || t === old) return after && after();
      span.textContent = t;
      api('PATCH', '/agent/sessions/' + encodeURIComponent(id), {title: t}).then(function(m){
        if (sess && sess.id === id) sess.title = m.title;
        return loadList();
      }).then(function(){ renderHead(); if (after) after(); })
        .catch(function(e){ errMsg = e.message; render(); });
    }
    // 입력 칸 안의 클릭·키가 바깥 버튼(세션 고르기·목록 열기)으로 번지지 않게
    ['click', 'mousedown', 'dblclick'].forEach(function(n){
      inp.addEventListener(n, function(ev){ ev.stopPropagation(); });
    });
    inp.addEventListener('keydown', function(ev){
      ev.stopPropagation();
      if (ev.key === 'Enter' && !ev.isComposing){ ev.preventDefault(); finish(true); }
      if (ev.key === 'Escape'){ ev.preventDefault(); finish(false); }
    });
    inp.addEventListener('blur', function(){ finish(true); });
    span.replaceWith(inp);
    setTimeout(function(){ inp.focus(); inp.select(); }, 0);
  }
  function deleteSession(id){
    return api('DELETE', '/agent/sessions/' + encodeURIComponent(id)).then(function(){
      if (id !== sid) return loadList().then(renderMenu);
      sid = null; sess = null; lsSet(LS_SESSION, null);     // 지금 세션 — 다음 최근 세션(없으면 새로)으로
      return ensureSession().then(function(){ renderHead(); renderMenu(); });
    }).catch(function(e){ errMsg = e.message; closeMenu(); render(); });
  }
  function renderMenu(){
    menu.innerHTML = '';
    sessList.forEach(function(s){
      var b = document.createElement('div');
      b.className = 'ag-mi' + (s.id === sid ? ' on' : '');
      b.setAttribute('role', 'menuitem'); b.tabIndex = 0;
      b.innerHTML = '<span class="ag-mi-t">' + esc(s.title || md4T('새 세션')) + '</span>'
        + '<span class="ag-mi-s">' + (s.turns ? md4T('{n}문', {n: s.turns}) + ' · ' : '') + esc(shortTime(s.updated))
        + '</span>';
      var x = document.createElement('button');
      x.type = 'button'; x.className = 'ag-mi-x';
      x.title = md4T('이 세션 지우기 — 되돌릴 수 없습니다');
      x.setAttribute('aria-label', md4T('세션 지우기') + ' — ' + (s.title || md4T('새 세션')));
      x.innerHTML = '<i class="material-icons" aria-hidden="true">delete_outline</i>';
      x.addEventListener('click', function(ev){
        ev.stopPropagation();
        if (!x.classList.contains('arm')){       // 첫 누름은 무장만 — 빨갛게 '지우기?'
          menu.querySelectorAll('.ag-mi-x.arm').forEach(function(o){
            o.classList.remove('arm'); o.innerHTML = '<i class="material-icons" aria-hidden="true">delete_outline</i>';
          });
          x.classList.add('arm'); x.textContent = md4T('지우기?');
          return;
        }
        x.disabled = true; deleteSession(s.id);
      });
      b.appendChild(x);
      var tEl = b.querySelector('.ag-mi-t');
      b.addEventListener('click', function(ev){
        if (b.querySelector('.ag-name-in')) return;
        if (ev.detail >= 2) return renameInline(tEl, s.id, renderMenu);   // 더블클릭 = 이름 바꾸기
        var pick = function(){
          if (b.querySelector('.ag-name-in')) return;
          closeMenu();
          if (s.id !== sid) openSession(s.id).then(renderHead)
            .catch(function(e){ errMsg = e.message; render(); });
        };
        // 고르기는 더블클릭인지 볼 만큼만 미룬다 — 바로 닫으면 두 번째 클릭이 갈 줄이 없다
        clearTimeout(b._t); b._t = setTimeout(pick, 230);
      });
      b.addEventListener('dblclick', function(){ clearTimeout(b._t); });
      b.addEventListener('keydown', function(ev){
        if (ev.key === 'Enter'){ ev.preventDefault(); b.click(); }
        if (ev.key === 'F2'){ ev.preventDefault(); renameInline(tEl, s.id, renderMenu); }
      });
      menu.appendChild(b);
    });
    var h = document.createElement('div'); h.className = 'ag-mi-hint';
    h.textContent = md4T('이름을 더블클릭하면 바꿀 수 있습니다');
    menu.appendChild(h);
  }
  sessBtn.addEventListener('click', function(ev){
    if (busy || sessBtn.querySelector('.ag-name-in')) return;
    if (ev.detail >= 2){                  // 더블클릭 = 지금 세션 이름 바꾸기 (메모 탭과 같은 손짓)
      closeMenu();
      return renameInline(sessT, sid, null);
    }
    if (menu.classList.contains('open')) return closeMenu();
    menuMode = 'list';
    loadList().catch(function(){}).then(function(){ renderMenu(); menu.classList.add('open'); });
  });
  document.addEventListener('mousedown', function(ev){
    if (menu.classList.contains('open') && !menu.contains(ev.target) && !sessBtn.contains(ev.target))
      closeMenu();
  });

  // ---- 렌더 ----
  var TOOL_KO = {search_paper: md4T('논문 검색'), read_rows: md4T('행 읽기'), outline: md4T('목차 보기'),
    list_note_tabs: md4T('메모 탭 목록'), read_notes: md4T('메모 읽기'), read_highlights: md4T('하이라이트 읽기'),
    list_papers: md4T('논문 목록'), list_projects: md4T('컬렉션 목록'), get_bibtex: 'BibTeX',
    goto_row: md4T('행으로 이동'), open_paper: md4T('논문 열기'),
    write_note: md4T('메모 쓰기'), add_highlight: md4T('하이라이트 추가'),
    set_project_membership: md4T('컬렉션 소속 바꾸기'), remember: md4T('기억하기'),
    retranslate_sections: md4T('섹션 재번역'), run_glossary: md4T('용어집 만들기')};
  // 승인 카드의 아이콘 — Material Icons 글꼴(NiceGUI가 싣는다)의 이름
  var TOOL_ICON = {write_note: 'edit_note', add_highlight: 'border_color',
    set_project_membership: 'bookmark_add', remember: 'psychology',
    retranslate_sections: 'translate', run_glossary: 'menu_book'};
  function argsText(args){
    if (!args || typeof args !== 'object') return args ? String(args) : '';
    return Object.keys(args).map(function(k){
      var v = args[k]; v = typeof v === 'string' ? v : JSON.stringify(v);
      return k + '=' + (v.length > 60 ? v.slice(0, 60) + '…' : v);
    }).join(', ');
  }
  function toolNode(t){
    var d = document.createElement('div');
    d.className = 'ag-tool';
    d.title = t.name + ' — ' + md4T('눌러서 펼치기/접기');
    d.innerHTML = '🔧 <b>' + esc(TOOL_KO[t.name] || t.name) + '</b>'
      + (t.args ? ': ' + esc(argsText(t.args)) : '')
      + (t.summary ? ' <span class="ag-tres">→ ' + esc(t.summary) + '</span>' : '');
    d.addEventListener('click', function(){ d.classList.toggle('full'); });
    return d;
  }
  // 한 답의 tool 줄 묶음 — 평소엔 마지막(가장 최근) 호출 한 줄만 보이고, 앞선 호출은 '앞선 N개'를
  // 눌러 펼친다. 펼침 상태는 key(턴 id·'live')별로 기억한다 — render()가 목록을 통째로 다시 그려서.
  var toolOpen = {};
  function toolGroup(tools, key){
    var g = document.createElement('div');
    g.className = 'ag-tools';
    if (!tools || !tools.length) return g;
    var open = !!toolOpen[key], n = tools.length - 1;
    if (n > 0){
      var more = document.createElement('button');
      more.type = 'button';
      more.className = 'ag-tools-more';
      more.textContent = open ? '▾ ' + md4T('tool {n}개 — 접기', {n: tools.length}) : '▸ ' + md4T('앞선 tool {n}개', {n: n});
      more.addEventListener('click', function(){
        toolOpen[key] = !open;
        g.replaceWith(toolGroup(tools, key));
      });
      g.appendChild(more);
    }
    (open ? tools : tools.slice(-1)).forEach(function(t){ g.appendChild(toolNode(t)); });
    return g;
  }
  function citeOf(turn, chip){
    var cs = turn.citations || [], kind = chip.getAttribute('data-kind');
    var tok = chip.getAttribute('data-token') || '', row = parseInt(chip.getAttribute('data-row'), 10);
    var id = chip.getAttribute('data-id');
    for (var i = 0; i < cs.length; i++){
      var c = cs[i];
      if (kind === 'note' ? (c.kind === 'note' && c.id === id)
                          : (c.kind !== 'note' && c.token === tok && c.row === row)) return c;
    }
    return null;
  }
  function wireChips(a, turn){
    a.querySelectorAll('a.chat-cite').forEach(function(chip){
      var isNote = chip.getAttribute('data-kind') === 'note';
      var tok = chip.getAttribute('data-token') || '';
      var row = parseInt(chip.getAttribute('data-row'), 10) || 0;
      var id = chip.getAttribute('data-id');
      var c = citeOf(turn, chip) || {};
      var here = !!tok && tok === paper && onReview();
      if (!here) chip.classList.add('ag-far');   // 다른 논문(또는 홈) — 누르면 그 논문을 연다
      var tip = isNote
        ? md4T('내 메모') + ' — “' + (c.quote || '').slice(0, 90) + '”'
          + ((c.note || '').trim() ? ' / ' + c.note.replace(/\s+/g, ' ').slice(0, 70) : '')
        : (c.heading ? c.heading + ' — ' : '') + (c.en || '').replace(/\s+/g, ' ').slice(0, 160);
      chip.title = (here ? '' : md4T('다른 논문 — 누르면 열고 그 자리로') + '\n') + tip;
      // 칩은 곧장 뷰어의 그 문단으로 보낸다 — 원문·번역은 거기 이미 나란히 있으므로
      // 서랍 안에 한 번 더 그리지 않는다(툴팁으로 어디로 가는지만 미리 보여준다).
      chip.addEventListener('click', function(ev){
        ev.preventDefault();
        if (tok && tok === paper && onReview()){
          if (isNote) gotoNote(id, row); else gotoRow(row);
          return;
        }
        if (!gotoElsewhere(tok, row, isNote ? id : null, c.url, true)){
          errMsg = md4T('그 논문의 위치를 찾지 못했습니다.'); render();
        }
      });
    });
  }
  function turnNode(t){
    var wrap = document.createElement('div');
    if (t.role === 'user'){
      wrap.className = 'ag-turn ag-user';
      var q = document.createElement('div');
      q.className = 'ag-q'; q.textContent = t.text || '';
      if (t.mentions && t.mentions.length) q.appendChild(ctxChips(t.mentions, false));
      wrap.appendChild(q);
      apprDone(wrap, t.id);
      // 승인 뒤 이어 돈 run이 다시 승인 대기로 끝났을 때 그 사이 부른 tool 줄 — 같은 카드가 왜 또
      // 떴는지(예: 실패한 호출 뒤 다시 시도) 보이게 잠긴 카드 뒤에 둔다
      wrap.appendChild(toolGroup(toolsByTurn['u:' + t.id], 'u:' + t.id));
      return wrap;
    }
    wrap.className = 'ag-turn ag-bot';
    wrap.appendChild(toolGroup(toolsByTurn[t.id], t.id));
    if (t.html || t.text){
      var a = document.createElement('div');
      a.className = 'ag-a';
      if (t.html) a.innerHTML = t.html;     // 서버에서 마크다운 렌더 (raw HTML 비활성)
      else a.textContent = t.text;
      wireChips(a, t);
      wrap.appendChild(a);
    }
    if (t.error){
      var e = document.createElement('div');
      e.className = 'ag-err'; e.textContent = t.error;
      wrap.appendChild(e);
    }
    // 답 아래에는 모델 이름만, 그것도 지금 고른 모델과 다를 때만 — 턴별 비용은 보이지 않는다
    if (t.model && t.model !== modelLabel()){
      var meta = document.createElement('div');
      meta.className = 'ag-meta'; meta.textContent = t.model;
      wrap.appendChild(meta);
    }
    apprDone(wrap, t.id);
    return wrap;
  }
  function apprDone(wrap, id){            // 이 턴 뒤에서 답한 승인 카드들 — 결과로 잠겨 있다
    (apprByTurn[id] || []).forEach(function(rec){ wrap.appendChild(approvalNode(rec, false)); });
  }
  // 승인 카드 — open이면 지금 답을 기다리는 카드(단추가 산다), 아니면 결과(승인됨/거절됨/건너뜀)로 잠긴 카드.
  // 호출마다 [승인]/[거절], 여럿이면 맨 아래 [모두 승인]/[모두 거절]. 서버는 대기 호출 전부에 대한 답을
  // 한 번에 받으므로(일부 거절은 가능) 모든 호출에 답이 모이는 순간 보낸다 — 하나뿐이면 누르는 즉시.
  function apprText(c){
    return c.description || ((TOOL_KO[c.name] || c.name) + (c.args ? ' — ' + argsText(c.args) : ''));
  }
  function apprArgs(args){
    try { return JSON.stringify(args, null, 2); } catch (e) { return String(args); }
  }
  function decide(m){
    if (busy || !pending) return;
    Object.keys(m).forEach(function(k){ decisions[k] = m[k]; });
    var calls = pending.calls || [];
    if (!calls.every(function(x){ return x.id in decisions; })) return render();
    var ap = {}; calls.forEach(function(x){ ap[x.id] = !!decisions[x.id]; });
    send('', ap);
  }
  function approvalNode(rec, open){
    var calls = rec.calls || [];
    var box = document.createElement('div');
    box.className = 'ag-appr' + (open ? '' : ' done');
    var h = document.createElement('div');
    h.className = 'ag-appr-h';
    h.textContent = open ? (calls.length > 1 ? md4T('실행해도 될까요? ({n}건)', {n: calls.length}) : md4T('실행해도 될까요?'))
      : (rec.skipped ? md4T('건너뜀 — 새 메시지를 보내 실행하지 않았습니다') : md4T('승인 결과'));
    box.appendChild(h);
    calls.forEach(function(c){
      var row = document.createElement('div'); row.className = 'ag-appr-c';
      var top = document.createElement('div'); top.className = 'ag-appr-r';
      var ic = document.createElement('i');
      ic.className = 'material-icons ag-appr-i'; ic.setAttribute('aria-hidden', 'true');
      ic.textContent = TOOL_ICON[c.name] || 'build';
      top.appendChild(ic);
      var d = document.createElement('span'); d.className = 'ag-appr-d'; d.textContent = apprText(c);
      d.title = c.name;
      top.appendChild(d);
      var hasArgs = c.args && typeof c.args === 'object' && Object.keys(c.args).length;
      if (hasArgs){                          // 무엇을 넘기는지 — 접어 두고 필요할 때만 펼친다
        var tg = document.createElement('button');
        tg.type = 'button'; tg.className = 'ag-appr-tg'; tg.textContent = md4T('자세히');
        tg.title = md4T('넘길 인자 펼치기/접기');
        tg.addEventListener('click', function(){ row.classList.toggle('open'); });
        top.appendChild(tg);
      }
      if (open){
        [[md4T('승인'), true, 'ag-appr-ok'], [md4T('거절'), false, 'ag-appr-no']].forEach(function(o){
          var b = document.createElement('button');
          b.type = 'button'; b.textContent = o[0];
          b.className = o[2] + (decisions[c.id] === o[1] ? ' on' : '');
          b.disabled = busy;
          b.addEventListener('click', function(){ var m = {}; m[c.id] = o[1]; decide(m); });
          top.appendChild(b);
        });
      } else {
        var st = document.createElement('span');
        var ok = !!(rec.decisions || {})[c.id];
        st.className = 'ag-appr-s ' + (rec.skipped ? 'skip' : (ok ? 'ok' : 'no'));
        st.textContent = rec.skipped ? md4T('건너뜀') : (ok ? md4T('승인됨') : md4T('거절됨'));
        top.appendChild(st);
      }
      row.appendChild(top);
      if (hasArgs){
        var pre = document.createElement('pre'); pre.className = 'ag-appr-args';
        pre.textContent = apprArgs(c.args);
        row.appendChild(pre);
      }
      box.appendChild(row);
    });
    if (open && calls.length > 1){
      var all = document.createElement('div'); all.className = 'ag-appr-all';
      [[md4T('모두 승인'), true], [md4T('모두 거절'), false]].forEach(function(o){
        var b = document.createElement('button');
        b.type = 'button'; b.textContent = o[0]; b.disabled = busy;
        b.className = o[1] ? 'ag-appr-allok' : 'ag-appr-allno';
        b.addEventListener('click', function(){
          var m = {}; calls.forEach(function(x){ m[x.id] = o[1]; }); decide(m);
        });
        all.appendChild(b);
      });
      box.appendChild(all);
    }
    return box;
  }
  // 비용 상한 경고 — 턴을 시작하기 전에 서버가 막았다(400 code=cost_cap)
  function capNode(){
    var box = document.createElement('div'); box.className = 'ag-cap';
    var t = document.createElement('span'); t.className = 'ag-cap-t';
    t.textContent = capRetry.msg || md4T('이 세션의 비용 상한을 넘었습니다.');
    var go = document.createElement('button');
    go.type = 'button'; go.className = 'ag-cap-go'; go.textContent = md4T('그래도 계속');
    go.title = md4T('이번 요청만 상한을 넘겨 보냅니다');
    go.disabled = busy;
    go.addEventListener('click', function(){ var r = capRetry; if (r) send(null, null, r.body); });
    var no = document.createElement('button');
    no.type = 'button'; no.className = 'ag-cap-x'; no.textContent = md4T('그만두기');
    no.addEventListener('click', function(){ capRetry = null; render(); });
    box.appendChild(t); box.appendChild(go); box.appendChild(no);
    return box;
  }
  function noticeNode(){                   // 단독 창 — 화면 이동을 여기서 못 했다
    var box = document.createElement('div'); box.className = 'ag-note';
    box.textContent = md4T('이 창에는 논문 화면이 없습니다 — 메인 창에서 열어 주세요.');
    if (!window.__md4AgentNative && notice.url){     // 브라우저면 진짜 링크 — 누르면 새 탭(차단 없음)
      var a = document.createElement('a');
      a.href = notice.url; a.target = '_blank'; a.rel = 'noopener'; a.textContent = md4T('새 탭에서 열기');
      box.appendChild(a);
    }
    return box;
  }
  var liveText = null, liveTools = null;
  function liveNode(){
    var frag = document.createDocumentFragment();
    if (live.q){
      var u = document.createElement('div'); u.className = 'ag-turn ag-user';
      var q = document.createElement('div'); q.className = 'ag-q'; q.textContent = live.q;
      if (live.mentions && live.mentions.length) q.appendChild(ctxChips(live.mentions, false));
      u.appendChild(q); frag.appendChild(u);
    }
    var w = document.createElement('div'); w.className = 'ag-turn ag-bot';
    liveTools = toolGroup(live.tools, 'live');
    w.appendChild(liveTools);
    liveText = document.createElement('div');
    liveText.className = 'ag-a ag-live';
    liveText.textContent = live.text.replace(/^\s+/, '');
    w.appendChild(liveText);
    if (!live.text){
      var wt = document.createElement('div'); wt.className = 'ag-wait';
      wt.innerHTML = '<span class="ag-dots"><span></span><span></span><span></span></span>'
        + (live.tools.length ? md4T('자료를 읽는 중…') : md4T('생각하는 중…'));
      w.appendChild(wt);
    }
    if (live.err){
      var e = document.createElement('div'); e.className = 'ag-err'; e.textContent = live.err;
      w.appendChild(e);
    }
    frag.appendChild(w);
    return frag;
  }
  function nearBottom(){ return list.scrollHeight - list.scrollTop - list.clientHeight < 80; }
  function renderHead(){
    if (sess && !sess.title)                  // 첫 질문이 제목이 된다 — 목록에서 받아 온다
      for (var i = 0; i < sessList.length; i++) if (sessList[i].id === sess.id) sess.title = sessList[i].title;
    if (!sessBtn.querySelector('.ag-name-in')) sessT.textContent = (sess && sess.title) || md4T('새 세션');
    sessBtn.disabled = busy; newBtn.disabled = busy;
    if (popBtn) popBtn.setAttribute('href', popHref());   // 가운데 클릭·⌘클릭도 지금 세션으로
  }
  function render(){
    renderHead();
    list.innerHTML = '';
    if (!modelInfo.ready){
      var note = document.createElement('div');
      note.className = 'ag-err';
      note.textContent = (modelInfo.reason || md4T('AI 키가 설정되지 않았습니다.'))
        + ' ' + md4T('홈 → 설정에서 키를 넣으세요.');
      list.appendChild(note);
    }
    if (!turns.length && !live && modelInfo.ready){
      var em = document.createElement('div');
      em.className = 'ag-empty';
      em.textContent = (paper && (onReview() || SA)
        ? md4T('열린 논문과 라이브러리의 다른 논문을 읽고 답합니다.') + '\n'
          + md4T('예: “이 논문의 방법 섹션 핵심은?”, “선택한 문장 쉽게 풀어 줘”, “3번째 섹션으로 가 줘”')
        : md4T('라이브러리의 논문을 찾아 읽고 답합니다.') + '\n'
          + md4T('예: “이 라이브러리에 논문 몇 편 있어?”, “튜터링 관련 논문 열어 줘”'))
        + '\n' + md4T('답 속 숫자 칩을 누르면 근거 문단(노란 칩은 메모)으로 이동합니다.');
      list.appendChild(em);
    }
    var used = {};
    turns.forEach(function(t){ list.appendChild(turnNode(t)); list.appendChild(jobsAfter(t.id, used)); });
    // 턴을 못 찾은 작업(이 창이 붙인 임시 턴 id 등)은 끝에
    jobs.forEach(function(j){ if (!used[j.id]) list.appendChild(jobNode(j)); });
    if (live) list.appendChild(liveNode());
    else { liveText = null; liveTools = null; }
    if (pending) list.appendChild(approvalNode(pending, true));   // 진행 중 말풍선 아래에
    if (capRetry) list.appendChild(capNode());
    if (notice) list.appendChild(noticeNode());
    if (errMsg){
      var er = document.createElement('div');
      er.className = 'ag-err'; er.textContent = errMsg;
      list.appendChild(er);
    }
    // 승인 대기 중에도 입력창은 연다 — 그냥 보내면 서버가 대기 호출을 모두 거절하고 이어 간다(건너뜀)
    input.disabled = busy || !modelInfo.ready;
    atBtn.disabled = input.disabled;
    input.placeholder = pending ? PH_WAIT : PH_IDLE;
    sendBtn.disabled = input.disabled;
    list.scrollTop = list.scrollHeight;
  }

  // ---- 질문 → SSE 스트림 ----
  function readSSE(resp, on){
    var reader = resp.body.getReader(), dec = new TextDecoder(), buf = '';
    function pump(){
      return reader.read().then(function(r){
        if (r.value) buf += dec.decode(r.value, {stream: true});
        var i;
        while ((i = buf.indexOf('\n\n')) >= 0){
          var block = buf.slice(0, i); buf = buf.slice(i + 2);
          var ev = 'message', data = '';
          block.split('\n').forEach(function(line){
            if (line.indexOf('event: ') === 0) ev = line.slice(7).trim();
            else if (line.indexOf('data: ') === 0) data += line.slice(6);
          });
          var obj = {};
          try { obj = data ? JSON.parse(data) : {}; } catch (e) {}
          on(ev, obj);
        }
        if (!r.done) return pump();
      });
    }
    return pump();
  }
  function doAction(a){
    if (!a || !a.kind) return;
    if (a.kind === 'goto_row'){
      if (a.token && a.token === paper && onReview()){ gotoRow(a.row); return; }
      gotoElsewhere(a.token, a.row, null, a.url);
    } else if (a.kind === 'open_paper'){
      if (a.token && a.token === paper && onReview()) return;   // 이미 그 논문
      nav(a.url);
    } else if (a.kind === 'refresh'){
      refreshView(a.what, a.token, a.collection);
    } else if (a.kind === 'watch_job'){
      // 비용 tool이 작업을 띄웠다 — 그 논문의 뷰어면 번역 패널이 붙어 진행 바·다시 그리기·완료 알림을
      // 맡는다(app.py on_agent_job). 다른 화면이면 할 일이 없다: 헤더 탭 줄이 끝날 때 알리고,
      // 그 논문을 다시 열면 뷰어가 도는 작업에 붙거나 새 ko.md로 지어진다.
      if (a.token && a.token === paper && onReview() && typeof emitEvent === 'function')
        emitEvent('md4-agent-watch-job', {token: a.token, job: a.job});
    }
  }
  // 쓰기 tool이 파일을 고친 뒤 — 지금 화면이 그것을 보여 주고 있으면 다시 그린다.
  // 메모·하이라이트는 지금 뷰어의 논문일 때만(다른 논문이면 열 때 새로 읽으므로 할 일이 없다).
  function refreshView(what, tok, coll){
    if (SA) return;          // 다시 그릴 화면(홈·뷰어)이 이 창에는 없다 — 메인 창은 열 때 새로 읽는다
    if (what === 'glossary'){
      // 컬렉션 용어집 — 논문 token이 없다(컬렉션 단위). 뷰어면 서버가 그 논문의 컬렉션인지 보고 용어집 표·
      // '원문 바뀜'을 다시 그린다. 홈은 할 일이 없다(용어집 대화상자는 열 때 파일을 읽는다)
      if (onReview() && typeof emitEvent === 'function') emitEvent('md4-glossary-refresh', {collection: coll || ''});
      return;
    }
    if (what === 'library'){
      // 홈의 목록·컬렉션 칩은 서버가 짓는다 — 본문(sub_pages)만 다시 짓게 한다. 같은 주소로 가는
      // ui.navigate.to는 sub_pages가 '이미 그 화면'으로 보고 다시 짓지 않아 refresh 표시를 붙인다
      if (!onReview() && typeof emitEvent === 'function')
        emitEvent('md4-agent-nav', {url: '/home', refresh: true});
      return;
    }
    if (!tok || tok !== paper || !onReview()) return;
    if (what === 'notes'){
      // 메모 패널(app.py notes_panel)이 파일을 다시 읽는다 — 고치던 탭이면 기존 '바깥에서 바뀜' 안내를 탄다
      if (typeof emitEvent === 'function') emitEvent('md4-notes-refresh', {token: tok});
    } else if (what === 'highlights'){
      fetch('/annotations/' + encodeURIComponent(tok)).then(function(r){ return r.json(); })
        .then(function(j){
          if (tok === paper && window.__mdAnnoReload) window.__mdAnnoReload(tok, j.items || []);
        }).catch(function(){});
    }
  }
  function onEvent(name, d){
    if (!live) return;
    if (name === 'text'){
      var stick = nearBottom(), first = !live.text;
      live.text += d.delta || '';
      if (first || !liveText) render();        // 첫 조각 — '생각하는 중…'을 걷어 낸다
      else {
        liveText.textContent = live.text.replace(/^\s+/, '');      // 조각마다 전체를 다시 그리지 않는다
        if (stick) list.scrollTop = list.scrollHeight;
      }
    } else if (name === 'tool_call'){
      live.tools.push({id: d.id, name: d.name, args: d.args, summary: ''});
      render();
    } else if (name === 'tool_result'){
      live.tools.forEach(function(t){ if (t.id === d.id) t.summary = d.summary || ''; });
      render();
    } else if (name === 'ui_action'){
      doAction(d);
    } else if (name === 'approval_needed'){
      pending = d; decisions = {};
      render();                              // 진행 중 말풍선 아래에 카드 (done까지는 단추가 잠겨 있다)
    } else if (name === 'done'){
      live.done = d;
    } else if (name === 'error'){
      live.err = d.message || md4T('답을 만들지 못했습니다.');
    }
  }
  function lastTurnId(){ return turns.length ? turns[turns.length - 1].id : null; }
  // again: 비용 상한에 막혔던 요청 본문 — override를 더해 그대로 다시 보낸다
  function send(text, approvals, again){
    if (busy || !sid) return;
    var q, body;
    if (again){
      var keepAt = (body = again).context && again.context.mentions;   // 막혔던 질문의 @ 맥락은 그대로
      body.override = true; body.context = ctxNow();
      if (keepAt) body.context.mentions = keepAt;
      q = body.message || ''; approvals = body.approvals || null;
      if (q && input.value.trim() === q){ input.value = ''; autosize(); }
    } else {
      q = (text || '').trim();
      if (!q && !approvals) return;
      body = {message: q, context: ctxNow()};
      if (approvals) body.approvals = approvals;
      if (q){ input.value = ''; autosize(); lastSel = null; paintSel(); atPicked = []; atClose(); paintCtx(); }
    }
    capRetry = null; notice = null;
    // 대기 중이던 승인 카드를 잠근다 — 승인/거절을 보냈거나, 그냥 새 질문을 보내 건너뛰었다.
    // 잠긴 카드는 마지막 턴(그 승인을 부른 질문) 뒤에 남는다. 스트림 전에 거절되면 되돌린다.
    var rec = null, key = null, was = pending;
    if (pending){
      rec = {calls: pending.calls || [], decisions: approvals || {}, skipped: !approvals};
      key = lastTurnId() || '-';
      (apprByTurn[key] = apprByTurn[key] || []).push(rec);
      pending = null; decisions = {};
    }
    busy = true; errMsg = null; closeMenu();
    live = {q: q, text: '', tools: [], err: null, done: null, started: false,
            mentions: (body.context && body.context.mentions) || []};
    toolOpen.live = false;
    render();
    var at = sid;
    fetch('/agent/sessions/' + encodeURIComponent(at) + '/turns', {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
      .then(function(r){
        if (!r.ok) return r.json().then(function(j){
          var er = new Error(j.error || j.detail || ('HTTP ' + r.status));
          er.code = j.code || ''; er.cap = j.cost_cap; throw er;
        }, function(){ throw new Error('HTTP ' + r.status); });
        live.started = true;
        return readSSE(r, onEvent);
      })
      .catch(function(e){
        if (live && !live.started){            // 스트림 전 거절(키 없음·409·비용 상한 등) — 되돌려 놓는다
          var m = e.message || String(e);
          if (q && !input.value) input.value = q;
          var gone = (body.context && body.context.mentions) || [];   // @ 맥락도 칩으로 되돌린다
          if (q && gone.length && !atPicked.length){ atPicked = gone.slice(); paintCtx(); }
          if (rec){                            // 카드는 아직 답을 기다린다
            var arr = apprByTurn[key] || [], k = arr.indexOf(rec);
            if (k >= 0) arr.splice(k, 1);
            pending = was;
          }
          if (e.code === 'cost_cap'){          // 경고 줄 + '그래도 계속' — 같은 본문을 override로 다시
            delete body.override;
            if (sess && e.cap) sess.cost_cap = e.cap;   // 상한을 그새 바꿨어도 머리에 지금 값을
            capRetry = {body: body, msg: m};
          } else {
            errMsg = m;
            if (approvals)                     // 승인 카드를 서버 기록으로 맞춘다 (대기 목록은 그대로)
              refreshSession().then(function(){ errMsg = m; render(); });
          }
          live = null;
        } else if (live) live.err = live.err || md4T('연결이 끊겼습니다: {e}', {e: e.message || e});
      })
      .then(function(){
        var L = live;
        busy = false;
        if (!L){ render(); return; }
        if (L.done && !L.done.pending && L.done.turn_id){
          // 서버가 저장한 것과 같은 모양으로 붙인다 (다시 읽지 않아도 칩·비용이 그대로)
          if (L.q) turns.push({id: 'local-' + Date.now(), role: 'user', text: L.q, html: ''});
          toolsByTurn[L.done.turn_id] = L.tools;
          if (toolOpen.live) toolOpen[L.done.turn_id] = true;   // 흐르는 동안 펼쳐 뒀으면 그대로
          turns.push({id: L.done.turn_id, role: 'assistant', text: L.text, html: L.done.html,
                      citations: L.done.citations || [], model: modelLabel()});
          if (sess){ sess.usage = sess.usage || {}; sess.usage.cost_usd = L.done.session_cost_usd; }
          if (L.done.jobs) setJobs(L.done.jobs);   // 이 턴이 띄운 작업 — 미완료면 폴링이 시작된다
          live = null;
          render();
          loadList().then(renderHead).catch(function(){});   // 첫 질문이 세션 제목이 된다
          return;
        }
        if (L.done && L.done.pending){        // 승인 대기 — 서버 기록(질문 턴·대기 목록)을 다시 읽는다
          var resumed = (!L.q && L.tools.length) ? L.tools : null;   // 승인 재개가 또 대기로 끝났다
          refreshSession().then(function(){
            var k = lastTurnId();
            if (!resumed || !k) return;
            toolsByTurn['u:' + k] = (toolsByTurn['u:' + k] || []).concat(resumed);
            render();
          });
          return;
        }
        if (L.err && at === sid){            // 서버가 오류 턴을 남겼다 — 기록을 다시 읽어 맞춘다
          var msg = L.err;
          refreshSession().then(function(){
            var last = turns[turns.length - 1];
            if (!last || !last.error) { errMsg = msg; render(); }
          });
          return;
        }
        live = null; render();
      });
  }
  // ---- @ 맥락 (입력창에서 논문·섹션·메모 탭·컬렉션을 집어넣기) ----
  // 커서 앞의 '@글자'를 보고 서버(/agent/mentions)에 후보를 묻는다. 고르면 그 '@글자'는 지우고 칩으로.
  // 칩은 가리키는 값(token·행 범위·탭 이름·컬렉션 id)만 싣는다 — 내용은 에이전트가 tool로 읽는다.
  var AT_ICON = {paper: 'description', section: 'segment', note: 'sticky_note_2', collection: 'folder'};
  var AT_GROUP = {here: md4T('이 논문'), section: md4T('섹션'), note: md4T('메모 탭'),
                  paper: md4T('다른 논문'), collection: md4T('컬렉션')};
  function atKeyOf(m){
    return m.kind + ':' + (m.token || m.id || '') + ':' + (m.row == null ? '' : m.row) + ':' + (m.tab || '');
  }
  function atLabel(m){
    if (m.kind === 'paper' && m.current) return md4T('이 논문') + ' · ' + (m.title || '');
    return m.title || m.tab || '';
  }
  function ctxChips(ms, removable){
    var box = document.createElement('div');
    box.className = removable ? 'ag-ctx-in' : 'ag-q-ctx';
    ms.forEach(function(m, i){
      var c = document.createElement('span'); c.className = 'ag-chip';
      c.title = atLabel(m) + (m.kind === 'section' && m.row != null
        ? ' (r' + m.row + (m.end != null && m.end !== m.row ? '–r' + m.end : '') + ')' : '');
      c.innerHTML = '<i class="material-icons" aria-hidden="true">' + (AT_ICON[m.kind] || 'label')
        + '</i><span class="ag-chip-t">' + esc(atLabel(m)) + '</span>';
      if (removable){
        var x = document.createElement('button'); x.type = 'button'; x.className = 'ag-chip-x';
        x.innerHTML = '&#10005;'; x.setAttribute('aria-label', md4T('맥락 빼기') + ' — ' + atLabel(m));
        x.addEventListener('click', function(){ atPicked.splice(i, 1); paintCtx(); input.focus(); });
        c.appendChild(x);
      }
      box.appendChild(c);
    });
    return box;
  }
  function paintCtx(){
    ctxBar.innerHTML = '';
    ctxBar.classList.toggle('on', atPicked.length > 0);
    if (atPicked.length){
      var inner = ctxChips(atPicked, true);
      while (inner.firstChild) ctxBar.appendChild(inner.firstChild);
    }
  }
  function atQuery(){                    // 커서 앞 '@글자' — 없으면 null (줄바꿈·40자를 넘으면 끝)
    var p = input.selectionStart;
    if (p == null || p !== input.selectionEnd) return null;
    var pre = input.value.slice(0, p), i = pre.lastIndexOf('@');
    if (i < 0 || (i > 0 && !/\s/.test(pre[i - 1]))) return null;   // 메일 주소 속 @는 아니다
    var q = pre.slice(i + 1);
    if (/[\n]/.test(q) || q.length > 40 || /\s\s/.test(q)) return null;
    return {from: i, q: q};
  }
  function atWatch(){
    var at = atQuery();
    if (!at) return atClose();
    atFrom = at.from;
    clearTimeout(atTimer);
    atTimer = setTimeout(function(){ atFetch(at.q); }, atBox.classList.contains('open') ? 120 : 0);
  }
  function atFetch(q){
    var seq = ++atSeq, tok = (paper && (SA || onReview())) ? paper : '';
    api('GET', '/agent/mentions?paper=' + encodeURIComponent(tok) + '&q=' + encodeURIComponent(q))
      .then(function(j){
        if (seq !== atSeq || !atQuery()) return;           // 그새 더 쳤거나 @를 지웠다
        var have = {};
        atPicked.forEach(function(m){ have[atKeyOf(m)] = 1; });
        atItems = (j.items || []).filter(function(m){ return !have[atKeyOf(m)]; });
        atSel = 0; atRender(); atBox.classList.add('open');
      }).catch(function(){});
  }
  function atRender(){
    atBox.innerHTML = '';
    if (!atItems.length){
      var n = document.createElement('div'); n.className = 'ag-at-none';
      n.textContent = md4T('맞는 맥락이 없습니다'); atBox.appendChild(n); return;
    }
    var last = '';
    atItems.forEach(function(m, i){
      var g = m.kind === 'paper' && m.current ? 'here' : m.kind;
      if (g !== last){
        last = g;
        var h = document.createElement('div'); h.className = 'ag-at-g'; h.textContent = AT_GROUP[g] || g;
        atBox.appendChild(h);
      }
      var b = document.createElement('button'); b.type = 'button';
      b.className = 'ag-at-i' + (i === atSel ? ' on' : ''); b.setAttribute('role', 'option');
      var sub = m.kind === 'section' ? 'r' + m.row + (m.end != null && m.end !== m.row ? '–r' + m.end : '')
        : (m.sub || '');
      b.innerHTML = '<i class="material-icons" aria-hidden="true">' + (AT_ICON[m.kind] || 'label')
        + '</i><span class="ag-at-t">' + (m.kind === 'section' && m.level > 1
          ? '&nbsp;'.repeat((m.level - 1) * 2) : '') + esc(m.kind === 'paper' && m.current ? m.title : atLabel(m))
        + '</span>' + (sub ? '<span class="ag-at-s">' + esc(sub) + '</span>' : '');
      b.addEventListener('mousedown', function(ev){ ev.preventDefault(); });   // 입력창 초점을 지킨다
      b.addEventListener('click', function(){ atPick(i); });
      atBox.appendChild(b);
    });
    var on = atBox.querySelector('.ag-at-i.on');
    if (on && on.scrollIntoView) on.scrollIntoView({block: 'nearest'});
  }
  function atPick(i){
    var m = atItems[i], at = atQuery();
    if (!m) return;
    var from = at ? at.from : atFrom, p = input.selectionStart;
    if (from >= 0 && p != null && p >= from){         // '@글자'를 지우고 칩으로
      input.value = input.value.slice(0, from) + input.value.slice(p).replace(/^ /, '');
      input.selectionStart = input.selectionEnd = from;
    }
    var keep = {kind: m.kind, token: m.token, title: m.title, row: m.row, end: m.end, tab: m.tab, id: m.id};
    Object.keys(keep).forEach(function(k){ if (keep[k] == null) delete keep[k]; });
    if (m.current) keep.current = true;
    if (atPicked.length < 8) atPicked.push(keep);
    atClose(); paintCtx(); autosize(); input.focus();
  }
  function atClose(){
    clearTimeout(atTimer); atSeq++; atItems = []; atFrom = -1;
    atBox.classList.remove('open'); atBox.innerHTML = '';
  }
  function atKey(ev){
    if (!atBox.classList.contains('open') || ev.isComposing) return false;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp'){
      ev.preventDefault();
      if (atItems.length){
        atSel = (atSel + (ev.key === 'ArrowDown' ? 1 : -1) + atItems.length) % atItems.length;
        atRender();
      }
      return true;
    }
    if ((ev.key === 'Enter' || ev.key === 'Tab') && atItems.length){ ev.preventDefault(); atPick(atSel); return true; }
    if (ev.key === 'Escape'){ ev.preventDefault(); ev.stopPropagation(); atClose(); return true; }
    return false;
  }
  function ask(){ send(input.value, null); }
  function autosize(){
    input.style.height = 'auto';
    input.style.height = Math.min(120, Math.max(30, input.scrollHeight)) + 'px';
  }

  // ---- 열기/닫기 (메모 서랍과 배타) ----
  // 'open'은 여러 경로에서 붙었다 뗀다(버튼·✕·Esc·메모 서랍). 한 곳에서 관찰해 body 클래스를
  // 맞춰야 본문 자리 비우기가 어느 경로로 닫아도 따라온다.
  function syncBody(){
    var on = panel.classList.contains('open');
    document.body.classList.toggle('ag-open', on);
    if (!SA) lsSet(LS_OPEN, on ? '1' : null);   // 단독 창은 늘 열려 있다 — 메인 창의 '열어 둠'을 건드리지 않게
    syncLayout();
  }
  // 서랍 위끝을 헤더 아래에 맞춘다 (메모 서랍이 여는 순간 하는 것과 같은 계산).
  function syncTop(){
    if (SA){ document.documentElement.style.setProperty('--ag-top', '0px'); return 1; }   // 헤더가 없다
    var h = document.querySelector('.q-header');
    var top = h ? Math.max(0, h.getBoundingClientRect().bottom) : 0;
    document.documentElement.style.setProperty('--ag-top', top + 'px');
    return top;
  }
  // 첫 로드에 열린 채로 뜨면(지난번에 열어 두었음) 헤더가 아직 안 그려져 0을 잰다 → 헤더가
  // 자리를 잡을 때까지 몇 프레임 다시 잰다 (안 그러면 서랍이 헤더를 덮는다)
  function syncTopSoon(){
    var n = 0;
    (function again(){ if (!syncTop() && ++n < 120) requestAnimationFrame(again); })();
  }
  window.addEventListener('resize', function(){ syncTop(); syncLayout(); });
  syncTop();
  if (window.MutationObserver)
    new MutationObserver(syncBody).observe(panel, {attributes: true, attributeFilter: ['class']});

  // 너비 — 사용자가 끈 폭(agWant, localStorage)과 실제로 쓰는 폭(--ag-w)을 나눈다. 실제 폭은 열 때·
  // 창 크기가 바뀔 때·끌 때마다 뷰포트에 맞춰 다시 잰다(syncLayout). 창을 잠깐 줄였다고 기억한
  // 폭까지 줄어들면 안 되므로 localStorage는 드래그만 쓴다.
  // 값은 파이썬 AG_* 상수와 한 짝이다(테스트가 대조한다).
  var AG_MIN = 320, AG_MAX = 760, AG_KEEP = 360;
  var AG_OVERLAY_BELOW = 760, AG_NARROW_BELOW = 1100, AG_SLIM_BELOW = 380;
  var agWant = 400;
  function agCap(){          // 지금 뷰포트에서 서랍이 가질 수 있는 최대 폭 — 본문 몫 AG_KEEP는 남긴다
    return Math.min(AG_MAX, Math.max(AG_MIN, window.innerWidth - AG_KEEP));
  }
  // 서랍 폭과 body 모드 클래스를 한 계산에서 정한다:
  //   ag-overlay — 남는 본문 폭 < AG_OVERLAY_BELOW → 밀지 않고 덮는다(본문 padding 0)
  //   ag-narrow — 밀기 모드에서 남는 폭이 모자라 홈 미리보기 열을 접는다
  //   (오버레이일 땐 본문이 제 폭이라 접을 까닭이 없다 — 붙이면 넓은 홈에서 미리보기만 사라진다)
  //   ag-slim(서랍에) — 서랍 자체가 좁아 머리줄을 줄인다
  function syncLayout(){
    var vw = window.innerWidth;
    if (SA){ panel.classList.toggle('ag-slim', vw < AG_SLIM_BELOW); return; }   // 폭은 CSS가 화면 전체로
    var w = Math.min(vw, agWant, agCap());
    document.documentElement.style.setProperty('--ag-w', w + 'px');
    var on = panel.classList.contains('open'), rest = vw - w;
    var over = on && rest < AG_OVERLAY_BELOW;
    var cl = document.body.classList;
    cl.toggle('ag-overlay', over);
    cl.toggle('ag-narrow', on && !over && rest < AG_NARROW_BELOW);
    panel.classList.toggle('ag-slim', w < AG_SLIM_BELOW);
  }
  function setWidth(px){     // 드래그 — 지금 뷰포트 상한까지만 받고, 받은 값을 기억한다
    agWant = Math.max(AG_MIN, Math.min(agCap(), Math.round(px)));
    lsSet(LS_W, String(agWant));
    syncLayout();
  }
  var w0 = parseInt(lsGet(LS_W), 10);
  if (w0) agWant = Math.max(AG_MIN, Math.min(AG_MAX, w0));
  var agDrag = false;
  panel.querySelector('.ag-handle').addEventListener('mousedown', function(ev){
    agDrag = true; ev.preventDefault(); document.body.style.userSelect = 'none';
  });
  window.addEventListener('mousemove', function(ev){
    if (agDrag) setWidth(window.innerWidth - ev.clientX);
  });
  window.addEventListener('mouseup', function(){
    if (agDrag){ agDrag = false; document.body.style.userSelect = ''; }
  });

  // 메모 서랍은 뷰어가 지을 때 생긴다(홈에서 시작하면 아직 없다) → 논문이 바뀔 때마다 다시 붙는다
  var annoSeen = null;
  function watchAnno(){
    var ap = document.getElementById('md-anno-panel');
    if (!ap || ap === annoSeen || !window.MutationObserver) return;
    annoSeen = ap;
    new MutationObserver(function(){             // 메모 서랍이 열리면 에이전트 창은 닫는다
      if (ap.classList.contains('open')) panel.classList.remove('open');
    }).observe(ap, {attributes: true, attributeFilter: ['class']});
  }
  function openPanel(){
    syncTopSoon();
    var ap = document.getElementById('md-anno-panel');
    if (ap) ap.classList.remove('open');          // 없을 수도 있다 (홈)
    panel.classList.add('open');
    // 다시 열 때는 세션을 새로 읽는다 — 그사이 새 창(단독 창)에서 쌓인 턴이 있을 수 있다
    if (!loaded){ loadModels(); ensureSession(); }
    else { render(); if (!busy) refreshSession(); }
    setTimeout(function(){ if (!input.disabled) input.focus(); }, 60);
  }
  function closePanel(){ closeMenu(); if (!SA) panel.classList.remove('open'); }
  window.__md4Agent = {
    toggle: function(){ if (panel.classList.contains('open')) closePanel(); else openPanel(); },
    open: openPanel, close: closePanel,
    // 설정 창(AI 탭)에서 에이전트 모델을 바꿨다 — 준비 상태·모델 이름을 다시 받는다
    reloadModels: function(){ return loadModels(); },
    // 화면이 바뀔 때 뷰어·홈이 부른다 — 창·대화·스트림은 그대로, 지금 논문만 바꾼다
    setPaper: function(tok){
      var prev = paper;
      paper = tok || null;
      window.__md4AgentTok = paper;
      watchAnno(); syncTopSoon();
      consumeGoto();
      if (panel.classList.contains('open') && !live) render();   // 칩의 '다른 논문' 표시를 다시
    },
    context: ctxNow
  };
  panel.querySelector('.ag-x').addEventListener('click', closePanel);
  newBtn.addEventListener('click', newSession);
  sendBtn.addEventListener('click', ask);
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', function(ev){
    if (atKey(ev)) return;               // @ 목록이 떠 있으면 위·아래·Enter·Tab·Esc는 목록 몫
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing){ ev.preventDefault(); ask(); }
  });
  input.addEventListener('input', atWatch);
  input.addEventListener('click', atWatch);
  input.addEventListener('blur', function(){ setTimeout(function(){
    if (document.activeElement !== input && !atBox.contains(document.activeElement)) atClose(); }, 150); });
  atBtn.addEventListener('click', function(){      // '@' 단추 = 커서 자리에 @를 넣고 목록을 연다
    var p = input.selectionStart == null ? input.value.length : input.selectionStart;
    var pre = input.value.slice(0, p), lead = pre && !/\s$/.test(pre) ? ' @' : '@';
    input.value = pre + lead + input.value.slice(p);
    input.focus(); input.selectionStart = input.selectionEnd = p + lead.length;
    autosize(); atWatch();
  });
  document.addEventListener('keydown', function(ev){
    if (ev.key !== 'Escape') return;
    if (menu.classList.contains('open')){ closeMenu(); return; }
    // 알약이 있으면 알약만 닫는다 — Esc 한 번에 알약과 서랍이 동시에 사라지지 않게.
    // 단 그림 확대·메모 카드·찾기 바·대화상자가 열려 있으면 Esc의 주인은 그쪽이라 비켜선다.
    if (document.querySelector('#md-img-zoom.open, #md-anno-pop.open, #md4-find.on, .q-dialog')) return;
    var jb = window.__mdJumpBack;
    if (jb && jb.state()){ jb.dismiss(); return; }
    if (!SA && panel.classList.contains('open')) panel.classList.remove('open');
  });
  // ---- 새 창으로 (머리의 ⧉) ----
  // 진짜 링크다: 브라우저 모드면 기본 동작(새 탭)을 그대로 두고 href만 지금 세션으로 맞춘다. 앱 창이면
  // 서버가 형제 웹뷰 창을 띄운다(app.py가 install에 넘긴 popout). 같은 세션을 두 창이 함께 쓰면 마지막
  // 저장이 이기므로 넘겨주기가 기본이다 — 이 창의 서랍은 닫는다.
  function popHref(){
    var q = [];
    if (sid) q.push('session=' + encodeURIComponent(sid));
    if (paper && onReview()) q.push('paper=' + encodeURIComponent(paper));
    return POPOUT_PATH + (q.length ? '?' + q.join('&') : '');
  }
  if (popBtn) popBtn.addEventListener('click', function(ev){
    popBtn.setAttribute('href', popHref());
    if (window.__md4AgentNative && typeof emitEvent === 'function'){
      ev.preventDefault();
      emitEvent('md4-agent-popout', {session: sid || '', paper: (paper && onReview()) ? paper : ''});
    }
    setTimeout(closePanel, 0);               // 링크의 기본 동작(새 탭)이 먼저 돈 뒤에
  });

  // ---- 단독 창: 다른 창에서 쌓인 턴 반영 ----
  // 턴이 없을 때만 세션을 다시 읽고, 바뀐 게 있을 때만 다시 그린다(무조건 그리면 읽던 스크롤이 맨 아래로
  // 튀고 고르던 승인 답이 지워진다).
  function sessSig(ts, pd, title, js){
    var last = ts && ts.length ? ts[ts.length - 1].id : '';
    return [(ts || []).length, last, ((pd && pd.calls) || []).map(function(c){ return c.id; }).join(','),
            title || '', (js || []).map(function(j){ return j.id + (j.done ? '+' : '-'); }).join(',')].join('|');
  }
  function saPoll(){
    if (!sid || busy || live || document.hidden || menu.classList.contains('open')) return;
    var at = sid;
    api('GET', '/agent/sessions/' + encodeURIComponent(at)).then(function(d){
      if (at !== sid || busy || live) return;
      if (sessSig(d.turns, d.pending, d.title, d.jobs)
          === sessSig(turns, pending, sess && sess.title, jobs)) return;
      setSession(d);
      loadList().then(renderHead).catch(function(){});
    }).catch(function(){});
  }

  var wasOpen = lsGet(LS_OPEN) === '1';        // syncBody가 지우기 전에 읽는다
  if (SA){
    document.body.classList.add('ag-standalone');
    setInterval(saPoll, SA_POLL_MS);
  }
  watchAnno();
  syncBody();
  render();
  if (wasOpen || SA) openPanel();              // 다시 켜도 열어 두었던 창은 연 채로 (단독 창은 늘)
})();
"""


def init_js(token: str | None) -> str:
    """화면(뷰어·홈)이 지어질 때 창에 지금 논문을 알리는 JS — 홈이면 None.

    창 스크립트보다 먼저 돌 수 있으므로(첫 화면) 전역에도 남겨 두고, 창이 뜰 때 그것을 읽는다.
    """
    tok = json.dumps(token).replace("<", "\\u003c")
    return (f"window.__md4AgentTok = {tok};"
            f"window.__md4Agent && window.__md4Agent.setPaper({tok});")


def _nav(url: str, refresh: bool = False) -> None:
    """JS가 부탁한 화면 이동 — 앱 안의 두 화면 주소만 받는다(바깥 주소로 끌려가지 않게).

    refresh(홈만): 이동 대신 지금 홈 본문을 다시 짓는다 — 에이전트가 컬렉션 소속을 바꾼 뒤 카드 칩을
    맞추려고. 같은 주소로 가는 ui.navigate.to는 sub_pages가 그대로 두므로 SubPages.refresh()를 부른다.
    그새 사용자가 뷰어로 옮겼으면(지금 본문이 홈이 아니면) 아무것도 하지 않는다.
    """
    from nicegui import ui

    if refresh and url == "/home":
        from nicegui import context
        from nicegui.elements.sub_pages import SubPages

        for el in list(context.client.elements.values()):
            if isinstance(el, SubPages) and getattr(el, "_rendered_path", "/home") in ("/", "/home"):
                el.refresh()
        return
    if isinstance(url, str) and (url.startswith("/review?wd=") or url == "/home"):
        ui.navigate.to(url)


def popout_path(session: str = "", paper: str = "") -> str:
    """단독 창 주소 — JS popHref()와 같은 꼴(`/agent?session=…&paper=…`, 빈 값은 뺀다)."""
    q = urlencode([(k, v) for k, v in (("session", session), ("paper", paper)) if v])
    return STANDALONE_PATH + (f"?{q}" if q else "")


def _flags_js(native: bool, standalone: dict | None) -> str:
    """창 스크립트보다 **먼저** 도는 전역 — 앱 창 모드인지, 단독 창이면 무엇을 넘겨받았는지."""
    sa = json.dumps(standalone, ensure_ascii=False).replace("<", "\\u003c")
    return (f"<script>window.__md4AgentNative = {'true' if native else 'false'};"
            f"window.__md4AgentStandalone = {sa};</script>")


def install(state: dict | None = None,  # noqa: ARG001 — 셸 설치 훅들과 같은 꼴
            popout: Callable[[str], object] | None = None) -> None:
    """셸 페이지가 한 번 부른다 — 창 CSS·마크업·스크립트와 화면 이동 이벤트.

    셸에 붙으므로 홈 ↔ 뷰어를 오가도(본문만 갈아 끼움) 창은 다시 지어지지 않는다.
    popout: 앱 창 모드에서만 app.py가 넘긴다 — 단독 창 경로(`popout_path`)를 형제 웹뷰 창으로 띄우는
    함수(코루틴 가능). 넘기면 '새 창으로'가 링크 대신 `POPOUT_EVENT`로 서버에 부탁한다.
    """
    from nicegui import ui

    from narani.ui import spa

    spa.add_css(CSS)
    spa.add_body_html(i18n.html(HTML))
    spa.add_body_html(_flags_js(popout is not None, None))
    spa.add_body_html(f"<script>{JS}</script>")
    ui.on(NAV_EVENT, lambda e: _nav((e.args or {}).get("url", ""), bool((e.args or {}).get("refresh"))))
    if popout is not None:
        ui.on(POPOUT_EVENT, lambda e: _popout(popout, e.args or {}))


def _popout(popout: Callable[[str], object], args: dict):  # noqa: ANN202 — popout의 반환(코루틴)을 그대로
    """JS가 부탁한 새 창 — 쿼리로 넘길 값은 형식을 걸러서만 싣는다."""
    sid, tok = (str(args.get(k) or "") for k in ("session", "paper"))
    return popout(popout_path(sid if _ID_RE.match(sid) else "", tok if _ID_RE.match(tok) else ""))


def standalone_page(state: dict, session: str = "", paper: str = "", *, native: bool = False) -> None:
    """단독 창(`/agent`) — 셸의 헤더·화면 없이 창 UI만 화면 전체로 (app.py의 @ui.page가 부른다).

    paper는 등록부(`state["wd_by_token"]`)에 있을 때만 맥락에 싣는다 — 서버를 다시 켠 뒤 옛 주소로
    열면 모르는 token이 되어 첫 질문부터 404('알 수 없는 논문 token')가 나기 때문이다.
    화면 이동 이벤트(NAV)는 등록하지 않는다: 이 창에서 뷰어로 옮겨 가면 창이 사라진다.
    """
    from nicegui import ui

    from narani.workdir import paper_title

    sid = session if _ID_RE.match(session or "") else ""
    wd = (state.get("wd_by_token") or {}).get(paper) if paper and _ID_RE.match(paper) else None
    title = ""
    if wd is not None:
        try:
            title = " ".join(paper_title(Path(wd.root)).split())[:200]
        except OSError:
            title = ""
    name = i18n.t(WINDOW_TITLE)
    ui.page_title(f"{name} — {title}" if title else name)
    ui.add_css(CSS)
    ui.add_head_html(i18n.head_html())  # 단독 창엔 셸 head가 없다 — md4T와 카탈로그를 여기서
    ui.add_body_html(i18n.html(HTML))
    ui.add_body_html(_flags_js(native, {"session": sid, "paper": paper if wd is not None else None,
                                        "title": title or None}))
    ui.add_body_html(f"<script>{JS}</script>")
