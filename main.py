import re
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

BASE = "https://cinesubz.co"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,si;q=0.8",
}

app = FastAPI(title="CineSubz Unofficial API", version="1.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True)

def soup_of(html): return BeautifulSoup(html, "lxml")

# ---------- NEW FALLBACK PARSER (class names නොබලා වැඩ කරනවා) ----------
def parse_listing(html: str) -> list[dict]:
    s = soup_of(html)
    items, seen = [], set()
    for h in s.find_all(["h2", "h3"]):
        a = h.find("a") or h.find_parent("a")
        if not a or not a.get("href"):
            continue
        url = a["href"]
        if "/movies/" not in url and "/tvshows/" not in url:
            continue
        if url in seen:
            continue
        seen.add(url)
        container = h.find_parent("div") or h.find_parent("article")
        img = container.select_one("img") if container else None
        rating = None
        if container:
            r_el = container.find(string=re.compile(r"★|\d\.\d"))
            if r_el:
                rating = r_el.strip()
        items.append({
            "title": h.get_text(strip=True),
            "url": url,
            "poster": (img.get("src") or img.get("data-src")) if img else None,
            "rating": rating,
        })
    return items

def get_pagination(s):
    nums = []
    for p in s.select("a[href]"):
        m = re.search(r"/page/(\d+)", p.get("href", "") or "")
        if m and int(m.group(1)) < 5000:
            nums.append(int(m.group(1)))
    return {"last_page": max(nums) if nums else 1}

# ---------- DEBUG ----------
@app.get("/debug")
async def debug(path: str = "/movies/"):
    r = await client.get(BASE + path)
    s = soup_of(r.text)
    classes = sorted({c for tag in s.find_all(True) for c in (tag.get("class") or [])})
    h2h3 = [h.get_text(strip=True)[:50] for h in s.find_all(["h2", "h3"])][:10]
    return {"status": r.status_code, "html_len": len(r.text),
            "classes": classes[:60], "headings": h2h3}

@app.get("/")
async def index():
    return {"endpoints": ["/home", "/movies?page=", "/tvshows?page=",
            "/genre/{g}?page=", "/search?query=", "/detail?url=", "/download?url=", "/debug"]}

@app.get("/home")
async def home():
    r = await client.get(BASE)
    r.raise_for_status()
    return {"results": parse_listing(r.text)}

@app.get("/movies")
async def movies(page: int = 1):
    url = f"{BASE}/movies/" if page == 1 else f"{BASE}/movies/page/{page}/"
    r = await client.get(url)
    r.raise_for_status()
    s = soup_of(r.text)
    return {"page": page, **get_pagination(s), "results": parse_listing(r.text)}

@app.get("/tvshows")
async def tvshows(page: int = 1):
    url = f"{BASE}/tvshows/" if page == 1 else f"{BASE}/tvshows/page/{page}/"
    r = await client.get(url)
    r.raise_for_status()
    s = soup_of(r.text)
    return {"page": page, **get_pagination(s), "results": parse_listing(r.text)}

@app.get("/genre/{genre}")
async def genre(genre: str, page: int = 1):
    url = f"{BASE}/genre/{genre}/" if page == 1 else f"{BASE}/genre/{genre}/page/{page}/"
    r = await client.get(url)
    if r.status_code == 404:
        raise HTTPException(404, f"Genre '{genre}' not found")
    r.raise_for_status()
    return {"page": page, "results": parse_listing(r.text)}

@app.get("/search")
async def search(query: str = Query(..., min_length=1)):
    r = await client.get(f"{BASE}/", params={"s": query})
    r.raise_for_status()
    return {"query": query, "results": parse_listing(r.text)}

@app.get("/detail")
async def detail(url: str):
    if not url.startswith("http"):
        url = BASE + "/" + url.lstrip("/")
    r = await client.get(url)
    r.raise_for_status()
    s = soup_of(r.text)
    title = s.select_one("h1")
    poster = s.select_one(".poster img, .single-poster img, .dtinfo img, .imdbwp img") or s.select_one("img")
    desc = s.select_one(".wp-content p, .description, .sinopsis") 
    if not desc:
        for p in s.find_all("p"):
            if len(p.get_text(strip=True)) > 60:
                desc = p
                break
    sub_links = [{"text": a.get_text(strip=True), "url": a["href"]}
                 for a in s.select("a[href]") 
                 if a.get("href") and (".srt" in a["href"] or "download" in a["href"].lower())]
    return {
        "url": url,
        "title": title.get_text(strip=True) if title else None,
        "poster": (poster.get("src") or poster.get("data-src")) if poster else None,
        "description": desc.get_text(" ", strip=True) if desc else None,
        "subtitle_links": sub_links,
    }

@app.get("/download")
async def download(url: str):
    if not url.startswith("http"):
        url = BASE + "/" + url.lstrip("/")
    r = await client.get(url)
    r.raise_for_status()
    ct = r.headers.get("content-type", "application/octet-stream")
    fname = url.split("/")[-1].split("?")[0] or "subtitle.srt"
    return StreamingResponse(iter([r.content]), media_type=ct,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
