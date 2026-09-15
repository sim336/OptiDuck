import mujoco, numpy as np
m = mujoco.MjModel.from_xml_path(r"src\mjlab_microduck\robot\microduck\robot_openmicroduck.xml")
d = mujoco.MjData(m); mujoco.mj_forward(m, d)
# highlight servos red, structure semi-transparent
mnames = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_MESH, i) for i in range(m.nmesh)]
hmid = [i for i,n in enumerate(mnames) if n=="hd1910"][0]
sg = [i for i in range(m.ngeom) if m.geom_dataid[i]==hmid]
m.geom_rgba[:] = [0.6,0.7,0.8,0.35]   # structure translucent blue
m.geom_rgba[sg] = [1,0.2,0.1,1]       # servos bright red
viewer = mujoco.viewer.launch_passive(m, d)
import time
time.sleep(5)  # let user see it
viewer.close()
print("viewer shown 5s")
