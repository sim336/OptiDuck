import pymupdf
doc = pymupdf.open(r"OpenMicroDuck\hardware_spec\servo\HD-1910-C001串型规格书-20260907.pdf")
page = doc[5]  # page 6 (0-indexed): 外观尺寸
print("page rect:", page.rect)
print("images:", page.get_images(full=True))
words = page.get_text("words")
print("# words:", len(words))
for w in words[:120]:
    print(f"  {w[4]!r:14} ({w[0]:.1f},{w[1]:.1f},{w[2]:.1f},{w[3]:.1f})")
dr = page.get_drawings()
from collections import Counter
cnt = Counter()
for it in dr:
    for itm in it["items"]:
        cnt[itm[0]] += 1
print("draw items:", dict(cnt))
doc.close()
