/**
 * Image Workshop standalone page (/image-workshop) — the same panel the
 * Character Studio lightbox uses, hosted as its own screen and reachable as a
 * mode under Tools in the main project screen.
 */
import { Link } from 'react-router-dom';
import { ChevronLeft, Palette } from 'lucide-react';
import ImageWorkshopPanel from './ImageWorkshopPanel';

export default function ImageWorkshopPage() {
  return (
    <div style={{ minHeight: '100vh', background: '#0b0e13', color: '#e6e9ee' }}>
      <div style={{ maxWidth: 1320, margin: '0 auto', padding: 'clamp(12px, 3vw, 24px)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18, flexWrap: 'wrap' }}>
          <Link to="/" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13, color: '#9aa4b2', textDecoration: 'none' }}>
            <ChevronLeft size={16} /> Home
          </Link>
          <Palette size={20} color="#7c9dff" />
          <h1 style={{ fontSize: 20, margin: 0 }}>Image Workshop</h1>
          <span style={{ fontSize: 12, color: '#7f8a99' }}>experiment with any model · one shared gallery</span>
        </div>
        <ImageWorkshopPanel />
      </div>
    </div>
  );
}
