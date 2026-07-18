import { enrichInputXmlDocument } from './xml-cii-inputxml-enrichment.js';
import { runXmlCii2019StandaloneEngineJob } from './xml-cii-workflow-engine-client.js';
import { createWorkflowDiagnostics, normalizeWorkflowResult } from './xml-cii-workflow-types.js';
import { applyStandaloneSifFacts } from './xml-cii-standalone-sif-apply.js';
import { enrichStandaloneXmlForRun } from './xml-cii-standalone-run-parity.js';

function parseSupportConfig(raw) {
  try {
    const value = JSON.parse(String(raw ?? '') || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

// Bridges the effective run-state facts (weight overrides, preview/manual matched facts, support
// mapper rules) into the support config the engine actually consumes, so a run reflects the same
// data the preview tabs showed rather than only the raw config JSON.
function applyEffectiveFactsToJob(job) {
  const config = parseSupportConfig(job.supportConfigJson);
  const weightOverrides = job.weightOverrides && typeof job.weightOverrides === 'object' ? job.weightOverrides : {};
  if (Object.keys(weightOverrides).length) {
    config.overrides = { ...(config.overrides || {}), rigidWeight: { ...(config.overrides?.rigidWeight || {}), ...weightOverrides } };
  }
  if (Array.isArray(job.previewFacts) && job.previewFacts.length) config.previewFacts = job.previewFacts;
  if (Array.isArray(job.manualFacts) && job.manualFacts.length) config.manualFacts = job.manualFacts;
  if (Array.isArray(job.supportMapperRules) && job.supportMapperRules.length) config.supportMapperRules = job.supportMapperRules;
  return { ...job, supportConfigJson: JSON.stringify(config, null, 2) };
}

function mergeInputXmlEngineResult(enrichedResult, engineResult, outputMode) {
  const logs = [...(enrichedResult.logs || []), ...(Array.isArray(engineResult.logs) ? engineResult.logs : [])];
  const diagnostics = createWorkflowDiagnostics({
    ...(enrichedResult.diagnostics || {}),
    sourceKind: 'inputxml',
    outputKind: 'enrichedInputXML',
    branch: 'direct-inputxml-enrichment-plus-cii-compatibility',
    engineDiagnostics: engineResult.diagnostics || null,
    warnings: [
      ...((Array.isArray(enrichedResult.diagnostics?.warnings) ? enrichedResult.diagnostics.warnings : [])),
      ...((Array.isArray(engineResult.diagnostics?.warnings) ? engineResult.diagnostics.warnings : [])),
    ],
  });
  if (!engineResult.ok) {
    return normalizeWorkflowResult({ ...enrichedResult, ok: false, diagnostics, logs, error: engineResult.error || 'InputXML CII compatibility route failed.' });
  }
  return normalizeWorkflowResult({
    ...enrichedResult,
    enrichedText: outputMode === 'cii-only' ? '' : enrichedResult.enrichedText,
    enrichedName: outputMode === 'cii-only' ? '' : enrichedResult.enrichedName,
    ciiText: engineResult.ciiText,
    ciiName: engineResult.ciiName,
    diagnostics,
    logs,
  });
}

function normalizeXmlEngineResult(engineResult) {
  const engineDiagnostics = engineResult?.diagnostics && typeof engineResult.diagnostics === 'object' ? engineResult.diagnostics : null;
  const diagnostics = createWorkflowDiagnostics({
    ...(engineDiagnostics || {}),
    sourceKind: 'xml',
    outputKind: 'enrichedXML',
    branch: 'psi116-xml-compatibility-engine',
    engineDiagnostics,
    warnings: Array.isArray(engineDiagnostics?.warnings) ? engineDiagnostics.warnings : [],
  });
  return normalizeWorkflowResult({
    ...engineResult,
    sourceKind: 'xml',
    outputKind: 'enrichedXML',
    diagnostics,
  });
}

export async function runXmlCii2019WorkflowService(job, runtime = {}) {
  const factedJob = applyEffectiveFactsToJob(job);
  if (factedJob.sourceKind === 'inputxml') {
    const enrichedResult = enrichInputXmlDocument(factedJob);
    const sif = applyStandaloneSifFacts(enrichedResult.enrichedText, 'inputxml', factedJob.sifFacts);
    const finalEnrichedResult = { ...enrichedResult, enrichedText: sif.text };
    if (factedJob.options.outputMode === 'enriched-only') return normalizeWorkflowResult(finalEnrichedResult);
    const engineJob = { ...factedJob, sourceText: sif.text, sourceName: enrichedResult.enrichedName, options: { ...factedJob.options, outputMode: 'cii-only' } };
    const engineResult = await runXmlCii2019StandaloneEngineJob(engineJob, runtime);
    return mergeInputXmlEngineResult(finalEnrichedResult, engineResult, factedJob.options.outputMode);
  }
  // Browser-side run parity (same enrichment the parent workflow performs):
  // bakes preview process data, weight master matches, CMPSUPGAP restraint
  // gaps, and split renumbering into the XML before the Python engine runs.
  const parity = await enrichStandaloneXmlForRun(factedJob);
  const engineJob = parity.applied ? { ...factedJob, sourceText: parity.xmlText } : factedJob;
  const result = await runXmlCii2019StandaloneEngineJob(engineJob, runtime);
  if (parity.applied && Array.isArray(result?.logs) && parity.logs.length) result.logs = [...parity.logs, ...result.logs];
  const normalized = normalizeXmlEngineResult(result);
  if (parity.applied && normalized.diagnostics) normalized.diagnostics.runParityStats = parity.stats;
  return normalized;
}
