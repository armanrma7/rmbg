# app.py
from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError
from io import BytesIO
from typing import Optional

app = FastAPI(title="Transparent Background API", version="1.0")

# --- Enable CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helper: crop image with margin ---
def _crop_with_margin(img: Image.Image, margin: int) -> Image.Image:
    bbox = img.getbbox()
    if not bbox:
        return img
    left, upper, right, lower = bbox
    left = max(0, left - margin)
    upper = max(0, upper - margin)
    right = min(img.width, right + margin)
    lower = min(img.height, lower + margin)
    return img.crop((left, upper, right, lower))

# --- Main endpoint ---
@app.post("/remove-bg-tb", response_class=StreamingResponse)
async def remove_bg_tb(
        file: UploadFile = File(...),
        mode: str = Query("fast", pattern="^(fast|base|base-nightly)$"),
        resize: str = Query("static", pattern="^(static|dynamic)$"),
        output_type: str = Query("rgba"),
        threshold: Optional[float] = Query(None, ge=0.0, le=1.0),
        reverse: bool = Query(False),
        crop: bool = Query(True),
        crop_margin: int = Query(10, ge=0, le=200),
):
    # --- Import libraries inside function ---
    try:
        import torch
        from transparent_background import Remover
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="transparent-background or torch not installed. Run: pip install transparent-background torch torchvision"
        )

    # --- Read image ---
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file uploaded")
    try:
        img = Image.open(BytesIO(contents)).convert("RGB")
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="Invalid image file")

    # --- Auto-resize large images for speed ---
    ow, oh = img.size
    max_side = 1024 if mode == "fast" else 2048
    if max(ow, oh) > max_side:
        scale = max_side / float(max(ow, oh))
        new_w, new_h = int(ow * scale), int(oh * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # --- Initialize remover ---
    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        remover = Remover(mode=mode, device=device)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to initialize remover: {e}")

    # --- Run background removal with fallback ---
    try:
        kwargs = {"type": output_type, "reverse": reverse}
        if threshold is not None:
            kwargs["threshold"] = threshold
        out_img = remover.process(img, **kwargs)
    except Exception:
        # fallback to fast CPU mode
        try:
            remover = Remover(mode="fast", device="cpu")
            out_img = remover.process(img, type="rgba", reverse=reverse)
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"transparent-background process failed: {e2}")

    # --- Ensure RGBA ---
    if out_img.mode != "RGBA":
        out_img = out_img.convert("RGBA")

    # --- Crop if requested ---
    if crop:
        out_img = _crop_with_margin(out_img, crop_margin)

    # --- Return PNG ---
    buf = BytesIO()
    out_img.save(buf, format="PNG", optimize=True, compress_level=6)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


# --- Health check endpoint ---
@app.get("/health")
async def health():
    return {"status": "ok"}


# --- Run server ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)
