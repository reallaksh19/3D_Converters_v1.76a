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

SUPPORTED_COMPILER_DIALECTS.add("xml-builder-root-branch-node")

_BRIDGE_SCRIPT = Path(__file__).with_name("node_xml_builder_bridge.cjs")
_COMPATIBILITY_ATTRS = {
    "MODULUS", "POISSONS", "PIPE_DENSITY",
    *{f"HOT_MOD{i}" for i in range(1, 10)},
    "REFRACTORY_DENSITY", "REFRACTORY_THK", "CLADDING_DEN", "CLADDING_THK",
    "INSUL_CLAD_UNIT_WEIGHT", "MILL_TOL_PLUS", "MILL_TOL_MINUS", "SEAM_WELD", "NAME",
}
_BLOCKING_DIAGNOSTIC_TOKENS = (
    "DROPPED", "TRUNCATED", "UNRESOLVED", "DEFAULT_SUBSTITUTED",
    "FABRICATED", "FAILED", "SYNTHETIC",
)


def _child_text(element: ET._Element, name: str) -> str:
    for child in element:
        if _namespace(child.tag) == "" and _local(child.tag).upper() == name.upper():
            return (child.text or "").strip()
    return ""


def _source_evidence(root: ET._Element) -> dict[str, Any]:
    branches: list[dict[str, Any]] = []
    for branch_index, branch in enumerate(
        (element for element in root.iter() if _namespace(element.tag) == "" and _local(element.tag) == "Branch"),
        1,
    ):
        line = _child_text(branch, "LineNo") or _child_text(branch, "LINE_ID") or _child_text(branch, "Branchname")
        branch_fields = {_local(child.tag): (child.text or "").strip() for child in branch if _local(child.tag) != "Node"}
        nodes: dict[str, set[str]] = {}
        for node in (child for child in branch if _namespace(child.tag) == "" and _local(child.tag) == "Node"):
            node_number = _child_text(node, "NodeNumber")
            if node_number:
                nodes[node_number] = {_local(child.tag) for child in node if (child.text or "").strip() != ""}
        branches.append({"index": branch_index, "line": line, "fields": branch_fields, "nodes": nodes})
    return {"branches": branches}


def _find_branch_evidence(evidence: dict[str, Any], line: str) -> dict[str, Any] | None:
    for branch in evidence.get("branches", []):
        if branch.get("line") == line:
            return branch
    return None


def _inactive_padding(child: ET._Element) -> bool:
    name = _local(child.tag)
    if name == "SIF":
        return child.get("NODE") is not None and _is_sentinel(child.get("NODE")) and all(
            not str(value).strip() or _is_sentinel(value) for value in child.attrib.values()
        )
    if name == "RESTRAINT":
        values = [value for key, value in child.attrib.items() if key not in {"NUM", "TAG", "GUID"}]
        return bool(values) and all(not str(value).strip() or _is_sentinel(value) for value in values)
    return False


def _sanitize_bridge_output(root: ET._Element, source_root: ET._Element, ledger: DecisionLedger) -> None:
    evidence = _source_evidence(source_root)
    model = next((child for child in root if _local(child.tag) == "PIPINGMODEL" and _namespace(child.tag) == ""), None)
    if model is None:
        raise CompileBlocked("XML Builder topology bridge did not emit a direct PIPINGMODEL.")
    removed_padding = 0
    removed_defaults = 0
    for element in (child for child in model if _local(child.tag) == "PIPINGELEMENT" and _namespace(child.tag) == ""):
        line = (element.get("LINE_ID") or element.get("LINE") or "").strip()
        branch = _find_branch_evidence(evidence, line)
        from_node = str(int(Decimal(element.get("FROM_NODE")))) if element.get("FROM_NODE") else ""
        to_node = str(int(Decimal(element.get("TO_NODE")))) if element.get("TO_NODE") else ""
        node_fields = (branch or {}).get("nodes", {})
        endpoint_fields = set(node_fields.get(from_node, set())) | set(node_fields.get(to_node, set()))

        for attr in list(element.attrib):
            value = element.get(attr) or ""
            if attr in _COMPATIBILITY_ATTRS:
                del element.attrib[attr]
                removed_defaults += 1
                continue
            if attr == "MATERIAL_NAME" and not value.strip():
                del element.attrib[attr]
                continue
            if attr == "MATERIAL_NUM" and not (
                (branch and str(branch.get("fields", {}).get("MaterialNumber", "")).strip())
                or "MaterialCode" in endpoint_fields
            ):
                del element.attrib[attr]
                removed_defaults += 1
                continue
            if attr == "INSUL_THICK" and "InsulationThickness" not in endpoint_fields:
                del element.attrib[attr]
                removed_defaults += 1
                continue
            if attr == "CORR_ALLOW" and "CorrosionAllowance" not in endpoint_fields:
                del element.attrib[attr]
                removed_defaults += 1
                continue

        for child in list(element):
            if _inactive_padding(child):
                element.remove(child)
                removed_padding += 1
            elif _local(child.tag) == "RIGID" and child.get("TYPE") == "Unspecified":
                child.attrib.pop("TYPE", None)
                removed_defaults += 1

    if removed_padding:
        ledger.add(
            source_entity_id="xml-builder-bridge/padding",
            source_type="LEGACY_FIXED_SLOT_PADDING",
            source_fields={"removedRecords": removed_padding},
            projection_cardinality="MANY_TO_ZERO",
            disposition="FILTER_INACTIVE_PADDING",
            evidence=["removed only records whose engineering payload was entirely blank or the InputXML missing sentinel"],
        )
    if removed_defaults:
        ledger.add(
            source_entity_id="xml-builder-bridge/compatibility-defaults",
            source_type="LEGACY_COMPATIBILITY_DEFAULT",
            source_fields={"removedAttributes": removed_defaults},
            projection_cardinality="MANY_TO_ZERO",
            disposition="FILTER_COMPATIBILITY_DEFAULT",
            evidence=["removed values injected by the legacy topology writer that were not present in Root/Branch/Node source evidence"],
        )


