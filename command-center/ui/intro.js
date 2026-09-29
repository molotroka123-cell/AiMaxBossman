// Заставка при первом открытии окна Bossman (5 с). Классический скрипт, а не
// модуль: он подключается первым в <body> и накрывает страницу до первой
// отрисовки приложения. Раз за сессию окна (sessionStorage); не показывается
// под автоматизацией (navigator.webdriver), при prefers-reduced-motion, на
// узких экранах, с ?nointro и при localStorage 'bossman.intro' = 'off'.
(function () {
  var KEY = 'bossman.intro.played';
  function get(store, key) { try { return window[store].getItem(key); } catch (e) { return null; } }
  function set(store, key, value) { try { window[store].setItem(key, value); } catch (e) { /* без хранилища — просто покажем ещё раз */ } }
  if (navigator.webdriver || get('sessionStorage', KEY) || get('localStorage', 'bossman.intro') === 'off'
      || /[?&]nointro\b/.test(location.search) || window.innerWidth < 700
      || (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
  set('sessionStorage', KEY, '1');

  var overlay = document.createElement('div');
  overlay.id = 'bossman-intro';
  overlay.setAttribute('aria-hidden', 'true');
  overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#0a0c10;display:flex;'
    + 'align-items:center;justify-content:center;cursor:pointer;opacity:1;transition:opacity .45s ease';
  var video = document.createElement('video');
  video.src = 'intro/bossman-intro.webm';
  video.muted = true; video.autoplay = true; video.playsInline = true; video.preload = 'auto';
  video.style.cssText = 'width:100%;height:100%;object-fit:cover;background:#0a0c10';
  overlay.appendChild(video);
  document.body.appendChild(overlay);

  var done = false, timer;
  function close() {
    if (done) return; done = true; clearTimeout(timer);
    document.removeEventListener('keydown', close, true);
    overlay.style.opacity = '0';
    setTimeout(function () { overlay.remove(); }, 500);
  }
  video.addEventListener('ended', close);
  video.addEventListener('error', close);
  overlay.addEventListener('click', close);
  document.addEventListener('keydown', close, true);
  timer = setTimeout(close, 7000);  // страховка, если видео зависло
  var started = video.play();
  if (started && started.catch) started.catch(close);
})();
