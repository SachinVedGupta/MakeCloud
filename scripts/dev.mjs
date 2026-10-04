/** Launch both loopback services and stop both when either exits. Never logs secrets. */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createConnection } from 'node:net';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { prepareWeb } from './prepare-web.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
if (!existsSync(python)) {
  console.error('Create .venv and install requirements.lock first. See README.md.');
  process.exit(1);
}
const apiPort = Number(process.env.MAKECLOUD_DEV_API_PORT || 5010);
const uiPort = Number(process.env.MAKECLOUD_DEV_UI_PORT || 3000);
if (![apiPort, uiPort].every((port) => Number.isInteger(port) && port > 1024 && port < 65536) || apiPort === uiPort) {
  console.error('Choose two different local ports between 1025 and 65535.'); process.exit(1);
}
function occupied(port) {
  return new Promise((resolve) => {
    const socket = createConnection({ host: '127.0.0.1', port });
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', (error) => resolve(error.code !== 'ECONNREFUSED'));
    socket.setTimeout(1000, () => { socket.destroy(); resolve(true); });
  });
}
const used = await Promise.all([apiPort, uiPort].map(occupied));
if (used.some(Boolean)) {
  console.error('A local port is occupied. Set MAKECLOUD_DEV_API_PORT and MAKECLOUD_DEV_UI_PORT to free ports, then restart.');
  process.exit(1);
}
const env = {
  ...process.env,
  MAKECLOUD_MODE: process.env.MAKECLOUD_MODE || 'demo',
  MAKECLOUD_API_TOKEN: process.env.MAKECLOUD_API_TOKEN || randomBytes(32).toString('hex'),
  MAKECLOUD_BACKEND_URL: `http://127.0.0.1:${apiPort}`,
  MAKECLOUD_LOCAL_ONLY: 'true',
  MAKECLOUD_SECURE_COOKIES: 'false',
};
const production = process.argv.includes('--production');
const children = [];
let closing = false;
function stop(code = 0) {
  if (closing) return;
  closing = true;
  for (const child of children) child.kill('SIGTERM');
  const timer = setTimeout(() => { for (const child of children) child.kill('SIGKILL'); process.exit(code); }, 6000);
  Promise.all(children.map((child) => child.exitCode !== null || child.signalCode !== null ? Promise.resolve() : new Promise((resolve) => child.once('exit', resolve))))
    .then(() => { clearTimeout(timer); process.exit(code); });
}
function launch(executable, args) {
  const child = spawn(executable, args, { cwd: root, env, stdio: 'inherit' });
  children.push(child);
  child.once('error', () => { console.error('A local service could not start. Check the setup and port availability.'); stop(1); });
  child.once('exit', (code) => { if (!closing) stop(code || 0); });
  return child;
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
launch(python, ['-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', String(apiPort), '--workers', '1', '--no-access-log']);
let ready = false;
for (let attempt = 0; attempt < 100 && !closing; attempt++) {
  try {
    const response = await fetch(`${env.MAKECLOUD_BACKEND_URL}/health`, { headers: { Authorization: `Bearer ${env.MAKECLOUD_API_TOKEN}` }, signal: AbortSignal.timeout(1000) });
    if (response.ok) { ready = true; break; }
  } catch { /* The API may still be starting. */ }
  await new Promise((resolve) => setTimeout(resolve, 200));
}
if (!ready && !closing) { console.error('The API did not become ready. Check the service output.'); stop(1); }
if (ready && !closing) {
  if (production) {
    env.HOSTNAME = '127.0.0.1'; env.PORT = String(uiPort);
    try { launch(process.execPath, [prepareWeb(root)]); }
    catch (error) { console.error(error.message); stop(1); }
  } else launch(process.execPath, [path.join(root, 'node_modules/next/dist/bin/next'), 'dev', '--hostname', '127.0.0.1', '--port', String(uiPort)]);
  console.log(`MakeCloud: http://127.0.0.1:${uiPort} · ${env.MAKECLOUD_MODE} mode`);
}
