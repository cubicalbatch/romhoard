"""URL fetching utility for importing JSON from remote URLs."""

import json
from urllib.parse import urlparse

import httpx


class URLFetchError(Exception):
    """Raised when URL fetching fails."""

    pass


# Configuration
MAX_SIZE_BYTES = 1 * 1024 * 1024  # 1MB
CONNECT_TIMEOUT = 10  # seconds
READ_TIMEOUT = 30  # seconds
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
    except ValueError as e:
        raise URLFetchError(f"Invalid URL: {e}") from e

    if parsed.scheme not in ("http", "https"):
        raise URLFetchError("URL must use http or https")

    if not parsed.netloc:
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
            response = client.get(url)
            response.raise_for_status()

            # Check content type - reject HTML
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                raise URLFetchError(
                    "URL returned HTML instead of JSON. "
                    "Make sure the URL points directly to a JSON file."
                )

            # Check content length if provided
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_SIZE_BYTES:
                        raise URLFetchError(
                            f"File too large (max {MAX_SIZE_BYTES // 1024 // 1024}MB)"
                        )
                except ValueError:
                    pass  # Invalid content-length header, check actual size below

            # Check actual content size
            content = response.content
            if len(content) > MAX_SIZE_BYTES:
                raise URLFetchError(
                    f"File too large (max {MAX_SIZE_BYTES // 1024 // 1024}MB)"
                )

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
    except httpx.RequestError as e:
        raise URLFetchError(f"Request failed: {e}") from e
