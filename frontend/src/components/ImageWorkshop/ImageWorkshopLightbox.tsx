/**
 * Image Workshop lightbox — the modal wrapper used from the Character Studio
 * header ("🎨 Image Workshop"). Full-screen on phones, centered card on desktop.
 */
import { createPortal } from 'react-dom';
import { X, Palette } from 'lucide-react';
import ImageWorkshopPanel from './ImageWorkshopPanel';
import type { WsRefT } from './imageWorkshopApi';

interface Props { onClose: () => void; seedReferences?: WsRefT[]; }

export default function ImageWorkshopLightbox({ onClose, seedReferences }: Props) {
  return createPortal(
    <div onClick={onClose}
      style={{ position: 'fixed', inset: 0, zIndex: 99990, background: 'rgba(4,6,10,0.82)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 'clamp(0px, 2vw, 24px)' }}>
      <div onClick={(e) => e.stopPropagation()}
        style={{ background: '#0e1218', border: '1px solid #262c38', borderRadius: 14, width: 'min(1240px, 100%)', height: 'min(96vh, 100%)', display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '0 24px 80px rgba(0,0,0,0.6)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: '1px solid #262c38', flexShrink: 0 }}>
          <Palette size={18} color="#7c9dff" />
          <b style={{ fontSize: 15, color: '#e6e9ee' }}>Image Workshop</b>
          <span style={{ fontSize: 11.5, color: '#7f8a99' }}>model playground · shared gallery</span>
          <button onClick={onClose} title="Close"
            style={{ marginLeft: 'auto', background: '#1c2230', border: '1px solid #2a2f3a', color: '#cdd5e0', borderRadius: 8, width: 34, height: 34, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <X size={18} />
          </button>
        </div>
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 'clamp(12px, 2.5vw, 20px)' }}>
          <ImageWorkshopPanel seedReferences={seedReferences} />
        </div>
      </div>
    </div>, document.body);
}
