#!/usr/bin/env python3
"""Convert the SolidWorks-exported open_duck URDF into an mjlab-compatible MJCF.

Generator for robot/crazy_chick/open_duck_walk.xml (kept in-repo so the model
can be regenerated after URDF/geometry iterations). Key transformations:
1. Root reorientation: rotate the whole model so the physical "up" (hip->ankle)
   points along +z, "forward" (trunk->neck) along +x, "lateral" along +y.
   Then translate so the feet rest on z=0 and the trunk is centered in x/y.
2. Left/right symmetry: right-leg joint axes are forced to be the exact sagittal
   mirror (flip y) of their left counterparts in the new world frame, then
   re-expressed in the child body frame (MuJoCo joint axis convention).
3. Inertias are re-rotated by the root rotation; the root link inertial (which
   MuJoCo's URDF loader silently drops) is read straight from the URDF XML.
4. Emit an MJCF shaped like crazy_chick_walk.xml: 14 position actuators in the
   contract order (left leg, head, right leg), imu/foot sites, and the same
   sensor set. EDF hardware is OPTIONAL (INCLUDE_EDF): the Open Duck training
   policy is pure joint control (14D), so by default no edf_exit site / edf_thrust
   motor is emitted (nu == 14, matching the 14D action space).

Usage:
    python make_open_duck.py [out_xml] [urdf_path]
Defaults point at the project's SolidWorks export and the in-repo output XML.
"""
import sys
import os
import xml.etree.ElementTree as ET
import numpy as np
import mujoco

URDF_PATH = sys.argv[2] if len(sys.argv) > 2 else \
    "/mnt/c/Users/刘/Desktop/疯狂小鸡项目/模型图/open_duck_urdf/open_duck/urdf/open_duck.urdf"
OUT_PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/mnt/c/Users/刘/Desktop/疯狂小鸡项目/upstream/microduck_rl/src/mjlab_microduck/robot/crazy_chick/open_duck_walk.xml"

SRC_MESHES = "/mnt/c/Users/刘/Desktop/疯狂小鸡项目/模型图/open_duck_urdf/open_duck/meshes"
ASSET_DIR = os.path.join(os.path.dirname(OUT_PATH), "open_duck_assets")

# Whether to emit the 40mm EDF hardware (edf_exit site + edf_thrust motor).
# Open Duck's training policy is pure joint control -> default OFF so that
# nu == 14 matches the 14D action space exactly.
INCLUDE_EDF = False

# MuJoCo's URDF loader cannot resolve the SolidWorks `package://open_duck/...`
# mesh URIs in this build (verified against sibling-package and package-internal
# layouts). Stage a copy of the URDF with mesh filenames rewritten to absolute
# paths so loading is deterministic and independent of the resolution quirk.
import shutil
from pathlib import Path

_STAGE = Path("/tmp/od")
_staged_urdf = _STAGE / "open_duck_urdf_abs.xml"
_urdf_tree = ET.parse(URDF_PATH)
for _el in _urdf_tree.iter():
    if _el.tag.endswith("mesh"):
        _fn = _el.get("filename", "")
        if _fn.startswith("package://open_duck/meshes/"):
            _el.set("filename", os.path.join(SRC_MESHES, os.path.basename(_fn)))
_urdf_tree.write(_staged_urdf, encoding="utf-8", xml_declaration=True)
URDF_PATH = str(_staged_urdf)

# --------------------------------------------------------------------------
# 1. parse URDF XML
# --------------------------------------------------------------------------
NS = {"urdf": "http://www.robot.com/urdf"}


def strip_tag(tag):
    return tag.split("}")[-1]


tree = ET.parse(URDF_PATH)
root = tree.getroot()

