"""Web upload portal for large audio files (meeting recordings)."""

import asyncio
import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from d_brain.config import get_settings
from d_brain.services.corrections import CorrectionsService
from d_brain.services.session import SessionStore
from d_brain.services.storage import VaultStorage
from d_brain.services.transcription import (
    DeepgramTranscriber,
    build_confidence_note,
    format_diarized,
    identify_user_speaker,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="d-brain", docs_url=None, redoc_url=None)

# ── SVG Icons ─────────────────────────────────────────────────────────────────

_SVG_OK = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" fill="none" viewBox="0 0 24 24" stroke="#0D9488" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75L11.25 15 15 9.75"/></svg>'
_SVG_ERR = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" fill="none" viewBox="0 0 24 24" stroke="#ef4444" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 9l-6 6M9 9l6 6"/></svg>'

# ── Shared CSS ────────────────────────────────────────────────────────────────

_BASE_CSS = """
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Lora:wght@400;500;600;700&display=swap');
  :root {
    --bg: #F0FDF9; --surface: #FFFFFF; --surface-2: #E6FAF5;
    --primary: #0D9488; --primary-light: #5EEAD4; --accent: #F59E0B;
    --text: #134E4A; --text-muted: #6B7280; --border: #CCFBEF;
    --shadow: 0 4px 20px rgba(0,0,0,0.08); --radius: 16px;
  }
  [data-theme="dark"] {
    --bg: #0B1A17; --surface: #0F2924; --surface-2: #163028;
    --primary: #2DD4BF; --primary-light: #5EEAD4; --accent: #FCD34D;
    --text: #ECFDF5; --text-muted: #9CA3AF; --border: #1A3A30;
    --shadow: 0 4px 20px rgba(0,0,0,0.3);
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', -apple-system, sans-serif;
    background: var(--bg); color: var(--text);
    min-height: 100vh; transition: background 0.3s, color 0.3s;
  }
  h1, h2, h3 { font-family: 'Lora', Georgia, serif; }
  .card {
    background: var(--surface); border-radius: var(--radius);
    border: 1px solid var(--border); box-shadow: var(--shadow);
    transition: background 0.3s, border-color 0.3s;
  }
  .btn {
    padding: 10px 20px; border-radius: 10px; border: none;
    font-size: 14px; font-weight: 500; cursor: pointer;
    transition: all 200ms ease; font-family: 'Inter', sans-serif;
  }
  .btn-primary {
    background: var(--primary); color: #fff;
  }
  .btn-primary:hover { opacity: 0.9; transform: translateY(-1px); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-secondary {
    background: var(--surface-2); color: var(--text);
    border: 1px solid var(--border);
  }
  .btn-secondary:hover { border-color: var(--primary); color: var(--primary); }
  .theme-toggle {
    position: fixed; top: 16px; right: 16px; z-index: 100;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 50%; width: 44px; height: 44px;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer; box-shadow: var(--shadow);
    transition: all 200ms ease;
  }
  .theme-toggle:hover { transform: scale(1.1); }
  .hero {
    width: 100%; height: 260px; position: relative;
    background: var(--primary) no-repeat center/cover;
    display: flex; flex-direction: column;
    align-items: center; justify-content: flex-end;
    padding-bottom: 32px; overflow: hidden;
  }
  .hero-overlay {
    position: absolute; inset: 0;
    background: linear-gradient(to bottom, rgba(0,0,0,0.15), rgba(0,0,0,0.55));
    backdrop-filter: blur(1px);
  }
  .hero-content { position: relative; z-index: 1; text-align: center; padding: 0 20px; }
  .hero-quote {
    font-family: 'Lora', serif; font-size: 17px; color: rgba(255,255,255,0.95);
    font-style: italic; line-height: 1.5; max-width: 600px;
    transition: opacity 0.6s ease;
  }
  .hero-author {
    font-size: 12px; color: rgba(255,255,255,0.65);
    margin-top: 6px; letter-spacing: 0.5px;
  }
"""

_NAV_CSS = """
  .nav {
    display: flex; align-items: center; gap: 4px;
    max-width: 900px; margin: 0 auto 24px;
    background: var(--surface); border-radius: 12px;
    padding: 6px; border: 1px solid var(--border);
    box-shadow: var(--shadow);
  }
  .nav-brand {
    font-family: 'Lora', serif; font-size: 15px; font-weight: 600;
    padding: 8px 12px; color: var(--primary); flex-shrink: 0;
  }
  .nav a {
    padding: 8px 16px; border-radius: 8px; color: var(--text-muted);
    text-decoration: none; font-size: 14px; transition: all 0.15s;
  }
  .nav a:hover { background: var(--surface-2); color: var(--text); }
  .nav a.active { background: var(--primary); color: #fff; }
"""

