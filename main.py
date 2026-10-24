"""SteppeAlert backend: берёт очаги огня NASA FIRMS и отдаёт их странице.
Запуск: uvicorn main:app --reload
Переменные окружения (необязательно):
  FIRMS_MAP_KEY       ключ NASA FIRMS (бесплатно). Без него работают демо-данные.
  TELEGRAM_BOT_TOKEN  токен бота от @BotFather для отправки уведомлений.
"""
import csv, io, math, os
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

async def get_fires(days: int = 1):
    if not FIRMS_KEY:
        return DEMO, True
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/VIIRS_SNPP_NRT/{BBOX}/{days}"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url)
    rows = csv.DictReader(io.StringIO(r.text))
    fires = [{"lat": float(x["latitude"]), "lon": float(x["longitude"]),
              "date": x["acq_date"], "confidence": x["confidence"]} for x in rows]
    return fires, False

@app.get("/api/fires")
async def fires(days: int = Query(1, ge=1, le=5)):
    data, demo = await get_fires(days)
    return {"demo": demo, "count": len(data), "fires": data}

@app.get("/api/check")
async def check(lat: float, lon: float, radius_km: float = 20, chat_id: str | None = None):
    data, demo = await get_fires(1)
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
