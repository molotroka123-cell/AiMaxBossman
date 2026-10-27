import sys
sys.path.insert(0, r"C:\Users\asd\Bossman\wt-trio-claude\tools\motion_studio")
import head_zoom as hz
W = r"D:\Bossman\work\genjutsu-local-20261026"
for tag, head in (("raw", "head768_gen.mp4"), ("ff", "head768_gen_ff.mp4")):
    n = hz.paste_back(W + r"\out\anim720.mp4", W + "\out\head768\\" + head, W + r"\src\head768\boxes.json",
                      W + r"\out\v720\anim720_head768_" + tag + ".mp4", W + r"\src\head768\head_mask.mp4")
    print("pasted", tag, n)
