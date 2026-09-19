// 疯狂小鸡 · URDF / MJCF 3D 查看器
// 支持两种格式（自动识别）：
//   - URDF  : <robot> 根元素，直接按 URDF 运动学树装配（origin.xyz/rpy + axis）
//   - MJCF  : <mujoco> 根元素（MuJoCo XML），按 body 树装配（pos/quat + joint axis/range）
// 提供基本查看功能：旋转视角、关节滑杆（按模型实际限位）、线框/半透明/包围盒、关节轴/坐标系。
//
// FK 约定：
//   URDF: child 在 parent 中的位姿 = T(origin.xyz) * R(origin.rpy) * R(axis, q)
//   MJCF:  body 位姿 = parent * T(body.pos) * R(body.quat) * R(axis1, q1) * R(axis2, q2) ...

const R2D = 180 / Math.PI;
const D2R = Math.PI / 180;

// ---------- 模型清单 ----------
// url      : XML/URDF 相对服务器根 的路径（serve.mjs 以项目根为根）
// meshBase : 网格基准目录；URDF 的 package:// 或相对网格由此解析
//            MJCF 留空则按 compiler.meshdir 相对模型文件目录解析
const MODELS = [
  // ---- crazy_chick 系列（本仓 training/，自研）----
  { id: "crazy_chick_walk", label: "crazy_chick_walk.xml",
    url: "/training/microduck_rl/src/mjlab_microduck/robot/crazy_chick/crazy_chick_walk.xml",
    meshBase: "/microduck_rl/src/mjlab_microduck/robot/microduck/assets/" },
  { id: "crazy_chick_walk_backlash", label: "crazy_chick_walk_backlash.xml",
    url: "/training/microduck_rl/src/mjlab_microduck/robot/crazy_chick/crazy_chick_walk_backlash.xml",
    meshBase: "/microduck_rl/src/mjlab_microduck/robot/microduck/assets/" },
  { id: "crazy_chick_allcollisions", label: "crazy_chick_allcollisions.xml",
    url: "/training/microduck_rl/src/mjlab_microduck/robot/crazy_chick/crazy_chick_allcollisions.xml",
    meshBase: "/microduck_rl/src/mjlab_microduck/robot/microduck/assets/" },
  { id: "crazy_chick_allcollisions_backlash", label: "crazy_chick_allcollisions_backlash.xml",
    url: "/training/microduck_rl/src/mjlab_microduck/robot/crazy_chick/crazy_chick_allcollisions_backlash.xml",
    meshBase: "/microduck_rl/src/mjlab_microduck/robot/microduck/assets/" },
  { id: "crazy_chick_allcollisions_rollers", label: "crazy_chick_allcollisions_rollers.xml",
    url: "/training/microduck_rl/src/mjlab_microduck/robot/crazy_chick/crazy_chick_allcollisions_rollers.xml",
    meshBase: "/microduck_rl/src/mjlab_microduck/robot/microduck/assets/" },
  { id: "crazy_chick_allcollisions_rollers_backlash", label: "crazy_chick_allcollisions_rollers_backlash.xml",
    url: "/training/microduck_rl/src/mjlab_microduck/robot/crazy_chick/crazy_chick_allcollisions_rollers_backlash.xml",
    meshBase: "/microduck_rl/src/mjlab_microduck/robot/microduck/assets/" },

  // ---- microduck 原版（microduck_rl 子模块，参考基准）----
  { id: "microduck_walk", label: "microduck robot_walk.xml (原版)",
    url: "/microduck_rl/src/mjlab_microduck/robot/microduck/robot_walk.xml",
    meshBase: "/microduck_rl/src/mjlab_microduck/robot/microduck/assets/" },

  // ---- Open Duck（本仓 training/，SolidWorks 转换版 MJCF）----
  { id: "open_duck_walk", label: "open_duck_walk.xml",
    url: "/training/microduck_rl/src/mjlab_microduck/robot/crazy_chick/open_duck_walk.xml",
    meshBase: "/training/microduck_rl/src/mjlab_microduck/robot/crazy_chick/open_duck_assets/" },

  // 注：SolidWorks 导出的 URDF 请用 ../editor/ 打开——本查看器面向 MJCF，
  //     URDF 的关节/惯量编辑在 editor 里做。
];

const CONFIG = { model: MODELS[0] };

let THREE, OrbitControls, STLLoader;
let renderer, scene, camera, controls;
const canvas = document.getElementById("gl");
const panelEl = document.getElementById("panel");
const hudEl = document.getElementById("hud");
const errEl = document.getElementById("err");

