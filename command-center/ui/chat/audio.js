/* ============================================================
   chat/audio.js — микрофон чата: запись → WAV 16 кГц моно PCM16.

   POST /api/oss/speech/transcribe принимает только сырой PCM16 WAV
   (8–48 кГц, до 600 с). Браузер записывает webm/ogg, поэтому запись
   декодируется и пересэмплируется здесь. Преобразование перенесено из
   ui/jeff.js (blobToWav16k); jeff.js не импортируется — у Jeff свой сервер.

   При импорте модуль не трогает ни DOM, ни аудио-API: encodeWavPcm16
   проверяется в node.
   ============================================================ */

export const TARGET_RATE = 16000;
export const MAX_RECORD_MS = 590 * 1000;   // сервер принимает до 600 с

/** Float32 [-1..1] → WAV (RIFF, PCM16, моно). Возвращает ArrayBuffer. */
export function encodeWavPcm16(samples, rate = TARGET_RATE) {
  const pcm = samples || new Float32Array(0);
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o, s) => { for (let i = 0; i < s.length; i += 1) out.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF');
  out.setUint32(4, 36 + pcm.length * 2, true);
  w(8, 'WAVE');
  w(12, 'fmt ');
  out.setUint32(16, 16, true);          // размер блока fmt
  out.setUint16(20, 1, true);           // PCM
  out.setUint16(22, 1, true);           // моно
  out.setUint32(24, rate, true);
  out.setUint32(28, rate * 2, true);    // байт в секунду
  out.setUint16(32, 2, true);           // выравнивание кадра
  out.setUint16(34, 16, true);          // бит на отсчёт
  w(36, 'data');
  out.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i += 1) {
    const v = Math.max(-1, Math.min(1, pcm[i]));
    out.setInt16(44 + i * 2, Math.round(v * 0x7fff), true);
  }
  return out.buffer;
}

/** Запись браузера (webm/ogg) → WAV 16 кГц моно (Blob audio/wav). */
export async function blobToWav16k(blob) {
  const buf = await blob.arrayBuffer();
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  let decoded;
  try {
    decoded = await ctx.decodeAudioData(buf);
  } finally {
    try { ctx.close(); } catch { /* уже закрыт */ }
  }
  const length = Math.max(1, Math.ceil(decoded.duration * TARGET_RATE));
  const off = new OfflineAudioContext(1, length, TARGET_RATE);
  const src = off.createBufferSource();
  src.buffer = decoded;
  src.connect(off.destination);
  src.start();
  const rendered = await off.startRendering();
  return new Blob([encodeWavPcm16(rendered.getChannelData(0), TARGET_RATE)], { type: 'audio/wav' });
}

/** Почему микрофон недоступен в этом окне ('' — доступен). */
export function micUnsupportedReason() {
  if (typeof navigator === 'undefined' || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return 'Браузер не даёт доступ к микрофону на этой странице';
  }
  if (typeof window === 'undefined' || typeof window.MediaRecorder !== 'function') {
    return 'Браузер не умеет записывать звук (нет MediaRecorder)';
  }
  if (!(window.AudioContext || window.webkitAudioContext) || typeof OfflineAudioContext !== 'function') {
    return 'Браузер не умеет перекодировать запись (нет AudioContext)';
  }
  return '';
}

/** Одна запись с микрофона. stop() → Blob; cancel() — выбросить без распознавания. */
export class Recorder {
  constructor() {
    this.stream = null;
    this.rec = null;
    this.chunks = [];
    this.startedAt = 0;
    this.done = null;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.chunks = [];
    /* onstop приходит позже самого stop(): cancel() к этому времени уже обнулил this.rec,
       поэтому обработчик держит собственную ссылку (иначе TypeError «reading 'mimeType'» на каждой отмене записи) */
    const rec = new MediaRecorder(this.stream);
    this.rec = rec;
    const chunks = this.chunks;
    this.done = new Promise((resolve) => {
      rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = () => resolve(new Blob(chunks, { type: rec.mimeType || 'audio/webm' }));
    });
    rec.start(250);
    this.startedAt = Date.now();
  }

  elapsed() { return this.startedAt ? Date.now() - this.startedAt : 0; }

  _release() {
    if (this.stream) {
      for (const t of this.stream.getTracks()) { try { t.stop(); } catch { /* уже остановлен */ } }
    }
    this.stream = null;
  }

  async stop() {
    if (!this.rec) return null;
    if (this.rec.state !== 'inactive') this.rec.stop();
    const blob = await this.done;
    this._release();
    this.rec = null;
    return blob;
  }

  cancel() {
    if (this.rec && this.rec.state !== 'inactive') {
      try { this.rec.stop(); } catch { /* уже остановлен */ }
    }
    this.rec = null;
    this.chunks = [];
    this._release();
  }
}
