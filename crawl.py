import os
import re
import json
import requests
from pathlib import Path

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

CACHE_FILES = [
    "public/data/kakao-promotions-today.json",
    "public/data/naver-promotions-today.json",
    "public/data/ridi-promotions-today.json",

    "public/data/kakao-today.json",
    "public/data/naver-today.json",
    "public/data/ridi-today.json",
    "public/data/combined-today.json",

    "out/kakao.json",
    "out/naver.json",
    "out/ridi.json",
    "out/combined.json",

    "kakao.json",
    "naver.json",
    "ridi.json",
    "combined.json",
]


def rich_text(value):
    return [{"type": "text", "text": {"content": value or ""}}]


def clean_value(value):
    if value is None:
        return None

    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip(" :：|·,/")

    bad_values = {"txt", "바로가기", "자동완성 끄기", "자동완성 켜기", "-"}
    if not value or "@" in value or value.lower() in bad_values:
        return None

    return value


def normalize_title(value):
    return re.sub(r"\s+", "", str(value or "").strip())


def get_plain_text(prop):
    if not prop:
        return ""

    values = prop.get("title") or prop.get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in values).strip()


def get_novels_to_update():
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    payload = {
        "filter": {
            "property": "platform",
            "select": {"is_not_empty": True},
        },
        "page_size": 100,
    }

    results = []

    while True:
        res = requests.post(url, headers=NOTION_HEADERS, json=payload)
        data = res.json()

        for page in data.get("results", []):
            props = page.get("properties", {})

            title_prop = props.get("제목") or props.get("이름")
            title = get_plain_text(title_prop)
            if not title:
                continue

            platform_prop = props.get("platform")
            platform = None
            if platform_prop:
                platform = platform_prop.get("select", {}).get("name", "").lower().strip()

            author = get_plain_text(props.get("저자 / 감독"))
            publisher = get_plain_text(props.get("출판사"))

            needs_cover = not page.get("cover")
            needs_author = not author
            needs_publisher = not publisher

            if not (needs_cover or needs_author or needs_publisher):
                continue

            results.append({
                "id": page["id"],
                "title": title,
                "platform": platform or "",
                "needs_cover": needs_cover,
                "needs_author": needs_author,
                "needs_publisher": needs_publisher,
            })

        if not data.get("has_more"):
            break

        payload["start_cursor"] = data["next_cursor"]

    return results


def iter_json_items(obj):
    if isinstance(obj, list):
        for item in obj:
            yield from iter_json_items(item)

    elif isinstance(obj, dict):
        if obj.get("title") or obj.get("제목"):
            yield obj

        for value in obj.values():
            yield from iter_json_items(value)


def pick_best_result(results):
    if not results:
        return None

    def score(item):
        return sum(
            1 for key in ["cover", "author", "publisher"]
            if item.get(key)
        )

    return max(results, key=score)


def search_from_cache(title):
    target = normalize_title(title)
    found_results = []

    for file_path in CACHE_FILES:
        path = Path(file_path)

        if not path.exists():
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for item in iter_json_items(data):
                item_title = item.get("title") or item.get("제목")

                if normalize_title(item_title) != target:
                    continue

                cover = (
                    item.get("thumbnail")
                    or item.get("thumbnailUrl")
                    or item.get("cover")
                    or item.get("coverUrl")
                    or item.get("image")
                    or item.get("표지")
                )

                author = (
                    item.get("author")
                    or item.get("저자")
                    or item.get("저자 / 감독")
                    or item.get("writer")
                    or item.get("작가")
                )

                publisher = (
                    item.get("출판사")
                    or item.get("publisher")
                    or item.get("publisherName")
                    or item.get("provider")
                    or item.get("cpName")
                    or item.get("발행자")
                )

                result = {
                    "cover": str(cover).strip() if cover else None,
                    "author": clean_value(author),
                    "publisher": clean_value(publisher),
                    "source_file": file_path,
                }

                if result["cover"] or result["author"] or result["publisher"]:
                    found_results.append(result)

        except Exception as e:
            print(f"  ⚠️ 캐시 읽기 실패: {file_path} / {e}")

    best = pick_best_result(found_results)

    if best:
        print(
            f"  ✅ 캐시 발견: {best['source_file']} / "
            f"cover={bool(best.get('cover'))}, "
            f"author={best.get('author')}, "
            f"publisher={best.get('publisher')}"
        )
        return best

    print("  ❌ 캐시에서 정보 없음")
    return None


def update_notion_page(page_id, found, novel):
    try:
        pid = page_id.replace("-", "")
        pid = f"{pid[:8]}-{pid[8:12]}-{pid[12:16]}-{pid[16:20]}-{pid[20:]}"

        payload = {"properties": {}}

        if novel["needs_author"] and found.get("author"):
            payload["properties"]["저자 / 감독"] = {
                "rich_text": rich_text(found["author"])
            }

        if novel["needs_publisher"] and found.get("publisher"):
            payload["properties"]["출판사"] = {
                "rich_text": rich_text(found["publisher"])
            }

        if novel["needs_cover"] and found.get("cover"):
            payload["cover"] = {
                "type": "external",
                "external": {"url": found["cover"]},
            }

        if not payload["properties"] and "cover" not in payload:
            print("  ⚠️ 업데이트할 값 없음")
            return False

        res = requests.patch(
            f"https://api.notion.com/v1/pages/{pid}",
            headers=NOTION_HEADERS,
            json=payload,
        )

        if not res.ok:
            print(f"  ❌ 노션 업데이트 실패: {res.status_code} {res.text}")

        return res.ok

    except Exception as e:
        print(f"  ❌ 노션 업데이트 오류: {e}")
        return False


novels = get_novels_to_update()
print(f"📚 업데이트 필요한 웹소설: {len(novels)}개")

for novel in novels:
    print(f"\n🔍 [{novel['platform']}] {novel['title']}")

    found = search_from_cache(novel["title"])

    if found:
        ok = update_notion_page(novel["id"], found, novel)
        print(f"  {'✅ 노션 업데이트 완료' if ok else '❌ 노션 업데이트 실패'}")
    else:
        print("  ❌ JSON 캐시에서 정보 찾기 실패")

print("\n🎉 완료!")
