// Open Duck · SolidWorks URDF 可视化编辑器
// 功能：加载 open_duck.urdf，3D 实时显示；选中关节可调整
//   · 关节 origin 位置 (xyz) 与坐标系 (rpy)   —— 移动/旋转子连杆及以下
//   · 关节 axis（旋转轴）                     —— 改变转动方向
//   · 关节限位 lower/upper                    —— 仿真行程
//   · link 质量 / 惯量                        —— 仿真质量属性
//   · 左右腿对称诊断                          —— 辅助检查
// 支持导出并保存为 open_duck_fixed.urdf（不覆盖原文件）。

const R2D = 180 / Math.PI;
const D2R = Math.PI / 180;

const CONFIG = {
  urdfUrl: "../models/open_duck_urdf/open_duck/urdf/open_duck.urdf",
  meshBase: "../models/open_duck_urdf/open_duck/meshes/",
  packagePrefix: "package://open_duck/meshes/",
  savePath: "models/open_duck_urdf/open_duck/urdf/open_duck_fixed.urdf",
  label: "open_duck.urdf (SolidWorks)",
};

let THREE, OrbitControls, STLLoader;
let renderer, scene, camera, controls;
const canvas = document.getElementById("gl");
const errEl = document.getElementById("err");
const hudEl = document.getElementById("hud");

// ---------- 可编辑数据模型 ----------
// links[name] = { name, mesh, inertial:{xyz,rpy,mass,ixx..izz}, visualRgba }
// joints[i] = { name, type, parent, child, xyz:[..], rpy:[..], axis:[..], lo, hi, effort, vel }
let model = { name: "", links: {}, joints: [] };
let robotGroup = null;
const linkGroups = new Map();   // name -> {group, meshes:[]}
let jointRuntime = [];          // 运行时关节节点（含 childGroup, arrow, axisNorm）
let selectedJoint = null;       // 当前选中关节名
let selectedLink = null;        // 惯量面板选中 link
const jointState = new Map();

let showAxesFlag = true, showGridFlag = true, wireframeFlag = false, transparentFlag = false;
let worldUp = "z";

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
function fail(msg) { errEl.style.display = "flex"; errEl.textContent = "⚠ 加载失败：" + msg; }
function showError(e) { fail((e && e.message) ? e.message : String(e)); console.error(e); }

// ---------- three ----------
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
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.minDistance = 0.1; controls.maxDistance = 12;
  controls.target.set(0, 0, 0.2);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x46505c, 1.05));
  const key = new THREE.DirectionalLight(0xffffff, 1.3); key.position.set(0.6, -1.0, 1.3); scene.add(key);
  const rim = new THREE.DirectionalLight(0xbcd2e8, 0.5); rim.position.set(-0.8, 0.9, -0.5); scene.add(rim);

  const grid = new THREE.GridHelper(1.2, 24, 0x4a5a68, 0x3c4a56);
  grid.rotation.x = Math.PI / 2;
  scene.add(grid);
}

