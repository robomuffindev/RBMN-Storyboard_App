/**
 * useBackingTrackPlayer — Web Audio API playback for backing tracks
 *
 * Loads backing track audio files, creates AudioBufferSourceNodes,
 * and syncs playback to WaveSurfer's play/pause/seek via the Zustand store.
 *
 * Each backing track has:
 *   - start_time / end_time: timeline position (seconds)
 *   - trim_start / trim_end: where to start/end within the audio file
 *   - volume_db: gain in decibels
 *   - fade_in_sec / fade_out_sec: fade durations
 */
import { useEffect, useRef, useCallback } from 'react';
import { useAppStore } from '@/store';

export interface BackingTrackData {
  id: string;
  rel_path: string;
  start_time: number;
  end_time: number;
  trim_start: number;
  trim_end: number;
  volume_db: number;
  fade_in_sec: number;
  fade_out_sec: number;
}

/**
 * Build the correct URL for a backing track file.
 * Files live at {project_dir}/{project_id}/assets/backing_tracks/filename.mp3
 * The /api/files/ endpoint resolves relative to project_dir, so the URL must
 * include the project_id prefix.
 */
function buildTrackUrl(projectId: string, relPath: string): string {
  return `/api/files/${projectId}/${relPath}`;
}

interface ActiveSource {
  trackId: string;
  source: AudioBufferSourceNode;
  gainNode: GainNode;
}

