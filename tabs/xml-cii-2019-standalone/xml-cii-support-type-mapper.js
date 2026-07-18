const KINDS = Object.freeze(['REST', 'GUIDE', 'LINESTOP', 'ANCHOR']);

const DEFAULT_ROWS = Object.freeze([
  { kind: 'REST', aliases: ['Shoe', 'Pipe Rest', 'Wear Pad', 'Rest'], ciiKind: 'REST_STYLE_CII_SUPPORT', ciiPreview: '+Y/restraint support preview' },
  { kind: 'GUIDE', aliases: ['Guide', 'PG', 'Lateral Guide'], ciiKind: 'GUIDE_STYLE_CII_SUPPORT', ciiPreview: 'lateral guide preview' },
  { kind: 'LINESTOP', aliases: ['Line Stop', 'Directional Anchor', 'LS', 'Axial Stop'], ciiKind: 'LINESTOP_STYLE_CII_SUPPORT', ciiPreview: 'axial stop preview' },
  { kind: 'ANCHOR', aliases: ['Anchor', 'Fixed'], ciiKind: 'ANCHOR_STYLE_CII_SUPPORT', ciiPreview: 'anchor/fixed support preview' },
]);

function array(value) { return Array.isArray(value) ? value : []; }
function object(value) { return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; }
function text(value) { return String(value ?? '').trim(); }
function norm(value) { return text(value).toUpperCase().replace(/[^A-Z0-9]+/g, ''); }

function parseJson(value) {
  try { return object(JSON.parse(text(value) || '{}')); }
  catch { return {}; }
}

function splitAliases(value) {
  if (Array.isArray(value)) return value.map(text).filter(Boolean);
  return text(value).split(/[|,\n]/).map(text).filter(Boolean);
}

function defaultRow(kind) {
  return DEFAULT_ROWS.find((row) => row.kind === kind) || DEFAULT_ROWS[0];
}

function normalizeMapperRow(row = {}, index = 0) {
  const fallback = defaultRow(String(row.kind || '').toUpperCase());
  const kind = KINDS.includes(String(row.kind || '').toUpperCase()) ? String(row.kind).toUpperCase() : fallback.kind;
  const base = defaultRow(kind);
  return {
    rowId: text(row.rowId || row.id) || `${kind.toLowerCase()}-${index + 1}`,
    kind,
    aliases: splitAliases(row.aliases || row.patterns || row.detectionText).length ? splitAliases(row.aliases || row.patterns || row.detectionText) : [...base.aliases],
    ciiKind: text(row.ciiKind || row.ciiOutputKind) || base.ciiKind,
    ciiPreview: text(row.ciiPreview || row.preview) || base.ciiPreview,
    source: text(row.source) || 'support-type-mapper',
  };
}

function rowsFromConfig(config) {
  const direct = array(config.supportTypeMapper?.rows).length ? config.supportTypeMapper.rows : config.supportTypeMapperRows;
  const rows = array(direct).length ? array(direct) : DEFAULT_ROWS;
  return rows.map(normalizeMapperRow);
}

function aliasKey(row, alias) { return `${norm(alias)}::${row.kind}`; }

function duplicateDiagnostics(rows) {
  const seen = new Map();
  const diagnostics = [];
  for (const row of rows) for (const alias of row.aliases) {
    const key = aliasKey(row, alias);
    if (seen.has(key)) diagnostics.push({ level: 'warning', source: 'support-type-mapper', type: 'duplicate-alias', kind: row.kind, message: `Duplicate alias ${alias} for ${row.kind}.` });
    seen.set(key, true);
  }
  return diagnostics;
}

export function createDefaultSupportTypeMapperConfig() {
  return DEFAULT_ROWS.map((row, index) => normalizeMapperRow(row, index));
}

export function parseSupportTypeMapperConfig(input = {}) {
  const raw = typeof input === 'string' ? input : input.supportConfigJson;
  const cfg = object(input.supportConfig);
  const config = Object.keys(cfg).length ? cfg : parseJson(raw);
  const mapperRows = rowsFromConfig(config);
  return { mapperRows, diagnostics: duplicateDiagnostics(mapperRows), raw: { config } };
}