// ---------- URDF 解析为可编辑模型 ----------
async function parseURDF(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
  const text = await res.text();
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.querySelector("parsererror")) throw new Error("URDF XML 解析错误");
  const robot = doc.querySelector("robot");
  const links = {}, joints = [];

  for (const l of robot.querySelectorAll("link")) {
    const name = l.getAttribute("name");
    const meshEl = l.querySelector("visual > geometry > mesh");
    const visColor = l.querySelector("visual > material > color");
    const iner = l.querySelector("inertial");
    let inertial = null;
    if (iner) {
      const io = iner.querySelector("origin");
      const ii = iner.querySelector("inertia");
      inertial = {
        xyz: io ? vec3(io.getAttribute("xyz") || "0 0 0").toArray() : [0, 0, 0],
        rpy: io ? (io.getAttribute("rpy") || "0 0 0").trim().split(/\s+/).map(parseFloat) : [0, 0, 0],
        mass: iner.querySelector("mass") ? num(iner.querySelector("mass"), "value", 0) : 0,
        ixx: ii ? num(ii, "ixx", 0) : 0, ixy: ii ? num(ii, "ixy", 0) : 0, ixz: ii ? num(ii, "ixz", 0) : 0,
        iyy: ii ? num(ii, "iyy", 0) : 0, iyz: ii ? num(ii, "iyz", 0) : 0, izz: ii ? num(ii, "izz", 0) : 0,
      };
    }
    links[name] = {
      name, mesh: meshEl ? meshEl.getAttribute("filename") : null,
      rgba: visColor ? (visColor.getAttribute("rgba") || "1 1 1 1") : "0.7 0.7 0.7 1",
      inertial,
    };
  }

  for (const j of robot.querySelectorAll("joint")) {
    const origin = j.querySelector("origin");
    const axisEl = j.querySelector("axis");
    const limit = j.querySelector("limit");
    joints.push({
      name: j.getAttribute("name"),
      type: j.getAttribute("type"),
      parent: j.querySelector("parent").getAttribute("link"),
      child: j.querySelector("child").getAttribute("link"),
      xyz: origin ? vec3(origin.getAttribute("xyz") || "0 0 0").toArray() : [0, 0, 0],
      rpy: origin ? (origin.getAttribute("rpy") || "0 0 0").trim().split(/\s+/).map(parseFloat) : [0, 0, 0],
      axis: axisEl ? vec3(axisEl.getAttribute("xyz") || "0 0 1").toArray() : [0, 0, 1],
      lo: limit ? num(limit, "lower", -1e9) : -1e9,
      hi: limit ? num(limit, "upper", 1e9) : 1e9,
      effort: limit ? num(limit, "effort", 0) : 0,
      vel: limit ? num(limit, "velocity", 0) : 0,
    });
  }
  return { name: robot.getAttribute("name"), links, joints };
}

// ---------- STL ----------
async function loadMesh(filename) {
  let url;
  if (filename.startsWith(CONFIG.packagePrefix)) url = CONFIG.meshBase + filename.slice(CONFIG.packagePrefix.length);
  else if (filename.startsWith("package://")) url = CONFIG.meshBase + filename.split("/").pop();
  else url = filename;
  return await new STLLoader().loadAsync(url);
}

// ---------- 装配（用可编辑模型，随时可重装配） ----------
async function buildRobot() {
  if (robotGroup) { scene.remove(robotGroup); robotGroup = null; }
  robotGroup = new THREE.Group();
  scene.add(robotGroup);
  linkGroups.clear();
  jointRuntime = [];

  for (const name of Object.keys(model.links)) {
    const g = new THREE.Group();
    robotGroup.add(g);
    linkGroups.set(name, { group: g, meshes: [] });
  }

  // 网格
  const tasks = [];
  for (const [name, link] of Object.entries(model.links)) {
    if (!link.mesh) continue;
    tasks.push(loadMesh(link.mesh).then(geo => {
      const rgba = link.rgba.split(/\s+/).map(parseFloat);
      const mat = new THREE.MeshStandardMaterial({
        color: new THREE.Color(rgba[0], rgba[1], rgba[2]),
        metalness: 0.25, roughness: 0.55, wireframe: false,
      });
      const mesh = new THREE.Mesh(geo, mat);
      linkGroups.get(name).meshes.push(mesh);
      linkGroups.get(name).group.add(mesh);
    }).catch(e => console.warn(`${name}: ${e.message}`)));
  }
  await Promise.all(tasks);

  // 关节层级
  const isChild = new Set(model.joints.map(j => j.child));
  const roots = Object.keys(model.links).filter(n => !isChild.has(n));
  if (!roots.length) roots.push(model.joints[0].parent);

  function attach(parentName, visited) {
    for (const j of model.joints) {
      if (j.parent !== parentName || visited.has(j.child)) continue;
      visited.add(j.child);
      const childGroup = linkGroups.get(j.child).group;
      const jnode = new THREE.Group();
      jnode.position.fromArray(j.xyz);
      jnode.quaternion.copy(quatFromRPY(j.rpy));
      const arrow = makeAxisArrow();
      jnode.add(arrow);
      robotGroup.remove(childGroup);
      jnode.add(childGroup);
      linkGroups.get(parentName).group.add(jnode);
      jointRuntime.push({ ...j, node: jnode, arrow, childGroup, axisNorm: new THREE.Vector3().fromArray(j.axis).normalize() });
      jointState.set(j.name, 0);
      attach(j.child, visited);
    }
  }
  const visited = new Set(roots);
  for (const r of roots) attach(r, visited);
  applyFk();
  updateAllMaterials();
}

