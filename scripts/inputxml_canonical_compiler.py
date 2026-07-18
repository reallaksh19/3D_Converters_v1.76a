#!/usr/bin/env python3
"""CLI compatibility wrapper for the InputXML canonical compiler package."""

from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.inputxml_compiler.base import CompileBlocked, EXT_NS, SUPPORTED_COMPILER_DIALECTS
from scripts.inputxml_compiler import compiler as _compiler
from scripts.inputxml_compiler import cli as _cli
from scripts.inputxml_compiler.xml import detect_dialect

compile_document = _compiler.compile_document
main = _cli.main

if __name__ == "__main__":
    raise SystemExit(main())