// ---------- 模型状态 ----------
let robotGroup = null;
let floorGroup = null;
let refGroup = null;          // 叠加参考模型（半透明）的组
let refLoaded = false;        // 参考模型是否已加载
const refLinkGroups = new Map();
let mode = "mjcf";                    // "urdf" | "mjcf"
const linkGroups = new Map();         // linkName/bodyName -> {group, meshes:[...]}
const jointList = [];                 // {name, parent, child, axis, lo, hi, node, arrow, childGroup, axisNorm, bodyNode?}
const jointState = new Map();         // jointName -> current rad
const bodyNodes = [];                 // MJCF: {group, baseQ, joints:[{name,axis,lo,hi}]}
let showAxesFlag = false, showGridFlag = true, wireframeFlag = false,
    transparentFlag = false, bboxFlag = false, homePoseFlag = true;
let activeJointName = null;

// ---------- 工具 ----------
function num(el, name, def) {
  const s = el.getAttribute(name);
  if (s === null || s === undefined || s.trim() === "") return def;
  return parseFloat(s);
}
function vec3(s) {
  const a = s.trim().split(/\s+/).map(parseFloat);
  return new THREE.Vector3(a[0], a[1], a[2]);
}
function quatFromRPY(rpy) {
  return new THREE.Quaternion().setFromEuler(new THREE.Euler(rpy[0], rpy[1], rpy[2], "XYZ"));
}
// MuJoCo quat 顺序 w x y z
function quatFromWXYZ(s) {
  const a = (s || "1 0 0 0").trim().split(/\s+/).map(parseFloat);
  return new THREE.Quaternion(a[1], a[2], a[3], a[0]);
}
function fail(msg) {
  errEl.style.display = "flex";
  errEl.textContent = "⚠ 加载失败：" + msg;
}
function showError(e) {
  fail((e && e.message) ? e.message : String(e));
  console.error(e);
}

// ---------- three 初始化 ----------
function initThree() {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(canvas.clientWidth, canvas.clientHeight);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x2a3340);

  camera = new THREE.PerspectiveCamera(55, canvas.clientWidth / canvas.clientHeight, 0.01, 300);
  camera.up.set(0, 0, 1);
  camera.position.set(0.9, -1.4, 0.6);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 0.1;
  controls.maxDistance = 12;
  controls.target.set(0, 0, 0.2);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x46505c, 1.05));
  const key = new THREE.DirectionalLight(0xffffff, 1.3);
  key.position.set(0.6, -1.0, 1.3);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xbcd2e8, 0.5);
  rim.position.set(-0.8, 0.9, -0.5);
  scene.add(rim);

  const grid = new THREE.GridHelper(1.2, 24, 0x4a5a68, 0x3c4a56);
  grid.rotation.x = Math.PI / 2;
  floorGroup = new THREE.Group();
  floorGroup.add(grid);
  scene.add(floorGroup);
}

// =====================================================================
//  URDF 解析（<robot>）
// =====================================================================
async function parseURDF(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.querySelector("parsererror")) {
    throw new Error("URDF XML 解析错误: " + doc.querySelector("parsererror").textContent);
  }
  const robot = doc.querySelector("robot");
  const links = {};
  const joints = [];

  for (const l of robot.querySelectorAll("link")) {
    const name = l.getAttribute("name");
    const meshEl = l.querySelector("visual > geometry > mesh");
    links[name] = { name, mesh: meshEl ? meshEl.getAttribute("filename") : null };
  }

  for (const j of robot.querySelectorAll("joint")) {
    const name = j.getAttribute("name");
    const type = j.getAttribute("type");
    const parent = j.querySelector("parent").getAttribute("link");
    const child = j.querySelector("child").getAttribute("link");
    const origin = j.querySelector("origin");
    const axisEl = j.querySelector("axis");
    const limit = j.querySelector("limit");
    const o = origin
      ? { xyz: vec3(origin.getAttribute("xyz") || "0 0 0"),
          rpy: (origin.getAttribute("rpy") || "0 0 0").trim().split(/\s+/).map(parseFloat) }
      : { xyz: new THREE.Vector3(), rpy: [0, 0, 0] };
    const axis = axisEl ? vec3(axisEl.getAttribute("xyz") || "0 0 1") : new THREE.Vector3(0, 0, 1);
    joints.push({
      name, type, parent, child, origin: o, axis,
      lo: limit ? num(limit, "lower", -1e9) : -1e9,
      hi: limit ? num(limit, "upper", 1e9) : 1e9,
    });
  }
  return { name: robot.getAttribute("name"), links, joints };
}

