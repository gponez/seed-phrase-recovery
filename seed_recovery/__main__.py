import argparse
from getpass import getpass
import json
from pathlib import Path
import sys
import time

from .core import SearchPlan
from .storage import run_recovery


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline English BIP39 checksum candidate recovery.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Run a public 12-word BIP39 test vector; no files or network.")
    recover = commands.add_parser("recover", help="Fill * placeholders; write candidates to a local file.")
    recover.add_argument("--phrase-file", type=Path, help="UTF-8 phrase file; otherwise prompt without echo.")
    recover.add_argument("--output", required=True, type=Path)
    recover.add_argument("--checkpoint", required=True, type=Path)
    recover.add_argument("--resume", action="store_true")
    recover.add_argument("--limit", type=int, default=1_000_000, help="Maximum checks in this run (default: 1000000).")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            plan = SearchPlan.parse("abandon " * 11 + "*")
            start = time.perf_counter()
            results = list(plan.scan())
            expected = "abandon " * 11 + "about"
            found = any(phrase == expected for _, phrase in results)
            print(json.dumps({"fixture": "public BIP39 zero-entropy vector", "checked": plan.total,
                              "checksum_valid": len(results), "expected_phrase_found": found,
                              "seconds": round(time.perf_counter() - start, 6)}, indent=2))
            return 0 if found and len(results) == 128 else 1
        protected = (args.output, args.checkpoint, args.checkpoint.with_name(args.checkpoint.name + ".lock"))
        if args.phrase_file and args.phrase_file.resolve() in tuple(path.resolve() for path in protected):
            raise ValueError("Phrase input must be separate from output and checkpoint files.")
        phrase = args.phrase_file.read_text(encoding="utf-8") if args.phrase_file else getpass("Phrase (* for missing words): ")
        plan = SearchPlan.parse(phrase)
        start = time.perf_counter()
        result = run_recovery(plan, args.output, args.checkpoint, resume=args.resume, limit=args.limit)
        print(json.dumps({"checked_total": result["next_index"], "search_space": result["total"],
                          "checksum_valid_total": result["valid"], "complete": result["complete"],
                          "seconds_this_run": round(time.perf_counter() - start, 6)}, indent=2))
        if not result["complete"]:
            print("Run again with --resume to continue from the committed checkpoint.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Use --resume; uncommitted work will be repeated safely.", file=sys.stderr)
        return 130
    except (ValueError, OSError, EOFError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