export function useBackingTrackPlayer(tracks: BackingTrackData[], projectId: string) {
  const audioContextRef = useRef<AudioContext | null>(null);
  const buffersRef = useRef<Map<string, AudioBuffer>>(new Map());
  const activeSourcesRef = useRef<ActiveSource[]>([]);
  const masterGainRef = useRef<GainNode | null>(null); // single master gain for live volume control
  const lastPositionRef = useRef<number>(0);
  const isPlayingRef = useRef<boolean>(false);
  const startedAtRef = useRef<number>(0); // AudioContext.currentTime when playback started
  const rafRef = useRef<number>(0);

  const { isPlaying, playbackPosition, backingMasterVolume } = useAppStore();

  // Get or create AudioContext + master gain node
  const getContext = useCallback(() => {
    if (!audioContextRef.current || audioContextRef.current.state === 'closed') {
      audioContextRef.current = new AudioContext();
      // Create a master gain node for live volume control
      const mg = audioContextRef.current.createGain();
      mg.gain.value = backingMasterVolume;
      mg.connect(audioContextRef.current.destination);
      masterGainRef.current = mg;
    }
    return audioContextRef.current;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Apply master volume changes in real-time
  useEffect(() => {
    if (masterGainRef.current) {
      masterGainRef.current.gain.value = backingMasterVolume;
    }
  }, [backingMasterVolume]);

  // Track failed loads to avoid infinite retry loops
  const failedLoadsRef = useRef<Set<string>>(new Set());

  // Load audio buffers for all tracks
  useEffect(() => {
    if (!tracks || tracks.length === 0 || !projectId) return;

    const ctx = getContext();
    let cancelled = false;

    tracks.forEach(async (track) => {
      if (buffersRef.current.has(track.id)) return; // already loaded
      if (failedLoadsRef.current.has(track.id)) return; // already failed, don't retry
      if (!track.rel_path) return; // no file path

      try {
        const url = buildTrackUrl(projectId, track.rel_path);
        console.debug(`[BackingTrackPlayer] Loading "${track.rel_path}" from ${url}`);
        const response = await fetch(url);
        if (!response.ok) {
          console.error(`[BackingTrackPlayer] Failed to fetch ${track.rel_path}: ${response.status}`);
          failedLoadsRef.current.add(track.id);
          return;
        }
        const arrayBuffer = await response.arrayBuffer();
        if (cancelled) return;

        const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
        if (cancelled) return;

        buffersRef.current.set(track.id, audioBuffer);
        // Remove from failed set in case it was there from a previous attempt
        failedLoadsRef.current.delete(track.id);
        console.debug(`[BackingTrackPlayer] Loaded "${track.rel_path}" (${audioBuffer.duration.toFixed(1)}s)`);
      } catch (err) {
        console.error(`[BackingTrackPlayer] Error loading ${track.rel_path}:`, err);
        failedLoadsRef.current.add(track.id);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [tracks, projectId, getContext]);

  // Stop all active sources
  const stopAll = useCallback(() => {
    activeSourcesRef.current.forEach(({ source }) => {
      try {
        source.stop();
      } catch {
        // may already be stopped
      }
    });
    activeSourcesRef.current = [];
  }, []);

  // Start playing backing tracks at a given timeline position
  const startPlayback = useCallback(async (timelinePos: number) => {
    const ctx = getContext();
    if (ctx.state === 'suspended') {
      await ctx.resume();
    }

    stopAll();

    const now = ctx.currentTime;
    startedAtRef.current = now;
    lastPositionRef.current = timelinePos;

    tracks.forEach((track) => {
      const buffer = buffersRef.current.get(track.id);
      if (!buffer) return;

      // Check if this track is relevant at the current timeline position
      // Track plays on timeline from track.start_time to track.end_time
      const trackDuration = track.end_time - track.start_time;
      if (trackDuration <= 0) return;

      // Where in the track's timeline range are we?
      if (timelinePos >= track.end_time) return; // past this track
      // If we haven't reached this track yet, schedule it for the future
      const delay = Math.max(0, track.start_time - timelinePos);
      const trackElapsed = Math.max(0, timelinePos - track.start_time);

      // Calculate the audio file offset
      const trimDuration = (track.trim_end > 0 ? track.trim_end : buffer.duration) - track.trim_start;
      if (trimDuration <= 0) return;

      // Map timeline position within the track to audio file position
      // The track may be shorter/longer than the audio file
      const audioOffset = track.trim_start + trackElapsed;
      const remainingAudio = (track.trim_end > 0 ? track.trim_end : buffer.duration) - audioOffset;
      if (remainingAudio <= 0) return;

      // Create source and gain nodes
      const source = ctx.createBufferSource();
      source.buffer = buffer;

      const gainNode = ctx.createGain();
      // Convert dB to linear gain
      const linearGain = Math.pow(10, track.volume_db / 20);

      // Set up fade in/out
      const startTime = now + delay;
      const playDuration = Math.min(remainingAudio, track.end_time - timelinePos - delay);
      if (playDuration <= 0) return;

      // Initial gain (handle fade-in if starting from the beginning of the track)
      if (track.fade_in_sec > 0 && trackElapsed < track.fade_in_sec) {
        // We're in the fade-in region
        const fadeRemaining = track.fade_in_sec - trackElapsed;
        const currentFadeLevel = trackElapsed / track.fade_in_sec;
        gainNode.gain.setValueAtTime(linearGain * currentFadeLevel, startTime);
        gainNode.gain.linearRampToValueAtTime(linearGain, startTime + fadeRemaining);
      } else if (track.fade_in_sec > 0 && delay > 0) {
        // Track hasn't started yet, will need full fade-in
        gainNode.gain.setValueAtTime(0, startTime);
        gainNode.gain.linearRampToValueAtTime(linearGain, startTime + track.fade_in_sec);
      } else {
        gainNode.gain.setValueAtTime(linearGain, startTime);
      }

      // Fade out
      if (track.fade_out_sec > 0) {
        const fadeOutStart = startTime + playDuration - track.fade_out_sec;
        if (fadeOutStart > startTime) {
          gainNode.gain.setValueAtTime(linearGain, fadeOutStart);
          gainNode.gain.linearRampToValueAtTime(0, startTime + playDuration);
        }
      }

      source.connect(gainNode);
      // Route through master gain for live volume control
      gainNode.connect(masterGainRef.current || ctx.destination);

      source.start(startTime, audioOffset, playDuration);

      activeSourcesRef.current.push({
        trackId: track.id,
        source,
        gainNode,
      });
    });
  }, [tracks, getContext, stopAll]);

  // Handle play/pause state changes
  useEffect(() => {
    if (!tracks || tracks.length === 0) return;

    if (isPlaying && !isPlayingRef.current) {
      // Started playing
      isPlayingRef.current = true;
      startPlayback(playbackPosition);
    } else if (!isPlaying && isPlayingRef.current) {
      // Stopped playing
      isPlayingRef.current = false;
      stopAll();
    }
  }, [isPlaying, tracks, startPlayback, stopAll, playbackPosition]);

  // Handle seek (position changes while playing)
  useEffect(() => {
    if (!isPlaying || !tracks || tracks.length === 0) return;

    // Only re-trigger if the position jump is significant (>0.5s)
    // Normal playback increments are small (~0.01-0.05s per frame)
    const expectedPosition = lastPositionRef.current +
      (audioContextRef.current ? audioContextRef.current.currentTime - startedAtRef.current : 0);
    const drift = Math.abs(playbackPosition - expectedPosition);

    if (drift > 0.5) {
      // User seeked — restart playback from new position
      startPlayback(playbackPosition);
    }
  }, [playbackPosition, isPlaying, tracks, startPlayback]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopAll();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
        audioContextRef.current.close().catch(() => {});
      }
    };
  }, [stopAll]);
}
