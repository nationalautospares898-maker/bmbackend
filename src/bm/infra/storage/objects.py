"""Uploaded-file storage (FR-IMP-003).

Three rules from `SECURITY.md §5` are enforced *here* rather than trusted to callers,
because every one of them has been a real-world remote-code-execution:

1. **The stored name is generated, never the user's.** `original_filename` is kept as a
   display string in the database and never touches the filesystem. A user who uploads
   `../../../etc/passwd` or `shell.php` gets a file called
   `2026/10/<uuid4>.bin` and no say in where it lands.
2. **Nothing is ever executed.** Files are written with no execute bit and read back as
   bytes. There is no code path that passes a stored key to a shell, an interpreter or
   an archive extractor.
3. **The root is resolved once and every key is checked against it.** Even though keys
   are generated, `_absolute()` re-verifies containment, so a future caller that builds
   a key from a request cannot escape the directory — the check is on the operation,
   not on the trust level of the caller.

A local filesystem backend is the v1 implementation. `storage_bucket` in the settings is
the seam for an object store later; the interface is deliberately the narrow set an
object store also offers (`put`, `get`, `delete`), so swapping it does not change any
caller.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

#: Accepted upload types, mapping sniffed-content family → canonical content type.
#:
#: Both the extension *and* the content are checked (FR-IMP-001). An extension alone is
#: a user-supplied string; content alone would reject a perfectly good `.csv` whose
#: first bytes happen to look like something else. Requiring agreement is what makes a
#: renamed executable fail.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}

#: Leading bytes that identify each family, for the sniff half of the check.
#:
#: `.xlsx` is a zip container, so it starts `PK`. `.pdf` starts `%PDF-`. A `.csv` has no
#: magic number at all — text is text — so it is validated by *decoding* instead, which
#: is a stronger check than any prefix would be.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".xlsx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".pdf": (b"%PDF-",),
}


class UnsafeStorageKey(ValueError):
    """A key that would resolve outside the storage root.

    Its own exception type rather than a bare `ValueError` so the API layer can never
    mistake it for a validation message that is safe to echo back to a caller.
    """


def sniff_extension(filename: str, content: bytes) -> str | None:
    """The accepted extension this upload really is, or `None` if it is not accepted.

    Returns the *extension*, not a content type, because the extension is what selects
    the parser. Agreement between the claimed extension and the content is required:

    * `.xlsx` and `.pdf` must begin with their magic bytes;
    * `.csv` must decode as UTF-8 or Latin-1 **and** contain no NUL byte, which is what
      distinguishes a spreadsheet exported as text from a binary file renamed `.csv`.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_CONTENT_TYPES:
        return None

    if suffix in _MAGIC:
        return suffix if content.startswith(_MAGIC[suffix]) else None

    # .csv — decodability is the test.
    if b"\x00" in content[:8192]:
        return None
    for encoding in ("utf-8", "latin-1"):
        try:
            content[:8192].decode(encoding)
        except UnicodeDecodeError:
            continue
        return suffix
    return None


def sha256_of(content: bytes) -> str:
    """The digest `uq_import_jobs__file` is keyed on (FR-IMP-004)."""
    return hashlib.sha256(content).hexdigest()


class ObjectStore:
    """Content-addressed-ish local storage with generated keys."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def _absolute(self, key: str) -> Path:
        """Resolve a key inside the root, refusing anything that escapes it.

        `strict=False` so this works for a key being *written*, where the file does not
        exist yet. Containment is checked on the resolved path either way.
        """
        candidate = (self._root / key).resolve(strict=False)
        if candidate != self._root and self._root not in candidate.parents:
            raise UnsafeStorageKey(f"{key!r} resolves outside the storage root")
        return candidate

    @staticmethod
    def generate_key(*, extension: str, now: datetime | None = None) -> str:
        """A key the uploader has no influence over.

        Date-prefixed so the directory stays navigable by an operator and a retention
        sweep can delete a whole month, and `uuid4` so two uploads of the same file by
        the same user at the same moment cannot collide. The extension is taken from the
        **sniffed** family, never from the original filename.
        """
        stamp = now or datetime.now(tz=UTC)
        return f"{stamp:%Y/%m}/{uuid.uuid4()}{extension}"

    def put(self, key: str, content: bytes) -> int:
        """Write bytes, returning the size. Parent directories are created as needed."""
        path = self._absolute(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        # No execute bit, ever (rule 2). Owner read/write only.
        path.chmod(0o600)
        return len(content)

    def get(self, key: str) -> bytes:
        return self._absolute(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._absolute(key).is_file()

    def delete(self, key: str) -> bool:
        """Remove an object. Returns whether anything was there.

        Used by the upload path when the database transaction that would have recorded
        the job rolls back — an orphaned blob is harmless but an unbounded pile of them
        is not.
        """
        path = self._absolute(key)
        if not path.is_file():
            return False
        path.unlink()
        return True
