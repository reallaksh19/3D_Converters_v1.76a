/**
 * Functionality: resolves a material code from the editable Piping Class ↔
 * Material Code configuration without reading files or mutating configuration.
 * Parameters: effective piping class and the visible converter configuration.
 * Output: the highest-confidence unambiguous match, or null when no safe match
 * exists. Exact rows outrank normalized-suffix and wildcard rows.
 */

import { DEFAULT_PIPING_CLASS_MATERIAL_CODE_ROWS } from './default-piping-class-material-code-rows.js';

function text(value) {
  return String(value ?? '').trim();
}

function normalizeClass(value) {
  return text(value).toUpperCase().replace(/\s+/g, '');
}

function baseClass(value) {
  const normalized = normalizeClass(value);
  const match = normalized.match(/^(\d+)[A-Z].*$/);
  return match ? match[1] : normalized;
}

function wildcardExpression(pattern) {
  const escaped = normalizeClass(pattern).replace(/[.+^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^${escaped.replace(/\?/g, '.').replace(/\*/g, '.*')}$`);
}

function rowMatch(row, effectiveClass, allowSuffixNormalization) {
  const pattern = normalizeClass(row?.pipingClass);
  if (!pattern) return null;
  if (pattern === effectiveClass) return { method: 'exact', rank: 3, specificity: pattern.length };
  const effectiveBase = allowSuffixNormalization ? baseClass(effectiveClass) : effectiveClass;
  if (allowSuffixNormalization && effectiveBase !== effectiveClass && pattern === effectiveBase) {
    return { method: 'normalized-suffix', rank: 2, specificity: pattern.length };
  }
  if (!pattern.includes('*') && !pattern.includes('?')) return null;
  if (!wildcardExpression(pattern).test(effectiveClass) && !wildcardExpression(pattern).test(effectiveBase)) return null;
  const specificity = pattern.replace(/[?*]/g, '').length;
  return { method: 'wildcard', rank: 1, specificity };
}

function compareCandidates(left, right) {
  return right.rank - left.rank
    || right.specificity - left.specificity
    || right.confidence - left.confidence
    || left.index - right.index;
}

function sameResolutionTier(left, right) {
  return left.rank === right.rank
    && left.specificity === right.specificity
    && left.confidence === right.confidence;
}

export function resolveConfiguredMaterialCode({ pipingClass, config }) {
  const mapConfig = config?.pipingClassMaterialCodeMap;
  if (mapConfig?.enabled === false) return null;
  const useAppDefaults = config?.useDefaultPipingClassMaterialCodeMap === true;
  if (!mapConfig && !useAppDefaults) return null;
  const effectiveClass = normalizeClass(pipingClass);
  if (!effectiveClass) return null;
  const threshold = Number.isFinite(Number(mapConfig?.confidenceThreshold)) ? Number(mapConfig.confidenceThreshold) : 80;
  const rows = Array.isArray(mapConfig?.rows) && mapConfig.rows.length ? mapConfig.rows : (useAppDefaults ? DEFAULT_PIPING_CLASS_MATERIAL_CODE_ROWS : []);
  const candidates = rows.map((row, index) => {
    const matched = rowMatch(row, effectiveClass, mapConfig?.normalizeClassSuffix !== false);
    const confidence = Number(row?.confidence);
    const materialCode = text(row?.materialCode);
    if (!matched || !materialCode || !Number.isFinite(confidence) || confidence < threshold) return null;
    return { ...matched, confidence, materialCode, row, index };
  }).filter(Boolean).sort(compareCandidates);
  const best = candidates[0];
  if (!best) return null;
  const conflictingCodes = candidates
    .filter((candidate) => sameResolutionTier(candidate, best))
    .map((candidate) => candidate.materialCode)
    .filter((code, index, all) => all.indexOf(code) === index);
  if (conflictingCodes.length > 1) return null;
  return {
    materialCode: best.materialCode,
    materialName: text(best.row.materialName),
    materialCategory: text(best.row.materialCategory),
    confidence: best.confidence,
    method: best.method,
    matchedRow: best.row,
  };
}
