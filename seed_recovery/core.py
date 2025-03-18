"""Enumerate missing English BIP39 words without building invalid strings."""

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
import unicodedata

WORDLIST = tuple(files(__package__).joinpath("english.txt").read_text().splitlines())
WORD_INDEX = {word: index for index, word in enumerate(WORDLIST)}
WORDLIST_HASH = sha256("\n".join(WORDLIST).encode()).hexdigest()
if len(WORDLIST) != 2048 or len(WORD_INDEX) != 2048:
    raise RuntimeError("The English word list must contain 2048 unique words.")


@dataclass(frozen=True)
class SearchPlan:
    words: tuple[str, ...]
    positions: tuple[int, ...]
    shifts: tuple[int, ...]
    known_bits: int
    checksum_bits: int
    entropy_bytes: int

    @classmethod
    def parse(cls, phrase: str) -> "SearchPlan":
        words = tuple(unicodedata.normalize("NFKD", phrase).lower().split())
        if len(words) not in (12, 15, 18, 21, 24):
            raise ValueError("Expected 12, 15, 18, 21, or 24 English BIP39 words.")
        positions = tuple(i for i, word in enumerate(words) if word == "*")
        known = 0
        for index, word in enumerate(words):
            known <<= 11
            if word != "*":
                if word not in WORD_INDEX:
                    raise ValueError(f"Word {index + 1} is not in the English BIP39 word list.")
                known |= WORD_INDEX[word]
        checksum_bits = len(words) // 3
        return cls(words, positions, tuple((len(words) - 1 - i) * 11 for i in positions),
                   known, checksum_bits, (len(words) * 11 - checksum_bits) // 8)

    @property
    def total(self) -> int:
        return 2048 ** len(self.positions)

    def candidate(self, index: int) -> str:
        if not 0 <= index < self.total:
            raise ValueError("Candidate index is outside the search range.")
        words = list(self.words)
        for position in reversed(self.positions):
            words[position] = WORDLIST[index & 2047]
            index >>= 11
        return " ".join(words)

    def scan(self, start: int = 0, stop: int | None = None):
        """Yield (combination index, phrase) in deterministic base-2048 order."""
        stop = self.total if stop is None else stop
        if not 0 <= start <= stop <= self.total:
            raise ValueError("Invalid search range.")
        mask = (1 << self.checksum_bits) - 1
        for index in range(start, stop):
            value, remaining = self.known_bits, index
            for shift in reversed(self.shifts):
                value |= (remaining & 2047) << shift
                remaining >>= 11
            entropy = (value >> self.checksum_bits).to_bytes(self.entropy_bytes, "big")
            expected = sha256(entropy).digest()[0] >> (8 - self.checksum_bits)
            if value & mask == expected:
                yield index, self.candidate(index)
