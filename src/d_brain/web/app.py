"""Web upload portal for large audio files (meeting recordings)."""

import logging
from datetime import date as date_cls
from datetime import datetime

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

_schema_v2_applied: bool = False


class EditMealBody(BaseModel):
    instruction: str


class DeleteMealBody(BaseModel):
    reason: str = ""

_UPLOAD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>d-brain</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         background:#0f0f0f;color:#e0e0e0;min-height:100vh;
         display:flex;align-items:center;justify-content:center;padding:20px}
    .card{background:#1a1a1a;border-radius:16px;padding:32px;
          max-width:420px;width:100%;box-shadow:0 8px 32px rgba(0,0,0,.4)}
    h1{font-size:20px;font-weight:600;margin-bottom:6px}
    .sub{font-size:13px;color:#888;margin-bottom:28px}
    .field{margin-bottom:18px}
    label.lbl{display:block;font-size:12px;color:#aaa;margin-bottom:7px}
    .fa{display:block;width:100%;padding:14px;background:#222;
        border:2px dashed #444;border-radius:10px;color:#ccc;
        font-size:14px;text-align:center;cursor:pointer;transition:border-color .2s}
    .fa:hover{border-color:#6366f1}
    input[type=file]{display:none}
    .fn{font-size:12px;color:#6366f1;margin-top:7px;min-height:16px}
    .tog{display:flex;align-items:flex-start;gap:12px;padding:14px;
         background:#222;border-radius:10px;cursor:pointer}
    .tog input{width:18px;height:18px;accent-color:#6366f1;cursor:pointer;
               margin-top:2px;flex-shrink:0}
    .tl{font-size:14px}.td{font-size:12px;color:#666;margin-top:3px}
    button[type=submit]{width:100%;padding:16px;background:#6366f1;border:none;
                        border-radius:10px;color:#fff;font-size:16px;font-weight:600;
                        cursor:pointer;margin-top:10px;transition:background .2s}
    button:hover{background:#4f52d6}
    button:disabled{background:#333;cursor:not-allowed}
    .fmt{font-size:11px;color:#555;text-align:center;margin-top:14px}
  </style>
</head>
<body>
<div class="card">
  <h1>&#127911; d-brain</h1>
  <p class="sub">Загрузи запись встречи — транскрипция придёт в Telegram</p>
  <form method="post" enctype="multipart/form-data" id="frm">
    <div class="field">
      <label class="lbl">Файл записи</label>
      <label class="fa" for="f">Выбрать файл</label>
      <input type="file" id="f" name="file" accept="audio/*,video/mp4"
             onchange="document.getElementById('fn').textContent=this.files[0]?.name||''">
      <div class="fn" id="fn"></div>
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
    <button type="submit" id="btn">Отправить</button>
    <p class="fmt">m4a &middot; mp3 &middot; ogg &middot; wav &middot; opus &middot; flac &middot; mp4</p>
  </form>
  <script>
    document.getElementById('frm').onsubmit = function() {
      if (!document.getElementById('f').files[0]) {
        alert('Выбери файл'); return false;
      }
      document.getElementById('btn').disabled = true;
      document.getElementById('btn').textContent = 'Обрабатывается\u2026';
    };
  </script>
</div>
</body></html>"""

_RESULT_TMPL = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>d-brain &middot; {title}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         background:#0f0f0f;color:#e0e0e0;min-height:100vh;
         display:flex;align-items:center;justify-content:center;padding:20px}}
    .card{{background:#1a1a1a;border-radius:16px;padding:32px;
           max-width:420px;width:100%;text-align:center}}
    .icon{{font-size:52px;margin-bottom:16px}}
    h2{{font-size:20px;margin-bottom:10px}}
    p{{color:#888;font-size:14px;margin-bottom:24px}}
    a{{display:block;padding:14px;background:#222;border-radius:10px;
       color:#6366f1;text-decoration:none;font-size:14px}}
  </style>
</head>
<body>
<div class="card">
  <div class="icon">{icon}</div>
  <h2>{title}</h2>
  <p>{message}</p>
  <a href="/">&#8592; Загрузить ещё</a>
</div>
</body></html>"""


def _result(icon: str, title: str, message: str) -> HTMLResponse:
    return HTMLResponse(_RESULT_TMPL.format(icon=icon, title=title, message=message))


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

    # File size limit: 100MB
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
                return _result("❌", "Ошибка", "Не удалось распознать речь в файле.")

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
                return _result("❌", "Ошибка", "Не удалось распознать речь в файле.")
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
            "✅",
            "Готово",
            f"Транскрипция отправлена в Telegram · {len(corrected)} символов",
        )

    except Exception as e:
        logger.exception("Web upload error")
        return _result("❌", "Ошибка", str(e))


async def _send_telegram(token: str, user_id: int, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(text), 4000):
            await client.post(url, json={"chat_id": user_id, "text": text[i : i + 4000]})


# ──────────────────────── Nutrition Dashboard ────────────────────────

def _nutrition_user_id() -> int:
    settings = get_settings()
    return settings.allowed_user_ids[0] if settings.allowed_user_ids else 0


@app.get("/nutrition", response_class=HTMLResponse)
async def nutrition_dashboard() -> str:
    global _schema_v2_applied
    settings = get_settings()
    user_id = _nutrition_user_id()

    if not settings.supabase_url or not settings.supabase_key:
        return HTMLResponse("<h2>Supabase не настроен</h2>", status_code=503)

    try:
        from d_brain.services.nutrition import get_nutrition_service
        svc = get_nutrition_service()
        if not _schema_v2_applied:
            await svc.ensure_schema_v2()
            _schema_v2_applied = True
        today = await svc.get_today_progress(user_id)
        weekly = await svc.get_weekly_data(user_id, days=14)
        recent = await svc.get_recent_meals(user_id, limit=10)
    except Exception as e:
        logger.exception("Nutrition dashboard error")
        return HTMLResponse(f"<h2>Ошибка: {e}</h2>", status_code=500)

    import json as _json
    today_json = _json.dumps(today, default=str)
    weekly_json = _json.dumps(weekly, default=str)
    recent_json = _json.dumps(recent, default=str)

    html = _NUTRITION_TMPL.replace("__TODAY__", today_json)
    html = html.replace("__WEEKLY__", weekly_json)
    html = html.replace("__RECENT__", recent_json)
    return html


@app.get("/nutrition/meals")
async def api_meals_by_date(date: str = "") -> JSONResponse:
    settings = get_settings()
    if not settings.supabase_url:
        return JSONResponse({"error": "Supabase not configured"}, status_code=503)
    try:
        target = date_cls.fromisoformat(date) if date else date_cls.today()
    except ValueError:
        return JSONResponse({"error": "invalid date"}, status_code=400)
    try:
        from d_brain.services.nutrition import get_nutrition_service
        svc = get_nutrition_service()
        meals = await svc.get_meals_by_date(_nutrition_user_id(), target)
        return JSONResponse(meals)
    except Exception as e:
        logger.exception("api_meals_by_date error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/nutrition/meals/{meal_id}/edit")
async def api_edit_meal(meal_id: str, body: EditMealBody) -> JSONResponse:
    settings = get_settings()
    if not settings.supabase_url:
        return JSONResponse({"error": "Supabase not configured"}, status_code=503)
    try:
        from d_brain.services.nutrition import get_nutrition_service
        svc = get_nutrition_service()
        updated = await svc.edit_meal_via_llm(meal_id, _nutrition_user_id(), body.instruction)
        if updated is None:
            return JSONResponse({"error": "meal not found"}, status_code=404)
        return JSONResponse(updated)
    except Exception as e:
        logger.exception("api_edit_meal error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/nutrition/meals/{meal_id}/delete")
async def api_delete_meal(meal_id: str, body: DeleteMealBody) -> JSONResponse:
    settings = get_settings()
    if not settings.supabase_url:
        return JSONResponse({"error": "Supabase not configured"}, status_code=503)
    try:
        from d_brain.services.nutrition import get_nutrition_service
        svc = get_nutrition_service()
        ok = await svc.delete_meal(meal_id, _nutrition_user_id(), body.reason)
        return JSONResponse({"ok": ok})
    except Exception as e:
        logger.exception("api_delete_meal error")
        return JSONResponse({"error": str(e)}, status_code=500)


_NUTRITION_TMPL = r"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>d-brain · Питание</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         background:#0f0f0f;color:#e0e0e0;padding:20px;min-height:100vh}
    a.back{display:inline-block;margin-bottom:20px;color:#6366f1;text-decoration:none;font-size:14px}
    h1{font-size:22px;font-weight:700;margin-bottom:4px}
    .sub{font-size:13px;color:#666;margin-bottom:16px}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-bottom:24px}
    .card{background:#1a1a1a;border-radius:14px;padding:20px}
    .card h2{font-size:14px;color:#888;font-weight:500;margin-bottom:14px;text-transform:uppercase;letter-spacing:.5px}
    .macros{display:grid;grid-template-columns:1fr 1fr;gap:10px}
    .macro{background:#222;border-radius:10px;padding:12px}
    .macro .val{font-size:22px;font-weight:700;color:#e0e0e0}
    .macro .lbl{font-size:11px;color:#666;margin-top:2px}
    .macro .pct{font-size:11px;color:#6366f1;margin-top:1px}
    .bar-wrap{margin-bottom:10px}
    .bar-label{display:flex;justify-content:space-between;font-size:12px;color:#888;margin-bottom:4px}
    .bar-bg{background:#222;border-radius:4px;height:8px;overflow:hidden}
    .bar-fill{height:100%;border-radius:4px;transition:width .4s}
    .bar-kcal{background:#6366f1}
    .bar-prot{background:#22c55e}
    .bar-fat{background:#f59e0b}
    .bar-carb{background:#3b82f6}
    .chart-wrap{position:relative;height:200px}
    .meals-list{list-style:none}
    .meals-list li{padding:12px 0;border-bottom:1px solid #222;font-size:13px}
    .meals-list li:last-child{border-bottom:none}
    .meal-head{display:flex;justify-content:space-between;margin-bottom:4px}
    .meal-type{font-weight:600;color:#e0e0e0}
    .meal-kcal{color:#6366f1;font-weight:600}
    .meal-desc{color:#888;margin-bottom:4px}
    .meal-comment{color:#555;font-size:11px;font-style:italic}
    .over{color:#ef4444 !important}
    /* ── tabs ── */
    .tabs{display:flex;gap:8px;margin-bottom:20px}
    .tab-btn{padding:8px 18px;border-radius:8px;border:none;background:#222;
             color:#888;font-size:14px;cursor:pointer;transition:all .2s}
    .tab-btn.active{background:#6366f1;color:#fff}
    .tab-panel{display:none}.tab-panel.active{display:block}
    /* ── date nav ── */
    .date-nav{display:flex;align-items:center;gap:12px;margin-bottom:16px}
    .date-nav button{background:#222;border:none;color:#aaa;border-radius:6px;
                     padding:6px 14px;cursor:pointer;font-size:18px;line-height:1}
    .date-nav button:hover{background:#333}
    .date-nav .date-label{font-size:15px;font-weight:600;min-width:160px;text-align:center;color:#e0e0e0}
    /* ── meal record cards ── */
    .meal-record{background:#222;border-radius:12px;padding:14px;margin-bottom:10px}
    .meal-record.deleted{opacity:.4}
    .mr-head{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px}
    .mr-title{font-weight:600;color:#e0e0e0}
    .mr-kcal{color:#6366f1;font-weight:600;font-size:13px}
    .mr-desc{font-size:13px;color:#999;margin-bottom:4px}
    .mr-kbzhu{font-size:12px;color:#666;margin-bottom:4px}
    .mr-comment{font-size:11px;color:#555;font-style:italic;margin-bottom:8px}
    .mr-del-label{font-size:11px;color:#555;margin-top:4px}
    .mr-actions{display:flex;gap:8px;margin-top:8px}
    .mr-btn{background:#2a2a2a;border:1px solid #333;color:#aaa;border-radius:6px;
            padding:4px 10px;font-size:12px;cursor:pointer}
    .mr-btn:hover{border-color:#555;color:#e0e0e0}
    .mr-form{margin-top:10px;display:none}
    .mr-form.open{display:block}
    .mr-textarea{width:100%;background:#1a1a1a;border:1px solid #333;border-radius:6px;
                 color:#e0e0e0;padding:8px;font-size:13px;resize:vertical;min-height:48px}
    .mr-textarea:focus{outline:none;border-color:#6366f1}
    .mr-submit{background:#6366f1;border:none;color:#fff;border-radius:6px;
               padding:6px 14px;font-size:13px;cursor:pointer;margin-top:6px}
    .mr-submit:disabled{background:#333;color:#666;cursor:not-allowed}
    .mr-del-submit{background:#ef4444;border:none;color:#fff;border-radius:6px;
                   padding:6px 14px;font-size:13px;cursor:pointer;margin-top:6px}
    .del-strikethrough{text-decoration:line-through;color:#555}
    .rec-empty{color:#555;padding:20px 0;text-align:center;font-size:13px}
  </style>
</head>
<body>
  <a class="back" href="/">← Главная</a>
  <h1>🥗 Питание</h1>
  <p class="sub" id="dateStr"></p>

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('svod')">📊 Сводка</button>
    <button class="tab-btn" onclick="switchTab('records')">📋 Записи</button>
  </div>

  <!-- ── Tab: Сводка ── -->
  <div id="tab-svod" class="tab-panel active">
    <div class="grid">
      <div class="card" id="todayCard">
        <h2>Сегодня</h2>
        <div id="barsWrap"></div>
        <div class="macros" id="macrosGrid"></div>
      </div>
      <div class="card">
        <h2>Калории за 14 дней</h2>
        <div class="chart-wrap"><canvas id="kcalChart"></canvas></div>
      </div>
    </div>
    <div class="card">
      <h2>Последние приёмы пищи</h2>
      <ul class="meals-list" id="mealsList"></ul>
    </div>
  </div>

  <!-- ── Tab: Записи ── -->
  <div id="tab-records" class="tab-panel">
    <div class="card">
      <div class="date-nav">
        <button onclick="shiftDay(-1)">&#8592;</button>
        <span class="date-label" id="recDateLabel"></span>
        <button onclick="shiftDay(1)">&#8594;</button>
      </div>
      <div id="recordsList"></div>
    </div>
  </div>

<script>
const TODAY   = __TODAY__;
const WEEKLY  = __WEEKLY__;
const RECENT  = __RECENT__;

const MEAL_EMOJI = {завтрак:"🌅",обед:"☀️",ужин:"🌙",перекус:"🍎"};

// ── date subtitle ──
document.getElementById("dateStr").textContent =
  new Date().toLocaleDateString("ru-RU",{weekday:"long",day:"numeric",month:"long"});

// ── tabs ──
function switchTab(name) {
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  document.getElementById("tab-" + name).classList.add("active");
  event.currentTarget.classList.add("active");
  if (name === "records") loadRecords();
}

// ── progress bars ──
function bar(id, label, val, goal, cls) {
  const pct = goal > 0 ? Math.min(Math.round(val/goal*100), 100) : 0;
  const over = val > goal;
  return '<div class="bar-wrap">'
    + '<div class="bar-label"><span>' + label + '</span>'
    + '<span class="' + (over?"over":"") + '">' + val + ' / ' + goal + '</span></div>'
    + '<div class="bar-bg"><div class="bar-fill ' + cls + '" style="width:' + pct + '%"></div></div>'
    + '</div>';
}
document.getElementById("barsWrap").innerHTML =
  bar("k","Калории ккал", TODAY.total_calories||0, TODAY.goal_calories||2000, "bar-kcal") +
  bar("p","Белки г",      TODAY.total_protein||0,  TODAY.goal_protein||155,   "bar-prot") +
  bar("f","Жиры г",       TODAY.total_fat||0,       TODAY.goal_fat||55,        "bar-fat")  +
  bar("c","Углеводы г",   TODAY.total_carbs||0,     TODAY.goal_carbs||220,     "bar-carb");

// ── macro tiles ──
function tile(val, lbl, goal, color) {
  const pct = goal>0?Math.round(val/goal*100):0;
  return '<div class="macro">'
    + '<div class="val" style="color:' + color + '">' + val + '</div>'
    + '<div class="lbl">' + lbl + '</div>'
    + '<div class="pct">' + pct + '% от нормы</div>'
    + '</div>';
}
document.getElementById("macrosGrid").innerHTML =
  tile(TODAY.total_calories||0, "ккал", TODAY.goal_calories||2000, "#6366f1") +
  tile(TODAY.total_protein||0,  "белки г", TODAY.goal_protein||155, "#22c55e") +
  tile(TODAY.total_fat||0,      "жиры г", TODAY.goal_fat||55, "#f59e0b") +
  tile(TODAY.total_carbs||0,    "углеводы г", TODAY.goal_carbs||220, "#3b82f6");

// ── weekly chart ──
if (WEEKLY.length > 0) {
  new Chart(document.getElementById("kcalChart"), {
    type: "bar",
    data: {
      labels: WEEKLY.map(d => d.date.slice(5)),
      datasets: [{
        label: "Калории",
        data: WEEKLY.map(d => d.total_calories||0),
        backgroundColor: WEEKLY.map(d =>
          (d.total_calories||0) > (d.goal_calories||2000) ? "#ef444480" : "#6366f180"),
        borderColor: WEEKLY.map(d =>
          (d.total_calories||0) > (d.goal_calories||2000) ? "#ef4444" : "#6366f1"),
        borderWidth: 2, borderRadius: 4,
      },{
        label: "Норма",
        data: WEEKLY.map(d => d.goal_calories||2000),
        type: "line", borderColor: "#ffffff30", borderDash: [4,4],
        pointRadius: 0, fill: false,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {legend:{display:false}},
      scales: {
        x: {grid:{color:"#222"},ticks:{color:"#666",font:{size:11}}},
        y: {grid:{color:"#222"},ticks:{color:"#666",font:{size:11}},min:0}
      }
    }
  });
}

// ── recent meals (Сводка tab) ──
const list = document.getElementById("mealsList");
if (RECENT.length === 0) {
  list.innerHTML = "<li style='color:#555;padding:16px 0'>Приёмов пищи пока нет</li>";
} else {
  list.innerHTML = RECENT.map(m => {
    const dt = new Date(m.logged_at);
    const time = dt.toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"});
    const emoji = MEAL_EMOJI[m.meal_type] || "🍽";
    return "<li>"
      + '<div class="meal-head">'
      + '<span class="meal-type">' + emoji + " " + (m.meal_type||"приём") + " · " + time + "</span>"
      + '<span class="meal-kcal">' + (m.calories||0) + " ккал</span>"
      + "</div>"
      + '<div class="meal-desc">' + (m.description||"") + "</div>"
      + (m.nutritionist_comment ? '<div class="meal-comment">' + m.nutritionist_comment + "</div>" : "")
      + "</li>";
  }).join("");
}

// ── Records tab ──
let recDate = new Date();
recDate.setHours(0,0,0,0);

function isoDate(d) {
  return d.getFullYear() + "-" +
    String(d.getMonth()+1).padStart(2,"0") + "-" +
    String(d.getDate()).padStart(2,"0");
}

function fmtDateLabel(d) {
  return d.toLocaleDateString("ru-RU",{weekday:"short",day:"numeric",month:"long"});
}

function shiftDay(delta) {
  recDate.setDate(recDate.getDate() + delta);
  loadRecords();
}

async function loadRecords() {
  document.getElementById("recDateLabel").textContent = fmtDateLabel(recDate);
  document.getElementById("recordsList").innerHTML =
    '<div class="rec-empty">Загрузка...</div>';
  try {
    const r = await fetch("/nutrition/meals?date=" + isoDate(recDate));
    const meals = await r.json();
    if (Array.isArray(meals)) {
      renderRecords(meals);
    } else {
      document.getElementById("recordsList").innerHTML =
        '<div class="rec-empty" style="color:#ef4444">Ошибка: ' + (meals.error||"unknown") + "</div>";
    }
  } catch(e) {
    document.getElementById("recordsList").innerHTML =
      '<div class="rec-empty" style="color:#ef4444">Ошибка: ' + e.message + "</div>";
  }
}

function renderRecords(meals) {
  const el = document.getElementById("recordsList");
  if (!meals.length) {
    el.innerHTML = '<div class="rec-empty">Нет записей за этот день</div>';
    return;
  }
  const active = meals.filter(m => !m.is_deleted);
  const deleted = meals.filter(m => m.is_deleted);
  el.innerHTML = active.concat(deleted).map(m => mealCard(m)).join("");
}

function mealCard(m) {
  const dt = new Date(m.logged_at);
  const time = dt.toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"});
  const emoji = MEAL_EMOJI[m.meal_type] || "🍽";
  const kbzhu = (m.calories||0) + " ккал · Б:" + (m.protein||0) + " Ж:" + (m.fat||0) + " У:" + (m.carbs||0);
  if (m.is_deleted) {
    return '<div class="meal-record deleted" id="mr-' + m.id + '">'
      + '<div class="mr-head">'
      + '<span class="mr-title del-strikethrough">' + emoji + " " + (m.meal_type||"приём") + " · " + time + "</span>"
      + '<span class="mr-kcal">' + (m.calories||0) + " ккал</span>"
      + "</div>"
      + '<div class="mr-desc del-strikethrough">' + (m.description||"") + "</div>"
      + '<div class="mr-del-label">🗑 ' + (m.delete_reason||"удалено") + "</div>"
      + "</div>";
  }
  return '<div class="meal-record" id="mr-' + m.id + '">'
    + '<div class="mr-head">'
    + '<span class="mr-title">' + emoji + " " + (m.meal_type||"приём") + " · " + time + "</span>"
    + '<span class="mr-kcal">' + (m.calories||0) + " ккал</span>"
    + "</div>"
    + '<div class="mr-desc">' + (m.description||"") + "</div>"
    + '<div class="mr-kbzhu">' + kbzhu + "</div>"
    + (m.nutritionist_comment ? '<div class="mr-comment">' + m.nutritionist_comment + "</div>" : "")
    + '<div class="mr-actions">'
    + '<button class="mr-btn" onclick="toggleEdit(\'' + m.id + '\')">✏️ Изменить</button>'
    + '<button class="mr-btn" onclick="toggleDel(\'' + m.id + '\')">🗑 Удалить</button>'
    + "</div>"
    + '<div class="mr-form" id="edit-' + m.id + '">'
    + '<textarea class="mr-textarea" id="edit-txt-' + m.id + '" placeholder="добавь 15г сахара"></textarea>'
    + '<button class="mr-submit" id="edit-btn-' + m.id + '" onclick="applyEdit(\'' + m.id + '\')">Применить</button>'
    + "</div>"
    + '<div class="mr-form" id="del-' + m.id + '">'
    + '<textarea class="mr-textarea" id="del-txt-' + m.id + '" placeholder="Причина (напр: дубль)"></textarea>'
    + '<button class="mr-del-submit" id="del-btn-' + m.id + '" onclick="applyDel(\'' + m.id + '\')">Удалить</button>'
    + "</div>"
    + "</div>";
}

function toggleEdit(id) {
  var ef = document.getElementById("edit-" + id);
  var df = document.getElementById("del-" + id);
  df.classList.remove("open");
  ef.classList.toggle("open");
}

function toggleDel(id) {
  var df = document.getElementById("del-" + id);
  var ef = document.getElementById("edit-" + id);
  ef.classList.remove("open");
  df.classList.toggle("open");
}

async function applyEdit(id) {
  var instruction = document.getElementById("edit-txt-" + id).value.trim();
  if (!instruction) return;
  var btn = document.getElementById("edit-btn-" + id);
  btn.disabled = true;
  btn.textContent = "Обрабатывается…";
  try {
    var r = await fetch("/nutrition/meals/" + id + "/edit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({instruction: instruction})
    });
    var data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
    loadRecords();
  } catch(e) {
    btn.textContent = "Ошибка: " + e.message;
    btn.disabled = false;
  }
}

async function applyDel(id) {
  var reason = document.getElementById("del-txt-" + id).value.trim();
  var btn = document.getElementById("del-btn-" + id);
  btn.disabled = true;
  try {
    var r = await fetch("/nutrition/meals/" + id + "/delete", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({reason: reason})
    });
    var data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
    loadRecords();
  } catch(e) {
    btn.textContent = "Ошибка";
    btn.disabled = false;
  }
}
</script>
</body></html>"""
