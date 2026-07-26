"""Import asset URL helpers."""
from __future__ import annotations

from app.services.import_assets import rewrite_local_image_refs


def test_rewrite_local_image_refs_skips_bare_filenames():
    url = "/api/v1/studio/proxy/import/asset?key=corpus%2Fimports%2Fx%2Fassets%2Fmineru%2Fabc.jpg"
    url_map = {
        "images/abc.jpg": url,
        "abc.jpg": "/api/v1/studio/proxy/import/asset?key=SHOULD_NOT_APPLY",
    }
    md = f"![t](images/abc.jpg) tail {url}"
    out = rewrite_local_image_refs(md, url_map)
    assert "SHOULD_NOT_APPLY" not in out
    assert out.count(url) == 2
    assert "images/abc.jpg" not in out
