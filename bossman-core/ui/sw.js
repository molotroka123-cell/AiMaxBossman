// Минимальный service worker: достаточно для установки PWA на экран телефона.
// Данные всегда живые (WS + API), поэтому ничего не кэшируем, кроме оболочки.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("fetch", () => {});
