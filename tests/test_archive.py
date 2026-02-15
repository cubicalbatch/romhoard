"""Tests for archive handling utilities."""

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from library.archive import (
    ArchiveInfo,
    ZipSlipError,
    _validate_archive_path,
    compute_archived_file_crc32,
    compute_file_crc32,
    extract_file_from_archive,
    file_exists_in_archive,
    is_archive_file,
    is_nested_archive,
    list_archive_contents,
)


# -----------------------------------------------------------------------------
# Tests for is_archive_file
# -----------------------------------------------------------------------------


class TestIsArchiveFile:
    """Tests for is_archive_file function."""

    @pytest.mark.parametrize(
        "filename,expected",
        [
            # Supported formats
            ("game.zip", True),
            ("game.ZIP", True),
            ("game.7z", True),
            ("game.7Z", True),
            # Unsupported format (RAR)
            ("game.rar", False),
            ("game.RAR", False),
            # Non-archive files
            ("game.gba", False),
            ("game.nes", False),
            ("game.txt", False),
        ],
    )
    def test_archive_detection(self, filename, expected):
        """Test archive file detection."""
        assert is_archive_file(filename) is expected


class TestIsNestedArchive:
    """Tests for is_nested_archive function."""

    @pytest.mark.parametrize(
        "path,expected",
        [
            # Nested archives (archive inside archive)
            ("inner.zip", True),
            ("inner.7z", True),
            ("folder/inner.zip", True),
            # Non-nested paths
            ("game.gba", False),
            ("game.iso", False),
        ],
    )
    def test_nested_archive_detection(self, path, expected):
        """Test nested archive detection."""
        assert is_nested_archive(path) is expected


# -----------------------------------------------------------------------------
# Tests for list_archive_contents
# -----------------------------------------------------------------------------


class TestListArchiveContents:
    """Tests for list_archive_contents function."""

    def test_list_zip_contents(self, tmp_path):
        """Test listing contents of a ZIP file."""
        # Create test ZIP
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("game1.gba", b"a" * 1000)
            zf.writestr("game2.gba", b"b" * 2000)
            zf.writestr("subfolder/game3.gba", b"c" * 3000)

        result = list_archive_contents(str(zip_path))

        assert len(result) == 3
        names = {f.name for f in result}
        assert names == {"game1.gba", "game2.gba", "subfolder/game3.gba"}

    def test_list_zip_skips_directories(self, tmp_path):
        """Test that directories are skipped when listing ZIP contents."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("folder/", "")  # Directory entry
            zf.writestr("folder/game.gba", b"ROM")

        result = list_archive_contents(str(zip_path))

        # Only the file, not the directory
        assert len(result) == 1
        assert result[0].name == "folder/game.gba"

    def test_list_zip_includes_crc32(self, tmp_path):
        """Test that CRC32 values are extracted from ZIP."""
        zip_path = tmp_path / "test.zip"
        content = b"hello world"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", content)

        result = list_archive_contents(str(zip_path))

        assert len(result) == 1
        # CRC32 should be a hex string
        assert result[0].crc32 is not None
        assert len(result[0].crc32) == 8

    @pytest.mark.parametrize(
        "name,size,crc32_int,crc32_hex",
        [
            ("game1.gba", 1000, 0x12345678, "12345678"),
            ("game2.gba", 2000, 0xABCDEF01, "abcdef01"),
        ],
    )
    @patch("library.archive.HAS_7Z_SUPPORT", True)
    @patch("library.archive.py7zr")
    def test_list_7z_contents(self, mock_py7zr, name, size, crc32_int, crc32_hex):
        """Test listing contents of a 7z file (mocked)."""
        mock_file = MagicMock()
        mock_file.filename = name
        mock_file.uncompressed = size
        mock_file.is_directory = False
        mock_file.crc32 = crc32_int

        mock_szf = MagicMock()
        mock_szf.__enter__ = MagicMock(return_value=mock_szf)
        mock_szf.__exit__ = MagicMock(return_value=False)
        mock_szf.list.return_value = [mock_file]

        mock_py7zr.SevenZipFile.return_value = mock_szf

        result = list_archive_contents("/path/to/test.7z")

        assert len(result) == 1
        assert result[0].name == name
        assert result[0].size == size
        assert result[0].crc32 == crc32_hex

    @patch("library.archive.HAS_7Z_SUPPORT", False)
    def test_list_7z_without_support(self):
        """Test 7z listing fails gracefully without py7zr."""
        with pytest.raises(ValueError, match="7z support requires py7zr"):
            list_archive_contents("/path/to/test.7z")

    def test_list_unsupported_format(self):
        """Test that unsupported formats raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported archive format"):
            list_archive_contents("/path/to/test.rar")

    def test_list_corrupted_zip(self, tmp_path):
        """Test that corrupted ZIP files raise error."""
        bad_zip = tmp_path / "corrupt.zip"
        bad_zip.write_bytes(b"not a zip file")

        with pytest.raises(Exception):  # Could be BadZipFile or other
            list_archive_contents(str(bad_zip))