function evidenceRows(input = {}) {
  const direct = array(input.supportTexts).map((value, index) => ({ source: `supportTexts[${index}]`, text: value }));
  const blocks = [input.testInput, input.manualRestraintsText, input.manualText, input.elementSideLoadText, input.dtxrText];
  return [...direct, ...blocks.flatMap(extractEvidenceLines)].filter((row) => text(row.text));
}

function extractEvidenceLines(value) {
  return text(value).split(/\r?\n/).map((line, index) => evidenceLine(line, index)).filter(Boolean);
}

function evidenceLine(line, index) {
  const cleaned = text(line);
  if (!cleaned) return null;
  const match = cleaned.match(/^\s*(DTXR_POS|DTXR_PS|RESTRAINT|SUPPORT)\s*=\s*(.+)$/i);
  return { source: match ? match[1].toUpperCase() : `line-${index + 1}`, text: match ? match[2] : cleaned };
}

function rowMatches(evidence, row) {
  const normalized = norm(evidence);
  return row.aliases.filter((alias) => normalized.includes(norm(alias)));
}

export function classifySupportText(value, mapperRows = createDefaultSupportTypeMapperConfig()) {
  const matches = mapperRows.map((row) => ({ row, aliases: rowMatches(value, row) })).filter((entry) => entry.aliases.length);
  const kinds = [...new Set(matches.map((entry) => entry.row.kind))];
  if (!matches.length) return { status: 'unmatched', supportKind: '', ciiKind: '', ciiPreview: '', matches };
  if (kinds.length > 1) return { status: 'ambiguous', supportKind: kinds.join('|'), ciiKind: '', ciiPreview: '', matches };
  const row = matches[0].row;
  return { status: 'matched', supportKind: row.kind, ciiKind: row.ciiKind, ciiPreview: row.ciiPreview, matches };
}

function previewRow(evidence, index, mapperRows) {
  const result = classifySupportText(evidence.text, mapperRows);
  return {
    rowId: `preview-${index + 1}`,
    source: evidence.source,
    evidenceText: evidence.text,
    supportKind: result.supportKind,
    ciiKind: result.ciiKind,
    ciiPreview: result.ciiPreview,
    status: result.status,
    matchedAliases: result.matches.flatMap((entry) => entry.aliases),
  };
}

function previewDiagnostics(previewRows) {
  return previewRows.filter((row) => row.status !== 'matched').map((row) => ({
    level: row.status === 'ambiguous' ? 'warning' : 'info',
    source: 'support-type-mapper',
    type: row.status === 'ambiguous' ? 'ambiguous-support-kind' : 'unmatched-support-text',
    evidenceText: row.evidenceText,
    message: row.status === 'ambiguous' ? `Ambiguous support text matched ${row.supportKind}.` : 'Support text did not match a mapper row.',
  }));
}

function serializeMapperConfig(input, mapperRows) {
  const config = parseJson(input.supportConfigJson);
  config.supportTypeMapper = {
    schema: 'xml-cii-2019-support-type-mapper/v1',
    rows: mapperRows.map(({ kind, aliases, ciiKind, ciiPreview }) => ({ kind, aliases, ciiKind, ciiPreview })),
  };
  return JSON.stringify(config, null, 2);
}

export function runStandaloneSupportTypeMapper(input = {}) {
  const parsed = parseSupportTypeMapperConfig(input);
  const mapperRows = array(input.mapperRows).length ? input.mapperRows.map(normalizeMapperRow) : parsed.mapperRows;
  const testRows = evidenceRows(input);
  const previewRows = testRows.map((row, index) => previewRow(row, index, mapperRows));
  const diagnostics = [...parsed.diagnostics, ...previewDiagnostics(previewRows)];
  return { mapperRows, testRows, previewRows, diagnostics, supportConfigJson: serializeMapperConfig(input, mapperRows), raw: { sourceKind: input.sourceKind || 'xml' } };
}
