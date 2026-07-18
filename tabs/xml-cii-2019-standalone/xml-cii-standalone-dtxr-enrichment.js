import {
  buildStandaloneDtxrPositionGroups,
  buildStandaloneDtxrResolutionLedger,
  canonicalStandaloneDtxrBranch,
  standaloneDtxrBranchRelationship,
} from './xml-cii-trace-resolution-ledger.js';

function text(value) {
  return value === undefined || value === null ? '' : String(value).trim();
}

function localName(node) {
  return text(node?.localName || node?.nodeName).replace(/^.*:/, '');
}

function childrenByName(parent, name) {
  return [...(parent?.childNodes || parent?.children || [])]
    .filter((node) => node?.nodeType === undefined || node.nodeType === 1)
    .filter((node) => localName(node) === name);
}

function firstChild(parent, name) {
  return childrenByName(parent, name)[0] || null;
}

function ensureChild(document, parent, name) {
  let child = firstChild(parent, name);
  if (child) return child;
  child = parent?.namespaceURI
    ? document.createElementNS(parent.namespaceURI, name)
    : document.createElement(name);
  parent.appendChild(child);
  return child;
}

function setChildText(document, parent, name, value) {
  const clean = text(value);
  if (!clean) return false;
  const child = ensureChild(document, parent, name);
  const changed = text(child.textContent) !== clean;
  child.textContent = clean;
  return changed;
}

function parserFailed(document) {
  return !!(document?.querySelector?.('parsererror') || document?.getElementsByTagName?.('parsererror')?.length);
}

function recordsByXmlIndex(records) {
  return new Map((records || []).map((record) => [Number(record.xmlIndex), record]));
}

export function enrichStandaloneDtxrAnnotations(xmlText, stagedJsonText, config = {}) {
  const source = String(xmlText ?? '');
  const stats = {
    dtxrPositionGroups: 0,
    dtxrPosAnnotations: 0,
    dtxrPsAnnotations: 0,
    dtxrUnresolvedNodes: 0,
    dtxrAmbiguousNodes: 0,
  };
  const ledgerResult = buildStandaloneDtxrResolutionLedger(source, stagedJsonText, config);
  stats.dtxrPositionGroups = ledgerResult.groups.length;
  stats.dtxrUnresolvedNodes = ledgerResult.records.filter((record) => record.status === 'UNRESOLVED').length;
  stats.dtxrAmbiguousNodes = ledgerResult.records.filter((record) => record.status === 'AMBIGUOUS_REVIEW').length;

  if (!source.trim() || !text(stagedJsonText)) {
    return { text: source, diagnostics: ledgerResult.diagnostics, stats, ledger: ledgerResult.records, ledgerResult };
  }
  if (typeof DOMParser === 'undefined' || typeof XMLSerializer === 'undefined') {
    return {
      text: source,
      diagnostics: [...ledgerResult.diagnostics, { type: 'standalone-dtxr-enrichment-skipped', reason: 'DOM unavailable' }],
      stats,
      ledger: ledgerResult.records,
      ledgerResult,
    };
  }

  const document = new DOMParser().parseFromString(source, 'application/xml');
  if (parserFailed(document)) {
    return {
      text: source,
      diagnostics: [...ledgerResult.diagnostics, { type: 'standalone-dtxr-enrichment-skipped', reason: 'XML parse failed' }],
      stats,
      ledger: ledgerResult.records,
      ledgerResult,
    };
  }

  const byIndex = recordsByXmlIndex(ledgerResult.records);
  let xmlIndex = 0;
  for (const branch of [...document.getElementsByTagName('Branch')]) {
    for (const node of childrenByName(branch, 'Node')) {
      xmlIndex += 1;
      const resolved = byIndex.get(xmlIndex);
      if (!resolved) continue;
      if (resolved.dtxrPosValue) {
        if (setChildText(document, node, 'DTXR_POS', resolved.dtxrPosValue)) stats.dtxrPosAnnotations += 1;
      }
      if (resolved.dtxrPsValue) {
        if (setChildText(document, node, 'DTXR_PS', resolved.dtxrPsValue)) stats.dtxrPsAnnotations += 1;
      }
      if (resolved.effectiveSource === 'DTXR_POS') {
        setChildText(document, node, 'DTXR_SOURCE', 'staged-json-position-group');
      } else if (resolved.effectiveSource === 'DTXR_PS_FALLBACK') {
        setChildText(document, node, 'DTXR_SOURCE', 'staged-json-ps-fallback');
      }
    }
  }

  return {
    text: new XMLSerializer().serializeToString(document),
    diagnostics: ledgerResult.diagnostics,
    stats,
    ledger: ledgerResult.records,
    ledgerResult,
  };
}

export {
  buildStandaloneDtxrPositionGroups,
  buildStandaloneDtxrResolutionLedger,
  canonicalStandaloneDtxrBranch,
  standaloneDtxrBranchRelationship,
};