# -----------------------------------------------------------------------------
# Tests for file_exists_in_archive
# -----------------------------------------------------------------------------


class TestFileExistsInArchive:
    """Tests for file_exists_in_archive function."""

    def test_file_exists(self, tmp_path):
        """Test finding a file that exists in archive."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("game.gba", b"ROM")

        assert file_exists_in_archive(str(zip_path), "game.gba") is True

    def test_file_not_exists(self, tmp_path):
        """Test file that doesn't exist in archive."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("game.gba", b"ROM")

        assert file_exists_in_archive(str(zip_path), "other.gba") is False

    def test_file_exists_nested_path(self, tmp_path):
        """Test finding nested files in archive."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("USA/game.gba", b"ROM")

        assert file_exists_in_archive(str(zip_path), "USA/game.gba") is True
        assert file_exists_in_archive(str(zip_path), "game.gba") is False

    def test_invalid_archive_returns_false(self):
        """Test that invalid archive paths return False."""
        assert file_exists_in_archive("/nonexistent/path.zip", "game.gba") is False


# -----------------------------------------------------------------------------
# Tests for extract_file_from_archive
# -----------------------------------------------------------------------------


class TestExtractFileFromArchive:
    """Tests for extract_file_from_archive function."""

    def test_extract_from_zip(self, tmp_path):
        """Test extracting a file from ZIP."""
        zip_path = tmp_path / "test.zip"
        content = b"ROM content here"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("game.gba", content)

        extract_file_from_archive(str(zip_path), "game.gba", str(tmp_path))

        extracted = tmp_path / "game.gba"
        assert extracted.exists()
        assert extracted.read_bytes() == content

    def test_extract_nested_file(self, tmp_path):
        """Test extracting nested file."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("folder/game.gba", b"ROM")

        output_file = tmp_path / "extracted.gba"

        extract_file_from_archive(str(zip_path), "folder/game.gba", str(output_file))

        assert output_file.exists()
        assert output_file.read_bytes() == b"ROM"

    def test_extract_file_not_found(self, tmp_path):
        """Test extracting non-existent file raises error."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("game.gba", b"ROM")

        with pytest.raises(FileNotFoundError, match="not found in archive"):
            extract_file_from_archive(str(zip_path), "nonexistent.gba", str(tmp_path))

    @patch("library.archive.HAS_7Z_SUPPORT", True)
    @patch("library.archive.py7zr")
    @patch("library.archive.file_exists_in_archive", return_value=True)
    def test_extract_from_7z(self, mock_exists, mock_py7zr, tmp_path):
        """Test extracting from 7z (mocked)."""
        mock_szf = MagicMock()
        mock_szf.__enter__ = MagicMock(return_value=mock_szf)
        mock_szf.__exit__ = MagicMock(return_value=None)

        # Simulate extraction by creating the file
        def mock_extract(targets, path):
            (Path(path) / targets[0]).write_bytes(b"ROM")

        mock_szf.extract.side_effect = mock_extract
        mock_py7zr.SevenZipFile.return_value = mock_szf

        dest = tmp_path / "extracted.gba"
        extract_file_from_archive("/path/to/test.7z", "game.gba", str(dest))

        # The file should exist (copied from temp extraction)
        mock_py7zr.SevenZipFile.assert_called_once_with("/path/to/test.7z", "r")

    @patch("library.archive.HAS_7Z_SUPPORT", False)
    @patch("library.archive.file_exists_in_archive", return_value=True)
    def test_extract_7z_without_support(self, mock_exists, tmp_path):
        """Test 7z extraction fails gracefully without py7zr."""
        with pytest.raises(ValueError, match="7z support requires py7zr"):
            extract_file_from_archive("/path/to/test.7z", "game.gba", str(tmp_path))

    @patch("library.archive.file_exists_in_archive", return_value=True)
    def test_extract_unsupported_format(self, mock_exists, tmp_path):
        """Test extracting from unsupported format raises error."""
        with pytest.raises(ValueError, match="Unsupported archive format"):
            extract_file_from_archive("/path/to/test.rar", "game.rom", str(tmp_path))


# -----------------------------------------------------------------------------
# Tests for compute_file_crc32
# -----------------------------------------------------------------------------


class TestComputeFileCrc32:
    """Tests for compute_file_crc32 function."""

    def test_compute_crc32_known_value(self, tmp_path):
        """Test CRC32 computation with known value."""
        test_file = tmp_path / "test.bin"
        # "hello world" has known CRC32: 0d4a1185
        test_file.write_bytes(b"hello world")

        crc = compute_file_crc32(str(test_file))

        assert crc == "0d4a1185"

    def test_compute_crc32_empty_file(self, tmp_path):
        """Test CRC32 of empty file."""
        test_file = tmp_path / "empty.bin"
        test_file.write_bytes(b"")

        crc = compute_file_crc32(str(test_file))

        # Empty file has CRC32 of 00000000
        assert crc == "00000000"

    def test_compute_crc32_file_not_found(self):
        """Test CRC32 computation raises for missing file."""
        with pytest.raises(IOError, match="Failed to compute CRC32"):
            compute_file_crc32("/nonexistent/path/file.bin")

    def test_compute_crc32_returns_lowercase_hex(self, tmp_path):
        """Test that CRC32 is returned as lowercase hex."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"TEST")

        crc = compute_file_crc32(str(test_file))

        assert crc == crc.lower()
        assert len(crc) == 8

    def test_compute_crc32_large_file(self, tmp_path):
        """Test CRC32 computation with larger file."""
        test_file = tmp_path / "large.bin"
        # 1MB of data
        test_file.write_bytes(b"x" * (1024 * 1024))

        crc = compute_file_crc32(str(test_file))

        assert len(crc) == 8
        assert all(c in "0123456789abcdef" for c in crc)


