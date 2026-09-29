"""Original 22 s soundtrack for the Bossman promo, synthesized from scratch (no samples).
120 BPM, D minor -> D major lift at the logo impact (13.0 s), then an epic D-major chapter (15-21 s) and a final hit at 21.0 s. Mixes the Kokoro VO with sidechain ducking."""
import json
import sys
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt, fftconvolve, resample_poly

WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')   # dir holding voice/ from voice.py
SR, DUR = 48000, 22.0
N = int(SR * DUR)
rng = np.random.default_rng(7)
L = np.zeros(N); R = np.zeros(N)
t_all = np.arange(N) / SR

def hz(m): return 440.0 * 2 ** ((m - 69) / 12)
def env_adsr(n, a, d, s, r, total):
    e = np.zeros(n); A, D_, Rr = int(a*SR), int(d*SR), int(r*SR); hold = max(int(total*SR) - A - D_, 0)
    seg = np.concatenate([np.linspace(0, 1, max(A,1)), np.linspace(1, s, max(D_,1)), np.full(hold, s), np.linspace(s, 0, max(Rr,1))])
    e[:min(n, len(seg))] = seg[:n]; return e
def put(buf, start, sig, gain=1.0):
    i = int(start * SR); j = min(i + len(sig), N)
    if i < N and j > i: buf[i:j] += sig[:j-i] * gain
def stereo(start, sig, gain=1.0, pan=0.0):
    put(L, start, sig, gain * np.sqrt(0.5 * (1 - pan))); put(R, start, sig, gain * np.sqrt(0.5 * (1 + pan)))
def lp(x, f, order=2): return sosfilt(butter(order, f, 'low', fs=SR, output='sos'), x)
def hp(x, f, order=2): return sosfilt(butter(order, f, 'high', fs=SR, output='sos'), x)
def bp(x, lo, hi): return sosfilt(butter(2, [lo, hi], 'band', fs=SR, output='sos'), x)
def saw(f, n, detune=(0,)):
    t = np.arange(n) / SR; out = np.zeros(n)
    for d in detune:
        ph = (f * (1 + d) * t + rng.random()) % 1.0; out += 2 * ph - 1
    return out / len(detune)

# ---------- instruments
def kick(g=1.0):
    n = int(0.45*SR); t = np.arange(n)/SR
    f = 42 + 110*np.exp(-t*32); ph = 2*np.pi*np.cumsum(f)/SR
    s = np.sin(ph)*np.exp(-t*7.5) + 0.35*np.exp(-t*140)*rng.standard_normal(n)*0.3
    return np.tanh(1.6*s)*g
def clap():
    n = int(0.35*SR); t = np.arange(n)/SR; nz = bp(rng.standard_normal(n), 900, 5000)
    e = np.zeros(n)
    for o in (0, .011, .022): e += (t >= o) * np.exp(-np.clip(t-o, 0, None)*(90 if o < .02 else 18))
    return nz*e*0.55
def hat(open_=False):
    n = int((0.22 if open_ else 0.06)*SR); t = np.arange(n)/SR
    return hp(rng.standard_normal(n), 7000)*np.exp(-t*(14 if open_ else 70))*0.35
def pluck(m, dur=0.22):
    n = int((dur+0.3)*SR); t = np.arange(n)/SR; f = hz(m)
    s = saw(f, n, (-0.004, 0.004)); cut = 600 + 5200*np.exp(-t*18)
    y = np.zeros(n); blk = 480
    for k in range(0, n, blk):
        y[k:k+blk] = lp(s[k:k+blk], min(cut[k], 20000))  # crude sweep
    return y*np.exp(-t*9)*0.5
def pad(ms, dur, bright=1800, g=0.18):
    n = int((dur+0.8)*SR); s = np.zeros(n)
    for m in ms: s += saw(hz(m), n, (-0.006, 0, 0.007))
    return hp(lp(s, bright, 3), 170)*env_adsr(n, 0.25, 0.3, 0.8, 0.8, dur)*g/len(ms)**0.5
def sub(m, dur):
    n = int(dur*SR); t = np.arange(n)/SR
    return np.sin(2*np.pi*hz(m)*t)*env_adsr(n, 0.005, 0.08, 0.7, 0.05, dur - 0.05)*0.55
def riser(dur, f0=300, f1=6000, g=0.25):
    n = int(dur*SR); t = np.arange(n)/SR; k = t/dur
    nz = rng.standard_normal(n); y = np.zeros(n); blk = 960
    for i in range(0, n, blk):
        c = f0*(f1/f0)**k[i]; y[i:i+blk] = bp(nz[i:i+blk], c*0.7, min(c*1.4, 20000))
    tone = np.sin(2*np.pi*np.cumsum(220*(4**k))/SR)*0.25
    return (y + tone)*k**2*g
