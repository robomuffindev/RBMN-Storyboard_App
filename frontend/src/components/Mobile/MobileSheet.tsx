import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

interface Props {
  open: boolean;
  title?: string;
  onClose: () => void;
  children: React.ReactNode;
}

/** Bottom sheet for touch actions/forms. */
export default function MobileSheet({ open, title, onClose, children }: Props) {
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[9997] flex flex-col justify-end bg-black/60" onClick={onClose}>
      <div
        className="rounded-t-2xl bg-gray-900 border-t border-gray-700 max-h-[85vh] flex flex-col animate-[slideup_.18s_ease-out]"
        onClick={(e) => e.stopPropagation()}
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
      >
        <div className="flex items-center justify-center pt-2 pb-1">
          <div className="w-10 h-1 rounded-full bg-gray-700" />
        </div>
        {title && (
          <div className="flex items-center px-4 pb-2">
            <h3 className="font-semibold text-gray-100">{title}</h3>
            <button onClick={onClose} className="ml-auto p-1.5 rounded-lg active:bg-gray-800 text-gray-400">
              <X className="w-5 h-5" />
            </button>
          </div>
        )}
        <div className="overflow-y-auto px-4 pb-4">{children}</div>
      </div>
      <style>{`@keyframes slideup{from{transform:translateY(100%)}to{transform:translateY(0)}}`}</style>
    </div>,
    document.body,
  );
}
