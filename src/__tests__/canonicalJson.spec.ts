import { webcrypto } from 'node:crypto';

import { canonicalStringify, sha256Json } from '../utils/canonicalJson';

const HASH_FIXTURE = {
  segments: [{ started_at: '2026-07-28T10:00:00Z', session_seq: 1 }],
  last_sequence: 1,
  first_sequence: 1
};

describe('canonical JSON', () => {
  it('matches the compact sorted cross-language fixture', () => {
    expect(canonicalStringify(HASH_FIXTURE)).toBe(
      '{"first_sequence":1,"last_sequence":1,"segments":[{"session_seq":1,"started_at":"2026-07-28T10:00:00Z"}]}'
    );
  });

  it('normalizes every key and string to NFC and preserves array order', () => {
    expect(
      canonicalStringify({
        z: ['e\u0301', 'second'],
        ñ: { value: 'n\u0303' },
        a: 'first'
      })
    ).toBe('{"a":"first","z":["é","second"],"ñ":{"value":"ñ"}}');
  });

  it('sorts keys by Unicode code point like Python rather than UTF-16 units', () => {
    expect(canonicalStringify({ ['\u{10000}']: 2, ['\uffff']: 1 })).toBe(
      '{"￿":1,"𐀀":2}'
    );
  });

  it('preserves an own __proto__ JSON key with Python-identical bytes', () => {
    const fixture = JSON.parse('{"__proto__":{"synthetic":1},"a":2}');

    expect(Object.prototype.hasOwnProperty.call(fixture, '__proto__')).toBe(
      true
    );
    expect(canonicalStringify(fixture)).toBe(
      '{"__proto__":{"synthetic":1},"a":2}'
    );
  });

  it.each([
    ['high surrogate in a value', '{"value":"\\ud800"}'],
    ['low surrogate in a value', '{"value":"\\udc00"}'],
    ['high surrogate in a key', '{"\\ud800":"synthetic"}'],
    ['low surrogate in a key', '{"\\udc00":"synthetic"}'],
    ['high surrogate in an array', '["\\ud800"]'],
    ['low surrogate in an array', '["\\udc00"]']
  ])('rejects an unpaired %s before canonical hashing', async (_label, raw) => {
    const fixture = JSON.parse(raw);

    expect(() => canonicalStringify(fixture)).toThrow(/surrogate/i);
    await expect(
      sha256Json(fixture, webcrypto.subtle as SubtleCrypto)
    ).rejects.toThrow(/surrogate/i);
  });

  it('accepts a valid surrogate pair as one Unicode scalar value', () => {
    const fixture = JSON.parse('{"value":"\\ud83d\\ude00"}');
    expect(canonicalStringify(fixture)).toBe('{"value":"😀"}');
  });

  it('omits undefined object properties and converts undefined array items to null', () => {
    expect(
      canonicalStringify({
        omitted: undefined,
        values: [1, undefined, 3]
      })
    ).toBe('{"values":[1,null,3]}');
  });

  it('rejects keys that collide after NFC normalization', () => {
    expect(() => canonicalStringify({ é: 1, ['e\u0301']: 2 })).toThrow(
      /collide/i
    );
  });

  it('rejects normalized key collisions even when one value is undefined', () => {
    expect(() => canonicalStringify({ é: undefined, ['e\u0301']: 2 })).toThrow(
      /collide/i
    );
  });

  it.each([NaN, Infinity, -Infinity])('rejects non-finite number %s', value => {
    expect(() => canonicalStringify({ value })).toThrow(/finite/i);
  });

  it.each([
    ['fractional', 1.5],
    ['negative zero', -0],
    ['positive unsafe integer', Number.MAX_SAFE_INTEGER + 1],
    ['negative unsafe integer', Number.MIN_SAFE_INTEGER - 1]
  ])('rejects %s before canonical hashing', async (_label, value) => {
    expect(() => canonicalStringify({ value })).toThrow(/safe integer/i);
    await expect(
      sha256Json({ value }, webcrypto.subtle as SubtleCrypto)
    ).rejects.toThrow(/safe integer/i);
  });

  it('supports signed safe integers with Python-identical bytes', () => {
    expect(
      canonicalStringify({
        minimum: Number.MIN_SAFE_INTEGER,
        negative: -1,
        positive: Number.MAX_SAFE_INTEGER,
        zero: 0
      })
    ).toBe(
      '{"minimum":-9007199254740991,"negative":-1,"positive":9007199254740991,"zero":0}'
    );
  });

  it.each([
    BigInt(1),
    () => undefined,
    Symbol('private'),
    new Date('2026-07-28T10:00:00Z'),
    new Map([['key', 'value']])
  ])('rejects unsupported value %#', value => {
    expect(() => canonicalStringify({ value })).toThrow(/unsupported/i);
  });

  it('rejects cyclic objects', () => {
    const value: { self?: unknown } = {};
    value.self = value;
    expect(() => canonicalStringify(value)).toThrow(/cyclic/i);
  });

  it('hashes the canonical UTF-8 bytes with injected SubtleCrypto', async () => {
    await expect(
      sha256Json(HASH_FIXTURE, webcrypto.subtle as SubtleCrypto)
    ).resolves.toBe(
      '86c432e46d6ac104d36234306439e5bad30f64abef0574ecc90aadbe21ad5095'
    );
  });
});