async function buildURDF(urdf) {
  // 加载 mesh：支持 package://xxx/name.stl、package:///name.stl 与相对路径 assets/name.stl
  const meshBase = CONFIG.model.meshBase || "";
  async function loadMesh(filename) {
    let url;
    if (filename.startsWith("package://")) {
      url = meshBase + filename.split("/").pop();
    } else if (/^(https?:)?\//.test(filename)) {
      url = filename;
    } else {
      // 相对路径（如 assets/xxx.stl）：相对模型目录解析
      const modelUrl = new URL(CONFIG.model.url, location.origin);
      url = new URL(filename, modelUrl.href.replace(/[^/]*$/, "")).href;
    }
    const loader = new STLLoader();
    return await loader.loadAsync(url);
  }

  const tasks = [];
  for (const [name, link] of Object.entries(urdf.links)) {
    if (!link.mesh) continue;
    tasks.push(loadMesh(link.mesh)
      .then(geo => {
        const mat = new THREE.MeshStandardMaterial({
          color: 0x9aa7b5, metalness: 0.25, roughness: 0.55, wireframe: false,
        });
        const mesh = new THREE.Mesh(geo, mat);
        linkGroups.get(name).meshes.push(mesh);
        linkGroups.get(name).group.add(mesh);
      })
      .catch(e => console.warn(`${name}: 网格加载失败 ${e.message}`)));
  }
  await Promise.all(tasks);

  // 建立关节层级
  const isChild = new Set(urdf.joints.map(j => j.child));
  const roots = Object.keys(urdf.links).filter(n => !isChild.has(n));
  if (roots.length === 0) roots.push(urdf.joints[0].parent);

  function attach(parentName, visited) {
    for (const j of urdf.joints) {
      if (j.parent !== parentName) continue;
      if (visited.has(j.child)) continue;
      visited.add(j.child);
      const childGroup = linkGroups.get(j.child).group;

      const jnode = new THREE.Group();
      jnode.position.copy(j.origin.xyz);
      jnode.quaternion.copy(quatFromRPY(j.origin.rpy));
      const arrow = makeAxisArrow();
      jnode.add(arrow);

      robotGroup.remove(childGroup);
      jnode.add(childGroup);
      linkGroups.get(parentName).group.add(jnode);
      alignArrowToAxis(arrow, j.axis);

      jointList.push({
        ...j, node: jnode, arrow, childGroup,
        axisNorm: j.axis.clone().normalize(),
      });
      jointState.set(j.name, 0);

      attach(j.child, visited);
    }
  }
  const visited = new Set(roots);
  for (const r of roots) attach(r, visited);
}

