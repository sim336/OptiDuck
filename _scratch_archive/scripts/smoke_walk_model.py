import mujoco

from mjlab_microduck.robot.microduck_constants import get_openmicroduck_walk_spec

spec = get_openmicroduck_walk_spec()
model = spec.compile()

print("=== collision-class geoms: name | group | contype | conaffinity | condim ===")
for g in range(model.ngeom):
    if model.geom_group[g] == 3:
        name = model.geom(g).name
        contype = model.geom_contype[g]
        conaff = model.geom_conaffinity[g]
        condim = model.geom_condim[g]
        print(f"{name or '(power_support)':22} g={model.geom_group[g]} ct={contype} ca={conaff} condim={condim}")

# ground is typically in group? check ground contactability: foot ct==1 so it touches ground(ca==1)
import mujoco
print("\nground sphere/plane contype/conaffinity:")
for b in range(model.nbody):
    pass
print("done")