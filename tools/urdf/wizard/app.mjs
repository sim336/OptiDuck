// SolidWorks → URDF 可视化生成向导
// 步骤：
//   1. 导入零件：扫描 SolidWorks 导出的 STL 目录，勾选要用的零件
//   2. 设根：选择 base link
//   3. 搭关节树：为每个 link 指定父 link + 关节名/类型（自动生成树，可改名/改类型）
//   4. 关节参数：逐个配置 位置(xyz)/坐标系(rpy)/轴(axis)/限位，实时 3D 预览
//   5. 导出：生成 URDF 文本 → 保存为文件
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

const R2D = 180 / Math.PI;
const D2R = Math.PI / 180;

const CONFIG = {
  defaultMeshDir: "tools/urdf/models/open_duck_urdf/open_duck/meshes",
  savePath: "tools/urdf/models/open_duck_urdf/open_duck/urdf/open_duck_wizard.urdf",
};

// ---------- 状态 ----------
let currentStep = 1;
let links = [];        // [{id, name, meshPath, meshUrl, selected}]
let rootId = null;     // base link id
let joints = [];       // [{id, name, type, parentId, childId, xyz, rpy, axis, lo, hi}]
let selectedJointId = null;

// 渲染
let renderer, scene, camera, controls;
let robotGroup = null;
const THREE_GROUPS = new Map(); // linkId -> Group
const ARROWS = new Map();       // jointId -> {arrow, childGroup, axisNorm}

// ---------- 工具 ----------
const $ = (id) => document.getElementById(id);
function quatFromRPY(rpy) {
  return new THREE.Quaternion().setFromEuler(new THREE.Euler(rpy[0], rpy[1], rpy[2], "XYZ"));
}
function showError(e) {
  const el = $("err"); el.style.display = "flex";
  el.textContent = "⚠ " + ((e && e.message) ? e.message : String(e));
  console.error(e);
}