# ── Shared nav ────────────────────────────────────────────────────────────────

_NAV = {
    "upload": '<a href="/">Загрузка</a>',
    "improves": '<a href="/improves">Улучшения</a>',
}


def _nav_html(active: str) -> str:
    links = []
    for key, html in _NAV.items():
        if key == active:
            links.append(html.replace('<a href', '<a class="active" href'))
        else:
            links.append(html)
    return (
        '<nav class="nav"><span class="nav-brand">d-brain</span>'
        + "".join(links)
        + "</nav>"
    )


# ── Theme toggle + hero HTML ──────────────────────────────────────────────────

_THEME_BTN = """
<button class="theme-toggle" id="themeBtn" onclick="toggleTheme()" title="Сменить тему">
  <svg id="sunIcon" xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="none"
       viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
    <circle cx="12" cy="12" r="4"/>
    <path stroke-linecap="round" d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42
          M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
  </svg>
  <svg id="moonIcon" xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="none"
       viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" style="display:none">
    <path stroke-linecap="round" stroke-linejoin="round"
          d="M21 12.79A9 9 0 1111.21 3a7 7 0 109.79 9.79z"/>
  </svg>
</button>
"""

_HERO_JS = """
const HERO_IMAGES = [
  "https://images.unsplash.com/photo-1506905489-04fe67f91614?w=1920&auto=format&q=80",
  "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=1920&auto=format&q=80",
  "https://images.unsplash.com/photo-1501854140801-50d01698950b?w=1920&auto=format&q=80",
  "https://images.unsplash.com/photo-1447752875215-b2761acb3c5d?w=1920&auto=format&q=80",
  "https://images.unsplash.com/photo-1465146344425-f00d5f5c8f07?w=1920&auto=format&q=80",
  "https://images.unsplash.com/photo-1490730141103-6cac27aaab94?w=1920&auto=format&q=80",
  "https://images.unsplash.com/photo-1470071459604-3b5ec3a7fe05?w=1920&auto=format&q=80",
  "https://images.unsplash.com/photo-1518173946687-a4c8892bbd9f?w=1920&auto=format&q=80",
];
const QUOTES = [
  {text: "Тело — это сад, а воля — его садовник.", author: "Уильям Шекспир"},
  {text: "Здоровье — это не всё, но всё без здоровья — ничто.", author: "Артур Шопенгауэр"},
  {text: "Take care of your body. It's the only place you have to live.", author: "Jim Rohn"},
  {text: "Каждое утро — это шанс начать лучше.", author: ""},
  {text: "The mind is everything. What you think, you become.", author: "Buddha"},
  {text: "Движение — это жизнь. Жизнь — это движение.", author: ""},
  {text: "In every walk with nature, one receives far more than he seeks.", author: "John Muir"},
  {text: "Солнечный свет — лучший антидепрессант.", author: ""},
  {text: "You are what you eat. So eat something amazing.", author: ""},
  {text: "Almost everything will work again if you unplug it for a few minutes.", author: "Anne Lamott"},
  {text: "Мозг — это мышца. Тренируй его каждый день.", author: ""},
  {text: "Your future self is watching you right now through your memories.", author: ""},
  {text: "Тишина — это голос мудрости.", author: ""},
  {text: "Хорошее питание — лучшее лекарство.", author: ""},
  {text: "Позаботься о минутах — часы позаботятся о себе сами.", author: ""},
];

let quoteIdx = Math.floor(Math.random() * QUOTES.length);
function initHero(heroId) {
  const hero = document.getElementById(heroId);
  if (hero) {
    const img = HERO_IMAGES[Math.floor(Math.random() * HERO_IMAGES.length)];
    hero.style.backgroundImage = "url('" + img + "')";
  }
  renderQuote();
  setInterval(cycleQuote, 12000);
}
function renderQuote() {
  const q = QUOTES[quoteIdx];
  const el = document.getElementById('heroQuote');
  const au = document.getElementById('heroAuthor');
  if (el) { el.style.opacity = '0'; setTimeout(function(){ el.textContent = q.text; el.style.opacity = '1'; }, 300); }
  if (au) au.textContent = q.author ? '— ' + q.author : '';
}
function cycleQuote() {
  quoteIdx = (quoteIdx + 1) % QUOTES.length;
  renderQuote();
}

function initTheme() {
  const saved = localStorage.getItem('d-theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  document.getElementById('sunIcon') && (document.getElementById('sunIcon').style.display = saved === 'dark' ? 'none' : 'block');
  document.getElementById('moonIcon') && (document.getElementById('moonIcon').style.display = saved === 'dark' ? 'block' : 'none');
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme') || 'light';
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('d-theme', next);
  document.getElementById('sunIcon').style.display = next === 'dark' ? 'none' : 'block';
  document.getElementById('moonIcon').style.display = next === 'dark' ? 'block' : 'none';
}
"""

