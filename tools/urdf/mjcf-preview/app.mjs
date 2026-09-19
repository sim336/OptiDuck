// Open Duck / 通用 MJCF 静态 3D 预览 + 关节限位滑杆
// FK 约定（已用 MuJoCo 真值校验）：q_body = q_parent * quat(body) * Q(axis_local, q)

const CONFIG = { xml: "./models/open_duck_walk.xml", label: "open_duck_walk.xml" };
const R2D = 180 / Math.PI;
const D2R = Math.PI / 180;

let THREE, OrbitControls, STLLoader;
let renderer, scene, camera, controls;
const canvas = document.getElementById("gl");
const panelEl = document.getElementById("panel");
const hudEl = document.getElementById("hud");
const errEl = document.getElementById("err");

// ---- model state
let robotGroup = null;
let floorGroup = null;
const bodyNodes = [];        // {body, group, baseQuat, joint:{name,axis,lo,hi}|null}
const meshes3 = [];          // THREE.Mesh 列表（不含地板）
let jointOrder = [];         // {name, lo, hi, axis, bodyName} 按 XML 顺序
const jointState = new Map();// name -> 当前弧度
let activeJointName = null;
let showAxesFlag = false;

function fail(msg) {
  errEl.style.display = "flex";
  errEl.textContent = "❌ 加载失败：" + msg;
}

function showError(e) {
  fail((e && e.message) ? e.message : String(e));
}

// ---------- tiny XML helpers ----------
function attr(el, name) { return el.getAttribute(name); }
function vec3(el, name, def) {
  const s = el.getAttribute(name);
  if (!s) return new THREE.Vector3(...def);
  const a = s.trim().split(/\s+/).map(parseFloat);
  return new THREE.Vector3(a[0], a[1], a[2]);
}
// MuJoCo quat 顺序 w x y z
function quatFromWXYZ(el) {
  const s = el.getAttribute("quat") || "1 0 0 0";
  const a = s.trim().split(/\s+/).map(parseFloat);
  return new THREE.Quaternion(a[1], a[2], a[3], a[0]);
}

// ---------- init three ----------
function initThree() {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(canvas.clientWidth, canvas.clientHeight);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;

  scene = new THREE.Scene();
  // 背景/雾色贴近 MuJoCo 原生 viewer 的 haze 观感（灰蓝），远处地板融入背景 = “无限地面”
  scene.background = new THREE.Color(0x334150);
  scene.fog = new THREE.Fog(0x334150, 25, 130);

  camera = new THREE.PerspectiveCamera(55, canvas.clientWidth / canvas.clientHeight, 0.01, 200);
  camera.up.set(0, 0, 1); // MuJoCo 世界坐标 z-up
  camera.position.set(0.8, -1.2, 0.5);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 0.15;
  controls.maxDistance = 8;
  controls.target.set(0, 0, 0.15);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x46505c, 1.05));
  const key = new THREE.DirectionalLight(0xffffff, 1.2);
  key.position.set(0.6, -1.0, 1.2);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xbcd2e8, 0.5);
  rim.position.set(-0.8, 0.9, -0.4);
  scene.add(rim);
}

// 可选参考网格（默认关闭；仿真场景本身无网格，仅调试用）
let gridMesh = null;
function toggleGrid(on) {
  if (!floorGroup) return;
  if (!gridMesh) {
    gridMesh = new THREE.GridHelper(2.0, 10, 0x556677, 0x4a5a68);
    gridMesh.position.z = 0.0005;
    floorGroup.add(gridMesh);
  }
  gridMesh.visible = !!on;
}

function onResize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", onResize);

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

