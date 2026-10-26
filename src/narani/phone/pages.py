"""폰 리더 웹앱과 함께 올리는 안내 페이지 — 소개(홈페이지)와 개인정보처리방침.

Google OAuth 동의 화면을 '프로덕션'으로 올리려면 앱을 설명하는 홈페이지와, 같은 도메인의 개인정보처리방침이
필요하다(동의 화면과 홈페이지 양쪽에서 같은 주소로 링크). 웹앱과 같은 곳(GitHub Pages)에 두면 도메인이 같다.

내용은 실제 동작과 맞아야 한다 — 권한(drive.file), 무엇을 어디에 두는지(§phone.gdrive·notes·web), 데스크톱
에이전트가 메모를 AI 서비스로 보낼 수 있다는 점(§ui.agent.retrieval). 동작을 바꾸면 여기도 고친다.
리뷰어가 읽도록 영어를 먼저, 한국어를 아래에 둔다.
"""

from __future__ import annotations

REPO = "https://github.com/wooogler/narani"
# 웹앱·이 페이지들이 올라가는 주소 — GitHub Pages(wooogler/narani의 gh-pages)에 사용자 도메인을 붙였다.
# Google 동의 화면의 홈페이지·개인정보처리방침·승인된 도메인, 웹 클라이언트의 원본·리디렉션 URI가 이 주소다.
DOMAIN = "narani.sangwooklee.info"
SITE = f"https://{DOMAIN}/"
UPDATED = "2026-10-26"  # 내용을 바꾸면 날짜도

_STYLE = """
:root { --bg:#fbfaf7; --fg:#1f1e1c; --muted:#6b6862; --line:#e4e1da; --accent:#2f6fd0; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --fg:#e8e6e1; --muted:#9a968f; --line:#33322f; --accent:#7fb0ff; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); line-height: 1.7; font-size: 17px;
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", "Segoe UI", sans-serif;
  word-break: keep-all; overflow-wrap: anywhere; }
main { max-width: 720px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.6em; line-height: 1.3; margin: .4em 0 .2em; }
h2 { font-size: 1.2em; margin: 1.8em 0 .4em; padding-top: .8em; border-top: 1px solid var(--line); }
h3 { font-size: 1.02em; margin: 1.2em 0 .3em; }
a { color: var(--accent); }
.lead { color: var(--muted); margin-top: 0; }
.nav { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: .9em; margin: 8px 0 20px; }
code, pre { font-size: .85em; background: rgba(127,127,127,.12); border-radius: 6px; }
code { padding: 1px 5px; } pre { padding: 10px 12px; overflow-x: auto; }
.lang { font-size: .85em; color: var(--muted); }
footer { margin-top: 48px; font-size: .85em; color: var(--muted); }
"""


def _page(title: str, body: str) -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta name=\"color-scheme\" content=\"light dark\">"
            f"<title>{title}</title><style>{_STYLE}</style></head><body><main>{body}"
            f"<footer>Narani · <a href=\"about.html\">About</a> · <a href=\"privacy.html\">Privacy Policy</a> · "
            f"<a href=\"{REPO}\">Source code</a></footer></main></body></html>")


