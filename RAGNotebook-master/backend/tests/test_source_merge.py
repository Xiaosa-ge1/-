from app.rag.source_formatter import merge_sources, normalize_distances


def test_normalize_smaller_distance_gets_higher_score():
    """距离越小，归一化后的相似度必须越高"""
    result = normalize_distances([0.2, 0.8])
    assert result[0] > result[1]


def test_normalize_result_in_unit_range():
    """归一化结果必须落在 0-1，端点分别为 1 和 0"""
    result = normalize_distances([0.1, 0.5, 0.9])
    assert max(result) == 1.0
    assert min(result) == 0.0
    assert all(0.0 <= r <= 1.0 for r in result)


def test_normalize_all_equal_avoids_division_by_zero():
    """所有距离相同时不应除零，统一给满分"""
    assert normalize_distances([0.5, 0.5, 0.5]) == [1.0, 1.0, 1.0]


def test_normalize_empty_input():
    assert normalize_distances([]) == []


def test_merge_sources_sorted_descending():
    """合并后必须按分数降序，否则 top-k 取不到真正相关的"""
    notes = [{"source_id": "note:a", "score": 0.9}, {"source_id": "note:b", "score": 0.1}]
    kb = [{"source_id": "kb:c", "score": 0.5}]
    merged = merge_sources(notes, kb)
    scores = [m["score"] for m in merged]
    assert scores == sorted(scores, reverse=True)


def test_merge_sources_normalizes_each_source_separately():
    """两源分量纲不同，必须各自归一化后再合并，不能直接拼"""
    notes = [{"source_id": "note:a", "score": 100.0}, {"source_id": "note:b", "score": 200.0}]
    kb = [{"source_id": "kb:c", "score": 0.01}]
    merged = merge_sources(notes, kb)
    by_id = {m["source_id"]: m["score"] for m in merged}
    # 笔记内部：距离小的 a 应高于 b
    assert by_id["note:a"] > by_id["note:b"]
    # kb 只有一个，归一化后为满分
    assert by_id["kb:c"] == 1.0


def test_merge_sources_keeps_both_source_types():
    """双源合并后，笔记与知识库都必须保留，不能被吃掉"""
    notes = [{"source_id": "note:a", "source_type": "note", "score": 0.1}]
    kb = [{"source_id": "kb:c", "source_type": "knowledge_base", "score": 0.1}]
    merged = merge_sources(notes, kb)
    assert {m["source_type"] for m in merged} == {"note", "knowledge_base"}
