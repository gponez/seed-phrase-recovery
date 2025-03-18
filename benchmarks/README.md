# Benchmarks

Measured **29 October 2026**, Apple M5 Pro, macOS 27.0 arm64, CPython 3.12.14. One process; median of three runs. Each run checks the first **1,048,576** combinations of a public zero-entropy BIP39 vector with its final two words missing.

| Phrase | Checksum-valid | Original algorithm (Python) | Packed bits | Packed bits / second | Speedup |
| --- | ---: | ---: | ---: | ---: | ---: |
| 12 words | 65,536 | 8.223 s | 0.334 s | 3,138,928 | 24.61× |
| 24 words | 4,096 | 9.415 s | 0.336 s | 3,116,563 | 27.98× |

The baseline faithfully reproduces the archived copy/join/`mnemonic.check` loop **in Python**, not the compiled Cython extension. The new implementation packs known words once, checks entropy bits directly, and builds strings only for valid candidates. Every run verifies identical ordered candidate output by SHA-256.

These measure checksum enumeration, including candidate-string construction; disk writes, checkpoints, address derivation, and wallet matching are excluded. They cover a quarter of each two-word search space. Runtime varies by hardware, word positions, and output volume. No extrapolated wallet-recovery claims.

## Reproduce

From the repository root, with the development dependencies installed:

```sh
python benchmarks/run.py --candidates 1048576 --repeats 3
```

The command overwrites [results.json](results.json), which contains individual timings, environment metadata, and output digests. Public fixtures only; no personal phrase material.
