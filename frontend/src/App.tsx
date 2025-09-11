import React, { useMemo, useRef, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8080'

async function downscaleImage(file: File, maxSide: number): Promise<File> {
  const img = new Image()
  const objectUrl = URL.createObjectURL(file)
  img.src = objectUrl
  await new Promise((resolve, reject) => {
    img.onload = resolve
    img.onerror = reject
  })

  const { width, height } = img
  const longest = Math.max(width, height)
  if (longest <= maxSide) {
    URL.revokeObjectURL(objectUrl)
    return file
  }
  const scale = maxSide / longest
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(width * scale)
  canvas.height = Math.round(height * scale)
  const ctx = canvas.getContext('2d')!
  ctx.imageSmoothingEnabled = true
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  URL.revokeObjectURL(objectUrl)

  const blob: Blob = await new Promise((resolve) => canvas.toBlob(b => resolve(b!), 'image/png', 0.92))
  return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.png', { type: 'image/png' })
}

export default function App() {
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [crop, setCrop] = useState(false)
  const [maxSide, setMaxSide] = useState<number>(1024)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const canSubmit = useMemo(() => !!file && !isLoading, [file, isLoading])

  const onSelectFile: React.ChangeEventHandler<HTMLInputElement> = (e) => {
    const f = e.target.files?.[0] || null
    setFile(f)
    setResultUrl(null)
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setPreviewUrl(f ? URL.createObjectURL(f) : null)
  }

  const onRemove = () => {
    setFile(null)
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setPreviewUrl(null)
    setResultUrl(null)
    inputRef.current?.value && (inputRef.current.value = '')
  }

  const onSubmit = async () => {
    if (!file) return
    setIsLoading(true)
    setResultUrl(null)
    try {
      const prepared = await downscaleImage(file, maxSide)
      const form = new FormData()
      form.append('file', prepared)
      const url = `${API_BASE}/remove-bg?crop=${crop ? 'true' : 'false'}`
      const res = await fetch(url, {
        method: 'POST',
        body: form,
      })
      if (!res.ok) throw new Error(`Request failed: ${res.status}`)
      const blob = await res.blob()
      const objectUrl = URL.createObjectURL(blob)
      setResultUrl(objectUrl)
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: 720, margin: '40px auto', padding: 16, fontFamily: 'ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica Neue, Arial' }}>
      <h1 style={{ marginBottom: 8 }}>Remove Background</h1>
      <p style={{ color: '#555', marginTop: 0 }}>Upload an image and get a transparent PNG. Optionally crop the transparent borders.</p>

      <div style={{ display: 'flex', gap: 16, alignItems: 'center', margin: '16px 0', flexWrap: 'wrap' }}>
        <input ref={inputRef} type="file" accept="image/*" onChange={onSelectFile} />
        {file && (
          <button onClick={onRemove} disabled={isLoading}>Clear</button>
        )}
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" checked={crop} onChange={(e) => setCrop(e.target.checked)} />
          Crop transparent borders
        </label>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          Max side:
          <input type="number" min={256} max={4096} value={maxSide} onChange={e => setMaxSide(Number(e.target.value || 0))} style={{ width: 90 }} />
        </label>
        <button onClick={onSubmit} disabled={!canSubmit}>
          {isLoading ? 'Processing…' : 'Remove background'}
        </button>
      </div>

      <div style={{ display: 'grid', gap: 16, gridTemplateColumns: '1fr 1fr' }}>
        <div>
          <h3>Input</h3>
          <div style={{ border: '1px solid #ddd', borderRadius: 8, padding: 8, minHeight: 260, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#fafafa' }}>
            {previewUrl ? (
              <img src={previewUrl} style={{ maxWidth: '100%', maxHeight: 400 }} />
            ) : (
              <span style={{ color: '#999' }}>No image selected</span>
            )}
          </div>
        </div>
        <div>
          <h3>Result</h3>
          <div style={{ border: '1px solid #ddd', borderRadius: 8, padding: 8, minHeight: 260, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'repeating-conic-gradient(#eee 0% 25%, transparent 0% 50%) 50% / 20px 20px' }}>
            {resultUrl ? (
              <img src={resultUrl} style={{ maxWidth: '100%', maxHeight: 400 }} />
            ) : (
              <span style={{ color: '#999' }}>No result yet</span>
            )}
          </div>
          {resultUrl && (
            <div style={{ marginTop: 8 }}>
              <a href={resultUrl} download={`output.png`}>
                <button>Download PNG</button>
              </a>
            </div>
          )}
        </div>
      </div>

      <p style={{ marginTop: 24, color: '#777', fontSize: 12 }}>Backend: {API_BASE}</p>
    </div>
  )
}


