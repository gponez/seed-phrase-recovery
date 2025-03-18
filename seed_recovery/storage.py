"""Durable ordered output with a checkpoint bound to its input and output."""

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile

from .core import SearchPlan, WORDLIST_HASH


def _open_private(path: Path, flags: int):
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Recovery files must be regular files.")
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        return os.fdopen(fd, "r+b")
    except BaseException:
        os.close(fd)
        raise


def _acquire_lock(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ValueError("Another process is using these recovery files.") from exc
    else:
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another process is using these recovery files.") from exc


@contextmanager
def _lock(path: Path):
    with _open_private(path, os.O_CREAT | os.O_RDWR) as handle:
        _acquire_lock(handle)
        yield


def _save(path: Path, state: dict):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_recovery(plan: SearchPlan, output: Path, checkpoint: Path, *,
                 resume: bool = False, limit: int = 1_000_000,
                 batch_size: int = 4096, on_checkpoint=None) -> dict:
    """Commit output before progress; discard only uncommitted tail on resume."""
    if limit <= 0 or batch_size <= 0:
        raise ValueError("Limit and batch size must be positive.")
    # Absolute paths without resolving symlinks: O_NOFOLLOW rejects leaf links.
    output = Path(os.path.abspath(output))
    checkpoint = Path(os.path.abspath(checkpoint))
    lock_path = checkpoint.with_name(checkpoint.name + ".lock")
    paths = (output, checkpoint, lock_path)
    for i, first in enumerate(paths):
        for second in paths[i + 1:]:
            if first.resolve() == second.resolve() or (first.exists() and second.exists()
                                                       and os.path.samefile(first, second)):
                raise ValueError("Output, checkpoint, and checkpoint lock must be different files.")
    config = sha256(json.dumps({"words": plan.words, "wordlist": WORDLIST_HASH,
                               "output": str(output)}, sort_keys=True).encode()).hexdigest()
    with _lock(lock_path):
        if resume:
            with _open_private(checkpoint, os.O_RDWR) as handle:
                state = json.load(handle)
            if not isinstance(state, dict) or state.get("version") != 1 or state.get("config") != config:
                raise ValueError("Checkpoint does not match this phrase, word list, or output path.")
            for name in ("next_index", "valid", "output_bytes"):
                if type(state.get(name)) is not int or state[name] < 0:
                    raise ValueError("Invalid checkpoint counters.")
            if state["next_index"] > plan.total or state["valid"] > state["next_index"]:
                raise ValueError("Invalid checkpoint search range.")
            handle = _open_private(output, os.O_RDWR)
        else:
            if checkpoint.exists() or checkpoint.is_symlink():
                raise ValueError("Checkpoint already exists. Use --resume or choose new paths.")
            handle = _open_private(output, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            state = {"version": 1, "config": config, "next_index": 0, "valid": 0,
                     "output_bytes": 0, "output_sha256": sha256(b"").hexdigest()}
        with handle:
            # The output inode is shared even when a checkpoint was copied or aliased.
            _acquire_lock(handle)
            if not resume:
                handle.flush()
                os.fsync(handle.fileno())
                _save(checkpoint, state)
            digest = sha256()
            remaining = state["output_bytes"]
            while remaining:
                data = handle.read(min(1024 * 1024, remaining))
                if not data:
                    raise ValueError("Output is shorter than its checkpoint; refusing to skip work.")
                digest.update(data)
                remaining -= len(data)
            if digest.hexdigest() != state.get("output_sha256"):
                raise ValueError("Output changed since the checkpoint; refusing to resume.")
            handle.truncate(state["output_bytes"])
            handle.seek(state["output_bytes"])
            stop = min(plan.total, state["next_index"] + limit)
            while state["next_index"] < stop:
                batch_end = min(stop, state["next_index"] + batch_size)
                matches = list(plan.scan(state["next_index"], batch_end))
                data = "".join(phrase + "\n" for _, phrase in matches).encode()
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                digest.update(data)
                state = {**state, "next_index": batch_end, "valid": state["valid"] + len(matches),
                         "output_bytes": handle.tell(), "output_sha256": digest.hexdigest()}
                _save(checkpoint, state)
                if on_checkpoint:
                    on_checkpoint(dict(state))
            return {**state, "total": plan.total, "complete": state["next_index"] == plan.total}
