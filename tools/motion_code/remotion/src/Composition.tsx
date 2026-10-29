import React from "react";
import {
  AbsoluteFill,
  Audio,
  Composition,
  Easing,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont as loadAnton } from "@remotion/google-fonts/Anton";
import { loadFont as loadInter } from "@remotion/google-fonts/InterTight";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";

// Bossman rap motion v2 — Remotion. Same 20 s ACE-Step track as v1; scene cuts on the Whisper word timestamps.
// Direction (taste-skill / impeccable / emil-design-eng): whole fields of colour, oversized condensed type,
// the subject's own idea (a receipt for every feature), no glows, no scale-from-zero, strong custom easing.

const { fontFamily: DISPLAY } = loadAnton();
const { fontFamily: UI } = loadInter("normal", { weights: ["500", "700", "900"] });
const { fontFamily: MONO } = loadMono("normal", { weights: ["500", "700"] });

const FPS = 60;
const BPM = 96;
const BEAT = (60 / BPM) * FPS; // 37.5 frames
const INK = "#0e0e0e";
const PAPER = "#f1ede4";
const SIGNAL = "#ff4f00";
const BLUE = "#1f4dff";

const EASE_OUT = Easing.bezier(0.23, 1, 0.32, 1);
const EASE_IO = Easing.bezier(0.77, 0, 0.175, 1);

const io = (f: number, a: number, d: number, ease = EASE_OUT) =>
  interpolate(f, [a, a + d], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease });
const beatPulse = (f: number) => Math.exp(-7 * ((f % BEAT) / BEAT));

// Scene boundaries in beats (from the vocal: Local@3.50s Pult@5.88s Every@8.38s Bossman!@11.14s Bossman!@15.96s)
const CUTS = [0, 3, 5.5, 9, 13, 17.5, 21.5, 25.5, 32].map((b) => Math.round(b * BEAT));

/** A line of text revealed by a mask: slides up from below its own baseline (no fade-from-nothing). */
const MaskLine: React.FC<{ text: string; at: number; size: number; color: string; font?: string; weight?: number; tracking?: number; dur?: number }> = ({
  text, at, size, color, font = DISPLAY, weight = 400, tracking = 0, dur = 16,
}) => {
  const f = useCurrentFrame();
  const k = io(f, at, dur);
  return (
    <div style={{ overflow: "hidden", lineHeight: 0.92, paddingBottom: size * 0.04 }}>
      <div style={{ transform: `translateY(${(1 - k) * 105}%)`, fontFamily: font, fontWeight: weight, fontSize: size, color, letterSpacing: tracking, whiteSpace: "nowrap" }}>
        {text}
      </div>
    </div>
  );
};

/** Colour field that wipes in from the left on the cut. */
const Field: React.FC<{ color: string; children: React.ReactNode }> = ({ color, children }) => {
  const f = useCurrentFrame();
  const k = io(f, 0, 14, EASE_IO);
  return <AbsoluteFill style={{ clipPath: `inset(0 ${(1 - k) * 100}% 0 0)`, background: color }}>{children}</AbsoluteFill>;
};

const Corner: React.FC<{ left: string; right: string; color: string }> = ({ left, right, color }) => (
  <>
    <div style={{ position: "absolute", left: 72, top: 56, fontFamily: UI, fontWeight: 700, fontSize: 22, letterSpacing: 4, color }}>{left}</div>
    <div style={{ position: "absolute", right: 72, top: 56, fontFamily: UI, fontWeight: 500, fontSize: 22, color, opacity: 0.7 }}>{right}</div>
  </>
);

// 1 — BOSSMAN, stacked, on signal orange
const SceneTitle: React.FC = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: f - 40, fps, config: { damping: 18, stiffness: 120 } });
  return (
    <Field color={SIGNAL}>
      <Corner left="BOSSMAN" right="v2.6 · local-first" color={INK} />
      <div style={{ position: "absolute", left: 110, top: 130 }}>
        <MaskLine text="BOSS" at={2} size={420} color={INK} />
        <MaskLine text="MAN" at={14} size={420} color={PAPER} />
      </div>
      <div style={{ position: "absolute", right: 110, bottom: 130, textAlign: "right", transform: `translateX(${(1 - s) * 60}px)`, opacity: s }}>
        <div style={{ fontFamily: UI, fontWeight: 900, fontSize: 58, color: INK, lineHeight: 1.05 }}>The AI that runs</div>
        <div style={{ fontFamily: UI, fontWeight: 900, fontSize: 58, color: INK, lineHeight: 1.05 }}>your PC.</div>
      </div>
    </Field>
  );
};

