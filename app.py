from io import BytesIO
import asyncio
import os
import logging
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageChops, ImageFilter, ImageOps
from rembg import remove, new_session

app = FastAPI(title="Remove Background API", version="8.2.0")
logger = logging.getLogger("uvicorn.error")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sessions
SESSIONS = {
    "u2netp": new_session("u2netp"),            # fastest
    "u2net": new_session("u2net"),              # balanced
    "isnet-general-use": new_session("isnet-general-use"),  # best for objects
    "u2net_human_seg": new_session("u2net_human_seg"),
}
DEFAULT_SESSION = SESSIONS["isnet-general-use"]  # best general object model

EXECUTOR = ThreadPoolExecutor(max_workers=os.cpu_count() or 4)

# Presets
PRESETS = {
    "fast": {"model": "u2netp", "max_side": 640, "matting": False},
    "balanced": {"model": "u2net", "max_side": 1280, "matting": False},
    "quality": {"model": "isnet-general-use", "max_side": 2048, "matting": True},
}

# Cache
CG_CACHE: dict[str, bytes] = {}

# Warmup
@app.on_event("startup")
async def warmup():
    tiny = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    buf = BytesIO()
    tiny.save(buf, format="PNG")
    await asyncio.to_thread(remove, buf.getvalue(), session=DEFAULT_SESSION)

# Helpers
def _ensure_rgba(img: Image.Image) -> Image.Image:
    return img.convert("RGBA") if img.mode != "RGBA" else img

def get_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def refine_alpha(mask: Image.Image, blur_radius=3, contract=1, expand=3) -> Image.Image:
    """Refine alpha edges like remove.bg for smoother borders"""
    mask = mask.convert("L")
    if contract > 0:
        mask = mask.filter(ImageFilter.MinFilter(contract * 2 + 1))
    if expand > 0:
        mask = mask.filter(ImageFilter.MaxFilter(expand * 2 + 1))
    if blur_radius > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(blur_radius))
    return mask

def despill(image: Image.Image) -> Image.Image:
    """Remove color spill/halo around edges"""
    image = _ensure_rgba(image)
    r, g, b, a = image.split()
    base = Image.new("RGBA", image.size, (0, 0, 0, 0))
    r = ImageChops.multiply(r, a)
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (r, g, b, a))

def crop_with_margin(image: Image.Image, margin=10) -> Image.Image:
    alpha = image.split()[-1]
    bbox = alpha.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    left = max(left - margin, 0)
    top = max(top - margin, 0)
    right = min(right + margin, image.width)
    bottom = min(bottom + margin, image.height)
    cropped = image.crop((left, top, right, bottom))
    return ImageOps.expand(cropped, border=margin, fill=(0, 0, 0, 0))

# Endpoint
@app.post("/remove-bg", response_class=StreamingResponse)
async def remove_bg(
    file: UploadFile = File(...),
    crop: bool = Query(True),
    crop_margin: int = Query(10, ge=0, le=50),
    preset: str = Query("quality", pattern="^(fast|balanced|quality)$"),
    refine: bool = Query(True),
    apply_despill: bool = Query(True),
):
    try:
        start = time.perf_counter()
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="Empty file uploaded")

        # Cache
        h = get_hash(contents)
        if h in CG_CACHE:
            buffer = BytesIO(CG_CACHE[h])
            buffer.seek(0)
            return StreamingResponse(buffer, media_type="image/png")

        # Preset
        cfg = PRESETS[preset]
        model_name = cfg["model"]
        max_side = cfg["max_side"]
        use_matting = cfg["matting"]

        # Load image
        original = Image.open(BytesIO(contents)).convert("RGBA")
        ow, oh = original.size

        # Resize for speed
        scale = 1.0
        img_proc = original
        if max(ow, oh) > max_side:
            scale = max_side / max(ow, oh)
            img_proc = original.resize((int(ow*scale), int(oh*scale)), Image.LANCZOS)

        # Remove background
        buf_proc = BytesIO()
        img_proc.save(buf_proc, format="PNG")
        session = SESSIONS[model_name]

        loop = asyncio.get_running_loop()
        removed_bytes = await loop.run_in_executor(
            EXECUTOR,
            lambda: remove(
                buf_proc.getvalue(),
                session=session,
                only_mask=False,
                alpha_matting=use_matting,
                alpha_matting_foreground_threshold=240,
                alpha_matting_background_threshold=10,
                alpha_matting_erode_size=2,
            ),
        )

        removed = Image.open(BytesIO(removed_bytes)).convert("RGBA")
        r, g, b, a = removed.split()

        # Upscale alpha
        if scale != 1.0:
            a = a.resize((ow, oh), Image.LANCZOS)

        # Transparent canvas
        out = Image.new("RGBA", original.size, (0, 0, 0, 0))

        # Refine edges
        if refine:
            a = refine_alpha(a, blur_radius=3, contract=1, expand=3)

        # Composite
        out = Image.composite(original, out, a)

        # Despill
        if apply_despill:
            out = despill(out)

        # Crop
        if crop:
            out = crop_with_margin(out, crop_margin)

        # Save & cache
        buffer = BytesIO()
        out.save(buffer, format="PNG")
        buffer.seek(0)
        CG_CACHE[h] = buffer.getvalue()

        logger.info(f"/remove-bg done in {time.perf_counter()-start:.2f}s")
        return StreamingResponse(buffer, media_type="image/png")

    except Exception as e:
        logger.exception("/remove-bg failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok"}
