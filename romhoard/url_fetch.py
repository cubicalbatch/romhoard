"""URL fetching utility for importing JSON from remote URLs."""

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx


class URLFetchError(Exception):
    """Raised when URL fetching fails."""

    pass


# Configuration
MAX_JSON_SIZE_BYTES = 1 * 1024 * 1024  # 1MB for JSON
MAX_ZIP_SIZE_BYTES = 200 * 1024 * 1024  # 200MB for ZIP
CONNECT_TIMEOUT = 10  # seconds
READ_TIMEOUT = 30  # seconds
ZIP_READ_TIMEOUT = 300  # 5 minutes for large ZIP files
MAX_REDIRECTS = 3


def validate_url(url: str) -> str:
    """Validate URL scheme and format.

    Args:
        url: URL to validate

    Returns:
        Validated URL string

    Raises:
        URLFetchError: If URL is invalid
    """
    if not url or not url.strip():
        raise URLFetchError("URL is required")

    url = url.strip()

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        # Accessing port also validates its syntax and range.
        parsed.port
    except ValueError as e:
        raise URLFetchError(f"Invalid URL: {e}") from e

    if parsed.scheme not in ("http", "https"):
        raise URLFetchError("URL must use http or https")

    if not hostname:
        raise URLFetchError("URL must have a hostname")

    return url


def fetch_json_from_url(url: str) -> dict:
    """Fetch and parse JSON from a URL.

    Security measures:
    - Only http/https schemes allowed
    - 1MB size limit (enforced during streaming)
    - 10s connect timeout, 30s read timeout
    - Max 3 redirects
    - Rejects HTML responses

    Args:
        url: URL to fetch JSON from

    Returns:
        Parsed JSON as dictionary

    Raises:
        URLFetchError: If fetching or parsing fails
    """
    url = validate_url(url)

    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                READ_TIMEOUT,  # default timeout
                connect=CONNECT_TIMEOUT,
            ),
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type:
                    raise URLFetchError(
                        "URL returned HTML instead of JSON. "
                        "Make sure the URL points directly to a JSON file."
                    )

                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > MAX_JSON_SIZE_BYTES:
                            raise URLFetchError(
                                f"File too large (max {MAX_JSON_SIZE_BYTES // 1024 // 1024}MB)"
                            )
                    except ValueError:
                        pass

                # Limit decoded bytes while receiving the body, including
                # responses without Content-Length or with HTTP compression.
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    if len(content) + len(chunk) > MAX_JSON_SIZE_BYTES:
                        raise URLFetchError(
                            f"File too large (max {MAX_JSON_SIZE_BYTES // 1024 // 1024}MB)"
                        )
                    content.extend(chunk)

            # Parse JSON
            try:
                return json.loads(content.decode("utf-8"))
            except UnicodeDecodeError as e:
                raise URLFetchError(f"Invalid encoding: {e}") from e
            except json.JSONDecodeError as e:
                raise URLFetchError(f"Invalid JSON: {e}") from e

    except httpx.TimeoutException:
        raise URLFetchError("Request timed out")
    except httpx.TooManyRedirects:
        raise URLFetchError(f"Too many redirects (max {MAX_REDIRECTS})")
    except httpx.HTTPStatusError as e:
        raise URLFetchError(f"HTTP error {e.response.status_code}") from e
    except (httpx.RequestError, httpx.InvalidURL) as e:
        raise URLFetchError(f"Request failed: {e}") from e


def fetch_zip_from_url(url: str, dest_path: str) -> None:
    """Fetch a ZIP file from a URL and save to a local path.

    Security measures:
    - Only http/https schemes allowed
    - 200MB size limit
    - 10s connect timeout, 5 minute read timeout
    - Max 3 redirects

    Args:
        url: URL to fetch ZIP from
        dest_path: Local file path to save the ZIP to

    Raises:
        URLFetchError: If fetching fails
    """
    url = validate_url(url)

    temp_path = None
    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                ZIP_READ_TIMEOUT,
                connect=CONNECT_TIMEOUT,
            ),
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
        ) as client:
            # Use streaming to handle large files
            with client.stream("GET", url) as response:
                response.raise_for_status()

                # Check content length if provided
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > MAX_ZIP_SIZE_BYTES:
                            raise URLFetchError(
                                f"File too large (max {MAX_ZIP_SIZE_BYTES // 1024 // 1024}MB)"
                            )
                    except ValueError:
                        pass

                # Stream to file, checking size as we go
                total_size = 0
                # Replace only a complete download; retain an existing file on
                # timeout, oversized response, or a local write failure.
                destination = Path(dest_path)
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent, prefix=f".{destination.name}.", delete=False
                ) as f:
                    temp_path = f.name
                    for chunk in response.iter_bytes(chunk_size=8192):
                        total_size += len(chunk)
                        if total_size > MAX_ZIP_SIZE_BYTES:
                            raise URLFetchError(
                                f"File too large (max {MAX_ZIP_SIZE_BYTES // 1024 // 1024}MB)"
                            )
                        f.write(chunk)
                os.replace(temp_path, dest_path)

    except httpx.TimeoutException:
        raise URLFetchError("Request timed out")
    except httpx.TooManyRedirects:
        raise URLFetchError(f"Too many redirects (max {MAX_REDIRECTS})")
    except httpx.HTTPStatusError as e:
        raise URLFetchError(f"HTTP error {e.response.status_code}") from e
    except (httpx.RequestError, httpx.InvalidURL) as e:
        raise URLFetchError(f"Request failed: {e}") from e
    finally:
        if temp_path is not None:
            Path(temp_path).unlink(missing_ok=True)