// 2 — ONE BRAIN. ONE BASE. ONE STOP.
const SceneStop: React.FC = () => {
  const f = useCurrentFrame();
  const stopK = io(f, BEAT * 1.6, 12, EASE_IO);
  return (
    <Field color={INK}>
      <div style={{ position: "absolute", left: 110, top: 170 }}>
        <MaskLine text="ONE BRAIN." at={0} size={210} color={PAPER} dur={12} />
        <MaskLine text="ONE BASE." at={BEAT * 0.8} size={210} color={PAPER} dur={12} />
      </div>
      <AbsoluteFill style={{ background: SIGNAL, clipPath: `circle(${stopK * 140}% at 78% 62%)`, justifyContent: "center", alignItems: "center" }}>
        <div style={{ fontFamily: DISPLAY, fontSize: 380, color: INK, lineHeight: 1 }}>ONE STOP.</div>
        <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 34, color: INK, marginTop: 10 }}>Owner STOP outranks every agent.</div>
      </AbsoluteFill>
    </Field>
  );
};

// 3 — LOCAL ON THE MAX: one giant real number
const SceneLocal: React.FC = () => {
  const f = useCurrentFrame();
  const n = Math.round(interpolate(f, [6, 50], [0, 128], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE_OUT }));
  const rows: [string, string, number][] = [
    ["Cold model load, SSD D:", "2.47× faster", 0.95],
    ["Voice reply", "11.2 → 4.8 s", 0.57],
    ["Data leaving the PC", "0 bytes by default", 0.015],
  ];
  return (
    <Field color={PAPER}>
      <Corner left="LOCAL ON THE MAX" right="Ryzen AI Max+ 395 · Radeon 8060S" color={INK} />
      <div style={{ position: "absolute", left: 96, top: 110, fontFamily: DISPLAY, fontSize: 540, color: INK, lineHeight: 1 }}>
        {n}
        <span style={{ fontSize: 190, color: SIGNAL, marginLeft: 20 }}>GB</span>
      </div>
      <div style={{ position: "absolute", left: 110, bottom: 112, fontFamily: UI, fontWeight: 700, fontSize: 40, color: INK, opacity: io(f, 30, 14) }}>
        of unified memory on the desk, not in someone's cloud.
      </div>
      <div style={{ position: "absolute", right: 110, top: 290, width: 620 }}>
        {rows.map(([label, value, w], i) => {
          const k = io(f, 24 + i * 12, 30, EASE_IO);
          return (
            <div key={label} style={{ marginBottom: 54, opacity: Math.min(1, k * 3) }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontFamily: UI, fontSize: 30, color: INK }}>
                <span style={{ fontWeight: 500 }}>{label}</span>
                <span style={{ fontWeight: 900 }}>{value}</span>
              </div>
              <div style={{ height: 22, background: "rgba(14,14,14,.1)", marginTop: 12 }}>
                <div style={{ height: "100%", width: `${Math.max(0.012, w) * k * 100}%`, background: i === 0 ? SIGNAL : INK }} />
              </div>
            </div>
          );
        })}
      </div>
    </Field>
  );
};

