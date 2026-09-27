import re
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx

BASE = "https://cinesubz.co"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,si;q=0.8",
}

app = FastAPI(title="CineSubz Unofficial API", version="1.0")

# Frontend / App වලින් Access කරන්න CORS සක්‍රීය කිරීම
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = httpx.AsyncClient(
    headers=HEADERS, timeout=20, follow_redirects=True
)


def soup_of(html: str) -> BeautifulSoup:
  return BeautifulSoup(html, "lxml")


def parse_card(el) -> dict | None:
  a = el.select_one("a[href]")
  img = el.select_one("img")
  if not a or not a.get("href"):
    return None
  title = (el.select_one(".title, h2, h3") or a).get_text(strip=True)
  rating = el.select_one(".rating, .imdb, .imdbpraise, .metadata")
  return {
      "title": title,
      "url": a["href"],
      "poster": img.get("src") or img.get("data-src") if img else None,
      "rating": rating.get_text(strip=True) if rating else None,
  }


def parse_listing(html: str) -> list[dict]:
  s = soup_of(html)
  cards = s.select(
      ".item.movies, .item.tvshows, .results-item, article.item"
  )
  items = [c for c in (parse_card(x) for x in cards) if c]
  if not items:
    items = [
        c for c in (parse_card(x) for x in s.select("article, .post")) if c
    ]
  return items


def get_pagination(s: BeautifulSoup) -> dict:
  pages = s.select(".pagination a, .nav-links a, .page-numbers")
  nums = []
  for p in pages:
    m = re.search(r"/page/(\d+)", p.get("href", "") or "")
    if m:
      nums.append(int(m.group(1)))
  return {"last_page": max(nums) if nums else 1}


@app.get("/")
async def index():
  return {
      "endpoints": {
          "/home": "Home page latest items",
          "/movies?page=1": "All movies listing",
          "/tvshows?page=1": "TV shows listing",
          "/genre/{genre}?page=1": (
              "Genre listing (e.g. sinhala, hindi, korea)"
          ),
          "/search?query=harry": "Search",
          "/detail?url=...": "Single movie/tvshow detail page",
          "/download?url=...": "Subtitle/SRT download link extract",
      }
  }


@app.get("/home")
async def home():
  r = await client.get(BASE)
  r.raise_for_status()
  s = soup_of(r.text)
  items = parse_listing(r.text)
  extra = [
      c
      for c in (
          parse_card(x)
          for x in s.select(".featured, .slider, .top10 .item")
      )
      if c
  ]
  return {"results": items, "trending": extra}


@app.get("/movies")
async def movies(page: int = 1):
  url = f"{BASE}/movies/" if page == 1 else f"{BASE}/movies/page/{page}/"
  r = await client.get(url)
  r.raise_for_status()
  s = soup_of(r.text)
  return {
      "page": page,
      **get_pagination(s),
      "results": parse_listing(r.text),
  }


@app.get("/tvshows")
async def tvshows(page: int = 1):
  url = f"{BASE}/tvshows/" if page == 1 else f"{BASE}/tvshows/page/{page}/"
  r = await client.get(url)
  r.raise_for_status()
  s = soup_of(r.text)
  return {
      "page": page,
      **get_pagination(s),
      "results": parse_listing(r.text),
  }


@app.get("/genre/{genre}")
async def genre(genre: str, page: int = 1):
  url = (
      f"{BASE}/genre/{genre}/"
      if page == 1
      else f"{BASE}/genre/{genre}/page/{page}/"
  )
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

  title = s.select_one("h1") or s.select_one(".title")
  poster = s.select_one(
      ".poster img, .single-poster img, .dtinfo img, .imdbwp img"
  )
  desc = s.select_one(
      ".wp-content p, .description, .sinopsis, .wpb_wrapper p"
  )

  sub_links = []
  for a in s.select(
      "a[href*='.srt'], a[href*='download'], .dt-button a, .download-links"
      " a"
  ):
    if a.get("href"):
      sub_links.append({"text": a.get_text(strip=True), "url": a["href"]})

  episodes = [
      {"text": a.get_text(strip=True), "url": a["href"]}
      for a in s.select(
          ".episodios a, .seasons a, .episode a, a[href*='/episodes/']"
      )
      if a.get("href")
  ]

  return {
      "url": url,
      "title": title.get_text(strip=True) if title else None,
      "poster": (
          poster.get("src") or poster.get("data-src") if poster else None
      ),
      "description": desc.get_text(" ", strip=True) if desc else None,
      "subtitle_links": sub_links,
      "episodes": episodes,
  }


@app.get("/download")
async def download(url: str):
  if not url.startswith("http"):
    url = BASE + "/" + url.lstrip("/")
  r = await client.get(url)
  r.raise_for_status()
  ct = r.headers.get("content-type", "application/octet-stream")
  fname = url.split("/")[-1].split("?")[0] or "subtitle.srt"
  return StreamingResponse(
      iter([r.content]),
      media_type=ct,
      headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
