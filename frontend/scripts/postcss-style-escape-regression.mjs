/** PLAT-4398 / GHSA-qx2v-qp2m-jg93: postcss >=8.5.10 floor. */
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const dir = dirname(fileURLToPath(import.meta.url));
const ver = require('postcss/package.json').version;
const [maj, min, pat] = ver.split('.').map(Number);
if (!(maj > 8 || (maj === 8 && (min > 5 || (min === 5 && pat >= 10))))) {
  console.error('FAIL postcss', ver); process.exit(1);
}
const lock = JSON.parse(readFileSync(join(dir, '../package-lock.json'), 'utf8'));
const locked = lock.packages?.['node_modules/postcss']?.version;
const resolved = lock.packages?.['node_modules/postcss']?.resolved || '';
if (locked !== ver) { console.error('FAIL lock', locked, ver); process.exit(1); }
if (resolved.includes('npm-cache.registry.svc')) { console.error('FAIL internal', resolved); process.exit(1); }
const ui = lock.packages?.['node_modules/@shizuha/ui'];
if (!ui?.link || ui.resolved !== '../packages/shizuha-ui') { console.error('FAIL ui', ui); process.exit(1); }
const postcss = require('postcss');
const out = postcss.parse('a{content:"</style><script>x</script>"}').toResult().css;
console.log(JSON.stringify({ ok: true, postcss: ver, locked, uiResolved: ui.resolved, sample: out.slice(0, 80) }, null, 2));