// ---------- model parsing ----------
async function loadModel() {
  const xmlText = await (await fetch(CONFIG.xml)).text();
  const doc = new DOMParser().parseFromString(xmlText, "text/xml");
  if (doc.querySelector("parsererror")) throw new Error("XML 解析失败");

  const mujoco = doc.querySelector("mujoco") || doc.documentElement;
  const compiler = mujoco.querySelector("compiler");
  const meshdir = compiler ? (compiler.getAttribute("meshdir") || "") : "";
  const assetDir = "./models/" + (meshdir ? meshdir + "/" : "");

  // mesh name -> 文件
  const meshFile = new Map();
  mujoco.querySelectorAll("mesh").forEach((me) => {
    if (me.getAttribute("file")) meshFile.set(me.getAttribute("name"), me.getAttribute("file"));
  });
  // material name -> THREE.Color
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
      geoCache.set(file, stlLoader.loadAsync(assetDir + file));
    }
    return geoCache.get(file);
  };

  const materialFor = (meshName, geomEl) => {
    // 颜色解析优先级：geom material attr -> <meshName>_material -> 默认
    let col = matColor.get(meshName + "_material") || matColor.get(meshName);
    if (!col) col = { r: 0.72, g: 0.74, b: 0.78, a: 1 };
    const c = new THREE.Color(col.r, col.g, col.b);
    return new THREE.MeshStandardMaterial({
      color: c, roughness: 0.55, metalness: 0.12,
      transparent: col.a < 0.999, opacity: col.a,
    });
  };

  const isVisualGeom = (g) => {
    const cls = g.getAttribute("class") || "";
    if (cls.includes("collision")) return false;
    const grp = g.getAttribute("group");
    if (grp !== null && grp !== "2") return false;
    return true;
  };

  robotGroup = new THREE.Group();
  floorGroup = new THREE.Group();

  // 世界固定 geom（地板等）
  const world = mujoco.querySelector("worldbody");
  const promises = [];

  for (const g of world.children) {
    if (g.tagName !== "geom") continue;
    const type = g.getAttribute("type") || "mesh";
    const geomName = g.getAttribute("name") || "";
    if (type === "plane") {
      // 与 mjlab Flat 场景一致：无限浅灰平面（MuJoCo 默认 geom rgba 0.8）
      // 用大平面 + 远处雾效模拟"无限"，避免深色网格/透明地板与仿真观感不符
      const size = vec3(g, "size", [1, 1, 1]);
      const EXTENT = 150; // 远超视锥，配合 fog 呈现无限平面
      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(EXTENT * 2, EXTENT * 2),
        new THREE.MeshStandardMaterial({ color: 0xcccccc, roughness: 0.95, metalness: 0 })
      );
      plane.rotation.x = -Math.PI / 2;
      plane.position.z = 0; // 地面正好在 z=0（脚底即原点，与仿真一致）
      plane.material.polygonOffset = true;
      plane.material.polygonOffsetFactor = -1;
      floorGroup.add(plane);
    } else if (type === "mesh" && g.getAttribute("mesh")) {
      promises.push((async () => {
        const geo = await geometryFor(g.getAttribute("mesh"));
        const mesh = new THREE.Mesh(geo, materialFor(g.getAttribute("mesh"), g));
        mesh.position.copy(vec3(g, "pos", [0, 0, 0]));
        mesh.quaternion.copy(quatFromWXYZ(g));
        mesh.scale.setScalar(num(g, "scale", 1));
        floorGroup.add(mesh);
      })());
    }
  }
  scene.add(floorGroup);

  // 递归解析 body 树
  const addBody = (el, parentGroup, parentBodyName) => {
    const name = el.getAttribute("name") || ("body_" + bodyNodes.length);
    const pos = vec3(el, "pos", [0, 0, 0]);
    const baseQ = quatFromWXYZ(el);

    const group = new THREE.Group();
    group.position.copy(pos);
    group.quaternion.copy(baseQ);

    // 该 body 上的 joint（取第一个驱动 hinge，记录用于滑杆）
    let jointInfo = null;
    for (const ch of el.children) {
      if (ch.tagName !== "joint") continue;
      const jtype = ch.getAttribute("type") || "hinge";
      const jname = ch.getAttribute("name");
      if (jtype === "hinge" && jname && jname !== "trunk_base_freejoint") {
        const axis = vec3(ch, "axis", [1, 0, 0]).normalize();
        const r = ch.getAttribute("range");
        let lo = -Math.PI, hi = Math.PI;
        if (r) { const a = r.trim().split(/\s+/).map(parseFloat); lo = a[0]; hi = a[1]; }
        jointInfo = { name: jname, axis, lo, hi };
      }
      break; // body 通常只有一个 joint
    }

    bodyNodes.push({ body: name, group, baseQ: baseQ.clone(), joint: jointInfo });
    parentGroup.add(group);

    // geoms
    for (const g of el.children) {
      if (g.tagName !== "geom") continue;
      if (!isVisualGeom(g)) continue;
      if (g.getAttribute("type") && g.getAttribute("type") !== "mesh") continue; // 只看 mesh 可视化
      const meshName = g.getAttribute("mesh");
      if (!meshName) continue;
      promises.push((async () => {
        const geo = await geometryFor(meshName);
        const mesh = new THREE.Mesh(geo, materialFor(meshName, g));
        mesh.position.copy(vec3(g, "pos", [0, 0, 0]));
        mesh.quaternion.copy(quatFromWXYZ(g));
        const sc = g.getAttribute("scale");
        if (sc) mesh.scale.setScalar(parseFloat(sc));
        group.add(mesh);
        meshes3.push(mesh);
      })());
    }
    // 记录 joint 到全局顺序（按 XML 顺序）
    if (jointInfo) {
      jointOrder.push({ name: jointInfo.name, lo: jointInfo.lo, hi: jointInfo.hi,
                        axis: jointInfo.axis.clone(), bodyName: name });
      jointState.set(jointInfo.name, 0);
    }
    // 子 body
    for (const c of el.children) {
      if (c.tagName === "body") addBody(c, group, name);
    }
  };
  for (const c of world.children) {
    if (c.tagName === "body") addBody(c, robotGroup, null);
  }
  scene.add(robotGroup);

  await Promise.all(promises);
  if (meshes3.length === 0) throw new Error("没有加载到任何可见网格");
}

