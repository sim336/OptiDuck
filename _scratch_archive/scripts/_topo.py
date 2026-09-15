import mujoco, trimesh
m = mujoco.MjModel.from_xml_path(r"src\mjlab_microduck\robot\microduck\robot_openmicroduck.xml")
mnames = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_MESH, i) for i in range(m.nmesh)]
for nm in ("hip_l", "ankle_right", "motor_support"):
    i = mnames.index(nm)
    vn = m.mesh_vertnum[i]; fn = m.mesh_facenum[i]
    va = m.mesh_vertadr[i]; fa = m.mesh_faceadr[i]
    t = trimesh.load(rf"src\mjlab_microduck\robot\microduck\assets\{nm}.stl")
    print(f"{nm}: mujoco vn={vn} fn={fn} | trimesh verts={len(t.vertices)} faces={len(t.faces)}")
    # face range
    f = m.mesh_face[fa:fa+fn*3]
    print(f"   mujoco face {f.min()}..{f.max()}, trimesh face {t.faces.min()}..{t.faces.max()}")