# ── Upload page ───────────────────────────────────────────────────────────────

_UPLOAD_HTML = (
    """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>d-brain — Загрузка</title>
  <style>
"""
    + _BASE_CSS
    + _NAV_CSS
    + """
    .page-body { padding: 20px; }
    .wrap { display: flex; align-items: center; justify-content: center; padding-top: 32px; }
    .upload-card { padding: 32px; max-width: 440px; width: 100%; }
    .card-title { font-size: 22px; margin-bottom: 6px; color: var(--text); }
    .card-sub { font-size: 13px; color: var(--text-muted); margin-bottom: 28px; }
    .field { margin-bottom: 18px; }
    .lbl { display: block; font-size: 12px; font-weight: 500;
           color: var(--text-muted); margin-bottom: 7px; text-transform: uppercase;
           letter-spacing: 0.5px; }
    .file-area {
      display: block; width: 100%; padding: 20px;
      background: var(--surface-2); border: 2px dashed var(--border);
      border-radius: 12px; color: var(--text-muted); font-size: 14px;
      text-align: center; cursor: pointer; transition: all 0.2s;
    }
    .file-area:hover { border-color: var(--primary); color: var(--primary); }
    input[type=file] { display: none; }
    .file-name { font-size: 12px; color: var(--primary); margin-top: 7px; min-height: 16px; }
    .tog {
      display: flex; align-items: flex-start; gap: 12px; padding: 14px;
      background: var(--surface-2); border-radius: 12px; cursor: pointer;
      border: 1px solid var(--border);
    }
    .tog input { width: 18px; height: 18px; accent-color: var(--primary);
                  cursor: pointer; margin-top: 2px; flex-shrink: 0; }
    .tl { font-size: 14px; font-weight: 500; color: var(--text); }
    .td { font-size: 12px; color: var(--text-muted); margin-top: 3px; }
    .submit-btn {
      width: 100%; padding: 16px; font-size: 16px;
      border-radius: 12px; margin-top: 10px;
      display: flex; align-items: center; justify-content: center; gap: 8px;
    }
    .fmt { font-size: 11px; color: var(--text-muted); text-align: center; margin-top: 12px; }
    .mic-icon { color: var(--primary); }
  </style>
</head>
<body>
<script>
  (function(){ var t=localStorage.getItem('d-theme')||'light'; document.documentElement.setAttribute('data-theme',t); })();
</script>
"""
    + _THEME_BTN
    + """
<div id="hero" class="hero">
  <div class="hero-overlay"></div>
  <div class="hero-content">
    <p class="hero-quote" id="heroQuote"></p>
    <p class="hero-author" id="heroAuthor"></p>
  </div>
</div>

<div class="page-body">
"""
    + _nav_html("upload")
    + """
<div class="wrap">
<div class="card upload-card">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
    <svg class="mic-icon" xmlns="http://www.w3.org/2000/svg" width="28" height="28" fill="none"
         viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
      <path stroke-linecap="round" stroke-linejoin="round"
            d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5
               M12 3a3 3 0 013 3v6a3 3 0 01-6 0V6a3 3 0 013-3z"/>
    </svg>
    <h1 class="card-title">Загрузка записи</h1>
  </div>
  <p class="card-sub">Загрузи запись встречи — транскрипция придёт в Telegram</p>
  <form method="post" enctype="multipart/form-data" id="frm">
    <div class="field">
      <label class="lbl">Файл записи</label>
      <label class="file-area" for="f">
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none"
             viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"
             style="display:block;margin:0 auto 8px">
          <path stroke-linecap="round" stroke-linejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5
                   m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/>
        </svg>
        Выбрать файл
      </label>
      <input type="file" id="f" name="file" accept="audio/*,video/mp4"
             onchange="document.getElementById('fn').textContent=this.files[0]?.name||''">
      <div class="file-name" id="fn"></div>
    </div>
    <div class="field">
      <label class="tog">
        <input type="checkbox" name="diarize" value="1" checked>
        <div>
          <div class="tl">Разделить по голосам</div>
          <div class="td">Для встреч с несколькими участниками</div>
        </div>
      </label>
    </div>
    <button type="submit" id="btn" class="btn btn-primary submit-btn">
      <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" fill="none"
           viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0
              0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5"/>
      </svg>
      Отправить
    </button>
    <p class="fmt">m4a &middot; mp3 &middot; ogg &middot; wav &middot; opus &middot; flac &middot; mp4</p>
  </form>
</div>
</div>
</div>

<script>
"""
    + _HERO_JS
    + """
  initTheme();
  initHero('hero');

  document.getElementById('frm').onsubmit = function() {
    if (!document.getElementById('f').files[0]) {
      alert('Выбери файл'); return false;
    }
    var btn = document.getElementById('btn');
    btn.disabled = true;
    btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" style="animation:spin 1s linear infinite"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg> Обрабатывается\u2026';
  };
</script>
<style>
  @keyframes spin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
</style>
</body></html>"""
)

