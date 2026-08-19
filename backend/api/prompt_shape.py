"""🎛 Per-engine PROMPT SHAPING for the music lane (v1.277.19).

The three engines want **opposite** things, and this is documented in their own
source and docs (research pass, 2026-08-16 — sources in the CHANGELOG):

    ACE-Step 1.5   tags = a CAPTION. bpm/key/timesignature/duration are
                   injected by the tokenizer as a `# Metas` block, so writing
                   them in the caption means the model is told two tempos.
                   → STRIP metadata out of the text, into the widgets.

    MiniMax 3      the node has NO metadata widgets at all. Tempo, key,
                   instruments and the VOCAL must be written INTO the caption,
                   in MiniMax's own three-section structured layout, or the
                   model drifts instrumental.
                   → PUT metadata into the text.

    F5-TTS         no music prompt; text rules live in the TTS lane.

One brief therefore has to be PROJECTED per engine, not copy-pasted. Everything
here is a pure function of its inputs — no I/O, no worker calls — so
`scripts/prompt_shape_smoke.py` can exercise every rule for free.

⭐ The rules encoded below are from the shipping code and the model authors'
docs, not from taste:
 · ACE lyric structure tags: Title Case, ONE modifier after a single "-"
   (stacked modifiers get SUNG), blank line between sections, `[Instrumental]`
   rather than an empty box, 6-10 syllables a line.
 · ACE line budget: bars = duration*bpm/240; a ballad needs ~2 bars a line, so
   a 20 s cue at 90 bpm holds about FOUR lines. Over-long sheets are the #1
   reported cause of skipped verses.
 · MM3 lyrics: EVERY `[...]` becomes a lowercased tag on its own line, and any
   text left on a tag's line can be DROPPED — so stage directions must use
   (parentheses), never brackets.
 · MM3 caption: four consecutive spaces are blind-deleted by `clean_caption`
   (they glue words together), markdown is stripped, and the whole prompt is
   HARD-CAPPED at 5000 tokens — over it the node RAISES instead of truncating.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# ── vocabularies used to project a flat tag list into MM3's sections ─────────
_VOCAL_WORDS = (
    "female vocal", "male vocal", "female voice", "male voice", "duet",
    "choir", "falsetto", "breathy", "raspy", "whisper", "whispered", "belting",
    "spoken word", "rap", "vocals", "vocal", "singer", "harmonies", "acapella",
)
_PRODUCTION_WORDS = (
    "reverb", "compression", "compressed", "lo-fi", "lofi", "hi-fi", "wide",
    "dry", "warm", "bright", "dark", "tape", "analog", "analogue", "vinyl",
    "close-miked", "close-mic'd", "room", "live", "studio", "mix", "master",
    "saturated", "crisp", "airy", "punchy", "muddy", "clean", "distorted",
)
_INSTRUMENTS = (
    "guitar", "acoustic guitar", "electric guitar", "bass", "upright bass",
    "drums", "brushed drums", "percussion", "piano", "rhodes", "organ",
    "synth", "strings", "violin", "cello", "harmonica", "banjo", "mandolin",
    "horns", "brass", "sax", "saxophone", "flute", "pads", "arpeggio",
    "808", "kick", "snare", "hi-hat", "hihat", "bells", "choir",
)
#: structure tags both engines understand (ACE Title Case, MM3 lowercases them)
_STRUCTURE = ("intro", "verse", "pre-chorus", "pre chorus", "chorus",
              "post-chorus", "post chorus", "bridge", "instrumental", "solo",
              "break", "breakdown", "build", "drop", "hook", "interlude",
              "outro", "fade out", "silence")

_BPM_RE = re.compile(r"\b(\d{2,3})\s*(?:bpm|beats per minute)\b", re.I)
_KEY_RE = re.compile(r"\b(?:in\s+)?([A-G](?:\s?#|\s?b|♯|♭)?)\s+(major|minor)\b", re.I)
_SIG_RE = re.compile(r"\b(\d)\s*/\s*(\d)\s*(?:time|meter)?\b")
_DUR_RE = re.compile(r"\b\d+\s*(?:seconds?|secs?|s)\b", re.I)
_TAG_RE = re.compile(r"\[([^\]]*)\]")


def _words(s: str) -> int:
    return len([w for w in re.split(r"\s+", s or "") if w])


def extract_meta(tags: str) -> Tuple[str, int, str, str]:
    """(clean_caption, bpm, keyscale, timesignature) — pulled OUT of the text.

    ⚠ This is the ACE rule and the exact inverse of what MM3 needs, which is
    why it returns the parts instead of just cleaning: the MM3 projection puts
    them straight back IN."""
    t = tags or ""
    bpm = 0
    keyscale = ""
    sig = ""
    m = _BPM_RE.search(t)
    if m:
        try:
            bpm = int(m.group(1))
        except ValueError:
            bpm = 0
    k = _KEY_RE.search(t)
    if k:
        keyscale = f"{k.group(1).replace(' ', '')} {k.group(2).lower()}"
    g = _SIG_RE.search(t)
    if g:
        sig = g.group(1)
    t = _DUR_RE.sub(" ", _SIG_RE.sub(" ", _KEY_RE.sub(" ", _BPM_RE.sub(" ", t))))
    t = re.sub(r"\s*,\s*(?=,)", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;.-")
    return t, bpm, keyscale, sig


def _pick(text: str, vocab) -> List[str]:
    low = (text or "").lower()
    got = [w for w in vocab if w in low]
    # keep the longest match of overlapping pairs ("acoustic guitar" beats "guitar")
    return [w for w in got if not any(w != o and w in o for o in got)]


# ── ACE-Step ─────────────────────────────────────────────────────────────────
def ace_lyrics(lyrics: str, instrumental: bool, seconds: float,
               bpm: int) -> Tuple[str, List[str]]:
    """Title-Case tags, one modifier each, section spacing, and a LINE BUDGET.

    The budget is the documented failure mode, not a nicety: a lyric sheet
    longer than the duration can hold makes the model skip lines or whole
    sections (ACE-Step issue #391)."""
    notes: List[str] = []
    body = (lyrics or "").strip()
    if instrumental or not body:
        return "[Instrumental]", (["instrumental → [Instrumental] (an EMPTY "
                                   "lyrics box is not the documented form)"]
                                  if not body else [])

    out_lines: List[str] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        m = _TAG_RE.fullmatch(line.strip())
        if m:
            inner = m.group(1).strip()
            parts = [p.strip() for p in inner.split("-") if p.strip()]
            if len(parts) > 2:                       # ⚠ stacked modifiers get SUNG
                notes.append(f"tag [{inner}] trimmed to one modifier")
                parts = parts[:2]
            name = parts[0].title()
            line = f"[{name}]" if len(parts) == 1 else f"[{name} - {parts[1].lower()}]"
            if out_lines and out_lines[-1] != "":
                out_lines.append("")                 # blank line between sections
        out_lines.append(line)

    # ⏱ line budget: bars = seconds * bpm / 240; ~2 bars a line under 100 bpm
    bars = max(1.0, float(seconds) * float(bpm or 100) / 240.0)
    per_line = 2.0 if (bpm or 100) <= 100 else 1.0
    budget = max(2, int(bars / per_line))
    kept: List[str] = []
    sung = 0
    for line in out_lines:
        is_tag = bool(_TAG_RE.fullmatch(line.strip()))
        if not is_tag and line.strip():
            if sung >= budget:
                continue                              # truncate, never mid-section
            sung += 1
        kept.append(line)
    if sung < len([x for x in out_lines
                   if x.strip() and not _TAG_RE.fullmatch(x.strip())]):
        notes.append(f"lyrics trimmed to {budget} sung line(s) for {seconds:.0f}s "
                     f"at {bpm or 100} bpm")
    text = "\n".join(kept).strip()
    return (text or "[Instrumental]"), notes


def for_ace(tags: str, lyrics: str, seconds: float, bpm: int = 0,
            keyscale: str = "", timesignature: str = "",
            instrumental: bool = False) -> Dict:
    """Project a brief onto ACE-Step's fields: caption WITHOUT metadata."""
    notes: List[str] = []
    caption, f_bpm, f_key, f_sig = extract_meta(tags)
    if f_bpm and not bpm:
        bpm, _ = f_bpm, notes.append(f"moved '{f_bpm} bpm' out of the caption "
                                     f"into the bpm field")
    if f_key and not keyscale:
        keyscale = f_key
        notes.append(f"moved key '{f_key}' out of the caption")
    if f_sig and not timesignature:
        timesignature = f_sig
    caption = _TAG_RE.sub(" ", caption).strip()       # [verse] never belongs here
    if _words(caption) > 150:
        words = caption.split()
        caption = " ".join(words[:150])
        notes.append("caption capped at 150 words (40-120 is the shipped range)")
    lyr, lnotes = ace_lyrics(lyrics, instrumental, seconds, bpm)
    return {"tags": caption, "lyrics": lyr, "bpm": bpm, "keyscale": keyscale,
            "timesignature": timesignature or "4", "notes": notes + lnotes}


# ── MiniMax Music 3 ──────────────────────────────────────────────────────────
def _mm3_sections_present(caption: str) -> bool:
    low = (caption or "").lower()
    return "global metadata" in low and "arrangement" in low


def mm3_caption(tags: str, seconds: float, bpm: int = 0, keyscale: str = "",
                instrumental: bool = False) -> Tuple[str, List[str]]:
    """Build MiniMax's THREE-SECTION structured caption from a flat brief.

    Layout and phrasing follow MiniMax's own templates (`Global Metadata` /
    `Vocal Details` / `Arrangement`, bare headings, and the training-corpus
    wording "bpm is 90. key is G, and scale is major."). It is a strong
    convention rather than a parsed schema — nothing validates it — but it is
    the distribution the model was captioned in."""
    notes: List[str] = []
    raw = (tags or "").strip()
    if _mm3_sections_present(raw):
        return raw, ["caption already structured — left alone"]

    body, f_bpm, f_key, _sig = extract_meta(raw)
    bpm = bpm or f_bpm
    keyscale = keyscale or f_key
    key, scale = (keyscale.split() + [""])[:2] if keyscale else ("", "")
    vocals = _pick(raw, _VOCAL_WORDS)
    prod = _pick(raw, _PRODUCTION_WORDS)
    instr = _pick(raw, _INSTRUMENTS)
    genre = body.split(",")[0].strip() if "," in body else body[:60].strip()

    meta_bits = []
    if bpm:
        meta_bits.append(f"bpm is {bpm}.")
    if key:
        meta_bits.append(f"key is {key}, and scale is {scale or 'major'}.")
    else:
        notes.append("no key given — left unstated rather than invented")
    if genre:
        meta_bits.append(f"{genre[0].upper() + genre[1:]}.")
    lines = ["Global Metadata",
             "Basic Attributes: " + (" ".join(meta_bits) or body[:120]),
             f"Global Emotional Progression: {body}." if body else "",
             ("Sonics & Production Profile: " + ", ".join(prod) + "."
              if prod else "")]
    lines.append("Vocal Details")
    if instrumental or not vocals:
        if instrumental:
            lines.append("Instrumental. There is no vocal; the lead melodic "
                         "role is carried by the instruments described below.")
        else:
            # ⚠ documented: an unnamed vocal makes MM3 drift INSTRUMENTAL
            lines.append("Vocal Gender & Timbre: a single clear lead vocal, "
                         "warm and unforced, sitting close and centred.")
            notes.append("no vocal described — added a neutral one (MM3 drifts "
                         "instrumental when the vocal is unnamed)")
    else:
        lines.append("Vocal Gender & Timbre: " + ", ".join(vocals) + ".")
    lines.append("Arrangement")
    if instr:
        lines.append("Instrument Lifecycle Description (Primary/Secondary "
                     "Layering): Primary: " + instr[0]
                     + " carries the harmony and the pulse throughout."
                     + (" Secondary: " + ", ".join(instr[1:]) + " support it."
                        if len(instr) > 1 else ""))
    else:
        lines.append("Instrument Lifecycle Description (Primary/Secondary "
                     "Layering): Primary: the arrangement described above "
                     "carries the piece from first second to last.")
    # ⚠ a 20s cue must not be narrated as a six-section song
    if seconds <= 30:
        lines.append("Groove & Foundation Progression: a single continuous "
                     "passage — one idea, stated and held, with no section "
                     "changes in this short cue.")
        notes.append("short cue (≤30s) — arc described as ONE section")
    else:
        lines.append("Groove & Foundation Progression: the arrangement opens "
                     "sparse, fills through the middle, and resolves at the end.")

    cap = "\n".join([ln for ln in lines if ln])
    cap = cap.replace("    ", " ")        # blind-deleted by clean_caption
    cap = re.sub(r"[ \t]{2,}", " ", cap)
    if _words(cap) > 450:
        notes.append("caption capped at 450 words")
        cap = " ".join(cap.split()[:450])
    return cap, notes


def mm3_lyrics(lyrics: str, instrumental: bool) -> Tuple[str, List[str]]:
    """Tags alone on their line, lowercased; stage directions in PARENTHESES.

    ⚠ MM3's `normalize_lyrics` turns EVERY bracketed token into a lowercased
    tag on its own line, and text left on a tag's line can be dropped — so a
    stage direction written as [rain on the window] both becomes a bogus tag
    and eats the words next to it."""
    notes: List[str] = []
    body = (lyrics or "").strip()
    if instrumental or not body:
        return "[Intro]\n(instrumental)", ["instrumental → [Intro] + "
                                           "(instrumental) (MM3 wants non-empty "
                                           "lyrics; the flag is cloud-only)"]
    out: List[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _TAG_RE.search(line)
        if not m:
            out.append(line)
            continue
        inner = m.group(1).strip()
        base = inner.split("-")[0].strip().lower()
        rest = line[:m.start()].strip() + " " + line[m.end():].strip()
        if base in _STRUCTURE:
            if inner.lower() != base:
                notes.append(f"[{inner}] → [{base}] (MM3 lowercases tags and "
                             "ignores modifiers)")
            out.append(f"[{base}]")
            if rest.strip():
                # ⚠ text on a tag's line is silently DROPPED — move it down
                out.append(rest.strip())
                notes.append("moved lyric text off a tag line (it would have "
                             "been dropped)")
        else:
            # not a structure tag → it is a stage direction: parentheses
            fixed = line.replace(f"[{inner}]", f"({inner})")
            out.append(fixed)
            notes.append(f"[{inner}] → ({inner}) — brackets become tags in MM3")
    return "\n".join(out), notes


def for_mm3(tags: str, lyrics: str, seconds: float, bpm: int = 0,
            keyscale: str = "", instrumental: bool = False) -> Dict:
    cap, n1 = mm3_caption(tags, seconds, bpm, keyscale, instrumental)
    lyr, n2 = mm3_lyrics(lyrics, instrumental)
    notes = n1 + n2
    # ⚠ HARD LIMIT: over 5000 tokens the node RAISES (it does not truncate).
    # ~4 chars a token is the usual heuristic; stay well under.
    if len(cap) + len(lyr) > 14000:
        cap = cap[:12000]
        notes.append("caption truncated — MM3 raises above 5000 tokens rather "
                     "than truncating")
    return {"tags": cap, "lyrics": lyr, "notes": notes}


# ── the one entry point the lanes call ───────────────────────────────────────
def shape(engine: str, tags: str, lyrics: str = "", seconds: float = 60.0,
          bpm: int = 0, keyscale: str = "", timesignature: str = "",
          instrumental: Optional[bool] = None) -> Dict:
    """Project one brief onto ONE engine's fields. Pure function.

    Returns {tags, lyrics, bpm, keyscale, timesignature, notes[]} — the notes
    are published on the job so a reshaped prompt is never a silent rewrite."""
    inst = (not (lyrics or "").strip()) if instrumental is None else instrumental
    if engine == "minimax3":
        out = for_mm3(tags, lyrics, seconds, bpm, keyscale, inst)
        out.setdefault("bpm", bpm)
        out.setdefault("keyscale", keyscale)
        out.setdefault("timesignature", timesignature)
        return out
    return for_ace(tags, lyrics, seconds, bpm, keyscale, timesignature, inst)
