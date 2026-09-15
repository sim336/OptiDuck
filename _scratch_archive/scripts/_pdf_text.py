import pymupdf, os

files = {
    "HD-1910 drawing": r"OpenMicroDuck\hardware_spec\servo\HD-1910M-C001-drawing-20260902.pdf",
    "HD-1910 spec": r"OpenMicroDuck\hardware_spec\servo\HD-1910-C001串型规格书-20260907.pdf",
}
for label, p in files.items():
    doc = pymupdf.open(p)
    print(f"########## {label}  pages={len(doc)} ##########")
    for i, page in enumerate(doc):
        txt = page.get_text("text")
        txt = "\n".join(l for l in txt.splitlines() if l.strip())
        print(f"--- page {i+1} ---")
        print(txt[:4000])
    doc.close()