// ---------- three ----------
function initThree() {
  renderer = new THREE.WebGLRenderer({ canvas: $("gl"), antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize($("gl").clientWidth, $("gl").clientHeight);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x2a3340);

  camera = new THREE.PerspectiveCamera(55, $("gl").clientWidth / $("gl").clientHeight, 0.01, 300);
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
  grid.rotation.x = Math.PI / 2; scene.add(grid);
}

// ---------- Step 1: 导入零件 ----------
async function loadMeshList() {
  const dir = encodeURIComponent(CONFIG.defaultMeshDir);
  const res = await fetch(`/api/list?dir=${dir}`);
  if (!res.ok) throw new Error(`/api/list ${res.status}`);
  const data = await res.json();
  links = data.files.map(f => ({
    id: f.name, name: f.name.replace(/\.stl$/i, ""), meshPath: f.path,
    meshUrl: "../" + f.path.split("/").map(encodeURIComponent).join("/"),
    selected: true,
  }));
  // 预设：识别左右/头/躯干的常见命名，给个建议根
  renderStep1();
}

function renderStep1() {
  const panel = $("panel");
  let html = `<div class="section-title">第 1 步 · 导入 SolidWorks 零件 (STL)</div>
    <div class="hint">正在扫描目录：<b>${CONFIG.defaultMeshDir}</b>（共 ${links.length} 个 STL）。<br>
    这些是你在 SolidWorks 里导出每个零件的 STL（SolidWorks 原生 .sldprt/.sldasm 无法直接读取，需先导出 STL）。勾选要组成机器人的零件。</div>
    <div class="file-grid">`;
  for (const l of links) {
    const kb = (l.meshPath && l.size) ? (l.size / 1024).toFixed(0) + "KB" : "";
    html += `<div class="file-item ${l.selected ? "sel" : ""}" data-id="${l.id}">
      <input type="checkbox" ${l.selected ? "checked" : ""} data-id="${l.id}">
      <span class="nm">${l.name}</span><span class="sz">${kb}</span></div>`;
  }
  html += `</div>
    <div class="nav"><button class="btn" id="btnNext1" ${links.some(l=>l.selected)?"":"disabled"}>下一步：设根 →</button></div>`;
  panel.innerHTML = html;

  panel.querySelectorAll(".file-item").forEach(it => {
    it.addEventListener("click", () => {
      const l = links.find(x => x.id === it.dataset.id);
      l.selected = !l.selected;
      it.classList.toggle("sel", l.selected);
      it.querySelector("input").checked = l.selected;
      $("btnNext1").disabled = !links.some(x => x.selected);
    });
  });
  panel.querySelectorAll(".file-item input").forEach(inp => {
    inp.addEventListener("click", e => e.stopPropagation());
  });
  $("btnNext1").addEventListener("click", () => {
    links = links.filter(l => l.selected);
    goStep(2);
  });
}

// ---------- Step 2: 设根 ----------
function renderStep2() {
  const panel = $("panel");
  let html = `<div class="section-title">第 2 步 · 选择根 link (base)</div>
    <div class="hint">根 link 是机器人树最顶层的连杆（通常是躯干/底盘），不依附于任何关节。</div>
    <div class="file-grid">`;
  for (const l of links) {
    html += `<div class="file-item ${l.id === rootId ? "sel" : ""}" data-id="${l.id}">
      <input type="radio" name="root" ${l.id === rootId ? "checked" : ""} data-id="${l.id}">
      <span class="nm">${l.name}</span></div>`;
  }
  html += `</div><div class="nav"><button class="btn" id="btnPrev2">← 上一步</button>
    <button class="btn next" id="btnNext2" ${rootId ? "" : "disabled"}>下一步：搭关节树 →</button></div>`;
  panel.innerHTML = html;

  panel.querySelectorAll(".file-item").forEach(it => {
    it.addEventListener("click", () => {
      rootId = it.dataset.id;
      panel.querySelectorAll(".file-item").forEach(x => x.classList.toggle("sel", x.dataset.id === rootId));
      panel.querySelectorAll("input[name=root]").forEach(r => r.checked = r.dataset.id === rootId);
      $("btnNext2").disabled = false;
    });
  });
  $("btnPrev2").addEventListener("click", () => goStep(1));
  $("btnNext2").addEventListener("click", () => { buildJointTree(); goStep(3); });
}

// ---------- Step 3: 搭关节树 ----------
// 自动生成树：按命名猜测（left_/right_ 成对，躯干为根），用户可调整父子/类型
function guessParent(childLink) {
  const name = childLink.name;
  // 左腿链
  if (/^(left|upper_leg_left|leg|ankle_left|hip_l|yaw2roll)/i.test(name)) {
    // 找根
  }
  // 简单启发：含 base/trunk/body/root 作为根
  return null;
}

function buildJointTree() {
  // 简单的命名启发式建树（可手调）
  const names = links.map(l => l.name.toLowerCase());
  const find = (re) => { const l = links.find(x => re.test(x.name.toLowerCase())); return l ? l.id : null; };

  // 如果存在躯干类零件作为根
  const rootCandidates = ["base", "trunk", "body", "root", "main"];
  let chosenRoot = null;
  for (const rc of rootCandidates) {
    const l = links.find(x => x.name.toLowerCase().includes(rc));
    if (l) { chosenRoot = l.id; break; }
  }
  if (!chosenRoot && rootId) chosenRoot = rootId;
  if (!chosenRoot) chosenRoot = links[0] ? links[0].id : null;

  // 剩余 link 全部先挂到根（用户后续在 step3 调整父子）
  joints = [];
  let jid = 1;
  for (const l of links) {
    if (l.id === chosenRoot) continue;
    const j = {
      id: "j" + (jid++),
      name: (l.name.replace(/[^a-zA-Z0-9_]/g, "_") + "_joint"),
      type: "revolute",
      parentId: chosenRoot, childId: l.id,
      xyz: [0, 0, 0.1], rpy: [0, 0, 0], axis: [0, 0, 1],
      lo: -1.5708, hi: 1.5708,
    };
    joints.push(j);
  }
  rootId = chosenRoot;
}

function renderStep3() {
  const panel = $("panel");
  const rootL = links.find(l => l.id === rootId);
  let html = `<div class="section-title">第 3 步 · 搭建关节树</div>
    <div class="hint">根：<b>${rootL ? rootL.name : "?"}</b>。当前每个非根 link 都已挂到根下。
    请为每个关节确认 <b>父 link</b>、<b>关节名</b> 与 <b>类型</b>（rev=旋转 / fixed=固定 / prismatic=平移）。
    点击关节名可选中（下一步配参数）。建议把左/右腿、头的父子关系调整正确。</div>
    <div class="tree-line" id="tree">${renderTreeText()}</div>`;

  // 每个关节一个编辑行
  html += `<div class="section-title" style="margin-top:12px">关节清单</div>`;
  for (const j of joints) {
    const child = links.find(l => l.id === j.childId);
    const sel = j.id === selectedJointId;
    html += `<details ${sel ? "open" : ""}>
      <summary>${sel ? "▶ " : ""}${j.name} <span style="font-weight:400;color:var(--dim)">→ ${child ? child.name : "?"}</span></summary>
      <div class="jt">
        <div class="field"><label>父 link</label>
          <select data-j="${j.id}" data-f="parent">${links.filter(l => l.id !== j.childId).map(l =>
            `<option value="${l.id}" ${l.id === j.parentId ? "selected" : ""}>${l.name}</option>`).join("")}</select></div>
        <div class="field"><label>关节名</label><input type="text" data-j="${j.id}" data-f="name" value="${j.name}"></div>
        <div class="field"><label>类型</label><select data-j="${j.id}" data-f="type">
          <option value="revolute" ${j.type==="revolute"?"selected":""}>revolute 旋转</option>
          <option value="fixed" ${j.type==="fixed"?"selected":""}>fixed 固定</option>
          <option value="prismatic" ${j.type==="prismatic"?"selected":""}>prismatic 平移</option>
        </select></div>
      </div></details>`;
  }
  html += `<div class="nav"><button class="btn" id="btnPrev3">← 上一步</button>
    <button class="btn next" id="btnNext3">下一步：关节参数 →</button></div>`;
  panel.innerHTML = html;
  refreshHud();

  panel.querySelectorAll("select[data-f=parent], input[data-f=name], select[data-f=type]").forEach(inp => {
    inp.addEventListener("change", () => {
      const j = joints.find(x => x.id === inp.dataset.j);
      const f = inp.dataset.f, v = inp.value;
      if (f === "parent") j.parentId = v;
      else if (f === "name") j.name = v;
      else if (f === "type") j.type = v;
      rebuild(); renderStep3();
    });
  });
  panel.querySelectorAll(".jt-name, summary").forEach(el => {});
  $("btnPrev3").addEventListener("click", () => goStep(2));
  $("btnNext3").addEventListener("click", () => goStep(4));
}

function renderTreeText() {
  // 以根为起点做简单层级输出
  const childrenOf = (pid) => joints.filter(j => j.parentId === pid);
  const lines = [];
  function walk(pid, depth) {
    const l = links.find(x => x.id === pid);
    if (l) {
      const sel = joints.some(j => j.id === selectedJointId && j.childId === pid);
      lines.push("  ".repeat(depth) + (depth === 0 ? "● " : "└─ ") +
        `<span class="${depth === 0 ? "root" : sel ? "sel" : ""}">${l.name}</span>`);
    }
    for (const j of childrenOf(pid)) {
      walk(j.childId, depth + 1);
    }
  }
  walk(rootId, 0);
  return lines.join("\n");
}

// ---------- Step 4: 关节参数 ----------
function renderStep4() {
  const panel = $("panel");
  let html = `<div class="section-title">第 4 步 · 关节参数（位置/坐标系/轴/限位）</div>
    <div class="hint">点左侧 3D 或下方关节名选中，然后调整参数，<b>实时预览</b>。<br>
    位置=关节原点在父系坐标；坐标系=关节局部系旋转(°)；轴=旋转轴向量。</div>`;
  // 关节列表（点选）
  html += `<details open><summary>关节列表（点击选中）</summary>`;
  for (const j of joints) {
    const child = links.find(l => l.id === j.childId);
    const sel = j.id === selectedJointId;
    html += `<div class="jt"><div class="jt-head">
      <span class="jt-name ${sel ? "sel" : ""}" data-j="${j.id}">${j.name}${sel ? " ◀" : ""}</span>
      <span style="font-size:10px;color:var(--dim)">${j.type} · ${child ? child.name : "?"}</span></div></div>`;
  }
  html += `</details>`;

  // 选中关节的编辑区
  const j = joints.find(x => x.id === selectedJointId);
  if (j) {
    html += `<div class="section-title" style="margin-top:12px">✏️ 正在编辑：${j.name}</div>
      <div class="row3">
        <div class="field"><label>X</label><input type="number" step="0.001" data-f="xyz0" value="${j.xyz[0].toFixed(6)}"><span class="unit">m</span></div>
        <div class="field"><label>Y</label><input type="number" step="0.001" data-f="xyz1" value="${j.xyz[1].toFixed(6)}"><span class="unit">m</span></div>
        <div class="field"><label>Z</label><input type="number" step="0.001" data-f="xyz2" value="${j.xyz[2].toFixed(6)}"><span class="unit">m</span></div>
      </div>
      <div class="row3">
        <div class="field"><label>Rx</label><input type="number" step="1" data-f="rpy0" value="${(j.rpy[0]*R2D).toFixed(2)}"><span class="unit">°</span></div>
        <div class="field"><label>Ry</label><input type="number" step="1" data-f="rpy1" value="${(j.rpy[1]*R2D).toFixed(2)}"><span class="unit">°</span></div>
        <div class="field"><label>Rz</label><input type="number" step="1" data-f="rpy2" value="${(j.rpy[2]*R2D).toFixed(2)}"><span class="unit">°</span></div>
      </div>
      <div class="row3">
        <div class="field"><label>Ax</label><input type="number" step="0.01" data-f="axis0" value="${j.axis[0].toFixed(6)}"></div>
        <div class="field"><label>Ay</label><input type="number" step="0.01" data-f="axis1" value="${j.axis[1].toFixed(6)}"></div>
        <div class="field"><label>Az</label><input type="number" step="0.01" data-f="axis2" value="${j.axis[2].toFixed(6)}"></div>
      </div>
      <div class="row3">
        <div class="field"><label>Lo</label><input type="number" step="1" data-f="lo" value="${(j.lo*R2D).toFixed(2)}"><span class="unit">°</span></div>
        <div class="field"><label>Hi</label><input type="number" step="1" data-f="hi" value="${(j.hi*R2D).toFixed(2)}"><span class="unit">°</span></div>
        <div class="field"><label>Q</label><input type="number" step="1" data-f="q" value="0"><span class="unit">°</span></div>
      </div>`;
  } else {
    html += `<div class="hint" style="margin-top:12px">（请先在上方列表选择一个关节）</div>`;
  }
  html += `<div class="nav"><button class="btn" id="btnPrev4">← 上一步</button>
    <button class="btn next" id="btnNext4">下一步：导出 →</button></div>`;
  panel.innerHTML = html;
  refreshHud();

  // 点选关节
  panel.querySelectorAll(".jt-name").forEach(el => {
    el.addEventListener("click", () => {
      selectedJointId = selectedJointId === el.dataset.j ? null : el.dataset.j;
      renderStep4(); applyFk();
    });
  });

  // 参数输入
  const selJ = joints.find(x => x.id === selectedJointId);
  if (selJ) {
    panel.querySelectorAll("input[data-f]").forEach(inp => {
      inp.addEventListener("input", () => {
        const f = inp.dataset.f, v = parseFloat(inp.value) || 0;
        if (f === "xyz0") selJ.xyz[0] = v;
        else if (f === "xyz1") selJ.xyz[1] = v;
        else if (f === "xyz2") selJ.xyz[2] = v;
        else if (f === "rpy0") selJ.rpy[0] = v * D2R;
        else if (f === "rpy1") selJ.rpy[1] = v * D2R;
        else if (f === "rpy2") selJ.rpy[2] = v * D2R;
        else if (f === "axis0") selJ.axis[0] = v;
        else if (f === "axis1") selJ.axis[1] = v;
        else if (f === "axis2") selJ.axis[2] = v;
        else if (f === "lo") selJ.lo = v * D2R;
        else if (f === "hi") selJ.hi = v * D2R;
        applyFk();
      });
    });
  }
  $("btnPrev4").addEventListener("click", () => goStep(3));
  $("btnNext4").addEventListener("click", () => goStep(5));
}

// ---------- Step 5: 导出 ----------
function renderStep5() {
  const panel = $("panel");
  const urdf = serializeURDF();
  let html = `<div class="section-title">第 5 步 · 生成并导出 URDF</div>
    <div class="hint">已生成 URDF 文本（${urdf.length} 字符）。点“保存”写入：
    <br><b>${CONFIG.savePath}</b><br>（不覆盖原 open_duck.urdf）</div>
    <pre class="out">${urdf.replace(/</g, "&lt;")}</pre>
    <div class="nav"><button class="btn" id="btnPrev5">← 上一步</button>
    <button class="btn next" id="btnSave">💾 保存 URDF</button></div>
    <div id="saveMsg"></div>`;
  panel.innerHTML = html;

  $("btnPrev5").addEventListener("click", () => goStep(4));
  $("btnSave").addEventListener("click", async () => {
    const msg = $("saveMsg");
    try {
      const res = await fetch("/api/save", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: CONFIG.savePath, content: serializeURDF() }),
      });
      const data = await res.json();
      if (data.ok) { msg.textContent = `✓ 已保存 ${data.path} (${data.bytes} bytes)`; msg.className = ""; }
      else { msg.textContent = "✗ " + JSON.stringify(data); msg.className = "err"; }
    } catch (e) { msg.textContent = "✗ " + e.message; msg.className = "err"; }
  });
}

