"""SteppeAlert backend: берёт очаги огня NASA FIRMS и отдаёт их странице.
Запуск: uvicorn main:app --reload
Переменные окружения (необязательно):
  FIRMS_MAP_KEY       ключ NASA FIRMS (бесплатно). Без него работают демо-данные.
  TELEGRAM_BOT_TOKEN  токен бота от @BotFather для отправки уведомлений.
"""
import csv, io, math, os, time
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="SteppeAlert")
FIRMS_KEY = os.getenv("FIRMS_MAP_KEY")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BBOX = "112.0,-44.0,154.0,-10.0"  # Австралия: запад,юг,восток,север

DEMO = [  # демо-очаги (не реальные данные), чтобы сайт работал без ключа
    {"lat": -33.70, "lon": 150.30, "date": "demo", "confidence": "h"},
    {"lat": -37.60, "lon": 148.10, "date": "demo", "confidence": "n"},
    {"lat": -35.80, "lon": 137.20, "date": "demo", "confidence": "n"},
]

def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))

# Несколько спутников: если один не обновился, данные придут с других
SOURCES = ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT", "MODIS_NRT"]
CACHE = {}          # days -> (время, очаги, ошибки)
CACHE_SEC = 600     # не спрашиваем NASA чаще раза в 10 минут

async def get_fires(days: int = 1):
    """Возвращает (очаги, демо?, ошибки)."""
    if not FIRMS_KEY:
        return DEMO, True, []
    hit = CACHE.get(days)
    if hit and time.time() - hit[0] < CACHE_SEC:
        return hit[1], False, hit[2]
    fires, errors, seen = [], [], set()
    async with httpx.AsyncClient(timeout=30) as c:
        for src in SOURCES:
            url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/{src}/{BBOX}/{days}"
            try:
                r = await c.get(url)
            except httpx.HTTPError as e:
                errors.append(f"{src}: {e.__class__.__name__}")
                continue
            text = r.text.strip()
            if not text.startswith("latitude"):   # NASA вернула ошибку, а не таблицу
                errors.append(f"{src}: {text[:120]}")
                continue
            for x in csv.DictReader(io.StringIO(text)):
                key = (round(float(x["latitude"]), 2), round(float(x["longitude"]), 2))
                if key in seen:                   # одну точку видели два спутника
                    continue
                seen.add(key)
                fires.append({"lat": float(x["latitude"]), "lon": float(x["longitude"]),
                              "date": f'{x["acq_date"]} {x.get("acq_time", "")}'.strip(),
                              "confidence": x.get("confidence", ""), "source": src})
    if len(errors) < len(SOURCES):                # хотя бы один спутник ответил
        CACHE[days] = (time.time(), fires, errors)
    return fires, False, errors

@app.get("/api/fires")
async def fires(days: int = Query(1, ge=1, le=5)):
    data, demo, errors = await get_fires(days)
    return {"demo": demo, "count": len(data), "fires": data, "errors": errors,
            "nasa_down": bool(errors) and len(errors) == len(SOURCES)}

@app.get("/api/check")
async def check(lat: float, lon: float, radius_km: float = 20, chat_id: str | None = None):
    data, demo, _ = await get_fires(1)
    near = sorted(
        ({**f, "distance_km": round(km(lat, lon, f["lat"], f["lon"]), 1)} for f in data),
        key=lambda f: f["distance_km"])
    near = [f for f in near if f["distance_km"] <= radius_km]
    sent = False
    if near and chat_id and TG_TOKEN:
        text = f"🔥 Рядом с вашим полем очаг огня: {near[0]['distance_km']} км. Всего очагов в радиусе {radius_km} км: {len(near)}."
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                         json={"chat_id": chat_id, "text": text})
        sent = True
    return {"demo": demo, "danger": bool(near), "near": near, "telegram_sent": sent}

@app.get("/")
def index():
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")
