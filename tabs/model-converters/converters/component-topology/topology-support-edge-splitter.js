/**
 * Matches support anchors to route edges and splits accepted edges. Explicit
 * ATTACHED_COMPONENT_REF/COMPRE evidence outranks coordinate proximity. Without
 * that evidence, branch ownership and the configured projection limit remain
 * mandatory. Global proximity is diagnostic only.
 */

import { pointSegmentDistance } from './topology-geometry-distance.js';
import { pointDistance, uniqueText } from './topology-values.js';

/** @param {{x:number,y:number,z:number}|null} point @returns {string} */
function pointText(point) {
  if (!point) return '(none)';
  return `${point.x.toFixed(3)},${point.y.toFixed(3)},${point.z.toFixed(3)}`;
}

/** @param {number|undefined} value @returns {string} */
function distanceText(value) {
  return Number.isFinite(value) ? Number(value).toFixed(3) : 'unbounded';
}

/** @param {Record<string, unknown>[]} edgeSpecs @param {Record<string, unknown>} support @param {'owning'|'referenced'|'all'} scope @returns {Record<string, unknown>[]} */
function rankedSupportCandidates(edgeSpecs, support, scope) {
  const attached = new Set(support.attachedComponentEntityIds ?? []);
  return edgeSpecs.flatMap((edge, index) => {
    const owns = support.sourceBranchEntityIds.some((id) => edge.sourceEntityIds.includes(id));
    const referenced = edge.sourceEntityIds.some((id) => attached.has(id));
    if (scope === 'owning' && !owns) return [];
    if (scope === 'referenced' && !referenced) return [];
    const proximity = pointSegmentDistance(support.sourcePosition, edge.fromPort.position, edge.toPort.position);
    return [{ edge, index, owns, referenced, ...proximity }];
  }).sort((left, right) => left.distanceMm - right.distanceMm || left.index - right.index);
}

/** @param {Record<string, unknown>} support @param {Record<string, unknown>|undefined} owning @param {Record<string, unknown>|undefined} global @param {number} toleranceMm @returns {Error} */
function supportProjectionError(support, owning, global, toleranceMm) {
  const classification = global && global.distanceMm <= toleranceMm
    ? 'POSSIBLE_WRONG_BRANCH_OWNERSHIP' : 'SOURCE_SUPPORT_POSITION_OR_ROUTE_INVALID';
  const owningText = owning
    ? `${owning.edge.sourcePath}/${owning.edge.segmentRole}@${pointText(owning.projected)}` : '(none)';
  const globalText = global
    ? `${global.edge.sourcePath}/${global.edge.segmentRole}@${pointText(global.projected)}:${distanceText(global.distanceMm)}mm` : '(none)';
  return new Error(
    `Support ${support.tag || support.id} is ${distanceText(owning?.distanceMm)} mm from its owning branch; limit is ${distanceText(toleranceMm)} mm. `
    + `[${classification}] sourcePath=${support.sourcePaths.join('|')}; branches=${support.sourceBranchNames.join('|')}; `
    + `positionAuthority=${support.positionAuthority}; position=${pointText(support.sourcePosition)}; nearestOwning=${owningText}; nearestAny=${globalText}.`,
  );
}

/** @param {Record<string, unknown>} support @returns {Error} */
function unresolvedReferenceError(support) {
  return new Error(
    `Support ${support.tag || support.id} has unresolved attachment reference(s): `
    + `${support.unresolvedAttachmentReferences.join(', ')}. sourcePath=${support.sourcePaths.join('|')}.`,
  );
}

/** @param {Record<string, unknown>} support @param {Record<string, unknown>} match @param {string} authority @returns {void} */
function acceptMatch(support, match, authority) {
  support.connectionResidual = match.distanceMm;
  support.edgeParameter = match.parameter;
  support.attachmentPosition = match.projected;
  support.attachmentAuthority = authority;
  support.attachmentEdgeSourcePath = match.edge.sourcePath;
  support.attachmentPort = {
    ...support.port,
    position: match.projected,
    sourcePath: `${support.port.sourcePath}:PROJECTED_ATTACHMENT`,
    canonicalPositionAuthority: true,
    attachmentAuthority: authority,
    sourcePosition: support.sourcePosition,
  };
}

