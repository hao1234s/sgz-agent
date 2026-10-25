"""Public website content collection; no game client or account APIs."""
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://galaxias-api.lingxigames.com/ds/ajax/endpoint.json"
TABLES = {
    "general": ("350659087930767364", "350958444404029449,350958444404029450,350958444404029451,350958444404029452"),
    "tactic": ("350659307657771017", "352052655832446980,352052655832446981"),
}
CAUTION = "官网未注明当前版本核验日期及数值等级；不可直接视为最新或满级数值。"


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", str(value)))).strip()


def normalize_official(item, kind, table, fetched_at):
    if not isinstance(item.get("priVal"), str):
        raise ValueError("invalid_official_record")
    identity, title = str(item.get("priRowId", "")), clean(item.get("priVal", ""))
    fields = {d["key"]: clean(d["val"]) for d in item.get("details", [])
              if d.get("colType") in {2, 3} and d.get("key") != "列传" and d.get("val")}
    required = "自带战法" if kind == "general" else "战法描述"
    if kind not in TABLES or not identity.isdigit() or not title or not fields.get(required):
        raise ValueError("invalid_official_record")
    section = "wjk" if kind == "general" else "zfk"
    return {"id": f"official:{table}:{identity}", "kind": kind, "name": title,
            "fields": fields, "text": title + "\n" + "\n".join(f"{k}：{v}" for k, v in fields.items()),
            "source_url": f"https://sgzzlb.lingxigames.com/m/station/detail/?tbid={table}&rowid={identity}&from={section}",
            "source_type": "official_catalog", "region": "cn", "season": fields.get("所属赛季"),
            "level": None, "source_updated_at": None, "published_at": None,
            "fetched_at": fetched_at, "version_status": "unverified", "caution": CAUTION}


class PublicSource:
    def __init__(self, directory, refresh=False, interval=0.5):
        self.cache = Path(directory) / "raw"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.refresh, self.interval, self.last = refresh, max(0.5, interval), 0.0

    def get(self, method, params):
        body = {"api": f"/api/l/owresource/{method}", "params": {"gameId": 10000100, **params}}
        encoded = json.dumps(body, sort_keys=True).encode()
        path = self.cache / (sha256(encoded).hexdigest() + ".json")
        if path.exists() and not self.refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        for attempt in range(3):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                request = Request(ENDPOINT, data=encoded, headers={
                    "Content-Type": "application/json", "User-Agent": "SGZKnowledge/0.1 (local research)",
                    "Referer": "https://sgzzlb.lingxigames.com/m/station/"})
                with urlopen(request, timeout=25) as response:
                    value = json.load(response)
                if value.get("success") is not True or not isinstance(value.get("result", {}).get("items"), list):
                    raise ValueError("unexpected_source_response")
                result = {"fetched_at": now(), "request": body, "response": value}
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
                return result
            except (HTTPError, URLError, TimeoutError) as error:
                if isinstance(error, HTTPError) and error.code not in {408, 429, 500, 502, 503, 504}:
                    raise
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)


def crawl(directory, refresh=False, progress=None):
    directory = Path(directory)
    source = PublicSource(directory, refresh=refresh)
    records, errors, coverage = {}, [], {}
    output = directory / "records.jsonl"
    # Keep previously collected records when a later refresh has a partial failure.
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            records[record["id"]] = record
    for kind, (table, categories) in TABLES.items():
        listing, expected, listing_complete = {}, None, False
        try:
            for page in range(100):
                data = source.get("getQueryDataInfoListByCategory", {
                    "tbId": table, "categoryIds": categories, "page": page, "size": 28})
                result = data["response"]["result"]
                expected = int(result["totalCount"])
                before = len(listing)
                for item in result["items"]:
                    listing[item["priRowId"]] = item
                if len(listing) >= expected:
                    break
                if len(listing) == before:
                    raise ValueError("catalog_pagination_stalled")
            else:
                raise ValueError("catalog_page_limit")
            if not listing:
                raise ValueError("empty_catalog")
            if len(listing) != expected:
                raise ValueError("catalog_count_changed_during_pagination")
            listing_complete = True
        except Exception as error:
            errors.append({"kind": kind, "stage": "list", "error": str(error)})
        if refresh and listing_complete:
            prefix = f"official:{table}:"
            for identity, record in records.items():
                if identity.startswith(prefix) and identity[len(prefix):] not in listing:
                    record["catalog_status"] = "not_in_latest_listing"
                    record["version_status"] = "historical"
                    record.setdefault("retired_at", now())
                    record["caution"] = CAUTION + " 已不在最近完整目录中，仅保留归档。"
        collected = 0
        for identity in listing:
            try:
                data = source.get("getQueryDataInfoByPriId", {"tbId": table, "priRowId": identity})
                items = data["response"]["result"]["items"]
                if len(items) != 1 or items[0].get("priRowId") != identity:
                    raise ValueError("detail_identity_mismatch")
                record = normalize_official(items[0], kind, table, data["fetched_at"])
                records[record["id"]] = record
                collected += 1
                if progress and collected % 25 == 0:
                    progress({"kind": kind, "collected": collected, "total": len(listing)})
            except Exception as error:
                errors.append({"kind": kind, "id": identity, "stage": "detail", "error": str(error)})
        coverage[kind] = {"reported": expected, "discovered": len(listing), "collected": collected,
                          "listing_complete": listing_complete}
    seed_path = Path(__file__).resolve().parents[2] / "config/knowledge-mechanics.json"
    seeds = json.loads(seed_path.read_text(encoding="utf-8"))
    for item in seeds["records"]:
        record = {"id": "mechanic:" + item["id"], "kind": "mechanic", "name": item["name"],
                  "text": item["name"] + "\n" + item["text"], "fields": {"解释": item["text"]},
                  "region": "cn", "season": None, "level": None, "source_url": seeds["source_url"],
                  "source_type": "official_community_guide_summary", "published_at": seeds["published_at"],
                  "source_updated_at": None, "fetched_at": seeds["checked_at"], "version_status": "historical",
                  "caution": "基础机制事实摘要；来源为2019年官方社区攻略，非当前版本逐项验证。"}
        records[record["id"]] = record
    temp = output.with_suffix(".tmp")
    temp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records.values()), encoding="utf-8")
    temp.replace(output)
    report = {"finished_at": now(), "records": len(records), "coverage": coverage, "errors": errors,
              "complete": not errors, "scope": "官网基础武将库、战法库；不含全赛季增补表", "path": str(output)}
    (directory / "crawl-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