function makeAxisArrow() {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.SphereGeometry(0.008, 12, 12), new THREE.MeshBasicMaterial({ color: 0xffffff })));
  const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.004, 0.004, 0.09, 8), new THREE.MeshBasicMaterial({ color: 0x2fe0b0 }));
  shaft.position.z = 0.045;
  const tip = new THREE.Mesh(new THREE.ConeGeometry(0.012, 0.03, 8), new THREE.MeshBasicMaterial({ color: 0x2fe0b0 }));
  tip.position.z = 0.11;
  const ax = new THREE.Group(); ax.add(shaft); ax.add(tip); g.add(ax);
  const col = [0xff5a5a, 0x57c98f, 0x4da3ff];
  [[1,0,0],[0,1,0],[0,0,1]].forEach((b, i) => {
    g.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(...b).multiplyScalar(0.05)]),
      new THREE.LineBasicMaterial({ color: col[i] })
    ));
  });
  g.visible = false;
  return g;
}

// ---------- FK 更新 ----------
function applyFk() {
  for (const jr of jointRuntime) {
    // 同步 jnode 位姿（可能被编辑）
    jr.node.position.fromArray(jr.xyz);
    jr.node.quaternion.copy(quatFromRPY(jr.rpy));
    // 子组绕轴转动
    const q = jointState.get(jr.name) || 0;
    jr.childGroup.quaternion.copy(new THREE.Quaternion().setFromAxisAngle(jr.axisNorm, q));
    // 轴箭头对齐
    const a = new THREE.Vector3().fromArray(jr.axis).normalize();
    if (a.lengthSq() > 1e-12) jr.arrow.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), a));
    jr.arrow.visible = showAxesFlag;
  }
  // 高亮选中
  for (const [name, lg] of linkGroups) {
    const isActive = (selectedJoint && model.joints.find(j => j.name === selectedJoint)?.child === name) || name === selectedLink;
    for (const m of lg.meshes) m.material.emissive = isActive ? new THREE.Color(0x1a3550) : new THREE.Color(0x000000);
  }
}

function updateAllMaterials() {
  for (const [name, lg] of linkGroups) {
    for (const m of lg.meshes) {
      m.material.wireframe = wireframeFlag;
      m.material.transparent = transparentFlag;
      m.material.opacity = transparentFlag ? 0.45 : 1.0;
      m.material.depthWrite = !transparentFlag;
    }
  }
}

// ---------- 面板：关节参数 ----------
function buildJointPanel() {
  const el = document.getElementById("tab-joints");
  const groups = [
    { title: "🦵 左腿", names: ["left_hip_yaw","left_hip_roll","left_hip_pitch","left_knee","left_ankle"] },
    { title: "🦵 右腿", names: ["right_hip_yaw","right_hip_roll","right_hip_pitch","right_knee","right_ankle"] },
    { title: "🐤 头部", names: ["neck_pitch","head_pitch","head_yaw","head_roll"] },
  ];
  let html = "";
  for (const g of groups) {
    let inner = "";
    for (const name of g.names) {
      const j = model.joints.find(x => x.name === name);
      if (!j) continue;
      const sel = name === selectedJoint;
      inner += `<div class="jt ${sel ? "sel" : ""}" data-j="${name}">
        <div class="jt-head"><span class="jt-name ${sel ? "sel" : ""}" data-name="${name}">${name}</span>
          <span class="jt-axis">axis ${j.axis.map(v => v.toFixed(2)).join(",")}</span></div>
      </div>`;
    }
    html += `<details open><summary>${g.title}</summary>${inner}</details>`;
  }
  el.innerHTML = html;

  // 选中关节
  el.querySelectorAll(".jt-name").forEach(n => {
    n.addEventListener("click", () => {
      selectedJoint = selectedJoint === n.dataset.name ? null : n.dataset.name;
      renderJointDetail();
      buildJointPanel();
      applyFk();
    });
  });
}

