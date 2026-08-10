import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import * as esbuild from 'esbuild';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const temporaryDirectory = await mkdtemp(join(tmpdir(), 'behavior-audit-soak-runner-'));
const outputFile = join(temporaryDirectory, 'accelerated-soak.mjs');

try {
  await esbuild.build({
    entryPoints: [join(scriptDirectory, 'accelerated-soak.ts')],
    outfile: outputFile,
    bundle: true,
    platform: 'node',
    format: 'esm',
    target: 'node22',
    sourcemap: false,
    logLevel: 'silent',
  });
  const module = await import(pathToFileURL(outputFile).href);
  if (typeof module.runAcceleratedSoak !== 'function') {
    throw new Error('Accelerated soak entry point is missing.');
  }
  await module.runAcceleratedSoak();
} finally {
  await rm(temporaryDirectory, { recursive: true, force: true });
}