// =====================================================================
//  MJCF 解析（<mujoco>）
// =====================================================================
async function loadMJCF(entry) {
  const xmlText = await (await fetch(entry.url)).text();
  const doc = new DOMParser().parseFromString(xmlText, "text/xml");
  if (doc.querySelector("parsererror")) throw new Error("MJCF XML 解析失败");

  const mujoco = doc.querySelector("mujoco") || doc.documentElement;
  const modelName = mujoco.getAttribute("model") || "model";

  // 网格基准：优先用配置的 meshBase，否则按 compiler.meshdir 相对模型目录解析
  let meshBase;
  if (entry.meshBase) {
    meshBase = entry.meshBase;
  } else {
    const compiler = mujoco.querySelector("compiler");
    const meshdir = compiler ? (compiler.getAttribute("meshdir") || "") : "";
    const modelUrl = new URL(entry.url, location.origin);
    meshBase = new URL((meshdir ? meshdir + "/" : ""), modelUrl.href.replace(/[^/]*$/, "")).href;
  }

  // mesh name -> 文件（MuJoCo 语义：未写 name 时用文件名去扩展名）
  const meshFile = new Map();
  mujoco.querySelectorAll("mesh").forEach((me) => {
    const file = me.getAttribute("file");
    if (!file) return;
    const name = me.getAttribute("name") || file.replace(/\.[^.]+$/, "");
    meshFile.set(name, file);
  });
  // material name -> rgba
  const matColor = new Map();
  mujoco.querySelectorAll("material").forEach((mt) => {
    const rgba = (mt.getAttribute("rgba") || "0.8 0.8 0.8 1").trim().split(/\s+/).map(parseFloat);
    matColor.set(mt.getAttribute("name"), { r: rgba[0], g: rgba[1], b: rgba[2], a: rgba.length > 3 ? rgba[3] : 1 });
  });

  const geoCache = new Map();
  const stlLoader = new STLLoader();
  const geometryFor = async (meshName) => {
    const file = meshFile.get(meshName);
    if (!file) throw new Error("找不到网格资产: " + meshName);
    if (!geoCache.has(file)) {
      geoCache.set(file, stlLoader.loadAsync(meshBase + file));
    }
    return geoCache.get(file);
  };

  const materialFor = (meshName, geomEl) => {
    let col = matColor.get(meshName + "_material") || matColor.get(meshName);
    if (!col) col = { r: 0.72, g: 0.74, b: 0.78, a: 1 };
    const c = new THREE.Color(col.r, col.g, col.b);
    return new THREE.MeshStandardMaterial({
      color: c, roughness: 0.55, metalness: 0.12,
      transparent: col.a < 0.999, opacity: col.a,
    });
  };

  // 只看可视化网格：class 不含 collision、group 为 2（默认 visual）或未指定
  const isVisualGeom = (g) => {
    const cls = g.getAttribute("class") || "";
    if (cls.includes("collision")) return false;
    const grp = g.getAttribute("group");
    if (grp !== null && grp !== "2") return false;
    return true;
  };

  const world = mujoco.querySelector("worldbody");
  const promises = [];

  // 世界固定 geom（地板等）
  for (const g of world.children) {
    if (g.tagName !== "geom") continue;
    const type = g.getAttribute("type") || "mesh";
    if (type === "mesh" && g.getAttribute("mesh")) {
      promises.push((async () => {
        const geo = await geometryFor(g.getAttribute("mesh"));
        const mesh = new THREE.Mesh(geo, materialFor(g.getAttribute("mesh"), g));
        mesh.position.copy(vec3(g.getAttribute("pos") || "0 0 0"));
        mesh.quaternion.copy(quatFromWXYZ(g.getAttribute("quat")));
        mesh.scale.setScalar(num(g, "scale", 1));
        floorGroup.add(mesh);
      })());
    }
  }

  // 递归解析 body 树
  const addBody = (el, parentGroup) => {
    const name = el.getAttribute("name") || ("body_" + bodyNodes.length);
    const pos = vec3(el.getAttribute("pos") || "0 0 0");
    const baseQ = quatFromWXYZ(el.getAttribute("quat"));

    const group = new THREE.Group();
    group.position.copy(pos);
    group.quaternion.copy(baseQ);

    // 该 body 上的 hinge 关节（可多个；freejoint 跳过）
    const joints = [];
    for (const ch of el.children) {
      if (ch.tagName !== "joint") continue;
      const jtype = ch.getAttribute("type") || "hinge";
      const jname = ch.getAttribute("name");
      if (jtype === "hinge" && jname && jname !== "trunk_base_freejoint") {
        const axis = vec3(ch.getAttribute("axis") || "1 0 0").normalize();
        const r = ch.getAttribute("range");
        let lo = -Math.PI, hi = Math.PI;
        if (r) { const a = r.trim().split(/\s+/).map(parseFloat); lo = a[0]; hi = a[1]; }
        joints.push({ name: jname, axis, lo, hi });
      }
    }

    bodyNodes.push({ body: name, group, baseQ: baseQ.clone(), joints });
    linkGroups.set(name, { group, meshes: [] });
    parentGroup.add(group);

    // geoms
    for (const g of el.children) {
      if (g.tagName !== "geom") continue;
      if (!isVisualGeom(g)) continue;
      if (g.getAttribute("type") && g.getAttribute("type") !== "mesh") continue;
      const meshName = g.getAttribute("mesh");
      if (!meshName) continue;
      promises.push((async () => {
        const geo = await geometryFor(meshName);
        const mesh = new THREE.Mesh(geo, materialFor(meshName, g));
        mesh.position.copy(vec3(g.getAttribute("pos") || "0 0 0"));
        mesh.quaternion.copy(quatFromWXYZ(g.getAttribute("quat")));
        const sc = g.getAttribute("scale");
        if (sc) mesh.scale.setScalar(parseFloat(sc));
        group.add(mesh);
        linkGroups.get(name).meshes.push(mesh);
      })());
    }

    // 记录关节到全局列表（用于滑杆 / 轴箭头）
    for (const j of joints) {
      const arrow = makeAxisArrow();
      group.add(arrow);
      alignArrowToAxis(arrow, j.axis);
      jointList.push({
        name: j.name, type: "hinge", parent: "?", child: name,
        origin: { xyz: pos, rpy: [0, 0, 0] }, axis: j.axis.clone(),
        lo: j.lo, hi: j.hi, node: group, arrow, childGroup: group,
        axisNorm: j.axis.clone(), bodyNode: group,
      });
      jointState.set(j.name, 0);
    }

    // 子 body
    for (const c of el.children) {
      if (c.tagName === "body") addBody(c, group);
    }
  };

  for (const c of world.children) {
    if (c.tagName === "body") addBody(c, robotGroup);
  }

  await Promise.all(promises);
  if (jointList.length === 0) throw new Error("没有解析到任何关节");
  return { name: modelName };
}

