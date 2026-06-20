import os
import re
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

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

BASE_RIDI_URL = "https://ridibooks.com"

RIDI_CATEGORY_URLS = [
    "https://ridibooks.com/bestsellers/fantasy_serial",
    "https://ridibooks.com/bestsellers/romance_serial",
    "https://ridibooks.com/bestsellers/romance_fantasy_serial",
    "https://ridibooks.com/bestsellers/bl-webnovel",
]


def rich_text(value):
    return [{"type": "text", "text": {"content": value or ""}}]


def clean_value(value):
    if not value:
        return None

    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip(" :：|·,/")

    bad_values = {"txt", "바로가기", "자동완성 끄기", "자동완성 켜기", "-"}
    if not value or "@" in value or value.lower() in bad_values:
        return None

    return value


def get_plain_text(prop):
    if not prop:
        return ""

    values = prop.get("title") or prop.get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in values).strip()


def fetch_soup_playwright(url, click_info_tab=False):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        page = browser.new_page(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1365, "height": 900},
            locale="ko-KR",
        )

        page.goto(url, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(1500)

        if click_info_tab:
            try:
                info_tab = page.locator("span.font-small1", has_text="정보").first
                if info_tab.count() > 0:
                    info_tab.click()
                    page.wait_for_timeout(1000)
            except Exception as e:
                print("  ⚠️ 정보 탭 클릭 실패:", e)

        html = page.content()
        browser.close()

    return BeautifulSoup(html, "html.parser")


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
        for url in RIDI_CATEGORY_URLS:
            soup = fetch_soup_playwright(url)

            cards = [
                li for li in soup.select("li")
                if li.select_one("a.fig-w1hthz")
            ]

            for item in cards:
                title_tag = item.select_one("a.fig-w1hthz")
                item_title = clean_value(title_tag.get_text(" ", strip=True)) if title_tag else None

                if item_title != title:
                    continue

                work_path = title_tag.get("href", "")
                work_id = ""

                m_id = re.search(r"/books/(\d+)", work_path)
                if m_id:
                    work_id = m_id.group(1)

                cover = None

                img = item.select_one("img[alt]")
                if img:
                    srcset = img.get("srcset", "")
                    if srcset:
                        candidates = [x.strip().split(" ")[0] for x in srcset.split(",")]
                        large_candidates = [
                            c for c in candidates
                            if "/large" in c or "/xxlarge" in c
                        ]
                        cover = large_candidates[0] if large_candidates else candidates[-1]
                    else:
                        cover = img.get("src")

                if not cover and work_id:
                    cover = f"https://img.ridicdn.net/cover/{work_id}/large#1"

                author_tag = item.select_one("a.fig-103urjl.e1s6unbg0")
                publisher_tag = item.select_one("a.fig-103urjl.efs2tg41")

                author = clean_value(author_tag.get_text(" ", strip=True)) if author_tag else None
                publisher = clean_value(publisher_tag.get_text(" ", strip=True)) if publisher_tag else None

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
        search_url = (
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

        res = requests.get(search_url, headers=kakao_headers, timeout=15)

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
            "author",
            "writer",
            "authors",
            "artist",
            "authorName",
            "writerName",
        ])

        if isinstance(author, list):
            author = ", ".join(
                str(a.get("name", a)) if isinstance(a, dict) else str(a)
                for a in author
            )

        publisher = deep_find(item, [
            "publisher",
            "publisherName",
            "cpName",
            "provider",
            "providerName",
            "company",
            "companyName",
            "copyright",
        ])

        content_id = deep_find(item, [
            "seriesId",
            "id",
            "productId",
            "contentId",
            "series_id",
            "content_id",
            "uid",
        ])

        if not publisher and content_id:
            detail_url = f"https://page.kakao.com/content/{content_id}"
            detail_soup = fetch_soup_playwright(detail_url, click_info_tab=True)

            publisher_span = detail_soup.find("span", string="발행자")
            if publisher_span:
                parent = publisher_span.find_parent("div")
                if parent:
                    spans = parent.find_all("span")
                    if len(spans) >= 2:
                        publisher = spans[1].get_text(" ", strip=True)

            if not publisher:
                detail_text = detail_soup.get_text(" ", strip=True)
                m = re.search(r"발행자\s+([^\s|·,]+)", detail_text)
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
