"""SwapMe hybrid ad — deterministic edit (DETERMINISTIC_TOOL: ffmpeg + PIL). EDL in production/EDL.json.
0.00-5.00 S1 Seedance | 5.00-7.50 L1 local (continues from S1 last frame) | 7.50-12.50 S2 Seedance (0.16 s light-flash
transition, whoosh) | 12.50-15.00 L2 local (continues from S2 last frame) + end card. Sound: procedural 120 BPM (cuts on beats)
+ Seedance ambience under S1/S2 + whooshes/riser/impact. Run with the Bossman bundle python.
"""
import json, subprocess, sys, time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, r"C:\Users\asd\Bossman\creative-runs")
import hybrid as h

RUN, FF = h.RUN, h.FFMPEG
FPS = 24
L2_STRETCH = 1.0


def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    if p.returncode:
        raise RuntimeError(p.stderr[-1500:])
    return p


def end_card():
    W, H = 1080, 1920
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    for y in range(1180, H):
        gd.line([(0, y), (W, y)], fill=(4, 7, 6, int(225 * min(1.0, (y - 1180) / 430))))
    img = Image.alpha_composite(img, g)
    GREEN = (52, 226, 122, 255)
    bold = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 172)
    reg = ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", 58)
    semi = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 50)
    d = ImageDraw.Draw(img)
    w1, w2 = d.textlength("Swap", font=bold), d.textlength("Me", font=bold)
    x, y = (W - w1 - w2) / 2, 1390
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((x + w1, y), "Me", font=bold, fill=(52, 226, 122, 200))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    d.text((x, y + 6), "SwapMe", font=bold, fill=(0, 0, 0, 150))
    d.text((x, y), "Swap", font=bold, fill=(255, 255, 255, 255))
    d.text((x + w1, y), "Me", font=bold, fill=GREEN)
    for text, font, fill, dy in (("Crypto Exchange Prague", reg, (238, 238, 238, 255), 222),
                                 ("Fast · Safe · Simple", semi, GREEN, 305)):
        d.text(((W - d.textlength(text, font=font)) / 2, y + dy), text, font=font, fill=fill)
    d.rounded_rectangle([(W / 2 - 140, y + 392), (W / 2 + 140, y + 399)], radius=4, fill=GREEN)
    out = RUN / "work" / "end_card.png"
    img.save(out)
    return out


def soundtrack(s1, s2):
    """Royalty-safe: every sample is computed here (aevalsrc) or comes from the two Seedance clips."""
    beat = ("0.85*sin(2*PI*(50*mod(t,0.5)+(100/30)*(1-exp(-30*mod(t,0.5)))))*exp(-11*mod(t,0.5))"
            "+0.10*sin(2*PI*if(lt(mod(t,4),2),55,41.2)*t)*(1-exp(-8*mod(t,0.5)))"
            "+0.07*sin(2*PI*if(lt(mod(t,4),2),110,82.4)*t)*(1-exp(-8*mod(t,0.5)))*gte(t,5)")
    hats = "0.11*(2*random(0)-1)*exp(-70*mod(t+0.25,0.5))*gte(t,2)"
    swoosh = "+".join(f"0.55*(2*random(1)-1)*exp(-pow((t-{c}+0.12)/0.16,2))" for c in (5.0, 7.5, 12.5))
    rise = "0.10*pow((t-11)/1.5,2)*sin(2*PI*(200*(t-11)+333.3*pow(t-11,2)))*between(t,11,12.5)"
    hit = "0.9*sin(2*PI*38*(t-12.5))*exp(-4*(t-12.5))*gte(t,12.5)"
    out = RUN / "work" / "soundtrack.wav"
    fc = (f"aevalsrc='{beat}':s=48000:d=15[beat];"
          f"aevalsrc='{hats}':s=48000:d=15,highpass=f=7000[hat];"
          f"aevalsrc='{swoosh}':s=48000:d=15,bandpass=f=1800:width_type=o:w=2[sw];"
          f"aevalsrc='{rise}+{hit}':s=48000:d=15[fx];"
          f"[0:a]aresample=48000,atrim=0:5,volume=0.55,afade=t=out:st=4.7:d=0.3[amb1];"
          f"[1:a]aresample=48000,atrim=0:5,volume=0.55,afade=t=in:d=0.2,afade=t=out:st=4.7:d=0.3,adelay=7500|7500[amb2];"
          f"[beat][hat][sw][fx]amix=inputs=4:normalize=0,aformat=channel_layouts=stereo[mus];"
          f"[amb1][amb2]amix=inputs=2:normalize=0,apad=whole_dur=15,aformat=channel_layouts=stereo[amb];"
          f"[mus][amb]amix=inputs=2:normalize=0,atrim=0:15,afade=t=out:st=14.2:d=0.8,"
          f"loudnorm=I=-14:TP=-1.5:LRA=9,aresample=48000[aout]")
    run([FF, "-y", "-v", "error", "-i", str(s1), "-i", str(s2), "-filter_complex", fc, "-map", "[aout]", "-t", "15",
         "-c:a", "pcm_s16le", str(out)])
    return out