def whoosh(dur=0.35, g=0.25):
    n = int(dur*SR); t = np.arange(n)/SR
    return bp(rng.standard_normal(n), 800, 7000)*np.sin(np.pi*t/dur)**2*g
def boom():
    n = int(2.6*SR); t = np.arange(n)/SR
    f = 30 + 55*np.exp(-t*6); s = np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-t*1.6)
    crash = hp(rng.standard_normal(n), 3000)*np.exp(-t*2.2)*0.35
    return np.tanh(1.3*s)*0.9 + crash
def bell(m, dur=1.2):
    n = int(dur*SR); t = np.arange(n)/SR; f = hz(m)
    return (np.sin(2*np.pi*f*t) + 0.4*np.sin(2*np.pi*f*2.76*t)*np.exp(-t*6) + 0.2*np.sin(2*np.pi*f*5.4*t)*np.exp(-t*10))*np.exp(-t*3.2)*0.22
def tick():
    n = int(0.03*SR); t = np.arange(n)/SR
    return bp(rng.standard_normal(n), 2500, 6000)*np.exp(-t*220)*0.35

# ---------- arrangement (beat = 0.5 s)
D, F, A, Bb, C, G, E = 50, 53, 57, 46, 48, 55, 52     # MIDI roots around D3
CH = {'Dm': [50, 53, 57, 62], 'Bb': [46, 50, 53, 58], 'F': [45, 48, 53, 57], 'C': [48, 52, 55, 60], 'D': [50, 54, 57, 62, 64]}
prog = ['Dm', 'Bb', 'F', 'C'] * 3
# intro 0-3: drone + clock ticks + riser into the drop
stereo(0.0, pad([38, 50, 57], 3.2, bright=700, g=0.22), 1.0)
for i in range(12): stereo(0.25*i + 0.0, tick(), 0.8 if i % 4 == 0 else 0.45, pan=0.3 if i % 2 else -0.3)
stereo(1.2, riser(1.8, 200, 8000, 0.22))
stereo(0.5, whoosh(0.4, 0.15)); stereo(1.85, whoosh(0.35, 0.12))
# groove 3.0-10.0 (+ light continuation to 11.5)
for b in range(int((11.5 - 3.0) / 0.5)):
    tb = 3.0 + 0.5*b
    stereo(tb, kick(0.95))
    if b % 2 == 1: stereo(tb, clap(), 0.9)
    stereo(tb + 0.25, hat(), 0.9, pan=0.25)
    if b % 4 == 3: stereo(tb + 0.25, hat(True), 0.6, pan=-0.3)
for c in range(10):                                  # chord per second 3.0-13.0
    tc = 3.0 + c; name = prog[c]; notes = CH[name]
    if tc < 12.5:
        stereo(tc, pad(notes, 1.0, bright=1400 + 180*c, g=0.16))
        root = notes[0] - 12
        for e in range(8):                             # 8th-note sub bass (ducked by kick)
            if tc + 0.125*e*1 < 12.5: stereo(tc + 0.125*e, sub(root, 0.11), 0.32 if e % 2 else 0.55)
        arp = [notes[0]+12, notes[2]+12, notes[1]+12, notes[3]+12]
        for s16 in range(8):                           # 16th arp
            ts = tc + 0.125*s16
            if 4.0 <= ts < 12.25:
                p = 0.45 if s16 % 2 else -0.45
                stereo(ts, pluck(arp[s16 % 4] + (12 if s16 >= 4 and c >= 5 else 0)), 0.55, pan=p)
                stereo(ts + 0.375, pluck(arp[s16 % 4], 0.12), 0.18, pan=-p)   # dotted-8th echo
# feature stabs on the words 6.0 .. 9.0
for k, tf in enumerate([6.0, 6.75, 7.5, 8.25, 9.0]):
    stereo(tf, pad([62, 65, 69, 74][:3] if k % 2 == 0 else [58, 62, 65], 0.18, bright=5000, g=0.22), 1.0, pan=(k-2)*0.2)
    stereo(tf - 0.12, whoosh(0.14, 0.18), pan=(k-2)*0.3)
# build 10.0-12.5: snare roll + riser, gap, impact at 13.0
roll_t = 11.0
while roll_t < 12.45:
    step = 0.25 if roll_t < 11.5 else (0.125 if roll_t < 12.0 else 0.0625)
    stereo(roll_t, clap(), 0.35 + 0.5*(roll_t - 11.0)/1.5); roll_t += step
