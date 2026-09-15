import pymupdf, os, numpy as np, cv2
os.makedirs(r"E:\optiDuck\_fig", exist_ok=True)

# render HD-1910 outline drawing at high res
doc = pymupdf.open(r"E:\optiDuck\OpenMicroDuck\hardware_spec\servo\HD-1910M-C001-drawing-20260902.pdf")
p = doc[0]
pix = p.get_pixmap(dpi=300)
pix.save(r"E:\optiDuck\_fig\hd1910_drawing.png")
img = cv2.imread(r"E:\optiDuck\_fig\hd1910_drawing.png", cv2.IMREAD_GRAYSCALE)
print("hd1910 drawing:", img.shape)

# Hough circles for holes
blur = cv2.medianBlur(img, 5)
circles = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.2, minDist=20,
                           param1=100, param2=25, minRadius=3, maxRadius=40)
if circles is not None:
    c = circles[0]
    print("circles found:", len(c))
    for x, y, r in sorted(c, key=lambda v: (v[1], v[0])):
        print(f"   center=({x:6.1f},{y:6.1f}) r={r:5.1f}px")
else:
    print("no circles")

# also render HL-2915 spec outline page (page 6 "9 outline dimension")
doc2 = pymupdf.open(r"E:\optiDuck\OpenMicroDuck\hardware_spec\servo\HL-2915-C002_HL-2909-C001串型规格书-20260227.pdf")
p2 = doc2[5]  # page 6
pix2 = p2.get_pixmap(dpi=300)
pix2.save(r"E:\optiDuck\_fig\hl2915_outline.png")
img2 = cv2.imread(r"E:\optiDuck\_fig\hl2915_outline.png", cv2.IMREAD_GRAYSCALE)
blur2 = cv2.medianBlur(img2, 5)
c2 = cv2.HoughCircles(blur2, cv2.HOUGH_GRADIENT, dp=1.2, minDist=20,
                      param1=100, param2=25, minRadius=3, maxRadius=40)
if c2 is not None:
    print("\nhl2915 outline circles:", len(c2[0]))
    for x, y, r in sorted(c2[0], key=lambda v: (v[1], v[0])):
        print(f"   center=({x:6.1f},{y:6.1f}) r={r:5.1f}px")
else:
    print("\nhl2915 outline: no circles")