# ── Result page ───────────────────────────────────────────────────────────────

_RESULT_TMPL = (
    """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>d-brain &middot; [[TITLE]]</title>
  <style>
"""
    + _BASE_CSS
    + """
    .page {
      min-height: 100vh; display: flex; flex-direction: column;
      align-items: center; justify-content: center; padding: 20px;
    }
    .result-card {
      padding: 40px 32px; max-width: 420px; width: 100%;
      text-align: center;
    }
    .result-icon { margin-bottom: 20px; }
    .result-title { font-size: 22px; margin-bottom: 10px; color: var(--text); }
    .result-msg { font-size: 14px; color: var(--text-muted); margin-bottom: 28px; line-height: 1.6; }
    .back-link {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 12px 24px; border-radius: 12px;
      background: var(--surface-2); color: var(--primary);
      text-decoration: none; font-size: 14px; font-weight: 500;
      border: 1px solid var(--border); transition: all 0.2s;
    }
    .back-link:hover { background: var(--primary); color: #fff; border-color: var(--primary); }
  </style>
</head>
<body>
<script>
  (function(){ var t=localStorage.getItem('d-theme')||'light'; document.documentElement.setAttribute('data-theme',t); })();
</script>
"""
    + _THEME_BTN
    + """
<div class="page">
  <div class="card result-card">
    <div class="result-icon">[[ICON]]</div>
    <h2 class="result-title">[[TITLE]]</h2>
    <p class="result-msg">[[MESSAGE]]</p>
    <a class="back-link" href="/">
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none"
           viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18"/>
      </svg>
      Загрузить ещё
    </a>
  </div>
</div>
<script>initTheme();</script>
</body></html>"""
)


def _result(icon_svg: str, title: str, message: str) -> HTMLResponse:
    html = _RESULT_TMPL.replace("[[ICON]]", icon_svg).replace("[[TITLE]]", title).replace("[[MESSAGE]]", message)
    return HTMLResponse(html)


# ── Improves page ─────────────────────────────────────────────────────────────

