# Seed phrase recovery

Offline recovery of missing words in **English BIP39** phrases. Supports 12, 15, 18, 21, and 24 words; use `*` for each missing position. Python 3.10+; no runtime dependencies or compiler.

**A valid checksum produces a candidate, not proof of the correct wallet.** This tool does not derive addresses, check balances, or recover passphrases.

## Demo

```sh
python3 -m seed_recovery demo
```

Checks all 2,048 replacements in a public test vector: **128 candidates**, including the expected phrase. No network, files, or credentials required.

## Recover

Try the included public example:

```sh
python3 -m seed_recovery recover --phrase-file examples/one-missing.txt \
  --output candidates.txt --checkpoint progress.json
```

For your own phrase, omit `--phrase-file` to enter it without echo. Candidates stay in the output file; the terminal shows counts only. Keep input, output, and checkpoint files private. POSIX output permissions are `0600`; on Windows, use a private user folder.

The search contains `2048 ** missing_words` combinations. Each run checks at most **1,000,000**; change `--limit` as needed. Repeat the same command with `--resume` to continue. Checkpoints verify the input and output, reject changed files, and safely repeat interrupted batches. Existing output is never overwritten by a fresh run.

## Verify

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python benchmarks/run.py
```

Tests cover official vectors, an independent BIP39 implementation, every supported phrase length, resume, interruption, tampering, and concurrent writers. [Measured benchmarks](benchmarks/README.md) compare the original algorithm in Python with packed-bit validation. `python -m pip install .` optionally installs the `seed-recovery` command.

## Sources

English word list and public vectors: [BIP39](https://github.com/bitcoin/bips/blob/master/bip-0039.mediawiki) and [python-mnemonic](https://github.com/trezor/python-mnemonic), MIT; see [third-party license](THIRD_PARTY_LICENSES.txt).
