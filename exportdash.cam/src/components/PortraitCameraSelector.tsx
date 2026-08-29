'use client';

import { useState, useRef, useEffect } from 'react';
import { ANGLE_LABELS, AlignPosition } from '@/types/video';
import { IconChevronDown } from '@tabler/icons-react';

interface PortraitCameraSelectorProps {
  currentAngle: string;
  availableAngles: string[];
  assignedAngles: string[];
  onChange: (newAngle: string) => void;
  currentAlign: AlignPosition;
  onAlignChange: (align: AlignPosition) => void;
}

const ALIGN_GRID: AlignPosition[][] = [
  ['top left', 'top center', 'top right'],
  ['center left', 'center', 'center right'],
  ['bottom left', 'bottom center', 'bottom right'],
];

export function PortraitCameraSelector({
  currentAngle,
  availableAngles,
  assignedAngles,
  onChange,
  currentAlign,
  onAlignChange,
}: PortraitCameraSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on click outside
  useEffect(() => {
    if (!isOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [isOpen]);

  return (
    <div
      ref={dropdownRef}
      className={`absolute top-1.5 left-1.5 z-20 transition-opacity ${isOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}
      onClick={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="flex items-center gap-1">
        {/* Camera dropdown button */}
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="flex items-center gap-1 px-2 py-1 rounded-md border border-white/20 bg-black/50 backdrop-blur-sm text-[11px] font-medium text-white/90 hover:bg-black/70 hover:border-white/40 transition-all"
        >
          {ANGLE_LABELS[currentAngle] || currentAngle}
          <IconChevronDown size={10} className={`transition-transform ${isOpen ? 'rotate-180' : ''}`} />
        </button>

        {/* Alignment 3×3 grid */}
        <div className="grid grid-cols-3 gap-[2px] p-1 rounded-md border border-white/20 bg-black/50 backdrop-blur-sm">
          {ALIGN_GRID.map((row, rowIdx) =>
            row.map((pos) => {
              const isActive = currentAlign === pos;
              return (
                <button
                  key={pos}
                  onClick={() => onAlignChange(pos)}
                  className={`w-[7px] h-[7px] rounded-full transition-all ${
                    isActive
                      ? 'bg-blue-400 scale-125'
                      : 'bg-white/30 hover:bg-white/60'
                  }`}
                  title={pos}
                />
              );
            })
          )}
        </div>
      </div>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 bg-gray-900/95 backdrop-blur-sm border border-gray-600 rounded-lg shadow-xl overflow-hidden min-w-[110px]">
          {availableAngles.map((angle) => {
            const isUsed = assignedAngles.includes(angle) && angle !== currentAngle;
            const isCurrent = angle === currentAngle;

            return (
              <button
                key={angle}
                onClick={() => {
                  onChange(angle);
                  setIsOpen(false);
                }}
                className={`w-full text-left px-2.5 py-1.5 text-[11px] transition-colors flex items-center justify-between gap-2 ${
                  isCurrent
                    ? 'bg-blue-600/30 text-blue-300'
                    : isUsed
                    ? 'text-gray-500 hover:bg-gray-800 hover:text-gray-300'
                    : 'text-gray-200 hover:bg-gray-800'
                }`}
              >
                <span>{ANGLE_LABELS[angle] || angle}</span>
                {isUsed && <span className="text-[9px] text-gray-600">(used)</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