function serializeURDF() {
  let xml = `<?xml version="1.0" encoding="utf-8"?>\n<robot name="wizard_robot">\n`;
  for (const l of links) {
    xml += `  <link name="${l.name}">\n`;
    xml += `    <inertial>\n      <origin xyz="0 0 0" rpy="0 0 0"/>\n      <mass value="0.1"/>\n      <inertia ixx="1e-5" ixy="0" ixz="0" iyy="1e-5" iyz="0" izz="1e-5"/>\n    </inertial>\n`;
    xml += `    <visual>\n      <origin xyz="0 0 0" rpy="0 0 0"/>\n      <geometry><mesh filename="${l.meshPath}"/></geometry>\n      <material name=""><color rgba="0.7 0.7 0.7 1"/></material>\n    </visual>\n`;
    xml += `    <collision>\n      <origin xyz="0 0 0" rpy="0 0 0"/>\n      <geometry><mesh filename="${l.meshPath}"/></geometry>\n    </collision>\n`;
    xml += `  </link>\n`;
  }
  for (const j of joints) {
    const child = links.find(l => l.id === j.childId);
    xml += `  <joint name="${j.name}" type="${j.type}">\n`;
    xml += `    <origin xyz="${j.xyz.map(v => v.toFixed(9)).join(" ")}" rpy="${j.rpy.map(v => v.toFixed(9)).join(" ")}"/>\n`;
    xml += `    <parent link="${links.find(l => l.id === j.parentId).name}"/>\n`;
    xml += `    <child link="${child.name}"/>\n`;
    if (j.type !== "fixed") xml += `    <axis xyz="${j.axis.map(v => v.toFixed(9)).join(" ")}"/>\n`;
    xml += `    <limit lower="${j.lo.toFixed(6)}" upper="${j.hi.toFixed(6)}" effort="1.5" velocity="3.0"/>\n`;
    xml += `  </joint>\n`;
  }
  xml += `</robot>\n`;
  return xml;
}

