import { useAppStore } from '@/store';
import type { SectionLabel } from '@/types/index';

const sectionColors: Record<SectionLabel, string> = {
  intro: 'bg-purple-700',
  verse: 'bg-blue-700',
  chorus: 'bg-green-700',
  bridge: 'bg-yellow-700',
  outro: 'bg-red-700',
  other: 'bg-gray-700',
};

interface SectionMarkersProps {
  duration: number;
}

export default function SectionMarkers({ duration }: SectionMarkersProps) {
  const sections = useAppStore(s => s.sections);
  const viewMode = useAppStore(s => s.viewMode);
  const setActiveScene = useAppStore(s => s.setActiveScene);

  if (viewMode !== 'sections' || !sections?.length) return null;

  // Use waveform duration if available, otherwise compute from section end times
  const effectiveDuration = duration > 0
    ? duration
    : Math.max(...sections.map((s) => s.end_time || 0), 1);

  if (effectiveDuration === 0) return null;

  return (
    <div className="relative w-full h-12 bg-gray-950 border-t border-gray-800 overflow-x-auto flex items-center">
      {sections.map((section) => {
        const startPercent = (section.start_time / effectiveDuration) * 100;
        const widthPercent = ((section.end_time - section.start_time) / effectiveDuration) * 100;

        return (
          <div
            key={section.id}
            className={`section-marker ${sectionColors[section.label]} absolute h-full cursor-pointer hover:opacity-80 transition-opacity`}
            style={{
              left: `${startPercent}%`,
              width: `${widthPercent}%`,
            }}
            onClick={() => setActiveScene(section as any)}
            title={`${section.label} (${(section.start_time).toFixed(1)}s - ${(section.end_time).toFixed(1)}s)`}
          >
            <div className="px-2 py-1 text-xs font-semibold text-white overflow-hidden whitespace-nowrap">
              {section.label}
            </div>
          </div>
        );
      })}
    </div>
  );
}
