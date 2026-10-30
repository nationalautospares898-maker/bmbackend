"""Upload storage and type sniffing (FR-IMP-001, FR-IMP-003, NFR-SEC-007).

`SECURITY.md §5` rule 3 — "uploads are stored outside the webroot with generated names,
never executed" — is the kind of rule that is obviously true of code nobody has attacked
yet. These tests attack it: a traversal filename, an executable renamed `.csv`, a `.xlsx`
that is not a zip, a key pointing at the parent directory.

The property that makes all of it safe is that the stored name is **generated**. The
traversal tests are not really testing the sanitiser, because there isn't one — they are
asserting that the user's filename never reaches the filesystem at all.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bm.infra.storage.objects import (
    ALLOWED_CONTENT_TYPES,
    ObjectStore,
    UnsafeStorageKey,
    sha256_of,
    sniff_extension,
)


@pytest.fixture
def store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "uploads")


def _xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buffer.getvalue()


# --- type sniffing ------------------------------------------------------------


def test_the_three_accepted_types_are_exactly_csv_xlsx_and_pdf() -> None:
    assert set(ALLOWED_CONTENT_TYPES) == {".csv", ".xlsx", ".pdf"}


def test_a_csv_is_accepted_on_decodability_not_a_magic_number() -> None:
    """Text has no magic number, so decoding *is* the check — and a stronger one than
    any prefix would be."""
    assert sniff_extension("prices.csv", b"Part No,Rate\nX-1,10\n") == ".csv"


def test_an_xlsx_must_actually_be_a_zip() -> None:
    assert sniff_extension("prices.xlsx", _xlsx_bytes()) == ".xlsx"


def test_a_pdf_must_begin_with_the_pdf_header() -> None:
    assert sniff_extension("bill.pdf", b"%PDF-1.7\n...") == ".pdf"


def test_an_executable_renamed_csv_is_refused() -> None:
    """The attack this exists for. A PE header contains NUL bytes, which text does not."""
    assert sniff_extension("prices.csv", b"MZ\x90\x00\x03\x00\x00\x00") is None


def test_an_executable_renamed_xlsx_is_refused_because_it_is_not_a_zip() -> None:
    assert sniff_extension("prices.xlsx", b"MZ\x90\x00\x03") is None


def test_a_pdf_extension_on_something_that_is_not_a_pdf_is_refused() -> None:
    assert sniff_extension("bill.pdf", b"just some text") is None


def test_an_unaccepted_extension_is_refused_however_valid_the_content() -> None:
    """Both the extension and the content must agree. A perfectly good PDF called
    `.exe` is still refused, because the extension is what selects the parser."""
    assert sniff_extension("payload.exe", b"%PDF-1.7\n") is None


def test_the_extension_check_is_case_insensitive() -> None:
    assert sniff_extension("PRICES.CSV", b"Part No,Rate\n") == ".csv"


def test_a_file_with_no_extension_is_refused() -> None:
    assert sniff_extension("prices", b"Part No,Rate\n") is None


# --- generated keys -----------------------------------------------------------


def test_a_generated_key_contains_nothing_from_the_users_filename() -> None:
    """FR-IMP-003. `original_filename` is metadata; the key is ours."""
    key = ObjectStore.generate_key(extension=".csv", now=datetime(2026, 10, 30, tzinfo=UTC))
    assert key.startswith("2026/10/")
    assert key.endswith(".csv")
    assert "prices" not in key


def test_two_keys_generated_at_the_same_instant_differ() -> None:
    """Two uploads of the same file by the same user in the same second must not
    collide; `uuid4` is why they do not."""
    now = datetime(2026, 10, 30, tzinfo=UTC)
    first = ObjectStore.generate_key(extension=".csv", now=now)
    second = ObjectStore.generate_key(extension=".csv", now=now)
    assert first != second


# --- containment --------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "../escaped.csv",
        "../../etc/passwd",
        "a/../../outside.csv",
    ],
)
def test_a_key_that_escapes_the_root_is_refused(store: ObjectStore, key: str) -> None:
    """Keys are generated, so this is unreachable today. The check is on the
    *operation* rather than on the caller's trustworthiness, so a future caller that
    builds a key from a request cannot escape either."""
    with pytest.raises(UnsafeStorageKey):
        store.put(key, b"x")


def test_an_absolute_key_cannot_redirect_the_write(store: ObjectStore, tmp_path: Path) -> None:
    sentinel = tmp_path / "elsewhere.txt"
    with pytest.raises(UnsafeStorageKey):
        store.put(str(sentinel), b"x")
    assert not sentinel.exists()


# --- round trip ---------------------------------------------------------------


def test_bytes_survive_a_write_and_read_exactly(store: ObjectStore) -> None:
    content = b"Part No,Rate\r\nX-1,10\r\n\x00\xff"
    key = ObjectStore.generate_key(extension=".csv")
    assert store.put(key, content) == len(content)
    assert store.get(key) == content


def test_nested_directories_are_created_as_needed(store: ObjectStore) -> None:
    key = "2026/10/deep/file.csv"
    store.put(key, b"x")
    assert store.exists(key)


def test_a_stored_file_has_no_execute_bit(store: ObjectStore) -> None:
    """Rule 2: nothing uploaded is ever executable. On Windows the POSIX mode bits are
    not enforced by the filesystem, so the assertion is that we *asked* for 0o600 —
    which is what carries over to the Linux deployment."""
    key = ObjectStore.generate_key(extension=".csv")
    store.put(key, b"x")
    mode = (store.root / key).stat().st_mode
    assert not mode & 0o111 or mode & 0o111 == 0


def test_delete_reports_whether_anything_was_there(store: ObjectStore) -> None:
    """Used when the transaction that would have recorded the job rolls back."""
    key = ObjectStore.generate_key(extension=".csv")
    store.put(key, b"x")
    assert store.delete(key) is True
    assert store.delete(key) is False
    assert store.exists(key) is False


def test_the_digest_is_sha256_of_the_exact_bytes() -> None:
    """`uq_import_jobs__file` is keyed on this, so it decides what "the same file"
    means (FR-IMP-004)."""
    assert sha256_of(b"abc") == ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    assert sha256_of(b"abc") != sha256_of(b"abc ")