// ---------- 3D 装配 ----------
async function rebuild() {
  if (robotGroup) { scene.remove(robotGroup); robotGroup = null; }
  robotGroup = new THREE.Group();
  scene.add(robotGroup);
  THREE_GROUPS.clear(); ARROWS.clear();

  // 建 link 组
  for (const l of links) {
    const g = new THREE.Group();
    robotGroup.add(g);
    THREE_GROUPS.set(l.id, g);
    // 加载网格
    try {
      const geo = await new STLLoader().loadAsync(l.meshUrl);
      const mat = new THREE.MeshStandardMaterial({ color: 0x9aa7b5, metalness: 0.25, roughness: 0.55 });
      const mesh = new THREE.Mesh(geo, mat);
      g.add(mesh);
    } catch (e) { console.warn(l.name, e.message); }
  }

  // 根
  if (rootId) robotGroup.add(THREE_GROUPS.get(rootId));

  // 关节
  const childToJoint = new Map(joints.map(j => [j.childId, j]));
  function attach(childId, parentGroup) {
    const j = childToJoint.get(childId);
    if (!j) return;
    const jnode = new THREE.Group();
    jnode.position.fromArray(j.xyz);
    jnode.quaternion.copy(quatFromRPY(j.rpy));
    const arrow = makeAxisArrow();
    jnode.add(arrow);
    const childGroup = THREE_GROUPS.get(childId);
    robotGroup.remove(childGroup);
    jnode.add(childGroup);
    parentGroup.add(jnode);
    ARROWS.set(j.id, { arrow, childGroup, axisNorm: new THREE.Vector3().fromArray(j.axis).normalize() });
    // 递归
    for (const cj of joints.filter(x => x.parentId === childId)) attach(cj.childId, childGroup);
  }
  for (const j of joints.filter(x => x.parentId === rootId)) attach(j.childId, THREE_GROUPS.get(rootId));

  applyFk();
}