// 参考模型材质：半透明 + 高亮色调，便于叠加对比（顶层，供 ref 加载使用）
function refMaterialFor() {
  const col = { r: 0.95, g: 0.35, b: 0.35, a: 0.32 };
  const c = new THREE.Color(col.r, col.g, col.b);
  return new THREE.MeshStandardMaterial({
    color: c, roughness: 0.3, metalness: 0.0,
    transparent: true, opacity: 0.32, depthWrite: false,
  });
}

function makeAxisArrow() {
  const g = new THREE.Group();
  const dot = new THREE.Mesh(
    new THREE.SphereGeometry(0.008, 12, 12),
    new THREE.MeshBasicMaterial({ color: 0xffffff })
  );
  g.add(dot);
  const shaft = new THREE.Mesh(
    new THREE.CylinderGeometry(0.004, 0.004, 0.09, 8),
    new THREE.MeshBasicMaterial({ color: 0x2fe0b0 })
  );
  shaft.position.z = 0.045;
  const tip = new THREE.Mesh(
    new THREE.ConeGeometry(0.012, 0.03, 8),
    new THREE.MeshBasicMaterial({ color: 0x2fe0b0 })
  );
  tip.position.z = 0.11;
  const ax = new THREE.Group();
  ax.add(shaft); ax.add(tip);
  g.add(ax);
  const axLen = 0.05;
  const col = [0xff5a5a, 0x57c98f, 0x4da3ff];
  const bases = [new THREE.Vector3(1,0,0), new THREE.Vector3(0,1,0), new THREE.Vector3(0,0,1)];
  bases.forEach((b, i) => {
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), b.clone().multiplyScalar(axLen)]),
      new THREE.LineBasicMaterial({ color: col[i] })
    );
    g.add(line);
  });
  g.visible = false;
  return g;
}

function alignArrowToAxis(arrow, axis) {
  const a = axis.clone().normalize();
  if (a.lengthSq() < 1e-12) return;
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), a);
  arrow.quaternion.copy(q);
}

// ---------- 运动学更新 ----------
function applyFk() {
  if (mode === "urdf") {
    for (const j of jointList) {
      const q = jointState.get(j.name) || 0;
      j.childGroup.quaternion.copy(new THREE.Quaternion().setFromAxisAngle(j.axisNorm, q));
      alignArrowToAxis(j.arrow, j.axis);
    }
  } else {
    // MJCF: body 位姿 = baseQ * R(axis1,q1) * R(axis2,q2)...
    for (const n of bodyNodes) {
      const q = n.baseQ.clone();
      for (const j of n.joints) {
        const a = jointState.get(j.name) || 0;
        if (a !== 0) q.multiply(new THREE.Quaternion().setFromAxisAngle(j.axis, a));
      }
      n.group.quaternion.copy(q);
      for (const j of n.joints) {
        const jj = jointList.find(x => x.name === j.name);
        if (jj) alignArrowToAxis(jj.arrow, j.axis);
      }
    }
  }
  for (const [name, lg] of linkGroups) {
    const isActive = name === activeJointName;
    for (const m of lg.meshes) {
      m.material.emissive = isActive ? new THREE.Color(0x1a3550) : new THREE.Color(0x000000);
    }
  }
}

function updateMaterials() {
  for (const [name, lg] of linkGroups) {
    for (const m of lg.meshes) {
      m.material.wireframe = wireframeFlag;
      m.material.transparent = transparentFlag;
      m.material.opacity = transparentFlag ? 0.45 : 1.0;
      m.material.depthWrite = !transparentFlag;
    }
  }
}

function toggleArrows(show) {
  for (const j of jointList) j.arrow.visible = show;
}

let bboxHelper = null;
function updateBBox(show) {
  if (bboxHelper) { robotGroup.remove(bboxHelper); bboxHelper = null; }
  if (!show) return;
  robotGroup.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(robotGroup);
  bboxHelper = new THREE.Box3Helper(box, 0xffaa44);
  robotGroup.add(bboxHelper);
}

// ---------- 面板：关节滑杆（按模型实际关节动态生成） ----------
function jointGroupLabel(name) {
  if (/^left_/.test(name)) return "🦵 左腿 Left";
  if (/^right_/.test(name)) return "🦵 右腿 Right";
  if (/^(head_|neck_)/.test(name)) return "🐤 头颈 Head/Neck";
  if (/^passive_/.test(name)) return "🔩 被动/滚轮 Passive";
  return "⚙️ 其他 Other";
}

