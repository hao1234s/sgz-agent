import json
from pathlib import Path
import sys

import pytest

from sgz_agent.knowledge import KnowledgeBase
from test_knowledge import Embeddings, record


def pack_file(tmp_path, **changes):
    item = {
        "id": "camp", "name": "营帐征兵", "text": "营帐可补充兵力。",
        "statement_type": "game_fact", "source_type": "official_guide",
        "source_url": "https://example.com/camp", "published_at": "2019-08-07",
        "checked_at": "2026-10-25", "version_status": "historical",
        "confidence": "medium", "limitations": ["当前赛季仍需现场核对。"],
        **changes,
    }
    path = tmp_path / "curated.json"
    path.write_text(json.dumps({"schema_version": 1, "pack_id": "logistics",
                               "records": [item]}, ensure_ascii=False), encoding="utf-8")
    return path


def read_jsonl(path):
    return {r["id"]: r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}


def test_curated_import_preserves_catalog_and_database_only_records_across_reopen(tmp_path):
    source = pack_file(tmp_path)
    directory = tmp_path / "knowledge"
    directory.mkdir()
    catalog = record("official:table:1")
    output = directory / "records.jsonl"
    output.write_text(json.dumps(catalog, ensure_ascii=False) + "\n", encoding="utf-8")
    with KnowledgeBase(directory, embedder=Embeddings()) as kb:
        kb.ingest([record("database-only")])
        result = kb.import_curated(source)
        assert result["changed"] == 1
        assert result["pending"] == 0 and result["records"] == 3
        saved = output.read_bytes()
        assert read_jsonl(output)["official:table:1"] == catalog
        assert kb.import_curated(source)["indexed"] == 0
        assert output.read_bytes() == saved
    with KnowledgeBase(directory, embedder=Embeddings()) as kb:
        hit = kb.search("营帐征兵", kind="mechanic")["results"][0]
        assert hit["source_url"] == "https://example.com/camp"
        assert hit["published_at"] == "2019-08-07"
        assert hit["checked_at"] == "2026-10-25"
        assert hit["confidence"] == "medium"
        assert hit["limitations"] == ["当前赛季仍需现场核对。"]
        assert {r["id"] for r in kb.search("曹操")["results"]} >= {"official:table:1", "database-only"}
        pack_file(tmp_path, text="营帐补兵需核对铜币。")
        assert kb.import_curated(source)["changed"] == 1
        assert kb.search("营帐征兵")["results"][0]["text"].endswith("营帐补兵需核对铜币。")
        assert read_jsonl(output)["official:table:1"] == catalog


def test_embedding_failure_leaves_curated_records_durable_and_retryable(tmp_path):
    class Broken(Embeddings):
        def documents(self, texts):
            raise RuntimeError("embedding_failed")
    source = pack_file(tmp_path)
    directory = tmp_path / "knowledge"
    with KnowledgeBase(directory, embedder=Broken()) as kb:
        with pytest.raises(RuntimeError, match="embedding_failed"):
            kb.import_curated(source)
        assert "curated:logistics:camp" in read_jsonl(directory / "records.jsonl")
        assert kb.status()["pending"] == 1
    with KnowledgeBase(directory, embedder=Embeddings()) as kb:
        result = kb.import_curated(source)
        assert result["changed"] == 0 and result["indexed"] == 1
        assert kb.search("营帐征兵")["results"][0]["id"] == "curated:logistics:camp"


def test_curated_import_does_not_replace_existing_catalog_revisions(tmp_path):
    source = pack_file(tmp_path)
    directory = tmp_path / "knowledge"
    directory.mkdir()
    file_record = record("official:table:1", text="等待目录索引的新内容")
    database_record = record("official:table:1", text="数据库中已有的目录内容")
    output = directory / "records.jsonl"
    output.write_text(json.dumps(file_record, ensure_ascii=False) + "\n", encoding="utf-8")
    with KnowledgeBase(directory, embedder=Embeddings()) as kb:
        kb.ingest([database_record])
        kb.import_curated(source)
        assert read_jsonl(output)["official:table:1"] == file_record
        assert kb.search("曹操")["results"][0]["text"] == database_record["text"]