links = {}      # name -> dict(inertial, visuals, collision)
joints = {}     # name -> dict(parent, child, axis, range, origin)
for el in root:
    if strip_tag(el.tag) == "link":
        name = el.get("name")
        d = {"inertial": None, "visuals": []}
        for sub in el:
            t = strip_tag(sub.tag)
            if t == "inertial":
                o = sub.find(".//{*}origin")
                m = sub.find(".//{*}mass")
                inert = sub.find(".//{*}inertia")
                d["inertial"] = dict(
                    pos=np.array([float(v) for v in o.get("xyz").split()]) if o is not None else np.zeros(3),
                    mass=float(m.get("value")) if m is not None else 0.0,
                    ixx=float(inert.get("ixx")), ixy=float(inert.get("ixy")),
                    ixz=float(inert.get("ixz")), iyy=float(inert.get("iyy")),
                    iyz=float(inert.get("iyz")), izz=float(inert.get("izz")),
                )
            elif t == "visual":
                vo = sub.find(".//{*}origin")
                vm = sub.find(".//{*}mesh")
                vc = sub.find(".//{*}color")
                rgba = [float(v) for v in vc.get("rgba").split()] if vc is not None else [0.8, 0.8, 0.8, 1.0]
                d["visuals"].append(dict(
                    pos=np.array([float(v) for v in vo.get("xyz").split()]) if vo is not None else np.zeros(3),
                    rpy=np.array([float(v) for v in vo.get("rpy").split()]) if (vo is not None and vo.get("rpy")) else np.zeros(3),
                    mesh=os.path.basename(vm.get("filename")),
                    rgba=rgba,
                ))
        links[name] = d
    elif strip_tag(el.tag) == "joint":
        name = el.get("name")
        j = {"parent": None, "child": None, "axis": np.array([1.0, 0.0, 0.0]), "range": None}
        for sub in el:
            t = strip_tag(sub.tag)
            if t == "parent":
                j["parent"] = sub.get("link")
            elif t == "child":
                j["child"] = sub.get("link")
            elif t == "axis":
                j["axis"] = np.array([float(v) for v in sub.get("xyz").split()])
            elif t == "limit":
                j["range"] = (float(sub.get("lower")), float(sub.get("upper")))
        joints[name] = j

link_names = list(links.keys())
root_link = [n for n in link_names if n not in {j["child"] for j in joints.values()}]
assert len(root_link) == 1, f"root links: {root_link}"
ROOT = root_link[0]
print(f"root link: {ROOT}")

# --------------------------------------------------------------------------
# 2. world poses at qpos=0 (MuJoCo) + root orientation
# --------------------------------------------------------------------------
model = mujoco.MjModel.from_xml_path(URDF_PATH)
data = mujoco.MjData(model)
mujoco.mj_forward(model, data)

body_id = {mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i): i for i in range(model.nbody)
           if mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i) is not None}

R_urdf = {}   # body -> world rotation (URDF frame)
t_urdf = {}   # body -> world position (URDF frame)
for name, i in body_id.items():
    R_urdf[name] = data.xmat[i].reshape(3, 3).copy()
    t_urdf[name] = data.xpos[i].copy()

# the URDF root link is merged into the MuJoCo world body: it sits at the origin
if ROOT not in body_id:
    body_id[ROOT] = 0
    R_urdf[ROOT] = np.eye(3)
    t_urdf[ROOT] = np.zeros(3)

# geometry-based directions in URDF frame
hip = (t_urdf["yaw2roll"] + t_urdf["bearing_roll"]) / 2
ankle = (t_urdf["ankle_left"] + t_urdf["ankle_right"]) / 2
up = hip - ankle
up /= np.linalg.norm(up)
fwd_raw = t_urdf["neck"] - t_urdf[ROOT]
fwd_raw = fwd_raw - (fwd_raw @ up) * up
fwd = fwd_raw / np.linalg.norm(fwd_raw)
lat = np.cross(up, fwd)          # right-handed: det([fwd,lat,up]) = +1
assert np.linalg.det(np.vstack([fwd, lat, up])) > 0.99
# R_root maps URDF-frame vectors into the new frame: new_x=fwd, new_y=lat, new_z=up
R_root = np.vstack([fwd, lat, up])
print("R_root (rows=fwd,lat,up):\n", np.round(R_root, 4))

# new world poses
def rotate_v(v):
    return R_root @ v

R_new = {n: R_root @ R_urdf[n] for n in body_id}
t_new = {n: rotate_v(t_urdf[n]) for n in body_id}