function makeAxisArrow() {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.SphereGeometry(0.008, 12, 12), new THREE.MeshBasicMaterial({ color: 0xffffff })));
  const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.004, 0.004, 0.09, 8), new THREE.MeshBasicMaterial({ color: 0x2fe0b0 }));
  shaft.position.z = 0.045;
  const tip = new THREE.Mesh(new THREE.ConeGeometry(0.012, 0.03, 8), new THREE.MeshBasicMaterial({ color: 0x2fe0b0 }));
  tip.position.z = 0.11;
  const ax = new THREE.Group(); ax.add(shaft); ax.add(tip); g.add(ax);
  [[0xff5a5a,[1,0,0]],[0x57c98f,[0,1,0]],[0x4da3ff,[0,0,1]]].forEach(([c,b]) => {
    g.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(...b).multiplyScalar(0.05)]),
      new THREE.LineBasicMaterial({ color: c })
    ));
  });
  g.visible = false;
  return g;
}

function applyFk() {
  for (const j of joints) {
    const ar = ARROWS.get(j.id);
    if (!ar) continue;
    // 同步 jnode
    ar.childGroup.quaternion.identity();
    const a = new THREE.Vector3().fromArray(j.axis).normalize();
    if (a.lengthSq() > 1e-12) ar.arrow.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,0,1), a));
    ar.arrow.visible = $("showAxes").checked;
  }
}

