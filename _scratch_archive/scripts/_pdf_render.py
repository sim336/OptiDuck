import pymupdf, os

jobs = {
    "hd1910_drawing": r"OpenMicroDuck\hardware_spec\servo\HD-1910M-C001-drawing-20260902.pdf",
    "hd1910_spec_p6": r"OpenMicroDuck\hardware_spec\servo\HD-1910-C001串型规格书-20260907.pdf",
}
for name, p in jobs.items():
    doc = pymupdf.open(p)
    outdir = r"_pdf_img"
    os.makedirs(outdir, exist_ok=True)
    for i, page in enumerate(doc):
        mat = pymupdf.Matrix(4, 4)  # 4x zoom
        pix = page.get_pixmap(matrix=mat)
        fp = os.path.join(outdir, f"{name}_p{i+1}.png")
        pix.save(fp)
        print(fp, pix.width, pix.height)
    doc.close()
