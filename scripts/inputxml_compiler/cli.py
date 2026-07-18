from .base import *
from .compiler import *
def _write_outputs(input_path: Path, output_dir: Path, canonical: ET._Element | None, ledger: dict[str, Any], validation: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = input_path.stem
    outputs: dict[str, str] = {}
    if canonical is not None:
        canonical_path = output_dir / f"{stem}.canonical.input.xml"
        canonical_path.write_bytes(_xml_bytes(canonical))
        outputs["canonical"] = str(canonical_path)
    ledger_path = output_dir / f"{stem}.canonicalization-ledger.json"
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    validation_path = output_dir / f"{stem}.canonical-validation.json"
    validation_path.write_text(json.dumps(validation, indent=2, sort_keys=True), encoding="utf-8")
    outputs["ledger"] = str(ledger_path)
    outputs["validation"] = str(validation_path)
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dialect")
    parser.add_argument("--context", type=Path)
    parser.add_argument("--source-repository", default="unknown")
    parser.add_argument("--source-path")
    parser.add_argument("--producer", default="inputxml-canonical-compiler")
    parser.add_argument("--contracts-dir", type=Path, default=Path(__file__).resolve().parents[2] / "contracts" / "inputxml" / "v1")
    args = parser.parse_args(argv)
    context = _load_json(args.context) if args.context else {}
    source = args.input.read_bytes()
    contracts = args.contracts_dir
    try:
        canonical, ledger, validation = compile_document(
            source,
            source_name=args.input.name,
            source_repository=args.source_repository,
            source_path=args.source_path or str(args.input),
            producer=args.producer,
            registry_path=contracts / "inputxml-dialect-registry.json",
            alias_registry_path=contracts / "catalogs" / "inputxml-alias-registry.json",
            canonical_xsd_path=contracts / "inputxml-canonical-v1.xsd",
            categorized_xsd_path=contracts / "categorized-inputxml-v3.xsd",
            semantic_rules_path=contracts / "inputxml-semantic-rules-v1.sch",
            dialect_override=args.dialect,
            context=context,
        )
    except CompileBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    outputs = _write_outputs(args.input, args.output_dir, canonical, ledger, validation)
    print(json.dumps({"status": validation["status"], "outputs": outputs}, indent=2))
    return 0 if validation["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [name for name in globals() if not name.startswith("__")]
