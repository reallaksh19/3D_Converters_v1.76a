from .base import *
from .xml import *
def _extension(scope: ET._Element, producer: str, dialect: str) -> ET._Element:
    existing = scope.find(f"{{{EXT_NS}}}Extension")
    if existing is not None:
        return existing
    return ET.SubElement(
        scope,
        ET.QName(EXT_NS, "Extension"),
        schema="inputxml-extension/v1",
        producer=producer,
        dialect=dialect,
    )


def _ensure_extension_last(scope: ET._Element) -> None:
    ext = scope.find(f"{{{EXT_NS}}}Extension")
    if ext is not None and len(scope) and scope[-1] is not ext:
        scope.remove(ext)
        scope.append(ext)


def _extension_record(
    scope: ET._Element,
    *,
    producer: str,
    dialect: str,
    name: str,
    source_path: str,
    source_entity_id: str,
    source_field: str,
    value: str,
    disposition: str,
    criticality: str,
    confidence: float,
) -> None:
    ext = _extension(scope, producer, dialect)
    record = ET.SubElement(
        ext,
        ET.QName(EXT_NS, "Record"),
        name=_safe_ncname(name, "FIELD"),
        sourcePath=source_path,
        sourceEntityId=source_entity_id,
        sourceField=source_field,
        disposition=disposition,
        criticality=criticality,
        confidence=f"{confidence:.2f}",
    )
    record.text = value


def _copy_existing_extension(source: ET._Element, target: ET._Element, ledger: DecisionLedger, entity_id: str) -> None:
    ext = source.find(f"{{{EXT_NS}}}Extension")
    if ext is None:
        return
    target_ext = _extension(target, ledger.producer, ledger.dialect)
    for index, record in enumerate(ext.findall(f"{{{EXT_NS}}}Record"), 1):
        target_ext.append(copy.deepcopy(record))
        disposition = record.get("disposition") or "UNSUPPORTED_BLOCKING"
        ledger.add(
            source_entity_id=f"{entity_id}/Extension/Record[{index}]",
            source_type="EXTENSION_RECORD",
            source_fields=dict(record.attrib),
            disposition=disposition,
            evidence=["existing extension record preserved verbatim"],
        )
    _ensure_extension_last(target)


def _preserve_attr(
    target_scope: ET._Element,
    source_path: str,
    entity_id: str,
    name: str,
    value: str,
    ledger: DecisionLedger,
    *,
    blocking: bool,
) -> None:
    disposition = "UNSUPPORTED_BLOCKING" if blocking else "PRESERVE_EXTENSION"
    criticality = "BLOCKING" if blocking else "MEDIUM"
    _extension_record(
        target_scope,
        producer=ledger.producer,
        dialect=ledger.dialect,
        name=name,
        source_path=source_path,
        source_entity_id=entity_id,
        source_field=name,
        value=value,
        disposition=disposition,
        criticality=criticality,
        confidence=1.0,
    )
    ledger.add(
        source_entity_id=f"{entity_id}@{name}",
        source_type="ATTRIBUTE",
        source_fields={name: value},
        disposition=disposition,
        evidence=["preserved in explicit extension namespace"],
    )


def _preserve_child(target_scope: ET._Element, child: ET._Element, source_path: str, entity_id: str, ledger: DecisionLedger, *, blocking: bool) -> None:
    name = _local(child.tag)
    value = ET.tostring(child, encoding="unicode")
    disposition = "UNSUPPORTED_BLOCKING" if blocking else "PRESERVE_EXTENSION"
    _extension_record(
        target_scope,
        producer=ledger.producer,
        dialect=ledger.dialect,
        name=name,
        source_path=source_path,
        source_entity_id=entity_id,
        source_field=name,
        value=value,
        disposition=disposition,
        criticality="BLOCKING" if blocking else "MEDIUM",
        confidence=1.0,
    )
    ledger.add(
        source_entity_id=f"{entity_id}/{name}",
        source_type="CHILD_ELEMENT",
        source_fields={"xml": value},
        disposition=disposition,
        evidence=["preserved in explicit extension namespace"],
    )



__all__ = [name for name in globals() if not name.startswith("__")]
