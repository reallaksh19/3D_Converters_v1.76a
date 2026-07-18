from __future__ import annotations

from io import BytesIO
import json
import re
from typing import Any

from pypdf import PdfReader

from converters.scripts import pdf_to_inputxml as pdf_parser

from .base import *
from .xml import *
from .extension import *

SUPPORTED_COMPILER_DIALECTS.add("pdf-input-echo")

_BAR_TO_KPA = Decimal("100")
_SPECIAL_RIGID_TOKENS = ("VALVE", "FLANGE", "FLAN", "GASK", "BLIND", "INSTRUMENT", "REDUCER")
_UNSUPPORTED_REPORT_MARKERS = {
    "HANGERS": re.compile(r"(?im)^\s*HANGERS?\s*$"),
    "DISPLACEMENTS": re.compile(r"(?im)^\s*DISPLACEMENTS?\s*$"),
    "FORCESMOMENTS": re.compile(r"(?im)^\s*(?:FORCES?\s*(?:AND|/)?\s*MOMENTS?|FORCE/MOMENT)\s*$"),
    "UNIFORM": re.compile(r"(?im)^\s*UNIFORM(?:\s+LOADS?)?\s*$"),
    "WIND": re.compile(r"(?im)^\s*WIND(?:\s+LOADS?)?\s*$"),
    "OFFSETS": re.compile(r"(?im)^\s*OFFSETS?\s*$"),
    "ALLOWABLESTRESS": re.compile(r"(?im)^\s*ALLOWABLE\s+STRESS(?:ES)?\s*$"),
}
_FIELD_MARKERS = {
    "DIAMETER": re.compile(r"\bDia\s*=", re.I),
    "WALL_THICK": re.compile(r"\bWall\s*=", re.I),
    "INSUL_THICK": re.compile(r"\bInsul\s+Thk\s*=", re.I),
    "CORR_ALLOW": re.compile(r"\bCor\s*=", re.I),
    "TEMP_EXP_C1": re.compile(r"\bT1\s*=", re.I),
    "PRESSURE_C1": re.compile(r"\bP1\s*=", re.I),
    "HYDRO_PRESSURE": re.compile(r"\bPHyd\s*=", re.I),
    "MODULUS": re.compile(r"\bE\s*=", re.I),
    "POISSONS": re.compile(r"\bv\s*=", re.I),
    "PIPE_DENSITY": re.compile(r"\bPipe\s+Den\s*=", re.I),
    "INSUL_DENSITY": re.compile(r"\bInsul\s+Den\s*=", re.I),
    "FLUID_DENSITY": re.compile(r"\bFluid\s+Den\s*=", re.I),
    "MATERIAL_NUM": re.compile(r"\bMat\s*=", re.I),
}
for _index in range(1, 10):
    _FIELD_MARKERS[f"HOT_MOD{_index}"] = re.compile(rf"\bEH{_index}\s*=", re.I)


