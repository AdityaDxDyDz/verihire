"""Command line entry point for the fully offline JD parser."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow ``python main.py`` from the ml directory without installing the package.
ML_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_ROOT))

from docint.pipeline import SUPPORTED_TYPES, process_batch  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jd-parser",
        description="Offline job description parser: PDF/DOCX/TXT to JobSpec JSON.",
    )
    parser.add_argument("--input", "-i", required=True,
                        help="a job description file or a folder searched recursively")
    parser.add_argument("--output", "-o", default="output",
                        help="output directory (default: output)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        print(f"error: input not found: {input_path}", file=sys.stderr)
        return 2
    if input_path.is_file() and input_path.suffix.lower() not in SUPPORTED_TYPES:
        print(f"error: unsupported file type '{input_path.suffix}'", file=sys.stderr)
        return 2
    if input_path.is_dir() and not any(
        p.is_file() and p.suffix.lower() in SUPPORTED_TYPES
        and not p.name.startswith(("~$", ".")) for p in input_path.rglob("*")
    ):
        print(f"error: no PDF/DOCX/TXT files found in {input_path}", file=sys.stderr)
        return 2
    status, _ = process_batch(input_path, Path(args.output).expanduser())
    return status


if __name__ == "__main__":
    sys.exit(main())