// ---------- 选中关节详情（编辑区） ----------
function renderJointDetail() {
  const el = document.getElementById("tab-joints");
  // 附加编辑区
  let detail = "";
  if (selectedJoint) {
    const j = model.joints.find(x => x.name === selectedJoint);
    if (j) {
      detail = `<div class="section-title">✏️ ${j.name}  (${j.parent} → ${j.child})</div>
      <div class="hint">位置 = 关节原点在该连杆坐标系中的坐标；坐标系 = 关节局部系相对父系的三轴旋转(度)；轴 = 旋转轴向量。拖动下方输入框即可实时预览。</div>
      <div class="row3">
        <div class="field"><label>X</label><input type="number" step="0.001" data-f="xyz0" value="${j.xyz[0].toFixed(6)}"><span class="unit">m</span></div>
        <div class="field"><label>Y</label><input type="number" step="0.001" data-f="xyz1" value="${j.xyz[1].toFixed(6)}"><span class="unit">m</span></div>
        <div class="field"><label>Z</label><input type="number" step="0.001" data-f="xyz2" value="${j.xyz[2].toFixed(6)}"><span class="unit">m</span></div>
      </div>
      <div class="field" style="margin-top:2px"><label style="width:52px">位置</label><span style="font-size:10px;color:var(--dim)">(joint origin xyz)</span></div>
      <div class="row3">
        <div class="field"><label>Rx</label><input type="number" step="1" data-f="rpy0" value="${(j.rpy[0]*R2D).toFixed(2)}"><span class="unit">°</span></div>
        <div class="field"><label>Ry</label><input type="number" step="1" data-f="rpy1" value="${(j.rpy[1]*R2D).toFixed(2)}"><span class="unit">°</span></div>
        <div class="field"><label>Rz</label><input type="number" step="1" data-f="rpy2" value="${(j.rpy[2]*R2D).toFixed(2)}"><span class="unit">°</span></div>
      </div>
      <div class="field" style="margin-top:2px"><label style="width:52px">坐标系</label><span style="font-size:10px;color:var(--dim)">(roll/pitch/yaw，固定轴 XYZ)</span></div>
      <div class="row3">
        <div class="field"><label>Ax</label><input type="number" step="0.01" data-f="axis0" value="${j.axis[0].toFixed(6)}"></div>
        <div class="field"><label>Ay</label><input type="number" step="0.01" data-f="axis1" value="${j.axis[1].toFixed(6)}"></div>
        <div class="field"><label>Az</label><input type="number" step="0.01" data-f="axis2" value="${j.axis[2].toFixed(6)}"></div>
      </div>
      <div class="field" style="margin-top:2px"><label style="width:52px">轴</label><span style="font-size:10px;color:var(--dim)">(rotation axis，可不必归一)</span></div>
      <div class="row3">
        <div class="field"><label>Lo</label><input type="number" step="1" data-f="lo" value="${(j.lo*R2D).toFixed(2)}"><span class="unit">°</span></div>
        <div class="field"><label>Hi</label><input type="number" step="1" data-f="hi" value="${(j.hi*R2D).toFixed(2)}"><span class="unit">°</span></div>
        <div class="field"><label>Q</label><input type="number" step="1" data-f="q" value="${((jointState.get(j.name)||0)*R2D).toFixed(2)}"><span class="unit">°</span></div>
      </div>
      <div class="field" style="margin-top:2px"><label style="width:52px">限位/当前</label><span style="font-size:10px;color:var(--dim)">lower / upper / 当前转角</span></div>
      <div style="display:flex;gap:6px;margin-top:6px">
        <button class="btn" id="btnCenterHere">此连杆移到世界原点</button>
        <button class="btn" id="btnResetJoint">重置该关节为原值</button>
      </div>`;
    }
  }
  const old = el.querySelector(".detail-edit");
  if (old) old.remove();
  if (detail) {
    const div = document.createElement("div");
    div.className = "detail-edit";
    div.innerHTML = detail;
    el.appendChild(div);
    bindJointEdits(div);
  }
}

