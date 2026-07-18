from __future__ import annotations

from typing import Any

from .base import *
from .xml import *
from .xml_builder import _compile_xml_builder_source

SUPPORTED_COMPILER_DIALECTS.add("seljson-custom-root")


def _rename_shared_bridge_evidence(root: ET._Element) -> None:
    model = next(
        (child for child in root if _namespace(child.tag) == "" and _local(child.tag) == "PIPINGMODEL"),
        None,
    )
    if model is None:
        return
    extension = model.find(f"{{{EXT_NS}}}Extension")
    if extension is None:
        return
    names = {
        "XmlBuilderSourceDocument": "SelectionJsonSourceDocument",
        "XmlBuilderElementSideLoad": "SelectionJsonElementSideLoad",
    }
    for record in extension.findall(f"{{{EXT_NS}}}Record"):
        current = record.get("name") or ""
        if current in names:
            record.set("name", names[current])


def _compile_seljson_custom_root(
    source: bytes,
    *,
    source_root: ET._Element,
    ledger: DecisionLedger,
    context: dict[str, Any],
) -> ET._Element:
    bridged = _compile_xml_builder_source(
        source,
        source_root=source_root,
        ledger=ledger,
        context=context,
    )
    _rename_shared_bridge_evidence(bridged)
    model = next(
        child for child in bridged
        if _namespace(child.tag) == "" and _local(child.tag) == "PIPINGMODEL"
    )
    elements = [
        child for child in model
        if _namespace(child.tag) == "" and _local(child.tag) == "PIPINGELEMENT"
    ]
    element_ids = [str(element.get("ID") or f"selectionjson-element-{index}") for index, element in enumerate(elements, 1)]
    ledger.add(
        source_entity_id="selectionjson-document",
        source_type="SELECTIONJSON_CUSTOM_ROOT",
        source_fields={"dialect": "seljson-custom-root"},
        canonical_element_ids=element_ids,
        canonical_node_ids=sorted({
            str(int(Decimal(element.get(field))))
            for element in elements for field in ("FROM_NODE", "TO_NODE")
        }),
        inputxml_element_ids=element_ids,
        cii_section="ELEMENTS",
        projection_cardinality="1_TO_MANY",
        disposition="PROJECT_WITH_AUTHORITATIVE_TOPOLOGY_ENGINE",
        evidence=[
            "converters/seljson-to-inputxml.js custom Root/Branch/Node contract",
            "shared XML Builder topology bridge with SelectionJSON-specific evidence identity",
        ],
    )
    return bridged


__all__ = [name for name in globals() if not name.startswith("__")]