_IMPROVES_HTML = (
    """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>d-brain &middot; Улучшения</title>
  <style>
"""
    + _BASE_CSS
    + _NAV_CSS
    + """
    .page-body { padding: 20px; }
    .filters { display: flex; gap: 6px; max-width: 900px; margin: 0 auto 20px; flex-wrap: wrap; }
    .filter-btn {
      padding: 6px 14px; border-radius: 8px; border: 1px solid var(--border);
      background: var(--surface); color: var(--text-muted); cursor: pointer;
      font-size: 13px; transition: all 0.15s; font-family: inherit;
    }
    .filter-btn.active { border-color: var(--primary); color: var(--primary); background: var(--surface-2); }
    .filter-btn:hover { border-color: var(--primary-light); color: var(--text); }

    .cards { max-width: 900px; margin: 0 auto; display: flex; flex-direction: column; gap: 8px; }
    .imp-card {
      background: var(--surface); border-radius: 12px; overflow: hidden;
      border: 1px solid var(--border); transition: border-color 0.15s;
    }
    .imp-card:hover { border-color: var(--primary-light); }
    .imp-card.expanded { border-color: var(--primary); }

    .card-head { display: flex; align-items: center; gap: 12px; padding: 14px 16px; cursor: pointer; }
    .badge {
      padding: 3px 9px; border-radius: 6px; font-size: 11px; font-weight: 600;
      flex-shrink: 0; white-space: nowrap;
    }
    .badge-pending { background: #3a2f00; color: #fbbf24; }
    .badge-accepted { background: #0a2540; color: #60a5fa; }
    .badge-later { background: #1e1060; color: #a78bfa; }
    .badge-done { background: #052e16; color: #4ade80; }
    .badge-rejected { background: var(--surface-2); color: var(--text-muted); }

    .cat-icon { font-size: 15px; flex-shrink: 0; }
    .card-title {
      flex: 1; font-size: 14px; font-weight: 500; min-width: 0;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text);
    }
    .card-date { font-size: 11px; color: var(--text-muted); flex-shrink: 0; }

    .card-body { display: none; padding: 0 16px 16px; border-top: 1px solid var(--border); }
    .imp-card.expanded .card-body { display: block; }
    .card-desc {
      font-size: 13px; color: var(--text-muted); margin-top: 12px;
      line-height: 1.6; white-space: pre-wrap;
    }
    .card-concept { font-size: 11px; color: var(--text-muted); margin-top: 8px; font-family: monospace; }
    .card-actions { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }

    .action-btn {
      padding: 8px 16px; border-radius: 8px; border: none; font-size: 13px;
      font-weight: 500; cursor: pointer; transition: all 0.15s; font-family: inherit;
    }
    .action-btn-primary { background: var(--primary); color: #fff; }
    .action-btn-primary:hover { opacity: 0.9; }
    .action-btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }
    .action-btn-secondary {
      background: var(--surface-2); color: var(--text-muted);
      border: 1px solid var(--border);
    }
    .action-btn-secondary:hover { border-color: var(--primary); color: var(--primary); }
    .action-btn-secondary:disabled { opacity: 0.4; cursor: not-allowed; }

    .result-msg {
      font-size: 12px; margin-top: 10px; padding: 8px 12px;
      border-radius: 8px; display: none; line-height: 1.5;
    }
    .result-ok { background: #052e16; color: #4ade80; }
    .result-err { background: #450a0a; color: #f87171; }
    .empty { text-align: center; color: var(--text-muted); padding: 60px 20px; font-size: 15px; }
    .loading { text-align: center; color: var(--text-muted); padding: 60px 20px; font-size: 15px; }
  </style>
</head>
<body>
<script>
  (function(){ var t=localStorage.getItem('d-theme')||'light'; document.documentElement.setAttribute('data-theme',t); })();
</script>
"""
    + _THEME_BTN
    + """
<div class="page-body">
"""
    + _nav_html("improves")
    + """
<div class="filters" id="filters">
  <button class="filter-btn active" onclick="setFilter('all')">Все</button>
  <button class="filter-btn" onclick="setFilter('pending')">Ожидают</button>
  <button class="filter-btn" onclick="setFilter('accepted')">Принято</button>
  <button class="filter-btn" onclick="setFilter('later')">Позже</button>
  <button class="filter-btn" onclick="setFilter('done')">Готово</button>
  <button class="filter-btn" onclick="setFilter('rejected')">Пропущено</button>
</div>
<div class="cards" id="cards"><div class="loading">Загрузка...</div></div>
</div>

<script>
var allItems = [];
var currentFilter = 'all';

var BADGE = {
  pending:  ['badge-pending',  'Ожидает'],
  accepted: ['badge-accepted', 'Принято'],
  later:    ['badge-later',    'Позже'],
  done:     ['badge-done',     'Готово'],
  rejected: ['badge-rejected', 'Пропущено']
};
var CAT_ICON = {issue:'⚠️', pattern:'🔄', error:'🔴', idea:'💡'};
var FILTER_KEYS = ['all','pending','accepted','later','done','rejected'];

function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function setFilter(status) {
  currentFilter = status;
  document.querySelectorAll('.filter-btn').forEach(function(b, i) {
    b.classList.toggle('active', FILTER_KEYS[i] === status);
  });
  render();
}

function render() {
  var container = document.getElementById('cards');
  var items = currentFilter === 'all'
    ? allItems
    : allItems.filter(function(i){ return i.status === currentFilter; });
  if (!items.length) {
    container.innerHTML = '<div class="empty">Нет записей в этой категории</div>';
    return;
  }
  container.innerHTML = items.map(function(item) {
    var badge = BADGE[item.status] || ['badge-rejected', item.status];
    var catIcon = CAT_ICON[item.category] || '💡';
    var canApply = item.auto_implementable && item.concept_file && item.status === 'later';
    var conceptRef = item.concept_file ? 'claude ' + item.concept_file : '';
    var statusBtns = '';
    if (item.status === 'pending') {
      statusBtns = [
        '<button class="action-btn action-btn-secondary" onclick="setStatus(event,\''+item.id+'\',\'accepted\')">Принять</button>',
        '<button class="action-btn action-btn-secondary" onclick="setStatus(event,\''+item.id+'\',\'later\')">Позже</button>',
        '<button class="action-btn action-btn-secondary" onclick="setStatus(event,\''+item.id+'\',\'rejected\')">Пропустить</button>'
      ].join('');
    }
    return '<div class="imp-card" id="card-'+item.id+'">'
      + '<div class="card-head" onclick="toggleCard(\''+item.id+'\')">'
      + '<span class="badge '+badge[0]+'">'+badge[1]+'</span>'
      + '<span class="cat-icon">'+catIcon+'</span>'
      + '<span class="card-title">'+esc(item.title)+'</span>'
      + '<span class="card-date">'+esc(item.date)+'</span>'
      + '</div>'
      + '<div class="card-body">'
      + (item.brief_desc ? '<div class="card-desc">'+esc(item.brief_desc)+'</div>' : '')
      + (item.concept_file ? '<div class="card-concept">'+esc(item.concept_file)+'</div>' : '')
      + '<div class="card-actions">'
      + (canApply ? '<button class="action-btn action-btn-primary" onclick="applyImprove(event,\''+item.id+'\')">Применить</button>' : '')
      + (conceptRef ? '<button class="action-btn action-btn-secondary" onclick="copyCmd(event,\''+esc(conceptRef)+'\')">Скопировать команду</button>' : '')
      + statusBtns
      + '</div>'
      + '<div class="result-msg" id="msg-'+item.id+'"></div>'
      + '</div></div>';
  }).join('');
}

function toggleCard(id) {
  document.getElementById('card-' + id).classList.toggle('expanded');
}

function applyImprove(e, id) {
  e.stopPropagation();
  var btn = e.target;
  btn.disabled = true;
  btn.textContent = 'Запускаю...';
  var msg = document.getElementById('msg-' + id);
  fetch('/api/improves/' + id + '/apply', {method: 'POST'})
    .then(function(r){ return r.json(); })
    .then(function(data) {
      msg.style.display = 'block';
      if (data.ok) {
        msg.className = 'result-msg result-ok';
        msg.textContent = data.result;
        btn.textContent = 'Готово';
        var item = allItems.find(function(i){ return i.id === id; });
        if (item) item.status = 'done';
      } else {
        msg.className = 'result-msg result-err';
        msg.textContent = data.error || 'Ошибка';
        btn.disabled = false;
        btn.textContent = 'Применить';
      }
    })
    .catch(function() {
      msg.style.display = 'block';
      msg.className = 'result-msg result-err';
      msg.textContent = 'Ошибка соединения';
      btn.disabled = false;
      btn.textContent = 'Применить';
    });
}

function setStatus(e, id, status) {
  e.stopPropagation();
  var btn = e.target;
  btn.disabled = true;
  fetch('/api/improves/' + id + '/status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: status})
  })
    .then(function(r){ return r.json(); })
    .then(function(data) {
      if (data.ok) {
        var item = allItems.find(function(i){ return i.id === id; });
        if (item) item.status = status;
        render();
      } else {
        btn.disabled = false;
      }
    })
    .catch(function(){ btn.disabled = false; });
}

function copyCmd(e, cmd) {
  e.stopPropagation();
  var btn = e.target;
  navigator.clipboard.writeText(cmd).then(function() {
    var orig = btn.textContent;
    btn.textContent = 'Скопировано';
    setTimeout(function(){ btn.textContent = orig; }, 2000);
  });
}

function updateFilterCounts(items) {
  var counts = {};
  items.forEach(function(i){ counts[i.status] = (counts[i.status]||0) + 1; });
  var labels = ['Все','Ожидают','Принято','Позже','Готово','Пропущено'];
  document.querySelectorAll('.filter-btn').forEach(function(b, i) {
    var s = FILTER_KEYS[i];
    var cnt = s === 'all' ? items.length : (counts[s]||0);
    b.textContent = labels[i] + (cnt ? ' ('+cnt+')' : '');
  });
}

fetch('/api/improves')
  .then(function(r){ return r.json(); })
  .then(function(data) {
    allItems = data;
    updateFilterCounts(data);
    render();
  })
  .catch(function() {
    document.getElementById('cards').innerHTML =
      '<div class="empty">Не удалось загрузить данные</div>';
  });

initTheme();
</script>
</body></html>"""
)