function bindJointEdits(div) {
  const j = model.joints.find(x => x.name === selectedJoint);
  if (!j) return;
  div.querySelectorAll("input[data-f]").forEach(inp => {
    inp.addEventListener("input", () => {
      const f = inp.dataset.f, v = parseFloat(inp.value) || 0;
      if (f === "xyz0") j.xyz[0] = v;
      else if (f === "xyz1") j.xyz[1] = v;
      else if (f === "xyz2") j.xyz[2] = v;
      else if (f === "rpy0") j.rpy[0] = v * D2R;
      else if (f === "rpy1") j.rpy[1] = v * D2R;
      else if (f === "rpy2") j.rpy[2] = v * D2R;
      else if (f === "axis0") j.axis[0] = v;
      else if (f === "axis1") j.axis[1] = v;
      else if (f === "axis2") j.axis[2] = v;
      else if (f === "lo") j.lo = v * D2R;
      else if (f === "hi") j.hi = v * D2R;
      else if (f === "q") jointState.set(j.name, v * D2R);
      // 同步 runtime
      const jr = jointRuntime.find(x => x.name === j.name);
      if (jr) { jr.xyz = j.xyz; jr.rpy = j.rpy; jr.axis = j.axis; jr.axisNorm = new THREE.Vector3().fromArray(j.axis).normalize(); }
      applyFk();
    });
  });
  const centerBtn = div.querySelector("#btnCenterHere");
  if (centerBtn) centerBtn.addEventListener("click", () => {
    // 把该关节的父世界位姿取反应用到关节坐标（使 child 原点落在世界原点）
    // 简化：把 j.xyz 设为 child 世界位姿的负值不可行（依赖父链）。
    // 这里提供一个直观操作：将该关节 origin 的 xyz 全部清零（不改变旋转）。
    j.xyz = [0, 0, 0];
    const jr = jointRuntime.find(x => x.name === j.name);
    if (jr) { jr.xyz = j.xyz; }
    renderJointDetail(); buildJointPanel(); applyFk();
  });
  const resetBtn = div.querySelector("#btnResetJoint");
  if (resetBtn) resetBtn.addEventListener("click", () => {
    // 恢复默认值（重新解析）
    loadModel().then(() => {
      selectedJoint = j.name;
      renderJointDetail(); buildJointPanel(); applyFk();
    });
  });
}

// ---------- 面板：质量/惯量 ----------
function buildInertiaPanel() {
  const el = document.getElementById("tab-inertia");
  let html = `<div class="hint">选择 link 调整质量与惯量（仿真关键参数）。单位 kg / kg·m²。</div>`;
  for (const [name, link] of Object.entries(model.links)) {
    const iner = link.inertial || { xyz:[0,0,0], rpy:[0,0,0], mass:0, ixx:0, iyy:0, izz:0, ixy:0, ixz:0, iyz:0 };
    const sel = name === selectedLink;
    html += `<details ${sel ? "open" : ""}><summary>${name} <span class="link-mass">mass=${iner.mass.toFixed(4)}kg</span></summary>
      <div class="jt" data-link="${name}">
        <div class="row3">
          <div class="field"><label>M</label><input type="number" step="0.0001" data-l="mass" value="${iner.mass.toFixed(6)}"><span class="unit">kg</span></div>
          <div class="field"><label>Ixx</label><input type="number" step="0.000001" data-l="ixx" value="${iner.ixx.toExponential(6)}"></div>
          <div class="field"><label>Iyy</label><input type="number" step="0.000001" data-l="iyy" value="${iner.iyy.toExponential(6)}"></div>
        </div>
        <div class="row3">
          <div class="field"><label>Izz</label><input type="number" step="0.000001" data-l="izz" value="${iner.izz.toExponential(6)}"></div>
          <div class="field"><label>Ixy</label><input type="number" step="0.000001" data-l="ixy" value="${iner.ixy.toExponential(6)}"></div>
          <div class="field"><label>Ixz</label><input type="number" step="0.000001" data-l="ixz" value="${iner.ixz.toExponential(6)}"></div>
        </div>
        <div class="row3">
          <div class="field"><label>Iyz</label><input type="number" step="0.000001" data-l="iyz" value="${iner.iyz.toExponential(6)}"></div>
          <div class="field" style="visibility:hidden"><label>_</label><input type="number"></div>
          <div class="field" style="visibility:hidden"><label>_</label><input type="number"></div>
        </div>
      </div>
    </details>`;
  }
  el.innerHTML = html;

  el.querySelectorAll("input[data-l]").forEach(inp => {
    inp.addEventListener("input", () => {
      const linkName = inp.closest(".jt").dataset.link;
      const link = model.links[linkName];
      if (!link.inertial) link.inertial = { xyz:[0,0,0], rpy:[0,0,0], mass:0, ixx:0, iyy:0, izz:0, ixy:0, ixz:0, iyz:0 };
      const k = inp.dataset.l, v = parseFloat(inp.value) || 0;
      link.inertial[k] = v;
      selectedLink = linkName;
      buildInertiaPanel();
    });
  });
}

