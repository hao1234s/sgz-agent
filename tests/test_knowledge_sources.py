import pytest


def test_official_facts_preserve_provenance_and_unknown_version():
    from sgz_agent.knowledge_sources import normalize_official
    item = {"priRowId": "123", "priVal": "测试武将", "details": [
        {"key": "武力", "val": "72", "colType": 2},
        {"key": "自带战法", "val": "示例战法", "colType": 2},
        {"key": "自带战法描述", "val": "造成52%伤害", "colType": 2},
        {"key": "所属赛季", "val": "", "colType": 2},
        {"key": "列传", "val": "不应参与检索的故事", "colType": 2},
        {"key": "移动icon", "val": "https://example.com/a.png", "colType": 4},
    ]}
    r = normalize_official(item, "general", "456", "2026-10-25T00:00:00+00:00")
    assert r["fields"]["武力"] == "72"
    assert "52%" in r["text"]
    assert "故事" not in r["text"] and "a.png" not in r["text"]
    assert r["season"] is None and r["source_updated_at"] is None
    assert r["level"] is None and r["version_status"] == "unverified"
    assert r["source_url"].endswith("tbid=456&rowid=123&from=wjk")


@pytest.mark.parametrize("item", [{}, {"priRowId": "1", "priVal": "", "details": []},
                                  {"priRowId": "1", "priVal": None, "details": [
                                      {"key": "战法描述", "val": "效果", "colType": 2}]},
                                  {"priRowId": "1", "priVal": "空数据", "details": []}])
def test_malformed_official_record_is_rejected(item):
    from sgz_agent.knowledge_sources import normalize_official
    with pytest.raises(ValueError):
        normalize_official(item, "tactic", "456", "2026-10-25")


def test_complete_catalog_retires_absent_ids_but_failed_listing_preserves_them(tmp_path, monkeypatch):
    import json
    from sgz_agent import knowledge_sources as source
    table = source.TABLES["general"][0]
    old = source.normalize_official({"priRowId": "999", "priVal": "已撤下武将", "details": [
        {"key": "自带战法", "val": "测试", "colType": 2}]}, "general", table, "2026-10-24")
    output = tmp_path / "records.jsonl"
    output.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    def response(self, method, params):
        if method.endswith("ListByCategory"):
            items = [{"priRowId": "1", "priVal": "现有武将"}]
        else:
            kind = "自带战法" if params["tbId"] == table else "战法描述"
            items = [{"priRowId": "1", "priVal": "现有武将", "details": [
                {"key": kind, "val": "测试效果", "colType": 2}]}]
        return {"fetched_at": "2026-10-25", "response": {"result": {"totalCount": 1, "items": items}}}
    monkeypatch.setattr(source.PublicSource, "get", response)
    source.crawl(tmp_path, refresh=True)
    records = [json.loads(s) for s in output.read_text(encoding="utf-8").splitlines()]
    retired = next(r for r in records if r["id"] == old["id"])
    assert retired["catalog_status"] == "not_in_latest_listing"
    assert retired["version_status"] == "historical"
    output.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    def failed(*args, **kwargs):
        raise RuntimeError("network_unavailable")
    monkeypatch.setattr(source.PublicSource, "get", failed)
    source.crawl(tmp_path, refresh=True)
    preserved = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert preserved.get("catalog_status") != "not_in_latest_listing"
