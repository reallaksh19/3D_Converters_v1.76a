// Standalone XML→CII run parity
// -----------------------------
// The parent Model Converters workflow enriches the XML in the browser
// (process data, staged-support restraints with CMPSUPGAP gaps, DTXR
// annotations, weight master matches, valve/flange split renumbering) BEFORE
// the Python conversion runs. The standalone engine used to hand the raw
// source straight to Python, so temperatures stayed at the -100000 sentinel,
// FluidDensity stayed 0 and node weights never propagated into the enriched
// XML or the CII. This module reuses the parent's enrichment core so the
// standalone run carries the same facts the preview tabs showed.

import { enrichStandaloneDtxrAnnotations } from './xml-cii-standalone-dtxr-enrichment.js';

const PARITY_MODULE_URL = new URL(
  '../model-converters/converters/xmltocii2019_helper/enrichment-run-parity.js',
  import.meta.url,
);

function text(value) { return value === undefined || value === null ? '' : String(value); }

function parseConfig(raw) {
  try {
    const value = JSON.parse(text(raw) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

function localName(node) { return text(node?.localName || node?.nodeName).replace(/^.*:/, ''); }
function childrenByName(parent, name) { return [...(parent?.childNodes || [])].filter((node) => node.nodeType === 1 && localName(node) === name); }
function firstChild(parent, name) { return childrenByName(parent, name)[0] || null; }
function childText(parent, name) { return text(firstChild(parent, name)?.textContent).trim(); }

function ensureChild(document, parent, name) {
  let element = firstChild(parent, name);
  if (element) return element;
  element = parent?.namespaceURI ? document.createElementNS(parent.namespaceURI, name) : document.createElement(name);
  parent.appendChild(element);
  return element;
}

function setChildText(document, parent, name, value) {
  const clean = text(value).trim();
  if (!clean) return false;
  ensureChild(document, parent, name).textContent = clean;
  return true;
}

function numeric(value) {
  const n = Number(text(value).replace(/[^0-9.+-eE]/g, ''));
  return Number.isFinite(n) ? n : null;
}

// Overwrites branch process blocks from the standalone dry-run preview rows —
// including rows resolved via the Similar-Linekey fuzzy fallback, which the
// parent's own resolver does not know about. This guarantees the enriched XML
// carries exactly the values the standalone Preview tab showed.
const WEIGHT_BEARING_TYPES = new Set(['FLAN', 'VALV', 'RIGID', 'INST']);

function mappedNodeWeights(nodeRows, config) {
  const weights = new Map();
  for (const nr of nodeRows || []) {
    if (!WEIGHT_BEARING_TYPES.has(text(nr.componentType).toUpperCase())) continue;
    const override = Number(config?.overrides?.rigidWeight?.[nr.key]);
    const auto = nr.weightMatch && !nr.weightMatch.zeroFallback ? Number(nr.weightMatch.weight) : NaN;
    const weight = Number.isFinite(override) && override > 0 ? override : (Number.isFinite(auto) && auto > 0 ? auto : null);
    if (weight !== null) weights.set(`${text(nr.branchName)}|${text(nr.nodeNumber)}`, weight);
  }
  return weights;
}

async function applyStandaloneProcessParity(xmlText, config, stagedJsonText, diagnostics) {
  if (typeof DOMParser === 'undefined' || typeof XMLSerializer === 'undefined') return xmlText;
  const { xmlCiiDryRunPreview } = await import('./ui-adapted/xml-cii-adapted-preview-dryrun.js');
  const { branchRows, nodeRows } = xmlCiiDryRunPreview(xmlText, config, stagedJsonText || '');
  if (!branchRows.length) return xmlText;
  const nodeWeights = mappedNodeWeights(nodeRows, config);
  const byBranch = new Map(branchRows.map((row) => [text(row.branchName), row]));
  const document = new DOMParser().parseFromString(xmlText, 'application/xml');
  if (document.getElementsByTagName('parsererror').length) return xmlText;
  let applied = 0;
  for (const branch of [...document.getElementsByTagName('Branch')]) {
    const row = byBranch.get(childText(branch, 'Branchname'));
    if (!row) continue;
    const pressure = ensureChild(document, branch, 'Pressure');
    const temperature = ensureChild(document, branch, 'Temperature');
    if (numeric(row.p1) !== null && setChildText(document, pressure, 'Pressure1', row.p1)) applied += 1;
    if (numeric(row.hydroPressure) !== null && setChildText(document, pressure, 'HydroPressure', row.hydroPressure)) applied += 1;
    if (numeric(row.t1) !== null && setChildText(document, temperature, 'Temperature1', row.t1)) applied += 1;
    if (numeric(row.t2) !== null && setChildText(document, temperature, 'Temperature2', row.t2)) applied += 1;
    if (numeric(row.t3) !== null && setChildText(document, temperature, 'Temperature3', row.t3)) applied += 1;
    if (numeric(row.density) !== null && setChildText(document, branch, 'FluidDensity', row.density)) applied += 1;
    // Node facts (material / material code / corrosion / wall / rating) —
    // mirrors the parent's setBranchNodeFacts so the resolved values reach
    // the enriched XML and then the CII material/allowable fields.
    if (setChildText(document, branch, 'MaterialNumber', row.materialCode)) applied += 1;
    for (const node of childrenByName(branch, 'Node')) {
      if (setChildText(document, node, 'PipingClass', row.pipingClass)) applied += 1;
      if (setChildText(document, node, 'Rating', row.rating)) applied += 1;
      if (setChildText(document, node, 'MaterialName', row.material)) applied += 1;
      if (setChildText(document, node, 'MaterialCode', row.materialCode)) applied += 1;
      if (numeric(row.wallThickness) !== null && setChildText(document, node, 'WallThickness', row.wallThickness)) applied += 1;
      if (numeric(row.corrosion) !== null && setChildText(document, node, 'CorrosionAllowance', row.corrosion)) applied += 1;
      // Mapped weight-match weights (auto match or saved override) — written
      // only when the node has no real weight yet.
      const mapped = nodeWeights.get(`${childText(branch, 'Branchname')}|${childText(node, 'NodeNumber')}`);
      const existingWeight = numeric(childText(node, 'Weight'));
      if (mapped !== undefined && (existingWeight === null || existingWeight === 0) && setChildText(document, node, 'Weight', mapped)) applied += 1;
    }
  }
  if (!applied) return xmlText;
  diagnostics.push({ type: 'standalone-process-parity-applied', fields: applied, message: 'Branch process blocks were synchronised from the standalone preview resolver (including Similar-Linekey fuzzy matches).' });
  return new XMLSerializer().serializeToString(document);
}

const WEIGHT_MATCH_MODULE_URL = new URL(
  '../model-converters/converters/xmltocii2019_helper/weight-match-renderer.js',
  import.meta.url,
);

// Loads the parent XML→CII(2019) weight-match phase + enrichment pair so the
// standalone Weight Match tab can reuse them (identical lengths, DTXR
// resolution, split node numbers, and candidate ranking).
export async function loadParentWeightMatchModules() {
  const [renderer, parity] = await Promise.all([
    import(WEIGHT_MATCH_MODULE_URL.href),
    import(PARITY_MODULE_URL.href),
  ]);
  return { bindXmlCiiWeightMatchPhase: renderer.bindXmlCiiWeightMatchPhase, enrichXmlForCii2019: parity.enrichXmlForCii2019 };
}

export function standaloneParityOptions(supportConfigJson, options = {}) {
  const config = parseConfig(supportConfigJson);
  const split = options.splitCondensedValveFlange !== undefined
    ? options.splitCondensedValveFlange !== false
    : (config.splitCondensedValveFlange !== false);
  return {
    supportConfigJson: text(supportConfigJson) || '{}',
    splitCondensedValveFlange: split,
    split_condensed_valve_flange: split,
    condenseRigidXsd: config.condenseRigidXsd === true || config.condense_rigid_xsd === true,
  };
}

// Enrich the raw XML source with the parent enrichment core, the standalone
// authoritative DTXR position-group ledger, and finally the standalone process
// parity pass. The DTXR ledger is deliberately applied after the parent core so
// the exact XML sent to Python contains the same DTXR_POS/DTXR_PS evidence that
// the Resolver / JSON Trace screen reports.
export async function enrichStandaloneXmlForRun(job, extraOptions = {}) {
  const sourceText = text(job?.sourceText);
  const out = { xmlText: sourceText, applied: false, diagnostics: [], stats: {}, logs: [], error: null };
  if (!sourceText.trim() || typeof DOMParser === 'undefined') return out;
  try {
    const parity = await import(PARITY_MODULE_URL.href);
    const options = { ...standaloneParityOptions(job.supportConfigJson, job.options || {}), ...extraOptions };
    const enriched = await parity.enrichXmlForCii2019(sourceText, text(job.stagedJsonText), options);
    const config = parseConfig(options.supportConfigJson);
    const dtxr = enrichStandaloneDtxrAnnotations(enriched.xmlText || sourceText, text(job.stagedJsonText), config);
    out.diagnostics = [
      ...(Array.isArray(enriched.diagnostics) ? enriched.diagnostics : []),
      ...(Array.isArray(dtxr.diagnostics) ? dtxr.diagnostics : []),
    ];
    out.stats = {
      ...(enriched.stats && typeof enriched.stats === 'object' ? enriched.stats : {}),
      ...(dtxr.stats && typeof dtxr.stats === 'object' ? dtxr.stats : {}),
    };
    out.xmlText = await applyStandaloneProcessParity(dtxr.text || enriched.xmlText || sourceText, config, text(job.stagedJsonText), out.diagnostics);
    out.applied = true;
    out.logs.push(`Standalone run parity applied: ${out.stats.weightAnnotations || 0} weight annotation(s), ${out.stats.processAnnotations || 0} process annotation(s), ${out.stats.stagedSupportsMapped || 0} staged support(s), ${out.stats.dtxrPosAnnotations || 0} DTXR_POS annotation(s), ${out.stats.dtxrPsAnnotations || 0} DTXR_PS annotation(s), ${out.stats.splitCondensedRigidNodes || 0} renumbered node(s).`);
  } catch (error) {
    out.error = text(error?.message || error);
    out.logs.push(`Standalone run parity skipped: ${out.error}`);
  }
  return out;
}
