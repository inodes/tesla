'use client';

import { useState, useRef, useCallback, ReactNode } from 'react';

interface TooltipProps {
  content: string;
  children: ReactNode;
  position?: 'top' | 'bottom' | 'left' | 'right';
  delay?: number;
}

export function Tooltip({ content, children, position = 'top', delay = 0 }: TooltipProps) {
  const [isVisible, setIsVisible] = useState(false);
  const [coords, setCoords] = useState({ x: 0, y: 0 });
  const triggerRef = useRef<HTMLDivElement>(null);
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  const calculatePosition = useCallback(() => {
    if (!triggerRef.current) return { x: 0, y: 0 };

    const triggerRect = triggerRef.current.getBoundingClientRect();
    // Estimate tooltip size (will be close enough for positioning)
    const estimatedWidth = content.length * 7 + 16; // ~7px per char + padding
    const estimatedHeight = 28;

    let x = 0;
    let y = 0;

    switch (position) {
      case 'top':
        x = triggerRect.left + (triggerRect.width / 2) - (estimatedWidth / 2);
        y = triggerRect.top - estimatedHeight - 8;
        break;
      case 'bottom':
        x = triggerRect.left + (triggerRect.width / 2) - (estimatedWidth / 2);
        y = triggerRect.bottom + 8;
        break;
      case 'left':
        x = triggerRect.left - estimatedWidth - 8;
        y = triggerRect.top + (triggerRect.height / 2) - (estimatedHeight / 2);
        break;
      case 'right':
        x = triggerRect.right + 8;
        y = triggerRect.top + (triggerRect.height / 2) - (estimatedHeight / 2);
        break;
    }

    // Keep tooltip within viewport
    x = Math.max(8, Math.min(x, window.innerWidth - estimatedWidth - 8));
    y = Math.max(8, Math.min(y, window.innerHeight - estimatedHeight - 8));

    return { x, y };
  }, [content, position]);

  const showTooltip = useCallback(() => {
    const pos = calculatePosition();
    setCoords(pos);

    if (delay > 0) {
      timeoutRef.current = setTimeout(() => setIsVisible(true), delay);
    } else {
      setIsVisible(true);
    }
  }, [calculatePosition, delay]);

  const hideTooltip = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    setIsVisible(false);
  }, []);

  return (
    <>
      <div
        ref={triggerRef}
        onMouseEnter={showTooltip}
        onMouseLeave={hideTooltip}
        onFocus={showTooltip}
        onBlur={hideTooltip}
        className="inline-flex"
      >
        {children}
      </div>
      {isVisible && (
        <div
          className="fixed z-[100] px-2 py-1 text-xs font-medium text-white bg-gray-900 rounded-md shadow-lg border border-gray-700 whitespace-nowrap pointer-events-none opacity-0 animate-[fadeIn_0.1s_ease-out_forwards]"
          style={{ left: coords.x, top: coords.y }}
        >
          {content}
          {/* Arrow */}
          <div
            className={`absolute w-2 h-2 bg-gray-900 border-gray-700 transform rotate-45 ${
              position === 'top' ? 'bottom-[-5px] left-1/2 -translate-x-1/2 border-r border-b' :
              position === 'bottom' ? 'top-[-5px] left-1/2 -translate-x-1/2 border-l border-t' :
              position === 'left' ? 'right-[-5px] top-1/2 -translate-y-1/2 border-t border-r' :
              'left-[-5px] top-1/2 -translate-y-1/2 border-b border-l'
            }`}
          />
        </div>
      )}
      <style jsx global>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </>
  );
}
