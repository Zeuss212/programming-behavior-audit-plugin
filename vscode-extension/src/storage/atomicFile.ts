import { randomUUID } from 'node:crypto';
import { open, mkdir, rename, unlink } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';

import { canonicalJson } from '../domain/canonicalJson';
import type { JsonValue } from '../domain/types';

function isIgnorableDirectorySyncError(error: unknown): boolean {
  if (!(error instanceof Error) || !('code' in error)) {
    return false;
  }
  const code = String(error.code);
  return ['EACCES', 'EBADF', 'EINVAL', 'EISDIR', 'ENOTSUP', 'EPERM'].includes(code);
}

async function syncParentDirectory(path: string): Promise<void> {
  let handle;
  try {
    handle = await open(dirname(path), 'r');
    await handle.sync();
  } catch (error) {
    if (!isIgnorableDirectorySyncError(error)) {
      throw error;
    }
  } finally {
    await handle?.close();
  }
}

export async function writeFileAtomic(path: string, bytes: Uint8Array): Promise<void> {
  const parent = dirname(path);
  await mkdir(parent, { recursive: true });
  const temporaryPath = join(parent, `.${basename(path)}.${randomUUID()}.tmp`);
  let handle;
  try {
    handle = await open(temporaryPath, 'wx');
    await handle.writeFile(bytes);
    await handle.sync();
    await handle.close();
    handle = undefined;
    await rename(temporaryPath, path);
    await syncParentDirectory(path);
  } catch (error) {
    await handle?.close().catch(() => undefined);
    await unlink(temporaryPath).catch(() => undefined);
    throw error;
  }
}

export async function writeJsonAtomic(path: string, value: unknown): Promise<void> {
  const bytes = new TextEncoder().encode(
    `${canonicalJson(value as JsonValue)}\n`,
  );
  await writeFileAtomic(path, bytes);
}
