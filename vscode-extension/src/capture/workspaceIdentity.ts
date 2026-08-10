import { sha256Hex } from '../domain/canonicalJson';

function normalizeWorkspaceUri(uri: string): string {
  return uri.replace(/\/+$/u, '');
}

export function workspaceIdentity(workspaceUris: readonly string[]): string {
  const normalized = workspaceUris.map(normalizeWorkspaceUri).sort();
  return sha256Hex(normalized.join('\n'));
}