ABOUT_HTML = _page("Narani — read papers side by side in your language", f"""
<h1>Narani</h1>
<p class="lead">Read English research papers side by side with a translation in your own language — on your
computer and on your phone. <span class="lang">한국어 안내는 아래에 있습니다.</span></p>
<div class="nav"><a href="./">Open the mobile reader</a><a href="privacy.html">Privacy Policy</a>
<a href="{REPO}">Source code &amp; documentation</a></div>

<h2>What it does</h2>
<p>Narani is a free, open-source desktop app (macOS, Windows, Linux). It converts a paper PDF into clean Markdown,
translates it into your language with the AI service and API key you choose, and shows the original and the
translation paragraph by paragraph. You can highlight sentences, write notes, and ask questions about the paper.</p>
<p>The <strong>mobile reader</strong> on this site lets you keep reading on your phone or tablet: the desktop app sends the
papers you pick to a <code>Narani</code> folder in <em>your own</em> Google Drive, and this web app reads them from
there. Highlights and notes you make on mobile go back to the desktop the same way. Downloaded papers stay
readable offline.</p>

<h2>Install</h2>
<p>macOS / Linux:</p>
<pre>curl -LsSf https://raw.githubusercontent.com/wooogler/narani/main/install.sh | sh</pre>
<p>Windows (PowerShell):</p>
<pre>irm https://raw.githubusercontent.com/wooogler/narani/main/install.ps1 | iex</pre>
<p>Then, in the app, tick papers on Home and press <strong>Send to mobile</strong>. On your phone, open this site,
add it to your home screen, and tap <strong>Connect Google Drive</strong> with the same Google account.</p>

<h2>How Narani uses your Google account</h2>
<ul>
<li>Narani asks for one permission: <code>drive.file</code> — access only to files that Narani itself created.
It cannot see or change any other file in your Drive.</li>
<li>Everything is stored in your own Drive and on your own devices. Narani has no server and no account system;
this site is a static web app hosted on GitHub Pages.</li>
<li>Details are in the <a href="privacy.html">Privacy Policy</a>.</li>
</ul>

<h2 lang="ko">한국어</h2>
<div lang="ko">
<p><strong>Narani(나란히)</strong>는 영어 논문을 내 언어 번역과 나란히 읽게 해 주는 무료 오픈소스 데스크톱 앱입니다.
논문 PDF를 마크다운으로 바꾸고, 사용자가 고른 AI 서비스와 API 키로 번역해 원문과 문단별로 나란히 보여 줍니다.
문장 하이라이트·메모·질문하기도 됩니다.</p>
<p>이 사이트의 <strong>모바일 리더</strong>로 폰·태블릿에서도 이어 읽습니다. 데스크톱 앱이 고른 논문을 <em>내</em> Google Drive의
<code>Narani</code> 폴더에 올리고, 이 웹앱이 거기서 읽습니다. 모바일에서 단 하이라이트·메모도 같은 길로 데스크톱에 돌아갑니다.
받아 둔 논문은 오프라인에서도 읽힙니다.</p>
<p>설치는 위의 명령 한 줄. 앱 홈에서 논문을 체크하고 <strong>모바일로 보내기</strong>를 누른 뒤, 폰에서 이 사이트를 열어
홈 화면에 추가하고 같은 계정으로 <strong>Google Drive 연결</strong>을 누르면 됩니다.</p>
<p>권한은 <code>drive.file</code>(Narani가 만든 파일만) 하나라 Drive의 다른 파일은 보지 못합니다. 서버도 계정 체계도 없고,
모든 데이터는 내 Drive와 내 기기에만 있습니다. 자세한 내용은 <a href="privacy.html">개인정보처리방침</a>에 있습니다.</p>
</div>
""")

