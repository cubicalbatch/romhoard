"""Large extracted downloads stay bounded and clean up failed copies."""

import io
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from library.views.download import _serve_rom_file


@pytest.mark.django_db
@pytest.mark.parametrize("fail_read", [False, True])
def test_extracted_download_reads_in_chunks_and_cleans_temp_files(tmp_path, fail_read):
    from library.views import download

    source = tmp_path / "extracted.rom"
    data = b"ROM data" * 200_000
    rom = SimpleNamespace(
        file_path="member.rom", archive_path="archive.zip", is_archived=True
    )
    real_open = open
    real_temporary_file = download.tempfile.NamedTemporaryFile

    class BoundedReader(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            if fail_read:
                raise OSError("Read failed")
            return super().read(size)

    def open_file(path, *args, **kwargs):
        if Path(path) == source:
            return BoundedReader(data)
        return real_open(path, *args, **kwargs)

    def temporary_file(**kwargs):
        return real_temporary_file(dir=tmp_path, **kwargs)

    with (
        patch.object(download, "open", side_effect=open_file, create=True),
        patch.object(
            download.tempfile, "NamedTemporaryFile", side_effect=temporary_file
        ),
    ):
        context = nullcontext((str(source), source.name))
        if fail_read:
            with pytest.raises(OSError, match="Read failed"):
                _serve_rom_file(rom, context)
        else:
            response = _serve_rom_file(rom, context)
            try:
                assert b"".join(response.streaming_content) == data
            finally:
                response.close()

    assert list(tmp_path.iterdir()) == []
