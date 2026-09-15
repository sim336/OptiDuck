import re
xml = r"src\mjlab_microduck\robot\microduck\robot_openmicroduck.xml"
src = open(xml, encoding="utf-8").read()
# replace servo mesh geoms with box geoms (34x20x23), keep material
def repl(m):
    attrs = m.group(0)
    attrs = re.sub(r'type="mesh"\s*', 'type="box" size="0.017 0.01 0.0115" ', attrs)
    attrs = attrs.replace('mesh="hd1910"', '')
    return attrs
new = re.sub(r'<geom[^>]*mesh="hd1910"[^>]*/>', repl, src)
open(xml, "w", encoding="utf-8").write(new)
print("servo geoms -> box:", new.count('type="box"'))
