from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import *
from .xml import *
from .extension import *

SUPPORTED_COMPILER_DIALECTS.add("managed-stage-json")

_BRIDGE_SCRIPT = Path(__file__).with_name("node_managed_stage_bridge.mjs")
_ALLOWED_SOURCE_TYPES = {"BRANCH", "PIPE", "SUPPORT", "ATTA", "RIGID", "FBLI"}
_BLOCKING_PRIMARY_DISPOSITIONS = {
    "BLOCK_AMBIGUOUS",
    "DEFER_SUPPORT",
    "EMIT_POINT_FEATURE",
    "EMIT_TEE_JUNCTION",
    "EMIT_OLET_JUNCTION",
    "EMIT_BEND_EDGE_SET",
}
_LINEAGE_ATTRS = {
    "GLOBAL_COORD_BASIS_NODE",
    "SOURCE_TYPE",
    "SOURCE_ENTITY_IDS",
    "CANONICAL_EDGE_ID",
    "TOPOLOGY_OPERATION",
}
_COORDINATE_ALIASES = {
    "FROM_GLOBAL_X": "FROM_X",
    "FROM_GLOBAL_Y": "FROM_Y",
    "FROM_GLOBAL_Z": "FROM_Z",
    "TO_GLOBAL_X": "TO_X",
    "TO_GLOBAL_Y": "TO_Y",
    "TO_GLOBAL_Z": "TO_Z",
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_hash(value: Any) -> str:
    return _sha256(_stable_json(value).encode("utf-8"))


def _run_managed_stage_bridge(source: bytes, *, source_name: str, context: dict[str, Any]) -> dict[str, Any]:
    node = str(context.get("nodeExecutable") or os.environ.get("NODE") or "node")
    if shutil.which(node) is None:
        raise CompileBlocked(f"Managed-stage adapter requires Node.js executable {node!r}.")
    if not _BRIDGE_SCRIPT.is_file():
        raise CompileBlocked(f"Managed-stage bridge script is missing: {_BRIDGE_SCRIPT}.")
    bridge_context = {
        "sourceName": source_name,
        "jobName": str(context.get("jobName") or Path(source_name).stem or "MANAGED_STAGE_CANONICAL"),
        "enrichmentConfig": context.get("enrichmentConfig") or {},
    }
    env = dict(os.environ)
    env["INPUTXML_COMPILER_MANAGED_STAGE_CONTEXT"] = json.dumps(bridge_context)
    completed = subprocess.run(
        [node, str(_BRIDGE_SCRIPT)],
        input=source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CompileBlocked(f"Managed-stage topology bridge returned invalid JSON: {stderr}") from exc
    if completed.returncode != 0 or not payload.get("ok"):
        raise CompileBlocked(
            "Managed-stage topology bridge failed: "
            + str(payload.get("error") or completed.stderr.decode("utf-8", errors="replace").strip() or "unknown error")
        )
    return payload


def _source_type_blocking(source_type: str, trace: dict[str, Any]) -> bool:
    normalized = str(source_type or "").upper()
    primary = str(trace.get("primaryDisposition") or "")
    if primary in _BLOCKING_PRIMARY_DISPOSITIONS and normalized != "BRANCH":
        return True
    if str(trace.get("status") or "").upper() == "ERROR":
        return True
    if normalized not in _ALLOWED_SOURCE_TYPES:
        return True
    if normalized in {"RIGID", "FBLI"}:
        return not any(str(value).startswith("RIGID:") for value in trace.get("inputXmlChildIds") or [])
    if normalized in {"SUPPORT", "ATTA"}:
        return primary != "EMIT_SUPPORT_ATTACHMENT"
    if normalized == "PIPE":
        return primary != "EMIT_ROUTE_EDGE"
    return False


def _cii_section(trace: dict[str, Any]) -> str | None:
    child_ids = [str(value) for value in trace.get("inputXmlChildIds") or []]
    if any(value.startswith("RESTRAINT:") for value in child_ids):
        return "RESTRANT"
    if any(value.startswith("RIGID:") for value in child_ids):
        return "RIGID"
    if trace.get("inputXmlElementIds"):
        return "ELEMENTS"
    return None


def _import_topology_ledger(payload: dict[str, Any], ledger: DecisionLedger) -> None:
    trace_ledger = payload.get("traceLedger") or {}
    traces = {str(row.get("sourceEntityId")): row for row in trace_ledger.get("records") or []}
    seen: set[str] = set()
    for entity in payload.get("sourceEntities") or []:
        entity_id = str(entity.get("sourceEntityId") or "")
        trace = traces.get(entity_id)
        if not entity_id or trace is None:
            ledger.add(
                source_entity_id=entity_id or "managed-stage-unidentified-entity",
                source_type=str(entity.get("sourceType") or "UNKNOWN"),
                source_fields=entity,
                projection_cardinality="BLOCKED",
                disposition="UNSUPPORTED_BLOCKING",
                diagnostics=["source entity has no TopologyTraceLedger.v1 record"],
            )
            continue
        seen.add(entity_id)
        blocking = _source_type_blocking(str(entity.get("sourceType") or ""), trace)
        source_fields = {
            "source": entity,
            "topologyTrace": trace,
        }
        evidence = [
            "ComponentTopologySourceModel.v1",
            "CanonicalTopology.v1",
            "TopologyTraceLedger.v1",
            _stable_json(trace.get("evidence") or {}),
        ]
        deferred = [str(value) for value in trace.get("deferredProperties") or []]
        diagnostics = [f"deferred property: {value}" for value in deferred]
        if blocking:
            diagnostics.append(
                f"source type {entity.get('sourceType') or '(blank)'} with disposition {trace.get('primaryDisposition') or '(blank)'} has no approved canonical CII projection"
            )
        ledger.add(
            source_entity_id=entity_id,
            source_type=str(entity.get("sourceType") or "UNKNOWN"),
            source_fields=source_fields,
            canonical_element_ids=[str(value) for value in trace.get("canonicalEdgeIds") or []],
            canonical_node_ids=[str(value) for value in trace.get("canonicalNodeIds") or []],
            inputxml_element_ids=[str(value) for value in trace.get("inputXmlElementIds") or []],
            cii_section=_cii_section(trace),
            projection_cardinality=str(trace.get("projectionCardinality") or "BLOCKED"),
            disposition="UNSUPPORTED_BLOCKING" if blocking else str(trace.get("primaryDisposition") or "PRESERVE_EXTENSION"),
            evidence=evidence,
            diagnostics=diagnostics,
        )
    for entity_id, trace in traces.items():
        if entity_id in seen:
            continue
        ledger.add(
            source_entity_id=entity_id,
            source_type=str(trace.get("sourceType") or "UNKNOWN"),
            source_fields={"topologyTrace": trace},
            projection_cardinality=str(trace.get("projectionCardinality") or "BLOCKED"),
            disposition="UNSUPPORTED_BLOCKING",
            diagnostics=["TopologyTraceLedger.v1 record has no matching source-model entity"],
        )
    for port in payload.get("sourcePorts") or []:
        ledger.add(
            source_entity_id=str(port.get("sourcePortId") or "managed-stage-port"),
            source_type="MANAGED_STAGE_SOURCE_PORT",
            source_fields=port,
            projection_cardinality="1_TO_1",
            disposition="PRESERVE_EXTENSION",
            evidence=["SourceEnvelope.v1/UniversalSourceGraph.v1 producer-owned port identity"],
        )
    for index, issue in enumerate(payload.get("buildIssues") or [], 1):
        blocking = issue.get("blocking") is not False
        ledger.add(
            source_entity_id=f"managed-stage-build-issue-{index}",
            source_type="TOPOLOGY_BUILD_ISSUE",
            source_fields=issue,
            projection_cardinality="BLOCKED" if blocking else "1_TO_0",
            disposition="UNSUPPORTED_BLOCKING" if blocking else "PRESERVE_EXTENSION",
            diagnostics=[str(issue.get("code") or "topology build issue")],
        )


def _preserve_metadata(
    scope: ET._Element,
    *,
    name: str,
    value: str,
    entity_id: str,
    ledger: DecisionLedger,
) -> None:
    _extension_record(
        scope,
        producer=ledger.producer,
        dialect=ledger.dialect,
        name=name,
        source_path=ledger.source_path,
        source_entity_id=entity_id,
        source_field=name,
        value=value,
        disposition="PRESERVE_EXTENSION",
        criticality="MEDIUM",
        confidence=1.0,
    )
    ledger.add(
        source_entity_id=f"{entity_id}@{name}",
        source_type="TOPOLOGY_INPUTXML_LINEAGE",
        source_fields={name: value},
        projection_cardinality="1_TO_0",
        disposition="PRESERVE_EXTENSION",
        evidence=["lineage retained in explicit extension namespace before canonical XSD validation"],
    )


def _rename_coordinate_attributes(element: ET._Element, ledger: DecisionLedger, entity_id: str) -> None:
    for source_name, canonical_name in _COORDINATE_ALIASES.items():
        if source_name not in element.attrib:
            continue
        value = element.attrib.pop(source_name)
        existing = element.get(canonical_name)
        if existing is not None and Decimal(existing) != Decimal(value):
            raise CompileBlocked(f"Managed-stage topology coordinate collision for {entity_id}: {source_name} conflicts with {canonical_name}.")
        element.set(canonical_name, value)
        ledger.add(
            source_entity_id=f"{entity_id}@{source_name}",
            source_type="FIELD_ALIAS",
            source_fields={source_name: value},
            canonical_element_ids=[entity_id],
            cii_section="COORDS",
            projection_cardinality="1_TO_1",
            disposition="RENAME_FIELD",
            evidence=[f"topology InputXML coordinate alias {source_name} -> {canonical_name}"],
        )


def _sanitize_topology_inputxml(root: ET._Element, payload: dict[str, Any], ledger: DecisionLedger) -> None:
    model = next(
        (child for child in root if _namespace(child.tag) == "" and _local(child.tag) == "PIPINGMODEL"),
        None,
    )
    if model is None:
        raise CompileBlocked("Managed-stage topology bridge emitted no direct PIPINGMODEL.")
    elements = [child for child in model if _namespace(child.tag) == "" and _local(child.tag) == "PIPINGELEMENT"]
    for element_index, element in enumerate(elements, 1):
        entity_id = element.get("ID") or f"PE-{element_index:06d}"
        _rename_coordinate_attributes(element, ledger, entity_id)
        for name in list(element.attrib):
            if name not in _LINEAGE_ATTRS:
                continue
            value = element.attrib.pop(name)
            _preserve_metadata(element, name=name, value=value, entity_id=entity_id, ledger=ledger)
        for child_index, child in enumerate(list(element), 1):
            child_name = _local(child.tag)
            if child_name not in {"RIGID", "RESTRAINT"}:
                continue
            for name in list(child.attrib):
                if name not in {"ID", "SOURCE_BODY_COUNT", "SOURCE_ENTITY_IDS"}:
                    continue
                value = child.attrib.pop(name)
                _preserve_metadata(
                    element,
                    name=f"{child_name}_{child_index}_{name}",
                    value=value,
                    entity_id=entity_id,
                    ledger=ledger,
                )
            if child_name == "RESTRAINT" and child.get("CNODE") is not None and _is_sentinel(child.get("CNODE")):
                previous = child.get("CNODE") or ""
                child.set("CNODE", "0")
                ledger.add(
                    source_entity_id=f"{entity_id}/RESTRAINT[{child_index}]@CNODE",
                    source_type="COMPATIBILITY_SENTINEL",
                    source_fields={"CNODE": previous},
                    canonical_element_ids=[entity_id],
                    cii_section="RESTRANT",
                    projection_cardinality="1_TO_1",
                    disposition="NORMALIZE_EXPLICIT_ABSENCE",
                    evidence=["component-topology support model exposes no connecting-node relationship; canonical CNODE=0 means none"],
                )
        _ensure_extension_last(element)

    original_counts = {name: model.get(name) for name in COUNT_FIELDS}
    canonical_counts = {
        "NUMELT": len(elements),
        "NUMNOZ": sum(1 for child in model if _local(child.tag) in NOZZLE_NAMES),
        "NOHGRS": sum(len(element.findall("HANGER")) for element in elements),
        "NUMBEND": sum(len(element.findall("BEND")) for element in elements),
        "NUMRIGID": sum(1 for element in elements if element.find("RIGID") is not None),
        "NUMEXPJNT": 0,
        "NUMREST": sum(1 for element in elements if element.find("RESTRAINT") is not None),
        "NUMFORCMNT": 0,
        "NUMUNFLOAD": 0,
        "NUMWIND": 0,
        "NUMELEOFF": 0,
        "NUMALLOW": 0,
        "NUMISECT": sum(1 for element in elements if element.find("SIF") is not None),
    }
    for name, value in canonical_counts.items():
        if original_counts.get(name) != str(value):
            ledger.add(
                source_entity_id=f"PIPINGMODEL@{name}",
                source_type="CONTROL_ASSERTION",
                source_fields={"source": original_counts.get(name), "canonical": value},
                cii_section="CONTROL",
                projection_cardinality="1_TO_1",
                disposition="RECOMPUTE_CONTROL_ASSERTION",
                evidence=["canonical count recomputed from sanitized topology InputXML children"],
            )
        model.set(name, str(value))
    source_bend_count = int(Decimal(original_counts.get("NUMBEND") or "0"))
    if source_bend_count and canonical_counts["NUMBEND"] == 0:
        ledger.add(
            source_entity_id="PIPINGMODEL@NUMBEND",
            source_type="BEND_PROJECTION_GAP",
            source_fields={"sourceBendCount": source_bend_count, "emittedBendChildren": 0},
            cii_section="BEND",
            projection_cardinality="MANY_TO_ZERO",
            disposition="UNSUPPORTED_BLOCKING",
            diagnostics=["component-topology compatibility writer reports bend edges but emits no BEND children"],
        )

    source_identity = payload.get("sourceIdentity") or {}
    topology = payload.get("canonicalTopology") or {}
    trace = payload.get("traceLedger") or {}
    metrics = payload.get("metrics") or {}
    artifact_records = {
        "ManagedStageSourceIdentity": _stable_json(source_identity),
        "CanonicalTopologyDigest": _json_hash(topology),
        "TopologyTraceLedgerDigest": _json_hash(trace),
        "ComponentTopologyMetrics": _stable_json(metrics),
    }
    for name, value in artifact_records.items():
        _extension_record(
            model,
            producer=ledger.producer,
            dialect=ledger.dialect,
            name=name,
            source_path=ledger.source_path,
            source_entity_id="document",
            source_field=name,
            value=value,
            disposition="PRESERVE_EXTENSION",
            criticality="MEDIUM",
            confidence=1.0,
        )
    _ensure_extension_last(model)


def _compile_managed_stage_source(
    source: bytes,
    *,
    source_name: str,
    ledger: DecisionLedger,
    context: dict[str, Any],
) -> ET._Element:
    payload = _run_managed_stage_bridge(source, source_name=source_name, context=context)
    _import_topology_ledger(payload, ledger)
    topology_xml = str(payload.get("topologyInputXml") or "")
    if not topology_xml:
        raise CompileBlocked("Managed-stage topology bridge returned no topology InputXML.")
    root = _parse_xml(topology_xml.encode("utf-8"), f"{source_name}.topology.input.xml")
    _sanitize_topology_inputxml(root, payload, ledger)
    ledger.add(
        source_entity_id="document",
        source_type="MANAGED_STAGE_JSON",
        source_fields={
            "sourceIdentity": payload.get("sourceIdentity") or {},
            "metrics": payload.get("metrics") or {},
            "canonicalTopologyHash": _json_hash(payload.get("canonicalTopology") or {}),
            "traceLedgerHash": _json_hash(payload.get("traceLedger") or {}),
        },
        canonical_element_ids=[str(edge.get("id")) for edge in (payload.get("canonicalTopology") or {}).get("edges") or []],
        canonical_node_ids=[str(node.get("id")) for node in (payload.get("canonicalTopology") or {}).get("nodes") or []],
        inputxml_element_ids=[
            str(value)
            for edge in (payload.get("canonicalTopology") or {}).get("edges") or []
            for value in edge.get("inputXmlElementIds") or []
        ],
        projection_cardinality="1_TO_MANY",
        disposition="PROJECT_WITH_COMPONENT_TOPOLOGY_LEDGER",
        evidence=[
            "ComponentTopologySourceModel.v1",
            "CanonicalTopology.v1",
            "TopologyTraceLedger.v1",
            "component-aware topology InputXML",
        ],
    )
    return root


__all__ = [name for name in globals() if not name.startswith("__")]
