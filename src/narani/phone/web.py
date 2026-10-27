"""폰 리더 웹앱(PWA) — 정적 파일 몇 개. 어디에 올려 두든(GitHub Pages 등) 서버 코드 없이 돈다.

화면은 모바일 HTML(§mobile)과 같다 — 같은 CSS, 같은 읽기 동작(`mobile.CORE_JS`: 보기 모드·글자 크기·
문장 짝 시트·인용/각주 시트). 다른 점:
  - 논문을 **하나씩** 저장소에서 받아(§phone.bundle) 꽂는다. 목록은 index.json.
  - 받은 것은 Cache Storage에 둔다 — 비행기 안에서도 목록과 받아 둔 논문이 열린다. 같은 지문(hash)이면
    다시 받지 않는다. 앱 껍데기(이 파일들)는 서비스 워커가 캐시한다.
  - 화면 문구는 브라우저 언어(또는 데스크톱이 index.json에 적은 화면 언어)로 — 카탈로그를 다 싣는다.

저장소: `local`(같은 주소의 `data/` — `narani phone preview`가 로컬 폴더를 그 자리에 붙인다).
Google Drive 어댑터가 같은 `get(name)` 모양으로 들어온다.

JS는 이 저장소의 관례대로 파이썬 문자열에 둔다 — 그래야 md4T 문구를 i18n 카탈로그가 모은다.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from narani import i18n
from narani.phone import pages
from narani.ui import mobile, sentpair

APP_NAME = "Narani"

_PWA_CSS = """
.top button { white-space: nowrap; }
.top .back { font-size: 1.3em; line-height: 1; padding: 4px 10px; }
.js.reading .top .tb.rd { display: inline-block; }
.top .tb.rd, #fab.rd { display: none; }
/* 제목 — 두 줄까지. 논문을 열었을 때는 누르면 목차(▾) */
.top .top-title { border: 0; background: none; min-height: 0; padding: 2px 2px; text-align: left; color: var(--fg);
  font: inherit; font-weight: 600; font-size: .92em; line-height: 1.25; white-space: normal; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.js.reading .top .top-title { position: relative; padding-right: 16px; }
.js.reading .top .top-title::after { content: "▾"; position: absolute; right: 2px; bottom: 2px; color: var(--muted);
  font-weight: 400; font-size: .9em; }
.top #lang-btn { font-weight: 600; min-width: 64px; }
/* 키보드가 올라오면 아래 시트·대화 창을 그 위로 (--kb: 키보드 높이, visualViewport로 잰다) */
#sheet { bottom: var(--kb, 0px); max-height: min(55vh, calc(100vh - var(--kb, 0px) - 70px)); }
.pop { position: absolute; right: 10px; top: calc(100% + 4px); display: none; gap: 6px; padding: 6px; z-index: 6;
  background: var(--card); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 6px 20px rgba(0,0,0,.12); }
.pop.open { display: flex; }
#scrim { position: fixed; inset: 0; background: rgba(0,0,0,.28); z-index: 11; opacity: 0; pointer-events: none;
  transition: opacity .18s ease; }
#scrim.open { opacity: 1; pointer-events: auto; }
.dh { display: flex; align-items: center; gap: 6px; padding: 10px 12px; border-bottom: 1px solid var(--line);
  font-weight: 600; font-size: .95em; }
.dh span { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dh button { border: 0; background: none; color: var(--muted); font: inherit; font-size: 1.2em; padding: 2px 6px; }
#tocd { position: fixed; top: 0; bottom: 0; left: 0; width: min(84vw, 340px); z-index: 12; background: var(--bg);
  border-right: 1px solid var(--line); transform: translateX(-102%); transition: transform .2s ease;
  display: flex; flex-direction: column; padding-top: env(safe-area-inset-top); }
#tocd.open { transform: none; }
#tocd ol { list-style: none; margin: 0; padding: 6px 0 calc(24px + env(safe-area-inset-bottom)); overflow-y: auto;
  font-size: .92em; line-height: 1.45; }
#tocd li a { display: block; padding: 8px 16px; color: var(--fg); text-decoration: none; }
#tocd li.l3 a { padding-left: 30px; } #tocd li.l4 a { padding-left: 44px; }
#tocd li.here a { background: var(--hl2); font-weight: 600; box-shadow: inset 3px 0 0 var(--accent); }
#fab { position: fixed; right: 16px; bottom: calc(18px + env(safe-area-inset-bottom)); z-index: 8; width: 52px; height: 52px;
  border-radius: 50%; border: 0; background: var(--accent); color: #fff; box-shadow: 0 6px 18px rgba(0,0,0,.22); }
.js.reading #fab.rd { display: grid; place-items: center; }
#agent { position: fixed; left: 0; right: 0; bottom: var(--kb, 0px); height: min(86vh, calc(100vh - var(--kb, 0px) - 40px)); z-index: 12; background: var(--sheet);
  border-radius: 16px 16px 0 0; box-shadow: 0 -8px 30px rgba(0,0,0,.18); display: flex; flex-direction: column;
  transform: translateY(105%); transition: transform .2s ease; visibility: hidden; }
#agent.open { transform: none; visibility: visible; }
#ag-body { flex: 1; overflow-y: auto; padding: 12px 14px; font-size: .95em; line-height: 1.6; }
#ag-body .m { margin: 0 0 12px; white-space: pre-wrap; }
#ag-body .m.u { margin-left: 18%; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 8px 12px; }
#ag-body .m.a { white-space: normal; } #ag-body .m.a p { margin: 0 0 .6em; } #ag-body .m.a ul { margin: 0 0 .6em; padding-left: 1.2em; }
#ag-body .m.err { color: #b3261e; }
#ag-body .hint { color: var(--muted); font-size: .9em; }
#ag-body a.rr { text-decoration: none; border-bottom: 1px dotted currentColor; font-size: .85em; }
#ag-body label { display: block; font-size: .8em; color: var(--muted); margin: 12px 0 4px; }
#ag-body select, #ag-body input { width: 100%; font: inherit; font-size: 16px; color: var(--fg); background: var(--card);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
#ag-body .acts { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
#ag-body .acts button { border: 1px solid var(--line); background: var(--card); color: var(--fg); border-radius: 9px;
  font: inherit; font-size: .9em; padding: 8px 14px; }
#ag-body .acts button.pri { background: var(--accent); border-color: var(--accent); color: #fff; }
#ag-form { display: flex; gap: 8px; padding: 8px 12px calc(10px + env(safe-area-inset-bottom)); border-top: 1px solid var(--line); }
#ag-in { flex: 1; font: inherit; font-size: 16px; color: var(--fg); background: var(--card); border: 1px solid var(--line);
  border-radius: 18px; padding: 8px 14px; resize: none; max-height: 30vh; }
#ag-send { width: 40px; height: 40px; border-radius: 50%; border: 0; background: var(--accent); color: #fff; font-size: 1.1em;
  align-self: flex-end; }
#ag-send:disabled { opacity: .45; }
.row.flash { animation: rowFlash 1.6s ease-out; }
@keyframes rowFlash { 0%, 50% { background: var(--hl2); } 100% { background: transparent; } }
.lib-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 4px 0 6px; color: var(--muted);
  font-size: .8em; line-height: 1.4; }
.lib-bar .grow { flex: 1; min-width: 12em; }
.lib-bar button { border: 1px solid var(--line); background: var(--card); color: var(--fg); border-radius: 9px;
  font: inherit; padding: 6px 10px; min-height: 32px; }
.lib-bar button:disabled { opacity: .55; }
.badge.off { border-style: dashed; }
.li-by { display: flex; gap: 5px; color: var(--muted); font-size: .8em; margin-top: 6px; white-space: nowrap; min-width: 0; }
.li-by .who { overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.li-by .sep, .li-by .when { flex: none; }
.li-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.li-tags .tag { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; font-size: .75em; color: var(--muted);
  border: 1px solid var(--line); border-radius: 999px; padding: 1px 9px; font-variant-numeric: tabular-nums; }
.li-tags .tag.au { color: var(--accent); border-color: currentColor; }
#loading { color: var(--muted); text-align: center; margin: 30vh 0 0; font-size: .95em; }
#lib .empty { color: var(--muted); font-size: .9em; padding: 12px 2px; }
.lib-foot { color: var(--muted); font-size: .8em; text-align: center; margin: 8px 0 24px; }
.lib-foot a { color: var(--muted); }

/* 하이라이트 — 데스크톱 뷰어와 같은 색. 글자는 형광 위라 늘 어둡게 */
mark.anno { color: #24211c; border-radius: 2px; padding: 0; box-decoration-break: clone; -webkit-box-decoration-break: clone; }
.c-yellow { background: #ffe08a; } .c-green { background: #b5e7b8; } .c-blue { background: #a9d8f5; }
.c-pink { background: #f9bcd4; } .c-purple { background: #d8c6f5; }
mark.anno.note-end::after { content: "✎"; font-size: .75em; margin-left: 2px; color: #6b5a2a; }
.anno-dots { display: flex; gap: 12px; margin: 12px 0 2px; }
.anno-dots .dot { width: 32px; height: 32px; border-radius: 50%; border: 2px solid rgba(0,0,0,.12); padding: 0; }
.anno-dots .dot.on { border-color: var(--fg); box-shadow: 0 0 0 2px var(--sheet) inset; }
.anno-note { margin-top: 10px; padding: 8px 10px; background: var(--card); border: 1px solid var(--line);
  border-radius: 8px; font-size: .9em; white-space: pre-wrap; }
.anno-ta { width: 100%; font: inherit; font-size: 16px; color: var(--fg); background: var(--card);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; resize: vertical; }
/* 떠 있는 💬 단추의 창 — '메모'(이 논문의 내 하이라이트·메모) · '질문하기'(에이전트) 중 하나를 고르는 두 칸 스위치 */
.ag-tabs { flex: 1; display: flex; min-width: 0; max-width: 300px; margin-right: auto; padding: 3px; border-radius: 11px; background: var(--line); }
.dh .ag-tabs button { flex: 1; display: flex; align-items: center; justify-content: center; gap: 5px; min-width: 0;
  font-size: .9em; font-weight: 600; color: var(--muted); padding: 6px 8px; border-radius: 8px; white-space: nowrap; }
.dh .ag-tabs button svg { flex: none; }
.dh .ag-tabs .cnt { flex: none; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; background: var(--accent); color: #fff;
  font-size: 11px; font-weight: 700; line-height: 18px; text-align: center; }
.dh .ag-tabs button.on { color: var(--fg); background: var(--sheet); box-shadow: 0 1px 3px rgba(0,0,0,.14); }
#agent.notes #ag-clear, #agent.notes #ag-set { display: none; }
#fab-n { position: absolute; top: -3px; right: -3px; min-width: 20px; height: 20px; padding: 0 5px; border-radius: 10px;
  background: var(--fg); color: var(--bg); font-size: 12px; font-weight: 700; line-height: 20px; display: none; }
#fab-n.on { display: block; }
.nl-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 4px; color: var(--muted); font-size: .85em; }
.nl-bar .grow { flex: 1; }
.nl-bar button { border: 1px solid var(--line); background: var(--card); color: var(--fg); border-radius: 9px;
  font: inherit; padding: 5px 10px; }
.nl-bar button.on { background: var(--hl2); border-color: var(--accent); }
ol.nl { list-style: none; margin: 0; padding: 0; }
ol.nl li { padding: 10px 2px; border-bottom: 1px solid var(--line); cursor: pointer; }
ol.nl .aq { border-radius: 3px; padding: 1px 4px; color: #24211c; font-size: .92em; box-decoration-break: clone;
  -webkit-box-decoration-break: clone; }
ol.nl .an { display: block; margin: 5px 0 0 4px; white-space: pre-wrap; }
ol.nl .al { display: block; color: var(--muted); font-size: .8em; margin-top: 3px; }
/* 듣기 — 🎧는 💬 위에, 재생 막대는 맨 아래(💬·🎧는 그 위로 올라간다). 지금 문장은 ::highlight(tts) */
#lis { position: fixed; right: 20px; bottom: calc(82px + env(safe-area-inset-bottom)); z-index: 8; width: 44px; height: 44px;
  border-radius: 50%; border: 1px solid var(--line); background: var(--card); color: var(--accent); display: none;
  box-shadow: 0 4px 14px rgba(0,0,0,.14); }
.js.reading.has-audio:not(.listening) #lis { display: grid; place-items: center; }
#player { position: fixed; left: 0; right: 0; bottom: 0; z-index: 9; display: none; background: var(--card);
  border-top: 1px solid var(--line); box-shadow: 0 -6px 20px rgba(0,0,0,.1); padding: 6px 12px calc(6px + env(safe-area-inset-bottom)); }
.js.reading.listening #player { display: block; }
.js.reading.listening #fab.rd { bottom: calc(118px + env(safe-area-inset-bottom)); }
.js.reading.listening main { padding-bottom: 130px; }
.pl-top { display: flex; gap: 8px; font-size: .8em; color: var(--muted); }
#pl-sec { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pl-t { white-space: nowrap; font-variant-numeric: tabular-nums; }
#pl-seek { width: 100%; margin: 4px 0 2px; }
.pl-ctl { display: flex; justify-content: space-between; align-items: center; }
.pl-ctl button { border: 0; background: none; color: var(--fg); font: inherit; font-size: 1em; min-width: 36px; padding: 6px 4px; }
.pl-ctl button svg { display: block; margin: 0 auto; }
#pl-pp { width: 46px; height: 46px; border-radius: 50%; background: var(--accent); color: #fff; font-size: 1.15em; }
#pl-range, #pl-spd { font-size: .8em; color: var(--muted); }
::highlight(tts) { background-color: rgba(255, 196, 0, .45); }
/* 말로 묻기 — 막대의 🎙, 대화 중 상태 막대(재생 막대 위·없으면 아래) */
/* 듣는 중엔 떠 있는 💬 단추가 '🎙 묻기' 알약으로 바뀐다(메모·질문하기 창은 막대를 닫고) */
#fab-ask { display: none; align-items: center; gap: 6px; font: inherit; font-weight: 700; font-size: 1em; }
.js.reading.listening #fab.rd { width: auto; padding: 0 18px 0 14px; border-radius: 26px; }
.js.reading.listening #fab .fab-c, .js.reading.listening #fab-n { display: none; }
.js.reading.listening #fab-ask { display: flex; }
#ag-mic { width: 40px; height: 40px; border-radius: 50%; border: 1px solid var(--line); background: var(--card); font-size: 1.05em;
  align-self: flex-end; }
#talkbar { position: fixed; left: 12px; right: 12px; bottom: calc(14px + env(safe-area-inset-bottom)); z-index: 13; display: none;
  align-items: center; gap: 8px; background: var(--fg); color: var(--bg); border-radius: 16px; padding: 10px 10px 10px 14px;
  box-shadow: 0 8px 24px rgba(0,0,0,.25); font-weight: 600; }
.js.reading.listening #talkbar { bottom: calc(126px + env(safe-area-inset-bottom)); }
#talkbar.on { display: flex; }
#talkbar .lv { width: 12px; height: 12px; border-radius: 50%; background: #9a968e; flex: none; }
#talkbar.listening .lv { background: #4caf50; animation: tkPulse 1.2s infinite; } #talkbar.speaking .lv { background: #6aa0ff; }
#talkbar.thinking .lv { background: #e6a700; }
@keyframes tkPulse { 50% { transform: scale(1.35); opacity: .6; } }
#tk-st { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#tk-meta { font-weight: 400; font-size: .8em; opacity: .7; font-variant-numeric: tabular-nums; }
#tk-end { border: 0; border-radius: 999px; background: #c4554d; color: #fff; font: inherit; padding: 6px 14px; }
#talkbar:not(.listening):not(.speaking):not(.thinking) #tk-end { display: none; }
.js.talking #fab.rd, .js.talking #lis { display: none; }   /* 대화 중엔 대화 막대만 */
.row.tts-row { background: var(--hl2); border-radius: 6px; }
.js.listening .row.tts-off { opacity: .35; transition: opacity .2s; }   /* 음성에 없는 행 — 듣는 동안 흐리게 */
.rng label { display: block; margin: 6px 0; }
.rng-secs { margin: 4px 0 0 22px; max-height: 32vh; overflow-y: auto; font-size: .9em; }
/* 새 판 알림 — 읽는 중일 때만 (목록에선 바로 다시 연다) */
#upd { position: fixed; left: 50%; top: calc(env(safe-area-inset-top) + 60px); transform: translateX(-50%); z-index: 20;
  display: none; align-items: center; gap: 8px; background: var(--fg); color: var(--bg); border-radius: 999px;
  padding: 6px 8px 6px 14px; font-size: .85em; box-shadow: 0 6px 18px rgba(0,0,0,.2); white-space: nowrap; }
