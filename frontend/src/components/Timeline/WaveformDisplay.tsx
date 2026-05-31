import { useRef, useEffect, useCallback } from 'react';
import WaveSurfer from 'wavesurfer.js';
import axios from 'axios';
import { useAppStore } from '@/store';
import { getAssetFileUrl } from '@/api/client';

interface WaveformDisplayProps {
  zoom: number;
  duration: number;
  setDuration: (duration: number) => void;
  playbackPosition: number;
  setPlaybackPosition: (position: number) => void;
  isPlaying: boolean;
  volume?: number; // 0-1 linear, controls narration playback volume
}

export default function WaveformDisplay({
  zoom,
  duration,
  setDuration,
  playbackPosition,
  setPlaybackPosition,
  isPlaying,
  volume = 1.0,
}: WaveformDisplayProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const wavesurferRef = useRef<WaveSurfer | null>(null);
  const isReadyRef = useRef(false);
  // Track whether position changes come from wavesurfer (playback/click)
  // vs from the external slider, to avoid feedback loops
  const isInternalUpdateRef = useRef(false);
  const currentProject = useAppStore(s => s.currentProject);
  const assets = useAppStore(s => s.assets);

  // Find the music asset URL
  const musicAsset = (assets || []).find(a => a.asset_type === 'music');
  const audioUrl = musicAsset && currentProject
    ? getAssetFileUrl(currentProject.id, musicAsset.id)
    : '';

  // Stable callback for position updates from wavesurfer
  const handleTimeUpdate = useCallback((currentTime: number) => {
    isInternalUpdateRef.current = true;
    setPlaybackPosition(currentTime);
    // Reset the flag after a tick so the seekTo effect doesn't fire
    requestAnimationFrame(() => {
      isInternalUpdateRef.current = false;
    });
  }, [setPlaybackPosition]);

  useEffect(() => {
    if (!containerRef.current) return;

    isReadyRef.current = false;

    const wavesurfer = WaveSurfer.create({
      container: containerRef.current,
      waveColor: '#6b7280',
      progressColor: '#3b82f6',
      barWidth: 2,
      barRadius: 3,
      barGap: 2,
      height: 'auto',
      normalize: true,
    });

    wavesurferRef.current = wavesurfer;

    // Fetch audio via axios (XMLHttpRequest) to avoid browser extension
    // interference with native fetch() — e.g. Norton Safe Connect blocks
    // wavesurfer's internal fetch calls.
    if (audioUrl) {
      let cancelled = false;
      axios.get(audioUrl, { responseType: 'blob' })
        .then((response) => {
          if (cancelled) return;
          wavesurfer.loadBlob(response.data);
        })
        .catch((err) => {
          console.error('[WaveformDisplay] Failed to load audio via axios:', err);
          // Fallback: try native wavesurfer load
          if (!cancelled) {
            try {
              wavesurfer.load(audioUrl);
            } catch (fallbackErr) {
              console.error('[WaveformDisplay] Fallback load also failed:', fallbackErr);
            }
          }
        });

      wavesurfer.on('ready', () => {
        isReadyRef.current = true;
        setDuration(wavesurfer.getDuration());
      });

      wavesurfer.on('timeupdate', handleTimeUpdate);

      // When user clicks on waveform to seek
      wavesurfer.on('seeking', (currentTime) => {
        isInternalUpdateRef.current = true;
        setPlaybackPosition(currentTime);
        requestAnimationFrame(() => {
          isInternalUpdateRef.current = false;
        });
      });

      wavesurfer.on('error', (err) => {
        console.error('[WaveformDisplay] WaveSurfer error:', err);
      });

      return () => {
        cancelled = true;
        isReadyRef.current = false;
        wavesurfer.destroy();
      };
    } else {
      // No audio URL — still need cleanup for the empty wavesurfer instance
      return () => {
        isReadyRef.current = false;
        wavesurfer.destroy();
      };
    }
  }, [currentProject?.id, audioUrl, setDuration, handleTimeUpdate, setPlaybackPosition]);

  // Zoom is handled by the parent container width scaling.
  // Wavesurfer's ResizeObserver auto-re-renders when its container resizes.
  // We trigger a manual re-render on zoom change as a fallback.
  useEffect(() => {
    if (wavesurferRef.current && isReadyRef.current) {
      try {
        // Force wavesurfer to re-measure its container after zoom change
        const wrapper = wavesurferRef.current.getWrapper();
        if (wrapper) {
          // Trigger resize by briefly toggling display (wavesurfer picks it up)
          wrapper.style.display = 'none';
          // eslint-disable-next-line no-unused-expressions
          wrapper.offsetHeight; // force reflow
          wrapper.style.display = '';
        }
      } catch {
        // wavesurfer may throw if not ready
      }
    }
  }, [zoom]);

  // Handle external playback position changes (from the slider) — skip
  // if the change came from wavesurfer itself (timeupdate/seeking)
  useEffect(() => {
    if (isInternalUpdateRef.current) return;
    if (wavesurferRef.current && isReadyRef.current && duration > 0) {
      try {
        const currentWsTime = wavesurferRef.current.getCurrentTime();
        // Only seek if the difference is meaningful (> 0.1s) to avoid jitter
        if (Math.abs(currentWsTime - playbackPosition) > 0.1) {
          wavesurferRef.current.seekTo(playbackPosition / duration);
        }
      } catch {
        // ignore seek errors
      }
    }
  }, [playbackPosition, duration]);

  // Apply narration volume changes in real-time
  useEffect(() => {
    if (wavesurferRef.current && isReadyRef.current) {
      try {
        wavesurferRef.current.setVolume(volume);
      } catch {
        // ignore volume errors
      }
    }
  }, [volume]);

  // Handle play/pause — only when audio is loaded
  useEffect(() => {
    if (wavesurferRef.current && isReadyRef.current) {
      try {
        if (isPlaying) {
          wavesurferRef.current.play();
        } else {
          wavesurferRef.current.pause();
        }
      } catch {
        // ignore play/pause errors when no audio
      }
    }
  }, [isPlaying]);

  return (
    <div ref={containerRef} className="w-full h-full">
      {!audioUrl && (
        <div className="w-full h-full flex items-center justify-center text-gray-500 text-sm">
          Upload a music file to see waveform
        </div>
      )}
    </div>
  );
}
