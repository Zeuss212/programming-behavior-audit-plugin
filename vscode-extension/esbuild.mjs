import * as esbuild from 'esbuild';
import process from 'node:process';

const production = process.argv.includes('--production');

await esbuild.build({
  entryPoints: ['src/extension.ts'],
  bundle: true,
  outfile: 'dist/extension.js',
  external: ['vscode'],
  platform: 'node',
  format: 'cjs',
  target: 'node22',
  minify: production,
  sourcemap: production ? false : 'linked',
  logLevel: 'info',
});
