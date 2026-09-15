from PIL import Image
import numpy as np

im = Image.open(r"e:\optiDuck\_xl330_drawing_img.png").convert("RGB")
a = np.asarray(im)
nonwhite = a.sum(axis=2) < 700
rows = nonwhite.sum(axis=1)
cols = nonwhite.sum(axis=0)

r_sel = np.where(rows > 5)[0]
c_sel = np.where(cols > 5)[0]
print("row range:", r_sel.min(), r_sel.max())
print("col range:", c_sel.min(), c_sel.max())

# 裁剪到内容边界
box = (max(0, c_sel.min()-5), max(0, r_sel.min()-5),
       min(im.width, c_sel.max()+5), min(im.height, r_sel.max()+5))
crop = im.crop(box)
print("crop size:", crop.size)
crop3 = crop.resize((crop.width*3, crop.height*3), Image.LANCZOS)
crop3.save(r"e:\optiDuck\_xl330_drawing_crop3.png")
print("saved crop3")

# 检查是否有彩色（标注颜色）
a2 = np.asarray(im.convert("RGB"))
colored = (a2.max(axis=2) - a2.min(axis=2)) > 30
print("colored pixel ratio: %.4f" % colored.mean())
# 彩色像素分布
cr = np.where(colored.sum(axis=1) > 0)[0]
cc = np.where(colored.sum(axis=0) > 0)[0]
if len(cr):
    print("colored row range:", cr.min(), cr.max(), "col range:", cc.min(), cc.max())
