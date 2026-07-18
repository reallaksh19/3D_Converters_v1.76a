#!/usr/bin/env node

import fs from 'node:fs';
import {
  buildComponentTopologyArtifacts,
} from '../../tabs/model-converters/converters/component-topology/topology-artifact-exporter.js';
import { buildTopologySourceModel } from '../../tabs/model-converters/converters/component-topology/topology-source-model.js';
import {
  SUPPORT_PROJECTION_TOLERANCE_MM,
  TOPOLOGY_TOLERANCE_MM,
} from '../../tabs/model-converters/converters/component-topology/topology-values.js';

const sourceText = fs.readFileSync(0, 'utf8');
const context = JSON.parse(process.env.INPUTXML_COMPILER_MANAGED_STAGE_CONTEXT || '{}');
const sourceName = String(context.sourceName || 'managed-stage.json');
const jobName = String(context.jobName || sourceName.replace(/\.json$/i, '') || 'MANAGED_STAGE_CANONICAL');

// Topology InputXML is a producer-side parity artifact and intentionally carries
// trace-only attributes that are not part of canonical CAESAR InputXML. The
// canonical compiler imports the authoritative CanonicalTopology.v1 and
// TopologyTraceLedger.v1 objects separately, so these serialization attributes
// must be removed at the bridge boundary rather than treated as engineering
// fields or copied into the production writer contract.
const TOPOLOGY_TRACE_ATTRIBUTES = Object.freeze([
  'CANONICAL_TOPOLOGY_HASH',
  'TOPOLOGY_TRACE_LEDGER_HASH',
  'GLOBAL_COORD_BASIS_NODE',
  'SOURCE_TYPE',
  'CANONICAL_EDGE_ID',
  'FROM_CANONICAL_NODE_ID',
  'TO_CANONICAL_NODE_ID',
  'SOURCE_ENTITY_IDS',
  'SOURCE_PORT_IDS',
  'FROM_SOURCE_ENTITY_IDS',
  'TO_SOURCE_ENTITY_IDS',
  'FROM_SOURCE_PORT_IDS',
  'TO_SOURCE_PORT_IDS',
  'BRANCH_IDS',
  'TOPOLOGY_OPERATION',
  'PROJECTION_CARDINALITY',
  'MERGE_AUTHORITY',
  'SUPPORT_ID',
  'CANONICAL_NODE_ID',
  'SOURCE_PATHS',
  'ATTACHMENT_AUTHORITY',
  'ATTACHMENT_REFERENCES',
  'CONNECTION_RESIDUAL',
  'SOURCE_X',
  'SOURCE_Y',
  'SOURCE_Z',
  'ATTACHMENT_X',
  'ATTACHMENT_Y',
  'ATTACHMENT_Z',
  'ASSIGNMENT_AUTHORITY',
]);

function stripTopologyTraceAttributes(xml) {
  let result = String(xml || '');
  for (const name of TOPOLOGY_TRACE_ATTRIBUTES) {
    result = result.replace(new RegExp(`\\s${name}="[^"]*"`, 'g'), '');
  }
  return result;
}

try {
  const sourceModel = await buildTopologySourceModel(sourceText, {
    sourceName,
    toleranceMm: TOPOLOGY_TOLERANCE_MM,
  });
  const artifacts = await buildComponentTopologyArtifacts(sourceText, {
    sourceName,
    jobName,
    toleranceMm: TOPOLOGY_TOLERANCE_MM,
    supportProjectionToleranceMm: SUPPORT_PROJECTION_TOLERANCE_MM,
    enrichmentConfig: context.enrichmentConfig && typeof context.enrichmentConfig === 'object'
      ? context.enrichmentConfig : {},
  });
  const sourceEntities = sourceModel.entities.map((entity) => ({
    sourceEntityId: entity.sourceEntityId,
    sourcePath: entity.sourcePath,
    sourceType: entity.sourceType,
    name: entity.name,
    sourceRef: entity.sourceRef,
    sourceBranchEntityId: entity.sourceBranchEntityId || '',
    sourceBranchName: entity.sourceBranchName || '',
    attributes: entity.attributes || {},
    enrichedAttributes: entity.enrichedAttributes || {},
    geometryClassification: entity.geometryClassification || 'BRANCH',
    finiteSpan: entity.finiteSpan === true,
    spanLengthMm: Number.isFinite(entity.spanLengthMm) ? entity.spanLengthMm : null,
    supportProjection: entity.supportProjection || null,
    ports: (entity.ports || []).map((port) => ({
      sourcePortId: port.sourcePortId,
      sourceEntityId: port.sourceEntityId,
      sourcePath: port.sourcePath,
      field: port.field,
      role: port.role,
      position: port.position,
    })),
  }));
  process.stdout.write(JSON.stringify({
    ok: true,
    sourceIdentity: sourceModel.sourceIdentity,
    sourceEntities,
    sourcePorts: sourceModel.sourcePorts,
    canonicalTopology: artifacts.canonicalTopology,
    traceLedger: artifacts.traceLedger,
    topologyInputXml: stripTopologyTraceAttributes(artifacts.topologyInputXml),
    metrics: artifacts.metrics,
    buildIssues: artifacts.buildIssues,
  }));
} catch (error) {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: error?.message || String(error),
    stack: error?.stack || '',
  }));
  process.exitCode = 2;
}

export const _test = Object.freeze({ stripTopologyTraceAttributes, TOPOLOGY_TRACE_ATTRIBUTES });