# ── agent_notes.md parser ─────────────────────────────────────────────────────

_STATUS_MAP = {
    "[ ]": "pending",
    "[→]": "accepted",
    "[❌]": "rejected",
    "[⏳]": "later",
    "[✅]": "done",
}
_CAT_PATTERNS = [
    ("⚠️", "issue"),
    ("🔄", "pattern"),
    ("🔴", "error"),
    ("💡", "idea"),
]
_STATUS_ORDER = {"pending": 0, "accepted": 1, "later": 2, "done": 3, "rejected": 4}


def _parse_agent_notes(vault_path: Path) -> list[dict]:
    notes_path = vault_path / "agent" / "agent_notes.md"
    if not notes_path.exists():
        return []
    content = notes_path.read_text(encoding="utf-8")
    items: list[dict] = []
    current_date = ""
    for line in content.splitlines():
        m_date = re.match(r"^## (\d{4}-\d{2}-\d{2})$", line.strip())
        if m_date:
            current_date = m_date.group(1)
            continue
        m_id = re.search(r"<!-- id: ([\w-]+) -->", line)
        if not m_id:
            continue
        note_id = m_id.group(1)
        m_status = re.search(r"`(\[.*?\])`", line)
        status_raw = m_status.group(1) if m_status else "[ ]"
        status = _STATUS_MAP.get(status_raw, "pending")
        category = "idea"
        for emoji, cat in _CAT_PATTERNS:
            if emoji in line:
                category = cat
                break
        m_file = re.search(r"\| файл: (vault/agent/concepts/\S+\.md)", line)
        concept_file = m_file.group(1) if m_file else None
        clean = re.sub(r"\s*<!--.*?-->.*$", "", line)
        clean = re.sub(r"^[-\s]*`\[.*?\]`\s*", "", clean)
        clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", clean)
        clean = re.sub(r"\s*\([^)]*https?://[^)]*\)\s*", "", clean)
        clean = clean.strip()
        items.append({
            "id": note_id,
            "status": status,
            "category": category,
            "date": current_date,
            "title": clean or note_id,
            "concept_file": concept_file,
            "brief_desc": "",
            "auto_implementable": False,
        })
    # Enrich with concept file data
    for item in items:
        if item["concept_file"]:
            info = _parse_concept_brief(vault_path, item["concept_file"])
            item["brief_desc"] = info["brief_desc"]
            item["auto_implementable"] = info["auto_implementable"]
    # Sort: by status priority, then by date descending within each group
    items.sort(key=lambda x: (_STATUS_ORDER.get(x["status"], 5), x["date"]))
    result: list[dict] = []
    for status_key in ["pending", "accepted", "later", "done", "rejected"]:
        group = [i for i in items if i["status"] == status_key]
        group.sort(key=lambda x: x["date"], reverse=True)
        result.extend(group)
    return result


