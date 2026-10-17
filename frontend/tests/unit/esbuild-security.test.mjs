/**
 * PLAT-4391 — esbuild GHSA-67mh-4wv8-2f99
 *
 * Consumer-boundary regression: Vite calls esbuild.transform / esbuild.build.
 * Pin must stay on patched >=0.25.0 without breaking that API.
 */
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

const require = createRequire(import.meta.url);

describe('esbuild security regression (PLAT-4391 / GHSA-67mh-4wv8-2f99)', () => {
  it('resolves a patched line (>= 0.25.0)', () => {
    const pkg = require('esbuild/package.json');
    const [major, minor] = pkg.version.split('.').map(Number);
    assert.equal(major, 0);
    assert.ok(minor >= 25, `expected esbuild >=0.25.0, got ${pkg.version}`);
  });

  it('exposes transform/build API used by vite', async () => {
    const esbuild = require('esbuild');
    assert.equal(typeof esbuild.transform, 'function');
    assert.equal(typeof esbuild.build, 'function');

    const transformed = await esbuild.transform('export const x = 1', {
      loader: 'js',
    });
    assert.match(transformed.code, /x/);

    const built = await esbuild.build({
      stdin: {
        contents: 'console.log(1)',
        sourcefile: 't.js',
        loader: 'js',
      },
      write: false,
      bundle: false,
    });
    assert.match(built.outputFiles[0].text, /console/);
  });
});
