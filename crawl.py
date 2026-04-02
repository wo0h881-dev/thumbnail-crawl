import sys
import os
import re
import time
import requests
from typing import Optional, List, Dict, Any

NOTION_API_KEY = os.environ["NOTION_API_KEY"]

NOTION_VERSION = "2022-06-28"


def ensure_absolute_url(src: str | None) -> str | None:
    if not src:
        return None
    if src.startswith("//"):
        return "https:" + src
    return src


def uniq(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        item = (item or "").strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_search_candidates(title: str) -> List[str]:
    raw = (title or "").strip()
    if not raw:
        return []

    candidates = [raw]

    no_bracket = re.sub(r"\[[^\]]*\]|\([^)]+\)|\{[^}]+\}", " ", raw)
    no_bracket = re.sub(r"\s+", " ", no_bracket).strip()
    candidates.append(no_bracket)

    cleaned = re.sub(r"[^\w\s가-힣]", " ", no_bracket, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    candidates.append(cleaned)

    words = cleaned.split()
    if len(words) >= 2:
        candidates.append(" ".join(words[:2]))
    if len(words) >= 3:
        candidates.append(" ".join(words[:3]))
    if len(words) >= 4:
        candidates.append(" ".join(words[:4]))

    if len(cleaned) > 10:
        candidates.append(cleaned[:10].strip())
    if len(cleaned) > 8:
        candidates.append(cleaned[:8].strip())
    if len(cleaned) > 6:
        candidates.append(cleaned[:6].strip())
    if len(cleaned) > 4:
        candidates.append(cleaned[:4].strip())

    return uniq(candidates)


def normalize_notion_page_id(page_id: str) -> str:
    clean = page_id.replace("-", "").strip()
    if len(clean) == 32:
        return f"{clean[:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:]}"
    return page_id


def safe_json_response(resp: requests.Response) -> Dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def search_naver(title: str) -> Optional[str]:
    url = (
        "https://series.naver.com/search/search.series"
        f"?query={requests.utils.quote(title)}&categoryTypeCode=novel"
    )
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, headers=headers, timeout=10)
        html = r.text

        matches = re.findall(r'<img[^>]+src="([^"]+)"', html, re.I)

        src = None
        for m in matches:
            if re.search(r"comic|novel|book|cover|thumbnail|thumb", m, re.I):
                src = m
                break

        if not src:
            for m in matches:
                if re.search(r"type=m140|type=m1", m, re.I):
                    src = m
                    break

        if not src:
            return None

        src = src.split("#")[0]
        src = ensure_absolute_url(src)

        if src and "type=m1" in src:
            src = src.replace("type=m1", "type=m140")

        if src and not re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", src, re.I):
            src += "&.jpg" if "?" in src else "?.jpg"

        return src
    except Exception as e:
        print(f"[Naver] error: {e}")
        return None


def search_ridi(title: str) -> Optional[str]:
    url = f"https://ridibooks.com/search?q={requests.utils.quote(title)}&adult_exclude=n"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, headers=headers, timeout=10)
        html = r.text

        matches = re.findall(r'(?:src|data-src)="([^"]+)"', html, re.I)

        src = None
        for m in matches:
            if re.search(r"img\.ridicdn\.net", m, re.I):
                src = m
                break

        if not src:
            for m in matches:
                if re.search(r"thumbnail|cover", m, re.I):
                    src = m
                    break

        if not src:
            return None

        src = ensure_absolute_url(src)
        return src
    except Exception as e:
        print(f"[Ridi] error: {e}")
        return None


def search_kakao(title: str) -> Optional[str]:
    url = "https://page.kakao.com/graphql"
    payload = {
        "operationName": "SearchKeyword",
        "variables": {
            "keyword": title,
            "page": 1,
            "size": 5,
        },
        "query": """
            query SearchKeyword($keyword: String!, $page: Int, $size: Int) {
              searchKeyword(keyword: $keyword, page: $page, size: $size) {
                list {
                  thumbnail
                  title
                }
              }
            }
        """,
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

        if not items:
            return None

        normalized_title = re.sub(r"\s+", "", title)

        exact = None
        for item in items:
            item_title = re.sub(r"\s+", "", item.get("title", ""))
            if item_title == normalized_title:
                exact = item
                break

        target = exact or items[0]
        thumb = target.get("thumbnail")
        return ensure_absolute_url(thumb) if thumb else None
    except Exception as e:
        print(f"[Kakao] error: {e}")
        return None


def set_notion_cover(page_id: str, img_url: str):
    page_id = normalize_notion_page_id(page_id)

    url = f"https://api.notion.com/v1/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }
    body = {
        "cover": {
            "type": "external",
            "external": {"url": img_url},
        }
    }

    r = requests.patch(url, json=body, headers=headers, timeout=15)

    if r.status_code == 200:
        print(f"✅ 노션 커버 설정 완료: {img_url}")
    else:
        print(f"❌ 노션 API 오류 {r.status_code}")
        print(safe_json_response(r))
        sys.exit(1)


def find_thumbnail(title: str) -> tuple[Optional[str], List[Dict[str, Any]], Optional[str], Optional[str]]:
    candidates = build_search_candidates(title)
    debug: List[Dict[str, Any]] = []

    crawlers = [
        ("네이버 시리즈", "naver", search_naver),
        ("카카오페이지", "kakao", search_kakao),
        ("리디북스", "ridi", search_ridi),
    ]

    for keyword in candidates:
        print(f"\n🔎 검색어 후보: {keyword}")

        for platform_name, platform_key, fn in crawlers:
            print(f"  [{platform_name}] 시도 중...")
            img_url = fn(keyword)

            debug.append(
                {
                    "platform": platform_key,
                    "keyword": keyword,
                    "found": bool(img_url),
                    "url": img_url,
                }
            )

            if img_url:
                print(f"  ✅ [{platform_name}] 찾음: {img_url}")
                return img_url, debug, platform_key, keyword

            print(f"  ❌ [{platform_name}] 못 찾음")
            time.sleep(0.5)

    return None, debug, None, None


def main():
    if len(sys.argv) < 3:
        print("Usage: python crawl.py <title> <notion_page_id>")
        sys.exit(1)

    title = sys.argv[1].strip()
    page_id = sys.argv[2].strip()

    if not title:
        print("❌ title이 비어 있습니다.")
        sys.exit(1)

    if not page_id:
        print("❌ notion_page_id가 비어 있습니다.")
        sys.exit(1)

    print(f"📘 원본 제목: {title}")

    img_url, debug, matched_platform, matched_keyword = find_thumbnail(title)

    if not img_url:
        print("\n❌ 모든 플랫폼에서 썸네일을 찾지 못했습니다.")
        print("🪵 debug:")
        for row in debug:
            print(row)
        sys.exit(1)

    print("\n🎯 최종 선택")
    print(f"   플랫폼: {matched_platform}")
    print(f"   검색어: {matched_keyword}")
    print(f"   이미지: {img_url}")

    set_notion_cover(page_id, img_url)


if __name__ == "__main__":
    main()