# shift so that lowest foot z = 0 and x/y centered
def geom_world_min_z():
    zmin = 1e9
    for gi in range(model.ngeom):
        bname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, model.geom_bodyid[gi])
        if bname not in ("ankle_left", "ankle_right"):
            continue
        m = model.geom_dataid[gi]
        va = model.mesh_vertadr[m]
        vb = model.mesh_vertadr[m + 1] if m + 1 < len(model.mesh_vertadr) else model.nmeshvert
        verts = model.mesh_vert[va:vb]
        Rg = data.geom_xmat[gi].reshape(3, 3)
        pg = data.geom_xpos[gi]
        w = pg[:, None] + Rg @ verts.T
        w_new = R_root @ w
        zmin = min(zmin, w_new[2].min())
    return zmin

zmin = geom_world_min_z()
xs = [t_new[n] for n in body_id]
cx = (min(p[0] for p in xs) + max(p[0] for p in xs)) / 2
cy = (min(p[1] for p in xs) + max(p[1] for p in xs)) / 2
shift = np.array([-cx, -cy, -zmin])
print(f"zmin={zmin:.4f} center=({cx:.4f},{cy:.4f}) shift={np.round(shift,4)}")

for n in body_id:
    t_new[n] = t_new[n] + shift

# --------------------------------------------------------------------------
# 3. relative body transforms for MJCF
# --------------------------------------------------------------------------
def r2q(R):
    # quat (w,x,y,z), normalized
    q = np.zeros(4)
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        q[0] = 0.25 * s
        q[1] = (R[2, 1] - R[1, 2]) / s
        q[2] = (R[0, 2] - R[2, 0]) / s
        q[3] = (R[1, 0] - R[0, 1]) / s
    else:
        i = np.argmax(np.diag(R))
        j = (i + 1) % 3
        k = (j + 1) % 3
        s = np.sqrt(1.0 + R[i, i] - R[j, j] - R[k, k]) * 2
        q[i + 1] = 0.25 * s
        q[j + 1] = (R[j, i] + R[i, j]) / s
        q[k + 1] = (R[k, i] + R[i, k]) / s
        q[0] = (R[k, j] - R[j, k]) / s
    q /= np.linalg.norm(q)
    return q


def rel_pose(child, parent):
    # T_pc = T_wp^{-1} @ T_wc  (child pose expressed in parent frame), in the NEW (upright) frame
    Rp, tp = R_new[parent], t_new[parent]
    Rc, tc = R_new[child], t_new[child]
    Rrel = Rp.T @ Rc
    trel = Rp.T @ (tc - tp)
    return trel, Rrel

# --------------------------------------------------------------------------
# 4. joint axes (MuJoCo <joint axis> is expressed in the PARENT body frame)
# --------------------------------------------------------------------------
MIRROR_PAIRS = {
    "right_hip_yaw": "left_hip_yaw",
    "right_hip_roll": "left_hip_roll",
    "right_hip_pitch": "left_hip_pitch",
    "right_knee": "left_knee",
    "right_ankle": "left_ankle",
}
S = np.diag([1.0, -1.0, 1.0])  # sagittal mirror (flip y)

axis_world_new = {}
axis_mj = {}
for jn, j in joints.items():
    parent, child = j["parent"], j["child"]
    # URDF axis is in the parent frame
    a_urdf = j["axis"] / np.linalg.norm(j["axis"])
    a_w = R_urdf[parent] @ a_urdf
    a_new = R_root @ a_w
    if jn in MIRROR_PAIRS:
        left = MIRROR_PAIRS[jn]
        a_new = S @ axis_world_new[left]   # exact mirror of left
    axis_world_new[jn] = a_new / np.linalg.norm(a_new)
    # express in the (rotated) parent body frame for the MJCF
    a_parent = R_urdf[parent].T @ R_root.T @ axis_world_new[jn]
    a_parent /= np.linalg.norm(a_parent)
    axis_mj[jn] = a_parent

