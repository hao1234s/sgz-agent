import pytest


class Embeddings:
    name = "test-embedding"
    dimensions = 3

    def documents(self, texts):
        return [[1.0, float("恢复" in t), float("缴械" in t)] for t in texts]

    def query(self, text):
        return self.documents([text])[0]


def record(identity="a", name="曹操", text="曹操\n恢复兵力", season=None, kind="general"):
    return {"id": identity, "name": name, "text": text, "kind": kind, "season": season,
            "region": "cn", "source_url": "https://example.com/" + identity,
            "fields": {"说明": text}, "fetched_at": "2026-10-25", "version_status": "unverified"}


def test_upsert_exact_match_provenance_and_filters(tmp_path):
    from sgz_agent.knowledge import KnowledgeBase
    with KnowledgeBase(tmp_path, embedder=Embeddings()) as kb:
        kb.ingest([record(), record("b", "曹丕", "曹丕\n缴械", "S2"),
                   record("c", "测试战法", "曹操的战法", "S2", "tactic")])
        kb.ingest([record()])
        assert kb.status()["records"] == 3
        kb.index()
        hits = kb.search("曹操")["results"]
        assert hits[0]["name"] == "曹操"
        assert hits[0]["source_url"] == "https://example.com/a"
        assert [x["name"] for x in kb.search("曹操", kind="tactic")["results"]] == ["测试战法"]
        assert all(x["season"] == "S2" for x in kb.search("曹操", season="S2")["results"])
        assert kb.search("曹操", season="S99")["results"] == []
        with pytest.raises(ValueError):
            kb.search(" ")
        with pytest.raises(ValueError):
            kb.search("曹操", limit=999)


def test_changed_entity_hides_old_chunks_and_reindex_replaces_them(tmp_path):
    from sgz_agent.knowledge import KnowledgeBase
    with KnowledgeBase(tmp_path, embedder=Embeddings()) as kb:
        kb.ingest([record(text="曹操\n旧效果恢复")])
        kb.index()
        kb.ingest([record(text="曹操\n新效果缴械")])
        assert kb.status()["pending"] == 1
        assert "旧效果" not in str(kb.search("曹操")["results"])
        kb.index()
        assert "新效果" in str(kb.search("曹操")["results"])
        assert kb.status()["vectors"] == 1
        assert kb.index()["indexed"] == 0


def test_failed_embedding_is_retryable_and_long_text_is_not_truncated(tmp_path):
    from sgz_agent.knowledge import KnowledgeBase
    class Broken(Embeddings):
        def documents(self, texts):
            raise RuntimeError("embedding_failed")
    with KnowledgeBase(tmp_path, embedder=Broken()) as kb:
        kb.ingest([record(text="长文" + "恢复兵力。" * 150 + "最后一段")])
        with pytest.raises(RuntimeError, match="embedding_failed"):
            kb.index()
        assert kb.status()["pending"] == 1
    with KnowledgeBase(tmp_path, embedder=Embeddings()) as kb:
        kb.index()
        assert kb.status()["vectors"] >= 3
        assert kb.status()["pending"] == 0
        assert "最后一段" in kb.search("曹操")["results"][0]["text"]


def test_source_timestamp_refresh_does_not_reembed_content(tmp_path):
    from sgz_agent.knowledge import KnowledgeBase
    with KnowledgeBase(tmp_path, embedder=Embeddings()) as kb:
        kb.ingest([record()]); kb.index()
        updated = {**record(), "fetched_at": "2026-10-26"}
        kb.ingest([updated])
        assert kb.index()["indexed"] == 0
        assert kb.search("曹操")["results"][0]["fetched_at"] == "2026-10-26"


def test_withdrawn_record_remains_archived_but_not_retrievable(tmp_path):
    from sgz_agent.knowledge import KnowledgeBase
    with KnowledgeBase(tmp_path, embedder=Embeddings()) as kb:
        kb.ingest([record()]); kb.index()
        kb.ingest([{**record(), "catalog_status": "not_in_latest_listing"}])
        assert kb.search("曹操")["results"] == []
        assert kb.status()["retired"] == 1


@pytest.mark.parametrize("change", ["updated", "retired"])
def test_stale_vectors_cannot_crowd_out_valid_semantic_candidates(tmp_path, change):
    from sgz_agent.knowledge import KnowledgeBase
    class CrowdingEmbeddings(Embeddings):
        def documents(self, texts):
            return [[0.7, 0.7, 0.0] if "remaining" in t else [1.0, 0.0, 0.0] for t in texts]
        def query(self, text):
            return [1.0, 0.0, 0.0]
    with KnowledgeBase(tmp_path, embedder=CrowdingEmbeddings()) as kb:
        old = [record(str(i), "old" + str(i), "old") for i in range(81)]
        kb.ingest(old + [record("valid", "remaining", "remaining")])
        kb.index()
        updates = [{**r, **({"text": "changed"} if change == "updated" else
                            {"catalog_status": "not_in_latest_listing"})} for r in old]
        kb.ingest(updates)
        hits = kb.search("罕见查询")["results"]
        assert [r["id"] for r in hits] == ["valid"]