function buildPanel() {
  const groups = new Map();
  for (const j of jointList) {
    const label = jointGroupLabel(j.name);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(j);
  }

  let html = "";
  for (const [label, joints] of groups) {
    let inner = "";
    for (const j of joints) {
      // 实际限位（度）；无限位时自由旋转 ±360° 便于检查坐标系与轴线
      let LO, HI, limitTxt;
      if (j.lo < -1e6 || j.hi > 1e6) {
        LO = -360; HI = 360; limitTxt = "不限位";
      } else {
        LO = Math.max(-360, Math.round(j.lo * R2D));
        HI = Math.min(360, Math.round(j.hi * R2D));
        limitTxt = `${(j.lo * R2D).toFixed(1)}° ~ ${(j.hi * R2D).toFixed(1)}°`;
      }
      inner += `<div class="jt" data-j="${j.name}">
        <div class="jt-head">
          <span class="jt-name" title="点击高亮 ${j.child} 部件">${j.name}</span>
          <span class="jt-val" data-val="${j.name}">0.0°</span>
        </div>
        <div class="jt-limits"><span>${limitTxt}</span><span>HOME=0°</span></div>
        <input type="range" data-range="${j.name}" min="${LO}" max="${HI}" step="0.1" value="0" />
        <div class="axis-row">axis: ${j.axis.toArray().map(v => v.toFixed(3)).join("  ")}</div>
      </div>`;
    }
    html += `<details open><summary>${label} <span style="font-weight:400">(${joints.length})</span></summary>${inner}</details>`;
  }
  panelEl.innerHTML = html;

  panelEl.querySelectorAll("input[type=range]").forEach(inp => {
    const name = inp.dataset.range;
    inp.addEventListener("input", () => {
      const deg = parseFloat(inp.value);
      jointState.set(name, deg * D2R);
      const valEl = panelEl.querySelector(`[data-val="${name}"]`);
      if (valEl) valEl.textContent = `${deg.toFixed(1)}°`;
      homePoseFlag = false;
      document.getElementById("homePose").checked = false;
      toggleArrows(showAxesFlag);
      applyFk(); updateBBox(bboxFlag);
    });
  });

  panelEl.querySelectorAll(".jt-name").forEach(el => {
    el.addEventListener("click", () => {
      const jn = el.closest(".jt").dataset.j;
      activeJointName = activeJointName === jn ? null : (jointList.find(j => j.name === jn).child);
      applyFk();
    });
  });
}

// ---------- HUD ----------
function refreshHud() {
  hudEl.innerHTML = `
    <div><b>${CONFIG.model.label}</b></div>
    <div>格式 <b>${mode.toUpperCase()}</b> · links <b>${linkGroups.size}</b> · joints <b>${jointList.length}</b></div>
    <div style="font-size:11px;color:var(--dim)">拖动旋转 · 滚轮缩放 · 右键平移 · 点击左侧关节名高亮</div>`;
}

// ---------- 视图 ----------
let worldUp = "z";
function setWorldUp(axis) {
  worldUp = axis;
  const btn = document.getElementById("btnUp");
  btn.textContent = `世界轴: ${axis === "z" ? "Z↑" : "Y↑"}`;
  camera.up.set(0, 0, 1);
  if (axis === "y") camera.up.set(0, 1, 0);
  controls.update();
  fitView();
}

function fitView() {
  if (!robotGroup) return;
  robotGroup.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(robotGroup);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const d = Math.max(size.length() * 1.7, 0.4);
  const dir = new THREE.Vector3(0.5, -1.0, 0.45).normalize();
  camera.position.copy(center).addScaledVector(dir, d);
  controls.target.copy(center);
  controls.update();
}

function resetHome() {
  for (const j of jointList) jointState.set(j.name, 0);
  panelEl.querySelectorAll("input[type=range]").forEach(inp => {
    inp.value = 0;
    const name = inp.dataset.range;
    const valEl = panelEl.querySelector(`[data-val="${name}"]`);
    if (valEl) valEl.textContent = "0.0°";
  });
  activeJointName = null;
  homePoseFlag = true;
  document.getElementById("homePose").checked = true;
  toggleArrows(showAxesFlag);
  applyFk(); updateBBox(bboxFlag);
}

function onResize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

