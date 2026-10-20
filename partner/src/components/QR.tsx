import QRCode from 'qrcode'
import { useEffect, useRef } from 'react'

export default function QR({ text, size = 240 }: { text: string; size?: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => { if (ref.current) QRCode.toCanvas(ref.current, text, { width: size, margin: 1 }) }, [text, size])
  return <canvas ref={ref} className="mx-auto" />
}
