"""Offline neon trailer renderer for validated Motion Studio specs (Pillow)."""
from __future__ import annotations
import math
import os
import subprocess
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 720
SUPPORTED = {'title', 'cards', 'grid', 'voice', 'logo', 'end_card', 'roadmap', 'bars'}
CYAN, PURPLE, WHITE = '#69edff', '#b68aff', '#f3f4ff'

@lru_cache(maxsize=64)
def font(size, bold=True):
    candidates = [os.environ.get('BOSSMAN_MOTION_FONT', ''),
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans' + ('-Bold' if bold else '') + '.ttf',
                  'C:/Windows/Fonts/' + ('arialbd.ttf' if bold else 'arial.ttf')]
    for path in candidates:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise RuntimeError('Set BOSSMAN_MOTION_FONT to a readable .ttf font')

def ease(x):
    return 1 - (1 - max(0, min(1, x))) ** 4

class Renderer:
    def __init__(self, spec):
        unsupported = {s['type'] for s in spec['scenes']} - SUPPORTED
        if unsupported:
            raise ValueError(f'Epic style does not support {sorted(unsupported)}; use classic')
        self.spec = spec
        y, x = np.mgrid[:H, :W]
        a = np.exp(-((x-830)**2+(y-300)**2)/125000)
        b = np.exp(-((x-290)**2+(y-490)**2)/150000)
        self.base = np.stack([5+a*20+b*2, 7+a*7+b*12, 15+a*45+b*22],axis=2).astype('uint8')

    def frame(self, t):
        scenes = self.spec['scenes']
        k = next((i for i,s in enumerate(scenes) if s['start'] <= t < s['end']), len(scenes)-1)
        sc = scenes[k]; u = t-sc['start']; en = ease(u/.7)
        im = Image.fromarray(self.base.copy()); fx = Image.new('RGB',(W,H)); d = ImageDraw.Draw(im); f = ImageDraw.Draw(fx)
        labels=Image.new('RGBA',(W,H));td=ImageDraw.Draw(labels)
        def text(s,x,y,size=24,color=WHITE,center=True,bold=True,maxwidth=1140):
            s=str(s)
            while size>10 and d.textlength(s,font=font(size,bold))>maxwidth:size-=1
            ft=font(size,bold)
            if center:x-=d.textlength(s,font=ft)/2
            td.text((x,y),s,font=ft,fill=color)
        # Camera-space star tunnel: deterministic trajectories, no random frame jitter.
        for j in range(180):
            a=j*2.39996; z=(j*.037+t*.16)%1
            r=20+z*z*840; x=640+math.cos(a)*r; y=345+math.sin(a)*r*.64
            if 0<x<W and 0<y<H:
                v=int(45+100*z); d.line((x,y,x+math.cos(a)*(2+z*11),y+math.sin(a)*(2+z*7)),fill=(v//2,v,v),width=1)
        # Architectural frame and timeline.
        for x,y,sx,sy in [(26,26,1,1),(1254,26,-1,1),(26,694,1,-1),(1254,694,-1,-1)]:
            d.line((x,y,x+sx*42,y),fill='#3d5165'); d.line((x,y,x,y+sy*25),fill='#3d5165')
        text(self.spec['meta'].get('hud','BOSSMAN / MOTION'),46,38,12,'#7d92ac',False)
        text(f'{t:05.2f} / {self.spec["meta"]["duration"]:05.2f}',1165,38,12,'#7d92ac',True)
        d.line((48,674,1232,674),fill='#263647',width=2)
        d.line((48,674,48+1184*t/self.spec['meta']['duration'],674),fill=CYAN,width=3)
        kind=sc['type']; dy=int((1-en)*65)
        if kind=='title':
            text(sc.get('kicker','THE BEGINNING'),640,160+dy,21,CYAN)
            text(sc['title'],640,239+dy,139)
            d.line((350,424,930,424),fill='#7766b0',width=2)
            text(sc.get('typed',''),640,459,23,'#9caec8',bold=False)
            text(sc.get('chip',''),640,515,16,PURPLE)
        elif kind=='cards':
            text(sc.get('heading','CAPABILITIES'),640,135+dy,37)
            items=sc['items']; w=min(226,1100/len(items)); gap=16; start=640-(len(items)*w+(len(items)-1)*gap)/2
            for j,it in enumerate(items):
                e=ease((t-it['t'])/.45)
                if e<=0:continue
                x=start+j*(w+gap); y=265+(1-e)*90; col=CYAN if j%2==0 else PURPLE
                d.rounded_rectangle((x,y,x+w,y+227),18,fill='#10182b',outline=col,width=2)
                # Orbit/nodes suggest tools linked to one shared core.
                cx=x+w/2;cy=y+73
                icon=it['icon']
                if icon=='memory':
                    for Y in [cy-22,cy,cy+22]:
                        f.ellipse((cx-29,Y-10,cx+29,Y+10),outline=col,width=3)
                    f.line((cx-29,cy-22,cx-29,cy+22),fill=col,width=3);f.line((cx+29,cy-22,cx+29,cy+22),fill=col,width=3)
                elif icon in ('play','cursor'):
                    pts=[(cx-19,cy-30),(cx+31,cy),(cx-19,cy+30)] if icon=='play' else [(cx-23,cy-30),(cx+29,cy+9),(cx+3,cy+12),(cx-8,cy+34)]
                    f.polygon(pts,outline=col,width=3)
                elif icon=='mic':
                    f.rounded_rectangle((cx-12,cy-32,cx+12,cy+12),12,outline=col,width=3)
                    f.arc((cx-24,cy-17,cx+24,cy+26),0,180,fill=col,width=3);f.line((cx,cy+26,cx,cy+38),fill=col,width=3)
                else:
                    f.ellipse((cx-29,cy-29,cx+29,cy+29),outline=col,width=3)
                    for a in range(3):
                        ang=t*.8+a*math.tau/3;px=cx+34*math.cos(ang);py=cy+34*math.sin(ang)
                        d.line((cx,cy,px,py),fill=col,width=2);d.ellipse((px-5,py-5,px+5,py+5),fill=col)
                text(it['title'],cx,y+130,24,col,maxwidth=w-14)
                text(it['sub'],cx,y+179,15,'#9dacbf',bold=False,maxwidth=w-14)
        elif kind=='grid':
            text(f'{round(sc["value"]*ease(u/1.25)):,}',640,144+dy,125)
            text(sc['label'],640,295,29,CYAN)
            count=min(sc['value'],432)
            cols=13 if count<=26 else 48; pitch=65 if count<=26 else 18; cell=48 if count<=26 else 11
            for j in range(count):
                x=640-(min(cols,count)-1)*pitch/2-cell/2+(j%cols)*pitch;y=375+(j//cols)*pitch
                e=ease((u-.25-j*.0018)/.3); c=(int(56*e),int(195*e),int(172*e))
                d.rounded_rectangle((x,y,x+cell,y+cell),4,fill=c)
            text(sc.get('caption',''),640,560,17,'#a0b0c6',bold=False)
        elif kind=='bars':
            vals=sc['values']; text(sc.get('headline',sum(vals)),640,126+dy,110)
            text(sc.get('counter_label',''),640,259,24,CYAN)
            w=1020/len(vals)
            for j,v in enumerate(vals):
                x=130+j*w; h=220*v/max(max(vals),1)*ease((u-j*.012)/.6)
                d.rounded_rectangle((x,550-h,x+w*.7,551),3,fill=CYAN if j%3 else PURPLE)
            text(sc.get('x_from',''),140,570,14,'#8fa8bf',False)
            text(sc.get('x_to',''),1110,570,14,'#8fa8bf')
        elif kind=='voice':
            x=160;y=138+dy
            d.rounded_rectangle((x,y,x+242,y+433),32,fill='#0a1122',outline='#7d7ec9',width=3)
            d.rounded_rectangle((x+70,y+13,x+172,y+27),8,fill='#283147')
            text(sc['name'],x+121,y+57,28,CYAN)
            for row in range(3):
                Y=y+124+row*82;d.rounded_rectangle((x+19,Y,x+223,Y+58),11,fill='#222551')
                for j in range(31):
                    h=5+27*abs(math.sin(j*.8+t*5+row));xx=x+32+j*5.7
                    d.line((xx,Y+29-h/2,xx,Y+29+h/2),fill=CYAN if row%2==0 else PURPLE,width=2)
            text(sc['name'],808,182+dy,106)
            text(sc.get('sub',''),810,320,23,CYAN)
            for j,it in enumerate(sc.get('status',[])):
                text(it['text'],810,406+j*38,17,'#a1b4c9',bold=False,maxwidth=670)
        elif kind in ('logo','end_card'):
            # Rotating 3D particle sphere and radial impact, behind the wordmark.
            r=170+12*math.sin(t*2); angle=t*.6
            for j in range(1100):
                y=1-2*(j+.5)/1100; a=j*2.39996+angle; q=math.sqrt(1-y*y); x=q*math.cos(a);z=q*math.sin(a)
                px=640+x*r*(1+.2*z);py=318+y*r
                col=CYAN if z>0 else '#434175';rr=1.1+max(0,z)
                f.ellipse((px-rr,py-rr,px+rr,py+rr),fill=col)
            for j in range(100):
                a=j*2.399; rr=200+(j%7)*13+u*22
                f.line((640+math.cos(a)*rr,318+math.sin(a)*rr*.65,640+math.cos(a)*(rr+22),318+math.sin(a)*(rr+22)*.65),fill='#444e82',width=1)
            text(sc.get('name',sc.get('text','')),640,257+dy,113 if kind=='logo' else 70)
            text(sc.get('tagline',sc.get('sub','')),640,453,24,CYAN)
        elif kind=='roadmap':
            text(sc.get('heading','NEXT'),640,130+dy,45)
            items=sc['items'];space=1100/len(items)
            d.line((120,390,1160,390),fill='#555b9e',width=2)
            for j,it in enumerate(items):
                e=ease((t-it['t'])/.5)
                if e<=0:continue
                x=90+space*(j+.5);y=262+(1-e)*55
                text(it['version'],x,y,72,CYAN if j%2==0 else PURPLE)
                d.ellipse((x-6,384,x+6,396),fill=CYAN)
                text(it['when'],x,419,16,PURPLE)
                for n,line in enumerate(it['lines']):text(line,x,459+n*27,16,'#aebcd0',bold=False,maxwidth=space-10)
            text(sc['disclaimer'],640,565,14,'#b9a47b',bold=False)
        # Soft bloom without sacrificing sharp typography.
        im=Image.blend(im,Image.fromarray(np.minimum(np.asarray(im,dtype=np.uint16)+np.asarray(fx.filter(ImageFilter.GaussianBlur(9)),dtype=np.uint16),255).astype('uint8')), .85)
        im=Image.fromarray(np.minimum(np.asarray(im,dtype=np.uint16)+np.asarray(fx,dtype=np.uint16),255).astype('uint8'))
        im=Image.alpha_composite(im.convert('RGBA'),labels).convert('RGB')
        # Subtitles use the same scene clock; no unsupported performance claims.
        d=ImageDraw.Draw(im)
        subtitle=sc.get('vo',[])
        active=[v for v in subtitle if v['t']<=t]
        if active:
            line=active[-1]['text']; ft=font(21,False);tw=d.textlength(line,font=ft)
            d.rounded_rectangle((630-tw/2,609,650+tw/2,651),8,fill='#060a16')
            d.text((640-tw/2,618),line,font=ft,fill=WHITE)
        if k and u<.16:im=Image.blend(im,Image.new('RGB',(W,H),'#aec9ee'),.24*(1-u/.16)**2)
        end=self.spec['meta']['duration']
        if t>end-.45:im=Image.blend(im,Image.new('RGB',(W,H)),(t-end+.45)/.45)
        return im

def render(spec, work, soundtrack, fps=30, previews=()):
    r=Renderer(spec);work=Path(work);work.mkdir(parents=True,exist_ok=True)
    for t in previews:r.frame(t).save(work/f'epic-preview-{t:g}.png')
    if previews:return
    output=work/'video.mp4';tmp=work/'video.partial.mp4'
    cmd=['ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(fps),'-i','-', '-i',str(soundtrack),'-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','256k','-t',str(spec['meta']['duration']),'-movflags','+faststart',str(tmp)]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    try:
        for n in range(round(fps*spec['meta']['duration'])):
            proc.stdin.write(r.frame(n/fps).tobytes())
            if n%(fps*3)==0:print(f'epic render: {n/fps:.0f}s',flush=True)
        proc.stdin.close()
        if proc.wait()!=0:raise RuntimeError('ffmpeg render failed')
        tmp.replace(output)
    finally:
        if proc.poll() is None:proc.kill();proc.wait()
        if tmp.exists():tmp.unlink()
    return output
