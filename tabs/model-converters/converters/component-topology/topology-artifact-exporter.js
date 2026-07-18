/**
 * Component-topology artifact orchestrator.
 * CanonicalTopology.v1 is built once, stamped, and used unchanged by the
 * topology InputXML writer and TopologySvgScene.v1 builder. Generated InputXML
 * is parsed back and exact three-way topology parity is a fail-closed gate.
 */

import { buildTopologySourceModel } from './topology-source-model.js';
import { buildExplicitConnectivity } from './topology-connectivity.js';
import { buildCanonicalTopology } from './topology-canonical-builder.js';
import { buildTopologyTraceLedger } from './topology-ledger-builder.js';
import { buildTopologyInputXml } from './topology-inputxml-writer.js';
import { buildTopologyEngineeringProjection } from './topology-engineering-projection.js';
import { materializeTopologyEditDraft } from './topology-edit-engine.js';
import { enrichCanonicalTopologyIdentity, enrichTopologyTraceLedgerIdentity } from './topology-artifact-identity.js';
import { stampArtifactHash } from './topology-deterministic-hash.js';
import { buildTopologySvgScene } from './topology-svg-scene-builder.js';
import { parseTopologyInputXml } from './topology-inputxml-topology-parser.js';
import {
  buildTopologyParityReport,
  topologyParityMismatchesCsv,
  TopologyParityError,
} from './topology-parity-engine.js';
import { cleanText, componentCategory, pointDistance } from './topology-values.js';

function componentCounts(model) {
  const counts = {};
  for (const component of model.components) {
    const category = componentCategory(component.sourceType);
    counts[category] = (counts[category] ?? 0) + 1;
  }
  return counts;
}

function referenceCounts(model) {
  const branchNames = new Set(model.branches.map((branch) => cleanText(branch.name)));
  const componentAliases = new Set(model.components.flatMap((component) => [component.sourceRef, component.name]).map(cleanText).filter(Boolean));
  const branchReferences = model.branches.flatMap((branch) => ['HREF', 'TREF'].flatMap((field) => {
    const value = cleanText(branch.attributes[field]);
    return value ? [{ value, resolved: branchNames.has(value) || componentAliases.has(value) }] : [];
  }));
  const crefReferences = model.components.flatMap((component) => {
    const value = cleanText(component.attributes.CREF);
    return value ? [{ value, resolved: branchNames.has(value) }] : [];
  });
  return {
    cref: crefReferences.length,
    resolvedCref: crefReferences.filter((row) => row.resolved).length,
    hrefTref: branchReferences.length,
    resolvedHrefTref: branchReferences.filter((row) => row.resolved).length,
    externalReferences: branchReferences.filter((row) => !row.resolved).length,
  };
}

function artifactMetrics(model, canonical, ledger, engineering, parsed, scene, parity, toleranceMm) {
  const nodes = new Map(canonical.nodes.map((node) => [node.id, node]));
  const zeroLengthEdges = canonical.edges.filter((edge) => pointDistance(
    nodes.get(edge.fromNodeId)?.position ?? null,
    nodes.get(edge.toNodeId)?.position ?? null,
  ) <= toleranceMm).length;
  return Object.freeze({
    source: Object.freeze({
      branches: model.branches.length,
      childRecords: model.components.length,
      routeComponents: model.components.filter((component) => component.isRouteComponent).length,
      supports: model.components.filter((component) => componentCategory(component.sourceType) === 'SUPPORT').length,
      componentCounts: Object.freeze(componentCounts(model)),
    }),
    references: Object.freeze(referenceCounts(model)),
    canonical: Object.freeze({
      nodes: canonical.nodes.length,
      edges: canonical.edges.length,
      pointFeatures: canonical.pointFeatures.length,
      junctions: canonical.junctions.length,
      boundaries: canonical.boundaries.length,
      supports: canonical.supports.length,
      rigids: canonical.rigids.length,
      zeroLengthEdges,
    }),
    ledger: Object.freeze({
      records: ledger.records.length,
      routeRecords: ledger.summary.routeRecords,
      blockedRecords: ledger.summary.blockedRecords,
      deferredSupports: ledger.records.filter((row) => row.primaryDisposition === 'DEFER_SUPPORT').length,
    }),
    engineering: engineering.metrics,
    inputXml: Object.freeze({
      elements: canonical.edges.length,
      restraints: canonical.supports.reduce((sum, support) => sum + support.restraints.length, 0),
      rigids: new Set(canonical.rigids.map((rigid) => rigid.edgeId)).size,
      rigidBodies: canonical.rigids.length,
    }),
    parsedInputXml: Object.freeze({
      nodes: parsed.nodes.length,
      elements: parsed.elements.length,
      restraints: parsed.restraints.length,
      rigids: parsed.rigids.length,
      parseIssues: parsed.issues.length,
    }),
    svgScene: scene.summary,
    parity: parity.summary,
  });
}

