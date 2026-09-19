// 极简静态文件服务器 + 保存端点 —— 用于本地预览/编辑 urdf_viewer / urdf_editor
// 用法: node serve.mjs [port] [root]
// 默认端口 8123，root 为脚本所在目录（疯狂小鸡项目根）
// 附加 POST /api/save  { "path": "<相对项目根的路径>", "content": "<文本>" }
//         → 把 content 写入该文件（仅允许 .urdf/.xml/.json/.txt/.mjs/.html/.js/.csv）
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = parseInt(process.argv[2] || "8123", 10);
const ROOT = path.resolve(process.argv[3] || __dirname);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".stl": "model/stl",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".xml": "application/xml; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".obj": "text/plain; charset=utf-8",
  ".wasm": "application/wasm",
};

const SAVE_EXT = new Set([".urdf", ".xml", ".json", ".txt", ".mjs", ".js", ".html", ".csv", ".yaml"]);

function send(res, code, body, type = "text/plain; charset=utf-8") {
  res.writeHead(code, { "Content-Type": type });
  res.end(body);
}

const server = http.createServer((req, res) => {
  try {
    const urlObj = new URL(req.url, `http://${req.headers.host || "127.0.0.1"}`);
    const pathname = decodeURIComponent(urlObj.pathname);

    // ---- 列目录端点（供 URDF 向导扫描 STL 零件）----
    if (req.method === "GET" && pathname === "/api/list") {
      const dir = urlObj.searchParams.get("dir") || "";
      const target = path.normalize(path.join(ROOT, dir));
      if (!target.startsWith(ROOT)) return send(res, 403, "Forbidden");
      if (!fs.existsSync(target) || !fs.statSync(target).isDirectory()) {
        return send(res, 404, "目录不存在: " + dir);
      }
      const files = fs.readdirSync(target, { withFileTypes: true })
        .filter(d => d.isFile() && /\.stl$/i.test(d.name))
        .map(d => {
          const p = path.join(target, d.name);
          return { name: d.name, size: fs.statSync(p).size, path: path.relative(ROOT, p).split(path.sep).join("/") };
        })
        .sort((a, b) => a.name.localeCompare(b.name));
      send(res, 200, JSON.stringify({ dir, files }), "application/json");
      return;
    }

    // ---- 保存端点 ----
    if (req.method === "POST" && pathname === "/api/save") {
      let data = "";
      req.on("data", (c) => { data += c; if (data.length > 5e6) { req.destroy(); } });
      req.on("end", () => {
        try {
          const body = JSON.parse(data);
          if (!body || typeof body.path !== "string" || typeof body.content !== "string") {
            return send(res, 400, "需要 {path, content}");
          }
          const ext = path.extname(body.path).toLowerCase();
          if (!SAVE_EXT.has(ext)) return send(res, 400, `不允许的扩展名: ${ext}`);
          const filePath = path.normalize(path.join(ROOT, body.path));
          if (!filePath.startsWith(ROOT)) return send(res, 403, "Forbidden");
          fs.mkdirSync(path.dirname(filePath), { recursive: true });
          fs.writeFileSync(filePath, body.content, "utf8");
          send(res, 200, JSON.stringify({ ok: true, path: body.path, bytes: Buffer.byteLength(body.content, "utf8") }), "application/json");
        } catch (e) {
          send(res, 500, "Save error: " + e.message);
        }
      });
      return;
    }

    // ---- 静态文件 ----
    let urlPath = pathname;
    if (urlPath.endsWith("/")) urlPath += "index.html";
    const filePath = path.normalize(path.join(ROOT, urlPath));
    if (!filePath.startsWith(ROOT)) { send(res, 403, "Forbidden"); return; }
    fs.stat(filePath, (err, st) => {
      if (err || !st.isFile()) { send(res, 404, "404 Not Found: " + urlPath); return; }
      const ext = path.extname(filePath).toLowerCase();
      const type = MIME[ext] || "application/octet-stream";
      res.writeHead(200, { "Content-Type": type });
      fs.createReadStream(filePath).pipe(res);
    });
  } catch (e) {
    send(res, 500, "Server error: " + e.message);
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[urdf tools] http://127.0.0.1:${PORT}/urdf_editor/   (save endpoint /api/save ready)`);
});