#upd.on { display: flex; }
#upd button { border: 0; border-radius: 999px; background: var(--accent); color: #fff; font: inherit; font-weight: 600; padding: 5px 12px; }
/* 오프라인 — 받아 두지 않은 논문은 흐리게 */
#lib-list li.na a { opacity: .45; }
.lib-bar label.auto { display: inline-flex; align-items: center; gap: 6px; color: var(--fg); }
.lib-bar label.auto input { width: 18px; height: 18px; margin: 0; }
"""

# 화면 언어 고르기 + md4T — 읽기 동작(CORE_JS)이 돌기 전에 있어야 한다
_PRELUDE_JS = r"""
(function(){
  var CFG = window.NARANI_CFG || {}, CATS = CFG.catalogs || {};
  function known(l){ return !!l && (l in CATS || l === CFG.source_lang); }
  function pick(){
    var saved = null;
    try { saved = localStorage.getItem('narani:ui_lang'); } catch (e) {}
    if (known(saved)) return saved;
    var prefs = navigator.languages || [navigator.language || ''], i, l;
    for (i = 0; i < prefs.length; i++){
      l = String(prefs[i]);
      if (known(l)) return l;
      if (/^zh-(TW|HK|MO|Hant)/i.test(l) && known('zh-Hant')) return 'zh-Hant';
      if (/^zh/i.test(l) && known('zh-Hans')) return 'zh-Hans';
      if (known(l.split('-')[0])) return l.split('-')[0];
    }
    return known(CFG.fallback_lang) ? CFG.fallback_lang : CFG.source_lang;
  }
  window.md4Lang = pick();
  window.md4T = function(s, kw){
    var r = (CATS[window.md4Lang] || {})[s] || s;
    if (kw) r = r.replace(/\{(\w+)\}/g, function(m, k){ return (k in kw) ? kw[k] : m; });
    return r;
  };
})();
"""

# 목록 ↔ 논문 — 논문은 저장소(또는 캐시)에서 받아 하나씩 꽂는다. mobile._ROUTE_JS의 자리.
_APP_JS = r"""
  var CFG = window.NARANI_CFG || {};
  var PID = 'p0', DC = 'narani-data';
  var main = doc.querySelector('main'), libList = doc.getElementById('lib-list'),
      libBar = doc.getElementById('lib-bar'), loading = doc.getElementById('loading');
  var DATA = {}, INDEX = null, loadSeq = 0, routed = false;
  var APP = CFG.app_name || 'Narani';

  // ---- 화면 문구 ----
  function setText(id, s){ var el = doc.getElementById(id); if (el) el.textContent = s; }
  function labels(){
    doc.documentElement.lang = md4Lang;
    langLabel();
    setText('fs-dn', md4T('가') + '−'); setText('fs-up', md4T('가') + '+'); setText('fs-btn', md4T('가'));
    doc.getElementById('fs-btn').setAttribute('aria-label', md4T('글자 크기'));
    doc.getElementById('top-title').setAttribute('aria-label', md4T('목차'));
    doc.getElementById('fab').setAttribute('aria-label', md4T('메모') + ' · ' + md4T('질문하기'));
    setText('lnk-about', md4T('Narani 소개')); setText('lnk-privacy', md4T('개인정보처리방침'));
    doc.getElementById('fs-dn').setAttribute('aria-label', md4T('글자 작게'));
    doc.getElementById('fs-up').setAttribute('aria-label', md4T('글자 크게'));
    doc.getElementById('back').setAttribute('aria-label', md4T('논문 목록'));
    audioLabels();
  }
  function setUiLang(l){
    var cats = CFG.catalogs || {};
    if (!l || l === md4Lang || !(l in cats || l === CFG.source_lang)) return;
    try { localStorage.setItem('narani:ui_lang', l); } catch (e) {}
    window.md4Lang = l; labels();
  }

  // ---- 저장소 ----
  function localSource(){
    return {raw: function(name){
      return fetch('data/' + name, {cache: 'no-store'}).then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r; });
    }, get: function(name){
      return fetch('data/' + name, {cache: 'no-store'}).then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
    }, put: function(name, obj){   // 미리보기 서버는 mobile/ 아래만 받아 준다
      return fetch('data/' + name, {method: 'PUT', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify(obj)}).then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status);
      });
    }};
  }
  // Google Drive — 데스크톱이 만든 `Narani` 폴더(같은 Cloud 프로젝트라 drive.file로 보인다)를 읽는다.
  // 로그인은 팝업 대신 **리디렉트**(OAuth 클라이언트 측 흐름) — iOS 홈 화면 앱에선 팝업이 막히거나
  // 다른 창으로 열린다. 돌아온 주소의 #access_token을 읽고 원래 화면(#!…)으로 되돌린다.
  // access token은 한 시간짜리다. 끊기면 읽던 것(캐시)은 그대로 열리고, 새로 받을 때만 다시 연결한다.
  var GSCOPE = 'https://www.googleapis.com/auth/drive.file', GAPI = 'https://www.googleapis.com/drive/v3',
      GUP = 'https://www.googleapis.com/upload/drive/v3';
  var authError = '';
  function qs(o){ return Object.keys(o).map(function(k){ return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); }
  function takeRedirect(){
    var h = location.hash.replace(/^#/, '');
    if (!/(^|&)(access_token|error)=/.test(h)) return;
    var p = {}, saved = null;
    h.split('&').forEach(function(kv){ var i = kv.indexOf('='); if (i > 0) p[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); });
    try { saved = JSON.parse(sessionStorage.getItem('narani:gstate') || 'null'); sessionStorage.removeItem('narani:gstate'); } catch (e) {}
    history.replaceState(null, '', location.pathname + location.search + ((saved && saved.back) || ''));
    if (!saved || saved.s !== p.state) return;
    if (p.access_token){
      try {
        localStorage.setItem('narani:gtok', JSON.stringify({at: p.access_token, exp: Date.now() + (+p.expires_in || 3600) * 1000}));
        localStorage.setItem('narani:gconn', '1');
      } catch (e) {}
    } else authError = p.error || 'error';
  }
  function gToken(){
    try { var t = JSON.parse(localStorage.getItem('narani:gtok') || 'null'); return t && t.exp > Date.now() + 60000 ? t.at : null; }
    catch (e) { return null; }
  }
  function gSignIn(){
    var st = Math.random().toString(36).slice(2) + Date.now().toString(36);
    try { sessionStorage.setItem('narani:gstate', JSON.stringify({s: st, back: location.hash})); } catch (e) {}
    var o = {client_id: CFG.google_client_id, redirect_uri: location.origin + location.pathname.replace(/index\.html$/, ''), response_type: 'token',
             scope: GSCOPE, include_granted_scopes: 'true', state: st};
    location.href = 'https://accounts.google.com/o/oauth2/v2/auth?' + qs(o);
  }
  function driveSource(){
    var folder = null, ids = null;
    function fail(kind){ var e = new Error(kind); e.kind = kind; return e; }
    function api(url, opt){
      var t = gToken();
      if (!t) return Promise.reject(fail('auth'));
      opt = opt || {};
      var h = opt.headers || {}; h.Authorization = 'Bearer ' + t; opt.headers = h;
      return fetch(url, opt).then(function(r){
        if (r.status === 401){ try { localStorage.removeItem('narani:gtok'); } catch (e) {} throw fail('auth'); }
        if (!r.ok) throw fail('http ' + r.status);
        return r;
      });
    }
    function list(q){
      var out = [];
      function page(tok){
        var u = GAPI + '/files?' + qs({q: q, fields: 'nextPageToken,files(id,name,modifiedTime)', pageSize: 1000,
                                       spaces: 'drive', orderBy: 'modifiedTime desc'}) + (tok ? '&pageToken=' + encodeURIComponent(tok) : '');
        return api(u).then(function(r){ return r.json(); }).then(function(d){
          out = out.concat(d.files || []);
          return d.nextPageToken ? page(d.nextPageToken) : out;
        });
      }
      return page(null);
    }
    function findFolder(){
      if (folder) return Promise.resolve(folder);
      return list("mimeType = 'application/vnd.google-apps.folder' and trashed = false and appProperties has { key='narani' and value='store' }")
        .then(function(fs){
          if (!fs.length) throw fail('nofolder');
          folder = fs[0].id; return folder;
        });
    }
    function loadIds(){
      return findFolder().then(function(f){ return list("'" + f + "' in parents and trashed = false"); }).then(function(fs){
        ids = {}; fs.forEach(function(x){ if (!(x.name in ids)) ids[x.name] = x.id; }); return ids;
      });
    }
    return {
      drive: true,
      raw: function(name){
        var p = ids && ids[name] ? Promise.resolve(ids) : loadIds();
        return p.then(function(m){
          if (!m[name]) throw fail('missing');
          return api(GAPI + '/files/' + m[name] + '?alt=media');
        });
      },
      get: function(name){
        // 목록을 받을 때마다 이름표를 새로 받는다 — 데스크톱이 그사이 새 번들을 올렸을 수 있다
        var p = (name === 'index.json' || !ids) ? loadIds() : Promise.resolve(ids);
        return p.then(function(m){
          if (!m[name]) throw fail(name === 'index.json' ? 'nofolder' : 'missing');
          return api(GAPI + '/files/' + m[name] + '?alt=media').then(function(r){ return r.json(); });
        });
      },
      // 폰이 쓰는 파일(mobile/…)만 — 있으면 내용만 바꾸고, 없으면 앱 폴더에 새로 만든다
      put: function(name, obj){
        var body = JSON.stringify(obj);
        return (ids ? Promise.resolve(ids) : loadIds()).then(function(m){
          if (m[name]) return api(GUP + '/files/' + m[name] + '?uploadType=media',
                                  {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: body});
          var b = 'narani' + Math.random().toString(36).slice(2);
          var meta = JSON.stringify({name: name, parents: [folder], mimeType: 'application/json'});
          return api(GUP + '/files?uploadType=multipart&fields=id', {method: 'POST',
            headers: {'Content-Type': 'multipart/related; boundary=' + b},
            body: '--' + b + '\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n' + meta +
                  '\r\n--' + b + '\r\nContent-Type: application/json\r\n\r\n' + body + '\r\n--' + b + '--'})
            .then(function(r){ return r.json(); }).then(function(d){ ids[name] = d.id; });
        });
      }
    };
  }
  takeRedirect();
  var source = CFG.local ? localSource() : (CFG.google_client_id ? driveSource() : null);
  function sourceProblem(err){
    // 저장소 오류 → 목록 위에 보일 안내와 단추
    var kind = err && err.kind;
    // 꺼진 망에서는 다시 연결도 못 한다 — 승인이 만료됐어도 오프라인 안내만
    if (navigator.onLine === false) return {text: md4T('오프라인 — 받아 둔 논문만 열려요')};
    if (source && source.drive && (kind === 'auth' || authError)){
      var first = true;
      try { first = !localStorage.getItem('narani:gconn'); } catch (e) {}
      return {text: first ? md4T('Google Drive에 연결해 데스크톱에서 보낸 논문을 받아요.') : md4T('Google Drive 연결이 만료됐어요.'),
              button: first ? md4T('Google Drive 연결') : md4T('다시 연결'), act: function(){ gSignIn(); }};
    }
    if (kind === 'nofolder') return {text: md4T('Drive에 Narani 폴더가 없어요. 데스크톱 앱에서 먼저 모바일로 보내 주세요.')};
    return {text: md4T('목록을 불러오지 못했어요. 인터넷 연결을 확인하세요.')};
  }

  // ---- 오프라인 캐시 (받은 목록·번들) ----
  function cacheGet(name){
    if (!window.caches) return Promise.resolve(null);
    return caches.open(DC).then(function(c){ return c.match('__data/' + name); })
      .then(function(r){ return r ? r.json() : null; }).catch(function(){ return null; });
  }
  function cachePut(name, obj){
    if (!window.caches) return Promise.resolve();
    return caches.open(DC).then(function(c){
      return c.put('__data/' + name, new Response(JSON.stringify(obj), {headers: {'Content-Type': 'application/json'}}));
    }).catch(function(){});
  }
  function haveMap(){ try { return JSON.parse(localStorage.getItem('narani:have') || '{}') || {}; } catch (e) { return {}; } }
  function setHave(file, hash){
    var m = haveMap(); if (hash) m[file] = hash; else delete m[file];
    try { localStorage.setItem('narani:have', JSON.stringify(m)); } catch (e) {}
  }
  function pruneCache(ix){
    // 목록에서 빠진 논문의 캐시는 지운다 (폰 저장 공간)
    var keep = {}, keys = {}, m = haveMap(), k;
    var keepA = {};
    ix.papers.forEach(function(e){
      keep[e.file] = 1; keep['notes/' + fileId(e) + '.json'] = 1; keys[e.key] = 1;
      if (audioOf(e)){ keep[e.audio.tl] = 1; keepA[audioKey(e.audio)] = 1; }
    });
    if (window.caches) caches.open(AC).then(function(c){
      return c.keys().then(function(reqs){
        reqs.forEach(function(q){ var u = new URL(q.url), k = decodeURIComponent(u.pathname + u.search).replace(/^.*?__audio\//, '__audio/'); if (!keepA[k]) c.delete(q); });
      });
    }).catch(function(){});
    for (k in m) if (!keep[k]) setHave(k, null);
    // 데스크톱이 모바일에서 뺀 논문 — 그 논문의 폰 기록도 지운다(다시 올리면 Drive에 주인 없는 파일이 남는다).
    // 데스크톱은 빼기 전에 올라와 있던 폰 기록을 합쳐 두었다.
    try {
      var ok = JSON.parse(localStorage.getItem('narani:opskeys') || '{}') || {}, changed = false;
      for (k in ok) if (!keys[k]){
        delete ok[k]; changed = true;
        localStorage.removeItem('narani:ops:' + k); localStorage.removeItem('narani:opsup:' + k);
      }
      if (changed) localStorage.setItem('narani:opskeys', JSON.stringify(ok));
    } catch (e) {}
    if (!window.caches) return;
    caches.open(DC).then(function(c){
      return c.keys().then(function(reqs){
        reqs.forEach(function(q){
          var name = decodeURIComponent(new URL(q.url).pathname).replace(/^.*?__data\//, '');
          if (name !== 'index.json' && !keep[name]) c.delete(q);
        });
      });
    }).catch(function(){});
  }
  function getPaper(e){
    return cacheGet(e.file).then(function(c){
      if (c && c.hash === e.hash) return c;
      if (!source){ if (c) return c; throw new Error('no source'); }
      return source.get(e.file).then(function(b){
        cachePut(e.file, b); setHave(e.file, b.hash); return b;
      }, function(err){ if (c) return c; throw err; });
    });
  }
  function getNotes(e){   // 데스크톱이 올린 하이라이트·메모 — 받아 둔 것과 지문(__h)이 같으면 캐시로
    if (!e.notes_hash) return Promise.resolve(null);
    var name = 'notes/' + fileId(e) + '.json';
    return cacheGet(name).then(function(c){
      if (c && c.__h === e.notes_hash) return c;
      if (!source) return c;
      return source.get(name).then(function(n){ n.__h = e.notes_hash; cachePut(name, n); return n; },
                                   function(){ return c; });
    });
  }

  // ---- 목록 ----
  function fileId(e){ return String(e.file || '').replace(/^papers\//, '').replace(/\.json$/, ''); }
  function entryOf(id){
    if (!INDEX) return null;
    for (var i = 0; i < INDEX.papers.length; i++) if (fileId(INDEX.papers[i]) === id) return INDEX.papers[i];
    return null;
  }
  function fmtDate(sec){
    var d = new Date(sec * 1000);
    try { return d.toLocaleString(md4Lang, {dateStyle: 'medium', timeStyle: 'short'}); } catch (e) { return d.toLocaleString(); }
  }
  function el(tag, cls, text){ var n = doc.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function renderLib(note){
    var have = haveMap(), n = 0;
    libList.innerHTML = '';
    doc.getElementById('lib-h').textContent = INDEX ? md4T('논문 {n}편', {n: INDEX.papers.length}) : APP;
    (INDEX ? INDEX.papers : []).forEach(function(e){
      var off = have[e.file] === e.hash; if (off) n++;
      var li = el('li', OFFLINE && !have[e.file] ? 'na' : ''), a = el('a');
      a.href = '#!' + fileId(e); a.setAttribute('data-open', fileId(e));
      // 카드 — 제목 / 원제 / 저자·연도·학회 한 줄(넘치면 …) / 작은 꼬리표 줄(번역·음성 길이·받아 둠)
      a.appendChild(el('span', 'li-title', e.title_ko || e.title));
      if (e.title_ko) a.appendChild(el('span', 'li-en', e.title));
      var when = [e.year, e.venue].filter(function(x){ return x; }).join(' · ');   // 저자만 줄이고 연도·학회는 늘 보이게
      if (e.authors || when){
        var by = el('span', 'li-by');
        if (e.authors) by.appendChild(el('span', 'who', e.authors));
        if (e.authors && when) by.appendChild(el('span', 'sep', '·'));
        if (when) by.appendChild(el('span', 'when', when));
        a.appendChild(by);
      }
      var tags = el('span', 'li-tags');
      tags.appendChild(el('span', 'tag', e.has_ko ? md4T('번역') : md4T('원문만')));
      if (audioOf(e)){
        var au = el('span', 'tag au');
        au.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M12 3a9 9 0 0 0-9 9v6a3 3 0 0 0 3 3h1a1 1 0 0 0 1-1v-6a1 1 0 0 0-1-1H5v-1a7 7 0 0 1 14 0v1h-2a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h1a3 3 0 0 0 3-3v-6a9 9 0 0 0-9-9z"/></svg>';
        au.appendChild(doc.createTextNode(fmtT(e.audio.duration)));
        tags.appendChild(au);
      }
      if (off) tags.appendChild(el('span', 'tag dl', '⤓'));   // 이 기기에 받아 둔 논문
      if (li.className === 'na') tags.appendChild(el('span', 'tag na-t', md4T('받아 두지 않음')));
      a.appendChild(tags); li.appendChild(a); libList.appendChild(li);
    });
    if (INDEX && !INDEX.papers.length) libList.appendChild(el('li', 'empty', md4T('올린 논문이 없어요. 데스크톱 앱에서 모바일로 보내 주세요.')));
    libBar.innerHTML = '';
    var info = [];
    if (INDEX && INDEX.updated) info.push(md4T('{date}에 올린 목록', {date: fmtDate(INDEX.updated)}));
    if (SAVING) info.push(md4T('저장 중… {n}/{m}', {n: SAVING.done, m: SAVING.total}));
    else if (INDEX && INDEX.papers.length) info.push(md4T('오프라인 {n}/{m}편', {n: n, m: INDEX.papers.length}));
    if (OFFLINE && !note) note = md4T('오프라인 — 받아 둔 논문만 열려요');
    if (note) info.push(note);
    libBar.appendChild(el('span', 'grow', info.join(' · ')));
    if (source){
      var re = el('button', '', md4T('새로 고침')); re.onclick = function(){ loadIndex(true); };
      libBar.appendChild(re);
    }
    if (INDEX && INDEX.papers.length && source){
      // 오프라인 모드 — 켜 두면 모든 논문(과 데스크톱의 하이라이트·메모)을 받아 두고, 목록을 새로 받을 때마다
      // 새로 올라온·바뀐 것만 더 받는다. 끄면 더 받지 않을 뿐 받아 둔 것은 그대로.
      var lab = el('label', 'auto'), cb = el('input');
      cb.type = 'checkbox'; cb.checked = autoSave();
      cb.onchange = function(){
        try { localStorage.setItem('narani:auto', cb.checked ? '1' : '0'); } catch (e) {}
        renderLib();   // '음성도 받아 두기'가 바로 보이게
        if (cb.checked) saveOffline();
      };
      lab.appendChild(cb); lab.appendChild(doc.createTextNode(md4T('오프라인 모드 — 모두 받아 두기')));
      libBar.appendChild(lab);
      var withAudio = INDEX.papers.filter(function(e){ return audioOf(e); });
      if (withAudio.length && cb.checked){   // 음성은 편당 십수 MB라 따로 고른다
        var mb = withAudio.reduce(function(n, e){ return n + (e.audio.bytes || 0); }, 0) / 1e6;
        var la = el('label', 'auto'), ca = el('input');
        ca.type = 'checkbox'; ca.checked = autoAudio();
        ca.onchange = function(){
          try { localStorage.setItem('narani:autoaudio', ca.checked ? '1' : '0'); } catch (e) {}
          if (ca.checked) saveOffline();
        };
        la.appendChild(ca); la.appendChild(doc.createTextNode(md4T('음성도 받아 두기 ({n}편 · {mb}MB — 와이파이에서)', {n: withAudio.length, mb: Math.round(mb)})));
        libBar.appendChild(la);
      }
    }
  }
  function autoAudio(){ try { return localStorage.getItem('narani:autoaudio') === '1'; } catch (e) { return false; } }
  function autoSave(){ try { return localStorage.getItem('narani:auto') === '1'; } catch (e) { return false; } }
  var SAVING = null, OFFLINE = false;
  function saveOffline(){
    if (!INDEX || !source || SAVING || OFFLINE) return;
    var have = haveMap(), todo = INDEX.papers.filter(function(e){ return have[e.file] !== e.hash; }), failed = 0;
    var all = INDEX.papers.slice();
    try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (e) {}
    SAVING = {done: 0, total: todo.length};
    if (todo.length) renderLib();
    (function next(){
      if (!todo.length){   // 논문을 다 받았으면 하이라이트·메모 파일 — 바뀐 것만 받는다
        Promise.all(all.map(function(e){ return getNotes(e).catch(function(){}); })).then(function(){
          return autoAudio() ? saveAudio(all) : 0;
        }).then(function(n){
          var had = SAVING.total || n; SAVING = null;
          if (had) renderLib(failed ? md4T('{n}편은 받지 못했어요', {n: failed}) : '');
        });
        return;
      }
      var e = todo.shift();
      getPaper(e).catch(function(){ failed++; }).then(function(){
        SAVING.done++; if (lib && !lib.classList.contains('off')) renderLib(); next();
      });
    })();
  }
  function saveAudio(list){   // 받아 두지 않은 음성과 시간표 — 한 편씩
    var todo = list.filter(function(e){ return audioOf(e); }), got = 0;
    return (function next(){
      if (!todo.length) return Promise.resolve(got);
      var e = todo.shift(), a = audioOf(e);
      return audioCached(a).then(function(yes){
        if (yes) return;
        SAVING.total++; if (lib && !lib.classList.contains('off')) renderLib();
        return audioTl(a).then(function(){ return audioBlob(a); }).then(function(){ got++; }, function(){});
      }).then(function(){ SAVING.done++; return next(); });
    })();
  }
  function addAct(pr){
    if (!pr || !pr.button) return;
    var b = el('button', '', pr.button); b.onclick = pr.act; libBar.appendChild(b);
  }
  function showIndex(ix, note, pr){
    INDEX = ix; renderLib(note); addAct(pr);
    if (!routed){ routed = true; route(); }
  }
  function loadIndex(force){
    if (!force) libBar.textContent = md4T('불러오는 중…');
    return cacheGet('index.json').then(function(cached){
      if (cached && !force) showIndex(cached);
      if (!source){
        if (!cached) libBar.textContent = md4T('연결된 저장소가 없어요.');
        return;
      }
      return source.get('index.json').then(function(ix){
        OFFLINE = false;
        cachePut('index.json', ix); setUiLang(ix.ui_lang); pruneCache(ix); showIndex(ix); flushPending();
        if (autoSave()) saveOffline();
      }, function(err){
        var pr = sourceProblem(err);
        OFFLINE = navigator.onLine === false || (!pr.button && !!err && !err.kind);   // 망 오류(인증·폴더 문제가 아니라)
        if (cached) showIndex(cached, OFFLINE ? '' : pr.text, pr);
        else { libBar.innerHTML = ''; libBar.appendChild(el('span', 'grow', pr.text)); addAct(pr); }
      });
    });
  }

  // ---- 목록 ↔ 논문 ----
  function unmount(){ if (cur){ cur.parentNode.removeChild(cur); cur = null; } }
  function showLib(push){
    loadSeq++; unmount(); loading.style.display = 'none'; readerClosed();
    lib.classList.remove('off'); topTitle.textContent = APP;
    closeSheet(); window.scrollTo(0, 0);
    if (push) history.pushState(null, '', '#!lib');
  }
  function mount(b, target){
    main.insertAdjacentHTML('beforeend', b.html);
    cur = doc.getElementById(PID); cur.classList.add('on');
    DATA[PID] = {key: b.key, title: b.title_ko || b.title, cites: b.cites || {}, fns: b.fns || {}};
    readerOpened(b);
    var t = target ? doc.getElementById(target) : null, off = 0;
    if (!t){
      var saved = load('pos:' + b.key, '').split(':');
      t = saved[0] ? doc.getElementById(PID + '-' + saved[0]) : null; off = +saved[1] || 0;
    }
    if (t){ t.scrollIntoView({block: 'start'}); if (off) window.scrollBy(0, off); } else window.scrollTo(0, 0);
  }
  function openPaper(id, push, target){
    if (id === PID){ if (cur) window.scrollTo(0, 0); return !!cur; }
    var e = entryOf(id);
    if (!e) return false;
    var seq = ++loadSeq;
    unmount(); closeSheet(); readerClosed();
    lib.classList.add('off'); topTitle.textContent = e.title_ko || e.title;
    loading.style.display = ''; loading.textContent = md4T('불러오는 중…');
    window.scrollTo(0, 0);
    if (push) history.pushState(null, '', '#!' + id);
    getPaper(e).then(function(b){
      if (seq !== loadSeq) return;
      loading.style.display = 'none'; mount(b, target);
      notesOpen(e, b);
      audioOpened(e);
      if (INDEX) renderLib();
    }, function(err){
      if (seq !== loadSeq) return;
      if (err && err.kind === 'auth'){
        loading.textContent = md4T('Google Drive 연결이 만료됐어요.') + ' ';
        var b = el('button', '', md4T('다시 연결')); b.onclick = function(){ gSignIn(); };
        loading.appendChild(b);
      } else loading.textContent = md4T('이 논문을 받지 못했어요. 인터넷에 연결된 뒤 다시 열어 보세요.');
    });
    return true;
  }
  try { history.scrollRestoration = 'manual'; } catch (e) {}
  function route(){
    var h = location.hash.replace(/^#!?/, '');
    if (h && h !== 'lib' && openPaper(h, false)) return;
    showLib(false);
  }
  doc.getElementById('back').onclick = function(){ showLib(true); };
  window.addEventListener('popstate', route);
  libList.addEventListener('click', function(e){
    var a = e.target.closest ? e.target.closest('a[data-open]') : null;
    if (!a) return;
    e.preventDefault(); e.stopPropagation();
    if (a.parentNode.classList.contains('na')) return;
    openPaper(a.getAttribute('data-open'), true);
  });
  var saveTimer = null;
  window.addEventListener('scroll', function(){
    if (!cur || saveTimer) return;
    saveTimer = setTimeout(function(){
      saveTimer = null;
      var r = topRow(); if (!r || !cur) return;
      var into = Math.max(0, Math.round(64 - r.getBoundingClientRect().top));
      store('pos:' + cur.getAttribute('data-key'), r.id.replace(/^p\d+-/, '') + ':' + into);
    }, 400);
  }, {passive: true});

  // 망이 끊기면 목록에 바로 표시하고, 다시 붙으면 목록을 새로 받는다(적어 둔 기록도 이때 올라간다)
  window.addEventListener('offline', function(){ OFFLINE = true; if (INDEX) renderLib(); });
  window.addEventListener('online', function(){ loadIndex(true); });

  labels(); topTitle.textContent = APP;
  // ---- 새 판 ----
  // 홈 화면 앱은 닫았다 열어도 메모리에서 되살아나 새 판을 확인하지 않는다 — 다시 보일 때마다 확인하고,
  // 새 판이 자리 잡으면 목록에선 바로 다시 열고, 읽는 중이면 위에 '새 판 적용' 막대를 띄운다(듣기·쓰는 메모를 끊지 않게).
  if ('serviceWorker' in navigator){
    var hadSW = !!navigator.serviceWorker.controller, swReg = null, lastCheck = 0;
    navigator.serviceWorker.register('sw.js', {updateViaCache: 'none'}).then(function(r){ swReg = r; }, function(){});
    doc.addEventListener('visibilitychange', function(){
      if (doc.visibilityState !== 'visible' || !swReg || Date.now() - lastCheck < 60000) return;
      lastCheck = Date.now(); swReg.update().catch(function(){});
    });
    navigator.serviceWorker.addEventListener('controllerchange', function(){
      if (!hadSW){ hadSW = true; return; }               // 처음 설치 — 이미 새 판이다
      var busy = cur || (au && !au.paused) || sheet.classList.contains('open');
      if (!busy){ location.reload(); return; }
      var bar = doc.getElementById('upd');
      bar.textContent = '';
      bar.appendChild(doc.createTextNode(md4T('새 판이 준비됐어요') + ' '));
      bar.appendChild(button(md4T('적용'), function(){ location.reload(); }));
      bar.classList.add('on');
    });
  }
  loadIndex(false);
"""

# 하이라이트·메모 — 문장을 누르면 아래 시트에 색과 메모 단추(데스크톱 뷰어의 '문장 통째로' 표시와 같은 모양:
# 원문·번역 짝 문장에 앵커 둘). 폰은 자기가 한 일만 기록(ops)으로 남겨 mobile/<id>.json에 올리고, 데스크톱이
# 올린 notes/<id>.json(annotations.json 그대로 + applied)을 받아 그 위에 기록을 얹어 보여 준다(§phone.notes).
# 기록은 이 폰(localStorage)에 먼저 남으므로 오프라인에서도 쓰고, 연결되면 올린다.
_NOTES_JS = r"""
  var COLORS = ['yellow', 'green', 'blue', 'pink', 'purple'], ACTX = 40;
  var NOTE = {key: null, fid: null, base: [], applied: 0, lost: []};
  function lsGet(k, d){ try { var v = JSON.parse(localStorage.getItem(k) || 'null'); return v == null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function ops(key){ return lsGet('narani:ops:' + key, []); }
  function upTs(key){ return lsGet('narani:opsup:' + key, 0); }
  function lastTs(list){ return list.length ? list[list.length - 1].ts : 0; }
  function pendingCount(key){ var t = upTs(key); return ops(key).filter(function(o){ return o.ts > t; }).length; }

  function items(){   // 데스크톱 것 위에 아직 합쳐지지 않은 폰 기록을 얹는다
    var map = {}, order = [];
    NOTE.base.forEach(function(it){ map[it.id] = it; order.push(it.id); });
    ops(NOTE.key).forEach(function(o){
      if (o.ts <= NOTE.applied) return;
      if (o.op === 'del'){ delete map[o.id]; return; }
      if (!(o.item.id in map)) order.push(o.item.id);
      map[o.item.id] = o.item;
    });
    return order.filter(function(id){ return map[id]; }).map(function(id){ return map[id]; });
  }

  // ---- 위치 찾기 (데스크톱 뷰어의 resolve와 같은 순서: 제자리 → 앞뒤 문맥째 → 가장 가까운 같은 문구) ----
  function resolve(txt, a){
    var q = a.quote || '';
    if (!q) return null;
    if (txt.substr(a.start, q.length) === q) return [a.start, a.start + q.length];
    var probe = (a.prefix || '') + q + (a.suffix || ''), i = txt.indexOf(probe);
    if (i >= 0) return [i + (a.prefix || '').length, i + (a.prefix || '').length + q.length];
    var best = -1, bd = Infinity, from = 0, j;
    while ((j = txt.indexOf(q, from)) >= 0){ var d = Math.abs(j - a.start); if (d < bd){ bd = d; best = j; } from = j + 1; }
    return best >= 0 ? [best, best + q.length] : null;
  }
  function cellAt(row, side){
    var r = doc.getElementById(PID + '-r' + row);
    return r ? (r.querySelector('.cell.' + side) || r.querySelector('.cell.same')) : null;
  }
  var TXT = null;   // 칠하는 동안만 쓰는 셀 평문 표 (셀마다 textContent를 거듭 세지 않게)
  function textOf(c){
    if (!TXT) return c.textContent;
    for (var i = 0; i < TXT.length; i++) if (TXT[i].c === c) return TXT[i].t;
    return c.textContent;
  }
  function locate(a){
    for (var d = 0; d <= 3; d++){        // 행이 조금 밀렸으면 이웃 행에서
      var rows = d ? [a.row - d, a.row + d] : [a.row];
      for (var k = 0; k < rows.length; k++){
        var c = cellAt(rows[k], a.side);
        if (!c) continue;
        var hit = resolve(textOf(c), a);
        if (hit) return {cell: c, s: hit[0], e: hit[1]};
      }
    }
    // 그래도 없으면 같은 쪽 셀 전체에서 문구로 — 다시 번역·조립해 행이 크게 밀렸거나, 원문·번역 절 수가
    // 달라 나란히 맞추지 못한 논문(번역 셀이 원문 뒤에 따로 붙는다). 원래 행에 가장 가까운 것.
    if (!cur || !a.quote) return null;
    var cells = cur.querySelectorAll('.cell.' + a.side + ', .cell.same'), best = null, bd = Infinity;
    for (var i = 0; i < cells.length; i++){
      var t = textOf(cells[i]), probe = (a.prefix || '') + a.quote + (a.suffix || ''), j = t.indexOf(probe), s;
      if (j >= 0) s = j + (a.prefix || '').length;
      else if ((j = t.indexOf(a.quote)) >= 0) s = j;
      else continue;
      var rn = +cells[i].parentElement.id.replace(/^p\d+-r/, ''), dd = Math.abs(rn - a.row) + (j === s ? 0 : 0.5);
      if (dd < bd){ bd = dd; best = {cell: cells[i], s: s, e: s + a.quote.length}; }
    }
    return best;
  }

  // ---- 칠하기 ----
  function unpaint(){
    if (!cur) return;
    var ms = cur.querySelectorAll('mark.anno'), parents = [];
    for (var i = 0; i < ms.length; i++){
      var m = ms[i], p = m.parentNode;
      while (m.firstChild) p.insertBefore(m.firstChild, m);
      p.removeChild(m);
      if (parents.indexOf(p) < 0) parents.push(p);
    }
    parents.forEach(function(p){ p.normalize(); });
  }
  function paint(){
    if (!cur || !NOTE.key) return;
    unpaint();
    TXT = [].map.call(cur.querySelectorAll('.cell'), function(c){ return {c: c, t: c.textContent}; });
    var list = items(), groups = [], lost = [];
    list.forEach(function(it){
      var placed = false;
      (it.anchors || []).forEach(function(a){
        var h = locate(a);
        if (!h) return;
        var g = null;
        for (var i = 0; i < groups.length; i++) if (groups[i].cell === h.cell){ g = groups[i]; break; }
        if (!g){ g = {cell: h.cell, segs: []}; groups.push(g); }
        // 겹치는 표시는 먼저 온 것만 칠한다 (겹쳐 감싸면 마크가 엉킨다)
        for (i = 0; i < g.segs.length; i++) if (g.segs[i].s < h.e && h.s < g.segs[i].e) return;
        g.segs.push({s: h.s, e: h.e, it: it});
        placed = true;
      });
      if (!placed) lost.push(it);
    });
    TXT = null;
    groups.forEach(function(g){
      wrapRanges(g.cell, g.segs, function(seg){
        var m = doc.createElement('mark');
        m.className = 'anno c-' + (COLORS.indexOf(seg.it.color) >= 0 ? seg.it.color : 'yellow');
        m.setAttribute('data-aid', seg.it.id);
        return m;
      });
    });
    list.forEach(function(it){    // 메모가 있으면 셀(원문·번역)마다 마지막 조각 뒤에 ✎ — 보기 모드에 따라 한쪽만 보이므로
      if (!it.note) return;
      var ms = cur.querySelectorAll('mark.anno[data-aid="' + it.id + '"]'), last = [];
      for (var i = 0; i < ms.length; i++){
        var c = ms[i].closest('.cell'), k;
        for (k = 0; k < last.length; k++) if (last[k].c === c) break;
        if (k < last.length) last[k].m = ms[i]; else last.push({c: c, m: ms[i]});
      }
      last.forEach(function(x){ x.m.classList.add('note-end'); });
    });
    NOTE.lost = lost.map(function(it){ return it.id; });
    notesChanged();
  }

  // ---- 내 메모 목록 — 떠 있는 단추의 '메모' 탭 (본문 순서로; 누르면 그 자리로) ----
  function rowOf(it){ var a = (it.anchors || [])[0]; return a ? +a.row || 0 : 0; }
  function notesChanged(){   // 표시가 바뀌면: 단추의 개수 · 열려 있는 메모 탭
    var n = NOTE.key ? items().length : 0, b = doc.getElementById('fab-n');
    b.textContent = n > 99 ? '99+' : String(n); b.classList.toggle('on', n > 0);
    if (agent.classList.contains('open') && AG.mode === 'notes') showNotes(); else setTabs();
  }
  function jumpTo(id){
    closeDrawers();
    var ms = cur ? cur.querySelectorAll('mark.anno[data-aid="' + id + '"]') : [], m = null;
    for (var i = 0; i < ms.length; i++) if (ms[i].offsetParent){ m = ms[i]; break; }   // 지금 보기(번역·원문)에 보이는 쪽
    m = m || ms[0];
    if (!m) return;
    m.scrollIntoView({block: 'center'});
    var r = m.closest('.row'); if (r){ r.classList.remove('flash'); void r.offsetWidth; r.classList.add('flash'); }
  }
  function showNotes(){
    AG.mode = 'notes'; setTabs(); agBody.innerHTML = '';
    doc.getElementById('ag-form').style.display = 'none';
    var list = items().slice().sort(function(a, b){ return rowOf(a) - rowOf(b); });
    var pend = pendingCount(NOTE.key), only = lsGet('narani:notesonly', false);
    var withNote = list.filter(function(it){ return it.note; }).length;
    var bar = el('div', 'nl-bar'), info = [md4T('하이라이트·메모 {n}개', {n: list.length})];
    if (pend) info.push(md4T('올릴 기록 {n}개', {n: pend}));
    bar.appendChild(el('span', 'grow', info.join(' · ')));
    if (pend && source && source.drive && !gToken() && navigator.onLine !== false)   // 연결이 끊겨 못 올린 기록
      bar.appendChild(button(md4T('다시 연결'), function(){ gSignIn(); }));
    if (withNote && withNote < list.length){
      var f = button(md4T('메모만'), function(){ lsSet('narani:notesonly', !only); showNotes(); });
      if (only) f.className = 'on';
      bar.appendChild(f);
    } else only = false;
    agBody.appendChild(bar);
    if (only) list = list.filter(function(it){ return it.note; });
    if (!list.length){
      agBody.appendChild(el('p', 'hint', source && source.put
        ? md4T('아직 표시한 문장이 없어요. 본문의 문장을 누르면 색을 칠하거나 메모를 달 수 있어요.')
        : md4T('저장소에 연결해야 하이라이트·메모를 남길 수 있어요.')));
      return;
    }
    var side = doc.getElementById('m-en').checked ? 'en' : 'ko', ol = el('ol', 'nl');
    list.forEach(function(it){
      var an = it.anchors || [], a = an.filter(function(x){ return x.side === side; })[0] || an[0] || {};
      var q = a.quote || '', li = el('li');
      li.appendChild(el('span', 'aq c-' + (COLORS.indexOf(it.color) >= 0 ? it.color : 'yellow'),
                        q.length > 160 ? q.slice(0, 160) + '…' : q));
      if (it.note) li.appendChild(el('span', 'an', it.note));
      if (NOTE.lost.indexOf(it.id) >= 0) li.appendChild(el('span', 'al', md4T('(본문에서 위치를 찾지 못했어요)')));
      else li.onclick = function(){ jumpTo(it.id); };
      ol.appendChild(li);
    });
    agBody.appendChild(ol);
  }

  // ---- 열기 · 받기 ----
  function notesOpen(e, b){
    NOTE = {key: b.key, fid: fileId(e), base: [], applied: 0, lost: []};
    paint();
    var key = b.key;
    getNotes(e).then(function(n){
      if (!n || NOTE.key !== key) return;
      NOTE.base = n.items || []; NOTE.applied = +n.applied || 0;
      var mine = ops(key), left = mine.filter(function(o){ return o.ts > NOTE.applied; });
      if (left.length !== mine.length) lsSet('narani:ops:' + key, left);   // 데스크톱이 합친 기록은 지운다
      paint();
    });
  }

  // ---- 기록 남기기 · 올리기 ----
  function record(op){
    var list = ops(NOTE.key), t = Date.now() / 1000;
    op.ts = Math.max(t, lastTs(list) + 0.001);
    list.push(op);
    lsSet('narani:ops:' + NOTE.key, list);
    var keys = lsGet('narani:opskeys', {}); keys[NOTE.key] = NOTE.fid; lsSet('narani:opskeys', keys);
    paint();
    upload(NOTE.key, NOTE.fid);
  }
  var uploading = {};
  function upload(key, fid){
    if (!source || !source.put || uploading[key]) return;
    var list = ops(key);
    if (!list.length || lastTs(list) <= upTs(key)) return;
    uploading[key] = true;
    var top = lastTs(list);
    source.put('mobile/' + fid + '.json', {format: 1, key: key, ops: list}).then(function(){
      lsSet('narani:opsup:' + key, top);
    }, function(){}).then(function(){
      uploading[key] = false;
      if (NOTE.key === key && cur) notesChanged();
      if (lastTs(ops(key)) > upTs(key) && lastTs(ops(key)) > top) upload(key, fid);   // 올리는 사이 새로 적은 것
    });
  }
  function flushPending(){   // 오프라인일 때 적어 둔 기록 — 목록을 새로 받으면(연결되면) 올린다
    var keys = lsGet('narani:opskeys', {});
    for (var k in keys) upload(k, keys[k]);
  }
  window.addEventListener('online', flushPending);

  // ---- 문장 시트의 동작 (CORE_JS의 onSentence가 부른다) ----
  function spanOf(list, idx){
    if (!idx || !idx.length) return null;
    var s = list[idx[0]].s, e = list[idx[0]].e;
    for (var i = 1; i < idx.length; i++){ s = Math.min(s, list[idx[i]].s); e = Math.max(e, list[idx[i]].e); }
    return [s, e];
  }
  function anchorOf(cell, row, side, sp){
    var txt = cell.textContent;
    return {side: side, row: row, start: sp[0], end: sp[1], quote: txt.slice(sp[0], sp[1]),
            prefix: txt.slice(Math.max(0, sp[0] - ACTX), sp[0]), suffix: txt.slice(sp[1], sp[1] + ACTX)};
  }
  function itemAt(row, side, sp){
    var list = items();
    for (var i = 0; i < list.length; i++){
      var an = list[i].anchors || [];
      for (var k = 0; k < an.length; k++){
        if (an[k].side !== side) continue;
        var h = locate(an[k]);
        if (h && h.cell === cellAt(row, side) && h.s < sp[1] && sp[0] < h.e) return list[i];
      }
    }
    return null;
  }
  // 재생 막대가 떠 있으면(듣는 중·멈춤) 문장을 누르는 게 곧 '여기부터 듣기' — 시트 없이 바로. 표시·메모는 막대를 닫고
  function sentenceTap(e, cell){
    if (!body.classList.contains('listening') || TK || !audioOf(AU.e)) return false;
    if (cell.parentElement.classList.contains('tts-off')){ clearSent(); return true; }   // 읽지 않는 행은 눌러도 그대로
    var rowEl = cell.parentElement, side = cell.classList.contains('ko') ? 'ko' : 'en', off = null;
    var pr = rowEl.querySelector('.cell.en') && rowEl.querySelector('.cell.ko') ? prepare(rowEl) : null;
    var pi = pr ? sentAt(e, cell, pr, side) : null;
    if (pi !== null && pi !== undefined){
      var sp = spanOf(pr[side], pr.pairs[+pi][side === 'en' ? 0 : 1]);
      var aside = AU.tl ? AU.tl.side : (AU.e.has_ko ? 'ko' : 'en');
      off = sp && side === aside ? sp[0] : null;
    }
    clearSent(); listenFrom(+rowEl.id.replace(/^p\d+-r/, ''), off);
    return true;
  }
  function sentenceActs(rowEl, pi, pr, side){
    var acts = notesActs(rowEl, pi, pr, side), a = audioOf(AU.e);
    if (!a) return acts;
    var sp = spanOf(pr[side], pr.pairs[pi][side === 'en' ? 0 : 1]), row = +rowEl.id.replace(/^p\d+-r/, '');
    var aside = AU.tl ? AU.tl.side : (AU.e.has_ko ? 'ko' : 'en');   // 음성이 읽는 쪽 — 다른 쪽을 눌렀으면 그 행 처음부터
    var off = sp && side === aside ? sp[0] : null;
    acts = acts || {nodes: [], buttons: []};
    acts.buttons = [button(md4T('여기부터 듣기'), function(){ clearSent(); listenFrom(row, off); })].concat(acts.buttons);
    return acts;
  }
  function notesActs(rowEl, pi, pr, side){
    if (!source || !source.put || !NOTE.key) return null;
    var row = +rowEl.id.replace(/^p\d+-r/, '');
    var spans = {en: spanOf(pr.en, pr.pairs[pi][0]), ko: spanOf(pr.ko, pr.pairs[pi][1])};
    if (!spans[side]) return null;
    var it = itemAt(row, side, spans[side]);
    function fresh(color, note){
      var an = [];
      ['en', 'ko'].forEach(function(s){
        var c = rowEl.querySelector('.cell.' + s);
        if (c && spans[s]) an.push(anchorOf(c, row, s, spans[s]));
      });
      return {id: 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), color: color,
              note: note || '', anchors: an, created: Date.now() / 1000};
    }
    var nodes = [], dots = el('div', 'anno-dots');
    if (it && it.note) nodes.push(para(it.note, 'anno-note'));
    COLORS.forEach(function(c){
      var b = el('button', 'dot c-' + c + (it && it.color === c ? ' on' : ''));
      b.setAttribute('aria-label', c);
      b.onclick = function(){
        record({op: 'put', item: it ? Object.assign({}, it, {color: c}) : fresh(c)});
        clearSent(); closeSheet();
      };
      dots.appendChild(b);
    });
    nodes.push(dots);
    var buttons = [button(it && it.note ? md4T('메모 고치기') : md4T('메모 달기'), function(){ editNote(it, fresh); })];
    if (it) buttons.push(button(md4T('지우기'), function(){ record({op: 'del', id: it.id}); clearSent(); closeSheet(); }));
    return {nodes: nodes, buttons: buttons};
  }
  // 쓰는 중인 메모 — 시트가 어떻게 닫히든('저장'·'닫기'·바깥 누르기) 바뀐 글은 저장한다. 폰에서는 키보드가
  // 시트 아래쪽을 가려 '저장'을 못 누르고 바깥을 눌러 닫는 일이 흔하다 — 그때 쓴 글을 잃지 않게.
  var noteDraft = null;
  function beforeSheetClose(){
    var d = noteDraft; noteDraft = null;
    if (!d) return;
    var note = d.ta.value.replace(/\s+$/, '');
    if (note === d.orig) return;
    if (d.it) record({op: 'put', item: Object.assign({}, d.it, {note: note})});
    else if (note) record({op: 'put', item: d.fresh('yellow', note)});
  }
  function editNote(it, fresh){
    var ta = el('textarea', 'anno-ta');
    ta.value = (it && it.note) || ''; ta.rows = 4; ta.placeholder = md4T('메모를 적으세요');
    var save = button(md4T('저장'), function(){ clearSent(); closeSheet(); });   // 저장은 beforeSheetClose가
    openSheet(md4T('메모'), [ta], [save]);
    noteDraft = {it: it, fresh: fresh, ta: ta, orig: (it && it.note) || ''};
    setTimeout(function(){ ta.focus(); }, 50);
  }
"""


# 읽기 화면의 둘레 — 상단 막대의 글자 크기 팝오버, 왼쪽 목차 서랍, 떠 있는 에이전트 단추와 대화 창.
# 에이전트(§phone 결정 2026-10-26): 지금 보는 논문 하나를 다룬다. 서버가 없으므로 사용자가 이 기기에 넣은 자기 AI 키로
# 브라우저가 AI 서비스(OpenAI·Anthropic·Gemini — 셋 다 브라우저 요청을 받는다)에 직접 묻는다. 키는 이 기기의
# localStorage에만 둔다. 보내는 것: 이 논문의 원문(행마다 [rN] 표식)과 내 하이라이트·메모, 대화. 답의 [rN]을 누르면 그 행으로.
_READER_JS = r"""
  // ---- 글자 크기 팝오버 ----
  var fsPop = doc.getElementById('fs-pop');
  doc.getElementById('fs-btn').onclick = function(e){ e.stopPropagation(); fsPop.classList.toggle('open'); };
  doc.addEventListener('click', function(e){
    if (!e.target.closest || !e.target.closest('#fs-pop, #fs-btn')) fsPop.classList.remove('open');
  }, true);

  // ---- 서랍 공통 ----
  var scrim = doc.getElementById('scrim'), tocd = doc.getElementById('tocd'), agent = doc.getElementById('agent');
  function closeDrawers(){
    tocd.classList.remove('open'); agent.classList.remove('open'); scrim.classList.remove('open');
    try { doc.getElementById('ag-in').blur(); } catch (e) {}
  }
  scrim.onclick = closeDrawers;
  doc.getElementById('tocd-x').onclick = closeDrawers;
  doc.getElementById('ag-x').onclick = closeDrawers;
  [tocd, agent].forEach(function(d){ d.addEventListener('click', function(e){ e.stopPropagation(); }); });
  function readerOpened(b){ body.classList.add('reading'); AG.key = b.key; AG.lang = b.lang || 'ko'; AG.title = b.title_ko || b.title; AG.msgs = null; }
  function readerClosed(){ body.classList.remove('reading'); closeDrawers(); if (TK) talkEnd(md4T('논문을 닫음'), true); stopAudio(); }
  function flashRow(row){
    if (!row) return;
    row.scrollIntoView({block: 'start'});
    row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
  }

  // ---- 목차 서랍 — 논문 머리의 목차를 그대로, 지금 읽는 절을 표시 ----
  function currentSection(){
    var r = topRow(); if (!r || !cur) return null;
    var rows = [].slice.call(cur.querySelectorAll('.row')), idx = rows.indexOf(r), best = null;
    [].forEach.call(cur.querySelectorAll('.toc a'), function(a){
      var id = (a.getAttribute('href') || '').slice(1), t = doc.getElementById(id);
      if (t && rows.indexOf(t) <= idx) best = id;
    });
    return best;
  }
  function openToc(){
    if (!cur) return;
    var list = doc.getElementById('tocd-list'), here = currentSection();
    list.innerHTML = '';
    doc.getElementById('tocd-title').textContent = (DATA[PID] || {}).title || md4T('목차');
    [].forEach.call(cur.querySelectorAll('.toc li'), function(li){
      var a = li.querySelector('a'); if (!a) return;
      var id = (a.getAttribute('href') || '').slice(1), item = el('li', li.className), b = el('a', '', a.textContent);
      b.href = '#';
      if (id === here) item.classList.add('here');
      b.onclick = function(ev){ ev.preventDefault(); closeDrawers(); flashRow(doc.getElementById(id)); };
      item.appendChild(b); list.appendChild(item);
    });
    if (!list.children.length) list.appendChild(el('li', 'empty', md4T('목차가 없는 논문이에요.')));
    tocd.classList.add('open'); scrim.classList.add('open');
    var h = list.querySelector('.here'); if (h) h.scrollIntoView({block: 'center'});
  }
  topTitle.onclick = function(e){ if (!cur) return; e.stopPropagation(); openToc(); };

  // ---- 번역 ⇄ 원문 토글 (대조는 화면에서 뺐다 — 문장을 누르면 짝 문장이 뜬다) ----
  if (doc.getElementById('m-both').checked){ doc.getElementById('m-ko').checked = true; store('mode', 'ko'); }
  function langLabel(){
    var en = doc.getElementById('m-en').checked;
    setText('lang-btn', (en ? md4T('원문') : md4T('번역')) + ' ⇄');
    doc.getElementById('lang-btn').setAttribute('aria-label', en ? md4T('번역으로 보기') : md4T('원문으로 보기'));
  }
  doc.getElementById('lang-btn').onclick = function(e){
    e.stopPropagation();
    modeAnchor = topRow();   // 바꾸기 전 화면 맨 위 문단 — 바꾼 뒤 그 문단으로 (CORE_JS의 change 처리)
    var to = doc.getElementById('m-en').checked ? 'm-ko' : 'm-en', r = doc.getElementById(to);
    r.checked = true; r.dispatchEvent(new Event('change'));
    langLabel();
  };

  // ---- 키보드 높이 — iOS는 키보드가 올라와도 아래에 붙은 창을 밀어 올리지 않는다 ----
  function fitKeyboard(){
    var vv = window.visualViewport; if (!vv) return;
    var kb = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
    doc.documentElement.style.setProperty('--kb', kb + 'px');
  }
  if (window.visualViewport){ visualViewport.addEventListener('resize', fitKeyboard); visualViewport.addEventListener('scroll', fitKeyboard); }

  // ---- 에이전트 ----
  var AG = {key: null, lang: 'ko', title: '', msgs: null, busy: false, mode: 'chat'};
  var agBody = doc.getElementById('ag-body'), agIn = doc.getElementById('ag-in'), agSend = doc.getElementById('ag-send');
  var PNAME = {openai: 'OpenAI', anthropic: 'Anthropic', gemini: 'Google Gemini'};
  function aiCfg(){
    var c = lsGet('narani:ai', null) || {}, d = (INDEX && INDEX.agent) || {}, tiers = CFG.ai_models || {};
    c.keys = c.keys || {}; c.models = c.models || {};
    c.provider = c.provider || d.provider || 'openai';
    c.model = c.models[c.provider] || (d.provider === c.provider && d.model) || (tiers[c.provider] || [])[0] || '';
    return c;
  }
  function chat(){
    if (!AG.msgs) AG.msgs = lsGet('narani:chat:' + AG.key, []) || [];
    return AG.msgs;
  }
  function saveChat(){ lsSet('narani:chat:' + AG.key, chat().slice(-30)); }
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function md(text){   // 답 — 굵게·코드·목록·문단, [r12]·[r12, r15] 근거 링크
    var h = esc(text).replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/`([^`\n]+)`/g, '<code>$1</code>');
    h = h.replace(/\[((?:r\d+\s*,?\s*)+)\]/g, function(m, ids){
      return ids.split(/\s*,\s*/).filter(Boolean).map(function(r){
        return '<a href="#" class="rr" data-row="' + r.replace(/\D/g, '') + '">[' + r + ']</a>';
      }).join(' ');
    });
    var out = [], list = false;
    h.split('\n').forEach(function(line){
      var m = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
      if (m){ if (!list){ out.push('<ul>'); list = true; } out.push('<li>' + m[1] + '</li>'); return; }
      if (list){ out.push('</ul>'); list = false; }
      if (line.trim()) out.push('<p>' + line + '</p>');
    });
    if (list) out.push('</ul>');
    return out.join('');
  }
  agBody.addEventListener('click', function(e){
    var a = e.target.closest ? e.target.closest('a.rr') : null;
    if (!a) return;
    e.preventDefault(); closeDrawers();
    flashRow(doc.getElementById(PID + '-r' + a.getAttribute('data-row')));
  });
  function msgEl(m){
    var d = el('div', 'm ' + (m.role === 'user' ? 'u' : (m.err ? 'a err' : 'a')));
    if (m.role === 'user' || m.err) d.textContent = (m.voice && m.role === 'user' ? '🎙 ' : '') + m.content; else d.innerHTML = md(m.content);
    return d;
  }
  var TAB_ICON = {   // 두 칸 스위치의 그림 — 연필(메모) · 말풍선(질문하기)
    'tab-notes': '<path fill="currentColor" d="M3 17.25V21h3.75L17.8 9.94l-3.75-3.75zM20.7 7.04a1 1 0 0 0 0-1.41l-2.33-2.33a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75z"/>',
    'tab-chat': '<path fill="currentColor" d="M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zm-7 13h-2v-2h2zm1.7-5.6-.8.8c-.7.6-.9 1.1-.9 1.8h-2v-.5c0-1 .4-1.7 1.1-2.4l1.1-1.1a1.3 1.3 0 0 0-1-2.2c-.8 0-1.4.6-1.4 1.4H8.8a3.2 3.2 0 0 1 6.4 0c0 .8-.2 1.5-.5 2.2z"/>'
  };
  function setTab(id, label, on, n){   // n — 메모 개수는 글자 뒤 작은 동그라미로
    var b = doc.getElementById(id);
    b.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">' + TAB_ICON[id] + '</svg>';
    b.appendChild(doc.createTextNode(label));
    if (n) b.appendChild(el('span', 'cnt', String(n)));
    b.classList.toggle('on', on); b.setAttribute('aria-selected', on ? 'true' : 'false');
  }
  function setTabs(){
    var n = NOTE.key ? items().length : 0, notes = AG.mode === 'notes';
    setTab('tab-notes', md4T('메모'), notes, n);
    setTab('tab-chat', md4T('질문하기'), !notes);
    agent.classList.toggle('notes', notes);
  }
  doc.getElementById('tab-notes').onclick = function(){ lsSet('narani:agtab', 'notes'); showNotes(); };
  doc.getElementById('tab-chat').onclick = function(){ lsSet('narani:agtab', 'chat'); showChat(); };
  function showChat(){
    AG.mode = 'chat'; setTabs(); agBody.innerHTML = '';
    doc.getElementById('ag-form').style.display = '';
    var c = aiCfg();
    if (!c.keys[c.provider]){ showSettings(md4T('질문하려면 이 기기에 AI 서비스와 API 키를 한 번 넣어 주세요.')); return; }
    var msgs = chat();
    if (!msgs.length) agBody.appendChild(el('p', 'hint', md4T('이 논문에 대해 무엇이든 물어보세요 — 답의 [r12] 같은 표시를 누르면 그 문단으로 갑니다. ({p} · {m})', {p: PNAME[c.provider] || c.provider, m: c.model})));
    msgs.forEach(function(m){ agBody.appendChild(msgEl(m)); });
    agBody.scrollTop = agBody.scrollHeight;
  }
  function showSettings(note){
    AG.mode = 'settings'; setTabs(); agBody.innerHTML = '';
    doc.getElementById('ag-form').style.display = 'none';
    var c = aiCfg(), tiers = CFG.ai_models || {};
    if (note) agBody.appendChild(el('p', 'hint', note));
    agBody.appendChild(el('label', '', md4T('AI 서비스')));
    var sel = el('select'); Object.keys(PNAME).forEach(function(p){ var o = el('option', '', PNAME[p]); o.value = p; if (p === c.provider) o.selected = true; sel.appendChild(o); });
    agBody.appendChild(sel);
    agBody.appendChild(el('label', '', md4T('모델')));
    var model = el('input'); model.setAttribute('list', 'ag-models'); model.value = c.model; agBody.appendChild(model);
    var dl = el('datalist'); dl.id = 'ag-models'; agBody.appendChild(dl);
    function fillModels(){ dl.innerHTML = ''; (tiers[sel.value] || []).forEach(function(m){ var o = el('option'); o.value = m; dl.appendChild(o); }); }
    fillModels();
    agBody.appendChild(el('label', '', md4T('API 키')));
    var key = el('input'); key.type = 'password'; key.autocomplete = 'off'; key.spellcheck = false; agBody.appendChild(key);
    function keyHint(){ key.value = ''; key.placeholder = c.keys[sel.value] ? md4T('저장됨 — 바꾸려면 새로 입력') : md4T('키를 붙여 넣으세요'); }
    keyHint();
    sel.onchange = function(){ fillModels(); model.value = c.models[sel.value] || ((INDEX && INDEX.agent && INDEX.agent.provider === sel.value) ? INDEX.agent.model : (tiers[sel.value] || [])[0] || ''); keyHint(); };
    agBody.appendChild(el('label', '', md4T('말로 묻기 (OpenAI realtime) — 모델 · 목소리')));
    var rt = c.rt || {}, rtRow = el('div', 'acts'), rtm = el('select'), rtv = el('select');
    RT_MODELS.forEach(function(m){ var o = el('option', '', m); o.value = m; if (m === (rt.model || RT_MODELS[0])) o.selected = true; rtm.appendChild(o); });
    RT_VOICES.forEach(function(v){ var o = el('option', '', v); o.value = v; if (v === (rt.voice || RT_VOICES[0])) o.selected = true; rtv.appendChild(o); });
    rtRow.appendChild(rtm); rtRow.appendChild(rtv); agBody.appendChild(rtRow);
    agBody.appendChild(el('p', 'hint', md4T('말로 묻기는 OpenAI 키를 씁니다(위에서 OpenAI를 골라 키를 넣어 두세요). 묻는 동안 내 목소리와 이 논문의 본문·메모가 OpenAI로 직접 가고, 10분에 대략 몇백 원입니다.')));
    agBody.appendChild(el('p', 'hint', md4T('키는 이 기기의 브라우저에만 저장됩니다. 질문하면 이 논문의 본문과 내 하이라이트·메모, 대화가 고른 AI 서비스로 직접 보내집니다. 요금은 그 키의 계정에 청구됩니다.')));
    var acts = el('div', 'acts'), save = el('button', 'pri', md4T('저장')), del = el('button', '', md4T('이 서비스 키 지우기'));
    save.onclick = function(){
      c.provider = sel.value; c.models[c.provider] = model.value.trim();
      if (key.value.trim()) c.keys[c.provider] = key.value.trim();
      c.rt = {model: rtm.value, voice: rtv.value};
      lsSet('narani:ai', {provider: c.provider, models: c.models, keys: c.keys, rt: c.rt});
      try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (e) {}
      showChat();
    };
    del.onclick = function(){ delete c.keys[sel.value]; lsSet('narani:ai', {provider: c.provider, models: c.models, keys: c.keys, rt: c.rt}); keyHint(); };
    acts.appendChild(save); if (c.keys[c.provider]) acts.appendChild(del);
    if (c.keys[c.provider]){ var back = el('button', '', md4T('취소')); back.onclick = showChat; acts.appendChild(back); }
    agBody.appendChild(acts);
  }
  doc.getElementById('ag-set').onclick = function(){ if (AG.mode === 'settings') showChat(); else showSettings(); };
  doc.getElementById('ag-clear').onclick = function(){ AG.msgs = []; saveChat(); showChat(); };
  doc.getElementById('fab').onclick = function(e){
    e.stopPropagation();
    closeSheet();
    if (body.classList.contains('listening')){ talkStart(); return; }   // 듣는 중엔 말로 묻기 단추
    setText('ag-clear', '⟲'); doc.getElementById('ag-clear').setAttribute('aria-label', md4T('대화 지우기'));
    agIn.placeholder = md4T('이 논문에 대해 물어보세요');
    agent.classList.add('open'); scrim.classList.add('open');
    var tab = lsGet('narani:agtab', null) || (items().length ? 'notes' : 'chat');   // 지난번 탭 (처음엔 표시가 있으면 메모)
    if (tab === 'notes') showNotes(); else showChat();
  };

  function paperText(){
    var out = [], n = 0;
    [].forEach.call(cur.querySelectorAll('.row'), function(r){
      var k = r.id.replace(/^p\d+-r/, ''), c = r.querySelector('.cell.en') || r.querySelector('.cell.same') || r.querySelector('.cell.ko');
      var t = c ? c.textContent.replace(/\s+/g, ' ').trim() : '';
      if (t && n < 300000){ out.push('[r' + k + '] ' + t); n += t.length; }
    });
    return out.join('\n');
  }
  function notesText(){
    if (NOTE.key !== AG.key) return '';
    return items().map(function(it){
      var a = (it.anchors || [])[0] || {};
      return '- [r' + a.row + '] "' + String(a.quote || '').slice(0, 300) + '"' + (it.note ? ' — ' + it.note : '');
    }).join('\n');
  }
  function systemPrompt(){
    var notes = notesText();
    return 'You are the reading assistant inside Narani, on the user\'s phone. The user is reading the paper below. ' +
      'Reply in the language whose BCP-47 code is "' + AG.lang + '". Be concise and concrete. Base every claim on the paper ' +
      'and cite the paragraphs you used with their ids, like [r12] or [r12, r15]. If the paper does not say, say so first; ' +
      'you may then add general knowledge, clearly marked as such.\n\nTITLE: ' + AG.title +
      (notes ? '\n\nTHE USER\'S HIGHLIGHTS AND NOTES:\n' + notes : '') +
      '\n\nPAPER (original text; one paragraph per line, each starting with its id):\n' + paperText();
  }
  function sse(resp, onData){
    var reader = resp.body.getReader(), dec = new TextDecoder(), buf = '';
    function pump(){
      return reader.read().then(function(r){
        if (r.done) return;
        buf += dec.decode(r.value, {stream: true});
        var lines = buf.split('\n'); buf = lines.pop();
        lines.forEach(function(l){
          l = l.trim();
          if (l.indexOf('data:') !== 0) return;
          var d = l.slice(5).trim();
          if (d && d !== '[DONE]'){ try { onData(JSON.parse(d)); } catch (e) {} }
        });
        return pump();
      });
    }
    return pump();
  }
  function failed(resp){
    return resp.text().then(function(t){
      var m = t; try { var j = JSON.parse(t); m = (j.error && (j.error.message || j.error)) || t; } catch (e) {}
      throw new Error('HTTP ' + resp.status + ': ' + String(m).slice(0, 300));
    });
  }
  function askAI(c, system, msgs, onText){
    var key = c.keys[c.provider], req;
    if (c.provider === 'anthropic'){
      req = fetch('https://api.anthropic.com/v1/messages', {method: 'POST', headers: {'x-api-key': key,
        'anthropic-version': '2023-06-01', 'anthropic-dangerous-direct-browser-access': 'true', 'content-type': 'application/json'},
        body: JSON.stringify({model: c.model, max_tokens: 2048, stream: true, system: system, messages: msgs})});
      return req.then(function(r){ if (!r.ok) return failed(r);
        return sse(r, function(d){ if (d.type === 'content_block_delta' && d.delta && d.delta.text) onText(d.delta.text); }); });
    }
    if (c.provider === 'gemini'){
      req = fetch('https://generativelanguage.googleapis.com/v1beta/models/' + encodeURIComponent(c.model) +
        ':streamGenerateContent?alt=sse', {method: 'POST', headers: {'x-goog-api-key': key, 'content-type': 'application/json'},
        body: JSON.stringify({systemInstruction: {parts: [{text: system}]}, contents: msgs.map(function(m){
          return {role: m.role === 'assistant' ? 'model' : 'user', parts: [{text: m.content}]}; })})});
      return req.then(function(r){ if (!r.ok) return failed(r);
        return sse(r, function(d){
          var p = d.candidates && d.candidates[0] && d.candidates[0].content && d.candidates[0].content.parts;
          if (p) onText(p.map(function(x){ return x.text || ''; }).join(''));
        }); });
    }
    req = fetch('https://api.openai.com/v1/chat/completions', {method: 'POST',
      headers: {'Authorization': 'Bearer ' + key, 'content-type': 'application/json'},
      body: JSON.stringify({model: c.model, stream: true, messages: [{role: 'system', content: system}].concat(msgs)})});
    return req.then(function(r){ if (!r.ok) return failed(r);
      return sse(r, function(d){ var t = d.choices && d.choices[0] && d.choices[0].delta && d.choices[0].delta.content; if (t) onText(t); }); });
  }
  function send(){
    var q = agIn.value.trim(), c = aiCfg();
    if (!q || AG.busy || !cur) return;
    if (!c.keys[c.provider]){ showSettings(); return; }
    if (navigator.onLine === false){ agBody.appendChild(msgEl({role: 'assistant', err: true, content: md4T('오프라인이라 질문할 수 없어요.')})); return; }
    var msgs = chat();
    msgs.push({role: 'user', content: q});
    agIn.value = ''; agIn.style.height = '';
    showChat();
    var ans = {role: 'assistant', content: ''}, box = msgEl(ans);
    box.innerHTML = '<p class="hint">' + esc(md4T('읽는 중…')) + '</p>';
    agBody.appendChild(box); agBody.scrollTop = agBody.scrollHeight;
    AG.busy = true; agSend.disabled = true;
    var hist = msgs.slice(-12).map(function(m){ return {role: m.role, content: m.content}; });
    askAI(c, systemPrompt(), hist, function(t){
      ans.content += t; box.innerHTML = md(ans.content);
      var near = agBody.scrollHeight - agBody.scrollTop - agBody.clientHeight < 120;
      if (near) agBody.scrollTop = agBody.scrollHeight;
    }).then(function(){
      if (!ans.content) throw new Error(md4T('빈 답이 왔어요'));
      msgs.push(ans);
    }).catch(function(e){
      msgs.push({role: 'assistant', err: true, content: md4T('답을 받지 못했어요: {e}', {e: e.message || e})});
      msgs.splice(msgs.length - 2, 1);   // 실패한 질문은 대화에서 빼고(다시 물을 수 있게) 오류만 남긴다
      agIn.value = q;
    }).then(function(){
      AG.busy = false; agSend.disabled = false;
      AG.msgs = msgs.filter(function(m){ return !m.err; }); saveChat();
      var shown = msgs.slice(); showChat(); if (shown.length && shown[shown.length - 1].err) agBody.appendChild(msgEl(shown[shown.length - 1]));
      agBody.scrollTop = agBody.scrollHeight;
    });
  }
  doc.getElementById('ag-form').addEventListener('submit', function(e){ e.preventDefault(); send(); });
  agIn.addEventListener('input', function(){ agIn.style.height = ''; agIn.style.height = Math.min(agIn.scrollHeight, 200) + 'px'; });
  agIn.addEventListener('keydown', function(e){ if (e.key === 'Enter' && !e.shiftKey && !e.isComposing){ e.preventDefault(); send(); } });
"""

# 듣기 — 데스크톱이 만든 음성(§phone.tts)을 읽기 화면 아래 막대로 재생한다.
_AUDIO_JS = r"""
  // ---- 듣기 (§phone.tts) — 데스크톱이 만든 음성 파일 하나 + 문장 시간표 [t, 행, s, e] ----
  // 화면이 꺼져도·잠금 화면·CarPlay 단추로 듣는다(실기기 시험 tts-test.html로 확인한 방식: <audio> 하나 +
  // Media Session, 섹션 이동은 파일 안에서 seek). 파일은 처음 들을 때 받아 Cache Storage에 두고 blob URL로 꽂는다.
  var AC = 'narani-audio', au = doc.getElementById('pl-au'), player = doc.getElementById('player');
  var AU = {e: null, key: null, tl: null, url: null, ready: false, cur: -1, sec: -1, lastSave: 0, lastPS: 0,
            lastTU: 0, userScroll: 0, want: null, busy: false, follow: false};
  var HL = !!(window.CSS && CSS.highlights && window.Highlight);
  function audioOf(e){ return e && e.audio && e.audio.file ? e.audio : null; }
  function fmtT(s){ s = Math.max(0, Math.floor(s || 0)); var h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
    return (h ? h + ':' + ('0' + m).slice(-2) : m) + ':' + ('0' + s % 60).slice(-2); }
  function audioKey(a){ return '__audio/' + a.file + '?h=' + a.hash; }
  function audioCached(a){
    if (!a || !window.caches) return Promise.resolve(false);
    return caches.open(AC).then(function(c){ return c.match(audioKey(a)); }).then(function(r){ return !!r; }, function(){ return false; });
  }
  function readAll(r, total, onp){
    if (!r.body || !r.body.getReader) return r.blob();
    var rd = r.body.getReader(), parts = [], got = 0;
    return (function pump(){
      return rd.read().then(function(x){
        if (x.done) return new Blob(parts, {type: 'audio/mp4'});
        parts.push(x.value); got += x.value.length; if (onp && total) onp(Math.min(1, got / total));
        return pump();
      });
    })();
  }
  function audioBlob(a, onp){
    function fetchIt(){
      if (!source || !source.raw) return Promise.reject(new Error('no source'));
      return source.raw(a.file).then(function(r){ return readAll(r, a.bytes, onp); });
    }
    if (!window.caches) return fetchIt();
    return caches.open(AC).then(function(c){
      return c.match(audioKey(a)).then(function(hit){
        if (hit) return hit.blob().then(function(b){ return b.type ? b : new Blob([b], {type: 'audio/mp4'}); });
        return fetchIt().then(function(b){
          return c.put(audioKey(a), new Response(b, {headers: {'Content-Type': 'audio/mp4'}})).then(function(){ return b; }, function(){ return b; });
        });
      });
    });
  }
  function audioTl(a){
    return cacheGet(a.tl).then(function(c){
      if (c && c.__h === a.hash) return c;
      if (!source) { if (c) return c; throw new Error('no source'); }
      return source.get(a.tl).then(function(t){ t.__h = a.hash; cachePut(a.tl, t); return t; }, function(err){ if (c) return c; throw err; });
    });
  }

  // ---- 열고 닫기 ----
  function audioOpened(e){
    stopAudio();
    AU.e = e; AU.key = e.key; AU.tl = null; AU.ready = false;
    var a = audioOf(e);
    body.classList.toggle('has-audio', !!a);
    if (!a) return;
    audioCached(a).then(function(yes){ if (yes && AU.e === e) prime(false); });   // 받아 둔 음성 — 바로 들을 수 있게 꽂아 둔다
  }
  function stopAudio(){
    if (!AU.e) return;
    try { au.pause(); } catch (x) {}
    savePos();
    body.classList.remove('listening', 'has-audio');
    unmarkNow(); markSpoken(true);
    if (AU.url){ try { URL.revokeObjectURL(AU.url); } catch (x) {} AU.url = null; }
    au.removeAttribute('src'); try { au.load(); } catch (x) {}
    AU.e = null; AU.tl = null; AU.ready = false; AU.cur = AU.sec = -1;
    if ('mediaSession' in navigator) try { navigator.mediaSession.metadata = null; navigator.mediaSession.playbackState = 'none'; } catch (x) {}
  }
  // 시간표와 파일을 준비해 <audio>에 꽂는다. 처음 받는 것이면 받는 동안 막대에 진행을 보인다.
  function prime(show){
    var e = AU.e, a = audioOf(e);
    if (!a) return Promise.reject(new Error('no audio'));
    if (AU.ready) return Promise.resolve();
    if (AU.busy) return AU.busy;
    if (show) setText('pl-sec', md4T('음성 받는 중…'));
    AU.busy = audioTl(a).then(function(tl){
      if (AU.e !== e) throw new Error('stale');
      AU.tl = tl; markSpoken();
      return audioBlob(a, function(f){ if (show && AU.e === e) setText('pl-sec', md4T('음성 받는 중… {p}%', {p: Math.round(f * 100)})); });
    }).then(function(blob){
      if (AU.e !== e) throw new Error('stale');
      if (AU.url) try { URL.revokeObjectURL(AU.url); } catch (x) {}
      var keep = au.currentTime || 0;   // 다시 꽂을 때(앱을 다시 열었을 때) 듣던 자리를 잃지 않게
      AU.url = URL.createObjectURL(blob);
      au.src = AU.url;
      if (keep) au.addEventListener('loadedmetadata', function f(){ au.removeEventListener('loadedmetadata', f); au.currentTime = keep; });   // iOS 26: 다시 연 홈 화면 앱의 <audio>가 소리 없이 재생되는 버그 — 재생 직전에 새로 꽂는 것으로 피한다
      AU.ready = true;
      doc.getElementById('pl-seek').max = Math.floor(AU.tl.duration);
      setText('pl-dur', fmtT(AU.tl.duration));
      var stale = a.src && AU.e.hash && a.src !== AU.e.hash;
      setText('pl-sec', stale ? md4T('본문이 바뀐 뒤의 옛 음성이에요 — 하이라이트가 어긋날 수 있어요') : (AU.tl.sections[0] || {}).title || '');
    });
    AU.busy.then(function(){ AU.busy = false; }, function(err){
      AU.busy = false;
      if (AU.e === e && show) setText('pl-sec', (err && err.kind === 'auth') ? md4T('Google Drive 연결이 만료됐어요.') : md4T('음성을 받지 못했어요. 인터넷에 연결된 뒤 다시 눌러 보세요.'));
    });
    return AU.busy;
  }

  // ---- 시간표 ----
  function SEC(){ return (AU.tl && AU.tl.sections) || []; }
  function secAt(t){ var S = SEC(), k = 0; for (var i = 0; i < S.length; i++) if (S[i].start <= t + 0.05) k = i; return k; }
  function sentAtT(t){
    var S = AU.tl.sents, lo = 0, hi = S.length - 1;
    while (lo < hi){ var m = (lo + hi + 1) >> 1; if (S[m][0] <= t + 0.05) lo = m; else hi = m - 1; }
    return lo;
  }
  function timeAtRow(row, off){   // 그 행(과 셀 안 위치)에서 가장 가까운, 그 뒤의 첫 문장
    var S = AU.tl.sents;
    for (var i = 0; i < S.length; i++){
      if (S[i][1] > row) return S[i][0];
      if (S[i][1] === row && (off == null || S[i][3] > off)) return S[i][0];
    }
    return 0;
  }

  // ---- 듣기 범위 ----
  function RANGE(){ return lsGet('narani:arange:' + AU.key, {mode: 'all'}); }
  function allowed(i){
    var R = RANGE(), s = SEC()[i];
    if (!s) return false;
    if (R.mode === 'sec') return s.row === R.row;
    if (R.mode === 'here') return s.start >= (R.from || 0) - 0.05 || s.row === R.row;
    if (R.mode === 'pick') return (R.rows || []).indexOf(s.row) >= 0;
    return true;
  }
  function rangeSheet(){
    var R = RANGE(), k = Math.max(0, secAt(au.currentTime || 0)), S = SEC(), here = S[k] || {};
    var box = el('div', 'rng'), picks = (R.rows || S.map(function(s){ return s.row; })).slice(), list = el('div', 'rng-secs');
    function radio(v, label){
      var l = el('label'), r = el('input'); r.type = 'radio'; r.name = 'rng'; r.value = v; r.checked = (R.mode || 'all') === v;
      r.onchange = function(){ list.style.display = v === 'pick' ? '' : 'none'; };
      l.appendChild(r); l.appendChild(doc.createTextNode(' ' + label)); box.appendChild(l);
    }
    radio('all', md4T('처음부터 끝까지'));
    radio('sec', md4T('이 섹션만 — {s}', {s: here.title || ''}));
    radio('here', md4T('여기부터 끝까지'));
    radio('pick', md4T('섹션 골라 듣기'));
    S.forEach(function(s){
      var l = el('label'), c = el('input'); c.type = 'checkbox'; c.checked = picks.indexOf(s.row) >= 0;
      c.onchange = function(){ var i = picks.indexOf(s.row); if (c.checked && i < 0) picks.push(s.row); if (!c.checked && i >= 0) picks.splice(i, 1); };
      l.appendChild(c); l.appendChild(doc.createTextNode(' ' + s.title + ' · ' + fmtT(s.end - s.start))); list.appendChild(l);
    });
    list.style.display = R.mode === 'pick' ? '' : 'none';
    box.appendChild(list);
    var ok = button(md4T('적용'), function(){
      var v = (box.querySelector('input[name=rng]:checked') || {}).value || 'all', t = au.currentTime || 0;
      var N = {mode: v, row: here.row, from: v === 'here' ? t : here.start, rows: picks};
      lsSet('narani:arange:' + AU.key, N);
      closeSheet();
      if (!allowed(secAt(t))) jumpSec(1, md4T('범위'));
    });
    openSheet(md4T('듣기 범위'), [box], [ok]);
  }

  // ---- 재생 ----
  function startAt(t){
    if (t != null){ try { au.currentTime = Math.max(0, Math.min(AU.tl.duration - 0.3, t)); } catch (x) {} }
    var k = secAt(au.currentTime || 0), S = SEC(), i, to = null;
    if (!allowed(k)){   // 범위 밖에서 재생 — 그 뒤의 첫 고른 섹션, 없으면 범위 처음부터
      for (i = k + 1; i < S.length && to == null; i++) if (allowed(i)) to = i;
      for (i = 0; i < S.length && to == null; i++) if (allowed(i)) to = i;
      if (to != null) au.currentTime = S[to].start;
    }
    var p = au.play();
    if (p && p.then) p.then(null, function(){ setText('pl-sec', md4T('▶을 눌러 들으세요')); });
  }
  function listenFrom(row, off){   // 🎧·'여기부터 듣기' — 화면을 보고 있던 자리에서
    if (!audioOf(AU.e)) return;
    body.classList.add('listening'); closeSheet(); closeDrawers();
    var go = function(){
      var t = row != null ? timeAtRow(row, off) : null;
      // 콕 집어 '여기부터' 들으면 그게 먼저다 — 고른 범위 밖이면 범위를 '처음부터 끝까지'로 되돌린다
      if (t != null && !allowed(secAt(t))) lsSet('narani:arange:' + AU.key, {mode: 'all'});
      startAt(t);
    };
    if (AU.ready) go(); else prime(true).then(go, function(){});
  }
  function seekTo(t){   // 막대·±15초·섹션 이동 — 옮긴 자리로 화면도 데려간다(멈춰 있어도)
    AU.follow = true; AU.userScroll = 0; AU.cur = -1;
    au.currentTime = Math.max(0, Math.min((AU.tl && AU.tl.duration) || au.duration || 1e9, t));
  }
  function jumpSec(dir, why){
    if (!AU.tl) return;
    var t = au.currentTime, k = secAt(t), S = SEC(), target = null, i;
    if (dir > 0){ for (i = k + 1; i < S.length; i++) if (allowed(i)){ target = i; break; } }
    else {
      if (t - S[k].start > 3 && allowed(k)) target = k;
      else for (i = k - 1; i >= 0; i--) if (allowed(i)){ target = i; break; }
      if (target == null) target = k;
    }
    if (target == null){ au.pause(); return; }
    seekTo(S[target].start);
  }
  function savePos(){
    if (!AU.tl || !AU.key || !au.currentTime) return;
    var s = AU.tl.sents[sentAtT(au.currentTime)];
    if (s) store('pos:' + AU.key, 'r' + s[1] + ':0');   // 다음에 열면 리더가 들은 자리로 — 🎧도 거기서
  }
  doc.getElementById('lis').onclick = function(e){
    e.stopPropagation();
    var r = topRow(); listenFrom(r ? +r.id.replace(/^p\d+-r/, '') : null, null);
  };
  doc.getElementById('pl-pp').onclick = function(){
    if (!AU.ready){ prime(true).then(function(){ startAt(null); }, function(){}); return; }
    if (au.paused) startAt(null); else au.pause();
  };
  doc.getElementById('pl-back').onclick = function(){ seekTo(au.currentTime - 15); };
  doc.getElementById('pl-fwd').onclick = function(){ seekTo(au.currentTime + 15); };
  doc.getElementById('pl-prev').onclick = function(){ jumpSec(-1); };
  doc.getElementById('pl-next').onclick = function(){ jumpSec(1); };
  doc.getElementById('pl-range').onclick = function(){ if (AU.tl) rangeSheet(); };
  // 막대의 누르기가 문서의 '시트 바깥을 눌렀다'(시트 닫기)까지 가지 않게 — 범위 시트가 열리자마자 닫혔다
  doc.getElementById('player').addEventListener('click', function(e){ e.stopPropagation(); });
  doc.getElementById('pl-x').onclick = function(){ au.pause(); savePos(); body.classList.remove('listening'); unmarkNow(); };
  function audioLabels(){
    setText('pl-range', md4T('범위'));
    setText('fab-ask-t', md4T('묻기')); setText('tk-end', md4T('끝'));
    var am = doc.getElementById('ag-mic'); if (am) am.setAttribute('aria-label', md4T('말로 묻기'));
    [['lis', md4T('여기부터 듣기')], ['pl-range', md4T('듣기 범위')], ['pl-prev', md4T('이전 섹션')], ['pl-back', md4T('15초 뒤로')],
     ['pl-pp', md4T('재생')], ['pl-fwd', md4T('15초 앞으로')], ['pl-next', md4T('다음 섹션')], ['pl-spd', md4T('속도')],
     ['pl-x', md4T('듣기 닫기')], ['pl-seek', md4T('재생 위치')]].forEach(function(x){
      var n = doc.getElementById(x[0]); if (n) n.setAttribute('aria-label', x[1]);
    });
  }
  var SPEEDS = [1, 1.25, 1.5, 1.75, 2, 0.8];
  function setAudioSession(type){   // iOS 17+ Safari — 듣기는 'playback'(무음 스위치·화면 꺼짐에도), 말로 묻기는 'play-and-record'
    if ('audioSession' in navigator) try { navigator.audioSession.type = type; } catch (x) {}
  }
  function setSpd(v){ au.playbackRate = au.defaultPlaybackRate = v; lsSet('narani:aspd', v); setText('pl-spd', v + '×'); }
  setSpd(lsGet('narani:aspd', 1));
  doc.getElementById('pl-spd').onclick = function(){ var v = au.playbackRate, i = SPEEDS.indexOf(v); setSpd(SPEEDS[(i + 1) % SPEEDS.length]); };
  var seekEl = doc.getElementById('pl-seek');
  seekEl.oninput = function(){ setText('pl-cur', fmtT(+seekEl.value)); };
  seekEl.onchange = function(){ seekTo(+seekEl.value); };

  function meta(k){
    if (!('mediaSession' in navigator) || !AU.tl) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({title: (DATA[PID] || {}).title || APP, artist: (SEC()[k] || {}).title || '',
        album: APP, artwork: [{src: 'icon-512.png', sizes: '512x512', type: 'image/png'}, {src: 'icon-192.png', sizes: '192x192', type: 'image/png'}]});
    } catch (x) {}
  }
  if ('mediaSession' in navigator){
    var MH = {
      play: function(){ startAt(null); }, pause: function(){ au.pause(); }, stop: function(){ au.pause(); },
      seekbackward: function(d){ au.currentTime = Math.max(0, au.currentTime - ((d && d.seekOffset) || 15)); },
      seekforward: function(d){ au.currentTime = au.currentTime + ((d && d.seekOffset) || 15); },
      previoustrack: function(){ jumpSec(-1); }, nexttrack: function(){ jumpSec(1); },
      seekto: function(d){ if (d && d.seekTime != null) au.currentTime = d.seekTime; }
    };
    Object.keys(MH).forEach(function(k){ try { navigator.mediaSession.setActionHandler(k, MH[k]); } catch (x) {} });
  }
  setAudioSession('playback');
  ['play', 'pause'].forEach(function(ev){
    au.addEventListener(ev, function(){
      setText('pl-pp', ev === 'play' ? '⏸' : '▶');
      doc.getElementById('pl-pp').setAttribute('aria-label', ev === 'play' ? md4T('일시정지') : md4T('재생'));
      if ('mediaSession' in navigator) try { navigator.mediaSession.playbackState = ev === 'play' ? 'playing' : 'paused'; } catch (x) {}
      if (ev === 'pause') savePos();
    });
  });
  au.addEventListener('ended', function(){ savePos(); });

  // ---- 지금 문장 칠하기 (CSS Custom Highlight — DOM을 바꾸지 않는다; 없으면 행째로) ----
  function cellRange(cell, s, e){
    var w = doc.createTreeWalker(cell, NodeFilter.SHOW_TEXT), n, pos = 0, r = doc.createRange(), started = false;
    while ((n = w.nextNode())){
      var len = n.data.length;
      if (!started && s < pos + len){ r.setStart(n, s - pos); started = true; }
      if (started && e <= pos + len){ r.setEnd(n, e - pos); return r; }
      pos += len;
    }
    return started ? (r.setEnd(cell, cell.childNodes.length), r) : null;
  }
  var nowRow = null, hlCell = null;
  function unmarkNow(){
    if (HL) try { CSS.highlights.delete('tts'); } catch (x) {}
    if (nowRow){ nowRow.classList.remove('tts-row'); nowRow = null; }
    // iOS Safari는 지운 강조의 배경을 글자 둘레만 다시 그려 줄 아래에 노란 띠가 남는다 — 그 셀을 통째로 다시 그리게 한다
    if (hlCell){
      var c = hlCell; hlCell = null;
      c.style.opacity = '.999';
      requestAnimationFrame(function(){ requestAnimationFrame(function(){ c.style.opacity = ''; }); });
    }
  }
  // 음성에 없는 행(그림·표·캡션·참고문헌·뺀 섹션…) — 듣는 동안 흐리게 두고 눌러도 아무 일 없게
  function markSpoken(clear){
    var art = doc.getElementById(PID), on = {};
    if (!art) return;
    if (!clear && AU.tl) AU.tl.sents.forEach(function(s){ on[s[1]] = 1; });
    [].forEach.call(art.querySelectorAll('.row[id]'), function(r){
      r.classList.toggle('tts-off', !clear && !on[+r.id.replace(/^p\d+-r/, '')]);
    });
  }
  function markNow(i){
    var s = AU.tl.sents[i], row = doc.getElementById(PID + '-r' + s[1]);
    unmarkNow();
    if (!row) return;
    var cell = row.querySelector('.cell.' + AU.tl.side) || row.querySelector('.cell.same'), rect = null;
    if (HL && cell && cell.offsetParent){
      var r = cellRange(cell, s[2], s[3]);
      if (r){ CSS.highlights.set('tts', new Highlight(r)); rect = r.getBoundingClientRect(); hlCell = cell; }
    }
    if (!rect){ nowRow = row; row.classList.add('tts-row'); rect = row.getBoundingClientRect(); }
    // 화면을 보고 있으면 따라 내려간다 — 손으로 스크롤한 뒤 몇 초는 두고, 이미 보이면 움직이지 않는다
    var bottom = window.innerHeight - player.offsetHeight - 16, follow = AU.follow;
    AU.follow = false;
    if (doc.visibilityState === 'visible' && (follow || Date.now() - AU.userScroll > 4000) && (rect.top < 70 || rect.bottom > bottom))
      window.scrollBy({top: rect.top - window.innerHeight * 0.3, behavior: follow ? 'auto' : 'smooth'});
  }
  ['touchmove', 'wheel'].forEach(function(ev){ window.addEventListener(ev, function(e){
    if (e.target && e.target.closest && e.target.closest('#player')) return;   // 재생 막대를 끄는 손가락은 본문 스크롤이 아니다
    AU.userScroll = Date.now();
  }, {passive: true}); });
  au.addEventListener('seeked', function(){
    if (!AU.tl || !body.classList.contains('listening')) return;
    var si = sentAtT(au.currentTime);
    if (si !== AU.cur || AU.follow){ AU.cur = si; markNow(si); }
  });
  au.addEventListener('timeupdate', function(){
    if (!AU.tl) return;
    var t = au.currentTime, now = Date.now(), k = secAt(t), S = SEC();
    if (!allowed(k)){   // 고른 범위 밖 — 다음 고른 섹션으로, 없으면 멈춤
      if (au.paused) return;
      var nx = null; for (var i = k + 1; i < S.length; i++) if (allowed(i)){ nx = i; break; }
      if (nx == null){ au.pause(); setText('pl-sec', md4T('고른 범위를 다 들었어요')); }
      else au.currentTime = S[nx].start;
      return;
    }
    if (k !== AU.sec){ AU.sec = k; meta(k); setText('pl-sec', (S[k] || {}).title || ''); }
    var si = sentAtT(t);
    if (si !== AU.cur && body.classList.contains('listening')){ AU.cur = si; markNow(si); }
    if (doc.activeElement !== seekEl){ seekEl.value = Math.floor(t); setText('pl-cur', fmtT(t)); }
    if (now - AU.lastSave > 5000){ AU.lastSave = now; savePos(); }
    if ('mediaSession' in navigator && navigator.mediaSession.setPositionState && now - AU.lastPS > 1000){
      AU.lastPS = now;
      try { navigator.mediaSession.setPositionState({duration: AU.tl.duration, playbackRate: au.playbackRate || 1, position: Math.min(t, AU.tl.duration)}); } catch (x) {}
    }
  });
  doc.addEventListener('visibilitychange', function(){
    if (doc.visibilityState !== 'visible' || !AU.e) return;
    // 멈춘 채 다시 보이면 다음 ▶ 때 캐시에서 새로 꽂는다 — 실기기 시험에서 통과한 방식(재생 직전에 src)과 같게
    if (au.paused) AU.ready = false;
    if (AU.tl && AU.cur >= 0 && body.classList.contains('listening')) markNow(AU.cur);
  });
  window.addEventListener('pageshow', function(e){ if (e.persisted && au.paused) AU.ready = false; });
"""

# 말로 묻기 — 듣다가 🎙로 이 논문에 대해 음성으로 묻는다(OpenAI realtime, 이 기기에 넣은 내 키).
_TALK_JS = r"""
  // ---- 말로 묻기 (OpenAI realtime) — 듣다가 🎙: 듣기를 멈추고 이 논문에 대해 음성으로 묻고 답을 듣는다 ----
  // 서버 없이: 이 기기에 넣어 둔 OpenAI 키로 단기 키(client_secrets)를 받아 WebRTC(calls)로 붙는다(마이크·재생·반향
  // 제거는 브라우저가). AI는 이 논문 본문([rN])·지금 듣던 자리·내 메모를 알고, 근거 행을 jump_to로 짚고(대화 기록에
  // [rN]), '계속 읽어 줘'면 resume_listening으로 끝내고 이어 듣는다. 조용히 20초면 끝, 끝나면 듣던 것을 이어 간다.
  // 실기기 시험(realtime-test.html)에서 확인한 흐름 그대로. 대답(response)은 한 번에 하나만 요청한다.
  var TK = null, tkBar = doc.getElementById('talkbar');
  var RT_VOICES = ['marin', 'cedar', 'alloy', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse'];
  var RT_MODELS = ['gpt-realtime-2.1-mini', 'gpt-realtime-2.1'];
  var RT_PRICE = {   // 100만 토큰당 달러 — mini는 공식 가격표(2026-10), 2.1은 어림
    'gpt-realtime-2.1-mini': {ti: 0.6, tic: 0.06, ai: 10, aic: 0.3, ao: 20, to: 2.4},
    'gpt-realtime-2.1': {ti: 4, tic: 0.4, ai: 32, aic: 0.4, ao: 64, to: 16}
  };
  function rtCfg(){
    var c = aiCfg(), rt = c.rt || {};
    return {key: c.keys.openai || '', model: RT_MODELS.indexOf(rt.model) >= 0 ? rt.model : RT_MODELS[0],
            voice: RT_VOICES.indexOf(rt.voice) >= 0 ? rt.voice : RT_VOICES[0]};
  }
  function voiceText(){   // 듣는 쪽(번역)으로 — 행마다 [rN]
    var side = (AU.tl && AU.tl.side) || (cur && cur.querySelector('.cell.ko') ? 'ko' : 'en'), out = [], n = 0;
    [].forEach.call(cur.querySelectorAll('.row'), function(r){
      var k = r.id.replace(/^p\d+-r/, ''), c = r.querySelector('.cell.' + side) || r.querySelector('.cell.same') || r.querySelector('.cell.en');
      var t = c ? c.textContent.replace(/\s+/g, ' ').trim() : '';
      if (t && n < 90000){ out.push('[r' + k + '] ' + t); n += t.length; }
    });
    return out.join('\n');
  }
  function talkWhere(){
    if (AU.tl && AU.e && body.classList.contains('listening')){
      var s = AU.tl.sents[sentAtT(au.currentTime || 0)], sec = SEC()[secAt(au.currentTime || 0)] || {};
      return 'The user was listening (audio) around [r' + (s ? s[1] : 0) + '] (section: ' + (sec.title || '') + ').';
    }
    var r = topRow();
    return 'The user was reading on screen around ' + (r ? '[r' + r.id.replace(/^p\d+-r/, '') + ']' : '?') + '.';
  }
  function talkInstructions(){
    var notes = notesText();
    return 'You are a voice assistant answering questions about the research paper below, which the user is listening to or reading '
      + '(possibly while driving, without looking at the screen). Reply in the language the user speaks (default: ' + (AG.lang || 'ko') + '), '
      + 'briefly and naturally (usually 2-4 sentences). Do not read lists, tables or formulas aloud; explain them in words. '
      + 'Ground answers in the paper; if it is not in the paper, say so. When a specific row supports your answer, call jump_to with it, '
      + 'but never say row numbers aloud. If the user wants to continue listening / go back to reading / stop asking, call resume_listening '
      + '(with row if they name a place).\n\n# Paper: ' + (AG.title || '') + '\n' + voiceText()
      + (notes ? '\n\n# My highlights and notes\n' + notes : '') + '\n\n# Where the user is\n' + talkWhere();
  }
  var TK_TOOLS = [
    {type: 'function', name: 'jump_to', description: 'Show the paper row that supports the answer.',
     parameters: {type: 'object', properties: {row: {type: 'integer'}}, required: ['row']}},
    {type: 'function', name: 'read_rows', description: 'Read rows of the paper again (to quote or check).',
     parameters: {type: 'object', properties: {from: {type: 'integer'}, to: {type: 'integer'}}, required: ['from', 'to']}},
    {type: 'function', name: 'resume_listening', description: 'End the conversation and continue listening to the paper (from row if given).',
     parameters: {type: 'object', properties: {row: {type: 'integer'}}}}
  ];
  function tkState(cls, text){ tkBar.className = 'on ' + cls; setText('tk-st', text); }
  function tkSend(o){ if (TK && TK.dc && TK.dc.readyState === 'open') TK.dc.send(JSON.stringify(o)); }
  function tkAsk(extra){   // 대답 요청은 한 번에 하나 — 진행 중이면 끝난 뒤로
    if (!TK) return;
    if (TK.active){ TK.pending = extra || TK.pending || {}; return; }
    TK.active = true;
    tkSend(extra && extra.response ? {type: 'response.create', response: extra.response} : {type: 'response.create'});
  }
  function tkChat(role, text){
    if (!text) return;
    var msgs = chat(); msgs.push({role: role, content: text, voice: true}); saveChat();
    if (agent.classList.contains('open') && AG.mode === 'chat') showChat();
  }
  function tkUsage(u){
    if (!u || !TK) return;
    var p = RT_PRICE[TK.model] || RT_PRICE[RT_MODELS[0]], id = u.input_token_details || {}, od = u.output_token_details || {};
    var cc = id.cached_tokens_details || {}, ct = cc.text_tokens || 0, ca = cc.audio_tokens || 0;
    TK.cost += (((id.text_tokens || 0) - ct) * p.ti + ct * p.tic + ((id.audio_tokens || 0) - ca) * p.ai + ca * p.aic
               + (od.audio_tokens || 0) * p.ao + (od.text_tokens || 0) * p.to) / 1e6;
  }
  function tkTool(name, args, callId){
    var out = {ok: true};
    if (name === 'jump_to'){
      var el = doc.getElementById(PID + '-r' + args.row);
      if (el && doc.visibilityState === 'visible') flashRow(el);
      var msgs = chat(), last = msgs[msgs.length - 1];   // 대화 기록의 마지막 답에 근거 [rN]
      if (last && last.role === 'assistant' && last.content.indexOf('[r' + args.row + ']') < 0){ last.content += ' [r' + args.row + ']'; saveChat(); }
    } else if (name === 'read_rows'){
      out = {text: voiceText().split('\n').filter(function(l){ var m = /^\[r(\d+)\]/.exec(l); return m && +m[1] >= args.from && +m[1] <= args.to; }).join('\n').slice(0, 6000)};
    } else if (name === 'resume_listening'){
      TK.closing = true; TK.resumeRow = args.row != null ? args.row : null; TK.resume = true;
      tkSend({type: 'conversation.item.create', item: {type: 'function_call_output', call_id: callId, output: '{"ok":true}'}});
      setTimeout(function(){ talkEnd(md4T('이어 듣기')); }, 1200);
      return;
    }
    tkSend({type: 'conversation.item.create', item: {type: 'function_call_output', call_id: callId, output: JSON.stringify(out)}});
  }
  function tkEvent(ev){
    var e; try { e = JSON.parse(ev.data); } catch (x) { return; }
    if (!TK) return;
    var now = Date.now();
    switch (e.type){
      case 'response.created': TK.active = true; break;
      case 'input_audio_buffer.speech_started': TK.last = now; TK.userTalking = true; tkState('listening', md4T('듣는 중 — 말씀하세요')); break;
      case 'input_audio_buffer.speech_stopped': TK.last = now; TK.userTalking = false; tkState('thinking', md4T('생각 중…')); break;
      case 'conversation.item.input_audio_transcription.completed': tkChat('user', (e.transcript || '').trim()); break;
      case 'response.output_audio_transcript.delta': TK.last = now; tkState('speaking', md4T('말하는 중')); break;
      case 'response.output_audio_transcript.done': tkChat('assistant', (e.transcript || '').trim()); break;
      case 'output_audio_buffer.started': TK.botTalking = true; TK.last = now; break;
      case 'output_audio_buffer.stopped': TK.botTalking = false; TK.last = now; tkState('listening', md4T('듣는 중 — 더 물어보세요')); break;
      case 'response.done':
        var r = e.response || {}, called = 0;
        TK.active = false; TK.last = now;
        tkUsage(r.usage);
        (r.output || []).forEach(function(it){
          if (it.type === 'function_call'){ var a = {}; try { a = JSON.parse(it.arguments || '{}'); } catch (x) {} tkTool(it.name, a, it.call_id); called++; }
        });
        if (!TK || TK.closing) break;
        if (called) tkAsk();
        else if (TK.pending){ var pd = TK.pending; TK.pending = null; tkAsk(pd); }
        break;
      case 'error':
        var msg = (e.error && e.error.message) || '';
        if (!/active response in progress/i.test(msg)) tkChat('assistant', md4T('음성 대화 오류: {e}', {e: msg}));
        break;
    }
  }
  function openAgentSettings(note){
    closeSheet(); agent.classList.add('open'); scrim.classList.add('open'); showSettings(note);
  }
  async function talkStart(){
    if (TK) return;
    var c = rtCfg();
    if (!c.key){ openAgentSettings(md4T('말로 묻기는 OpenAI 키가 필요해요 — 이 기기에 한 번 넣어 주세요(질문하기와 같은 키).')); return; }
    if (navigator.onLine === false){ tkState('', md4T('오프라인이라 말로 물을 수 없어요.')); setTimeout(function(){ if (!TK) tkBar.className = ''; }, 3000); return; }
    var wasPlaying = !!(AU.e && !au.paused);
    au.pause();
    TK = {model: c.model, cost: 0, started: Date.now(), last: Date.now(), wasPlaying: wasPlaying, active: false};
    body.classList.add('talking'); tkState('', md4T('연결 중…'));
    try {
      // iOS: 듣기에 맞춘 'playback' 세션으로는 마이크를 못 연다("AudioSession category is not compatible with
      // audio capture") — 대화 동안만 'play-and-record'로, 끝나면 talkEnd에서 되돌린다
      setAudioSession('play-and-record');
      TK.mic = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      var tok = await fetch('https://api.openai.com/v1/realtime/client_secrets', {method: 'POST',
        headers: {'Authorization': 'Bearer ' + c.key, 'Content-Type': 'application/json'},
        body: JSON.stringify({session: {type: 'realtime', model: c.model, audio: {output: {voice: c.voice}}}})});
      if (!tok.ok) throw new Error((tok.status === 401 ? md4T('OpenAI 키가 맞지 않아요') : 'HTTP ' + tok.status) + ' — ' + (await tok.text()).slice(0, 160));
      var ek = (await tok.json()).value;
      if (!TK) return;
      var pc = new RTCPeerConnection(); TK.pc = pc;
      pc.ontrack = function(ev){ var a = doc.getElementById('tk-au'); a.srcObject = ev.streams[0]; a.play().catch(function(){}); };
      pc.onconnectionstatechange = function(){ if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') talkEnd(md4T('연결이 끊겼어요')); };
      TK.mic.getTracks().forEach(function(tr){ pc.addTrack(tr, TK.mic); });
      var dc = pc.createDataChannel('oai-events'); TK.dc = dc;
      dc.onmessage = tkEvent;
      dc.onopen = function(){
        tkSend({type: 'session.update', session: {type: 'realtime', instructions: talkInstructions(), tools: TK_TOOLS, tool_choice: 'auto',
          audio: {input: {turn_detection: {type: 'semantic_vad'}, transcription: {model: 'gpt-4o-mini-transcribe'}}, output: {voice: c.voice}}}});
        tkAsk({response: {instructions: 'Say only a very short phrase inviting the question (like "Yes, go ahead."), in the language ' + (AG.lang || 'ko') + '.'}});
        tkState('listening', md4T('듣는 중 — 말씀하세요'));
      };
      var offer = await pc.createOffer(); await pc.setLocalDescription(offer);
      var ans = await fetch('https://api.openai.com/v1/realtime/calls', {method: 'POST',
        headers: {'Authorization': 'Bearer ' + ek, 'Content-Type': 'application/sdp'}, body: offer.sdp});
      if (!ans.ok) throw new Error('HTTP ' + ans.status + ' — ' + (await ans.text()).slice(0, 160));
      await pc.setRemoteDescription({type: 'answer', sdp: await ans.text()});
    } catch (err){
      var m = (err && err.name === 'NotAllowedError') ? md4T('마이크를 허락해야 말로 물을 수 있어요.') : ((err && err.message) || String(err));
      tkChat('assistant', md4T('음성 대화를 시작하지 못했어요: {e}', {e: m}));
      talkEnd(md4T('시작 실패'), true);
    }
  }
  function talkEnd(why, quiet){
    if (!TK) return;
    var t = TK; TK = null;
    try { t.dc && t.dc.close(); } catch (x) {}
    try { t.pc && t.pc.close(); } catch (x) {}
    try { t.mic && t.mic.getTracks().forEach(function(tr){ tr.stop(); }); } catch (x) {}
    doc.getElementById('tk-au').srcObject = null;
    setAudioSession('playback');
    body.classList.remove('talking');
    var secs = Math.round((Date.now() - t.started) / 1000);
    tkBar.className = 'on'; setText('tk-st', md4T('대화 끝 · {t} · 약 ${c}', {t: fmtT(secs), c: t.cost.toFixed(3)})); setText('tk-meta', '');
    setTimeout(function(){ if (!TK) tkBar.className = ''; }, quiet ? 4000 : 2500);
    if ((t.wasPlaying || t.resume) && AU.e){
      if (t.resumeRow != null) listenFrom(t.resumeRow, null);
      else startAt(null);
    }
  }
  setInterval(function(){   // 조용하면 저절로 끝 · 시간·비용 표시 · 30분 상한
    if (!TK) return;
    var secs = Math.round((Date.now() - TK.started) / 1000);
    setText('tk-meta', fmtT(secs) + ' · $' + TK.cost.toFixed(3));
    if (secs > 1800) talkEnd(md4T('30분이 지나 끝냈어요'));
    else if (!TK.userTalking && !TK.botTalking && TK.dc && Date.now() - TK.last > 20000) talkEnd(md4T('조용해서 끝냈어요'));
  }, 1000);
  doc.getElementById('ag-mic').onclick = function(){ if (TK) talkEnd(md4T('끝')); else { closeDrawers(); talkStart(); } };
  doc.getElementById('tk-end').onclick = function(){ talkEnd(md4T('끝')); };
"""

_SW_JS = r"""
// 앱 껍데기 캐시 — 먼저 캐시로 띄우고 뒤에서 새로 받아 둔다. 데이터(data/·__data/)는 앱이 직접 캐시한다.
var V = '%(version)s', SHELL = 'narani-shell-' + V;
var FILES = ['./', 'index.html', 'app.js', 'manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png'];
self.addEventListener('install', function(e){
  // 새 판의 껍데기는 늘 서버에서 새로 — GitHub Pages가 10분짜리 HTTP 캐시를 주므로 그냥 받으면 옛 app.js가 섞인다
  e.waitUntil(caches.open(SHELL).then(function(c){
    return c.addAll(FILES.map(function(f){ return new Request(f, {cache: 'reload'}); }));
  }).then(function(){ return self.skipWaiting(); }));
});
self.addEventListener('activate', function(e){
  e.waitUntil(caches.keys().then(function(ks){
    return Promise.all(ks.filter(function(k){ return k.indexOf('narani-shell-') === 0 && k !== SHELL; })
      .map(function(k){ return caches.delete(k); }));
  }).then(function(){ return self.clients.claim(); }));
});
self.addEventListener('fetch', function(e){
  var req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;
  var scope = new URL(self.registration.scope).pathname, rel = url.pathname.slice(scope.length);
  if (/^(data|__data)\//.test(rel)) return;
  // 오디오·영상은 브라우저가 Range로 나눠 받는다(206) — 캐시가 끼면 iOS에서 재생·이동이 깨진다
  if (req.headers.has('range') || /\.(m4a|mp3|aac|mp4|opus|ogg|wav)$/i.test(rel)) return;
  // 앱 첫 화면으로 가는 이동만 껍데기('./')로 — 소개·개인정보처리방침 같은 다른 페이지는 그 페이지로
  var shell = req.mode === 'navigate' && (rel === '' || rel === 'index.html');
  if (req.mode === 'navigate' && !shell) return;
  e.respondWith(caches.open(SHELL).then(function(c){
    var key = shell ? './' : req;
    return c.match(key, {ignoreSearch: true}).then(function(hit){
      // 받은 것은 늘 캐시에 — 폰이 캐시 일부를 비웠어도 다음 실행부터 다시 오프라인으로 뜨게
      var net = fetch(req, {cache: 'no-cache'}).then(function(r){ if (r.ok) c.put(key, r.clone()); return r; });
      if (hit){ net.catch(function(){}); return hit; }
      return net;
    });
  }));
});
"""


def _js_strings(*codes: str) -> set[str]:
    return {m.group("s") for code in codes for m in i18n._JS_CALL.finditer(code)}


def _catalogs() -> dict[str, dict[str, str]]:
    """화면 언어별 카탈로그 — 리더 JS가 쓰는 문구만 (전부 실으면 index.html이 커진다)."""
    used = _js_strings(mobile.CORE_JS, _APP_JS, _NOTES_JS, _READER_JS, _AUDIO_JS, _TALK_JS)
    out: dict[str, dict[str, str]] = {}
    for lang in i18n.available():
        if lang == i18n.SOURCE:
            continue
        cat = i18n.catalog(lang)
        out[lang] = {k: v for k, v in cat.items() if k in used}
    return out


ICON = Path(__file__).resolve().parents[1] / "ui" / "assets" / "icon-maskable.png"  # tools/make_icon.py


def _icon_png(size: int) -> bytes:
    """앱 아이콘의 꽉 찬 사각형판(maskable·apple-touch-icon)을 size로 줄인 PNG (PIL이 없으면 원본)."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover — 데스크톱 의존성에 늘 있다
        return ICON.read_bytes()
    buf = io.BytesIO()
    Image.open(ICON).convert("RGB").resize((size, size), Image.LANCZOS).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _index_html(cfg: dict) -> str:
    payload = json.dumps(cfg, ensure_ascii=False).replace("<", "\\u003c")
    return (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">"
        "<meta name=\"color-scheme\" content=\"light dark\">"
        "<meta name=\"theme-color\" content=\"#fbfaf7\" media=\"(prefers-color-scheme: light)\">"
        "<meta name=\"theme-color\" content=\"#161615\" media=\"(prefers-color-scheme: dark)\">"
        "<meta name=\"mobile-web-app-capable\" content=\"yes\">"
        "<meta name=\"apple-mobile-web-app-capable\" content=\"yes\">"
        f"<meta name=\"apple-mobile-web-app-title\" content=\"{APP_NAME}\">"
        "<link rel=\"manifest\" href=\"manifest.webmanifest\">"
        "<link rel=\"apple-touch-icon\" href=\"icon-180.png\">"
        f"<title>{APP_NAME}</title><style>{mobile._CSS}{_PWA_CSS}</style>"
        f"<script>window.NARANI_CFG = {payload};</script></head><body>"
        "<input type=\"radio\" name=\"mode\" id=\"m-ko\" class=\"mode\" checked>"
        "<input type=\"radio\" name=\"mode\" id=\"m-en\" class=\"mode\">"
        "<input type=\"radio\" name=\"mode\" id=\"m-both\" class=\"mode\">"  # 읽기 동작이 찾는다 — 화면엔 번역/원문 토글만
        "<header class=\"top\">"
        "<button class=\"only-js tb rd back\" id=\"back\">‹</button>"
        f"<button class=\"top-title\" id=\"top-title\">{APP_NAME}</button>"
        "<button class=\"only-js tb rd\" id=\"lang-btn\"></button>"
        "<button class=\"only-js tb rd\" id=\"fs-btn\"></button>"
        "<div class=\"pop\" id=\"fs-pop\"><button class=\"fs\" id=\"fs-dn\"></button>"
        "<button class=\"fs\" id=\"fs-up\"></button></div>"
        "</header>"
        f"<main><nav id=\"lib\"><h1 id=\"lib-h\">{APP_NAME}</h1><div class=\"lib-bar\" id=\"lib-bar\"></div>"
        "<ol id=\"lib-list\"></ol><p class=\"lib-foot\"><a href=\"about.html\" id=\"lnk-about\"></a> · "
        "<a href=\"privacy.html\" id=\"lnk-privacy\"></a></p></nav><p id=\"loading\" style=\"display:none\"></p></main>"
        "<div id=\"sheet\" role=\"dialog\" aria-live=\"polite\"><div class=\"sh-grip\"></div>"
        "<div class=\"sh-label\"></div><div class=\"sh-text\"></div><div class=\"sh-acts\"></div></div>"
        # 목차 서랍(왼쪽) · 에이전트(떠 있는 단추 → 아래에서 올라오는 창) · 둘 뒤의 어둠막
        "<div id=\"scrim\"></div><div id=\"upd\" role=\"status\"></div>"
        "<nav id=\"tocd\"><div class=\"dh\"><span id=\"tocd-title\"></span><button id=\"tocd-x\">×</button></div>"
        "<ol id=\"tocd-list\"></ol></nav>"
        # 듣기 — 🎧(음성이 있는 논문) · 아래 재생 막대
        "<button id=\"lis\" class=\"rd\"><svg viewBox=\"0 0 24 24\" width=\"22\" height=\"22\" aria-hidden=\"true\">"
        "<path fill=\"currentColor\" d=\"M12 3a9 9 0 0 0-9 9v6a3 3 0 0 0 3 3h1a1 1 0 0 0 1-1v-6a1 1 0 0 0-1-1H5v-1a7 7 0 0 1"
        " 14 0v1h-2a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h1a3 3 0 0 0 3-3v-6a9 9 0 0 0-9-9z\"/></svg></button>"
        "<div id=\"player\" role=\"region\"><div class=\"pl-top\"><span id=\"pl-sec\"></span>"
        "<span class=\"pl-t\"><span id=\"pl-cur\">0:00</span> / <span id=\"pl-dur\">0:00</span></span>"
        "</div>"
        "<input type=\"range\" id=\"pl-seek\" min=\"0\" step=\"1\" value=\"0\">"
        "<div class=\"pl-ctl\"><button id=\"pl-range\"></button><button id=\"pl-prev\"><svg viewBox=\"0 0 24 24\" width=\"22\" height=\"22\" aria-hidden=\"true\"><path fill=\"currentColor\" d=\"M6 6h2v12H6zm3.5 6 8.5 6V6z\"/></svg></button>"
        "<button id=\"pl-back\">−15</button><button id=\"pl-pp\">▶</button><button id=\"pl-fwd\">+15</button>"
        "<button id=\"pl-next\"><svg viewBox=\"0 0 24 24\" width=\"22\" height=\"22\" aria-hidden=\"true\"><path fill=\"currentColor\" d=\"M6 18l8.5-6L6 6v12zM16 6v12h2V6z\"/></svg></button><button id=\"pl-spd\">1×</button><button id=\"pl-x\">×</button></div>"
        "<audio id=\"pl-au\" preload=\"none\"></audio></div>"
        "<button id=\"fab\" class=\"rd\"><span id=\"fab-n\"></span><svg class=\"fab-c\" viewBox=\"0 0 24 24\" width=\"24\" height=\"24\" aria-hidden=\"true\">"
        "<path fill=\"currentColor\" d=\"M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zM18 14H6v-2h12zm0-3H6V9h12zm0-3H6V6h12z\"/>"
        "</svg><span id=\"fab-ask\"><svg viewBox=\"0 0 24 24\" width=\"20\" height=\"20\" aria-hidden=\"true\"><path fill=\"currentColor\""
        " d=\"M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-3.1a7 7 0 0 0 6-6.9z\"/>"
        "</svg><span id=\"fab-ask-t\"></span></span></button>"
        "<section id=\"agent\" role=\"dialog\"><div class=\"dh\"><div class=\"ag-tabs\" role=\"tablist\">"
        "<button id=\"tab-notes\" role=\"tab\"></button><button id=\"tab-chat\" role=\"tab\"></button></div>"
        "<button id=\"ag-clear\"></button><button id=\"ag-set\">⚙</button><button id=\"ag-x\">×</button></div>"
        "<div id=\"ag-body\"></div><form id=\"ag-form\"><textarea id=\"ag-in\" rows=\"1\"></textarea>"
        "<button id=\"ag-mic\" type=\"button\">🎙</button><button id=\"ag-send\" type=\"submit\">↑</button></form></section>"
        # 말로 묻기 — 대화 중 상태 막대와 답 소리
        "<div id=\"talkbar\" role=\"status\"><span class=\"lv\"></span><span id=\"tk-st\"></span>"
        "<span id=\"tk-meta\"></span><button id=\"tk-end\"></button></div><audio id=\"tk-au\" autoplay playsinline></audio>"
        "<script src=\"app.js\"></script></body></html>"
    )


def site_files(*, local: bool = False, google_client_id: str = "") -> dict[str, tuple[bytes, str]]:
    """웹앱 파일들 → {이름: (바이트, MIME)}. local=True면 같은 주소의 `data/`를 저장소로 쓴다(미리보기)."""
    from narani import config

    cfg = {"app_name": APP_NAME, "source_lang": i18n.SOURCE, "fallback_lang": "en", "local": local,
           "ai_models": {p: list(m) for p, m in config.MODEL_TIERS.items()},  # 모바일 에이전트의 모델 고르기
           "google_client_id": google_client_id, "catalogs": _catalogs()}
    app_js = (_PRELUDE_JS + "(function(){\n" + sentpair.JS + mobile.CORE_JS + _APP_JS + _NOTES_JS + _READER_JS
              + _AUDIO_JS + _TALK_JS              + "})();\n")
    manifest = {
        "name": APP_NAME, "short_name": APP_NAME, "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#fbfaf7", "theme_color": "#fbfaf7",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    }
    files: dict[str, tuple[bytes, str]] = {
        "index.html": (_index_html(cfg).encode("utf-8"), "text/html; charset=utf-8"),
        "app.js": (app_js.encode("utf-8"), "text/javascript; charset=utf-8"),
        "manifest.webmanifest": (json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
                                 "application/manifest+json"),
        "icon-180.png": (_icon_png(180), "image/png"),
        "icon-192.png": (_icon_png(192), "image/png"),
        "icon-512.png": (_icon_png(512), "image/png"),
        # 소개·개인정보처리방침 — Google 동의 화면이 링크하는 페이지(같은 도메인이어야 한다) (§phone.pages)
        "about.html": (pages.ABOUT_HTML.encode("utf-8"), "text/html; charset=utf-8"),
        "privacy.html": (pages.PRIVACY_HTML.encode("utf-8"), "text/html; charset=utf-8"),
    }
    # 껍데기가 바뀌면 서비스 워커도 바뀌어야 새 판을 받는다 — 내용 지문을 캐시 이름에
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + files[name][0])
    files["sw.js"] = ((_SW_JS % {"version": h.hexdigest()[:12]}).encode("utf-8"), "text/javascript; charset=utf-8")
    return files


def write_site(out: Path, cname: str | None = pages.DOMAIN, **kw: object) -> list[Path]:
    """웹앱 파일을 폴더에 쓴다 (정적 호스팅에 올릴 것). cname이 있으면 GitHub Pages 사용자 도메인 파일(CNAME)도."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (data, _mime) in site_files(**kw).items():  # type: ignore[arg-type]
        (out / name).write_bytes(data)
        written.append(out / name)
    # GitHub Pages가 Jekyll로 다시 굽지 않고 파일 그대로 내보내게
    (out / ".nojekyll").write_bytes(b"")
    written.append(out / ".nojekyll")
    if cname:  # 없으면 Pages가 사용자 도메인 설정을 지운다(배포할 때마다 함께 올려야 한다)
        (out / "CNAME").write_text(cname + "\n", encoding="ascii")
        written.append(out / "CNAME")
    return written


def make_server(data_dir: Path, port: int = 0, host: str = "127.0.0.1",
                google_client_id: str = "") -> ThreadingHTTPServer:
    """미리보기 서버 — 웹앱 + `data/`에 로컬 저장소 폴더. 기본은 127.0.0.1에만 붙는다(시험용)."""
    # Google 클라이언트가 있으면 Drive가 저장소다(로컬 data/는 그대로 두지만 앱은 쓰지 않는다)
    files = site_files(local=not google_client_id, google_client_id=google_client_id)
    root = Path(data_dir).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:  # 조용히
            pass

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0].split("#", 1)[0].lstrip("/") or "index.html"
            if path.startswith("data/"):
                target = (root / path[5:]).resolve()
                if root not in target.parents or not target.is_file():
                    self.send_error(404)
                    return
                body = target.read_bytes()
                mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            elif path in files:
                body, mime = files[path]
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self) -> None:  # noqa: N802 — 폰이 쓰는 기록(mobile/…)만 받는다
            path = self.path.split("?", 1)[0].lstrip("/")
            target = (root / path[5:]).resolve() if path.startswith("data/mobile/") else None
            size = int(self.headers.get("Content-Length") or 0)
            if target is None or (root / "mobile") not in target.parents or not 0 < size <= 2_000_000:
                self.send_error(403)
                return
            data = self.rfile.read(size)
            try:
                json.loads(data)
            except ValueError:
                self.send_error(400)
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.send_response(204)
            self.end_headers()

    return ThreadingHTTPServer((host, port), Handler)


def serve_in_background(data_dir: Path, port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread]:
    srv = make_server(data_dir, port)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, th