/**
 * Builds the immutable producer state shared by normal and edited exports.
 * Source parsing, engineering projection, connectivity, and base canonical
 * identity are performed exactly once per regeneration.
 *
 * @param {string} sourceText
 * @param {Record<string, unknown>} options
 * @returns {Promise<Readonly<Record<string, unknown>>>}
 */
async function buildProducerState(sourceText, options) {
  const model = await buildTopologySourceModel(sourceText, {
    sourceName: options.sourceName,
    toleranceMm: options.toleranceMm,
  });
  const engineering = buildTopologyEngineeringProjection(sourceText, model, {
    enrichmentConfig: options.enrichmentConfig,
    supportProjectionToleranceMm: options.supportProjectionToleranceMm,
  });
  const connectivity = buildExplicitConnectivity(model, options.toleranceMm);
  const builtCanonical = buildCanonicalTopology(
    model, connectivity, engineering, options.toleranceMm, options.supportProjectionToleranceMm,
  );
  const canonicalTopology = stampArtifactHash(
    enrichCanonicalTopologyIdentity(builtCanonical, model), 'canonicalTopologyHash',
  );
  return Object.freeze({ model, engineering, canonicalTopology });
}

/**
 * Projects one canonical authority to every regenerated artifact and executes
 * the independent InputXML/SVG parity gate.
 *
 * @param {Record<string, unknown>} state
 * @param {Record<string, unknown>} options
 * @param {Record<string, unknown>|null} editMetadata
 * @param {boolean} failOnParity
 * @returns {Readonly<Record<string, unknown>>}
 */
function buildArtifactsFromState(state, options, editMetadata, failOnParity) {
  const { model, engineering, canonicalTopology } = state;
  const builtLedger = buildTopologyTraceLedger(model, canonicalTopology);
  const traceLedger = stampArtifactHash(enrichTopologyTraceLedgerIdentity(
    builtLedger, canonicalTopology, model, engineering, {
      sourceName: options.sourceName,
      topologyToleranceMm: options.toleranceMm,
      supportProjectionToleranceMm: options.supportProjectionToleranceMm,
    },
  ), 'topologyTraceLedgerHash');
  const topologyInputXml = buildTopologyInputXml(canonicalTopology, engineering, {
    jobName: options.jobName,
    topologyTraceLedgerHash: traceLedger.topologyTraceLedgerHash,
    topologyEditDraftHash: editMetadata?.editDraft?.topologyEditDraftHash,
    topologyEditLedgerHash: editMetadata?.editLedger?.topologyEditLedgerHash,
  });
  const parsedInputXmlTopology = parseTopologyInputXml(topologyInputXml);
  const topologySvgScene = buildTopologySvgScene(canonicalTopology, traceLedger, []);
  const topologyParityReport = buildTopologyParityReport({
    canonicalTopology,
    traceLedger,
    parsedInputXmlTopology,
    topologySvgScene,
  });
  const metrics = artifactMetrics(
    model, canonicalTopology, traceLedger, engineering,
    parsedInputXmlTopology, topologySvgScene, topologyParityReport, options.toleranceMm,
  );
  const artifacts = Object.freeze({
    sourceIdentity: model.sourceIdentity,
    canonicalTopology,
    traceLedger,
    topologyInputXml,
    parsedInputXmlTopology,
    topologySvgScene,
    topologyParityReport,
    topologyParityMismatchesCsv: topologyParityMismatchesCsv(topologyParityReport),
    metrics,
    buildIssues: canonicalTopology.buildIssues,
    ...(editMetadata ?? {}),
  });
  if (failOnParity && !topologyParityReport.ok) throw new TopologyParityError(topologyParityReport);
  return artifacts;
}

/**
 * Builds the immutable baseline topology artifacts.
 *
 * @param {string} sourceText
 * @param {Record<string, unknown>} options
 * @returns {Promise<Readonly<Record<string, unknown>>>}
 */