def _parse_concept_brief(vault_path: Path, concept_file: str) -> dict:
    doc_path = vault_path.parent / concept_file
    if not doc_path.exists():
        return {"brief_desc": "", "auto_implementable": False}
    try:
        content = doc_path.read_text(encoding="utf-8")
        m_desc = re.search(r"## Что это\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
        brief_desc = m_desc.group(1).strip()[:400] if m_desc else ""
        auto_implementable = "**Автоматически:** Да" in content
        return {"brief_desc": brief_desc, "auto_implementable": auto_implementable}
    except Exception:
        return {"brief_desc": "", "auto_implementable": False}


def _update_note_status_web(vault_path: Path, note_id: str, new_status: str) -> bool:
    notes_path = vault_path / "agent" / "agent_notes.md"
    if not notes_path.exists():
        return False
    try:
        content = notes_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        updated = False
        for i, line in enumerate(lines):
            if f"<!-- id: {note_id} -->" in line:
                lines[i] = re.sub(r"`\[.*?\]`", f"`{new_status}`", line)
                updated = True
                break
        if updated:
            notes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return updated
    except Exception as e:
        logger.error("Failed to update note status: %s", e)
        return False



@app.get("/api/improves/{note_id}/detail", response_class=JSONResponse)
async def api_improves_detail(note_id: str) -> dict:
    settings = get_settings()
    items = _parse_agent_notes(settings.vault_path)
    item = next((i for i in items if i["id"] == note_id), None)
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404)
    full_content = ""
    if item.get("concept_file"):
        cf = settings.vault_path.parent / item["concept_file"]
        if cf.exists():
            full_content = cf.read_text(encoding="utf-8")
    import json as _json
    comments_file = settings.vault_path / "agent" / "improve_comments.json"
    comment = ""
    if comments_file.exists():
        data = _json.loads(comments_file.read_text(encoding="utf-8"))
        comment = data.get(note_id, "")
    return JSONResponse({**item, "full_content": full_content, "comment": comment})


class CommentBody(BaseModel):
    comment: str = ""


