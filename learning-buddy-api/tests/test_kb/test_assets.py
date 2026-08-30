from __future__ import annotations

from app.kb.assets import assert_kb_key, asset_url, rewrite_local_image_paths
from app.kb.mineru_client import _rewrite_block


def test_assert_kb_key():
    assert assert_kb_key("kb/pdfs/a/x.jpg") == "kb/pdfs/a/x.jpg"
    try:
        assert_kb_key("corpus/imports/a/x.jpg")
        raise AssertionError("expected reject")
    except ValueError as exc:
        assert "kb/pdfs/" in str(exc)


def test_asset_url_and_rewrite():
    url = asset_url("kb/pdfs/b1/assets/mineru/fig.png")
    assert url.startswith("/api/v1/kb/proxy/asset?key=")
    assert "kb%2Fpdfs%2Fb1" in url
    md = rewrite_local_image_paths("![x](images/fig.png)", {"images/fig.png": url})
    assert url in md


def test_delete_book_assets_uses_kb_prefix():
    from unittest.mock import patch

    from app.kb.assets import delete_book_assets

    with patch("app.kb.assets.delete_objects_by_prefix", return_value=4) as purge:
        assert delete_book_assets("b1") == 4
    purge.assert_called_once_with("kb/pdfs/b1")


def test_rewrite_block_rewrites_table_html():
    url = asset_url("kb/pdfs/b1/assets/mineru/fig.png")
    out = _rewrite_block(
        {
            "type": "table",
            "content": {
                "table_body": '<table><tr><td><img src="images/fig.png"/></td></tr></table>',
            },
        },
        {"images/fig.png": url},
    )
    assert url in out["content"]["table_body"]
    assert "images/fig.png" not in out["content"]["table_body"]
