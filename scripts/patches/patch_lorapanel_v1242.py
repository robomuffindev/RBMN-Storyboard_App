"""v1.242 UI — framing comes back, as a measurement this time.

v1.241 removed the framing chips because the vision model's verdict was worthless.
The verdict is now face-box height against image height, calibrated on 40 images
with non-overlapping bands, so the chips come back with a number behind them —
and `NOT checked` loses framing and keeps crop, because crop still has no honest
instrument.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1
         else "frontend/src/components/VNCCSNative/LoraPanel.tsx")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''  ok: boolean; issues?: string[]; angle_ok?: boolean;
  yaw?: number | null; angle_method?: string; angle_note?: string;''',
    '''  ok: boolean; issues?: string[]; angle_ok?: boolean;
  yaw?: number | null; angle_method?: string; angle_note?: string;
  framing_ok?: boolean; framing_method?: string; framing_note?: string;
  face_h_ratio?: number | null;''',
    "item qc type")

rep('''  angle_off: number; angle_measured?: number; angle_unmeasured?: number;
  expression_off: number;''',
    '''  angle_off: number; angle_measured?: number; angle_unmeasured?: number;
  framing_off?: number; framing_measured?: number; framing_unmeasured?: number;
  expression_off: number;''',
    "flags type")

rep('''                            ds.flags.angle_off ? `${ds.flags.angle_off} wrong angle` : '',''',
    '''                            ds.flags.angle_off ? `${ds.flags.angle_off} wrong angle` : '',
                            ds.flags.framing_off ? `${ds.flags.framing_off} wrong shot type` : '',''',
    "summary chip")

rep('''                        · one person · artifacts''',
    '''                        · shot type (face height{typeof ds.flags.framing_measured === 'number'
                          ? ` ×${ds.flags.framing_measured}${ds.flags.framing_unmeasured
                            ? `, ${ds.flags.framing_unmeasured} back rows n/a` : ''}` : ''})
                        · one person · artifacts''',
    "measured line")

rep('''                          {it.qc && (
                            <span style={it.qc.ok ? okTxt : errTxt}''',
    '''                          {/* v1.242: the measured shot type. A face crop with no
                              face, or a face sitting at the bottom of the frame,
                              is now visible here instead of passing silently. */}
                          {it.qc?.framing_method === 'face-height' && (
                            <span style={it.qc.framing_ok === false ? errTxt : okTxt}
                                  title={it.qc.framing_note || ''}>
                              {`🖼 ${((it.qc.face_h_ratio || 0) * 100).toFixed(0)}%`}
                            </span>
                          )}
                          {it.qc && (
                            <span style={it.qc.ok ? okTxt : errTxt}''',
    "framing badge")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
