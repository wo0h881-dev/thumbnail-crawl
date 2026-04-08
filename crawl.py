import os, requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
PAGE_ID = os.environ["PAGE_ID"]
TITLE = os.environ["TITLE"]
PLATFORM = os.environ.get("PLATFORM", "naver")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

def crawl_naver(title):
    url = f"https://series.naver.com/search/search.series?t=all&fs=novel&q={requests.utils.quote(title)}"
    res = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")
    
    a = soup.select_one("a.pic[href*='productNo']")
    if not a:
        print("❌ 네이버 검색결과 없음")
        return None
    
    img = a.select_one("img")
    if img and img.get("src"):
        src = img["src"].replace("type=m79", "type=m260")
        print("✅ 네이버 이미지:", src[:60])
        return src
    return None

def crawl_ridi(title):
    url = f"https://ridibooks.com/search?q={requests.utils.quote(title)}&adult_exclude=n"
    res = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")
    
    img = soup.select_one("img[src*='img.ridicdn.net/cover']")
    if img and img.get("src"):
        print("✅ 리디 이미지:", img["src"][:60])
        return img["src"]
    return None

def crawl_kakao(title):
    url = f"https://page.kakao.com/search/result?keyword={requests.utils.quote(title)}&categoryUid=11"
    res = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")
    
    img = soup.select_one("img[src*='dn-img-page.kakao.com'], img[src*='page-images.kakaoentcdn.com']")
    if img and img.get("src"):
        src = img["src"].split("&")[0] + "&filename=o1/dims/resize/384"
        print("✅ 카카오 이미지:", src[:60])
        return src
    return None

def set_notion_cover(page_id, img_url):
    pid = page_id.replace("-", "")
    pid = f"{pid[:8]}-{pid[8:12]}-{pid[12:16]}-{pid[16:20]}-{pid[20:]}"
    
    res = requests.patch(
        f"https://api.notion.com/v1/pages/{pid}",
        headers={
            "Authorization": f"Bearer {NOTION_API_KEY}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        },
        json={"cover": {"type": "external", "external": {"url": img_url}}}
    )
    print("노션 업데이트:", res.status_code, res.text[:100])
    return res.ok

crawlers = {
    "naver": crawl_naver,
    "ridi": crawl_ridi,
    "kakao": crawl_kakao,
}

order = [PLATFORM] + [p for p in ["naver", "ridi", "kakao"] if p != PLATFORM]

img_url = None
for platform in order:
    print(f"🔍 {platform} 시도 중...")
    img_url = crawlers[platform](TITLE)
    if img_url:
        break

if img_url:
    set_notion_cover(PAGE_ID, img_url)
else:
    print("❌ 모든 플랫폼에서 이미지 찾기 실패")