print("\n--- joint axes (new world) ---")
for jn in joints:
    print(f"{jn:16s} world={np.round(axis_world_new[jn],3)} mj(parent)={np.round(axis_mj[jn],3)}")
print("\n--- mirror check (world) ---")
for r, l in MIRROR_PAIRS.items():
    print(f"{r} == mirror({l}): {np.round(S @ axis_world_new[l],3)} vs {np.round(axis_world_new[r],3)}")

# --------------------------------------------------------------------------
# 5. inertials
#    Child body frames in the MJCF are identical to the URDF frames (R_root
#    cancels in the relative poses), so child inertials are emitted verbatim.
#    Only the ROOT body frame is rotated by R_root w.r.t. the URDF base_link
#    frame: its inertia TENSOR must be rotated (pos stays in body coords).
# --------------------------------------------------------------------------
def rotate_tensor_only(d, R):
    I = np.array([[d["ixx"], d["ixy"], d["ixz"]],
                  [d["ixy"], d["iyy"], d["iyz"]],
                  [d["ixz"], d["iyz"], d["izz"]]])
    Inew = R @ I @ R.T
    return dict(
        ixx=Inew[0, 0], iyy=Inew[1, 1], izz=Inew[2, 2],
        ixy=Inew[0, 1], ixz=Inew[0, 2], iyz=Inew[1, 2],
    )

def emit_inertial(d, R_root=None):
    pos = d["pos"] if R_root is None else d["pos"]  # pos always in body coords
    I = d if R_root is None else rotate_tensor_only(d, R_root)
    return f'      <inertial pos="{qstr(pos)}" mass="{fmt.format(d["mass"])}" ' \
           f'fullinertia="{fmt.format(I["ixx"])} {fmt.format(I["iyy"])} {fmt.format(I["izz"])} ' \
           f'{fmt.format(I["ixy"])} {fmt.format(I["ixz"])} {fmt.format(I["iyz"])}"/>'

# --------------------------------------------------------------------------
# 6. emit MJCF
# --------------------------------------------------------------------------
fmt = "{:.9g}"

def qstr(a):
    return " ".join(fmt.format(v) for v in a)

def q_str(q):
    return f"{fmt.format(q[0])} {fmt.format(q[1])} {fmt.format(q[2])} {fmt.format(q[3])}"

# contract order: left leg, head, right leg
CONTRACT = ["left_hip_yaw", "left_hip_roll", "left_hip_pitch", "left_knee", "left_ankle",
            "neck_pitch", "head_pitch", "head_yaw", "head_roll",
            "right_hip_yaw", "right_hip_roll", "right_hip_pitch", "right_knee", "right_ankle"]
assert set(CONTRACT) == set(joints.keys())

# foot body -> pipeline-contract names (foot collision geom + foot site)
FOOT_NAME = {"ankle_left": "left_foot", "ankle_right": "right_foot"}

# tree structure: children of root
children_of = {n: [] for n in link_names}
for jn, j in joints.items():
    children_of[j["parent"]].append((j["child"], jn))
# order of direct children of root for joint ordering
root_children = dict(children_of[ROOT])
def subtree_order(child, jn):
    out = [jn]
    for c, cj in children_of[child]:
        out.extend(subtree_order(c, cj))
    return out

order_blocks = {"left": [], "head": [], "right": []}
for child, jn in root_children.items():
    if jn.startswith("left_"):
        order_blocks["left"] = [jn] + [x for sub in (subtree_order(c, cj) for c, cj in children_of[child]) for x in sub]
    elif jn.startswith("neck_") or jn.startswith("head_"):
        order_blocks["head"] = [jn] + [x for sub in (subtree_order(c, cj) for c, cj in children_of[child]) for x in sub]
    else:
        order_blocks["right"] = [jn] + [x for sub in (subtree_order(c, cj) for c, cj in children_of[child]) for x in sub]

tree_order = order_blocks["left"] + order_blocks["head"] + order_blocks["right"]
print("joint order:", tree_order)

