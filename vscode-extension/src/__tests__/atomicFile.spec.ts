import { mkdtemp, readFile, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { writeJsonAtomic } from '../storage/atomicFile';

describe('writeJsonAtomic', () => {
  it('never exposes partial JSON during concurrent replacements', async () => {
    const root = await mkdtemp(join(tmpdir(), 'behavior-audit-atomic-'));
    const target = join(root, 'state.json');
    await writeJsonAtomic(target, { version: 0, payload: 'initial' });

    let keepReading = true;
    const reader = (async () => {
      while (keepReading) {
        const parsed = JSON.parse(await readFile(target, 'utf8')) as Record<string, unknown>;
        expect(parsed).toHaveProperty('version');
      }
    })();

    await Promise.all(
      Array.from({ length: 30 }, (_, index) =>
        writeJsonAtomic(target, { version: index + 1, payload: 'x'.repeat(2048) }),
      ),
    );
    keepReading = false;
    await reader;

    const finalText = await readFile(target, 'utf8');
    expect(() => {
      JSON.parse(finalText);
    }).not.toThrow();
    expect((await readdir(root)).filter((name) => name.endsWith('.tmp'))).toEqual([]);
  });
});