def _record_bridge_diagnostics(payload: dict[str, Any], ledger: DecisionLedger) -> None:
    diagnostics = payload.get("diagnostics") or {}
    records = diagnostics.get("records") or []
    for index, row in enumerate(records, 1):
        code = str(row.get("code") or f"XML_BUILDER_DIAGNOSTIC_{index}")
        severity = str(row.get("severity") or "INFO").upper()
        blocking = severity in {"ERROR", "BLOCKING"} or any(token in code for token in _BLOCKING_DIAGNOSTIC_TOKENS)
        disposition = "UNSUPPORTED_BLOCKING" if blocking else "PRESERVE_EXTENSION"
        ledger.add(
            source_entity_id=f"xml-builder-diagnostic-{index}",
            source_type="XML_BUILDER_DIAGNOSTIC",
            source_fields=row,
            projection_cardinality="1_TO_0",
            disposition=disposition,
            evidence=["diagnostic emitted by tabs/model-converters/xml-cii-node-to-inputxml-core.js or its authoritative dependencies"],
            diagnostics=[str(row.get("message") or code)],
        )
    for index, warning in enumerate(payload.get("warnings") or [], 1):
        ledger.add(
            source_entity_id=f"xml-builder-warning-{index}",
            source_type="XML_BUILDER_WARNING",
            source_fields={"warning": warning},
            projection_cardinality="1_TO_0",
            disposition="UNSUPPORTED_BLOCKING",
            diagnostics=[str(warning)],
        )


def _compile_xml_builder_source(
    source: bytes,
    *,
    source_root: ET._Element,
    ledger: DecisionLedger,
    context: dict[str, Any],
) -> ET._Element:
    node = str(context.get("nodeExecutable") or os.environ.get("NODE") or "node")
    if shutil.which(node) is None:
        raise CompileBlocked(f"XML Builder adapter requires Node.js executable {node!r}.")
    if not _BRIDGE_SCRIPT.is_file():
        raise CompileBlocked(f"XML Builder bridge script is missing: {_BRIDGE_SCRIPT}.")
    bridge_context = {
        "jobName": str(context.get("jobName") or "XML_BUILDER_CANONICAL_COMPILER"),
        "defaultTeeSifType": context.get("defaultTeeSifType", 5),
    }
    env = dict(os.environ)
    env["INPUTXML_COMPILER_BRIDGE_CONTEXT"] = json.dumps(bridge_context)
    completed = subprocess.run(
        [node, str(_BRIDGE_SCRIPT)],
        input=source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CompileBlocked(f"XML Builder topology bridge failed with exit {completed.returncode}: {diagnostic}")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CompileBlocked("XML Builder topology bridge returned invalid JSON.") from exc
    if not payload.get("ok") or not payload.get("coreInputXmlText"):
        raise CompileBlocked(f"XML Builder topology bridge returned no InputXML: {payload.get('error') or 'unknown error'}")

    _record_bridge_diagnostics(payload, ledger)
    bridged = _parse_xml(payload["coreInputXmlText"].encode("utf-8"), "xml-builder-bridge.input.xml")
    _sanitize_bridge_output(bridged, source_root, ledger)
    model = next(child for child in bridged if _local(child.tag) == "PIPINGMODEL" and _namespace(child.tag) == "")
    _extension_record(
        model,
        producer=ledger.producer,
        dialect=ledger.dialect,
        name="XmlBuilderSourceDocument",
        source_path=ledger.source_path,
        source_entity_id="document",
        source_field="sourceXml",
        value=source.decode("utf-8", errors="strict"),
        disposition="PRESERVE_EXTENSION",
        criticality="MEDIUM",
        confidence=1.0,
    )
    side_load = str(payload.get("elementSideLoadText") or "").strip()
    if side_load:
        _extension_record(
            model,
            producer=ledger.producer,
            dialect=ledger.dialect,
            name="XmlBuilderElementSideLoad",
            source_path=ledger.source_path,
            source_entity_id="document",
            source_field="elementSideLoadText",
            value=side_load,
            disposition="PRESERVE_EXTENSION",
            criticality="MEDIUM",
            confidence=1.0,
        )
    _ensure_extension_last(model)
    elements = [child for child in model if _local(child.tag) == "PIPINGELEMENT" and _namespace(child.tag) == ""]
    element_ids = [str(element.get("ID") or f"bridge-element-{index}") for index, element in enumerate(elements, 1)]
    ledger.add(
        source_entity_id="document",
        source_type="ROOT_BRANCH_NODE",
        source_fields={"bridgeProfile": "inputxml-topo", "generatedElementCount": len(elements)},
        canonical_element_ids=element_ids,
        canonical_node_ids=sorted({str(int(Decimal(element.get(field)))) for element in elements for field in ("FROM_NODE", "TO_NODE")}),
        inputxml_element_ids=element_ids,
        cii_section="ELEMENTS",
        projection_cardinality="1_TO_MANY",
        disposition="PROJECT_WITH_AUTHORITATIVE_TOPOLOGY_ENGINE",
        evidence=[
            "tabs/model-converters/xml-cii-node-to-inputxml-core.js",
            "duplicate-coordinate coalescing disabled",
            "short and ray filler synthesis disabled",
        ],
    )
    return bridged


__all__ = [name for name in globals() if not name.startswith("__")]
