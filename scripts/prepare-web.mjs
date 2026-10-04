import { cpSync, existsSync } from 'node:fs';
import path from 'node:path';
export function prepareWeb(root) {
  const standalone = path.join(root, '.next/standalone');
  if (!existsSync(path.join(standalone, 'server.js'))) throw new Error('Build the frontend first with npm run build.');
  cpSync(path.join(root, '.next/static'), path.join(standalone, '.next/static'), { recursive: true });
  if (existsSync(path.join(root, 'public'))) cpSync(path.join(root, 'public'), path.join(standalone, 'public'), { recursive: true });
  return path.join(standalone, 'server.js');
}
