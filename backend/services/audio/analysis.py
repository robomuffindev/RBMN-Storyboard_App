"""
Audio Analysis Pipeline

Provides stem separation, transcription, and section detection.
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


# ── GPU Detection for Demucs (PyTorch / CUDA) ───────────────────────────
# Demucs uses PyTorch under the hood.  If CUDA is available we pass
# `-d cuda` to the CLI so stem separation runs on the GPU.  Detection
# is cached after the first call.

class _DemucsDevice:
    """Detect whether CUDA is available for Demucs stem separation."""

    def __init__(self):
        self._detected = False
        self.device: str = "cpu"

    def detect(self):
        if self._detected:
            return
        self._detected = True
        try:
            import torch
            if torch.cuda.is_available():
                self.device = "cuda"
                logger.info("Demucs GPU: CUDA available — will use GPU for stem separation")
            else:
                logger.info("Demucs GPU: CUDA not available — using CPU")
        except ImportError:
            # PyTorch not installed (Demucs won't work either, but be safe)
            logger.info("Demucs GPU: PyTorch not found — using CPU")

    def get_device_flags(self) -> list[str]:
        """Return CLI flags for Demucs device selection."""
        self.detect()
        return ["-d", self.device]


_demucs_device = _DemucsDevice()


class AudioAnalyzer:
    """
    Combined audio analysis pipeline.

    Provides stem separation (Demucs), transcription (WhisperX), and
    section detection (allin1).
    """

    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize audio analyzer.

        Args:
            cache_dir: Directory for caching stems and results (defaults to OS temp dir)
        """
        if cache_dir is None:
            cache_dir = str(Path(tempfile.gettempdir()) / "audio_analysis")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"AudioAnalyzer initialized with cache: {self.cache_dir}")

    def analyze_full(
        self,
        audio_path: str,
        whisper_mode: str = "local",
        whisper_remote_url: Optional[str] = None,
        whisper_comfyui_url: Optional[str] = None,
        initial_text: Optional[str] = None,
        whisper_model: str = "large-v2",
        whisper_language: str = "English",
    ) -> Dict[str, Any]:
        """
        Run full analysis pipeline: stem separation → transcription → section detection.

        Args:
            audio_path: Path to input audio
            whisper_mode: "local", "remote", or "comfyui"
            whisper_remote_url: Remote Whisper server URL (if mode="remote")
            whisper_comfyui_url: ComfyUI server URL (if mode="comfyui")
            initial_text: Optional lyrics/script to help guide WhisperX transcription

        Returns:
            Dict with:
                - stems: dict of stem paths (vocals, drums, bass, other)
                - transcription: list of words with timestamps
                - sections: list of sections with labels and timing

        Raises:
            FileNotFoundError: If audio file not found
            RuntimeError: If processing fails
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info(f"Starting full analysis: {audio_path}")
        if initial_text:
            logger.info(f"Using provided lyrics/script as initial prompt ({len(initial_text)} chars)")

        # Step 1: Stem separation
        stems = self.separate_stems(str(audio_path))
        logger.info(f"Stem separation complete: {stems.keys()}")

        # Step 2: Transcription on vocal stem (with fallback to original audio)
        vocal_stem = stems.get("vocals")
        transcription = []

        def _try_transcribe(audio_for_whisper: str, label: str) -> List[Dict[str, Any]]:
            """Attempt transcription and return word list."""
            stem_size = Path(audio_for_whisper).stat().st_size
            logger.info(f"Sending {label} to Whisper: {audio_for_whisper} ({stem_size / 1024:.0f} KB)")
            if stem_size < 10_000:  # < 10KB is suspiciously small
                logger.warning(f"{label} is only {stem_size} bytes — may be empty/silent")

            if whisper_mode == "comfyui" and whisper_comfyui_url:
                try:
                    return self.transcribe_comfyui(audio_for_whisper, whisper_comfyui_url, initial_text=initial_text, whisper_model=whisper_model, whisper_language=whisper_language)
                except RuntimeError as e:
                    logger.warning(f"ComfyUI Whisper failed for {label} ({e}), falling back to local")
                    try:
                        return self.transcribe_local(audio_for_whisper, initial_text=initial_text, whisper_model=whisper_model)
                    except RuntimeError as e2:
                        logger.warning(f"Local WhisperX also unavailable — skipping transcription: {e2}")
                        return []
            elif whisper_mode == "remote" and whisper_remote_url:
                try:
                    return self.transcribe_remote(audio_for_whisper, whisper_remote_url, initial_text=initial_text, whisper_model=whisper_model)
                except RuntimeError as e:
                    logger.warning(f"Remote Whisper failed for {label} ({e}), falling back to local")
                    try:
                        return self.transcribe_local(audio_for_whisper, initial_text=initial_text, whisper_model=whisper_model)
                    except RuntimeError as e2:
                        logger.warning(f"Local WhisperX also unavailable — skipping transcription: {e2}")
                        return []
            else:
                try:
                    return self.transcribe_local(audio_for_whisper, initial_text=initial_text, whisper_model=whisper_model)
                except RuntimeError as e:
                    logger.warning(f"Local WhisperX not available — skipping transcription: {e}")
                    return []

        def _has_meaningful_words(words: List[Dict[str, Any]]) -> bool:
            """Check if transcription words contain actual text, not garbage."""
            if not words:
                return False
            ascii_word_count = sum(
                1 for w in words
                if any(c.isascii() and c.isalpha() for c in w.get("word", ""))
            )
            ratio = ascii_word_count / len(words) if words else 0
            logger.info(f"Word quality: {ascii_word_count}/{len(words)} words contain ASCII letters ({ratio:.0%})")
            return ratio > 0.3

        # Transcribe using the FULL audio file (not the vocal stem).
        # Whisper was trained on full audio mixes and often produces better
        # results with the original file. Demucs vocal separation can introduce
        # artifacts (phase cancellation, distortion) that degrade transcription
        # accuracy, especially for certain mixing styles.
        transcription = _try_transcribe(str(audio_path), "original audio (full mix)")

        if not _has_meaningful_words(transcription):
            # If full audio failed, try the vocal stem as fallback
            if vocal_stem and Path(vocal_stem).exists():
                logger.warning(
                    "Full audio transcription produced no meaningful text — "
                    "retrying with vocal stem"
                )
                transcription = _try_transcribe(vocal_stem, "vocal stem")
            if not _has_meaningful_words(transcription):
                logger.warning(
                    "Transcription produced no meaningful results — "
                    "Whisper may not be able to transcribe this audio. "
                    "Clearing garbage results."
                )
                transcription = []

        # Step 3: Section detection (clear cache to force re-detection)
        audio_id = Path(audio_path).stem
        section_cache = self.cache_dir / f"{audio_id}_sections.json"
        if section_cache.exists():
            section_cache.unlink()
            logger.info(f"Cleared stale section cache: {section_cache}")
        sections = self.detect_sections(str(audio_path))
        logger.info(f"Section detection complete: {len(sections)} sections")

        result = {
            "stems": stems,
            "transcription": transcription,
            "sections": sections,
        }

        return result

    def separate_stems(self, audio_path: str) -> Dict[str, str]:
        """
        Separate audio into stems using Demucs.

        Uses htdemucs model for good quality with reasonable speed.

        Args:
            audio_path: Path to input audio

        Returns:
            Dict mapping stem names to output paths:
            - vocals
            - drums
            - bass
            - other

        Raises:
            RuntimeError: If separation fails
        """
        audio_path = Path(audio_path)
        audio_id = audio_path.stem
        model_name = "htdemucs"

        # Demucs output structure: <output_dir>/<model_name>/<track_name>/*.wav
        # Check cache — look for both htdemucs and htdemucs_ft
        for cached_model in [model_name, "htdemucs_ft"]:
            stem_dir = self.cache_dir / cached_model / audio_id
            if stem_dir.exists() and list(stem_dir.glob("*.wav")):
                logger.info(f"Using cached stems: {stem_dir}")
                stems = {}
                for stem_file in ["vocals.wav", "drums.wav", "bass.wav", "other.wav"]:
                    stem_path = stem_dir / stem_file
                    if stem_path.exists():
                        stem_name = stem_file.replace(".wav", "")
                        stems[stem_name] = str(stem_path)
                if stems:
                    return stems

        stem_dir = self.cache_dir / model_name / audio_id
        logger.info(f"Separating stems with {model_name}: {audio_path}")

        try:
            cmd = [
                "demucs",
                *_demucs_device.get_device_flags(),
                "-n", model_name,
                "-o", str(self.cache_dir),
                str(audio_path),
            ]

            # Use Popen so we can log progress in real-time and avoid timeout issues
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Read stderr in real time (demucs writes progress there)
            stderr_lines = []
            if process.stderr:
                for line in process.stderr:
                    line = line.rstrip()
                    if line:
                        stderr_lines.append(line)
                        # Log progress lines so user can see activity in console
                        if "%" in line or "Separated" in line or "segment" in line.lower():
                            logger.info(f"Demucs: {line}")
                        else:
                            logger.debug(f"Demucs: {line}")

            process.wait()

            if process.returncode != 0:
                stderr_text = "\n".join(stderr_lines)
                raise RuntimeError(f"Demucs failed (exit {process.returncode}): {stderr_text}")

            # Collect stems
            stems = {}
            for stem_name in ["vocals", "drums", "bass", "other"]:
                stem_path = stem_dir / f"{stem_name}.wav"
                if stem_path.exists():
                    stems[stem_name] = str(stem_path)
                    logger.debug(f"Found stem: {stem_name} → {stem_path}")

            if not stems:
                # Search the entire cache dir for the stems — Demucs may use
                # a slightly different directory structure or sanitized name
                logger.warning(f"Expected stems in {stem_dir}, searching cache...")
                for candidate in self.cache_dir.rglob("vocals.wav"):
                    candidate_dir = candidate.parent
                    logger.info(f"Found stems in: {candidate_dir}")
                    for sn in ["vocals", "drums", "bass", "other"]:
                        sp = candidate_dir / f"{sn}.wav"
                        if sp.exists():
                            stems[sn] = str(sp)
                    if stems:
                        break

            if not stems:
                raise RuntimeError("Demucs produced no output files")

            logger.info(f"Stem separation complete: {list(stems.keys())}")
            return stems

        except RuntimeError:
            raise
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Demucs failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Stem separation error: {e}")

    def transcribe_local(self, audio_path: str, initial_text: Optional[str] = None, whisper_model: str = "base") -> List[Dict[str, Any]]:
        """
        Transcribe audio using local WhisperX.

        Returns word-level timestamps with hallucination defense:
        - VAD filtering
        - no_speech_prob threshold
        - condition_on_previous_text=False

        Args:
            audio_path: Path to audio file
            initial_text: Optional lyrics/script to use as initial_prompt for Whisper

        Returns:
            List of words with timestamps:
            [
                {
                    "word": "hello",
                    "start": 0.1,
                    "end": 0.5,
                    "confidence": 0.95
                },
                ...
            ]

        Raises:
            RuntimeError: If transcription fails
        """
        audio_path = Path(audio_path)
        audio_id = audio_path.stem

        # Check cache
        cache_file = self.cache_dir / f"{audio_id}_transcription.json"
        if cache_file.exists():
            logger.info(f"Using cached transcription: {cache_file}")
            with open(cache_file) as f:
                return json.load(f)

        logger.info(f"Transcribing: {audio_path}")

        try:
            import whisperx
            import torch

            # Auto-detect device: use CUDA if available, otherwise CPU
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            logger.info(f"WhisperX device: {device} (compute_type={compute_type})")

            # Build ASR options — these are passed at model load time
            # (FasterWhisperPipeline.transcribe() no longer accepts them directly)
            asr_options: Dict[str, Any] = {
                "condition_on_previous_text": False,
            }
            if initial_text:
                asr_options["initial_prompt"] = initial_text
                logger.info("Using provided text as Whisper initial_prompt")

            # Load model with ASR options
            logger.info(f"Loading WhisperX model: {whisper_model}")
            model = whisperx.load_model(
                whisper_model, device=device, language="en",
                compute_type=compute_type, asr_options=asr_options,
            )

            # Transcribe with VAD
            audio = whisperx.load_audio(str(audio_path))
            result = model.transcribe(audio, language="en")

            # Align words
            model_a, metadata = whisperx.load_align_model(language_code="en", device=device)
            result = whisperx.align(
                result["segments"],
                model_a,
                metadata,
                audio,
                device=device,
                return_char_alignments=False,
            )

            # Extract word-level timestamps with filtering
            words = []
            for segment in result.get("segments", []):
                for word_obj in segment.get("words", []):
                    # Filter by confidence (VAD)
                    if word_obj.get("confidence", 1.0) > 0.5:
                        words.append({
                            "word": word_obj.get("word", "").strip(),
                            "start": word_obj.get("start", 0),
                            "end": word_obj.get("end", 0),
                            "confidence": word_obj.get("confidence", 1.0),
                        })

            # Cache results
            with open(cache_file, "w") as f:
                json.dump(words, f, indent=2)

            logger.info(f"Transcription complete: {len(words)} words")
            return words

        except ImportError:
            logger.error("WhisperX not installed. Install with: pip install openai-whisper-x")
            raise RuntimeError("WhisperX not available")
        except Exception as e:
            logger.error(f"WhisperX transcription failed: {type(e).__name__}: {e}", exc_info=True)
            raise RuntimeError(f"Transcription error: {e}")

    def transcribe_remote(self, audio_path: str, server_url: str, initial_text: Optional[str] = None, whisper_model: str = "large-v2") -> List[Dict[str, Any]]:
        """
        Transcribe via remote Whisper server.

        Supports:
        - Whisper-WebUI (jhj0517) — queue-based: POST /transcription/, poll GET /task/{id}
        - OpenAI-compatible — POST /v1/audio/transcriptions
        - Generic — POST /asr or /transcribe

        Args:
            audio_path: Path to audio file
            server_url: Remote server URL (e.g., "http://192.168.12.176:7860")
            initial_text: Optional lyrics/script to send as initial_prompt hint
            whisper_model: Whisper model name (e.g., "large-v2", "large-v3")

        Returns:
            List of words with timestamps

        Raises:
            RuntimeError: If transcription fails
        """
        import time
        audio_path = Path(audio_path)
        logger.info(f"Transcribing via remote: {server_url} (model: {whisper_model})")

        try:
            import requests

            base_url = server_url.rstrip('/')

            # Detect which Whisper server we're talking to
            server_type = self._detect_whisper_server_type(base_url)
            logger.info(f"Detected Whisper server type: {server_type}")

            if server_type == "gradio":
                return self._transcribe_gradio_whisper(audio_path, base_url, initial_text, whisper_model=whisper_model)
            elif server_type == "openai":
                return self._transcribe_openai_compatible(audio_path, base_url, initial_text)
            else:
                return self._transcribe_generic(audio_path, base_url, initial_text)

        except Exception as e:
            raise RuntimeError(f"Remote transcription error: {e}")

    @staticmethod
    def _detect_whisper_server_type(base_url: str) -> str:
        """Detect which type of Whisper server is running."""
        import requests

        # Check for Gradio app (Whisper-WebUI is a Gradio app)
        # Gradio apps expose /info and /config endpoints
        try:
            r = requests.get(f"{base_url}/info", timeout=5)
            if r.status_code == 200:
                info = r.json()
                if "version" in info or "api" in str(info).lower():
                    logger.info(f"Detected Gradio app at {base_url}")
                    return "gradio"
        except Exception:
            pass

        # Also check /config (another Gradio indicator)
        try:
            r = requests.get(f"{base_url}/config", timeout=5)
            if r.status_code == 200:
                config = r.json()
                if "components" in config or "dependencies" in config:
                    logger.info(f"Detected Gradio app via /config at {base_url}")
                    return "gradio"
        except Exception:
            pass

        # Check for OpenAI-compatible
        try:
            r = requests.options(f"{base_url}/v1/audio/transcriptions", timeout=5)
            if r.status_code < 500:
                return "openai"
        except Exception:
            pass

        return "generic"

    def _transcribe_gradio_whisper(
        self, audio_path: Path, base_url: str, initial_text: Optional[str],
        whisper_model: str = "large-v2",
    ) -> List[Dict[str, Any]]:
        """
        Transcribe using Whisper-WebUI (jhj0517/Whisper-WebUI) via Gradio client.

        Uses the /transcribe_file API endpoint with gradio_client library.
        Requests SRT output with word-level timestamps enabled, then parses
        the SRT into our standard word-timestamp format.
        """
        try:
            from gradio_client import Client, handle_file
        except ImportError:
            raise RuntimeError(
                "gradio_client not installed. Install with: pip install gradio_client"
            )

        logger.info(f"Connecting to Whisper-WebUI Gradio app at {base_url}")

        try:
            client = Client(base_url, verbose=False)
            logger.info("Connected to Whisper-WebUI Gradio app")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Gradio app at {base_url}: {e}")

        # Build the predict call with the /transcribe_file endpoint
        # Based on the Whisper-WebUI Gradio API spec:
        #   param 0:  files (list of filepath)  — audio files
        #   param 1:  file_format (str)         — output format: "SRT"
        #   param 2:  (bool)                    — add timestamp (True)
        #   param 3:  progress (str)            — model size: "large-v2"
        #   param 4:  (str)                     — compute type: "float16"
        #   param 5:  (int)                     — batch size: 24
        #   param 6:  (bool)                    — use diarization: False
        #   param 7:  (str)                     — language: "english"
        #   param 8:  (bool)                    — is translate: False
        #   param 9:  (int)                     — beam size: 5
        #   param 10: (int)                     — log prob threshold: -1
        #   param 11: (float)                   — no speech threshold: 0.6
        #   param 12: (float)                   — best of: 5  (actually int)
        #   param 13: (float)                   — patience: 1
        #   param 14: (float)                   — condition on prev text: True (actually bool)
        #   param 15: (str)                     — prompt reset on temperature: 0.5
        #   param 16: (str)                     — prepend punctuations
        #   param 17: (str)                     — initial prompt (LYRICS GO HERE)
        #   param 18: (float)                   — temperature: 0
        #   param 19: (float)                   — compression ratio threshold: 2.4
        #   param 20: (float)                   — hallucination silence threshold: 0
        #   param 21: (str)                     — append punctuations
        #   param 22: (float)                   — max new tokens: 0
        #   param 23: (int)                     — chunk length: 30
        #   param 24: (float)                   — clip timestamps: 0
        #   param 25: (str)                     — hallucination_silence_threshold (dup): "0"
        #   param 26: (str)                     — hotwords: ""
        #   param 27: (bool)                    — WORD TIMESTAMPS (True — critical!)
        #   param 28: (bool)                    — is bgm separation: False
        #   param 29: (str)                     — uvr device: "cuda"
        #   param 30: (str)                     — uvr model size: "UVR-MDX-NET-Inst_HQ_4"
        #   param 31: (int)                     — uvr segment size: 256
        #   param 32: (bool)                    — uvr save file: False
        #   ... remaining params are VAD-related defaults

        initial_prompt = initial_text or ""
        logger.info(
            f"Calling /transcribe_file with model=large-v2, lang=english, "
            f"word_timestamps=True, initial_prompt={len(initial_prompt)} chars"
        )

        # gradio_client.predict() uses POSITIONAL arguments.
        # Whisper-WebUI has many params (33-54 depending on version) and
        # their order varies between versions. We introspect the API to
        # discover parameter names/labels and build args dynamically.
        try:
            # Introspect the API to learn parameter names and count
            param_specs: list[dict] = []
            try:
                api_info = client.view_api(return_format="dict", print_info=False)
                if api_info and "named_endpoints" in api_info:
                    endpoint_info = api_info["named_endpoints"].get("/transcribe_file", {})
                    param_specs = endpoint_info.get("parameters", [])
                    if param_specs:
                        logger.info(f"Whisper-WebUI /transcribe_file has {len(param_specs)} parameters")
                        # Log param names for debugging
                        param_names = [p.get("parameter_name", p.get("label", f"?{i}")) for i, p in enumerate(param_specs)]
                        logger.info(f"  Param names: {param_names[:20]}{'...' if len(param_names) > 20 else ''}")
                        # Log full spec for Dropdown params so we can see choices structure
                        for i, p in enumerate(param_specs):
                            if p.get("component") == "Dropdown":
                                logger.info(f"  Dropdown param {i} ({p.get('parameter_name')}): {json.dumps(p, default=str)[:300]}")
            except Exception as e:
                logger.warning(f"Could not introspect Gradio API: {e}")

            # Values we want to set, keyed by EXACT parameter names only.
            # No partial matching — that caused type mismatches (e.g. "progress"
            # Number component matching our "progress": "large-v2" string).
            desired_values = {
                # Audio files
                "files": [handle_file(str(audio_path))],
                # Output format
                "file_format": "SRT",
                "output_format": "SRT",
                # Timestamp toggle
                "add_timestamp": True,
                # Whisper model selection (various parameter names across versions)
                "model": whisper_model,
                "model_size": whisper_model,
                "whisper_model": whisper_model,
                "whisper_model_size": whisper_model,
                "model_size_or_path": whisper_model,
                # Compute type
                "compute_type": "float16",
                # Batch size
                "batch_size": 24,
                # Diarization
                "diarization": False,
                "is_diarize": False,
                # Language
                "language": "english",
                "lang": "english",
                # Translation
                "is_translate": False,
                "translate": False,
                # Beam size
                "beam_size": 5,
                # Thresholds
                "log_prob_threshold": -1,
                "no_speech_threshold": 0.6,
                "compression_ratio_threshold": 2.4,
                # Best of / patience
                "best_of": 5,
                "patience": 1,
                # Condition on previous — False reduces hallucination/looping on music
                "condition_on_previous_text": False,
                # Prompt reset on temperature
                "prompt_reset_on_temperature": "0.5",
                # Punctuations
                "prepend_punctuations": "\"'([{-",
                "append_punctuations": "\"'.)]}",
                # Initial prompt (lyrics)
                "initial_prompt": initial_prompt,
                # Temperature
                "temperature": 0,
                # Hallucination
                "hallucination_silence_threshold": 0,
                # Max tokens
                "max_new_tokens": 0,
                # Chunk length
                "chunk_length": 30,
                "chunk_length_s": 30,
                # Clip timestamps
                "clip_timestamps": 0,
                # Hotwords
                "hotwords": "",
                # WORD TIMESTAMPS — critical for our pipeline
                "word_timestamps": True,
                # BGM separation
                "bgm_separation": False,
                "is_bgm_separate": False,
                # UVR settings
                "uvr_device": "cuda",
                "uvr_model_size": "UVR-MDX-NET-Inst_HQ_4",
                "uvr_segment_size": 256,
                "uvr_save_file": False,
                # Folder/path params (newer versions)
                "input_folder_path": "",
                "include_subdirectory": False,
                "save_same_dir": False,
                # VAD defaults
                "vad_filter": False,
                "enable_vad": False,
                "threshold": 0.5,
                "min_speech_duration_ms": 2000,
                "min_silence_duration_ms": 250,
                "speech_pad_ms": 400,
                "max_speech_duration_s": 30,
                "window_size_samples": 500,
            }

            def _match_param(param_spec: dict, idx: int) -> any:
                """Find the best value for a parameter by exact name match only.

                For Dropdown components, validates that the value is actually in
                the dropdown's choices list to avoid sending e.g. a Whisper model
                name to a UVR model dropdown that shares the name "model".
                """
                name = param_spec.get("parameter_name", "").lower().strip()
                label = param_spec.get("label", "").lower().strip().replace(" ", "_")
                component = param_spec.get("component", "")
                default = param_spec.get("parameter_default")
                # Extract valid choices for Dropdown components
                choices = None
                if component == "Dropdown":
                    # Gradio view_api() puts choices in various places depending on version:
                    # - "choices" or "options" directly
                    # - type.enum (Gradio 4.x)
                    # - type -> enum (nested dict)
                    raw_choices = param_spec.get("choices", param_spec.get("options", []))
                    if not raw_choices:
                        type_info = param_spec.get("type", {})
                        if isinstance(type_info, dict):
                            raw_choices = type_info.get("enum", [])
                    if raw_choices:
                        # Choices can be list of strings or list of [value, label] pairs
                        choices = set()
                        for c in raw_choices:
                            if isinstance(c, (list, tuple)):
                                choices.add(str(c[0]))
                            else:
                                choices.add(str(c))

                def _validate(val, match_type: str):
                    """Check that a Dropdown value is in its choices list."""
                    if component == "Dropdown" and choices and str(val) not in choices:
                        logger.info(
                            f"  Arg {idx} ({name}): {match_type} matched '{val}' "
                            f"but value not in choices {choices} — skipping"
                        )
                        return False
                    return True

                # Try EXACT name match, then EXACT label match
                if name in desired_values:
                    val = desired_values[name]
                    if _validate(val, "name"):
                        logger.info(f"  Arg {idx} ({name}): matched by name -> {repr(val)[:60]}")
                        return val
                if label and label in desired_values:
                    val = desired_values[label]
                    if _validate(val, "label"):
                        logger.info(f"  Arg {idx} ({name}, label={label}): matched by label -> {repr(val)[:60]}")
                        return val

                # Fall back to the parameter's own default value
                if default is not None:
                    return default

                # Last resort: safe type-based defaults
                if component == "Checkbox":
                    return False
                elif component in ("Number", "Slider"):
                    return 0
                elif component in ("Dropdown", "Textbox", "File"):
                    return ""
                return None

            if param_specs:
                # Build args from introspected parameter specs
                full_args = [_match_param(p, i) for i, p in enumerate(param_specs)]
                logger.info(f"Built {len(full_args)} args via introspection")
                # Log ALL args for debugging (critical for diagnosing mismatches)
                for i, (p, v) in enumerate(zip(param_specs, full_args)):
                    pname = p.get("parameter_name", p.get("label", f"?{i}"))
                    comp = p.get("component", "?")
                    logger.info(f"  Arg {i} ({pname}, {comp}): {repr(v)[:80]}")
            else:
                # Fallback: hardcoded order (original Whisper-WebUI layout)
                logger.warning("No API introspection available — using hardcoded param order")
                full_args = [
                    [handle_file(str(audio_path))],     # 0: files
                    "SRT",                               # 1: file_format
                    True,                                # 2: add timestamp
                    whisper_model,                       # 3: model
                    "float16",                           # 4: compute type
                    24,                                  # 5: batch size
                    False,                               # 6: diarization
                    "english",                           # 7: language
                    False,                               # 8: is translate
                    5,                                   # 9: beam size
                    -1,                                  # 10: log prob threshold
                    0.6,                                 # 11: no speech threshold
                    5,                                   # 12: best of
                    1,                                   # 13: patience
                    False,                               # 14: condition on previous text (False reduces looping on music)
                    "0.5",                               # 15: prompt reset on temperature
                    "\"'([{-",                           # 16: prepend punctuations
                    initial_prompt,                      # 17: INITIAL PROMPT
                    0,                                   # 18: temperature
                    2.4,                                 # 19: compression ratio threshold
                    0,                                   # 20: hallucination silence threshold
                    "\"'.)]}",                           # 21: append punctuations
                    0,                                   # 22: max new tokens
                    30,                                  # 23: chunk length
                    0,                                   # 24: clip timestamps
                    "0",                                 # 25: hallucination silence (str)
                    "",                                  # 26: hotwords
                    True,                                # 27: WORD TIMESTAMPS
                    False,                               # 28: bgm separation
                    "cuda",                              # 29: uvr device
                    "UVR-MDX-NET-Inst_HQ_4",             # 30: uvr model
                    256,                                 # 31: uvr segment size
                    False,                               # 32: uvr save file
                ]

            logger.info(f"Calling Gradio predict with {len(full_args)} positional args")
            result = client.predict(
                *full_args,
                api_name="/transcribe_file",
            )
        except Exception as e:
            raise RuntimeError(f"Gradio transcription failed: {e}")

        # result is a tuple: (output_text, list_of_file_paths)
        # output_text is the SRT content, file_paths are downloadable SRT files
        logger.info(f"Gradio transcription returned: type={type(result)}")

        srt_text = ""
        srt_file_paths = []

        if isinstance(result, tuple):
            srt_text = result[0] if len(result) > 0 else ""
            srt_file_paths = result[1] if len(result) > 1 else []
        elif isinstance(result, str):
            srt_text = result

        # Ensure srt_text is a proper string (gradio_client may return bytes)
        if isinstance(srt_text, bytes):
            logger.info(f"SRT text is bytes ({len(srt_text)} bytes), decoding")
            for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
                try:
                    srt_text = srt_text.decode(enc)
                    logger.info(f"Decoded SRT bytes as {enc}")
                    break
                except (UnicodeDecodeError, AttributeError):
                    continue

        # Force to str if it's some other type (e.g. GradioFileData)
        if not isinstance(srt_text, str):
            srt_text = str(srt_text)

        file_count = len(srt_file_paths) if isinstance(srt_file_paths, list) else (1 if srt_file_paths else 0)
        logger.info(
            f"SRT output: {len(srt_text)} chars, {file_count} file(s)"
        )
        if srt_file_paths:
            logger.info(f"SRT file paths: {srt_file_paths}")
        # Log first 300 chars as repr for encoding diagnostics
        logger.info(f"SRT text preview: {repr(srt_text[:300])}")

        def _is_meaningful_text(text: str) -> bool:
            """Check if text contains meaningful content (actual words, not garbage).

            Whisper hallucinates repeated non-ASCII tokens on instrumental audio.
            These show up as repeated sequences like U+EEAA (PUA chars) or
            0xEE 0xAA bytes decoded as Latin-1 ('îª'). We detect this by checking
            whether the text blocks contain a reasonable ratio of ASCII letters.
            """
            import re
            # Extract just the text lines from SRT (skip index/timecode lines)
            text_lines = []
            for block in re.split(r'\n\n+', text.strip()):
                lines = block.strip().split('\n')
                if len(lines) >= 3:
                    text_lines.extend(lines[2:])
            if not text_lines:
                return False

            combined = " ".join(text_lines)
            if not combined.strip():
                return False

            # Count ASCII letters vs total non-whitespace characters
            ascii_letters = sum(1 for c in combined if c.isascii() and c.isalpha())
            non_ws = sum(1 for c in combined if not c.isspace())
            if non_ws == 0:
                return False

            ratio = ascii_letters / non_ws
            logger.debug(f"Text meaningfulness: {ascii_letters}/{non_ws} ASCII letters = {ratio:.2f}")

            # Real English lyrics should be >50% ASCII letters.
            # Garbage like 'îªîªîª' or '���' will be near 0%.
            return ratio > 0.3

        def _read_srt_file(file_paths) -> str:
            """Try to read SRT content from file paths, preferring UTF-8."""
            for fp in (file_paths if isinstance(file_paths, list) else [file_paths]):
                srt_path = fp if isinstance(fp, str) else str(fp)
                if not srt_path:
                    continue
                # Only try UTF-8 variants — Latin-1/CP1252 would silently
                # decode garbage bytes as mojibake (e.g. 0xEE 0xAA → 'îª')
                for enc in ("utf-8", "utf-8-sig"):
                    try:
                        with open(srt_path, "r", encoding=enc) as f:
                            content = f.read()
                        if content and _is_meaningful_text(content):
                            logger.info(f"Read meaningful SRT from file ({enc}): {len(content)} chars")
                            return content
                        elif content:
                            logger.info(f"Read SRT from file ({enc}): {len(content)} chars but content looks like garbage")
                    except (UnicodeDecodeError, FileNotFoundError, OSError) as e:
                        logger.debug(f"Failed to read {srt_path} as {enc}: {e}")
                        continue
                # Fallback: read as binary, decode as UTF-8 with replacement
                try:
                    with open(srt_path, "rb") as f:
                        raw = f.read()
                    content = raw.decode("utf-8", errors="replace")
                    if content and _is_meaningful_text(content):
                        logger.info(f"Read meaningful SRT from file (binary fallback): {len(content)} chars")
                        return content
                    elif content:
                        logger.info(f"Read SRT from file (binary fallback): {len(content)} chars but content looks like garbage")
                except (FileNotFoundError, OSError):
                    pass
            return ""

        # Check if result[0] is actually a file path rather than inline text.
        # Some gradio_client versions return file paths for text outputs.
        if srt_text and not srt_text.strip().startswith("1") and Path(srt_text.strip()).exists():
            logger.info(f"result[0] appears to be a file path: {srt_text.strip()}")
            file_content = _read_srt_file([srt_text.strip()])
            if file_content:
                srt_text = file_content

        # If we got SRT file paths but no text, read the first SRT file
        if not srt_text and srt_file_paths:
            srt_text = _read_srt_file(srt_file_paths)
            if srt_text:
                logger.info(f"Read SRT from file path: {len(srt_text)} chars")

        # If inline SRT text contains garbled content (replacement chars or
        # non-meaningful text), try reading from SRT file paths instead.
        if srt_text and ("\ufffd" in srt_text or not _is_meaningful_text(srt_text)):
            reason = "replacement chars" if "\ufffd" in srt_text else "non-meaningful content"
            logger.warning(
                f"SRT text contains {reason} — trying SRT file fallback"
            )
            if srt_file_paths:
                file_text = _read_srt_file(srt_file_paths)
                if file_text:
                    logger.info("SRT file fallback succeeded — meaningful text obtained")
                    srt_text = file_text

        # Strip BOM if present
        if srt_text.startswith("\ufeff"):
            srt_text = srt_text[1:]

        if not srt_text:
            logger.warning("No SRT output from Whisper-WebUI")
            return []

        # Final garbage check: if the SRT text doesn't contain meaningful words,
        # Whisper likely hallucinated on instrumental/non-vocal audio.
        if not _is_meaningful_text(srt_text):
            logger.warning(
                "Whisper output appears to be hallucinated garbage "
                "(no meaningful text found). This typically happens with "
                "instrumental audio or poor vocal separation. Returning empty lyrics."
            )
            return []

        # Parse SRT into word timestamps
        words = self._parse_srt_to_words(srt_text)
        logger.info(f"Parsed {len(words)} words from SRT output")

        return words

    @staticmethod
    def _parse_srt_to_words(srt_text: str) -> List[Dict[str, Any]]:
        """
        Parse SRT subtitle text into word-level timestamps.

        SRT format:
        1
        00:00:01,000 --> 00:00:04,500
        This is some text

        2
        00:00:05,000 --> 00:00:08,000
        More text here

        We split each subtitle block's text into words and interpolate
        timestamps evenly across the words in each block.
        """
        import re

        words = []

        # Match SRT blocks: index, timecodes, text
        # Pattern handles both \r\n and \n line endings
        srt_pattern = re.compile(
            r'(\d+)\s*\n'                                    # index
            r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*'       # start time
            r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n'           # end time
            r'((?:(?!\n\n|\n\d+\n).)+)',                     # text (non-greedy, stop at blank line or next index)
            re.DOTALL
        )

        def srt_time_to_seconds(time_str: str) -> float:
            """Convert SRT timestamp (HH:MM:SS,mmm) to seconds."""
            time_str = time_str.replace(",", ".")
            parts = time_str.split(":")
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds

        matches = srt_pattern.findall(srt_text)

        if not matches:
            # Fallback: try a simpler split-based approach
            blocks = re.split(r'\n\n+', srt_text.strip())
            for block in blocks:
                lines = block.strip().split('\n')
                if len(lines) >= 3:
                    time_line = lines[1]
                    text = ' '.join(lines[2:]).strip()
                    time_match = re.match(
                        r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})',
                        time_line
                    )
                    if time_match:
                        matches.append((
                            lines[0],
                            time_match.group(1),
                            time_match.group(2),
                            text,
                        ))

        # Deduplicate consecutive SRT blocks with identical text.
        # Whisper commonly hallucinates/loops on music, producing the same line
        # 5-10 times in a row across overlapping time ranges. We keep only the
        # first occurrence of each consecutive run.
        deduped_matches = []
        prev_text = None
        for _index, start_str, end_str, text in matches:
            cleaned = re.sub(r'<[^>]+>', '', text).strip()
            cleaned = re.sub(r'\s+', ' ', cleaned)
            if not cleaned:
                continue
            if cleaned == prev_text:
                # Skip consecutive duplicate — Whisper hallucination
                continue
            prev_text = cleaned
            deduped_matches.append((_index, start_str, end_str, cleaned))

        if len(deduped_matches) < len(matches):
            logger.info(
                f"SRT dedup: {len(matches)} blocks → {len(deduped_matches)} "
                f"(removed {len(matches) - len(deduped_matches)} consecutive duplicates)"
            )

        for _index, start_str, end_str, text in deduped_matches:
            start_sec = srt_time_to_seconds(start_str)
            end_sec = srt_time_to_seconds(end_str)

            # Split into words and interpolate timestamps
            text_words = text.split()
            if not text_words:
                continue

            duration = end_sec - start_sec
            word_duration = duration / len(text_words) if len(text_words) > 0 else duration

            for i, word_text in enumerate(text_words):
                word_start = start_sec + (i * word_duration)
                word_end = start_sec + ((i + 1) * word_duration)
                words.append({
                    "word": word_text.strip(),
                    "start": round(word_start, 3),
                    "end": round(word_end, 3),
                    "confidence": 1.0,  # SRT doesn't include confidence
                })

        return words

    def transcribe_comfyui(
        self,
        audio_path: str,
        comfyui_url: str,
        initial_text: Optional[str] = None,
        whisper_model: str = "large-v2",
        whisper_language: str = "English",
    ) -> List[Dict[str, Any]]:
        """
        Transcribe audio via a ComfyUI server running the ComfyUI-Whisper extension.

        Loads the whisper_audio_transcription_workflow_api.json workflow,
        uploads the audio file to ComfyUI, submits the workflow, and
        parses the word-level alignment output from the PreviewAny nodes.

        The workflow outputs:
        - Node 92 ("Preview Text"): Full transcription text
        - Node 93 ("Preview Segments Alignment"): Segment-level JSON alignment
        - Node 98 ("Preview Words Alignment"): Word-level JSON alignment
        - Nodes 94/95: Save SRT files on the ComfyUI server
        - Nodes 96/97: Preview the saved SRT file paths

        We use the word-level alignment (node 98) for the best timestamp
        granularity.  If that fails, we fall back to the segment-level
        alignment (node 93) or the SRT files.

        Args:
            audio_path: Path to audio file
            comfyui_url: ComfyUI server URL (e.g., "http://192.168.1.100:8188")
            initial_text: Optional lyrics/script as initial prompt
            whisper_model: Whisper model size (e.g., "large", "large-v2")

        Returns:
            List of words with timestamps

        Raises:
            RuntimeError: If transcription fails
        """
        import copy
        import re
        import time
        import requests
        import websocket as ws_lib

        audio_path = Path(audio_path)
        base_url = comfyui_url.rstrip("/")

        logger.info(f"Transcribing via ComfyUI Whisper: {base_url} (model: {whisper_model})")

        # Step 1: Introspect the Apply Whisper node to discover its actual input fields.
        # We build the workflow dynamically based on what the node accepts,
        # since different versions of ComfyUI-Whisper use different field names.
        whisper_required = {}
        whisper_optional = {}
        try:
            obj_resp = requests.get(f"{base_url}/object_info/Apply%20Whisper", timeout=10)
            if obj_resp.status_code == 200:
                info = obj_resp.json()
                node_info = info.get("Apply Whisper", {}).get("input", {})
                whisper_required = node_info.get("required", {})
                whisper_optional = node_info.get("optional", {})
                logger.info(
                    f"ComfyUI Whisper node: required={list(whisper_required.keys())}, "
                    f"optional={list(whisper_optional.keys())}"
                )
        except Exception as intro_err:
            logger.warning(f"Failed to introspect Apply Whisper node: {intro_err}")

        # Step 2: Build the workflow dynamically from introspected info
        # Use a unique filename with timestamp to bust ComfyUI's execution cache.
        # ComfyUI caches results by input values — if we re-upload with the same
        # filename, it returns the cached output without re-running Whisper.
        import time as _time
        stem = Path(audio_path).stem
        suffix = Path(audio_path).suffix
        upload_filename = f"{stem}_{int(_time.time())}{suffix}"
        model_size = whisper_model.split("-")[0] if "-" in whisper_model else whisper_model

        # Build Apply Whisper inputs from introspection
        whisper_inputs: dict = {"audio": ["99", 0]}
        all_fields = {**whisper_required, **whisper_optional}

        # Model size field
        for field_name in ["model_size", "model", "whisper_model"]:
            if field_name in all_fields:
                field_info = all_fields[field_name]
                if isinstance(field_info, list) and len(field_info) > 0 and isinstance(field_info[0], list):
                    choices = field_info[0]
                    if model_size in choices:
                        whisper_inputs[field_name] = model_size
                    elif whisper_model in choices:
                        whisper_inputs[field_name] = whisper_model
                    else:
                        large_choices = [c for c in choices if "large" in str(c).lower()]
                        whisper_inputs[field_name] = large_choices[0] if large_choices else choices[0]
                    logger.info(f"Set {field_name}={whisper_inputs[field_name]} (choices: {choices})")
                else:
                    whisper_inputs[field_name] = model_size
                break
        else:
            # Fallback if no field found via introspection
            whisper_inputs["model_size"] = model_size

        # Language field — use configured language, validate against choices
        lang_value = whisper_language if whisper_language else "English"
        for field_name in ["language", "lang"]:
            if field_name in all_fields:
                field_info = all_fields[field_name]
                if isinstance(field_info, list) and len(field_info) > 0 and isinstance(field_info[0], list):
                    choices = field_info[0]
                    if lang_value in choices:
                        whisper_inputs[field_name] = lang_value
                    elif lang_value.lower() in [c.lower() for c in choices]:
                        # Case-insensitive match
                        whisper_inputs[field_name] = next(c for c in choices if c.lower() == lang_value.lower())
                    else:
                        whisper_inputs[field_name] = "auto" if "auto" in choices else choices[0]
                    logger.info(f"Set {field_name}={whisper_inputs[field_name]} (requested: {lang_value})")
                else:
                    whisper_inputs[field_name] = lang_value
                break

        # Send an empty prompt — do NOT send the user's lyrics text here.
        # Testing showed that sending full lyrics (especially with section tags
        # like [Intro], [Verse 1]) as Whisper's conditioning prompt degrades
        # transcription quality. An empty string matches the user's manual
        # ComfyUI tests which produce significantly better results.
        for field_name in ["prompt", "initial_text", "initial_prompt"]:
            if field_name in all_fields:
                whisper_inputs[field_name] = ""
                break

        logger.info(f"Apply Whisper built inputs: {list(whisper_inputs.keys())}")

        # Construct the complete workflow
        workflow = {
            "99": {
                "inputs": {"audio": upload_filename},
                "class_type": "LoadAudio",
                "_meta": {"title": "LOAD AUDIO"},
            },
            "47": {
                "inputs": whisper_inputs,
                "class_type": "Apply Whisper",
                "_meta": {"title": "Apply Whisper"},
            },
            # PreviewAny nodes to capture output in execution history
            "92": {
                "inputs": {"source": ["47", 0]},
                "class_type": "PreviewAny",
                "_meta": {"title": "Preview Text"},
            },
            "93": {
                "inputs": {"source": ["47", 1]},
                "class_type": "PreviewAny",
                "_meta": {"title": "Preview Segments Alignment"},
            },
            "98": {
                "inputs": {"source": ["47", 2]},
                "class_type": "PreviewAny",
                "_meta": {"title": "Preview Words Alignment"},
            },
        }

        # Step 3: Upload the audio file to ComfyUI
        try:
            with open(audio_path, "rb") as f:
                resp = requests.post(
                    f"{base_url}/upload/image",
                    files={"image": (upload_filename, f, "audio/mpeg")},
                    data={"overwrite": "true"},
                    timeout=120,
                )
            resp.raise_for_status()
            logger.info(f"Uploaded audio to ComfyUI: {upload_filename}")
        except Exception as e:
            raise RuntimeError(f"Failed to upload audio to ComfyUI: {e}")

        # Step 4: Submit the workflow
        try:
            import uuid
            client_id = str(uuid.uuid4())
            resp = requests.post(
                f"{base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=30,
            )
            if resp.status_code != 200:
                # Log the actual error body from ComfyUI for debugging
                try:
                    error_detail = resp.json()
                except Exception:
                    error_detail = resp.text[:500]
                logger.error(
                    f"ComfyUI rejected workflow (HTTP {resp.status_code}): {error_detail}"
                )
                resp.raise_for_status()
            result = resp.json()
            prompt_id = result["prompt_id"]
            logger.info(f"ComfyUI Whisper workflow submitted: prompt_id={prompt_id}")
        except requests.HTTPError as e:
            raise RuntimeError(f"Failed to submit ComfyUI Whisper workflow: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to submit ComfyUI Whisper workflow: {e}")

        # Step 5: Wait for completion via polling history
        # Whisper can take minutes for long audio files
        max_wait = 1200  # 20 minutes
        poll_interval = 3
        elapsed = 0.0
        history = None

        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval

            try:
                hist_resp = requests.get(f"{base_url}/history/{prompt_id}", timeout=10)
                if hist_resp.status_code == 200:
                    hist_data = hist_resp.json()
                    if prompt_id in hist_data:
                        prompt_history = hist_data[prompt_id]
                        outputs = prompt_history.get("outputs", {})
                        # Check if the key output nodes have data
                        if outputs.get("92") or outputs.get("98") or outputs.get("93"):
                            history = prompt_history
                            break
            except Exception as e:
                logger.debug(f"History poll failed: {e}")

            if elapsed % 30 < poll_interval:
                logger.info(f"ComfyUI Whisper: waiting... ({elapsed:.0f}s elapsed)")

        if not history:
            raise RuntimeError(f"ComfyUI Whisper timed out after {max_wait}s")

        outputs = history.get("outputs", {})
        logger.info(f"ComfyUI Whisper completed. Output nodes: {list(outputs.keys())}")

        # Step 6: Parse the output
        # Priority: word alignment (node 98) > segment alignment (node 93) > full text (node 92)
        words: List[Dict[str, Any]] = []

        # Try word-level alignment from PreviewAny node 98
        words_output = outputs.get("98", {})
        words_text_list = words_output.get("text", [])
        if words_text_list:
            raw_words_text = words_text_list[0] if isinstance(words_text_list, list) else str(words_text_list)
            logger.info(f"ComfyUI Whisper words alignment output ({len(raw_words_text)} chars)")
            words = self._parse_comfyui_alignment(raw_words_text, level="words")

        # Fallback: try segment-level alignment from node 93
        if not words:
            seg_output = outputs.get("93", {})
            seg_text_list = seg_output.get("text", [])
            if seg_text_list:
                raw_seg_text = seg_text_list[0] if isinstance(seg_text_list, list) else str(seg_text_list)
                logger.info(f"ComfyUI Whisper segments alignment output ({len(raw_seg_text)} chars)")
                words = self._parse_comfyui_alignment(raw_seg_text, level="segments")

        # Fallback: try downloading the words SRT from the path in node 97
        if not words:
            srt_path_output = outputs.get("97", {})
            srt_path_list = srt_path_output.get("text", [])
            if srt_path_list:
                srt_path_str = srt_path_list[0] if isinstance(srt_path_list, list) else str(srt_path_list)
                logger.info(f"ComfyUI Whisper SRT path: {srt_path_str}")
                # Try to download the SRT file from ComfyUI's output directory
                try:
                    srt_filename = Path(srt_path_str).name
                    srt_resp = requests.get(
                        f"{base_url}/view",
                        params={"filename": srt_filename, "type": "output"},
                        timeout=30,
                    )
                    if srt_resp.status_code == 200:
                        srt_content = srt_resp.text
                        words = self._parse_srt_to_words(srt_content)
                        logger.info(f"Parsed {len(words)} words from downloaded SRT")
                except Exception as e:
                    logger.warning(f"Failed to download SRT from ComfyUI: {e}")

        # Final fallback: use full text from node 92 (no timestamps)
        if not words:
            text_output = outputs.get("92", {})
            text_list = text_output.get("text", [])
            if text_list:
                full_text = text_list[0] if isinstance(text_list, list) else str(text_list)
                full_text = full_text.strip()
                if full_text:
                    logger.warning(
                        f"ComfyUI Whisper: Only full text available (no word timestamps). "
                        f"Text: {full_text[:100]}..."
                    )
                    # Return as a single block with no timing
                    text_words = full_text.split()
                    for i, w in enumerate(text_words):
                        words.append({
                            "word": w,
                            "start": 0.0,
                            "end": 0.0,
                            "confidence": 1.0,
                        })

        logger.info(f"ComfyUI Whisper transcription: {len(words)} words extracted")
        return words

    @staticmethod
    def _parse_comfyui_alignment(raw_text: str, level: str = "words") -> List[Dict[str, Any]]:
        """Parse word or segment alignment output from ComfyUI-Whisper PreviewAny node.

        The ComfyUI-Whisper extension outputs alignment data as a string
        representation.  The format depends on the version, but common
        formats include:

        Word-level: list of dicts with word/start/end keys, or a formatted
        text with timestamps per word.

        Segment-level: list of dicts with text/start/end keys.

        We try JSON first, then fall back to regex-based parsing.
        """
        import re

        words: List[Dict[str, Any]] = []

        if not raw_text or not raw_text.strip():
            return words

        text = raw_text.strip()

        # Strategy 1: Try parsing as JSON
        try:
            data = json.loads(text)
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue

                    if level == "words":
                        # ComfyUI-Whisper uses "value" for word text, not "word"
                        word_text = item.get("word", item.get("text", item.get("value", ""))).strip()
                        if word_text:
                            words.append({
                                "word": word_text,
                                "start": float(item.get("start", 0)),
                                "end": float(item.get("end", 0)),
                                "confidence": float(item.get("confidence", item.get("probability", 1.0))),
                            })
                    else:
                        # Segment level — split text into words and interpolate
                        seg_text = item.get("text", item.get("value", "")).strip()
                        seg_start = float(item.get("start", 0))
                        seg_end = float(item.get("end", 0))

                        # If the segment has its own word-level data, use it
                        seg_words = item.get("words", [])
                        if seg_words:
                            for sw in seg_words:
                                w_text = sw.get("word", sw.get("text", sw.get("value", ""))).strip()
                                if w_text:
                                    words.append({
                                        "word": w_text,
                                        "start": float(sw.get("start", seg_start)),
                                        "end": float(sw.get("end", seg_end)),
                                        "confidence": float(sw.get("confidence", sw.get("probability", 1.0))),
                                    })
                        elif seg_text:
                            # Interpolate word timestamps within the segment
                            text_words = seg_text.split()
                            if text_words:
                                duration = seg_end - seg_start
                                word_dur = duration / len(text_words)
                                for i, w in enumerate(text_words):
                                    words.append({
                                        "word": w,
                                        "start": round(seg_start + i * word_dur, 3),
                                        "end": round(seg_start + (i + 1) * word_dur, 3),
                                        "confidence": 1.0,
                                    })

                if words:
                    logger.info(f"Parsed {len(words)} words from JSON alignment ({level})")
                    return words
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # Strategy 2: Try SRT format (the data might be SRT text)
        if "-->" in text:
            from backend.services.audio.analysis import AudioAnalyzer
            words = AudioAnalyzer._parse_srt_to_words(text)
            if words:
                logger.info(f"Parsed {len(words)} words from SRT-format alignment")
                return words

        # Strategy 3: Regex-based parsing for common string representations
        # Pattern: "word" (start=0.5, end=1.0)  or  [0.5 - 1.0] word
        pattern1 = re.compile(r'"([^"]+)"\s*\(\s*start\s*=\s*([\d.]+)\s*,\s*end\s*=\s*([\d.]+)\s*\)')
        matches = pattern1.findall(text)
        if matches:
            for word_text, start, end in matches:
                words.append({
                    "word": word_text.strip(),
                    "start": float(start),
                    "end": float(end),
                    "confidence": 1.0,
                })
            logger.info(f"Parsed {len(words)} words from regex pattern 1")
            return words

        # Pattern: start - end : text
        pattern2 = re.compile(r'([\d.]+)\s*-\s*([\d.]+)\s*:\s*(.+)')
        matches2 = pattern2.findall(text)
        if matches2:
            for start, end, seg_text in matches2:
                seg_words = seg_text.strip().split()
                seg_start = float(start)
                seg_end = float(end)
                if seg_words:
                    dur = seg_end - seg_start
                    wdur = dur / len(seg_words)
                    for i, w in enumerate(seg_words):
                        words.append({
                            "word": w,
                            "start": round(seg_start + i * wdur, 3),
                            "end": round(seg_start + (i + 1) * wdur, 3),
                            "confidence": 1.0,
                        })
            if words:
                logger.info(f"Parsed {len(words)} words from regex pattern 2")
                return words

        # If nothing worked, log the raw output for debugging
        logger.warning(
            f"Could not parse ComfyUI alignment output ({level}). "
            f"Raw text preview: {text[:500]}"
        )
        return words

    def _transcribe_openai_compatible(
        self, audio_path: Path, base_url: str, initial_text: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Transcribe using OpenAI-compatible API (e.g., faster-whisper-server)."""
        import requests

        url = f"{base_url}/v1/audio/transcriptions"
        logger.info(f"Posting to OpenAI-compatible endpoint: {url}")

        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/wav")}
            data = {
                "model": "whisper-1",
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
            }
            if initial_text:
                data["initial_prompt"] = initial_text

            response = requests.post(url, files=files, data=data, timeout=600)

        logger.info(f"OpenAI-compatible response: {response.status_code}")
        if response.status_code != 200:
            logger.error(f"OpenAI-compatible error: {response.text[:500]}")
        response.raise_for_status()

        result = response.json()
        words = []
        for w in result.get("words", []):
            words.append({
                "word": w.get("word", "").strip(),
                "start": w.get("start", 0),
                "end": w.get("end", 0),
                "confidence": w.get("confidence", w.get("probability", 1.0)),
            })

        if not words:
            for seg in result.get("segments", []):
                for w in seg.get("words", []):
                    words.append({
                        "word": w.get("word", "").strip(),
                        "start": w.get("start", 0),
                        "end": w.get("end", 0),
                        "confidence": w.get("confidence", w.get("probability", 1.0)),
                    })

        logger.info(f"OpenAI-compatible transcription: {len(words)} words")
        return words

    def _transcribe_generic(
        self, audio_path: Path, base_url: str, initial_text: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Transcribe using a generic endpoint (/asr or /transcribe)."""
        import requests

        # Try common endpoint paths
        for endpoint in ["/asr", "/transcribe"]:
            url = f"{base_url}{endpoint}"
            logger.info(f"Trying generic endpoint: {url}")
            try:
                with open(audio_path, "rb") as f:
                    files = {"audio": (audio_path.name, f, "audio/wav")}
                    data = {}
                    if initial_text:
                        data["initial_prompt"] = initial_text

                    response = requests.post(url, files=files, data=data, timeout=600)

                if response.status_code == 404:
                    continue

                logger.info(f"Generic endpoint response: {response.status_code}")
                response.raise_for_status()
                result = response.json()

                words = []
                if "words" in result:
                    for w in result["words"]:
                        words.append({
                            "word": w.get("word", "").strip(),
                            "start": w.get("start", 0),
                            "end": w.get("end", 0),
                            "confidence": w.get("confidence", 1.0),
                        })
                elif "segments" in result:
                    for seg in result["segments"]:
                        for w in seg.get("words", []):
                            words.append({
                                "word": w.get("word", "").strip(),
                                "start": w.get("start", 0),
                                "end": w.get("end", 0),
                                "confidence": w.get("confidence", 1.0),
                            })
                elif "text" in result:
                    words = [{"word": result["text"], "start": 0, "end": 0, "confidence": 1.0}]

                logger.info(f"Generic transcription: {len(words)} words")
                return words

            except requests.ConnectionError:
                continue
            except Exception as e:
                logger.warning(f"Generic endpoint {endpoint} failed: {e}")
                continue

        raise RuntimeError(f"No working transcription endpoint found at {base_url}")

    def detect_sections(self, audio_path: str) -> List[Dict[str, Any]]:
        """
        Detect audio sections using allin1 model.

        Identifies structure, beats, and BPM.

        Args:
            audio_path: Path to audio file

        Returns:
            List of sections:
            [
                {
                    "label": "intro",
                    "start": 0.0,
                    "end": 8.0,
                    "bpm": 120.0,
                    "beats_per_section": 32
                },
                ...
            ]

        Raises:
            RuntimeError: If detection fails
        """
        audio_path = Path(audio_path)
        audio_id = audio_path.stem

        # Check cache
        cache_file = self.cache_dir / f"{audio_id}_sections.json"
        if cache_file.exists():
            logger.info(f"Using cached sections: {cache_file}")
            with open(cache_file) as f:
                return json.load(f)

        logger.info(f"Detecting sections: {audio_path}")

        try:
            import librosa
            import numpy as np

            # Load audio
            y, sr = librosa.load(str(audio_path), sr=None)
            duration = librosa.get_duration(y=y, sr=sr)

            # Estimate BPM
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            # librosa >= 0.10.2 renamed onset_env → onset_envelope
            try:
                bpm = librosa.beat.tempo(onset_envelope=onset_env, sr=sr)
            except TypeError:
                bpm = librosa.beat.tempo(onset_env=onset_env, sr=sr)
            if isinstance(bpm, np.ndarray):
                bpm = float(bpm[0])

            # Section detection via spectral feature segmentation
            # Uses self-similarity novelty to find structural boundaries,
            # then labels sections based on energy profile similarity
            hop_length = 512
            n_frames = len(y) // hop_length

            # Compute chroma and MFCC features for structural analysis
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
            mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop_length, n_mfcc=13)

            # Stack features
            features = np.vstack([chroma, mfcc])

            # Compute self-similarity via recurrence matrix
            # Use cosine distance between feature frames
            from scipy.spatial.distance import cdist
            # Downsample features for efficiency (1 frame per ~0.5s)
            downsample = max(1, int(sr / (hop_length * 2)))
            feat_ds = features[:, ::downsample]
            n_ds = feat_ds.shape[1]

            # Compute novelty curve from feature distance
            # Compare each frame to its neighbors
            kernel_size = max(4, min(16, n_ds // 10))  # adaptive kernel
            novelty = np.zeros(n_ds)
            for i in range(kernel_size, n_ds - kernel_size):
                left = feat_ds[:, i - kernel_size:i]
                right = feat_ds[:, i:i + kernel_size]
                # Distance between left and right context
                left_mean = np.mean(left, axis=1)
                right_mean = np.mean(right, axis=1)
                novelty[i] = np.linalg.norm(right_mean - left_mean)

            # Normalize novelty
            if novelty.max() > 0:
                novelty = novelty / novelty.max()

            # Find peaks in novelty curve — these are section boundaries
            # Minimum section length: 8 seconds
            min_section_frames = max(1, int(8.0 * sr / (hop_length * downsample)))

            # Adaptive threshold: take peaks above mean + 0.5*std
            threshold = np.mean(novelty) + 0.5 * np.std(novelty)

            boundaries_frames = [0]  # Always start at 0
            last_boundary = 0

            for i in range(1, n_ds - 1):
                if (novelty[i] > threshold and
                    novelty[i] > novelty[i - 1] and
                    novelty[i] > novelty[i + 1] and
                    (i - last_boundary) >= min_section_frames):
                    boundaries_frames.append(i)
                    last_boundary = i

            # Convert frame indices to seconds
            frame_to_sec = (hop_length * downsample) / sr
            boundaries_sec = [f * frame_to_sec for f in boundaries_frames]
            boundaries_sec.append(duration)  # Close final section

            # Cap at reasonable number of sections (5-15 for a typical song)
            if len(boundaries_sec) > 16:
                # Raise threshold and re-detect
                threshold = np.mean(novelty) + 1.0 * np.std(novelty)
                boundaries_frames = [0]
                last_boundary = 0
                for i in range(1, n_ds - 1):
                    if (novelty[i] > threshold and
                        novelty[i] > novelty[i - 1] and
                        novelty[i] > novelty[i + 1] and
                        (i - last_boundary) >= min_section_frames):
                        boundaries_frames.append(i)
                        last_boundary = i
                boundaries_sec = [f * frame_to_sec for f in boundaries_frames]
                boundaries_sec.append(duration)

            # If still too many or too few, fall back to even splits
            if len(boundaries_sec) < 3:
                # Too few boundaries — create ~8 even sections
                n_sections = min(8, max(3, int(duration / 30)))
                boundaries_sec = [i * duration / n_sections for i in range(n_sections + 1)]
            elif len(boundaries_sec) > 16:
                # Still too many — keep top peaks by novelty score
                peak_scores = [(boundaries_frames[i], novelty[boundaries_frames[i]])
                               for i in range(1, len(boundaries_frames))]
                peak_scores.sort(key=lambda x: x[1], reverse=True)
                top_peaks = sorted([p[0] for p in peak_scores[:14]])
                boundaries_sec = [0] + [f * frame_to_sec for f in top_peaks] + [duration]

            # Label sections using energy profile matching
            # Compute mean energy per section
            S = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop_length)
            S_db = librosa.power_to_db(S, ref=np.max)
            frame_times = librosa.frames_to_time(np.arange(S_db.shape[1]), sr=sr, hop_length=hop_length)

            section_energies = []
            for i in range(len(boundaries_sec) - 1):
                start_t = boundaries_sec[i]
                end_t = boundaries_sec[i + 1]
                mask = (frame_times >= start_t) & (frame_times < end_t)
                if mask.any():
                    section_energies.append(float(np.mean(S_db[:, mask])))
                else:
                    section_energies.append(-80.0)

            # Classify: intro (first), outro (last), high-energy = chorus, medium = verse, transitions = bridge
            n_sections = len(boundaries_sec) - 1
            sections = []

            if n_sections == 0:
                sections = [{
                    "label": "verse",
                    "start": 0.0,
                    "end": duration,
                    "bpm": float(bpm),
                    "beats_per_section": 32,
                }]
            else:
                # Compute energy percentiles for labeling
                energy_arr = np.array(section_energies)
                p66 = np.percentile(energy_arr, 66)
                p33 = np.percentile(energy_arr, 33)

                for i in range(n_sections):
                    start_t = round(boundaries_sec[i], 2)
                    end_t = round(boundaries_sec[i + 1], 2)
                    energy = section_energies[i]

                    # First section = intro, last = outro
                    if i == 0 and n_sections > 2:
                        label = "intro"
                    elif i == n_sections - 1 and n_sections > 2:
                        label = "outro"
                    elif energy >= p66:
                        label = "chorus"
                    elif energy <= p33:
                        label = "bridge"
                    else:
                        label = "verse"

                    sections.append({
                        "label": label,
                        "start": start_t,
                        "end": end_t,
                        "bpm": float(bpm),
                        "beats_per_section": 32,
                    })

            logger.info(f"Detected {len(sections)} sections from novelty curve")

            # Cache results
            with open(cache_file, "w") as f:
                json.dump(sections, f, indent=2)

            logger.info(f"Section detection complete: {len(sections)} sections, BPM={bpm:.1f}")
            return sections

        except ImportError:
            logger.error("librosa not installed. Install with: pip install librosa")
            raise RuntimeError("librosa not available")
        except Exception as e:
            raise RuntimeError(f"Section detection error: {e}")