@app.post("/api/improves/{note_id}/comment", response_class=JSONResponse)
async def api_save_comment(note_id: str, body: CommentBody) -> dict:
    settings = get_settings()
    comments_file = settings.vault_path / "agent" / "improve_comments.json"
    import json as _json
    data = {}
    if comments_file.exists():
        try:
            data = _json.loads(comments_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    data[note_id] = body.comment
    comments_file.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse({"ok": True})


# ── Routes: upload ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _UPLOAD_HTML


@app.post("/", response_class=HTMLResponse)
async def upload(
    file: UploadFile = File(...),
    diarize: str = Form(default=""),
) -> HTMLResponse:
    settings = get_settings()
    use_diarize = diarize == "1"

    MAX_SIZE = 100 * 1024 * 1024
    content = await file.read(MAX_SIZE + 1)
    if len(content) > MAX_SIZE:
        return HTMLResponse("<h2>Файл слишком большой (макс. 100 MB)</h2>", status_code=413)
    try:
        audio_bytes = content
        filename = file.filename or "audio"
        size_mb = len(audio_bytes) / 1024 / 1024
        logger.info("Web upload: %s %.1f MB diarize=%s", filename, size_mb, use_diarize)

        transcriber = DeepgramTranscriber(settings.deepgram_api_key)

        if use_diarize:
            utterances = await transcriber.transcribe_diarized(audio_bytes)
            if not utterances:
                return _result(_SVG_ERR, "Ошибка", "Не удалось распознать речь в файле.")
            user_speaker, is_confident = identify_user_speaker(utterances)
            num_speakers = len({u.speaker for u in utterances})
            transcript = format_diarized(utterances, user_speaker)
            source_tag = f"[web-meeting · {num_speakers} speakers]"
            confidence_note = (
                ""
                if is_confident or num_speakers == 1
                else build_confidence_note(utterances, user_speaker)
            )
        else:
            transcript = await transcriber.transcribe(audio_bytes)
            if not transcript:
                return _result(_SVG_ERR, "Ошибка", "Не удалось распознать речь в файле.")
            source_tag = "[web-voice]"
            confidence_note = ""
            num_speakers = 1

        corrections = CorrectionsService(settings.vault_path)
        corrected, applied = corrections.apply(transcript)

        storage = VaultStorage(settings.vault_path)
        storage.append_to_daily(corrected, datetime.now(), source_tag)

        user_id = settings.allowed_user_ids[0] if settings.allowed_user_ids else 0
        session = SessionStore(settings.vault_path)
        session.append(user_id, "web-voice", text=corrected)

        tg_text = (
            f"🌐 {filename} ({size_mb:.1f} MB)\n\n"
            + corrected
            + confidence_note
            + "\n\n✓ Сохранено"
        )
        if applied:
            tg_text += f" · Исправлено: {', '.join(applied)}"

        await _send_telegram(settings.telegram_bot_token, user_id, tg_text)

        return _result(
            _SVG_OK,
            "Готово",
            f"Транскрипция отправлена в Telegram · {len(corrected)} символов",
        )

    except Exception as e:
        logger.exception("Web upload error")
        return _result(_SVG_ERR, "Ошибка", str(e))


# ── Routes: improves ──────────────────────────────────────────────────────────

@app.get("/improves", response_class=HTMLResponse)
async def improves_page() -> str:
    return _IMPROVES_HTML


@app.get("/api/improves", response_class=JSONResponse)
async def api_improves() -> list:
    settings = get_settings()
    items = await asyncio.to_thread(_parse_agent_notes, settings.vault_path)
    return items


class StatusUpdate(BaseModel):
    status: str


_STATUS_TO_MARKER = {
    "accepted": "[→]",
    "rejected": "[❌]",
    "later": "[⏳]",
    "done": "[✅]",
    "pending": "[ ]",
}


@app.post("/api/improves/{note_id}/status", response_class=JSONResponse)
async def api_update_status(note_id: str, body: StatusUpdate) -> dict:
    settings = get_settings()
    marker = _STATUS_TO_MARKER.get(body.status)
    if not marker:
        return {"ok": False, "error": f"Unknown status: {body.status}"}
    ok = await asyncio.to_thread(
        _update_note_status_web, settings.vault_path, note_id, marker
    )
    return {"ok": ok}


@app.post("/api/improves/{note_id}/apply", response_class=JSONResponse)
async def api_apply_improve(note_id: str) -> dict:
    settings = get_settings()
    vault_path = settings.vault_path
    # Find concept_file for this note_id
    items = await asyncio.to_thread(_parse_agent_notes, vault_path)
    item = next((i for i in items if i["id"] == note_id), None)
    if not item:
        return {"ok": False, "error": "Запись не найдена"}
    concept_file = item.get("concept_file")
    if not concept_file:
        return {"ok": False, "error": "Нет файла концепта для этой записи"}
    project_dir = vault_path.parent
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "scripts/night_implement_single.sh", note_id, concept_file,
            cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        result_str = stdout.decode().strip()
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Превышено время ожидания (5 мин)"}
    except Exception as e:
        return {"ok": False, "error": f"Ошибка запуска: {e}"}

    if result_str.startswith("DONE:"):
        what = result_str[5:].strip()
        await asyncio.to_thread(_update_note_status_web, vault_path, note_id, "[✅]")
        return {"ok": True, "result": what}
    else:
        reason = result_str[7:].strip() if result_str.startswith("FAILED:") else result_str
        return {"ok": False, "error": reason}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _send_telegram(token: str, user_id: int, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(text), 4000):
            await client.post(url, json={"chat_id": user_id, "text": text[i : i + 4000]})