def main():
    st = h.load()
    S1, L1, S2, L2 = (Path(st["jobs"][k]["file"]) for k in ("S1", "L1", "S2", "L2"))
    card = end_card()
    audio = soundtrack(S1, S2)
    # L2 was rendered short (33 frames, owner 25-min deadline): stretched to 2.55 s with motion interpolation.
    global L2_STRETCH
    L2_STRETCH = max(1.0, 2.55 / max(0.1, h.probe(L2)["duration"] - 1 / 24))
    grade = "eq=contrast=1.04:saturation=1.06:gamma=0.98,vignette=PI/5"
    sc = f"scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1,fps={FPS}"
    fc = (
        f"[0:v]{sc},trim=0:5,setpts=PTS-STARTPTS,{grade},format=yuv420p[s1];"
        f"[1:v]{sc},trim=start_frame=1,trim=0:2.66,setpts=PTS-STARTPTS,unsharp=5:5:0.5,{grade},format=yuv420p[l1];"
        f"[2:v]{sc},trim=0:5,setpts=PTS-STARTPTS,{grade},format=yuv420p[s2];"
        f"[3:v]trim=start_frame=1,setpts={L2_STRETCH:.4f}*(PTS-STARTPTS),minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:vsbmc=1,"
        f"{sc},trim=0:2.5,setpts=PTS-STARTPTS,unsharp=5:5:0.5,{grade},format=yuv420p[l2];"
        f"[s1][l1]concat=n=2:v=1:a=0[a];"
        f"[a][s2]xfade=transition=fadewhite:duration=0.16:offset=7.5[b];"
        f"[b][l2]concat=n=2:v=1:a=0[c];"
        f"[4:v]format=rgba,fade=t=in:st=12.55:d=0.45:alpha=1[card];"
        f"[c][card]overlay=0:0:enable='gte(t,12.55)',trim=0:15,setpts=PTS-STARTPTS[v]")
    master = RUN / "final" / "swapme_fox_15s_master.mp4"
    t0 = time.time()
    run([FF, "-y", "-v", "error", "-i", str(S1), "-i", str(L1), "-i", str(S2), "-i", str(L2), "-loop", "1", "-t", "15", "-i", str(card),
         "-i", str(audio), "-filter_complex", fc, "-map", "[v]", "-map", "5:a", "-t", "15",
         "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
         "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(master)])
    social = RUN / "final" / "swapme_fox_15s_social.mp4"
    run([FF, "-y", "-v", "error", "-i", str(master), "-vf", "crop=1080:1350:0:(ih-1350)/2", "-c:v", "libx264", "-preset", "slow",
         "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(social)])
    run([FF, "-y", "-v", "error", "-ss", "14.2", "-i", str(master), "-frames:v", "1", "-q:v", "2", str(RUN / "final" / "poster.jpg")])
    run([FF, "-y", "-v", "error", "-i", str(master), "-vf", "fps=4/3,scale=270:480,tile=5x4:padding=4", "-frames:v", "1",
         "-q:v", "3", str(RUN / "final" / "contact_sheet.jpg")])
    edl = {"fps": FPS, "size": "1080x1920", "segments": [
        {"t": [0, 5.0], "src": "S1", "kind": "cloud", "in": [0, 5.0]},
        {"t": [5.0, 7.5], "src": "L1", "kind": "local", "in": [1 / 24, 2.66], "join": "seamless (L1 starts on S1 last frame)"},
        {"t": [7.5, 12.5], "src": "S2", "kind": "cloud", "in": [0, 5.0], "join": "fadewhite 0.16 s + whoosh on beat"},
        {"t": [12.5, 15.0], "src": "L2", "kind": "local", "in": [1 / 24, 2.5], "join": "seamless (L2 starts on S2 last frame)"}],
        "overlay": {"end_card": str(card), "from": 12.55}, "audio": "procedural 120 BPM + Seedance ambience; loudnorm -14 LUFS",
        "grade": grade, "filter_complex": fc, "render_s": round(time.time() - t0, 1)}
    (RUN / "production" / "EDL.json").write_text(json.dumps(edl, ensure_ascii=False, indent=1), encoding="utf-8")
    print("master", h.probe(master), h.sha(master))


if __name__ == "__main__":
    main()
