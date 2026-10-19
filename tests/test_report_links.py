import copy
import json

import lake
import refresh_report_links as links


def test_committed_report_links_resolve_after_the_medallion_move():
    doc = json.loads(links.REPORTS.read_text(encoding="utf-8"))
    assert links.refresh(doc) == 0
    for record in doc["records"]:
        assert lake.resolve_data_path(record["source_file"]).is_file()
        if record.get("source_md_url"):
            assert "/data/silver/reports/markdown/" in record["source_md_url"]


def test_link_migration_preserves_research_results_and_provenance():
    record = json.loads(links.REPORTS.read_text(encoding="utf-8"))["records"][0]
    expected = {"generated_at": "unchanged", "summary": {"count": 1}, "records": [record]}
    old = copy.deepcopy(expected)
    old["records"][0]["source_md_url"] = record["source_md_url"].replace(
        "/data/silver/reports/markdown/", "/data/markdown/")
    assert links.refresh(old) == 1
    assert old == expected