export async function buildComponentTopologyArtifacts(sourceText, options) {
  const state = await buildProducerState(sourceText, options);
  return buildArtifactsFromState(state, options, null, true);
}

/**
 * Regenerates every topology artifact from source plus a reversible edit draft.
 * Blocking edit validation and parity evidence are returned to the editor so
 * export can be gated without mutating or overwriting any uploaded artifact.
 *
 * @param {string} sourceText
 * @param {Record<string, unknown>} draft
 * @param {Record<string, unknown>} options
 * @returns {Promise<Readonly<Record<string, unknown>>>}
 */
export async function buildEditedComponentTopologyArtifacts(sourceText, draft, options) {
  const baseState = await buildProducerState(sourceText, options);
  const materialized = materializeTopologyEditDraft(baseState.canonicalTopology, draft);
  const editValidation = materialized.validation;
  const state = Object.freeze({
    ...baseState,
    canonicalTopology: materialized.canonicalTopology,
  });
  const artifacts = buildArtifactsFromState(state, options, {
    editDraft: draft,
    editLedger: materialized.editLedger,
    editValidation,
  }, false);
  const parityBlocking = artifacts.topologyParityReport.ok ? [] : [Object.freeze({
    code: 'TOPOLOGY_PARITY_BLOCKING',
    blocking: true,
    message: `${artifacts.topologyParityReport.summary.topologyMismatchCount} independent topology parity mismatches remain.`,
    objectIds: Object.freeze([]),
  })];
  const combinedFindings = Object.freeze([...editValidation.findings, ...parityBlocking]);
  const blockingCount = combinedFindings.filter((finding) => finding.blocking).length;
  return Object.freeze({
    ...artifacts,
    editValidation: Object.freeze({
      ...editValidation,
      ok: blockingCount === 0,
      findings: combinedFindings,
      summary: Object.freeze({ findingCount: combinedFindings.length, blockingCount }),
    }),
  });
}

export function topologyArtifactOutputs(artifacts, options) {
  const outputs = [
    Object.freeze({
      name: `${options.stem}.canonical-topology.json`,
      text: `${JSON.stringify(artifacts.canonicalTopology, null, 2)}\n`,
      mime: 'application/json;charset=utf-8',
    }),
    Object.freeze({
      name: `${options.stem}.topology-trace-ledger.json`,
      text: `${JSON.stringify(artifacts.traceLedger, null, 2)}\n`,
      mime: 'application/json;charset=utf-8',
    }),
    Object.freeze({
      name: `${options.stem}.topology.input.xml`,
      text: artifacts.topologyInputXml,
      mime: 'application/xml;charset=utf-8',
    }),
    Object.freeze({
      name: `${options.stem}.topology-svg-scene.json`,
      text: `${JSON.stringify(artifacts.topologySvgScene, null, 2)}\n`,
      mime: 'application/json;charset=utf-8',
    }),
    Object.freeze({
      name: `${options.stem}.parsed-inputxml-topology.json`,
      text: `${JSON.stringify(artifacts.parsedInputXmlTopology, null, 2)}\n`,
      mime: 'application/json;charset=utf-8',
    }),
    Object.freeze({
      name: `${options.stem}.topology-parity-report.json`,
      text: `${JSON.stringify(artifacts.topologyParityReport, null, 2)}\n`,
      mime: 'application/json;charset=utf-8',
    }),
    Object.freeze({
      name: `${options.stem}.topology-parity-mismatches.csv`,
      text: artifacts.topologyParityMismatchesCsv,
      mime: 'text/csv;charset=utf-8',
    }),
  ];
  if (artifacts.editDraft) outputs.unshift(Object.freeze({
    name: `${options.stem}.topology-edit-draft.json`,
    text: `${JSON.stringify(artifacts.editDraft, null, 2)}\n`,
    mime: 'application/json;charset=utf-8',
  }));
  if (artifacts.editLedger) outputs.push(Object.freeze({
    name: `${options.stem}.topology-edit-ledger.json`,
    text: `${JSON.stringify(artifacts.editLedger, null, 2)}\n`,
    mime: 'application/json;charset=utf-8',
  }));
  return Object.freeze(outputs);
}

export const _test = Object.freeze({
  componentCounts,
  referenceCounts,
  artifactMetrics,
  buildProducerState,
  buildArtifactsFromState,
});