// ---------- 面板：左右对称诊断 ----------
function buildMirrorPanel() {
  const el = document.getElementById("tab-mirror");
  // 计算当前模型 HOME 位形左右对应关节的世界位置与轴
  const world = computeWorld();
  const pairs = [
    ["left_hip_yaw","right_hip_yaw"],["left_hip_roll","right_hip_roll"],
    ["left_hip_pitch","right_hip_pitch"],["left_knee","right_knee"],["left_ankle","right_ankle"],
  ];
  let rows = "";
  for (const [l, r] of pairs) {
    const wl = world[l], wr = world[r];
    if (!wl || !wr) continue;
    const d = wl.pos.map((v,i)=>v-wr.pos[i]);
    const dist = Math.hypot(...d);
    const axisMatch = 1 - Math.abs(wl.axis[0]*wr.axis[0] + wl.axis[1]*wr.axis[1] + wl.axis[2]*wr.axis[2]);
    rows += `<tr><td>${l}/${r}</td><td>${dist.toFixed(4)} m</td><td class="${axisMatch<0.1?"ok":"bad"}">${(axisMatch).toFixed(3)}</td></tr>`;
  }
  el.innerHTML = `<div class="hint">左右镜像理想值：位置差 = 0；轴夹角(90° 化) = 0。<br>仅诊断提示，不自动修改。</div>
    <table class="mirror-table"><tr><th>关节对</th><th>位置差(m)</th><th>轴夹角残差</th></tr>${rows}</table>
    <div class="section-title">当前模型整体朝向</div>
    <div class="hint" id="orientInfo">计算中…</div>`;
  // 整体朝向
  const pts = [];
  for (const [name, lg] of linkGroups) {
    const v = new THREE.Vector3(); lg.group.getWorldPosition(v); pts.push([v.x, v.y, v.z]);
  }
  const ex = Math.max(...pts.map(p=>p[0])) - Math.min(...pts.map(p=>p[0]));
  const ey = Math.max(...pts.map(p=>p[1])) - Math.min(...pts.map(p=>p[1]));
  const ez = Math.max(...pts.map(p=>p[2])) - Math.min(...pts.map(p=>p[2]));
  const vert = [["X",ex],["Y",ey],["Z",ez]].sort((a,b)=>b[1]-a[1])[0];
  document.getElementById("orientInfo").textContent =
    `link 原点范围 X=${ex.toFixed(3)} Y=${ey.toFixed(3)} Z=${ez.toFixed(3)} m → 当前主要沿 ${vert[0]} 轴站立。${vert[0]==="Z" ? "✓ 已是 Z-up" : "（非 Z-up；若需转 Z-up 请用“世界轴”观察或手动调整根关节）"}`;
}

// 计算当前模型所有关节世界位姿/轴（HOME 即当前 jointState）
function computeWorld() {
  const world = {};
  const childToJoint = new Map(model.joints.map(j => [j.child, j]));
  function rpyMat(rpy) {
    const [r,p,y]=rpy; const cr=Math.cos(r),sr=Math.sin(r),cp=Math.cos(p),sp=Math.sin(p),cy=Math.cos(y),sy=Math.sin(y);
    return [[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],[sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],[-sp, cp*sr, cp*cr]];
  }
  function mmul(A,B){const C=[[0,0,0],[0,0,0],[0,0,0]];for(let i=0;i<3;i++)for(let j=0;j<3;j++)for(let k=0;k<3;k++)C[i][j]+=A[i][k]*B[k][j];return C;}
  function mul(R,v){return [R[0][0]*v[0]+R[0][1]*v[1]+R[0][2]*v[2],R[1][0]*v[0]+R[1][1]*v[1]+R[1][2]*v[2],R[2][0]*v[0]+R[2][1]*v[1]+R[2][2]*v[2]];}
  function add(a,b){return [a[0]+b[0],a[1]+b[1],a[2]+b[2]];}
  function norm(v){const l=Math.hypot(...v);return l?[v[0]/l,v[1]/l,v[2]/l]:[0,0,0];}
  function qmat(q){ // quaternion [w,x,y,z] -> matrix
    const {w,x,y,z}=q; return [[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]];
  }
  const roots = Object.keys(model.links).filter(n => !model.joints.some(j=>j.child===n));
  const childToJointName = new Map(model.joints.map(j=>[j.child,j.name]));
  function walk(name, pos, rot) {
    const j = childToJoint.get(name);
    let myPos, myRot;
    if (j) {
      // joint origin 旋转 (rpy) 已在编辑器里作为关节局部系；世界位姿 = parent * R(rpy)
      const T = rpyMat(j.rpy);
      // 当前关节转角
      const q = jointState.get(j.name) || 0;
      const a = norm(j.axis);
      // Rodrigues
      const [ax,ay,az]=a; const c=Math.cos(q),s=Math.sin(q),t=1-c;
      const Rq=[[t*ax*ax+c,t*ax*ay-az*s,t*ax*az+ay*s],[t*ax*ay+az*s,t*ay*ay+c,t*ay*az-ax*s],[t*ax*az-ay*s,t*ay*az+ax*s,t*az*az+c]];
      myRot = mmul(mmul(rot, T), Rq);
      myPos = add(pos, mul(rot, j.xyz));
      world[j.name] = { pos: myPos, axis: norm(mul(myRot, j.axis)), rot: myRot };
    } else { myPos = pos; myRot = rot; }
    for (const cj of model.joints.filter(x=>x.parent===name)) walk(cj.child, myPos, myRot);
  }
  for (const r of roots) walk(r, [0,0,0], [[1,0,0],[0,1,0],[0,0,1]]);
  return world;
}

