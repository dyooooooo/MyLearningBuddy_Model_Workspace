from app.util.page_sort import page_sort_key


def test_page_sort_key_from_chunk_id():
    assert page_sort_key(node_id="chunk:tb1:p0007") == (0, 7, "")
    assert page_sort_key(node_id="chunk:tb1:p0071") == (0, 71, "")


def test_page_sort_key_from_label():
    assert page_sort_key(label="第 10 页") == (0, 10, "")
    assert page_sort_key(label="第 7 页") == (0, 7, "")


def test_page_sort_key_numeric_order():
    pages = [
        page_sort_key(label="第 10 页"),
        page_sort_key(label="第 1 页"),
        page_sort_key(label="第 7 页"),
        page_sort_key(node_id="chunk:x:p0071"),
        page_sort_key(label="第 11 页"),
    ]
    assert sorted(pages) == [
        (0, 1, ""),
        (0, 7, ""),
        (0, 10, ""),
        (0, 11, ""),
        (0, 71, ""),
    ]


def test_page_sort_key_prefers_source_page():
    assert page_sort_key(label="第 99 页", source_page=3) == (0, 3, "")
