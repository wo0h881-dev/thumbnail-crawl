import sys
import os
import re
import time
import requests
import cloudscraper
from bs4 import BeautifulSoup

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
WORKER_URL     = os.environ.get("WORKER_URL", "")   # Cloudflare Worker URL (선택)

def search_naver(title: str) -> str | None:
    """네이버 시리즈 검색"""
    url = f"https://series.naver.com/search/search.series?query={requests.utils.quote(title)}&categoryTypeCode=novel"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        img = soup.select_one(".pic_area img, .thumbnail img, li.item img")
        if img:
            src = img.get("src") or img.get("data-src") or ""
            src = src.split("#")[0]
            if "type=m1" in src:
                src = src.replace("type=m1", "type=m140")
            if src and not src.endswith(".jpg"):
                src += "&.jpg" if "?" in src else "?.jpg"
            return src if src else None
    except Exception as e:
        print(f"[Naver] error: {e}")
    return None

def search_ridi(title: str) -> str | None:
    """리디 검색"""
    url = f"https://ridibooks.com/search?q={requests.utils.quote(title)}&adult_exclude=n"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        # 검색 결과 첫 번째 책 커버
        img = soup.select_one(".thumbnail_image img, .book_thumbnail img, img.lazy")
        if img:
            src = img.get("src") or img.get("data-src") or ""
            src = src.split("#")[0]
            if src.startswith("//"):
                src = "https:" + src
            return src if src else None
    except Exception as e:
        print(f"[Ridi] error: {e}")
    return None

def search_kakao(title: str) -> str | None:
    """카카오페이지 검색 (API)"""
    url = "https://page.kakao.com/graphql"
    payload = {
        "operationName": "SearchKeyword",
        "variables": {"keyword": title, "page": 1, "size": 1},
        "query": """
            query SearchKeyword($keyword: String!, $page: Int, $size: Int) {
              searchKeyword(keyword: $keyword, page: $page, size: $size) {
                list {
                  thumbnail
                  title
                }
              }
            }
        """
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://page.kakao.com/",
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        data = r.json()
        items = data.get("data", {}).get("searchKeyword", {}).get("list", [])
        if items and items[0].get("thumbnail"):
            return items[0]["thumbnail"]
    except Exception as e:
        print(f"[Kakao] error: {e}")
    return None

def set_notion_cover(page_id: str, img_url: str):
    """노션 페이지 상단 커버 직접 설정"""
    clean_id = page_id.replace("-", "")
    # 32자 → 표준 UUID 형식으로
    if len(clean_id) == 32:
        page_id = f"{clean_id[:8]}-{clean_id[8:12]}-{clean_id[12:16]}-{clean_id[16:20]}-{clean_id[20:]}"

    url = f"https://api.notion.com/v1/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    body = {
        "cover": {
            "type": "external",
            "external": {"url": img_url}
        }
    }
    r = requests.patch(url, json=body, headers=headers, timeout=10)
    if r.status_code == 200:
        print(f"✅ 노션 커버 설정 완료: {img_url}")
    else:
        print(f"❌ 노션 API 오류 {r.status_code}: {r.text}")

def main():
    if len(sys.argv) < 3:
        print("Usage: python crawl.py <title> <notion_page_id>")
        sys.exit(1)

    title   = sys.argv[1]
    page_id = sys.argv[2]

    print(f"🔍 검색 중: {title}")

    crawlers = [
        ("네이버 시리즈", search_naver),
        ("리디북스",      search_ridi),
        ("카카오페이지",  search_kakao),
    ]

    img_url = None
    for name, fn in crawlers:
        print(f"  [{name}] 시도 중...")
        img_url = fn(title)
        if img_url:
            print(f"  [{name}] 찾음: {img_url}")
            break
        time.sleep(1)

    if not img_url:
        print("❌ 모든 플랫폼에서 썸네일을 찾지 못했습니다.")
        sys.exit(1)

    set_notion_cover(page_id, img_url)

if __name__ == "__main__":
    main()