def test_curated_import_rejects_ownership_collision_without_changing_files(tmp_path):
    source = pack_file(tmp_path)
    directory = tmp_path / "knowledge"
    with KnowledgeBase(directory, embedder=Embeddings()) as kb:
        kb.ingest([record("curated:logistics:camp")])
        with pytest.raises(ValueError, match="curated_record_ownership_conflict"):
            kb.import_curated(source)
        assert kb.status()["records"] == 1
        assert not (directory / "records.jsonl").exists()


def test_curated_records_survive_later_catalog_crawl_and_reindex(tmp_path, monkeypatch):
    from sgz_agent import knowledge_sources
    source = pack_file(tmp_path)
    directory = tmp_path / "knowledge"
    def unavailable(*args, **kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(knowledge_sources.PublicSource, "get", unavailable)
    with KnowledgeBase(directory, embedder=Embeddings()) as kb:
        kb.import_curated(source)
        knowledge_sources.crawl(directory)
        records = read_jsonl(directory / "records.jsonl")
        assert records["curated:logistics:camp"]["source_url"] == "https://example.com/camp"
        kb.ingest(records.values())
        kb.index()
    with KnowledgeBase(directory, embedder=Embeddings()) as kb:
        assert kb.search("营帐征兵")["results"][0]["curation_pack"] == "logistics"


@pytest.mark.parametrize("changes", [
    {"statement_type": "user_policy"},
    {"source_url": "user://conversation/2026-10-25"},
    {"checked_at": "not-a-date"},
    {"limitations": []},
    {"id": "official:table:1"},
    {"name": "长" * 250},
])
def test_invalid_provenance_is_rejected_before_persistence(tmp_path, changes):
    source = pack_file(tmp_path, **changes)
    directory = tmp_path / "knowledge"
    with KnowledgeBase(directory, embedder=Embeddings()) as kb:
        with pytest.raises(ValueError, match="curated"):
            kb.import_curated(source)
        assert kb.status()["records"] == 0
        assert not (directory / "records.jsonl").exists()


def test_shipped_pack_keeps_user_policies_separate_in_search(tmp_path):
    source = Path(__file__).resolve().parents[1] / "config/knowledge-logistics.json"
    with KnowledgeBase(tmp_path, embedder=Embeddings()) as kb:
        result = kb.import_curated(source)
        assert result["pending"] == 0
        camp = kb.search("营帐调动与士气", kind="mechanic")["results"][0]
        assert camp["statement_type"] == "game_fact"
        assert camp["source_type"] == "official_guide"
        assert camp["version_status"] == "historical"
        policy = kb.search("回城满兵策略", kind="mechanic")["results"][0]
        assert policy["statement_type"] == "user_policy"
        assert policy["source_type"] == "user_instruction"
        assert policy["source_url"].startswith("user://")
        assert policy["version_status"] == "user_policy"
        assert "用户策略" in policy["text"] and "非游戏规则" in policy["text"]
        assert policy["policy"]["city_requires_full_capacity"] is True


def test_import_cli_indexes_and_respects_existing_worker_lock(tmp_path, monkeypatch, capsys):
    from sgz_agent import knowledge_cli
    source = pack_file(tmp_path)
    directory = tmp_path / "knowledge"
    monkeypatch.setattr(knowledge_cli, "KnowledgeBase", lambda directory: KnowledgeBase(directory, embedder=Embeddings()))
    monkeypatch.setattr(sys, "argv", ["sgz-knowledge", "import", "--source", str(source), "--directory", str(directory)])
    with KnowledgeBase(directory, embedder=Embeddings()):
        assert knowledge_cli.main() == 1
        assert "knowledge_in_use" in capsys.readouterr().err
        assert not (directory / "records.jsonl").exists()
    assert knowledge_cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["changed"] == 1 and result["pending"] == 0
    assert knowledge_cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["changed"] == 0 and result["indexed"] == 0