lines = []
W = lines.append
W('<?xml version="1.0" ?>')
W('<!-- Generated from SolidWorks SW2URDF export (open_duck.urdf) by urdf2mjcf.py -->')
W('<!-- Morphology: user SolidWorks Open Duck; joints/limits from CAD; axes mirrored symmetric; root uprighted -->')
W('<mujoco model="open_duck">')
W('  <compiler angle="radian" meshdir="open_duck_assets"/>')
W('  <default>')
W('    <default class="microduck">')
W('      <joint frictionloss="0.1" armature="0.005"/>')
W('      <position kp="50" dampratio="1"/>')
W('      <default class="visual">')
W('        <geom type="mesh" contype="0" conaffinity="0" group="2"/>')
W('      </default>')
W('      <default class="collision">')
W('        <geom group="3"/>')
W('      </default>')
W('    </default>')
W('    <default class="chosen_actuator">')
W('      <geom contype="0" conaffinity="0"/>')
W('      <joint damping="0.053" frictionloss="0.0048" armature="0.0018"/>')
W('      <position kp="0.55" kv="0.0" forcerange="-0.96 0.96" ctrlrange="-10.0 10.0"/>')
W('    </default>')
W('    <default class="passive_joint">')
W('      <geom contype="0" conaffinity="0"/>')
W('      <joint damping="0.0" frictionloss="0.0" armature="0.0001"/>')
W('    </default>')
W('  </default>')
W('  <worldbody>')
W('    <geom name="floor" type="plane" size="5 5 1" condim="3"/>')

# --- trunk (root) ---
trel, Rrel = rel_pose(ROOT, ROOT)  # root relative to world
trel = shift  # = R_root @ 0 + shift
Rrel = R_root
W(f'    <body name="trunk_base" pos="{qstr(trel)}" quat="{q_str(r2q(Rrel))}" childclass="microduck">')
W('      <freejoint name="trunk_base_freejoint"/>')
d = links[ROOT]["inertial"]
W(emit_inertial(d, R_root))
for g in links[ROOT]["visuals"]:
    quat = r2q(np.eye(3)) if np.allclose(g["rpy"], 0) else r2q(euler_rpy(g["rpy"]))
    W(f'      <geom class="visual" mesh="{g["mesh"]}" pos="{qstr(g["pos"])}" quat="{q_str(quat)}"/>')
# sites: imu at trunk center-ish (edf_exit only if EDF hardware enabled)
W('      <site group="3" name="imu" pos="0 0 0.02" quat="1 0 0 0"/>')
if INCLUDE_EDF:
    e3 = np.array([0.0, 0.0, 1.0])
    site_up_local = R_root.T @ e3
    q_site = r2q(np.eye(3))
    if np.linalg.norm(np.cross(e3, site_up_local)) > 1e-6:
        ax = np.cross(e3, site_up_local); ax /= np.linalg.norm(ax)
        ang = np.arccos(np.clip(e3 @ site_up_local, -1, 1))
        K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
        q_site = r2q(np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * K @ K)
    W(f'      <site group="3" name="edf_exit" pos="{qstr(R_root.T @ np.array([0.0, 0.0, -0.045]))}" quat="{q_str(q_site)}"/>')

# --- subtrees in contract order ---
def emit_body(body, parent_joint):
    """body is the child link; parent_joint is the joint that connects parent->body."""
    trel, Rrel = rel_pose(body, joints[parent_joint]["parent"])
    W(f'      <body name="{body}" pos="{qstr(trel)}" quat="{q_str(r2q(Rrel))}">')
    rng = joints[parent_joint]["range"]
    a = axis_mj[parent_joint]
    rngs = f' range="{fmt.format(rng[0])} {fmt.format(rng[1])}"' if rng else ""
    W(f'        <joint name="{parent_joint}" type="hinge" axis="{qstr(a)}"{rngs} class="chosen_actuator"/>')
    d = links[body]["inertial"]
    W(emit_inertial(d))
    for g in links[body]["visuals"]:
        W(f'        <geom class="visual" mesh="{g["mesh"]}" pos="{qstr(g["pos"])}" quat="{q_str(r2q(euler_rpy(g["rpy"])))}"/>')
    if body in ("ankle_left", "ankle_right"):
        # duplicate feet mesh as collision geom + foot site at foot bottom.
        # Naming follows the pipeline contract (velocity/jump tasks hardcode
        # left_foot_collision / right_foot_collision and left_foot / right_foot).
        foot = FOOT_NAME[body]
        for g in links[body]["visuals"]:
            W(f'        <geom name="{foot}_collision" class="collision" type="mesh" mesh="{g["mesh"]}" pos="{qstr(g["pos"])}" quat="{q_str(r2q(euler_rpy(g["rpy"])))}"/>')
        zb = foot_local_vertex(body)
        W(f'        <site group="3" name="{foot}" pos="{qstr(zb)}"/>')
    for c, cj in children_of[body]:
        emit_body(c, cj)
    W('      </body>')