// ---------- 参考模型（叠加半透明，用于双模型对比） ----------
async function loadReference(entry) {
  // 清掉旧参考
  if (refGroup) { scene.remove(refGroup); refGroup = null; }
  refLinkGroups.clear();
  refGroup = new THREE.Group();
  scene.add(refGroup);

  const xmlText = await (await fetch(entry.url)).text();
  const doc = new DOMParser().parseFromString(xmlText, "text/xml");
  if (doc.querySelector("parsererror")) throw new Error("参考模型 XML 解析失败");
  const root = doc.documentElement;
  const rootTag = root.tagName.toLowerCase();
  if (rootTag === "mujoco") {
    await loadMJCFRefBody(entry, xmlText);
  } else if (rootTag === "robot") {
    throw new Error("参考模型暂只支持 MJCF（microduck/open_duck 均为 MJCF）");
  } else {
    throw new Error("未知模型根: " + rootTag);
  }

  // 对齐：让参考模型 trunk_base 与主模型 trunk_base 世界位置重合
  alignReferenceToMain();
  refLoaded = true;
  refreshHud();
}

async function loadMJCFRefBody(entry, xmlText) {
  const doc = new DOMParser().parseFromString(xmlText, "text/xml");
  const mujoco = doc.querySelector("mujoco") || doc.documentElement;
  let meshBase = entry.meshBase;
  if (!meshBase) {
    const compiler = mujoco.querySelector("compiler");
    const meshdir = compiler ? (compiler.getAttribute("meshdir") || "") : "";
    const modelUrl = new URL(entry.url, location.origin);
    meshBase = new URL((meshdir ? meshdir + "/" : ""), modelUrl.href.replace(/[^/]*$/, "")).href;
  }
  const meshFile = new Map();
  mujoco.querySelectorAll("mesh").forEach((me) => {
    const file = me.getAttribute("file");
    if (!file) return;
    const name = me.getAttribute("name") || file.replace(/\.[^.]+$/, "");
    meshFile.set(name, file);
  });
  const matColor = new Map();
  mujoco.querySelectorAll("material").forEach((mt) => {
    const rgba = (mt.getAttribute("rgba") || "0.8 0.8 0.8 1").trim().split(/\s+/).map(parseFloat);
    matColor.set(mt.getAttribute("name"), rgba);
  });
  const geoCache = new Map();
  const stlLoader = new STLLoader();
  const geometryFor = async (meshName) => {
    const file = meshFile.get(meshName);
    if (!file) return null;
    if (!geoCache.has(file)) geoCache.set(file, stlLoader.loadAsync(meshBase + file));
    return geoCache.get(file);
  };
  const isVisualGeom = (g) => {
    const cls = g.getAttribute("class") || "";
    if (cls.includes("collision")) return false;
    const grp = g.getAttribute("group");
    if (grp !== null && grp !== "2") return false;
    return true;
  };
  const world = mujoco.querySelector("worldbody");
  const tasks = [];
  const addBody = (el, parentGroup) => {
    const name = el.getAttribute("name") || ("body_" + refLinkGroups.size);
    const pos = vec3(el.getAttribute("pos") || "0 0 0");
    const baseQ = quatFromWXYZ(el.getAttribute("quat"));
    const group = new THREE.Group();
    group.position.copy(pos);
    group.quaternion.copy(baseQ);
    refLinkGroups.set(name, { group, meshes: [] });
    parentGroup.add(group);
    for (const g of el.children) {
      if (g.tagName !== "geom") continue;
      if (!isVisualGeom(g)) continue;
      if (g.getAttribute("type") && g.getAttribute("type") !== "mesh") continue;
      const meshName = g.getAttribute("mesh");
      if (!meshName) continue;
      tasks.push((async () => {
        const geo = await geometryFor(meshName);
        if (!geo) return;
        const mesh = new THREE.Mesh(geo, refMaterialFor(meshName));
        mesh.position.copy(vec3(g.getAttribute("pos") || "0 0 0"));
        mesh.quaternion.copy(quatFromWXYZ(g.getAttribute("quat")));
        group.add(mesh);
        refLinkGroups.get(name).meshes.push(mesh);
      })());
    }
    for (const c of el.children) {
      if (c.tagName === "body") addBody(c, group);
    }
  };
  for (const c of world.children) {
    if (c.tagName === "body") addBody(c, refGroup);
  }
  await Promise.all(tasks);
}

