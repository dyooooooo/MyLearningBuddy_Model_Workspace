from app.rag.rrf import rrf_fuse


def test_rrf_prefers_consensus():
    a = ["x", "y", "z"]
    b = ["y", "x", "w"]
    fused = rrf_fuse([a, b], key_fn=lambda s: s, k=60, limit=3)
    assert fused[0] in ("x", "y")
    assert "w" in fused or "z" in fused
