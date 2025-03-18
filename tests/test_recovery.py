from hashlib import sha256
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mnemonic import Mnemonic

from seed_recovery.core import SearchPlan, WORDLIST
from seed_recovery.storage import run_recovery

REFERENCE = Mnemonic("english")
DEMO = "abandon " * 11 + "*"


class ChecksumTests(unittest.TestCase):
    def test_wordlist_matches_reference(self):
        self.assertEqual(list(WORDLIST), REFERENCE.wordlist)

    def test_all_official_english_vectors(self):
        vectors = json.loads(Path(__file__).with_name("vectors.json").read_text())
        for vector in vectors:
            with self.subTest(entropy=vector["entropy"]):
                phrase = vector["mnemonic"]
                self.assertEqual(REFERENCE.to_mnemonic(bytes.fromhex(vector["entropy"])), phrase)
                self.assertEqual(list(SearchPlan.parse(phrase).scan()), [(0, phrase)])

    def test_missing_word_each_position_all_lengths(self):
        for length in (12, 15, 18, 21, 24):
            phrase = REFERENCE.to_mnemonic(bytes(range(length * 4 // 3)))
            for position in (0, length // 2, length - 1):
                words = phrase.split()
                words[position] = "*"
                plan = SearchPlan.parse(" ".join(words))
                actual = list(plan.scan())
                expected = [(i, plan.candidate(i)) for i in range(2048)
                            if REFERENCE.check(plan.candidate(i))]
                self.assertEqual(actual, expected)
                self.assertIn(phrase, [p for _, p in actual])

    def test_two_missing_words_ranges_and_random_positions(self):
        rng = random.Random(117)
        for length in (12, 15, 18, 21, 24):
            words = REFERENCE.to_mnemonic(bytes(range(length * 4 // 3))).split()
            for position in rng.sample(range(length), 2):
                words[position] = "*"
            plan = SearchPlan.parse(" ".join(words))
            for start in (0, 2040, plan.total - 50):
                stop = min(start + 50, plan.total)
                self.assertEqual(list(plan.scan(start, stop)),
                                 [(i, plan.candidate(i)) for i in range(start, stop)
                                  if REFERENCE.check(plan.candidate(i))])

    def test_invalid_phrase_and_range(self):
        for phrase in ("", "abandon " * 13, "abandon " * 11 + "notaword"):
            with self.assertRaises(ValueError):
                SearchPlan.parse(phrase)
        self.assertEqual(list(SearchPlan.parse("abandon " * 12).scan()), [])
        plan = SearchPlan.parse(DEMO)
        for start, stop in ((-1, 1), (2, 1), (0, 2049)):
            with self.assertRaises(ValueError):
                list(plan.scan(start, stop))

    def test_normalized_input(self):
        self.assertEqual(SearchPlan.parse(" ABANDON\t" * 11 + "*\n"), SearchPlan.parse(DEMO))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output, self.checkpoint = self.root / "candidates.txt", self.root / "progress.json"
        self.plan = SearchPlan.parse(DEMO)

    def run_search(self, **kwargs):
        return run_recovery(self.plan, self.output, self.checkpoint, **kwargs)

    def expected(self):
        return "".join(p + "\n" for _, p in self.plan.scan()).encode()

    def test_resume_matches_uninterrupted_with_no_duplicates(self):
        first = self.run_search(limit=101, batch_size=50)
        self.assertEqual(first["next_index"], 101)
        self.assertFalse(first["complete"])
        final = self.run_search(resume=True, limit=10000, batch_size=137)
        self.assertTrue(final["complete"])
        self.assertEqual(final["valid"], 128)
        self.assertEqual(self.output.read_bytes(), self.expected())
        self.assertEqual(self.run_search(resume=True)["valid"], 128)
        if os.name != "nt":
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.checkpoint.stat().st_mode & 0o777, 0o600)

    def test_interrupt_after_commit_and_uncommitted_tail(self):
        def interrupt(_):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_search(batch_size=300, on_checkpoint=interrupt)
        with self.output.open("ab") as f:
            f.write(b"uncommitted partial data")
        self.run_search(resume=True)
        self.assertEqual(self.output.read_bytes(), self.expected())

    def test_failed_checkpoint_write_repeats_batch_safely(self):
        from seed_recovery import storage
        save = storage._save
        calls = 0
        def fail_second(path, state):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated interruption")
            return save(path, state)
        with patch.object(storage, "_save", fail_second):
            with self.assertRaises(OSError):
                self.run_search(batch_size=99)
        self.run_search(resume=True)
        self.assertEqual(self.output.read_bytes(), self.expected())

    def test_wrong_config_does_not_touch_output(self):
        self.run_search(limit=400)
        before = self.output.read_bytes()
        other = SearchPlan.parse("legal winner thank year wave sausage worth useful legal winner thank *")
        with self.assertRaisesRegex(ValueError, "does not match"):
            run_recovery(other, self.output, self.checkpoint, resume=True)
        self.assertEqual(self.output.read_bytes(), before)

    def test_modified_or_truncated_output_is_rejected(self):
        self.run_search(limit=400)
        original = self.output.read_bytes()
        for changed in (b"x" + original[1:], original[:-1]):
            self.output.write_bytes(changed)
            with self.assertRaises(ValueError):
                self.run_search(resume=True)
            self.assertEqual(self.output.read_bytes(), changed)

    def test_overwrite_and_alias_rejected(self):
        self.run_search(limit=2)
        with self.assertRaises(ValueError):
            self.run_search()
        with self.assertRaises(ValueError):
            run_recovery(self.plan, self.output, self.output)
        self.checkpoint.unlink()
        with self.assertRaises(FileExistsError):
            self.run_search()

    @unittest.skipIf(os.name == "nt", "POSIX symlink and permission semantics")
    def test_symlink_output_rejected(self):
        target = self.root / "target"
        target.write_bytes(b"keep")
        self.output.symlink_to(target)
        with self.assertRaises(OSError):
            self.run_search()
        self.assertEqual(target.read_bytes(), b"keep")

    def test_bad_checkpoint_counter_rejected(self):
        self.run_search(limit=1)
        state = json.loads(self.checkpoint.read_text())
        state["next_index"] = self.plan.total + 1
        self.checkpoint.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "search range"):
            self.run_search(resume=True)

    def test_concurrent_writer_is_rejected(self):
        from seed_recovery.storage import _lock
        lock_path = self.checkpoint.with_name(self.checkpoint.name + ".lock")
        code = "from pathlib import Path; from seed_recovery.storage import _lock; import sys\nwith _lock(Path(sys.argv[1])): pass"
        with _lock(lock_path):
            result = subprocess.run([sys.executable, "-c", code, str(lock_path)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another process", result.stderr)

    def test_copied_checkpoint_cannot_write_locked_output(self):
        from seed_recovery.storage import _acquire_lock
        self.run_search(limit=400)
        backup = self.root / "backup.json"
        backup.write_bytes(self.checkpoint.read_bytes())
        before = self.output.read_bytes()
        with self.output.open("r+b") as handle:
            _acquire_lock(handle)
            code = ("from pathlib import Path; import sys; from seed_recovery.core import SearchPlan; "
                    "from seed_recovery.storage import run_recovery; "
                    "run_recovery(SearchPlan.parse('abandon '*11+'*'), Path(sys.argv[1]), "
                    "Path(sys.argv[2]), resume=True, limit=1)")
            result = subprocess.run([sys.executable, "-c", code, str(self.output), str(backup)],
                                    capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another process", result.stderr)
        self.assertEqual(self.output.read_bytes(), before)
        self.run_search(resume=True)
        self.assertEqual(self.output.read_bytes(), self.expected())

    def test_lock_path_collision_rejected_before_mutation(self):
        lock_path = self.checkpoint.with_name(self.checkpoint.name + ".lock")
        lock_path.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            run_recovery(self.plan, lock_path, self.checkpoint)
        self.assertEqual(lock_path.read_bytes(), b"keep")


class CliTests(unittest.TestCase):
    def test_demo(self):
        result = subprocess.run([sys.executable, "-m", "seed_recovery", "demo"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["checksum_valid"], 128)
        self.assertTrue(data["expected_phrase_found"])


if __name__ == "__main__":
    unittest.main()
