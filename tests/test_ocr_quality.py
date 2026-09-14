"""Fixed OCR quality-suite composition and metric helpers."""

from collections import Counter

from scripts.benchmark_ocr_quality import _cases, _distance


def test_quality_suite_has_planned_50_image_mix():
    cases = list(_cases())
    assert len(cases) == 50
    assert Counter(case[0] for case in cases) == {
        "english": 20,
        "chinese": 20,
        "mixed_code": 10,
    }
    assert len({(category, index) for category, index, *_ in cases}) == 50


def test_quality_distance_counts_whitespace_and_character_edits():
    assert _distance("    return", "return") == 4
    assert _distance("结果: {item}", "结果：｛item｝") == 4
