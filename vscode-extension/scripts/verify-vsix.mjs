import { Buffer } from 'node:buffer';
import { access } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import process from 'node:process';

const require = createRequire(import.meta.url);
const yauzl = require('yauzl');

const requestedPath = process.argv[2];
if (requestedPath === undefined) {
  throw new Error('Usage: node scripts/verify-vsix.mjs <vsix>');
}
const vsixPath = resolve(requestedPath);
try {
  await access(vsixPath);
} catch {
  throw new Error(`VSIX not found: ${vsixPath}`);
}

function openZip(path) {
  return new Promise((resolveOpen, reject) => {
    yauzl.open(path, { lazyEntries: true }, (error, zip) => {
      if (error) {
        reject(error);
      } else if (zip === undefined) {
        reject(new Error('Unable to open VSIX.'));
      } else {
        resolveOpen(zip);
      }
    });
  });
}

function readEntry(zip, entry) {
  return new Promise((resolveRead, reject) => {
    zip.openReadStream(entry, (error, stream) => {
      if (error) {
        reject(error);
        return;
      }
      const chunks = [];
      stream.on('data', (chunk) => chunks.push(Buffer.from(chunk)));
      stream.once('error', reject);
      stream.once('end', () => resolveRead(Buffer.concat(chunks)));
    });
  });
}

async function readZip(path) {
  const zip = await openZip(path);
  const entries = new Map();
  return new Promise((resolveEntries, reject) => {
    zip.once('error', reject);
    zip.once('end', () => resolveEntries(entries));
    zip.on('entry', (entry) => {
      if (entry.fileName.endsWith('/')) {
        zip.readEntry();
        return;
      }
      void readEntry(zip, entry)
        .then((bytes) => {
          entries.set(entry.fileName, bytes);
          zip.readEntry();
        })
        .catch(reject);
    });
    zip.readEntry();
  });
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const entries = await readZip(vsixPath);
const names = [...entries.keys()].sort();
const required = [
  'extension/package.json',
  'extension/dist/extension.js',
  'extension/media/activity.svg',
  'extension/media/sidebar.css',
  'extension/media/sidebar.js',
  'extension/media/plan-wizard.css',
  'extension/media/plan-wizard.js',
  'extension/schemas/plan-v1.schema.json',
  'extension/schemas/export-manifest-v1.schema.json',
  'extension/schemas/ai-plan-suggestion-v1.schema.json',
  'extension/schemas/ai-session-analysis-v1.schema.json',
];
for (const name of required) {
  assert(entries.has(name), `Required VSIX entry is missing: ${name}`);
}

const manifest = JSON.parse(entries.get('extension/package.json').toString('utf8'));
assert(manifest.publisher === 'bluedot-ai', 'Unexpected VSIX publisher.');
assert(manifest.name === 'behavior-audit-vscode', 'Unexpected VSIX extension name.');
assert(manifest.version === '0.1.2', 'Unexpected VSIX version.');
assert(manifest.engines?.vscode === '^1.125.0', 'Unexpected VS Code engine.');
assert(manifest.main === './dist/extension.js', 'Unexpected production entry point.');

const forbiddenEntryPatterns = [
  /(?:^|\/)src\//u,
  /(?:^|\/)test\//u,
  /(?:^|\/)fixtures\//u,
  /(?:^|\/)node_modules\//u,
  /(?:^|\/)\.git(?:\/|$)/u,
  /(?:^|\/)\.env(?:\.|$)/u,
  /\.map$/u,
  /tsconfig[^/]*\.json$/u,
];
for (const name of names) {
  assert(
    !forbiddenEntryPatterns.some((pattern) => pattern.test(name)),
    `Forbidden VSIX entry: ${name}`,
  );
}

const productionEntries = names.filter((name) => /^extension\/dist\/[^/]+\.js$/u.test(name));
assert(
  productionEntries.length === 1 && productionEntries[0] === 'extension/dist/extension.js',
  `Expected exactly one production entry point, found: ${productionEntries.join(', ')}`,
);

const forbiddenText = [
  'ARK_API_KEY=',
  'gho_',
  'github_pat_',
  '/Users/',
  'C:\\Users\\',
  'must-not-ship-secret',
];
for (const [name, bytes] of entries) {
  if (bytes.includes(0)) {
    continue;
  }
  const text = bytes.toString('utf8');
  for (const marker of forbiddenText) {
    assert(!text.includes(marker), `Sensitive marker ${marker} found in ${name}`);
  }
}

process.stdout.write(`VSIX verification: OK (${String(names.length)} files)\n`);
