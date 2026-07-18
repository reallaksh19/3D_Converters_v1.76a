function _toText(v) { return String(v ?? '').trim(); }
function _xmlCiiNormalizeHeader(value) { return _toText(value).trim().toUpperCase().replace(/[^A-Z0-9]/g, ''); }
function _xmlCiiWordTokens(value) { return _toText(value).toUpperCase().split(/[^A-Z0-9]+/).map(t => t.trim()).filter(Boolean); }

function _xmlCiiHeaderScore(header, aliases) {
  const headerText = _toText(header).trim();
  const normalizedHeader = _xmlCiiNormalizeHeader(headerText);
  if (!normalizedHeader) return 0;
  let bestScore = 0;
  for (let aliasIndex = 0; aliasIndex < (aliases || []).length; aliasIndex += 1) {
    const alias = aliases[aliasIndex];
    const aliasText = _toText(alias).trim();
    const normalizedAlias = _xmlCiiNormalizeHeader(aliasText);
    const exactScore = aliasIndex === 0 ? 120 : 100;
    if (!normalizedAlias) continue;
    if (normalizedHeader === normalizedAlias) bestScore = Math.max(bestScore, exactScore);
    else if (normalizedHeader.startsWith(normalizedAlias) || normalizedAlias.startsWith(normalizedHeader)) bestScore = Math.max(bestScore, 78);
    else if (normalizedHeader.includes(normalizedAlias) || normalizedAlias.includes(normalizedHeader)) bestScore = Math.max(bestScore, 68);

    const aliasTokens = _xmlCiiWordTokens(aliasText);
    const headerTokens = _xmlCiiWordTokens(headerText);
    const matches = aliasTokens.filter((token) => headerTokens.includes(token)).length;
    if (aliasTokens.length && matches) bestScore = Math.max(bestScore, Math.round((matches / aliasTokens.length) * 62));
  }
  return bestScore;
}

const _XML_CII_LABEL_ROW_KEYWORDS = Object.freeze({
  lineSeqNo:    ['line number', 'line no', 'line no.', 'seq', 'sequence', 'lineno'],
  lineKey1:     ['service', 'line key', 'key 1', 'key1', 'area'],
  lineKey2:     ['line number', 'line no', 'key 2', 'key2'],
  pipingClass:  ['piping class', 'piping_class', 'class', 'spec', 'pipe class'],
  rating:       ['rating', 'pressure class', 'class rating', 'pressure rating', 'press rating', 'press. rating', 'class/rating', 'rating/class'],
  material:     ['material', 'material_name', 'material name'],
  convertedBore:['bore', 'size', 'dn', 'nps', 'nb', 'nominal pipe', 'nominal bore', 'pipe size'],
  p1:           ['p1', 'design pr', 'design pressure', 'operating pressure', 'design cond', 'p1 / design pressure', 'pressure max'],
  t1:           ['t1', 'design temp', 'design temperature', 'operating temp', 't1 (c)', 't1 (ºc)', 'temp max', 'temp max c', 'temp max ºc'],
  t2:           ['t2', 'temp', 'temperature', 't2 (c)', 't2 (ºc)', 'temp. c', 'temp. ºc'],
  t3:           ['t3', 'temp min', 'minimum temp', 'min temp', 'temperature min', 'min', 't3 (c)', 't3 (ºc)', 'temp min c', 'temp min ºc'],
  insThk:       ['insulation', 'ins thk', 'insthk', 'insulation thickness'],
  densityMixed: ['mixed', 'density mixed', 'mixed kg', 'mixed density', 'mixed kg/m3', 'mixed kg/m³'],
  densityGas:   ['gas kg', 'density gas', 'gas density', 'gas kg/m3', 'gas kg/m³'],
  densityLiquid:['liquid kg', 'density liquid', 'density liquid', 'liquid kg/m3', 'liquid kg/m³'],
  phase:        ['phase', 'fluid phase', 'medium phase'],
  hydroPressure:['hydropressure', 'hydro test pressure', 'hydrotest pressure', 'hydro pressure', 'test pressure', 'hydro/test pressure'],
});

function _xmlCiiLabelRowHint(header, rawRows) {
  if (!_toText(header).startsWith('__EMPTY')) return {};
  const SCAN_ROWS = 3;
  const scores = {};
  for (let ri = 0; ri < Math.min(SCAN_ROWS, rawRows.length); ri++) {
    const cellText = _toText(rawRows[ri]?.[header]).toLowerCase().trim();
    if (!cellText || cellText.length > 60) continue;
    for (const [fieldName, keywords] of Object.entries(_XML_CII_LABEL_ROW_KEYWORDS)) {
      for (const kw of keywords) {
        if (cellText === kw || cellText.includes(kw) || kw.includes(cellText)) {
          let score = ri === 0 ? 110 : (ri === 1 ? 90 : 75);
          if (cellText === kw) {
            score += 30; // Exact keyword match bonus
          } else if (cellText.startsWith(kw)) {
            score += 15 + Math.min(kw.length, 30); // Specific startsWith weight
          } else {
            score += Math.min(kw.length, 30); // Specific includes weight
          }
          if (!scores[fieldName] || score > scores[fieldName]) scores[fieldName] = score;
        }
      }
    }
  }
  return scores;
}