stereo(10.2, riser(2.3, 300, 9000, 0.3))
rev = riser(0.5, 2000, 200, 0.2)[::-1]; stereo(12.5, rev)
stereo(13.0, boom(), 1.0)
stereo(13.0, pad(CH['D'] + [69, 74], 2.0, bright=3800, g=0.32))
stereo(13.0, sub(38, 1.9), 0.9)
for k, m in enumerate([74, 78, 81, 86, 88]): stereo(13.08 + 0.12*k, bell(m), 0.7, pan=-0.5 + 0.25*k)

# ---------- chapter 2 (15-22 s): epic projection section in D major
def tom(f0=110, g=0.8):
    n = int(0.5*SR); t = np.arange(n)/SR
    f = f0*(1 + 0.8*np.exp(-t*30)); s = np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-t*6)
    return np.tanh(1.5*(s + 0.15*lp(rng.standard_normal(n), 900)*np.exp(-t*25)))*g
def big_snare():
    n = int(0.9*SR); t = np.arange(n)/SR
    body = np.sin(2*np.pi*190*t)*np.exp(-t*18)*0.5
    nz = bp(rng.standard_normal(n), 1200, 9000)*np.exp(-t*7)
    return (body + nz)*0.6
def choir(ms, dur, g=0.2):
    n = int((dur + 1.0)*SR); t = np.arange(n)/SR; s = np.zeros(n)
    for m in ms:
        vib = 1 + 0.004*np.sin(2*np.pi*5.2*t + rng.random()*6)
        ph = np.cumsum(hz(m)*vib)/SR
        for d in (-0.005, 0.0, 0.006):
            s += 2*((ph*(1 + d) + rng.random()) % 1.0) - 1
    s = bp(s, 550, 900)*0.9 + bp(s, 1050, 1400)*0.6 + bp(s, 2300, 2900)*0.25   # "ah" formants
    return s*env_adsr(n, 0.35, 0.3, 0.85, 1.0, dur)*g/len(ms)**0.5
def brass(ms, dur=0.45, g=0.3):
    n = int((dur + 0.4)*SR); t = np.arange(n)/SR; s = np.zeros(n)
    for m in ms: s += saw(hz(m), n, (-0.004, 0.004))
    y = np.zeros(n); cut = 900 + 4200*np.exp(-t*7); blk = 480
    for k in range(0, n, blk): y[k:k+blk] = lp(s[k:k+blk], min(cut[k], 20000))
    return hp(y, 120)*env_adsr(n, 0.012, 0.15, 0.7, 0.35, dur)*g/len(ms)**0.5

CH2 = {'D': [50, 54, 57, 62], 'Bm': [47, 50, 54, 59], 'G': [43, 50, 55, 59], 'A': [45, 49, 52, 57]}
prog2 = ['D', 'Bm', 'G', 'A', 'D', 'Bm']
stereo(14.0, riser(1.0, 250, 9000, 0.3)); stereo(14.55, riser(0.45, 3000, 300, 0.18)[::-1])
stereo(15.0, boom(), 0.75)
for c, name in enumerate(prog2):                                   # 15.0 .. 21.0, one chord per second
    tc = 15.0 + c; notes = CH2[name]
    stereo(tc, choir([n + 12 for n in notes[1:]] + [notes[0] + 24], 1.0, 0.24))
    stereo(tc, pad(notes, 1.0, bright=2600, g=0.16))
    for e in range(8):
        stereo(tc + 0.125*e, sub(notes[0] - 12, 0.11), 0.5 if e % 2 else 0.7)
    arp = [notes[0] + 24, notes[2] + 24, notes[1] + 24, notes[3] + 24, notes[2] + 24, notes[1] + 36]
    for s16 in range(8):
        ts = tc + 0.125*s16
        if ts < 20.9:
            p = 0.5 if s16 % 2 else -0.5
            stereo(ts, pluck(arp[s16 % 6], 0.16), 0.42, pan=p)
for b in range(int((20.0 - 15.0)/0.5)):                             # half-time epic drums
    tb = 15.0 + 0.5*b
    stereo(tb, kick(1.0))
    if b % 2 == 1: stereo(tb, big_snare(), 0.85)
    for h in range(2): stereo(tb + 0.25*h + 0.125, hat(), 0.6, pan=0.3 if h else -0.3)
    if b % 4 == 3:                                                  # taiko fill into each bar
        for k, f0 in enumerate((140, 120, 100, 82)): stereo(tb + 0.125*k, tom(f0, 0.7), 1.0, pan=-0.4 + 0.27*k)
