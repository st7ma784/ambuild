from ambuild_ingest.ingest import _fragmentTypes


def test_fragment_types_dict_is_stored_as_json():
    """Newer Ambuild records fragment_types as a dict; steps.fragment_types keeps JSON text"""
    assert _fragmentTypes({"B": 1, "A": 3}) == '{"A": 3, "B": 1}'


def test_fragment_types_string_is_kept():
    """Older runs recorded the repr of a defaultdict; it is stored as it was"""
    legacy = "defaultdict(<class 'list'>, {'A': 3})"
    assert _fragmentTypes(legacy) == legacy
    assert _fragmentTypes(None) is None