// 4 — PULT IN MY POCKET: a working phone, messages land on the beat
const ScenePult: React.FC = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const msgs: [string, boolean][] = [
    ["Сделай отчёт и пришли", true],
    ["Готово · квитанция sha256 5c2195d9", false],
    ["STOP", true],
    ["Остановлено. Ничего не тронуто.", false],
  ];
  const phoneIn = spring({ frame: f, fps, config: { damping: 20, stiffness: 110 } });
  return (
    <Field color={INK}>
      <div style={{ position: "absolute", left: 960, top: 210 }}>
        <MaskLine text="PULT IN" at={6} size={200} color={PAPER} />
        <MaskLine text="MY POCKET" at={BEAT * 0.9} size={200} color={SIGNAL} />
        <div style={{ fontFamily: UI, fontWeight: 500, fontSize: 34, color: PAPER, opacity: io(f, BEAT * 2, 14) * 0.75, marginTop: 24 }}>
          iPhone · Telegram · Command Center
        </div>
      </div>
      <div style={{ position: "absolute", left: 200, top: 90, width: 560, height: 900, borderRadius: 72, background: "#191919", border: "3px solid #2c2c2c",
        transform: `translateY(${(1 - phoneIn) * 500}px) rotate(${(1 - phoneIn) * -6}deg)`, padding: 34, boxSizing: "border-box" }}>
        <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 28, color: PAPER, textAlign: "center", marginTop: 18, marginBottom: 40 }}>Bossman · пульт</div>
        {msgs.map(([m, mine], i) => {
          const at = BEAT * (0.6 + i * 0.95);
          const k = spring({ frame: f - at, fps, config: { damping: 16, stiffness: 180 } });
          return (
            <div key={i} style={{ display: "flex", justifyContent: mine ? "flex-end" : "flex-start", marginBottom: 26 }}>
              <div style={{ maxWidth: 420, padding: "22px 28px", borderRadius: 28, fontFamily: UI, fontWeight: 700, fontSize: 28,
                background: mine ? SIGNAL : "#2a2a2a", color: mine ? INK : PAPER, opacity: Math.min(1, k * 2),
                transform: `translateY(${(1 - k) * 24}px) scale(${0.95 + 0.05 * k})`, transformOrigin: mine ? "right bottom" : "left bottom" }}>
                {m}
              </div>
            </div>
          );
        })}
      </div>
    </Field>
  );
};

// 5 — EVERY FEATURE PROVEN: a receipt prints, line by line, with the real live receipts
const SceneReceipt: React.FC = () => {
  const f = useCurrentFrame();
  const lines: [string, string][] = [
    ["Job Object kill-on-close", "81b2552b"],
    ["Approval bound to payload", "3b1018f5"],
    ["Owner spend reconcile", "c49757e1"],
    ["Harness execute-step", "5c2195d9"],
  ];
  const paper = io(f, 0, BEAT * 3.2, Easing.linear);
  return (
    <Field color={BLUE}>
      <div style={{ position: "absolute", left: 110, top: 150 }}>
        <MaskLine text="EVERY" at={4} size={200} color={PAPER} />
        <MaskLine text="FEATURE" at={14} size={200} color={PAPER} />
        <MaskLine text="PROVEN." at={24} size={200} color={INK} />
      </div>
      <div style={{ position: "absolute", right: 170, top: 0, width: 640, height: 80 + paper * 900, overflow: "hidden", background: PAPER,
        boxShadow: "0 30px 60px rgba(0,0,0,.25)", fontFamily: MONO, color: INK, padding: "60px 44px 0", boxSizing: "border-box" }}>
        <div style={{ fontWeight: 700, fontSize: 30, textAlign: "center", letterSpacing: 6 }}>BOSSMAN · RECEIPT</div>
        <div style={{ fontSize: 22, textAlign: "center", marginTop: 8, opacity: 0.7 }}>live :8801 · build fd11affc · 29.10.2026</div>
        <div style={{ borderTop: "3px dashed #0e0e0e", margin: "28px 0" }} />
        {lines.map(([name, sha], i) => {
          const k = io(f, 26 + i * 16, 8);
          return (
            <div key={name} style={{ opacity: k, marginBottom: 30 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 25, fontWeight: 700 }}>
                <span>{name}</span><span style={{ color: SIGNAL }}>PASS</span>
              </div>
              <div style={{ fontSize: 21, opacity: 0.65, marginTop: 4 }}>sha256 {sha}…</div>
            </div>
          );
        })}
        <div style={{ borderTop: "3px dashed #0e0e0e", margin: "10px 0 22px" }} />
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 28, fontWeight: 700, opacity: io(f, 96, 8) }}>
          <span>TESTS</span><span>1152 passed</span>
        </div>
      </div>
    </Field>
  );
};

// 6 — BOSSMAN! (the chorus shout at 11.14 s)
const SceneChorus: React.FC = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const hit = spring({ frame: f, fps, config: { damping: 11, stiffness: 160 } });
  const p = beatPulse(f + 8);
  const stats: [number, string, string][] = [[1152, "", "tests passed"], [24, "", "lessons in memory"], [0, " $", "cloud cost of this video"]];
  return (
    <Field color={SIGNAL}>
      <AbsoluteFill style={{ alignItems: "center", top: 70 }}>
        <div style={{ fontFamily: DISPLAY, fontSize: 470, color: INK, lineHeight: 1, transform: `scale(${0.92 + 0.08 * hit + 0.02 * p})` }}>BOSSMAN!</div>
      </AbsoluteFill>
      <div style={{ position: "absolute", left: 110, right: 110, bottom: 110, display: "flex", justifyContent: "space-between", borderTop: `4px solid ${INK}`, paddingTop: 34 }}>
        {stats.map(([v, suf, label], i) => {
          const k = io(f, 20 + i * 12, 50, EASE_OUT);
          return (
            <div key={label} style={{ opacity: Math.min(1, k * 4) }}>
              <div style={{ fontFamily: DISPLAY, fontSize: 130, color: i === 1 ? PAPER : INK, lineHeight: 1 }}>{Math.round(v * k).toLocaleString("en-US")}{suf}</div>
              <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 30, color: INK, marginTop: 6 }}>{label}</div>
            </div>
          );
        })}
      </div>
    </Field>
  );
};