function _xmlCiiDataValueScore(header, fieldName, rawRows) {
  if (!_toText(header).startsWith('__EMPTY')) return 0;
  const SAMPLE = 8;
  const values = [];
  for (const row of rawRows) {
    if (values.length >= SAMPLE) break;
    const v = _toText(row?.[header]).trim();
    if (v && v !== header) values.push(v);
  }
  if (!values.length) return 0;
  const n = values.length;
  const numeric = (v) => !Number.isNaN(Number(v)) && v !== '';
  const inRange = (v, lo, hi) => { const x = Number(v); return Number.isFinite(x) && x >= lo && x <= hi; };
  const passRate = (fn) => values.filter(fn).length / n;

  switch (fieldName) {
    case 'lineSeqNo': {
      const p = passRate((v) => /^\d[A-Z0-9]{3,11}$/i.test(v));
      return Math.round(p * 70);
    }
    case 'lineKey1': {
      const p = passRate((v) => /^[A-Z]{1,6}$/.test(v));
      return Math.round(p * 72);
    }
    case 'lineKey2': {
      const p = passRate((v) => /^\d{5,10}$/.test(v));
      return Math.round(p * 72);
    }
    case 'pipingClass': {
      const p = passRate((v) => /^[A-Z0-9]{1,4}[/\-]?[A-Z0-9]{0,6}$/i.test(v) && v.length >= 2 && v.length <= 10 && !numeric(v));
      return Math.round(p * 68);
    }
    case 'material': {
      const p = passRate((v) => /^[A-Z]{1,4}[A-Z0-9]{0,8}$/i.test(v) && !numeric(v) && v.length >= 2);
      return Math.round(p * 65);
    }
    case 'convertedBore': {
      const p = passRate((v) => inRange(v.replace(/"$/, ''), 6, 1200));
      return Math.round(p * 65);
    }
    case 'p1': {
      const p = passRate((v) => inRange(v, 0, 1000));
      return Math.round(p * 62);
    }
    case 't1':
    case 't2':
    case 't3': {
      const p = passRate((v) => inRange(v, -200, 800));
      return Math.round(p * 62);
    }
    case 'insThk': {
      const p = passRate((v) => inRange(v, 0, 500));
      return Math.round(p * 60);
    }
    case 'densityMixed':
    case 'densityGas':
    case 'densityLiquid': {
      const p = passRate((v) => inRange(v, 0.01, 2000));
      return Math.round(p * 60);
    }
    case 'phase': {
      const phases = new Set(['g', 'l', 'm', 'gas', 'liquid', 'mixed', '2p', 'liq', 'vap', 'vapour', 'vapor']);
      const p = passRate((v) => phases.has(v.toLowerCase()));
      return p >= 0.8 ? Math.round(p * 75) : 0;
    }
    case 'rating': {
      const knownRatings = new Set(['150', '300', '600', '900', '1500', '2500']);
      const p = passRate((v) => knownRatings.has(v.replace(/cl|pn|#/gi, '').trim()) || /^(cl|pn)?\s*(\d{2,4})$/i.test(v));
      return Math.round(p * 62);
    }
    default: return 0;
  }
}

export function fuzzyAutoMapFields(headers, fields, rawRows) {
  const safeRows = Array.isArray(rawRows) ? rawRows : [];
  const mapped = {};
  const claimed = new Set();

  const canShare = (fieldName, header) => {
    if (fieldName === 'lineKey2' && mapped['lineSeqNo'] === header) return true;
    return false;
  };

  for (const field of fields) {
    let bestHeader = '';
    let bestScore = 0;
    for (const header of headers) {
      const score = _xmlCiiHeaderScore(header, field.aliases);
      const headerText = _toText(header).trim().toUpperCase();
      if (field.name === 'pipingClass' && headerText === 'CONSTRUCTION CLASS') continue;
      if (score > bestScore) {
        bestHeader = header;
        bestScore = score;
      }
    }
    if (bestScore >= 60) {
      mapped[field.name] = bestHeader;
      claimed.add(bestHeader);
    } else {
      mapped[field.name] = '';
    }
  }

  if (safeRows.length) {
    const labelHints = {};
    for (const header of headers) {
      if (!_toText(header).startsWith('__EMPTY')) continue;
      labelHints[header] = _xmlCiiLabelRowHint(header, safeRows);
    }

    for (const field of fields) {
      if (mapped[field.name]) continue;
      let bestHeader = '';
      let bestScore = 0;
      for (const header of headers) {
        if (!_toText(header).startsWith('__EMPTY')) continue;
        if (claimed.has(header) && !canShare(field.name, header)) continue;
        const labelScore = (labelHints[header] || {})[field.name] || 0;
        if (labelScore > bestScore) {
          bestHeader = header;
          bestScore = labelScore;
        }
      }
      if (bestScore >= 60) {
        mapped[field.name] = bestHeader;
        claimed.add(bestHeader);
      }
    }

    for (const field of fields) {
      if (mapped[field.name]) continue;
      let bestHeader = '';
      let bestScore = 0;
      for (const header of headers) {
        if (!_toText(header).startsWith('__EMPTY')) continue;
        if (claimed.has(header) && !canShare(field.name, header)) continue;
        const dataScore = _xmlCiiDataValueScore(header, field.name, safeRows);
        if (dataScore > bestScore) {
          bestHeader = header;
          bestScore = dataScore;
        }
      }
      if (bestScore >= 60) {
        mapped[field.name] = bestHeader;
        claimed.add(bestHeader);
      }
    }
  }

  return mapped;
}