function alignReferenceToMain() {
  if (!robotGroup || !refGroup) return;
  const mainTrunk = linkGroups.get("trunk_base");
  const refTrunk = refLinkGroups.get("trunk_base");
  if (!mainTrunk || !refTrunk) return;
  robotGroup.updateMatrixWorld(true);
  refGroup.updateMatrixWorld(true);
  const mp = new THREE.Vector3();
  mainTrunk.group.getWorldPosition(mp);
  const rp = new THREE.Vector3();
  refTrunk.group.getWorldPosition(rp);
  refGroup.position.add(mp.clone().sub(rp));
  refGroup.updateMatrixWorld(true);
}

function toggleReference(show) {
  if (refGroup) refGroup.visible = show;
}

// ---------- 模型加载/切换 ----------
function clearModel() {
  if (robotGroup) { scene.remove(robotGroup); robotGroup = null; }
  linkGroups.clear();
  jointList.length = 0;
  bodyNodes.length = 0;
  jointState.clear();
  bboxHelper = null;
}

async function loadModel(entry) {
  clearModel();
  const xmlText = await (await fetch(entry.url)).text();
  const probe = new DOMParser().parseFromString(xmlText, "text/xml");
  const root = probe.documentElement;
  const rootTag = root && root.tagName ? root.tagName.toLowerCase() : "";

  robotGroup = new THREE.Group();
  scene.add(robotGroup);

  if (rootTag === "robot") {
    mode = "urdf";
    const urdf = parseURDF(xmlText);
    for (const name of Object.keys(urdf.links)) {
      const g = new THREE.Group();
      robotGroup.add(g);
      linkGroups.set(name, { group: g, meshes: [] });
    }
    await buildURDF(urdf);
  } else if (rootTag === "mujoco") {
    mode = "mjcf";
    await loadMJCF(entry);
  } else {
    throw new Error(`未知模型根元素: ${rootTag || "(空)"}（仅支持 <robot> 与 <mujoco>）`);
  }

  applyFk();
  buildPanel();
  refreshHud();
  updateMaterials();
  toggleArrows(showAxesFlag);
  fitView();

  document.getElementById("hdrInfo").textContent =
    `${linkGroups.size} links · ${jointList.length} joints · ${CONFIG.model.label}`;
}

// ---------- boot ----------
async function boot() {
  try {
    THREE = await import("three");
    OrbitControls = (await import("three/addons/controls/OrbitControls.js")).OrbitControls;
    STLLoader = (await import("three/addons/loaders/STLLoader.js")).STLLoader;
    initThree();
    onResize();

    // 模型下拉框
    const sel = document.getElementById("modelSel");
    for (const m of MODELS) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.label;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", async () => {
      CONFIG.model = MODELS.find(m => m.id === sel.value) || MODELS[0];
      try {
        await loadModel(CONFIG.model);
      } catch (e) { showError(e); }
    });

    // 参考模型（叠加半透明对比）
    const refSel = document.getElementById("refSel");
    const refOn = document.getElementById("refOn");
    for (const m of MODELS) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.label;
      refSel.appendChild(opt);
    }
    const loadRef = async (id) => {
      const entry = MODELS.find(m => m.id === id);
      if (!entry) return;
      try {
        await loadReference(entry);
        refOn.checked = true;
      } catch (e) { console.warn("参考模型加载失败", e); }
    };
    refSel.addEventListener("change", () => loadRef(refSel.value));
    refOn.addEventListener("change", () => toggleReference(refOn.checked));

    await loadModel(CONFIG.model);
    animate();

    document.getElementById("btnHome").addEventListener("click", resetHome);
    document.getElementById("btnFit").addEventListener("click", fitView);
    document.getElementById("btnUp").addEventListener("click", () => {
      setWorldUp(worldUp === "z" ? "y" : "z");
    });
    document.getElementById("showAxes").addEventListener("change", (e) => {
      showAxesFlag = e.target.checked;
      toggleArrows(showAxesFlag);
    });
    document.getElementById("showGrid").addEventListener("change", (e) => {
      showGridFlag = e.target.checked;
      floorGroup.visible = showGridFlag;
    });
    document.getElementById("wireframe").addEventListener("change", (e) => {
      wireframeFlag = e.target.checked;
      updateMaterials();
    });
    document.getElementById("transparent").addEventListener("change", (e) => {
      transparentFlag = e.target.checked;
      updateMaterials();
    });
    document.getElementById("bbox").addEventListener("change", (e) => {
      bboxFlag = e.target.checked;
      updateBBox(bboxFlag);
    });
    document.getElementById("homePose").addEventListener("change", (e) => {
      homePoseFlag = e.target.checked;
      if (homePoseFlag) resetHome();
      else { toggleArrows(showAxesFlag); applyFk(); }
    });
    window.addEventListener("resize", onResize);
  } catch (e) {
    showError(e);
  }
}
boot();
