from io import BytesIO
import asyncio
import os
import logging
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageChops, ImageFilter, ImageOps
from rembg import remove, new_session

# --- App setup ---
app = FastAPI(title="Remove Background API", version="1.0.0")
logger = logging.getLogger("uvicorn.error")
logging.basicConfig(level=logging.INFO)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Root route for DigitalOcean health check ---
@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "ok"}

# --- Model sessions ---
SESSIONS: dict[str, object] = {}
DEFAULT_MODEL = os.getenv("DEFAULT_REMBG_MODEL", "birefnet-general")

def get_session(model_name: str = DEFAULT_MODEL) -> object:
    if model_name not in SESSIONS:
        SESSIONS[model_name] = new_session(model_name)
    return SESSIONS[model_name]

EXECUTOR = ThreadPoolExecutor(max_workers=min(2, os.cpu_count() or 2))

# --- Presets ---
PRESETS = {
    "fast": {"model": "u2netp", "max_side": 640, "matting": False},
    "balanced": {"model": "u2net", "max_side": 1280, "matting": False},
    "quality": {"model": DEFAULT_MODEL, "max_side": 2048, "matting": True},
}

CACHE: dict[str, bytes] = {}

# ----------------- Helpers -----------------
def _ensure_rgba(img: Image.Image) -> Image.Image:
    return img.convert("RGBA") if img.mode != "RGBA" else img

def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _crop_with_margin(img: Image.Image, margin: int = 10) -> Image.Image:
    alpha = img.split()[-1]
    bbox = alpha.getbbox()
    if not bbox:
        return img
    left, top, right, bottom = bbox
    left = max(left - margin, 0)
    top = max(top - margin, 0)
    right = min(right + margin, img.width)
    bottom = min(bottom + margin, img.height)
    cropped = img.crop((left, top, right, bottom))
    return ImageOps.expand(cropped, border=margin, fill=(0, 0, 0, 0))

# ----------------- Main Endpoint -----------------
@app.post("/remove-bg", response_class=StreamingResponse)
async def remove_bg(
        file: UploadFile = File(...),
        preset: str = Query("quality", pattern="^(fast|balanced|quality)$"),
        size: str = Query("auto", pattern="^(auto|preview|full)$"),
        model: Optional[str] = Query(None),
        crop: bool = Query(True),
        crop_margin: int = Query(10, ge=0, le=200),
):
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    cache_key = _hash_bytes(contents + f"{preset}:{size}:{model}:{crop}:{crop_margin}".encode())
    if cache_key in CACHE:
        buf = BytesIO(CACHE[cache_key])
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")

    chosen_model = model or PRESETS.get(preset, PRESETS["quality"])["model"]
    try:
        session = get_session(chosen_model)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Model '{chosen_model}' failed: {str(e)}")

    preset_cfg = PRESETS.get(preset, PRESETS["quality"])
    max_side = preset_cfg["max_side"]

    original = Image.open(BytesIO(contents)).convert("RGBA")
    ow, oh = original.size

    proc_max_side = {
        "preview": min(512, max_side),
        "full": max(ow, oh),
        "auto": max_side
    }[size]

    scale = 1.0
    proc_img = original
    if max(ow, oh) > proc_max_side:
        scale = proc_max_side / float(max(ow, oh))
        new_w = int(round(ow * scale))
        new_h = int(round(oh * scale))
        proc_img = original.resize((new_w, new_h), Image.LANCZOS)

    data = BytesIO()
    proc_img.save(data, format="PNG", optimize=True)
    data_bytes = data.getvalue()

    loop = asyncio.get_running_loop()
    removed_bytes = await loop.run_in_executor(
        EXECUTOR,
        lambda: remove(data_bytes, session=session)
    )

    removed = Image.open(BytesIO(removed_bytes)).convert("RGBA")
    if scale != 1.0:
        removed = removed.resize((ow, oh), Image.LANCZOS)

    if crop:
        removed = _crop_with_margin(removed, crop_margin)

    buf = BytesIO()
    removed.save(buf, format="PNG", optimize=True, compress_level=6)
    buf.seek(0)
    CACHE[cache_key] = buf.getvalue()

    # Free memory
    del original, removed

    return StreamingResponse(buf, media_type="image/png")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