def euler_rpy(rpy):
    r, p, y = rpy
    cr, sr = np.cos(r), np.sin(r)
    cp, sp = np.cos(p), np.sin(p)
    cy, sy = np.cos(y), np.sin(y)
    Rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    Ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx

def foot_local_vertex(body):
    # lowest vertex of this body's meshes, expressed in the body frame
    zmin = 1e9
    best = np.zeros(3)
    for gi in range(model.ngeom):
        if mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, model.geom_bodyid[gi]) != body:
            continue
        m = model.geom_dataid[gi]
        va = model.mesh_vertadr[m]
        vb = model.mesh_vertadr[m + 1] if m + 1 < len(model.mesh_vertadr) else model.nmeshvert
        verts = model.mesh_vert[va:vb]
        Rg = data.geom_xmat[gi].reshape(3, 3)
        pg = data.geom_xpos[gi]
        w = pg[:, None] + Rg @ verts.T                 # URDF world frame
        w_new = R_root @ w + shift[:, None]            # NEW (upright, shifted) world frame
        wb = R_new[body].T @ (w_new - t_new[body][:, None])   # body-local frame
        k = int(np.argmin(wb[2]))
        if wb[2, k] < zmin:
            zmin = wb[2, k]
            best = wb[:, k]
    return best

# emit subtrees in order: find child link per joint
child_of_joint = {jn: j["child"] for jn, j in joints.items()}
def emit_from_root():
    for block in ("left", "head", "right"):
        for jn in order_blocks[block]:
            child = child_of_joint[jn]
            if joints[jn]["parent"] == ROOT:
                emit_body(child, jn)
emit_from_root()

W('    </body>')
W('  </worldbody>')

# assets
W('  <asset>')
for n in link_names:
    for g in links[n]["visuals"]:
        W(f'    <mesh name="{g["mesh"]}" file="{g["mesh"]}"/>')
        W(f'    <material name="{g["mesh"]}_material" rgba="{" ".join(fmt.format(v) for v in g["rgba"])}"/>')
W('  </asset>')

# sensors (same set as crazy_chick_walk.xml)
W('  <sensor>')
W('    <framequat name="orientation" objtype="site" noise="0.001" objname="imu"/>')
W('    <gyro name="angular-velocity" site="imu" noise="0.005"/>')
W('    <gyro name="imu_ang_vel" site="imu"/>')
W('    <velocimeter name="imu_lin_vel" site="imu"/>')
W('    <accelerometer name="imu_accel" site="imu"/>')
W('    <subtreeangmom name="root_angmom" body="trunk_base"/>')
W('  </sensor>')

# actuators
W('  <actuator>')
for jn in CONTRACT:
    W(f'    <position class="chosen_actuator" name="{jn}" joint="{jn}"/>')
if INCLUDE_EDF:
    W('    <motor name="edf_thrust" site="edf_exit" gear="0 0 1" ctrlrange="0 1" ctrllimited="true"/>')
W('  </actuator>')
W('</mujoco>')

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"\nWrote {OUT_PATH}")

# copy meshes
os.makedirs(ASSET_DIR, exist_ok=True)
import shutil
for f in os.listdir(SRC_MESHES):
    shutil.copy2(os.path.join(SRC_MESHES, f), os.path.join(ASSET_DIR, f))
print(f"Copied {len(os.listdir(ASSET_DIR))} meshes -> {ASSET_DIR}")
