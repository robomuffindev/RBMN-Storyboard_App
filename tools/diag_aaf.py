"""Inspect an AAF file: what's actually inside it.

Usage:  python tools/diag_aaf.py path/to/timeline.aaf

Prints:
  - timeline clip count / tracks / duration
  - clip NAMES (is there dialogue text, or just generic "Render"?)
  - mob USER COMMENTS (alternate place producers stash text)
  - embedded audio ESSENCE (count, sizes, format signatures) — i.e. whether
    the AAF carries the audio itself or is timeline-only

Run this when an AAF import surprises you (no audio, no names, weird
boundaries) and paste the output.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from the repo root or tools/
_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    aaf_path = sys.argv[1]
    if not Path(aaf_path).exists():
        print(f"No such file: {aaf_path}")
        return 2

    try:
        import aaf2
        from aaf2 import components as C
    except Exception:
        print("pyaaf2 is not installed in this environment: pip install pyaaf2")
        return 2

    # Load import_aaf directly by path — avoids backend.services.__init__
    # pulling in heavy runtime deps this tool doesn't need.
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "rbmn_import_aaf", str(_repo / "backend" / "services" / "import_aaf.py"))
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    parse_aaf_clips, _clean_name = _mod.parse_aaf_clips, _mod._clean_name

    print(f"=== AAF: {aaf_path} ({Path(aaf_path).stat().st_size / 1e6:.1f} MB) ===\n")

    # ── Timeline clips (via the app's own parser) ──────────────────────
    try:
        clips = parse_aaf_clips(aaf_path)
        total = max((c["end"] for c in clips), default=0.0)
        named = [c for c in clips if (c.get("name") or "").strip()]
        print(f"Timeline: {len(clips)} clips, {total:.1f}s total")
        print(f"Clip names: {len(named)}/{len(clips)} non-generic "
              f"({'TEXT MAY BE PRESENT' if named else 'no usable text — generic names only'})")
        for c in clips[:10]:
            nm = (c.get("name") or "(unnamed/generic)")[:70]
            print(f"  {c['start']:8.2f}s - {c['end']:8.2f}s  {nm}")
        if len(clips) > 10:
            print(f"  ... and {len(clips) - 10} more")
    except Exception as e:
        print(f"Timeline parse FAILED: {e}")

    # ── Per-track breakdown (multi-track AAFs cut scenes at EVERY track's
    #    clip starts — a second track explains "weird mid-paragraph splits") ──
    print("\nPer-track breakdown:")
    try:
        from aaf2 import components as _C2
        with aaf2.open(aaf_path, "r") as f:
            comps = list(f.content.toplevel()) or [
                m for m in f.content.mobs if type(m).__name__ == "CompositionMob"]
            for ci, comp in enumerate(comps):
                for si, slot in enumerate(getattr(comp, "slots", []) or []):
                    mk = getattr(slot, "media_kind", None)
                    seg = getattr(slot, "segment", None)
                    if mk != "Sound" or not isinstance(seg, _C2.Sequence):
                        print(f"  comp{ci} slot{si}: media={mk} (skipped by importer)")
                        continue
                    ncl = sum(1 for c in seg.components if isinstance(c, _C2.SourceClip))
                    nfl = sum(1 for c in seg.components if isinstance(c, _C2.Filler))
                    er = float(slot.edit_rate)
                    print(f"  comp{ci} slot{si}: Sound track — {ncl} clips, {nfl} fillers, edit_rate={er:g}")
    except Exception as e:
        print(f"  per-track scan failed: {e}")

    # ── Mob names + user comments + essence ────────────────────────────
    print("\nMobs / comments / embedded essence:")
    ess_count = 0
    ess_bytes = 0
    heads: dict[str, int] = {}
    comment_samples: list[str] = []
    mob_kinds: dict[str, int] = {}
    try:
        with aaf2.open(aaf_path, "r") as f:
            for mob in f.content.mobs:
                kind = type(mob).__name__
                mob_kinds[kind] = mob_kinds.get(kind, 0) + 1
                # comments
                try:
                    cm = getattr(mob, "comments", None)
                    if cm:
                        for k, v in dict(cm).items():
                            sv = _clean_name(v)
                            if sv and len(comment_samples) < 8:
                                comment_samples.append(f"  [{kind}] {k}: {str(sv)[:80]}")
                except Exception:
                    pass
                # essence
                try:
                    ed = getattr(mob, "essence", None)
                    if ed is not None:
                        stream = ed.open("r")
                        # pyaaf2 returns a BYTEARRAY (unhashable — a plain
                        # dict lookup on it raised TypeError and silently
                        # zeroed all the counts in the first version).
                        head = bytes(stream.read(4) or b"")
                        ess_count += 1
                        if head == b"RIFF":
                            sig = "WAV"
                        elif head == b"FORM":
                            sig = "AIFF/AIFC"
                        else:
                            sig = f"raw PCM ({head.hex()})"
                        heads[sig] = heads.get(sig, 0) + 1
                        # size: read through in chunks (cheap count, no store)
                        sz = len(head)
                        while True:
                            b = stream.read(1 << 22)
                            if not b:
                                break
                            sz += len(b)
                        ess_bytes += sz
                        # per-stream duration hint from the PCM descriptor
                        try:
                            desc = mob.descriptor
                            sr = int(float(desc["SampleRate"].value))
                            ch = int(desc["Channels"].value)
                            bw = max(1, int(desc["QuantizationBits"].value) // 8)
                            if sr > 0 and ch > 0:
                                heads[f"  ~{sig} {sr}Hz {ch}ch {bw * 8}bit"] = heads.get(
                                    f"  ~{sig} {sr}Hz {ch}ch {bw * 8}bit", 0) + 0
                        except Exception:
                            pass
                except Exception:
                    pass
        print(f"  Mob types: {mob_kinds}")
        print(f"  Embedded essence: {ess_count} streams, {ess_bytes / 1e6:.1f} MB total "
              f"({'AUDIO IS EMBEDDED' if ess_count else 'TIMELINE-ONLY (no audio inside)'})")
        if heads:
            print(f"  Essence formats: {heads}")
        if comment_samples:
            print("  User comments found (possible text source!):")
            for line in comment_samples:
                print(line)
        else:
            print("  User comments: none with usable text")
    except Exception as e:
        print(f"  Mob scan FAILED: {e}")

    print("\nVerdict:")
    print("  - audio: " + ("embedded → the importer extracts it automatically"
                           if ess_count else "NOT embedded → upload the audio export alongside the AAF"))
    print("  - text:  " + ("possible — check clip names / comments above"
                           if (comment_samples) else "not in this AAF → upload the SRT for narration text + subtitles"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
