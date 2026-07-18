from .base import *
from .xml import *
from .extension import *
from .counts import *
from .children import *
from .elements import *
from .family import *
from .model import *
from .categorized import *
from .validation import *
from .xml_builder import *
from .seljson import *
from .managed_stage import *
from .pdf_input_echo import *
from .pdf_input_echo_bindings import *
from .uxml_rvm import *
from .ledger_reconciliation import *


def _json_dialect(source: bytes, dialect_override: str | None) -> str:
    if dialect_override:
        return dialect_override
    try:
        payload = json.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompileBlocked(f"JSON intake could not be parsed: {exc}") from exc
    candidate = payload.get("uxml") if isinstance(payload, dict) and isinstance(payload.get("uxml"), dict) else payload
    if isinstance(candidate, dict) and candidate.get("schemaVersion") == "uxml-topology-v1":
        return "uxml-rvm-intake"
    if isinstance(payload, dict) and any(key in payload for key in ("rvmRows", "rows", "inputKind")):
        return "uxml-rvm-intake"
    return "managed-stage-json"


def compile_document(
    source: bytes,
    *,
    source_name: str,
    source_repository: str,
    source_path: str,
    producer: str,
    registry_path: Path,
    alias_registry_path: Path,
    canonical_xsd_path: Path,
    categorized_xsd_path: Path,
    semantic_rules_path: Path,
    dialect_override: str | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[ET._Element | None, dict[str, Any], dict[str, Any]]:
    context = context or {}
    registry = _load_json(registry_path)
    dialects = _registered_dialects(registry)
    stripped = source.lstrip()
    json_source = dialect_override in {"managed-stage-json", "uxml-rvm-intake"} or stripped[:1] in {b"{", b"["}
    pdf_source = dialect_override == "pdf-input-echo" or stripped.startswith(b"%PDF")
    root: ET._Element | None
    if pdf_source:
        root = None
        dialect = dialect_override or "pdf-input-echo"
    elif json_source:
        root = None
        dialect = _json_dialect(source, dialect_override)
    else:
        root = _parse_xml(source, source_name)
        dialect = dialect_override or detect_dialect(root, registry)
    if dialect not in dialects:
        raise CompileBlocked(f"Dialect {dialect!r} is not registered.")
    ledger = DecisionLedger(source_repository, source_path, producer, dialect)
    source_hash = _sha256(source)
    validation: dict[str, Any] = {"schema":"InputXmlCanonicalValidation.v1","dialect":dialect,"status":"FAILED","xsdErrors":[],"semanticErrors":[],"diagnostics":[]}
    if dialect not in SUPPORTED_COMPILER_DIALECTS:
        acceptance = dialects[dialect].get("canonicalAcceptance")
        disposition = "REJECT_INVALID" if acceptance == "EXCLUDED" else ("DEFER_EXPLICITLY" if acceptance == "DIAGNOSTIC_ONLY" else "UNSUPPORTED_BLOCKING")
        ledger.add(source_entity_id="document", source_type="DIALECT", source_fields={"dialect":dialect,"canonicalAcceptance":acceptance}, disposition=disposition, diagnostics=["registered dialect has no PR-D adapter implementation"])
        ledger.blocked = True
        validation.update(status="BLOCKED", diagnostics=ledger.diagnostics + [{"code":"DIALECT_ADAPTER_NOT_IMPLEMENTED","dialect":dialect,"disposition":disposition}])
        return None, ledger.as_dict(source_hash, None), validation

    try:
        aliases = _resolve_aliases(_load_json(alias_registry_path))
        if dialect == "uxml-rvm-intake":
            wrapped = _compile_uxml_rvm_source(source, source_name=source_name, source_path=source_path, ledger=ledger, context=context)
            canonical = _compile_inputxml_family(wrapped, dialect=dialect, ledger=ledger, aliases=aliases, context=context)
        elif dialect == "pdf-input-echo":
            wrapped = _compile_pdf_input_echo_source(source, source_name=source_name, ledger=ledger, context=context)
            canonical = _compile_inputxml_family(wrapped, dialect=dialect, ledger=ledger, aliases=aliases, context=context)
        elif dialect == "managed-stage-json":
            wrapped = _compile_managed_stage_source(source, source_name=source_name, ledger=ledger, context=context)
            canonical = _compile_inputxml_family(wrapped, dialect=dialect, ledger=ledger, aliases=aliases, context=context)
        elif dialect == "canonical-inputxml-v1":
            if root is None:
                raise CompileBlocked("Canonical InputXML requires XML input.")
            canonical = copy.deepcopy(root)
            xsd_errors = _validate_xsd(canonical, canonical_xsd_path)
            semantic_errors = [] if xsd_errors else _validate_schematron(canonical, semantic_rules_path)
            if xsd_errors or semantic_errors:
                raise CompileBlocked("Direct canonical input failed canonical validation.")
            _ledger_direct_canonical(canonical, ledger)
        elif dialect == "inputxml-fragment":
            if root is None:
                raise CompileBlocked("InputXML fragment adapter requires XML input.")
            wrapped = _fragment_document(root, context)
            canonical = _compile_inputxml_family(wrapped, dialect=dialect, ledger=ledger, aliases=aliases, context=context)
        elif dialect == "categorized-inputxml-v3":
            if root is None:
                raise CompileBlocked("CategorizedInputXML adapter requires XML input.")
            categorized_errors = _validate_xsd(root, categorized_xsd_path)
            if categorized_errors:
                raise CompileBlocked("CategorizedInputXML failed categorized-inputxml-v3.xsd: " + "; ".join(categorized_errors))
            wrapped = _categorized_document(root, context, ledger)
            canonical = _compile_inputxml_family(wrapped, dialect=dialect, ledger=ledger, aliases=aliases, context=context)
        elif dialect == "xml-builder-root-branch-node":
            if root is None:
                raise CompileBlocked("XML Builder adapter requires XML input.")
            wrapped = _compile_xml_builder_source(source, source_root=root, ledger=ledger, context=context)
            canonical = _compile_inputxml_family(wrapped, dialect=dialect, ledger=ledger, aliases=aliases, context=context)
        elif dialect == "seljson-custom-root":
            if root is None:
                raise CompileBlocked("SelectionJSON custom-root adapter requires XML input.")
            wrapped = _compile_seljson_custom_root(source, source_root=root, ledger=ledger, context=context)
            canonical = _compile_inputxml_family(wrapped, dialect=dialect, ledger=ledger, aliases=aliases, context=context)
        else:
            if root is None:
                raise CompileBlocked(f"Dialect {dialect} requires XML input.")
            canonical = _compile_inputxml_family(root, dialect=dialect, ledger=ledger, aliases=aliases, context=context)
            if dialect == "xml-to-cii-diagnostic-sidecar":
                _extension_record(canonical, producer=ledger.producer, dialect=ledger.dialect, name="DiagnosticSidecarReconciliation", source_path=ledger.source_path, source_entity_id="document", source_field="dialect", value="Diagnostic reconstruction requires independent source reconciliation before production use.", disposition="DEFER_EXPLICITLY", criticality="HIGH", confidence=0.80)
                _ensure_extension_last(canonical)
                ledger.add(source_entity_id="document", source_type="DIAGNOSTIC_RECONSTRUCTION", source_fields={"dialect":dialect}, disposition="DEFER_EXPLICITLY", evidence=["diagnostic sidecar is not assumed to be the exact writer-consumed model"], confidence={"identity":0.80,"topology":0.80,"geometry":0.80,"dimensions":0.80,"attributes":0.80,"ciiProjection":0.80})
                ledger.blocked = True
                ledger.diagnostic("BLOCKING", "DIAGNOSTIC_RECONCILIATION_REQUIRED", "Diagnostic InputXML sidecar requires independent source reconciliation.")

        reconcile_decision_ledger(canonical, ledger, context=context)
        xsd_errors = _validate_xsd(canonical, canonical_xsd_path)
        semantic_errors = [] if xsd_errors else _validate_schematron(canonical, semantic_rules_path)
        validation["xsdErrors"] = xsd_errors
        validation["semanticErrors"] = semantic_errors
        if xsd_errors:
            ledger.diagnostic("ERROR", "CANONICAL_XSD_FAILED", "Canonical output failed XSD.", errors=xsd_errors)
        if semantic_errors:
            ledger.diagnostic("BLOCKING", "CANONICAL_SEMANTIC_FAILED", "Canonical output failed Schematron.", errors=semantic_errors)
        status = "BLOCKED" if ledger.blocked or xsd_errors or semantic_errors else "PASS"
        validation["status"] = status
        validation["diagnostics"] = ledger.diagnostics
        output = _xml_bytes(canonical)
        return canonical, ledger.as_dict(source_hash, _sha256(output)), validation
    except CompileBlocked as exc:
        ledger.diagnostic("BLOCKING", "CANONICALIZATION_BLOCKED", str(exc))
        validation.update(status="BLOCKED", diagnostics=ledger.diagnostics)
        return None, ledger.as_dict(source_hash, None), validation


__all__ = [name for name in globals() if not name.startswith("__")]
