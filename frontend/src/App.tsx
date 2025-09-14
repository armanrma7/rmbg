import React, { useEffect, useMemo, useRef, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || localStorage.getItem('apiBase') || 'http://localhost:8080'

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
  const [maxSide, setMaxSide] = useState<number>(() => Number(localStorage.getItem('maxSide')) || 1600)
  const [preset, setPreset] = useState<'fast'|'balanced'|'quality'>(() => (localStorage.getItem('preset') as any) || 'quality')
  const [apiBase, setApiBase] = useState<string>(API_BASE)
  const [theme, setTheme] = useState<'light'|'dark'>(() => (localStorage.getItem('theme') as any) || 'light')
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

  useEffect(() => {
    localStorage.setItem('maxSide', String(maxSide))
    localStorage.setItem('preset', preset)
    localStorage.setItem('apiBase', apiBase)
    localStorage.setItem('theme', theme)
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [maxSide, preset, apiBase, theme])

  const onSubmit = async () => {
    if (!file) return
    setIsLoading(true)
    setResultUrl(null)
    try {
      const prepared = await downscaleImage(file, maxSide)
      const form = new FormData()
      form.append('file', prepared)
      const params = new URLSearchParams({
        crop: String(crop),
        preset,
        max_side: String(maxSide),
        refine: 'true',
        boost_dark_edges: 'true',
        despill_edges: 'true',
        edge_contract: '2',
        edge_feather: '1.6'
      })
      const url = `${apiBase}/remove-bg?${params.toString()}`
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
    <div className="max-w-6xl mx-auto p-6">
      <header className="flex flex-wrap items-center gap-3 justify-between mb-6">
        <h1 className="text-2xl font-semibold">Remove Background</h1>
        <div className="flex flex-wrap gap-2 items-center">
          <input className="border rounded px-2 py-1 text-sm w-64" placeholder="API base" value={apiBase} onChange={e=>setApiBase(e.target.value)} />
          <select className="border rounded px-2 py-1 text-sm" value={preset} onChange={e=>setPreset(e.target.value as any)}>
            <option value="fast">Fast</option>
            <option value="balanced">Balanced</option>
            <option value="quality">Quality</option>
          </select>
          <label className="flex items-center gap-2 text-sm">
            <span>Max side</span>
            <input className="border rounded px-2 py-1 w-24 text-sm" type="number" min={512} max={4096} value={maxSide} onChange={e=>setMaxSide(Number(e.target.value||0))} />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={crop} onChange={e=>setCrop(e.target.checked)} /> Crop
          </label>
          <button onClick={()=>setTheme(t=> t==='dark'?'light':'dark')} className="px-2 py-1 border rounded text-sm">
            {theme==='dark' ? 'Light' : 'Dark'}
          </button>
          <button onClick={onSubmit} disabled={!canSubmit} className="px-3 py-2 bg-indigo-600 text-white rounded disabled:opacity-50">
            {isLoading ? 'Processing…' : 'Remove background'}
          </button>
          {file && <button onClick={onRemove} className="px-3 py-2 border rounded">Clear</button>}
        </div>
      </header>

      <div className="grid md:grid-cols-2 gap-6">
        <div>
          <h3 className="font-medium mb-2">Input</h3>
          <div className="border rounded-lg min-h-[260px] bg-white dark:bg-slate-800 shadow-card flex items-center justify-center p-4">
            {previewUrl ? (
              <img src={previewUrl} className="max-w-full max-h-[480px]" />
            ) : (
              <label className="flex flex-col items-center justify-center gap-2 text-slate-500 cursor-pointer">
                <input ref={inputRef} type="file" accept="image/*" onChange={onSelectFile} className="hidden" />
                <span className="text-sm">Click to upload image</span>
              </label>
            )}
          </div>
        </div>
        <div>
          <h3 className="font-medium mb-2">Result</h3>
          <div className="border rounded-lg min-h-[260px] bg-[conic-gradient(#eee_0_25%,transparent_0_50%)] dark:bg-[conic-gradient(#1f2937_0_25%,transparent_0_50%)] [background-size:20px_20px] shadow-card flex items-center justify-center p-4">
            {resultUrl ? (
              <img src={resultUrl} className="max-w-full max-h-[480px]" />
            ) : (
              <span className="text-slate-500 text-sm">No result yet</span>
            )}
          </div>
          {resultUrl && (
            <div className="mt-3 flex gap-2">
              <a href={resultUrl} download={`output.png`} className="px-3 py-2 bg-green-600 text-white rounded">Download PNG</a>
              <button onClick={()=>{ URL.revokeObjectURL(resultUrl); setResultUrl(null) }} className="px-3 py-2 border rounded">Reset result</button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


