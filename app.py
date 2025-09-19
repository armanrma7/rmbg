# app.py
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
app = FastAPI(title="Remove Background API (Transparent PNG)", version="1.0.0")
logger = logging.getLogger("uvicorn.error")
logging.basicConfig(level=logging.INFO)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Model sessions (Lazy Loading for Memory) ---
SESSIONS: dict[str, object] = {}
DEFAULT_MODEL = os.getenv("DEFAULT_REMBG_MODEL", "birefnet-general")

def get_session(model_name: str = DEFAULT_MODEL) -> object:
    """Lazy load sessions to save memory"""
    if model_name not in SESSIONS:
        SESSIONS[model_name] = new_session(model_name)
    return SESSIONS[model_name]

EXECUTOR = ThreadPoolExecutor(max_workers=min(2, os.cpu_count() or 2))  # Limit workers for memory

# --- Presets ---
PRESETS = {
    "fast": {"model": "u2netp", "max_side": 640, "matting": False},
    "balanced": {"model": "u2net", "max_side": 1280, "matting": False},
    "quality": {"model": DEFAULT_MODEL, "max_side": 2048, "matting": True},
}

CACHE: dict[str, bytes] = {}

# Removed warmup to save memory - models load on first use


# ----------------- Helpers -----------------
def _ensure_rgba(img: Image.Image) -> Image.Image:
    return img.convert("RGBA") if img.mode != "RGBA" else img

def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _refine_alpha(mask: Image.Image, contract: int = 1, expand: int = 2, small_blur: float = 1.0, boost_dark_edges: bool = True) -> Image.Image:
    a = mask.convert("L")
    if contract > 0:
        a = a.filter(ImageFilter.MinFilter(contract * 2 + 1))
    if expand > 0:
        a = a.filter(ImageFilter.MaxFilter(expand * 2 + 1))
    if small_blur > 0:
        a = a.filter(ImageFilter.GaussianBlur(small_blur))

    if boost_dark_edges:
        px = a.load()
        for y in range(a.height):
            for x in range(a.width):
                v = px[x, y]
                if v < 64:
                    px[x, y] = min(255, int(v * 1.4) + 6)
    return a

def _premultiply_and_clean(img: Image.Image) -> Image.Image:
    img = _ensure_rgba(img)
    r, g, b, a = img.split()
    r = ImageChops.multiply(r, a)
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (r, g, b, a))

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
        refine: bool = Query(True),
        apply_despill: bool = Query(True),
        boost_dark_edges: bool = Query(True),
        crop: bool = Query(True),
        crop_margin: int = Query(10, ge=0, le=200),
):
    start = time.perf_counter()
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    cache_key = _hash_bytes(contents + f"{preset}:{size}:{model}:{refine}:{apply_despill}:{boost_dark_edges}:{crop}:{crop_margin}".encode())
    if cache_key in CACHE:
        buf = BytesIO(CACHE[cache_key])
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")

    chosen = model or PRESETS.get(preset, PRESETS["quality"])["model"]

    # Use lazy session loading instead of checking SESSIONS dict
    try:
        session = get_session(chosen)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Model '{chosen}' failed to load: {str(e)}")
    preset_cfg = PRESETS.get(preset, PRESETS["quality"])
    max_side = preset_cfg["max_side"]

    original = Image.open(BytesIO(contents)).convert("RGBA")
    ow, oh = original.size

    if size == "preview":
        proc_max_side = min(512, max_side)
    elif size == "full":
        proc_max_side = max(ow, oh)
    else:
        proc_max_side = max_side

    # Memory optimization: limit processing size
    scale = 1.0
    proc_img = original
    max_processing = min(proc_max_side, 1200)  # Cap at 1200px for memory
    if max(ow, oh) > max_processing and max_processing > 0:
        scale = max_processing / float(max(ow, oh))
        new_w = int(round(ow * scale))
        new_h = int(round(oh * scale))
        proc_img = original.resize((new_w, new_h), Image.LANCZOS)

    data = BytesIO()
    proc_img.save(data, format="PNG", optimize=True)  # Memory optimization
    data_bytes = data.getvalue()

    # Use the already loaded session
    loop = asyncio.get_running_loop()
    removed_bytes = await loop.run_in_executor(
        EXECUTOR,
        lambda: remove(
            data_bytes,
            session=session,
            only_mask=False,
            alpha_matting=True,
            alpha_matting_foreground_threshold=245,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=3,
        )
    )

    removed = Image.open(BytesIO(removed_bytes)).convert("RGBA")
    r, g, b, a = removed.split()
    if scale != 1.0:
        a = a.resize((ow, oh), Image.NEAREST)

    if refine:
        a = _refine_alpha(a, contract=1, expand=2, small_blur=1.0, boost_dark_edges=boost_dark_edges)

    out = Image.new("RGBA", original.size, (0,0,0,0))
    out = Image.composite(original, out, a)

    if apply_despill:
        out = _premultiply_and_clean(out)

    if crop:
        out = _crop_with_margin(out, crop_margin)

    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True, compress_level=6)  # Memory optimization
    buf.seek(0)
    CACHE[cache_key] = buf.getvalue()

    # Clear large objects from memory
    del original, removed, out, a

    return StreamingResponse(buf, media_type="image/png")


@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
