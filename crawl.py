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


def get_novels_without_cover():
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
            if page.get("cover"):
                continue

            title_prop = page["properties"].get("제목") or page["properties"].get("이름")
            if not title_prop:
                continue
            title = "".join([t["plain_text"] for t in title_prop.get("title", [])])
            if not title:
                continue

            platform_prop = page["properties"].get("platform")
            platform = None
            if platform_prop:
                platform = platform_prop.get("select", {}).get("name", "").lower().strip()

            results.append({
                "id": page["id"],
                "title": title,
                "platform": platform or "naver"
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
            print(f"  ❌ 네이버 검색결과 없음")
            return None

        img = a.select_one("img")
        if img and img.get("src"):
            src = img["src"].replace("type=m79", "type=m260")
            print(f"  ✅ 네이버: {src[:60]}")
            return src
    except Exception as e:
        print(f"  ❌ 네이버 오류: {e}")
    return None


def crawl_ridi(title):
    try:
        url = f"https://ridibooks.com/search?q={requests.utils.quote(title)}&adult_exclude=n"
        res = requests.get(url, headers=HEADERS, timeout=10)
        
        # 정규식으로 이미지 URL 추출
        matches = re.findall(r'https://img\.ridicdn\.net/cover/[^\s"\'<>]+', res.text)
        if matches:
            img = matches[0].split('"')[0]
            print(f"  ✅ 리디: {img[:60]}")
            return img
        print(f"  ❌ 리디 검색결과 없음")
    except Exception as e:
        print(f"  ❌ 리디 오류: {e}")
    return None


def crawl_kakao(title):
    try:
        url = f"https://bff-page.kakao.com/api/gateway/api/v1/search/series?keyword={requests.utils.quote(title)}&category_uid=11&is_complete=false&sort_type=ACCURACY&page=0&size=25"
        res = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://page.kakao.com/",
            "Origin": "https://page.kakao.com",
        }, timeout=10)

        if not res.ok:
            print(f"  ❌ 카카오 API 응답 실패: {res.status_code}")
            return None

        data = res.json()
        items = data.get("result", {}).get("list", [])
        if not items:
            print(f"  ❌ 카카오 검색결과 없음")
            return None

        thumbnail_key = items[0].get("thumbnail")
        if not thumbnail_key:
            print(f"  ❌ 카카오 썸네일 키 없음")
            return None

        img = f"https://dn-img-page.kakao.com/download/resource?kid={thumbnail_key}&filename=th3"
        print(f"  ✅ 카카오: {img[:60]}")
        return img

    except Exception as e:
        print(f"  ❌ 카카오 오류: {e}")
    return None

def set_notion_cover(page_id, img_url):
    try:
        pid = page_id.replace("-", "")
        pid = f"{pid[:8]}-{pid[8:12]}-{pid[12:16]}-{pid[16:20]}-{pid[20:]}"

        res = requests.patch(
            f"https://api.notion.com/v1/pages/{pid}",
            headers=NOTION_HEADERS,
            json={"cover": {"type": "external", "external": {"url": img_url}}}
        )
        return res.ok
    except Exception as e:
        print(f"  ❌ 노션 업데이트 오류: {e}")
        return False


crawlers = {
    "naver": crawl_naver,
    "ridi": crawl_ridi,
    "kakao": crawl_kakao,
}

novels = get_novels_without_cover()
print(f"📚 커버 없는 웹소설: {len(novels)}개")

for novel in novels:
    print(f"\n🔍 [{novel['platform']}] {novel['title']}")

    order = [novel["platform"]] + [p for p in ["naver", "ridi", "kakao"] if p != novel["platform"]]

    img_url = None
    for platform in order:
        img_url = crawlers[platform](novel["title"])
        if img_url:
            break

    if img_url:
        ok = set_notion_cover(novel["id"], img_url)
        print(f"  {'✅ 노션 커버 업데이트 완료' if ok else '❌ 노션 업데이트 실패'}")
    else:
        print(f"  ❌ 모든 플랫폼에서 이미지 찾기 실패")

print("\n🎉 완료!")
