// Usage: start the preview (python -m tests.dev_server), then: node tools/check_layout.mjs 320 /tmp/some-empty-dir
// Load every page at a phone width and report anything wider than the screen.
import { spawn } from "node:child_process";
const port = 9333, base = "http://127.0.0.1:8791";
const width = Number(process.argv[2] || 320);
const chrome = spawn("chromium", ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${process.argv[3]}`,
  "--no-first-run", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let targets;
for (let i = 0; i < 50; i++) { try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); break; } catch { await sleep(200); } }
const page = targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0; const pending = new Map(); const waiters = [];
ws.addEventListener("message", (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); }
  if (msg.method === "Page.loadEventFired") waiters.splice(0).forEach((w) => w());
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: true });
const go = async (path) => { const loaded = new Promise((r) => waiters.push(r)); await send("Page.navigate", { url: base + path }); await loaded; await sleep(300); };
const probe = `(() => { const w = document.documentElement.clientWidth; const bad = [];
  for (const el of document.querySelectorAll("body *")) { const r = el.getBoundingClientRect();
    if (r.width && r.right > w + 1 && getComputedStyle(el).position !== "fixed" && !el.closest(".tabs, .nav, .sprite, dialog:not([open])")) {
      bad.push(el.tagName.toLowerCase() + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\\s+/).join(".") : "") + " right=" + Math.round(r.right)); } }
  return JSON.stringify({ scroll: document.documentElement.scrollWidth, width: w, bad: bad.slice(0, 4) }); })()`;
const pages = { alex: ["/home", "/requests", "/help", "/kids/new", "/kids/1", "/kids/1/titles", "/kids/1/schedule", "/kids/1/activity", "/kids/1/settings", "/kids/2", "/kids/2/settings"],
  sam: ["/home"], owner: ["/admin?tab=requests", "/admin?tab=kids", "/admin?tab=people", "/admin?tab=activity"], none: ["/"] };
let problems = 0;
for (const [who, list] of Object.entries(pages)) {
  if (who !== "none") await go(`/dev-login?u=${who}&next=/help`);
  for (const p of list) {
    await go(p);
    const res = JSON.parse((await send("Runtime.evaluate", { expression: probe, returnByValue: true })).result.value);
    const ok = res.scroll <= res.width && !res.bad.length;
    if (!ok) problems++;
    console.log(`${ok ? "ok  " : "WIDE"} ${width}px ${who.padEnd(5)} ${p}${ok ? "" : "  scroll=" + res.scroll + " " + res.bad.join(" | ")}`);
  }
}
console.log("pages too wide:", problems);
ws.close(); chrome.kill();
process.exit(0);
