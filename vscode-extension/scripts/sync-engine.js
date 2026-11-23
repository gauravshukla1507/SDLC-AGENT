// Copies the Python engine (../engine) into this extension so it ships inside the .vsix.
// The repo keeps one source of truth; vscode-extension/engine/ is generated and git-ignored.
const fs = require("fs"); const path = require("path");
const src = path.resolve(__dirname, "..", "..", "engine");
const dst = path.resolve(__dirname, "..", "engine");
const skip = new Set(["tests", "runs", "__pycache__", ".pytest_cache", "pytest.ini"]);
fs.rmSync(dst, { recursive: true, force: true });
(function copy(from, to) {
  fs.mkdirSync(to, { recursive: true });
  for (const e of fs.readdirSync(from, { withFileTypes: true })) {
    if (skip.has(e.name) || e.name.endsWith(".pyc")) continue;
    const a = path.join(from, e.name), b = path.join(to, e.name);
    e.isDirectory() ? copy(a, b) : fs.copyFileSync(a, b);
  }
})(src, dst);
console.log(`engine synced: ${src} -> ${dst}`);
