import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote

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


def clean_value(value):
    if not value:
        return None

    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip(" :：|·,/")

    if not value:
        return None

    bad_values = {"txt", "바로가기", "자동완성 끄기", "자동완성 켜기"}
    if "@" in value or value.lower() in bad_values:
        return None

    return value

def deep_find(obj, keys):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and v:
                return v
            found = deep_find(v, keys)
            if found:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = deep_find(item, keys)
            if found:
                return found

    return None



def extract_author_publisher_from_text(text):
    author = None
    publisher = None

    # 작가/글 라벨 뒤에 공백이 있을 때만 라벨로 인정
    # 글근육 같은 작가명을 "근육"으로 자르지 않기 위함
    author_patterns = [
        r"(?:저자|작가|글)\s+([^|·,\n\r]+)",
        r"작가명\s*[:：]\s*([^|·,\n\r]+)",
    ]

    publisher_patterns = [
        r"(?:출판사|출판|제공)\s+([^|·,\n\r]+)",
        r"출판사명\s*[:：]\s*([^|·,\n\r]+)",
    ]

    for pattern in author_patterns:
        m = re.search(pattern, text)
        if m:
            author = clean_value(m.group(1))
            break

    for pattern in publisher_patterns:
        m = re.search(pattern, text)
        if m:
            publisher = clean_value(m.group(1))
            break

    return author, publisher


def crawl_naver(title):
    try:
        search_url = f"https://series.naver.com/search/search.series?t=all&fs=novel&q={requests.utils.quote(title)}"
        res = requests.get(search_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        a = soup.select_one("a.pic[href*='productNo']")
        if not a:
            print("  ❌ 네이버 검색결과 없음")
            return None

        cover = None
        img = a.select_one("img")
        if img and img.get("src"):
            cover = img["src"].replace("type=m79", "type=m260")

        href = a.get("href", "")
        detail_url = "https://series.naver.com" + href if href.startswith("/") else href

        detail_res = requests.get(detail_url, headers=HEADERS, timeout=10)
        detail_soup = BeautifulSoup(detail_res.text, "html.parser")
        detail_text = detail_soup.get_text(" ", strip=True)

        author = None
        publisher = None

        author_a = detail_soup.select_one("a[href*='authorNo']")
        if author_a:
            author = clean_value(author_a.get_text(" ", strip=True))

        if not author:
            author_match = re.search(r"글\s+([^\s|·,]+)", detail_text)
            if author_match:
                author = clean_value(author_match.group(1))

        publisher_span = detail_soup.find("span", string="출판사")
        if publisher_span:
            parent_li = publisher_span.find_parent("li")
            if parent_li:
                publisher_a = parent_li.find("a")
                if publisher_a:
                    publisher = clean_value(publisher_a.get_text(" ", strip=True))

        if not publisher:
            publisher_match = re.search(r"출판사\s+([^\s|·,]+)", detail_text)
            if publisher_match:
                publisher = clean_value(publisher_match.group(1))

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
        urls = [
            "https://ridibooks.com/bestsellers/fantasy_serial",
            "https://ridibooks.com/bestsellers/romance_serial",
            "https://ridibooks.com/bestsellers/romance_fantasy_serial",
            "https://ridibooks.com/bestsellers/bl_serial",
        ]

        ridi_headers = {
            **HEADERS,
            "Referer": "https://ridibooks.com/",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }

        for url in urls:
            res = requests.get(url, headers=ridi_headers, timeout=15)
            html = res.text

            if title not in html:
                continue

            idx = html.find(title)
            block = html[max(0, idx - 3000): idx + 5000]

            cover = None
            cover_match = re.search(r'https://img\.ridicdn\.net/cover/[^"\']+', block)
            if cover_match:
                cover = cover_match.group(0)
                cover = cover.replace("/small", "/large").split("?")[0] + "#1"

            author = None
            author_match = re.search(r'<a[^>]+href="/author/[^"]+"[^>]*>([^<]+)</a>', block)
            if author_match:
                author = clean_value(author_match.group(1))

            publisher = None
            publisher_match = re.search(
                r'<a[^>]+href="/search\?q=[^"]*%EC%B6%9C%ED%8C%90%EC%82%AC[^"]*"[^>]*>([^<]+)</a>',
                block,
            )
            if publisher_match:
                publisher = clean_value(publisher_match.group(1))

            print(f"  ✅ 리디: cover={bool(cover)}, author={author}, publisher={publisher}")

            return {
                "cover": cover,
                "author": author,
                "publisher": publisher,
            }

        print("  ❌ 리디 순위 페이지에서 제목 없음")
        return None

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

        kakao_headers = {
            "User-Agent": HEADERS["User-Agent"],
            "Referer": "https://page.kakao.com/",
            "Origin": "https://page.kakao.com",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }

        res = requests.get(url, headers=kakao_headers, timeout=15)

        if not res.ok:
            print(f"  ❌ 카카오 API 응답 실패: {res.status_code}")
            return None

        data = res.json()
        items = data.get("result", {}).get("list", [])
        if not items:
            print("  ❌ 카카오 검색결과 없음")
            return None

        item = items[0]

        thumbnail_key = deep_find(item, ["thumbnail", "thumbnailUrl", "image"])
        cover = (
            f"https://dn-img-page.kakao.com/download/resource?kid={thumbnail_key}&filename=th3"
            if thumbnail_key and not str(thumbnail_key).startswith("http")
            else thumbnail_key
        )

        author = deep_find(item, [
            "author", "writer", "authors", "artist", "authorName", "writerName"
        ])

        if isinstance(author, list):
            author = ", ".join(
                str(a.get("name", a)) if isinstance(a, dict) else str(a)
                for a in author
            )

        publisher = deep_find(item, [
            "publisher", "publisherName", "cpName", "provider",
            "providerName", "company", "companyName", "copyright", "publisherName"
        ])

        content_id = deep_find(item, [
            "seriesId", "id", "productId", "contentId", "series_id", "content_id", "uid"
        ])

        if not publisher and content_id:
            detail_url = f"https://page.kakao.com/content/{content_id}"
            detail_res = requests.get(detail_url, headers=kakao_headers, timeout=15)
            detail_html = detail_res.text

            m = re.search(
                r'발행자</span>\s*<span[^>]*>([^<]+)</span>',
                detail_html
            )
            if m:
                publisher = m.group(1).strip()

        author = clean_value(author)
        publisher = clean_value(publisher)

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