function num(el, name, def) {
  const v = el.getAttribute(name);
  return v === null ? def : parseFloat(v);
}

// ---------- FK ----------
function applyFk() {
  for (const n of bodyNodes) {
    const q = n.group.quaternion;
    q.copy(n.baseQ);
    if (n.joint) {
      const a = jointState.get(n.joint.name) || 0;
      if (a !== 0) {
        q.multiply(new THREE.Quaternion().setFromAxisAngle(n.joint.axis, a));
      }
    }
  }
  robotGroup.updateMatrixWorld(true);
  updateAxisArrow();
}

function jointWorldFrame(name) {
  const n = bodyNodes.find((b) => b.joint && b.joint.name === name);
  if (!n) return null;
  const origin = new THREE.Vector3();
  n.group.getWorldPosition(origin);
  const dir = n.joint.axis.clone();
  const qw = new THREE.Quaternion();
  n.group.getWorldQuaternion(qw);
  dir.applyQuaternion(qw);
  return { origin, dir, name, lo: n.joint.lo, hi: n.joint.hi };
}

let arrowObj = null;
function updateAxisArrow() {
  if (arrowObj) { scene.remove(arrowObj); arrowObj.traverse((o) => o.dispose && o.dispose()); arrowObj = null; }
  if (!showAxesFlag) return;
  const jf = activeJointName && jointWorldFrame(activeJointName);
  if (!jf) return;
  const L = 0.14;
  const c = Math.min(Math.abs(jointState.get(activeJointName) || 0) / (Math.PI / 2), 1) || 0;
  const arrow = new THREE.ArrowHelper(jf.dir, jf.origin, L, c > 0.02 ? new THREE.Color().setHSL(0.55 - c * 0.4, 1, 0.55) : 0xff5566, 0.035, 0.02);
  // 双向显示：负方向淡色
  arrowObj = arrow;
  scene.add(arrowObj);
}

// ---------- UI ----------
function deg(n) { return (Math.round(n * 10) / 10); }