// ---------- 导出 URDF ----------
function serializeURDF() {
  let xml = `<?xml version="1.0" encoding="utf-8"?>\n`;
  xml += `<robot name="${model.name}">\n`;
  for (const [name, link] of Object.entries(model.links)) {
    xml += `  <link name="${name}">\n`;
    const iner = link.inertial;
    if (iner) {
      xml += `    <inertial>\n`;
      xml += `      <origin xyz="${iner.xyz.map(v=>v.toFixed(9)).join(" ")}" rpy="${iner.rpy.map(v=>v.toFixed(9)).join(" ")}"/>\n`;
      xml += `      <mass value="${iner.mass.toFixed(9)}"/>\n`;
      xml += `      <inertia ixx="${iner.ixx.toExponential(6)}" ixy="${iner.ixy.toExponential(6)}" ixz="${iner.ixz.toExponential(6)}" iyy="${iner.iyy.toExponential(6)}" iyz="${iner.iyz.toExponential(6)}" izz="${iner.izz.toExponential(6)}"/>\n`;
      xml += `    </inertial>\n`;
    }
    if (link.mesh) {
      const rgba = link.rgba.split(/\s+/).map(parseFloat);
      xml += `    <visual>\n      <origin xyz="0 0 0" rpy="0 0 0"/>\n      <geometry><mesh filename="${link.mesh}"/></geometry>\n      <material name=""><color rgba="${rgba.map(v=>v.toFixed(5)).join(" ")}"/></material>\n    </visual>\n`;
      xml += `    <collision>\n      <origin xyz="0 0 0" rpy="0 0 0"/>\n      <geometry><mesh filename="${link.mesh}"/></geometry>\n    </collision>\n`;
    }
    xml += `  </link>\n`;
  }
  for (const j of model.joints) {
    xml += `  <joint name="${j.name}" type="${j.type}">\n`;
    xml += `    <origin xyz="${j.xyz.map(v=>v.toFixed(9)).join(" ")}" rpy="${j.rpy.map(v=>v.toFixed(9)).join(" ")}"/>\n`;
    xml += `    <parent link="${j.parent}"/>\n`;
    xml += `    <child link="${j.child}"/>\n`;
    xml += `    <axis xyz="${j.axis.map(v=>v.toFixed(9)).join(" ")}"/>\n`;
    const lo = j.lo === -1e9 ? "-3.141592654" : j.lo.toFixed(9);
    const hi = j.hi === 1e9 ? "3.141592654" : j.hi.toFixed(9);
    xml += `    <limit lower="${lo}" upper="${hi}" effort="${j.effort.toFixed(9)}" velocity="${j.vel.toFixed(9)}"/>\n`;
    xml += `  </joint>\n`;
  }
  xml += `</robot>\n`;
  return xml;
}

async function saveURDF() {
  const msgEl = document.getElementById("saveMsg");
  try {
    const content = serializeURDF();
    const res = await fetch("/api/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: CONFIG.savePath, content }),
    });
    const data = await res.json();
    if (data.ok) {
      msgEl.textContent = `✓ 已保存 ${data.path} (${data.bytes} bytes)`;
      msgEl.className = "";
    } else { msgEl.textContent = "✗ " + JSON.stringify(data); msgEl.className = "err"; }
  } catch (e) {
    msgEl.textContent = "✗ " + e.message; msgEl.className = "err";
  }
}