def _extract_input_echo_text(source: bytes, *, source_name: str, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if source.lstrip().startswith(b"%PDF"):
        try:
            reader = PdfReader(BytesIO(source))
            page_text = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:  # pypdf exposes several parser-specific exception classes
            raise CompileBlocked(f"PDF Input Echo extraction failed for {source_name}: {exc}") from exc
        text = "\n".join(page_text)
        provenance = {
            "sourceKind": "PDF_TEXT_LAYER",
            "pageCount": len(page_text),
            "pageTextHashes": [_sha256(value.encode("utf-8")) for value in page_text],
        }
    else:
        if str(context.get("sourceKind") or "").lower() not in {"extracted-text", "pdf-extracted-text"}:
            raise CompileBlocked(
                "pdf-input-echo non-PDF input is accepted only with explicit context sourceKind='extracted-text'."
            )
        try:
            text = source.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CompileBlocked("Extracted Input Echo text must be strict UTF-8.") from exc
        provenance = {
            "sourceKind": "EXTRACTED_TEXT",
            "pageCount": context.get("pageCount"),
            "extractor": str(context.get("extractor") or "caller-supplied"),
        }
    if not pdf_parser._is_input_echo_text(text):
        raise CompileBlocked("Input does not contain the CAESAR Input Echo/Input Listing and PIPE DATA signatures.")
    provenance["textHashSha256"] = _sha256(text.encode("utf-8"))
    provenance["textLength"] = len(text)
    return text, provenance


def _strict_report_header(text: str) -> tuple[str, str, str]:
    job_match = re.search(r"(?im)^\s*Job Name:\s*([^\r\n]+)", text)
    if job_match is None or not job_match.group(1).strip():
        raise CompileBlocked("Input Echo report is missing an explicit non-empty Job Name header.")
    datetime_match = re.search(
        r"Date:\s*([A-Z]{3})\s+(\d{1,2}),\s*(\d{4})\s+Time:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?",
        text,
        flags=re.IGNORECASE,
    )
    if datetime_match is None:
        raise CompileBlocked("Input Echo report is missing an explicit Date/Time header; current-clock substitution is forbidden.")
    job_name, date_text, time_text = pdf_parser._parse_job_header(text)
    seconds = datetime_match.group(6)
    if seconds is not None:
        time_text = f"{int(datetime_match.group(4)):02d}:{int(datetime_match.group(5)):02d}:{int(seconds):02d}"
    return job_name, date_text, time_text


def _input_echo_blocks(text: str) -> list[dict[str, Any]]:
    primary = pdf_parser._slice_primary_section(text)
    header_pattern = re.compile(r"^\s*From\s+(\d+)(?:\s+\S+)?\s+To\s+(\d+)(?:\s+\S+)?(.*)$", re.I)
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in primary.splitlines():
        match = header_pattern.match(raw_line)
        if match:
            if current is not None:
                blocks.append(current)
            current = {
                "fromNode": int(match.group(1)),
                "toNode": int(match.group(2)),
                "header": raw_line,
                "lines": [],
            }
        elif current is not None:
            current["lines"].append(raw_line)
    if current is not None:
        blocks.append(current)
    return blocks


def _explicit_fields(block: dict[str, Any], element: Any) -> set[str]:
    content = "\n".join(block.get("lines") or [])
    explicit = {field for field, pattern in _FIELD_MARKERS.items() if pattern.search(content)}
    for axis in element.axis_present:
        explicit.add(f"DELTA_{axis[-1]}")
    if element.rigid_weight_kg is not None:
        explicit.add("RIGID")
    if element.bend is not None:
        explicit.add("BEND")
    if element.restraints:
        explicit.add("RESTRAINT")
    if element.sifs:
        explicit.add("SIF")
    return explicit


def _set_decimal_if_known(target: ET._Element, name: str, value: Any) -> bool:
    if value is None or _is_sentinel(value):
        return False
    target.set(name, _decimal_text(value, f"PDF Input Echo {name}"))
    return True


def _preserve_report_record(
    scope: ET._Element,
    *,
    name: str,
    value: Any,
    ledger: DecisionLedger,
    entity_id: str,
    disposition: str = "PRESERVE_EXTENSION",
    criticality: str = "MEDIUM",
) -> None:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    _extension_record(
        scope,
        producer=ledger.producer,
        dialect=ledger.dialect,
        name=name,
        source_path=ledger.source_path,
        source_entity_id=entity_id,
        source_field=name,
        value=text,
        disposition=disposition,
        criticality=criticality,
        confidence=1.0,
    )
    ledger.add(
        source_entity_id=f"{entity_id}@{name}",
        source_type="PDF_INPUT_ECHO_EVIDENCE",
        source_fields={name: value},
        projection_cardinality="1_TO_0",
        disposition=disposition,
        evidence=["CAESAR Input Echo text parsed by converters/scripts/pdf_to_inputxml.py grammar"],
    )


def _append_pdf_bend(target: ET._Element, bend: Any, ledger: DecisionLedger, entity_id: str) -> None:
    if bend.radius_mm <= 0:
        ledger.add(
            source_entity_id=f"{entity_id}/BEND",
            source_type="PDF_BEND",
            source_fields=vars(bend),
            projection_cardinality="1_TO_0",
            disposition="UNSUPPORTED_BLOCKING",
            diagnostics=["Input Echo bend has no positive radius"],
        )
        return
    child = ET.SubElement(target, "BEND", RADIUS=_decimal_text(bend.radius_mm, "BEND@RADIUS"))
    for suffix, angle, node in (
        ("1", bend.angle1, bend.node1),
        ("2", bend.angle2, bend.node2),
        ("3", bend.angle3, bend.node3),
    ):
        if node and node > 0:
            child.set(f"ANGLE{suffix}", _decimal_text(angle, f"BEND@ANGLE{suffix}"))
            child.set(f"NODE{suffix}", _integer_text(node, f"BEND@NODE{suffix}", positive=True))
        elif angle and abs(angle) > 1e-12:
            ledger.add(
                source_entity_id=f"{entity_id}/BEND@ANGLE{suffix}",
                source_type="PDF_BEND_ANGLE",
                source_fields={"angle": angle, "node": node},
                projection_cardinality="1_TO_0",
                disposition="UNSUPPORTED_BLOCKING",
                diagnostics=["Bend angle has no owning node"],
            )
    ledger.add(
        source_entity_id=f"{entity_id}/BEND",
        source_type="PDF_BEND",
        source_fields=vars(bend),
        canonical_element_ids=[entity_id],
        cii_section="BEND",
        projection_cardinality="1_TO_1",
        disposition="ATTACH_AUXILIARY",
    )


def _append_pdf_rigid(target: ET._Element, element: Any, ledger: DecisionLedger, entity_id: str) -> None:
    if element.rigid_weight_kg is None:
        return
    rigid_type = str(element.rigid_type or "").strip()
    ET.SubElement(target, "RIGID", WEIGHT=_decimal_text(element.rigid_weight_kg, "RIGID@WEIGHT"))
    special_identity = any(token in rigid_type.upper() for token in _SPECIAL_RIGID_TOKENS)
    _preserve_report_record(
        target,
        name="PdfRigidType",
        value=rigid_type or "UNSPECIFIED",
        ledger=ledger,
        entity_id=entity_id,
        disposition="UNSUPPORTED_BLOCKING" if special_identity else "PRESERVE_EXTENSION",
        criticality="BLOCKING" if special_identity else "MEDIUM",
    )
    ledger.add(
        source_entity_id=f"{entity_id}/RIGID",
        source_type="PDF_RIGID",
        source_fields={"weightKg": element.rigid_weight_kg, "type": rigid_type},
        canonical_element_ids=[entity_id],
        cii_section="RIGID",
        projection_cardinality="1_TO_1",
        disposition="UNSUPPORTED_BLOCKING" if special_identity else "ATTACH_AUXILIARY",
        diagnostics=["special rigid component identity requires an approved component projection"] if special_identity else [],
    )


def _preserve_incomplete_auxiliaries(target: ET._Element, element: Any, ledger: DecisionLedger, entity_id: str) -> None:
    if len(element.restraints) > 6:
        disposition = "UNSUPPORTED_BLOCKING"
        diagnostic = "Input Echo contains more than six restraints on one owner element"
    else:
        disposition = "UNSUPPORTED_BLOCKING"
        diagnostic = "Input Echo restraint grammar does not recover stiffness, gap, friction, or connecting-node semantics"
    if element.restraints:
        _preserve_report_record(
            target,
            name="PdfRestraints",
            value=[vars(row) for row in element.restraints],
            ledger=ledger,
            entity_id=entity_id,
            disposition=disposition,
            criticality="BLOCKING",
        )
        ledger.add(
            source_entity_id=f"{entity_id}/RESTRAINTS",
            source_type="PDF_RESTRAINT_BLOCK",
            source_fields=[vars(row) for row in element.restraints],
            canonical_element_ids=[entity_id],
            cii_section="RESTRANT",
            projection_cardinality="MANY_TO_ZERO",
            disposition=disposition,
            diagnostics=[diagnostic],
        )
    if element.sifs:
        _preserve_report_record(
            target,
            name="PdfSifTeeRecords",
            value=[vars(row) for row in element.sifs],
            ledger=ledger,
            entity_id=entity_id,
            disposition="UNSUPPORTED_BLOCKING",
            criticality="BLOCKING",
        )
        ledger.add(
            source_entity_id=f"{entity_id}/SIF",
            source_type="PDF_SIF_TEE_BLOCK",
            source_fields=[vars(row) for row in element.sifs],
            canonical_element_ids=[entity_id],
            cii_section="SIF&TEES",
            projection_cardinality="MANY_TO_ZERO",
            disposition="UNSUPPORTED_BLOCKING",
            diagnostics=["Input Echo SIF labels/types are meaningful but the verified canonical SIF field contract is incomplete"],
        )


def _compile_pdf_input_echo_source(
    source: bytes,
    *,
    source_name: str,
    ledger: DecisionLedger,
    context: dict[str, Any],
) -> ET._Element:
    version = str(context.get("version") or "").strip()
    if not version:
        raise CompileBlocked("PDF Input Echo adapter requires explicit context version; report-version guessing is forbidden.")
    north = context.get("north")
    if not isinstance(north, list) or len(north) != 3:
        raise CompileBlocked("PDF Input Echo adapter requires explicit context north=[x,y,z].")
    line_id = str(context.get("lineId") or context.get("defaultLine") or "").strip()
    if not line_id:
        raise CompileBlocked("PDF Input Echo adapter requires explicit context lineId/defaultLine; Job Name is not assumed to be a piping line identity.")

    text, provenance = _extract_input_echo_text(source, source_name=source_name, context=context)
    job_name, date_text, time_text = _strict_report_header(text)
    try:
        elements = pdf_parser._parse_input_echo_elements(text)
    except Exception as exc:
        raise CompileBlocked(f"Input Echo PIPE DATA parsing failed: {exc}") from exc
    blocks = _input_echo_blocks(text)
    if len(blocks) != len(elements):
        raise CompileBlocked(
            f"Input Echo block/accounting mismatch: {len(blocks)} source blocks versus {len(elements)} parsed elements."
        )

    root, model = _new_root(version)
    model.set("JOBNAME", job_name)
    model.set("TIME", f"{date_text} {time_text}")
    model.set("ISSUE_NO", str(context.get("issueNo") or ""))
    for field, value in zip(("NORTH_X", "NORTH_Y", "NORTH_Z"), north):
        model.set(field, _decimal_text(value, f"context {field}"))

    _preserve_report_record(model, name="PdfExtractionProvenance", value=provenance, ledger=ledger, entity_id="document")
    _preserve_report_record(model, name="PdfExtractedInputEchoText", value=text, ledger=ledger, entity_id="document")
    ledger.add(
        source_entity_id="document",
        source_type="PDF_INPUT_ECHO",
        source_fields={"jobName": job_name, "date": date_text, "time": time_text, "elementCount": len(elements), **provenance},
        projection_cardinality="1_TO_MANY",
        disposition="PARSE_AUTHORITATIVE_INPUT_ECHO_GRAMMAR",
        evidence=[
            "converters/scripts/pdf_to_inputxml.py::_parse_input_echo_elements",
            "internal profile/template substitution deliberately disabled",
            "current-clock and generated-default fallbacks deliberately disabled",
        ],
    )

    for marker, pattern in _UNSUPPORTED_REPORT_MARKERS.items():
        if pattern.search(text):
            _preserve_report_record(
                model,
                name=f"PdfUnsupportedSection_{marker}",
                value=marker,
                ledger=ledger,
                entity_id="document",
                disposition="UNSUPPORTED_BLOCKING",
                criticality="BLOCKING",
            )

    previous_state: dict[str, str] = {}
    for index, (element, block) in enumerate(zip(elements, blocks), 1):
        entity_id = f"PDF-PE-{index:06d}"
        target = ET.Element(
            "PIPINGELEMENT",
            ID=entity_id,
            FROM_NODE=_integer_text(element.from_node, f"{entity_id}@FROM_NODE", positive=True),
            TO_NODE=_integer_text(element.to_node, f"{entity_id}@TO_NODE", positive=True),
            LINE=line_id,
        )
        explicit = _explicit_fields(block, element)
        for axis, value in (
            ("DELTA_X", element.delta_x_mm),
            ("DELTA_Y", element.delta_y_mm),
            ("DELTA_Z", element.delta_z_mm),
        ):
            if axis in explicit:
                target.set(axis, _decimal_text(value, f"{entity_id}@{axis}"))
            else:
                target.set(axis, "0")
                ledger.add(
                    source_entity_id=f"{entity_id}@{axis}",
                    source_type="PDF_OMITTED_DELTA_AXIS",
                    source_fields={"header": block["header"], "axis": axis},
                    canonical_element_ids=[entity_id],
                    canonical_node_ids=[target.get("FROM_NODE"), target.get("TO_NODE")],
                    cii_section="ELEMENTS",
                    projection_cardinality="1_TO_1",
                    disposition="NORMALIZE_EXPLICIT_ABSENCE",
                    evidence=["CAESAR Input Echo element header omits zero delta axes"],
                    confidence={"identity":1.0,"topology":1.0,"geometry":0.95,"dimensions":1.0,"attributes":1.0,"ciiProjection":1.0},
                )
        if all(Decimal(target.get(field)) == 0 for field in ("DELTA_X", "DELTA_Y", "DELTA_Z")):
            ledger.add(
                source_entity_id=entity_id,
                source_type="PDF_ZERO_LENGTH_ELEMENT",
                source_fields=block,
                canonical_element_ids=[entity_id],
                projection_cardinality="1_TO_0",
                disposition="UNSUPPORTED_BLOCKING",
                diagnostics=["Input Echo element resolves to zero-length geometry"],
            )

        state_values = {
            "DIAMETER": element.state.diameter_mm,
            "WALL_THICK": element.state.wall_mm,
            "INSUL_THICK": element.state.insulation_mm,
            "CORR_ALLOW": element.state.corrosion_mm,
            "TEMP_EXP_C1": element.state.temp_c1,
            "PRESSURE_C1": Decimal(str(element.state.pressure1_bar)) * _BAR_TO_KPA if not _is_sentinel(element.state.pressure1_bar) else SENTINEL,
            "HYDRO_PRESSURE": Decimal(str(element.state.hydro_bar)) * _BAR_TO_KPA if not _is_sentinel(element.state.hydro_bar) else SENTINEL,
            "INSUL_DENSITY": element.state.insul_density_kg_cucm,
            "FLUID_DENSITY": element.state.fluid_density_kg_cucm,
            "MATERIAL_NUM": element.state.material_num,
        }
        for field, value in state_values.items():
            if _set_decimal_if_known(target, field, value):
                current_value = target.get(field) or ""
                if field not in explicit and field in previous_state and previous_state[field] == current_value:
                    ledger.add(
                        source_entity_id=f"{entity_id}@{field}",
                        source_type="PDF_INPUT_ECHO_INHERITANCE",
                        source_fields={"value": current_value, "sourceBlock": index},
                        canonical_element_ids=[entity_id],
                        cii_section="ELEMENTS",
                        projection_cardinality="1_TO_1",
                        disposition="EMIT_1_TO_1",
                        evidence=["Input Echo pipe state carries forward until explicitly changed"],
                        confidence={"identity":1.0,"topology":1.0,"geometry":1.0,"dimensions":0.95,"attributes":0.95,"ciiProjection":1.0},
                    )
                previous_state[field] = current_value
        if target.get("MATERIAL_NUM") is not None:
            target.set("MATERIAL_NUM", _integer_text(target.get("MATERIAL_NUM"), f"{entity_id}@MATERIAL_NUM", nonnegative=True))

        mechanical = {
            "MATERIAL_NAME": element.state.material_name,
            "MODULUS_MPA": element.state.modulus_mpa,
            "POISSONS": element.state.poisson,
            "PIPE_DENSITY_KG_CUCM": element.state.pipe_density_kg_cucm,
            **{f"HOT_MOD{hot_index}_MPA": value for hot_index, value in enumerate(element.state.hot_mod_mpa, 1)},
        }
        for name, value in mechanical.items():
            if value == "" or _is_sentinel(value):
                continue
            _preserve_report_record(target, name=f"Pdf{name}", value=value, ledger=ledger, entity_id=entity_id)

        if element.name:
            _preserve_report_record(target, name="PdfElementName", value=element.name, ledger=ledger, entity_id=entity_id)
        _append_pdf_bend(target, element.bend, ledger, entity_id) if element.bend is not None else None
        _append_pdf_rigid(target, element, ledger, entity_id)
        _preserve_incomplete_auxiliaries(target, element, ledger, entity_id)
        _ensure_extension_last(target)
        model.append(target)
        ledger.add(
            source_entity_id=entity_id,
            source_type="PDF_PIPE_DATA_ELEMENT",
            source_fields={
                "header": block["header"],
                "lines": block["lines"],
                "explicitFields": sorted(explicit),
                "parsed": {
                    "fromNode": element.from_node,
                    "toNode": element.to_node,
                    "axisPresent": element.axis_present,
                },
            },
            canonical_element_ids=[entity_id],
            canonical_node_ids=[target.get("FROM_NODE"), target.get("TO_NODE")],
            inputxml_element_ids=[entity_id],
            cii_section="ELEMENTS",
            projection_cardinality="1_TO_1",
            disposition="EMIT_1_TO_1",
        )

    counts = {
        "NUMELT": len(elements),
        "NUMNOZ": 0,
        "NOHGRS": 0,
        "NUMBEND": sum(1 for child in model.findall("PIPINGELEMENT/BEND")),
        "NUMRIGID": sum(1 for child in model.findall("PIPINGELEMENT/RIGID")),
        "NUMEXPJNT": 0,
        "NUMREST": 0,
        "NUMFORCMNT": 0,
        "NUMUNFLOAD": 0,
        "NUMWIND": 0,
        "NUMELEOFF": 0,
        "NUMALLOW": 0,
        "NUMISECT": 0,
    }
    for field, value in counts.items():
        model.set(field, str(value))
    _ensure_extension_last(model)
    return root


__all__ = [name for name in globals() if not name.startswith("__")]
