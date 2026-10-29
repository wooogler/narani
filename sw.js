
// 앱 껍데기 캐시 — 먼저 캐시로 띄우고 뒤에서 새로 받아 둔다. 데이터(data/·__data/)는 앱이 직접 캐시한다.
var V = '27cc08f2c89e', SHELL = 'narani-shell-' + V;
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