PRIVACY_HTML = _page("Narani — Privacy Policy", f"""
<h1>Privacy Policy</h1>
<p class="lead">Narani desktop app and mobile reader · last updated {UPDATED}.
<span class="lang">한국어는 아래에 있습니다.</span></p>

<h2>Summary</h2>
<p>Narani has no server and collects nothing. When you connect Google Drive, Narani reads and writes only the files
it creates in a <code>Narani</code> folder in your own Drive. Your papers and notes stay in your Drive and on your
own devices.</p>

<h3>What we access from your Google account</h3>
<ul>
<li><strong>Google Drive, <code>drive.file</code> scope</strong> — only files Narani created. Narani creates a folder
named <code>Narani</code> and keeps these files in it:
  <ul>
  <li>the papers you chose to send (converted text, translation, figures, citation details);</li>
  <li>your highlights and notes for those papers (from the desktop);</li>
  <li>highlights and notes you made on the phone, until the desktop merges them.</li>
  </ul>
Narani cannot see, list, or change any other file in your Drive.</li>
<li><strong>Your account email address</strong> — read once when you connect on the desktop, only to show you which
account is connected. It is not stored or sent anywhere.</li>
</ul>

<h3>How we use it</h3>
<p>Only to provide the feature you asked for: sending papers from your computer to your phone and keeping
highlights and notes in sync between them. Nothing is used for advertising, analytics, or profiling.</p>

<h3>Where it is stored</h3>
<ul>
<li><strong>Your Google Drive</strong> — the <code>Narani</code> folder described above.</li>
<li><strong>Your computer</strong> — the authorization token is saved in Narani's settings folder
(<code>~/.config/narani/google_drive.json</code>), readable only by your user account. Highlights and notes you made
on the phone are merged into your local paper folders.</li>
<li><strong>Your phone's browser</strong> — a short-lived access token (about one hour), copies of papers you
downloaded for offline reading, phone notes waiting to be uploaded, and — only if you use the mobile assistant — your
AI API key and that conversation.</li>
</ul>
<p>Narani does not operate any server or database. This site is a static web app on GitHub Pages; GitHub may keep
standard web server logs under the <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">GitHub
Privacy Statement</a>. The mobile reader talks directly to Google's APIs from your browser.</p>

<h3>Sharing</h3>
<p>We do not sell, rent, or share Google user data. Narani transfers it only between Google Drive and your own
devices.</p>
<p><strong>Mobile assistant.</strong> The mobile reader has an optional assistant ("Ask") for the paper you are
reading. To use it you enter <em>your own</em> API key for an AI service (OpenAI, Anthropic, or Google Gemini) on that
device; the key is stored only in that browser and is never sent to Narani or saved to Drive. When you ask a question,
your browser sends the paper's text, your highlights and notes for that paper, and the conversation directly to the
service you chose, and usage is billed to that key's account. The conversation is kept only in that browser.</p>
<p>One thing to know: the desktop app has an optional assistant that answers questions about your papers using the
AI service and API key <em>you</em> configure. When you ask it a question, it may include passages and your own
highlights and notes for that paper in the request it sends to that service — including notes that came from your
phone. This happens only when you use the assistant, and the data goes only to the provider you chose.</p>
<p>Narani's use and transfer of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a>,
including the Limited Use requirements. Narani does not use Google user data to develop, improve, or train
generalized AI or machine-learning models.</p>

<h3>Your choices and deleting data</h3>
<ul>
<li>Disconnect on the desktop: Home sidebar <strong>Mobile</strong> → <strong>Disconnect</strong> (or
<code>narani phone disconnect</code>). This revokes the token and deletes it from your computer.</li>
<li>Revoke access at any time in your Google Account: <a href="https://myaccount.google.com/permissions">Third-party
apps &amp; services</a>.</li>
<li>Delete the <code>Narani</code> folder in Google Drive to remove everything Narani stored there.</li>
<li>On the phone, clear this site's data in your browser settings to remove offline copies, the token, and any AI
key you entered (or use <strong>Delete this service's key</strong> in the assistant's settings).</li>
</ul>

<h3>Children</h3>
<p>Narani is not directed to children under 13.</p>

<h3>Changes and contact</h3>
<p>We will post changes on this page and update the date above. Questions:
<a href="{REPO}/issues">open an issue on GitHub</a>.</p>

<h2 lang="ko">개인정보처리방침 (한국어)</h2>
<div lang="ko">
<p><strong>요약</strong> — Narani는 서버가 없고 아무것도 수집하지 않습니다. Google Drive를 연결하면 내 Drive의
<code>Narani</code> 폴더에 Narani가 만든 파일만 읽고 씁니다. 논문과 메모는 내 Drive와 내 기기에만 있습니다.</p>
<h3>Google 계정에서 쓰는 것</h3>
<ul>
<li><strong>Google Drive, <code>drive.file</code> 권한</strong> — Narani가 만든 파일만. <code>Narani</code> 폴더에 보낸 논문
(변환한 글·번역·그림·인용 정보), 그 논문의 하이라이트·메모, 폰에서 단 하이라이트·메모(데스크톱이 합칠 때까지)를 둡니다.
Drive의 다른 파일은 보지도 고치지도 못합니다.</li>
<li><strong>계정 이메일</strong> — 데스크톱에서 연결할 때 어느 계정인지 보여 주려고 한 번 읽습니다. 저장하거나 보내지 않습니다.</li>
</ul>
<h3>쓰는 목적</h3>
<p>요청한 기능에만 — 컴퓨터의 논문을 폰으로 보내고, 하이라이트·메모를 양쪽에서 맞추는 일. 광고·분석·프로파일링에 쓰지 않습니다.</p>
<h3>저장 위치</h3>
<ul>
<li><strong>내 Google Drive</strong>의 <code>Narani</code> 폴더.</li>
<li><strong>내 컴퓨터</strong> — 인증 토큰은 Narani 설정 폴더(<code>~/.config/narani/google_drive.json</code>)에 내 계정만
읽을 수 있게 저장합니다. 폰에서 단 메모는 내 논문 폴더에 합쳐집니다.</li>
<li><strong>폰 브라우저</strong> — 한 시간쯤 쓰는 접근 토큰, 오프라인으로 받아 둔 논문, 아직 올리지 못한 폰 메모, 그리고
모바일 질문하기를 쓸 때만 내 AI API 키와 그 대화.</li>
</ul>
<p>Narani는 서버나 데이터베이스를 운영하지 않습니다. 이 사이트는 GitHub Pages의 정적 웹앱이며, GitHub은
<a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">GitHub 개인정보 고지</a>에
따라 일반적인 웹 서버 기록을 남길 수 있습니다. 모바일 리더는 브라우저에서 Google API와 직접 통신합니다.</p>
<h3>제공·공유</h3>
<p>Google 사용자 데이터를 팔거나 빌려주거나 공유하지 않습니다. Google Drive와 내 기기 사이에서만 오갑니다.</p>
<p><strong>모바일 질문하기.</strong> 모바일 리더에는 읽고 있는 논문에 대해 묻는 기능(선택)이 있습니다. 쓰려면 그 기기에
<em>내</em> AI 서비스(OpenAI·Anthropic·Google Gemini) API 키를 넣습니다. 키는 그 브라우저에만 저장되고 Narani나 Drive로
보내지 않습니다. 질문하면 브라우저가 그 논문의 본문, 그 논문의 내 하이라이트·메모, 대화를 고른 서비스로 직접 보내고, 요금은
그 키의 계정에 청구됩니다. 대화는 그 브라우저에만 남습니다.</p>
<p>알아 둘 점: 데스크톱 앱의 질문하기(에이전트)는 <em>사용자가 설정한</em> AI 서비스와 API 키로 답합니다. 질문하면 그 논문의
본문 일부와 내 하이라이트·메모(폰에서 온 것 포함)를 그 서비스로 보내는 요청에 담을 수 있습니다. 질문하기를 쓸 때만, 사용자가
고른 서비스로만 보냅니다.</p>
<p>Narani가 Google API로 받은 정보를 쓰고 옮기는 방식은 Limited Use 요건을 포함한
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API 서비스 사용자 데이터 정책</a>을 따릅니다.
Google 사용자 데이터를 범용 AI·머신러닝 모델을 만들거나 학습시키는 데 쓰지 않습니다.</p>
<h3>선택과 삭제</h3>
<ul>
<li>데스크톱에서 연결 끊기: 홈 사이드바 <strong>모바일</strong> → <strong>연결 끊기</strong>(또는 <code>narani phone disconnect</code>) — 토큰을
반납하고 컴퓨터에서 지웁니다.</li>
<li>언제든 Google 계정의 <a href="https://myaccount.google.com/permissions">서드파티 앱 및 서비스</a>에서 권한을 거둘 수 있습니다.</li>
<li>Google Drive의 <code>Narani</code> 폴더를 지우면 Narani가 둔 것이 모두 지워집니다.</li>
<li>폰에서는 브라우저 설정에서 이 사이트의 데이터를 지우면 오프라인 사본·토큰·넣어 둔 AI 키가 지워집니다(질문하기
설정의 <strong>이 서비스 키 지우기</strong>로 키만 지울 수도 있습니다).</li>
</ul>
<h3>아동</h3>
<p>Narani는 만 13세 미만 아동을 대상으로 하지 않습니다.</p>
<h3>변경과 문의</h3>
<p>바뀌면 이 페이지에 올리고 위 날짜를 고칩니다. 문의: <a href="{REPO}/issues">GitHub 이슈</a>.</p>
</div>
""")