# -----------------------------------------------------------------------------
# Tests for compute_archived_file_crc32
# -----------------------------------------------------------------------------


class TestComputeArchivedFileCrc32:
    """Tests for compute_archived_file_crc32 function."""

    def test_compute_crc32_from_zip(self, tmp_path):
        """Test CRC32 extraction from ZIP file headers."""
        zip_path = tmp_path / "test.zip"
        content = b"hello world"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", content)

        crc = compute_archived_file_crc32(str(zip_path), "test.txt")

        # Should match CRC32 of "hello world"
        assert crc == "0d4a1185"

    def test_compute_crc32_file_not_in_archive(self, tmp_path):
        """Test CRC32 for non-existent file in archive."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("exists.txt", b"content")

        crc = compute_archived_file_crc32(str(zip_path), "notexists.txt")

        assert crc is None

    def test_compute_crc32_nested_path(self, tmp_path):
        """Test CRC32 extraction for nested files."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("folder/game.gba", b"ROM DATA")

        crc = compute_archived_file_crc32(str(zip_path), "folder/game.gba")

        assert crc is not None
        assert len(crc) == 8


# -----------------------------------------------------------------------------
# Tests for _validate_archive_path (security-critical)
# -----------------------------------------------------------------------------


class TestValidateArchivePath:
    """Tests for _validate_archive_path - security-critical path validation."""

    def test_allows_normal_paths(self, tmp_path):
        """Test that normal paths are allowed."""
        result = _validate_archive_path("game.gba", str(tmp_path))
        assert result == tmp_path / "game.gba"

    def test_allows_nested_paths(self, tmp_path):
        """Test that nested paths within dest are allowed."""
        result = _validate_archive_path("folder/game.gba", str(tmp_path))
        assert result == tmp_path / "folder" / "game.gba"

    def test_blocks_path_traversal_dotdot(self, tmp_path):
        """Test that '../' path traversal is blocked (ZipSlip attack)."""
        with pytest.raises(ZipSlipError, match="escape destination"):
            _validate_archive_path("../../../etc/passwd", str(tmp_path))

    def test_blocks_absolute_paths(self, tmp_path):
        """Test that absolute paths in archive are blocked."""
        with pytest.raises(ZipSlipError, match="escape destination"):
            _validate_archive_path("/etc/passwd", str(tmp_path))

    def test_blocks_traversal_with_nested_start(self, tmp_path):
        """Test traversal even when starting with legitimate folder."""
        with pytest.raises(ZipSlipError, match="escape destination"):
            _validate_archive_path("folder/../../etc/passwd", str(tmp_path))

    def test_allows_dotdot_in_filename(self, tmp_path):
        """Test that '..' in filename (not path) is allowed."""
        # "game..gba" is a valid filename
        result = _validate_archive_path("game..gba", str(tmp_path))
        assert result == tmp_path / "game..gba"


# -----------------------------------------------------------------------------
# Tests for ZipSlipError
# -----------------------------------------------------------------------------


class TestZipSlipError:
    """Tests for ZipSlipError exception."""

    def test_exception_message(self):
        """Test that ZipSlipError includes the offending path."""
        with pytest.raises(ZipSlipError) as exc_info:
            raise ZipSlipError("../evil.txt")

        assert "../evil.txt" in str(exc_info.value)

    def test_exception_inherits_from_exception(self):
        """Test that ZipSlipError can be caught as Exception."""
        try:
            raise ZipSlipError("test")
        except Exception as e:
            assert isinstance(e, ZipSlipError)
