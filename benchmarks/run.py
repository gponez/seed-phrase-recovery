"""Compare a faithful interpreted original loop with the packed-bit engine."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mnemonic import Mnemonic
from seed_recovery.core import SearchPlan


def original_reference(plan, stop, reference):
    """Original enumerate/copy/join/mnemonic.check algorithm, without Cython."""
    words, positions = list(plan.words), plan.positions
    valid = []
    for index in range(stop):
        remaining = index
        combination = [0] * len(positions)
        for i in range(len(positions) - 1, -1, -1):
            combination[i] = remaining % 2048
            remaining //= 2048
        candidate = words[:]
        for i, position in enumerate(positions):
            candidate[position] = reference.wordlist[combination[i]]
        phrase = " ".join(candidate)
        if reference.check(phrase):
            valid.append((index, phrase))
    return valid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=int, default=1048576)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results.json"))
    args = parser.parse_args()
    if not 1 <= args.candidates <= 2048 ** 2 or args.repeats < 1:
        parser.error("Use 1..4194304 candidates and at least one repeat.")
    reference = Mnemonic("english")
    results = []
    for length in (12, 24):
        words = reference.to_mnemonic(bytes(length * 4 // 3)).split()
        words[-2:] = ["*", "*"]
        plan = SearchPlan.parse(" ".join(words))
        functions = {"original_algorithm_python": lambda: original_reference(plan, args.candidates, reference),
                     "packed_bits": lambda: list(plan.scan(0, args.candidates))}
        timings = {name: [] for name in functions}
        # Warm both paths and compare their actual candidate output, not only counts.
        expected = original_reference(plan, min(2048, args.candidates), reference)
        assert list(plan.scan(0, min(2048, args.candidates))) == expected
        digest, count = None, None
        for repeat in range(args.repeats):
            order = list(functions) if repeat % 2 == 0 else list(reversed(functions))
            for name in order:
                start = time.perf_counter()
                output = functions[name]()
                timings[name].append(time.perf_counter() - start)
                actual = hashlib.sha256(json.dumps(output).encode()).hexdigest()
                if digest is not None:
                    assert digest == actual, "Benchmark implementations disagree"
                digest, count = actual, len(output)
        measured = {name: {"seconds": times, "median_seconds": statistics.median(times),
                           "candidates_per_second": args.candidates / statistics.median(times)}
                    for name, times in timings.items()}
        results.append({"words": length, "missing_positions_1_based": [length - 1, length],
                        "checked": args.candidates, "checksum_valid": count, "output_sha256": digest,
                        "implementations": measured,
                        "speedup": measured["original_algorithm_python"]["median_seconds"] /
                                   measured["packed_bits"]["median_seconds"]})
        print(f"{length} words: {results[-1]['speedup']:.2f}x", flush=True)
    cpu = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        try:
            cpu = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"],
                                          text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    report = {"measured_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0],
              "platform": platform.platform(), "cpu": cpu, "repeats": args.repeats,
              "method": "One process; ordered prefix of 2-missing-word public zero-entropy fixtures; no file I/O; interpreted original algorithm, not compiled Cython.",
              "results": results}
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
