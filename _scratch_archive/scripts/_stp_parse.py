import sys, numpy as np
sys.path.insert(0, r"e:\optiDuck")
from steputils import parser as sp
from steputils import ast as stepast

path = r"e:\optiDuck\OpenMicroDuck\cad\HD-1910-C001-20260902.stp"
data = sp.parse_file(path)

# collect entity map
ents = {}
for node in data.data_():
    if isinstance(node, stepast.SimpleInstance) and node.args:
        name = node.args[0]
        body = node.args[-1]
        if body and isinstance(body, stepast.CallExpression):
            ents[name] = body

def call_text(expr):
    return expr.name if isinstance(expr, stepast.CallExpression) else str(expr)

def args_of(name):
    e = ents.get(name)
    if not e: return []
    return list(e.args)

from collections import Counter
c = Counter()
for name, e in ents.items():
    c[e.name] += 1
print("entity types:", dict(c))
print("total entities:", len(ents))
