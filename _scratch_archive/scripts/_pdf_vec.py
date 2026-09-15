import pymupdf, json

doc = pymupdf.open(r"OpenMicroDuck\hardware_spec\servo\HD-1910M-C001-drawing-20260902.pdf")
page = doc[0]

# 1) words with positions
words = page.get_text("words")  # x0,y0,x1,y1,word,block,line,word_no
print(f"page rect: {page.rect}  # words: {len(words)}")
for w in words:
    print(f"  text={w[4]!r:12} bbox=({w[0]:.1f},{w[1]:.1f},{w[2]:.1f},{w[3]:.1f})")

# 2) drawing primitives
dr = page.get_drawings()
print(f"\n# draw items: {len(dr)}")
from collections import Counter
cnt = Counter()
for it in dr:
    for itm in it["items"]:
        cnt[itm[0]] += 1
print(cnt)
