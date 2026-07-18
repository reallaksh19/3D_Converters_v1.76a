/**
 * Deterministic topology artifact hashing.
 * Object keys are sorted, array order is preserved, and selected self-hash keys
 * are omitted before canonical JSON serialization. The implementation is
 * browser-safe and does not depend on Node crypto.
 */

const DEFAULT_OMIT_KEYS = Object.freeze([
  'canonicalTopologyHash',
  'topologyTraceLedgerHash',
  'svgSceneHash',
  'parsedInputXmlTopologyHash',
  'topologyParityReportHash',
]);

/** @param {unknown} value @param {Set<string>} omitKeys @returns {unknown} */
function canonicalValue(value, omitKeys) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError(`Cannot hash non-finite number: ${value}.`);
    return Object.is(value, -0) ? 0 : value;
  }
  if (Array.isArray(value)) return value.map((item) => canonicalValue(item, omitKeys));
  if (value && typeof value === 'object') {
    const record = /** @type {Record<string, unknown>} */ (value);
    const result = {};
    for (const key of Object.keys(record).sort()) {
      if (omitKeys.has(key) || record[key] === undefined) continue;
      result[key] = canonicalValue(record[key], omitKeys);
    }
    return result;
  }
  throw new TypeError(`Unsupported value in deterministic topology hash: ${typeof value}.`);
}

/** @param {unknown} value @param {{omitKeys?:string[]}} [options] @returns {string} */
export function canonicalJson(value, options = {}) {
  const omitKeys = new Set(options.omitKeys ?? DEFAULT_OMIT_KEYS);
  return JSON.stringify(canonicalValue(value, omitKeys));
}

/** @param {string} text @returns {string} */
export function hashText(text) {
  const bytes = new TextEncoder().encode(String(text ?? ''));
  let hash = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  const mask = 0xffffffffffffffffn;
  for (const byte of bytes) {
    hash ^= BigInt(byte);
    hash = (hash * prime) & mask;
  }
  return `fnv1a64:${hash.toString(16).padStart(16, '0')}`;
}

/** @param {unknown} value @param {{omitKeys?:string[]}} [options] @returns {string} */
export function deterministicHash(value, options = {}) {
  return hashText(canonicalJson(value, options));
}

/** @param {Record<string, unknown>} artifact @param {string} fieldName @returns {Readonly<Record<string, unknown>>} */
export function stampArtifactHash(artifact, fieldName) {
  if (!fieldName) throw new TypeError('A topology artifact hash field name is required.');
  const hash = deterministicHash(artifact, { omitKeys: [...DEFAULT_OMIT_KEYS, fieldName] });
  return Object.freeze({ ...artifact, [fieldName]: hash });
}

export const TOPOLOGY_HASH_ALGORITHM = 'fnv1a64-canonical-json-v1';
export const _test = Object.freeze({ canonicalValue, DEFAULT_OMIT_KEYS });