// 7 — the harness, as a stepper the beat walks through
const SceneHarness: React.FC = () => {
  const f = useCurrentFrame();
  const steps = ["STOP check", "Recall", "Act", "Verify", "Learn", "Receipt"];
  const active = Math.min(steps.length - 1, Math.floor(f / ((BEAT * 4) / steps.length)));
  return (
    <Field color={INK}>
      <Corner left="HARNESS" right="/api/v20/harness/execute-step" color={PAPER} />
      <div style={{ position: "absolute", left: 110, top: 170 }}>
        <MaskLine text="LOCKED." at={4} size={300} color={PAPER} />
      </div>
      <div style={{ position: "absolute", left: 110, right: 110, top: 600, display: "flex", gap: 18 }}>
        {steps.map((s, i) => {
          const on = i <= active;
          const k = io(f, i * 4, 14);
          return (
            <div key={s} style={{ flex: 1, transform: `translateY(${(1 - k) * 40}px)`, opacity: k }}>
              <div style={{ height: 14, background: on ? SIGNAL : "#2c2c2c" }} />
              <div style={{ fontFamily: MONO, fontWeight: 700, fontSize: 24, color: on ? SIGNAL : "#777", marginTop: 20 }}>0{i + 1}</div>
              <div style={{ fontFamily: UI, fontWeight: 900, fontSize: 40, color: on ? PAPER : "#666", marginTop: 6 }}>{s}</div>
            </div>
          );
        })}
      </div>
      <div style={{ position: "absolute", left: 110, bottom: 120, fontFamily: UI, fontWeight: 500, fontSize: 34, color: PAPER, opacity: io(f, BEAT * 1.5, 14) * 0.8 }}>
        No postcondition, no pass. Every lesson lands in SQLite + Obsidian.
      </div>
    </Field>
  );
};

// 8 — lockup
const SceneOutro: React.FC = () => {
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const fade = interpolate(f, [durationInFrames - 30, durationInFrames], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <Field color={PAPER}>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <MaskLine text="BOSSMAN" at={4} size={360} color={INK} tracking={6} />
        <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 44, color: INK, opacity: io(f, 30, 16), marginTop: 10 }}>
          Local-first operator of your PC.
        </div>
        <div style={{ fontFamily: MONO, fontWeight: 500, fontSize: 24, color: INK, opacity: io(f, 50, 16) * 0.6, marginTop: 36 }}>
          v2.6 · main 9f031330 · music ACE-Step on this PC · motion Remotion on this PC
        </div>
      </AbsoluteFill>
      <div style={{ position: "absolute", left: 0, bottom: 0, height: 26, width: `${io(f, 10, 60, EASE_IO) * 100}%`, background: SIGNAL }} />
      <AbsoluteFill style={{ background: INK, opacity: fade }} />
    </Field>
  );
};

const SCENES = [SceneTitle, SceneStop, SceneLocal, ScenePult, SceneReceipt, SceneChorus, SceneHarness, SceneOutro];
const TOTAL = 20 * FPS;

export const BossmanRap: React.FC = () => (
  <AbsoluteFill style={{ background: INK }}>
    {SCENES.map((S, i) => {
      const from = CUTS[i];
      const to = Math.min(TOTAL, CUTS[i + 1]);
      return (
        <Sequence key={i} from={from} durationInFrames={to - from} premountFor={30}>
          <S />
        </Sequence>
      );
    })}
    <Audio src={staticFile("rap.mp3")} />
  </AbsoluteFill>
);

export const MyComposition = () => (
  <Composition id="BossmanRap" component={BossmanRap} durationInFrames={TOTAL} fps={FPS} width={1920} height={1080} />
);
