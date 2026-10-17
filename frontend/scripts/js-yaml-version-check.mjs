/** PLAT-4395: pin js-yaml >=4.2.0 (GHSA-h67p-54hq-rp68). */
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const dir = dirname(fileURLToPath(import.meta.url));
const ver = require('js-yaml/package.json').version;
const [maj, min] = ver.split('.').map(Number);
if (maj < 4 || (maj === 4 && min < 2)) {
  console.error('FAIL js-yaml', ver);
  process.exit(1);
}
const lock = JSON.parse(readFileSync(join(dir, '../package-lock.json'), 'utf8'));
const locked = lock.packages?.['node_modules/js-yaml']?.version;
const resolved = lock.packages?.['node_modules/js-yaml']?.resolved || '';
if (locked !== ver) {
  console.error('FAIL lock mismatch', locked, ver);
  process.exit(1);
}
if (resolved.includes('npm-cache.registry.svc')) {
  console.error('FAIL internal registry URL', resolved);
  process.exit(1);
}
const ui = lock.packages?.['node_modules/@shizuha/ui'];
if (!ui?.link || ui.resolved !== '../packages/shizuha-ui') {
  console.error('FAIL @shizuha/ui link topology', ui);
  process.exit(1);
}
if (lock.packages?.['../../../../packages/shizuha-ui'] !== undefined) {
  console.error('FAIL escaping workspace entry present');
  process.exit(1);
}
console.log(JSON.stringify({ ok: true, jsYaml: ver, locked, uiResolved: ui.resolved }, null, 2));
