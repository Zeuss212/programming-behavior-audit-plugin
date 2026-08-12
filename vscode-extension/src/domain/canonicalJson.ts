import { createHash } from 'node:crypto';

import type { JsonValue } from './types';

function isJsonArray(value: JsonValue): value is readonly JsonValue[] {
  return Array.isArray(value);
}

function compareCodePoints(left: string, right: string): number {
  if (left < right) {
    return -1;
  }
  if (left > right) {
    return 1;
  }
  return 0;
}

function normalize(value: JsonValue): JsonValue {
  if (isJsonArray(value)) {
    return value.map((item) => normalize(item));
  }

  if (typeof value === 'number' && !Number.isFinite(value)) {
    throw new TypeError('Canonical JSON does not support non-finite numbers.');
  }

  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value)
      .sort(([left], [right]) => compareCodePoints(left, right))
      .map(([key, child]) => [key, normalize(child)] as const);
    return Object.fromEntries(entries);
  }

  return value;
}

export function canonicalJson(value: JsonValue): string {
  const serialized = JSON.stringify(normalize(value));
  if (serialized === undefined) {
    throw new TypeError('Value cannot be represented as canonical JSON.');
  }
  return serialized;
}

export function sha256Hex(value: string | Uint8Array): string {
  return createHash('sha256').update(value).digest('hex');
}