function buildPanel() {
  const groups = new Map(); // label -> [joints]
  for (const j of jointOrder) {
    let label = "其他 Other";
    if (/^left_/.test(j.name)) label = "左腿 Left leg";
    else if (/^right_/.test(j.name)) label = "右腿 Right leg";
    else if (/^(head_|neck_)/.test(j.name)) label = "头颈 Head / Neck";
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(j);
  }
  panelEl.innerHTML = "";
  let first = true;
  for (const [label, joints] of groups) {
    const det = document.createElement("details");
    det.open = first; first = false;
    const sum = document.createElement("summary");
    sum.textContent = `${label}（${joints.length}）`;
    det.appendChild(sum);
    for (const j of joints) {
      const row = document.createElement("div");
      row.className = "jt";
      row.innerHTML = `
        <div class="jt-head">
          <span class="jt-name" title="点击重置该关节到 0°">${j.name}</span>
          <span class="jt-val">0.0°</span>
        </div>
        <input type="range" />
        <div class="jt-limits"><span>min ${deg(j.lo * R2D)}°</span><span>max ${deg(j.hi * R2D)}°</span></div>
        <div class="axis-row">axis [${j.axis.x.toFixed(2)}, ${j.axis.y.toFixed(2)}, ${j.axis.z.toFixed(2)}]</div>`;
      const slider = row.querySelector("input");
      slider.min = deg(j.lo * R2D); slider.max = deg(j.hi * R2D); slider.step = 0.1;
      // 初始 0°；若 0 超出该关节 range，则钳到边界（本模型 0 恒在范围内）
      const v0 = Math.min(parseFloat(slider.max), Math.max(parseFloat(slider.min), 0));
      slider.value = v0;
      jointState.set(j.name, v0 * D2R);
      const valEl = row.querySelector(".jt-val");
      valEl.textContent = v0.toFixed(1) + "°";
      slider.addEventListener("input", () => {
        const v = parseFloat(slider.value);
        valEl.textContent = v.toFixed(1) + "°";
        jointState.set(j.name, v * D2R);
        activeJointName = j.name;
        applyFk();
      });
      valEl.addEventListener("dblclick", () => {
        slider.value = 0; valEl.textContent = "0.0°";
        jointState.set(j.name, 0); applyFk();
      });
      row.querySelector(".jt-name").addEventListener("click", () => {
        slider.value = 0; valEl.textContent = "0.0°";
        jointState.set(j.name, 0); applyFk();
      });
      det.appendChild(row);
    }
    panelEl.appendChild(det);
  }
}

function refreshHud() {
  hudEl.innerHTML = "";
  const t1 = document.createElement("div");
  t1.innerHTML = `<b>模型</b> ${CONFIG.label}`;
  const t2 = document.createElement("div");
  t2.innerHTML = `<b>场景</b> 与仿真一致：浅灰无限地面（z=0），HOME 站立位姿，脚底位于世界原点`;
  const t3 = document.createElement("div");
  t3.innerHTML = `<b>可调关节</b> ${jointOrder.length}（单位：度，钳制在 MJCF range 内）`;
  const t4 = document.createElement("div");
  t4.innerHTML = `<b>HOME</b> 复位 = 全部 0°`;
  hudEl.append(t1, t2, t3, t4);
}

function resetHome() {
  for (const j of jointOrder) jointState.set(j.name, 0);
  panelEl.querySelectorAll("details .jt").forEach((row) => {
    const slider = row.querySelector("input");
    const valEl = row.querySelector(".jt-val");
    slider.value = 0;
    valEl.textContent = "0.0°";
  });
  activeJointName = null;
  applyFk();
}

function fitView() {
  if (!meshes3.length) return;
  robotGroup.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(robotGroup);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const d = Math.max(size.length() * 1.9, 0.5, size.y * 3.0);
  const dir = new THREE.Vector3(0.42, -0.9, 0.5).normalize();
  camera.position.copy(center).addScaledVector(dir, d);
  controls.target.copy(center);
  controls.update();
}

// ---------- boot ----------
async function boot() {
  try {
    THREE = await import("three");
    OrbitControls = (await import("three/addons/controls/OrbitControls.js")).OrbitControls;
    STLLoader = (await import("three/addons/loaders/STLLoader.js")).STLLoader;
    initThree();
    onResize();
    await loadModel();
    buildPanel();
    refreshHud();
    applyFk();
    fitView();
    animate();
    document.getElementById("hdrInfo").textContent =
      `${meshes3.length} 个网格 · ${jointOrder.length} 关节 · 场景与 Mjlab Flat 仿真一致`;

    document.getElementById("btnHome").addEventListener("click", resetHome);
    document.getElementById("btnFit").addEventListener("click", fitView);
    document.getElementById("showAxes").addEventListener("change", (e) => {
      showAxesFlag = e.target.checked;
      updateAxisArrow();
    });
    document.getElementById("showGrid").addEventListener("change", (e) => {
      toggleGrid(e.target.checked);
    });
  } catch (e) {
    showError(e);
    console.error(e);
  }
}
boot();