for k, tv in enumerate([17.0, 17.9, 18.8, 19.7]):                  # brass hits on each version
    name = ['G', 'A', 'D', 'Bm'][k]
    stereo(tv, brass([n + 12 for n in CH2[name]], 0.4, 0.34), pan=(k - 1.5)*0.15)
    stereo(tv, tom(70, 0.9)); stereo(tv - 0.1, whoosh(0.12, 0.16))
roll_t = 20.0                                                       # build to the final hit
while roll_t < 20.97:
    step = 0.125 if roll_t < 20.5 else 0.0625
    stereo(roll_t, big_snare(), 0.25 + 0.55*(roll_t - 20.0)); roll_t += step
stereo(20.0, riser(1.0, 300, 11000, 0.32))
stereo(21.0, boom(), 1.1)
stereo(21.0, choir([62, 66, 69, 74, 78], 1.0, 0.34))
stereo(21.0, pad([38, 50, 57, 62, 66, 69], 1.0, bright=4200, g=0.3))
stereo(21.0, sub(38, 1.0), 0.9)
for k, m in enumerate([74, 78, 81, 86, 90]): stereo(21.05 + 0.1*k, bell(m), 0.6, pan=-0.5 + 0.25*k)

# ---------- reverb on the music bus
n_ir = int(2.4*SR); ti = np.arange(n_ir)/SR
irL = rng.standard_normal(n_ir)*np.exp(-ti*2.6); irR = rng.standard_normal(n_ir)*np.exp(-ti*2.6)
irL = lp(irL, 6000); irR = lp(irR, 6000); irL /= np.abs(irL).sum()**0.5 * 6; irR /= np.abs(irR).sum()**0.5 * 6
wetL = fftconvolve(L, irL)[:N]; wetR = fftconvolve(R, irR)[:N]
mL, mR = L + 0.35*wetL, R + 0.35*wetR
mL, mR = hp(mL, 28), hp(mR, 28)
# high-pass sweep on the build (10.0 -> 12.5), silence gap feel
for arr in (mL, mR):
    i0, i1 = int(10.0*SR), int(12.5*SR)
    seg = arr[i0:i1]; out = np.zeros_like(seg); blk = 2400
    for k in range(0, len(seg), blk):
        f = 30 * (600/30)**(k/len(seg)); out[k:k+blk] = hp(seg[k:k+blk], f)
    arr[i0:i1] = out

# ---------- voice-over
seg = json.load(open(WORK / 'voice' / 'seg.json'))
starts = [0.45, 1.8, 3.05, 4.2, 6.0, 6.75, 7.5, 8.25, 9.0, 10.1, 11.65, 13.12,
          15.1, 17.0, 17.9, 18.8, 19.7, 21.02]
vo = np.zeros(N)
for s, st in zip(seg, starts):
    a, sr = sf.read(WORK / 'voice' / f"seg{s['i']:02d}.wav"); a = resample_poly(a, SR, sr)
    a = a / np.abs(a).max() * 0.9
    put(vo, st, a)
vo = hp(vo, 90); vo = vo + 0.6*bp(vo, 2500, 6000)*0.3           # presence
vo = np.tanh(vo*1.4)/np.tanh(1.4)                                  # gentle compression
vo_rev = fftconvolve(vo, irL[:int(0.6*SR)]*0.5)[:N]
# sidechain ducking from VO envelope
envv = lp(np.abs(vo), 12); envv = envv / (envv.max() + 1e-9)
duck = 1 - 0.55*np.clip(envv*3, 0, 1)
mL *= duck; mR *= duck
outL = 0.62*mL + 0.95*vo + 0.15*vo_rev
outR = 0.62*mR + 0.95*vo + 0.15*vo_rev
fade = np.ones(N); fi = int(21.55*SR); fade[fi:] = np.linspace(1, 0, N - fi)**1.5
fade[:int(0.02*SR)] = np.linspace(0, 1, int(0.02*SR))
st = np.stack([outL*fade, outR*fade], 1)
st = np.tanh(st*1.2)/np.tanh(1.2)
st = st / np.abs(st).max() * 0.93
sf.write(WORK / 'soundtrack.wav', st.astype(np.float32), SR)
sf.write(WORK / 'vo_only.wav', vo.astype(np.float32)/max(np.abs(vo).max(),1e-9)*0.9, SR)
print('ok', st.shape)
