import os
import re
import requests
from bs4 import BeautifulSoup

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def rich_text(value):
    return [
        {
            "type": "text",
            "text": {
                "content": value or "",
            },
        }
    ]


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
            "select": {
                "is_not_empty": True
            }
        },
        "page_size": 100
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
                "platform": platform or "naver",
                "needs_cover": needs_cover,
                "needs_author": needs_author,
                "needs_publisher": needs_publisher,
            })

        if not data.get("has_more"):
            break

        payload["start_cursor"] = data["next_cursor"]

    return results


def crawl_naver(title):
    try:
        url = f"https://series.naver.com/search/search.series?t=all&fs=novel&q={requests.utils.quote(title)}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        a = soup.select_one("a.pic[href*='productNo']")
        if not a:
            print("  ❌ 네이버 검색결과 없음")
            return None

        cover = None
        img = a.select_one("img")
        if img and img.get("src"):
            cover = img["src"].replace("type=m79", "type=m260")

        item = a.find_parent("li") or a.find_parent("div") or soup
        text = item.get_text(" ", strip=True)

        author = None
        publisher = None

        author_match = re.search(r"(?:글|작가)\s*[:：]?\s*([^\s|·]+)", text)
        if author_match:
            author = author_match.group(1).strip()

        publisher_match = re.search(r"(?:출판사|출판)\s*[:：]?\s*([^\s|·]+)", text)
        if publisher_match:
            publisher = publisher_match.group(1).strip()

        print(f"  ✅ 네이버: cover={bool(cover)}, author={author}, publisher={publisher}")

        return {
            "cover": cover,
            "author": author,
            "publisher": publisher,
        }

    except Exception as e:
        print(f"  ❌ 네이버 오류: {e}")

    return None


def crawl_ridi(title):
    try:
        url = f"https://ridibooks.com/search?q={requests.utils.quote(title)}&adult_exclude=n"
        res = requests.get(url, headers=HEADERS, timeout=10)
        html = res.text

        cover = None
        matches = re.findall(r'https://img\.ridicdn\.net/cover/[^\s"\'<>]+', html)
        if matches:
            cover = matches[0].split('"')[0]

        author = None
        publisher = None

        author_match = re.search(r'"author"\s*:\s*"([^"]+)"', html)
        if author_match:
            author = author_match.group(1).strip()

        publisher_match = re.search(r'"publisher"\s*:\s*"([^"]+)"', html)
        if publisher_match:
            publisher = publisher_match.group(1).strip()

        print(f"  ✅ 리디: cover={bool(cover)}, author={author}, publisher={publisher}")

        if not cover and not author and not publisher:
            print("  ❌ 리디 검색결과 없음")
            return None

        return {
            "cover": cover,
            "author": author,
            "publisher": publisher,
        }

    except Exception as e:
        print(f"  ❌ 리디 오류: {e}")

    return None


def crawl_kakao(title):
    try:
        url = (
            "https://bff-page.kakao.com/api/gateway/api/v1/search/series"
            f"?keyword={requests.utils.quote(title)}"
            "&category_uid=11&is_complete=false&sort_type=ACCURACY&page=0&size=25"
        )

        res = requests.get(
            url,
            headers={
                "User-Agent": HEADERS["User-Agent"],
                "Referer": "https://page.kakao.com/",
                "Origin": "https://page.kakao.com",
            },
            timeout=10,
        )

        if not res.ok:
            print(f"  ❌ 카카오 API 응답 실패: {res.status_code}")
            return None

        data = res.json()
        items = data.get("result", {}).get("list", [])
        if not items:
            print("  ❌ 카카오 검색결과 없음")
            return None

        item = items[0]

        thumbnail_key = item.get("thumbnail")
        cover = None
        if thumbnail_key:
            cover = f"https://dn-img-page.kakao.com/download/resource?kid={thumbnail_key}&filename=th3"

        author = (
            item.get("author")
            or item.get("writer")
            or item.get("authors")
            or item.get("artist")
        )

        if isinstance(author, list):
            author = ", ".join(
                str(a.get("name", a)) if isinstance(a, dict) else str(a)
                for a in author
            )

        publisher = (
            item.get("publisher")
            or item.get("publisherName")
            or item.get("cpName")
            or item.get("provider")
        )

        print(f"  ✅ 카카오: cover={bool(cover)}, author={author}, publisher={publisher}")

        return {
            "cover": cover,
            "author": author,
            "publisher": publisher,
        }

    except Exception as e:
        print(f"  ❌ 카카오 오류: {e}")

    return None


def update_notion_page(page_id, found, novel):
    try:
        pid = page_id.replace("-", "")
        pid = f"{pid[:8]}-{pid[8:12]}-{pid[12:16]}-{pid[16:20]}-{pid[20:]}"

        payload = {
            "properties": {}
        }

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
                "external": {
                    "url": found["cover"]
                }
            }

        if not payload["properties"] and "cover" not in payload:
            print("  ⚠️ 업데이트할 값 없음")
            return False

        res = requests.patch(
            f"https://api.notion.com/v1/pages/{pid}",
            headers=NOTION_HEADERS,
            json=payload
        )

        if not res.ok:
            print(f"  ❌ 노션 업데이트 실패: {res.status_code} {res.text}")

        return res.ok

    except Exception as e:
        print(f"  ❌ 노션 업데이트 오류: {e}")
        return False


crawlers = {
    "naver": crawl_naver,
    "ridi": crawl_ridi,
    "kakao": crawl_kakao,
}


novels = get_novels_to_update()
print(f"📚 업데이트 필요한 웹소설: {len(novels)}개")

for novel in novels:
    print(f"\n🔍 [{novel['platform']}] {novel['title']}")

    order = [novel["platform"]] + [
        p for p in ["naver", "ridi", "kakao"] if p != novel["platform"]
    ]

    found = None

    for platform in order:
        found = crawlers[platform](novel["title"])
        if found and (found.get("cover") or found.get("author") or found.get("publisher")):
            break

    if found:
        ok = update_notion_page(novel["id"], found, novel)
        print(f"  {'✅ 노션 업데이트 완료' if ok else '❌ 노션 업데이트 실패'}")
    else:
        print("  ❌ 모든 플랫폼에서 정보 찾기 실패")

print("\n🎉 완료!")