/** @param {Record<string, unknown>[]} edgeSpecs @param {Record<string, unknown>[]} supports @param {number} toleranceMm @returns {Map<number,Record<string, unknown>[]>} */
function matchSupportsToEdges(edgeSpecs, supports, toleranceMm) {
  const matches = new Map();
  for (const support of supports) {
    const referenced = rankedSupportCandidates(edgeSpecs, support, 'referenced');
    if (support.attachmentReferences.length && !referenced.length) throw unresolvedReferenceError(support);
    const owning = rankedSupportCandidates(edgeSpecs, support, 'owning');
    const global = rankedSupportCandidates(edgeSpecs, support, 'all');
    const match = referenced[0] ?? owning[0];
    if (!match || (!referenced.length && match.distanceMm > toleranceMm)) {
      throw supportProjectionError(support, owning[0], global[0], toleranceMm);
    }
    const authority = referenced.length
      ? `${support.attachmentAuthorities.join('+') || 'ATTACHED_COMPONENT_REF/COMPRE'}:${support.attachmentReferences.join('|')}`
      : 'OWNING_BRANCH_PROXIMITY';
    acceptMatch(support, match, authority);
    matches.set(match.index, [...(matches.get(match.index) ?? []), support]);
  }
  return matches;
}

/** @param {Record<string, unknown>} support @returns {Record<string, unknown>} */
function effectivePort(support) {
  return support.attachmentPort ?? support.port;
}

/** @param {Record<string, unknown>} spec @param {Record<string, unknown>[]} supports @param {Readonly<Record<string, unknown>>} set @param {number} topologyToleranceMm @returns {Record<string, unknown>[]} */
function splitOneEdge(spec, supports, set, topologyToleranceMm) {
  const ordered = [...supports].sort((left, right) => left.edgeParameter - right.edgeParameter || left.order - right.order);
  const interior = [], atStart = [], atEnd = [];
  for (const support of ordered) {
    const port = effectivePort(support);
    if (pointDistance(port.position, spec.fromPort.position) <= topologyToleranceMm) {
      set.unite(port.key, spec.fromPort.key);
      atStart.push(support);
    } else if (pointDistance(port.position, spec.toPort.position) <= topologyToleranceMm) {
      set.unite(port.key, spec.toPort.key);
      atEnd.push(support);
    } else interior.push(support);
  }
  const interiorPoints = [];
  for (const support of interior) {
    const port = effectivePort(support);
    const previous = interiorPoints.at(-1);
    if (previous && pointDistance(previous.port.position, port.position) <= topologyToleranceMm) {
      set.unite(previous.port.key, port.key);
      previous.supports.push(support);
    } else interiorPoints.push({ port, supports: [support] });
  }
  const points = [{ port: spec.fromPort, supports: atStart }, ...interiorPoints, { port: spec.toPort, supports: atEnd }];
  return points.slice(1).map((point, index) => {
    const attachments = uniqueText([...points[index].supports, ...point.supports].flatMap((row) => row.sourceEntityIds));
    return {
      ...spec,
      fromPort: points[index].port,
      toPort: point.port,
      sourceEntityIds: uniqueText([...spec.sourceEntityIds, ...attachments]),
      sourceTypes: attachments.length ? uniqueText([...spec.sourceTypes, 'SUPPORT']) : spec.sourceTypes,
      segmentRole: interior.length ? `${spec.segmentRole}_SUPPORT_SPLIT` : spec.segmentRole,
    };
  });
}

/** @param {Record<string, unknown>[]} specs @param {Record<string, unknown>[]} supports @param {Record<string, unknown>} connectivity @param {number} topologyToleranceMm @param {number} projectionToleranceMm @returns {Record<string, unknown>[]} */
export function splitEdgeSpecsAtSupports(specs, supports, connectivity, topologyToleranceMm, projectionToleranceMm) {
  const matches = matchSupportsToEdges(specs, supports, projectionToleranceMm);
  return specs.flatMap((spec, index) => {
    const assigned = matches.get(index) ?? [];
    return assigned.length ? splitOneEdge(spec, assigned, connectivity.set, topologyToleranceMm) : [spec];
  });
}

export const _test = Object.freeze({
  rankedSupportCandidates,
  supportProjectionError,
  unresolvedReferenceError,
});
