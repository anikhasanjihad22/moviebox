"""
MovieBox API — FastAPI
Deploy: uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

import mb_client as mb

app = FastAPI(
    title="MovieBox API",
    version="3.0.0",
    description="MovieBox proxy — search, detail, catalog, streams, subtitles",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def ok(data):
    return data


def fail(err):
    return {"error": str(err)}


# ---------------------------------------------------------------- health

@app.get("/health")
def health():
    return {"status": "ok", "curl_cffi": mb._HAS_CFFI}


@app.get("/debug/upstream")
def debug_upstream(q: str = "avatar"):
    return mb.probe(q)


# ---------------------------------------------------------------- discovery

@app.get("/home")
def get_home():
    try:
        return ok(mb.home())
    except Exception as e:
        return fail(e)


@app.get("/movies")
def get_movies(page: int = 1):
    try:
        return ok(mb.catalog("movie", page))
    except Exception as e:
        return fail(e)


@app.get("/tv-series")
def get_tv_series(page: int = 1):
    try:
        return ok(mb.catalog("series", page))
    except Exception as e:
        return fail(e)


# ---------------------------------------------------------------- search

@app.get("/search")
def get_search(q: str = Query(..., min_length=1), page: int = 1):
    try:
        return ok(mb.search(q, page))
    except Exception as e:
        return {"query": q, "page": page, "total": 0, "items": [], "error": str(e)}


@app.get("/search/suggest")
def get_suggest(q: str = Query(..., min_length=1)):
    try:
        return ok(mb.suggest(q))
    except Exception as e:
        return {"suggestions": [], "error": str(e)}


# ---------------------------------------------------------------- detail

@app.get("/detail/{subject_id}")
def get_detail(subject_id: str):
    try:
        return ok(mb.detail(subject_id))
    except Exception as e:
        return {"subject_id": subject_id, "error": str(e)}


@app.get("/episodes/{subject_id}")
def get_episodes(subject_id: str):
    try:
        return ok(mb.episodes(subject_id))
    except Exception as e:
        return {"subject_id": subject_id, "seasons": [], "error": str(e)}


# ---------------------------------------------------------------- stream

@app.get("/api/stream/{subject_id}")
def get_streams(subject_id: str,
                se: int = 0,
                ep: int = 0,
                quality: int = 1080,
                detail_path: str | None = None):
    try:
        return ok(mb.streams(subject_id, se, ep, quality))
    except Exception as e:
        return {"subject_id": subject_id, "count": 0, "streams": [], "error": str(e)}


@app.get("/api/stream/{subject_id}/captions")
def get_captions(subject_id: str,
                 se: int = 0,
                 ep: int = 0,
                 resource_id: str = ""):
    try:
        return ok(mb.captions(subject_id, se, ep, resource_id))
    except Exception as e:
        return {"subject_id": subject_id, "count": 0, "captions": [], "error": str(e)}


# ---------------------------------------------------------------- dashboard

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD


DASHBOARD = """<!doctype html>
<html><head><meta charset="utf-8"><title>MovieBox API</title>
<style>
body{background:#0d0d0f;color:#e8e8e8;font-family:system-ui;margin:0;padding:28px}
h1{color:#8A05FF;margin:0 0 4px;font-size:22px}
p.sub{color:#888;margin:0 0 20px;font-size:13px}
.row{display:flex;gap:8px;margin-bottom:18px}
input{flex:1;padding:11px 14px;border-radius:8px;border:1px solid #333;background:#17171a;color:#fff;font-size:14px}
button{padding:11px 20px;border-radius:8px;border:0;background:#8A05FF;color:#fff;font-weight:600;cursor:pointer}
button:hover{background:#9b2bff}
#res{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px}
.card{background:#17171a;border:1px solid #26262b;border-radius:10px;overflow:hidden;cursor:pointer}
.card img{width:100%;height:200px;object-fit:cover;display:block}
.card div{padding:8px;font-size:12px;line-height:1.4}
.meta{color:#888;font-size:11px;margin-top:3px}
pre{background:#141414;border:1px solid #26262b;border-radius:8px;padding:12px;overflow:auto;font-size:12px;max-height:340px}
video{width:100%;max-width:760px;border-radius:10px;background:#000;margin-top:14px}
</style></head><body>
<h1>MovieBox API</h1>
<p class="sub">v3.0.0 — search, detail, catalog, streams</p>
<div class="row">
  <input id="q" placeholder="মুভি বা সিরিজ লিখুন…" value="Avatar">
  <button onclick="go()">Search</button>
</div>
<div id="res"></div>
<pre id="log" style="display:none"></pre>
<video id="pl" controls style="display:none"></video>
<script>
async function go(){
  const q=document.getElementById('q').value.trim(); if(!q)return;
  document.getElementById('res').innerHTML='<p style="color:#888">খোঁজা হচ্ছে…</p>';
  const r=await fetch('/search?q='+encodeURIComponent(q));
  const d=await r.json();
  if(!d.items||!d.items.length){
    document.getElementById('log').style.display='block';
    document.getElementById('log').textContent=JSON.stringify(d,null,2);return;
  }
  document.getElementById('res').innerHTML=d.items.map(i=>`
    <div class="card" onclick="play('${i.subject_id}','${i.slug}')">
      <img src="${i.poster_url}" loading="lazy">
      <div>${i.name}<div class="meta">${i.year||''} ★ ${i.rating||''}</div></div>
    </div>`).join('');
}
async function play(id,slug){
  const se=slug?0:0;
  const r=await fetch(`/api/stream/${id}?se=${se}&ep=${se}`);
  const d=await r.json();
  document.getElementById('log').style.display='block';
  document.getElementById('log').textContent=JSON.stringify(d,null,2);
  if(d.streams&&d.streams.length){
    const v=document.getElementById('pl');
    const s=d.streams.find(x=>x.format==='MP4')||d.streams[0];
    v.style.display='block'; v.src=s.url; v.play();
  }
}
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter')go()});
go();
</script></body></html>"""