// ---------- 加载模型 ----------
async function loadModel() {
  const m = await parseURDF(CONFIG.urdfUrl);
  model = m;
  await buildRobot();
  buildJointPanel();
  renderJointDetail();
  buildInertiaPanel();
  buildMirrorPanel();
  refreshHud();
}

function refreshHud() {
  hudEl.innerHTML = `
    <div><b>${CONFIG.label}</b></div>
    <div>links <b>${Object.keys(model.links).length}</b> · joints <b>${model.joints.length}</b></div>
    <div style="font-size:11px;color:var(--dim)">拖动旋转 · 滚轮缩放 · 右键平移 · 点关节名选中编辑</div>`;
}

// ---------- 视图 ----------
function setWorldUp(axis) {
  worldUp = axis;
  document.getElementById("btnUp").textContent = `世界轴: ${axis === "z" ? "Z↑" : "Y↑"}`;
  camera.up.set(0, 0, 1);
  if (axis === "y") camera.up.set(0, 1, 0);
  controls.update(); fitView();
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
  controls.target.copy(center); controls.update();
}
function resetHome() {
  for (const j of model.joints) jointState.set(j.name, 0);
  buildJointPanel(); renderJointDetail(); applyFk();
}
function onResize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
}
function animate() { requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); }

// ---------- boot ----------
async function boot() {
  try {
    THREE = await import("three");
    OrbitControls = (await import("three/addons/controls/OrbitControls.js")).OrbitControls;
    STLLoader = (await import("three/addons/loaders/STLLoader.js")).STLLoader;
    initThree();
    onResize();
    await loadModel();
    applyFk();
    fitView();
    animate();

    document.getElementById("hdrInfo").textContent =
      `${Object.keys(model.links).length} links · ${model.joints.length} joints · 编辑器`;

    // tab 切换
    document.querySelectorAll(".tab").forEach(t => {
      t.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
        t.classList.add("active");
        const tab = t.dataset.tab;
        ["joints","inertia","mirror","help"].forEach(n => {
          document.getElementById("tab-" + n).style.display = n === tab ? "" : "none";
        });
      });
    });
    document.getElementById("tab-help").innerHTML = `
      <div class="section-title">📖 使用说明</div>
      <div class="hint">
        1. <b>关节参数</b>：点击左侧关节名 → 右侧编辑 位置(xyz)/坐标系(rpy°)/轴/限位/当前角，实时预览。<br>
        2. <b>质量/惯量</b>：逐 link 调整 mass 与惯量张量。<br>
        3. <b>左右对称诊断</b>：显示左右腿关节世界位置差与轴夹角残差（0=理想），辅助判断。<br>
        4. 编辑完成后点 <b>💾 导出并保存 URDF</b> → 写入 <code>open_duck_fixed.urdf</code>（不覆盖原文件）。<br>
        5. 若需把模型整体转 Z-up，可手动把根关节(第一个关节)的坐标系 rpy 改为对应旋转；或用“世界轴”切换查看。<br>
        <b>提示</b>：数值可输入，也可点上下箭头微调；改动即时生效。
      </div>`;

    document.getElementById("btnSave").addEventListener("click", () => {
      // 在 header 后追加一条提示
      let m = document.getElementById("saveMsg");
      if (!m) { m = document.createElement("div"); m.id = "saveMsg"; document.querySelector("header").appendChild(m); }
      saveURDF();
    });
    document.getElementById("btnHome").addEventListener("click", resetHome);
    document.getElementById("btnFit").addEventListener("click", fitView);
    document.getElementById("btnUp").addEventListener("click", () => setWorldUp(worldUp === "z" ? "y" : "z"));
    document.getElementById("showAxes").addEventListener("change", e => { showAxesFlag = e.target.checked; applyFk(); });
    document.getElementById("showGrid").addEventListener("change", e => {
      const grid = scene.children.find(c => c.isGridHelper); if (grid) grid.visible = e.target.checked;
    });
    document.getElementById("wireframe").addEventListener("change", e => { wireframeFlag = e.target.checked; updateAllMaterials(); });
    document.getElementById("transparent").addEventListener("change", e => { transparentFlag = e.target.checked; updateAllMaterials(); });
    window.addEventListener("resize", onResize);
  } catch (e) { showError(e); }
}
boot();
