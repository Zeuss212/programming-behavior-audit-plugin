function normalizeJsonValue(
  value: unknown,
  ancestors: Set<object>,
  inArray: boolean
): unknown {
  if (value === undefined) {
    if (inArray) {
      return null;
    }
    throw new TypeError('Unsupported canonical JSON value: undefined.');
  }
  if (value === null || typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'string') {
    return normalizeJsonString(value);
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new ValueError(
        'Canonical JSON does not permit non-finite numbers.'
      );
    }
    if (!Number.isSafeInteger(value) || Object.is(value, -0)) {
      throw new ValueError(
        'Canonical JSON numbers must be safe integers and cannot be negative zero.'
      );
    }
    return value;
  }
  if (
    typeof value === 'bigint' ||
    typeof value === 'function' ||
    typeof value === 'symbol'
  ) {
    throw new TypeError(`Unsupported canonical JSON value: ${typeof value}.`);
  }
  if (typeof value !== 'object') {
    throw new TypeError('Unsupported canonical JSON value.');
  }
  if (ancestors.has(value)) {
    throw new TypeError('Canonical JSON does not permit cyclic objects.');
  }

  ancestors.add(value);
  try {
    if (Array.isArray(value)) {
      return value.map(item => normalizeJsonValue(item, ancestors, true));
    }

    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      throw new TypeError('Unsupported non-plain canonical JSON object.');
    }
    if (Object.getOwnPropertySymbols(value).length > 0) {
      throw new TypeError('Unsupported symbol canonical JSON object key.');
    }

    const normalized: Record<string, unknown> = Object.create(null);
    const originals = new Map<string, string>();
    const source = value as Record<string, unknown>;
    for (const originalKey of Object.keys(source)) {
      const normalizedKey = normalizeJsonString(originalKey);
      const prior = originals.get(normalizedKey);
      if (prior !== undefined && prior !== originalKey) {
        throw new Error(
          'Canonical JSON object keys collide after NFC normalization.'
        );
      }
      originals.set(normalizedKey, originalKey);
      const item = source[originalKey];
      if (item === undefined) {
        continue;
      }
      normalized[normalizedKey] = normalizeJsonValue(item, ancestors, false);
    }

    const sorted: Record<string, unknown> = Object.create(null);
    for (const key of Object.keys(normalized).sort(compareUnicodeCodePoints)) {
      sorted[key] = normalized[key];
    }
    return sorted;
  } finally {
    ancestors.delete(value);
  }
}

function normalizeJsonString(value: string): string {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const nextCodeUnit = value.charCodeAt(index + 1);
      if (
        index + 1 >= value.length ||
        nextCodeUnit < 0xdc00 ||
        nextCodeUnit > 0xdfff
      ) {
        throw new ValueError(
          'Canonical JSON strings cannot contain unpaired UTF-16 surrogates.'
        );
      }
      index += 1;
      continue;
    }
    if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      throw new ValueError(
        'Canonical JSON strings cannot contain unpaired UTF-16 surrogates.'
      );
    }
  }
  return value.normalize('NFC');
}

function compareUnicodeCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, character => character.codePointAt(0)!);
  const rightPoints = Array.from(right, character => character.codePointAt(0)!);
  const sharedLength = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < sharedLength; index += 1) {
    if (leftPoints[index] !== rightPoints[index]) {
      return leftPoints[index] - rightPoints[index];
    }
  }
  return leftPoints.length - rightPoints.length;
}

class ValueError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ValueError';
  }
}

export function canonicalStringify(value: unknown): string {
  const normalized = normalizeJsonValue(value, new Set<object>(), false);
  return JSON.stringify(normalized);
}

export async function sha256Json(
  value: unknown,
  subtle: SubtleCrypto = globalThis.crypto.subtle
): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalStringify(value));
  const digest = await subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), byte =>
    byte.toString(16).padStart(2, '0')
  ).join('');
}
