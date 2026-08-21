import os
import time
from unittest.mock import MagicMock, patch

import requests

from src.data.base import download_cached


def test_download_cached_retries_after_a_broken_connection(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data.base.CACHE_DIR", tmp_path)
    target = tmp_path / "angelone_scrip_master.json"
    payload = b'[{"symbol":"RELIANCE-EQ"}]'

    broken = requests.exceptions.ChunkedEncodingError("IncompleteRead")
    ok = MagicMock()
    ok.raise_for_status.return_value = None
    ok.iter_content.return_value = [payload]
    ok.__enter__.return_value = ok
    ok.__exit__.return_value = False

    with patch("src.data.base.requests.get", side_effect=[broken, ok]) as mocked:
        with patch("src.data.base.time.sleep"):
            path = download_cached("https://example.test/master", target.name)

    assert mocked.call_count == 2
    assert path.read_bytes() == payload


def test_download_cached_reuses_stale_file_when_every_attempt_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data.base.CACHE_DIR", tmp_path)
    target = tmp_path / "angelone_scrip_master.json"
    target.write_bytes(b'[{"symbol":"STALE-EQ"}]')
    stale = time.time() - 200_000
    os.utime(target, (stale, stale))

    broken = requests.exceptions.ChunkedEncodingError("IncompleteRead")
    with patch("src.data.base.requests.get", side_effect=broken):
        with patch("src.data.base.time.sleep"):
            path = download_cached("https://example.test/master", target.name)

    assert path.read_bytes() == b'[{"symbol":"STALE-EQ"}]'
