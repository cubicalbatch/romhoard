"""Common utilities for views."""

from pathlib import Path

from django.http import FileResponse


class TempFileResponse(FileResponse):
    """FileResponse that deletes temp file after streaming."""

    def __init__(self, *args, temp_path: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.temp_path = temp_path

    def close(self):
        super().close()
        if self.temp_path:
            Path(self.temp_path).unlink(missing_ok=True)
