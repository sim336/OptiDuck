import pymupdf
doc = pymupdf.open(r"OpenMicroDuck\hardware_spec\servo\HD-1910-C001串型规格书-20260907.pdf")
page = doc[5]
# extract embedded images at native res
for xref, smask, w, h, bpc, cs, altcs, name, filt, ref in page.get_images(full=True):
    if w > 1000:
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n - pix.alpha > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        out = rf"e:\optiDuck\_pdf_img\hd1910_spec_p6_embed_{xref}.png"
        pix.save(out)
        print(out, pix.width, pix.height)
doc.close()