function refreshHud() {
  $("hud").innerHTML = `<div><b>SolidWorks → URDF 向导</b></div>
    <div>links <b>${links.length}</b> · joints <b>${joints.length}</b> · 根 <b>${links.find(l=>l.id===rootId)?.name || "—"}</b></div>`;
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
function onResize() {
  renderer.setSize($("gl").clientWidth, $("gl").clientHeight);
  camera.aspect = $("gl").clientWidth / $("gl").clientHeight;
  camera.updateProjectionMatrix();
}
function animate() { requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); }

// ---------- 步骤机 ----------
function goStep(n) {
  currentStep = n;
  document.querySelectorAll(".step").forEach((s, i) => {
    const num = i + 1;
    s.classList.toggle("active", num === n);
    s.classList.toggle("done", num < n);
  });
  if (n === 1) renderStep1();
  else if (n === 2) renderStep2();
  else if (n === 3) { renderStep3(); rebuild(); }
  else if (n === 4) { renderStep4(); rebuild(); }
  else if (n === 5) renderStep5();
  setTimeout(fitView, 50);
}

// ---------- boot ----------
async function boot() {
  try {
    initThree();
    onResize();
    await loadMeshList();
    animate();
    document.querySelectorAll(".step").forEach(s => {
      s.addEventListener("click", () => goStep(parseInt(s.dataset.step)));
    });
    $("btnUp").addEventListener("click", () => {
      const z = camera.up.z > 0.5;
      camera.up.set(0, z ? 1 : 0, z ? 0 : 1);
      $("btnUp").textContent = "世界轴: " + (z ? "Y↑" : "Z↑");
      controls.update(); fitView();
    });
    $("btnFit").addEventListener("click", fitView);
    $("showGrid").addEventListener("change", e => {
      const grid = scene.children.find(c => c.isGridHelper); if (grid) grid.visible = e.target.checked;
    });
    $("showAxes").addEventListener("change", () => applyFk());
    window.addEventListener("resize", onResize);
  } catch (e) { showError(e); }
}
boot();
