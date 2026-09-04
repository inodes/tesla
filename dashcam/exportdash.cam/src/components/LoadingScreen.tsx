'use client';

import { ProcessingProgress } from '@/types/video';

interface LoadingScreenProps {
  progress: ProcessingProgress;
}

const STAGES = [
  { id: 'scanning', label: 'Scanning', description: 'Finding video files' },
  { id: 'metadata', label: 'Processing', description: 'Extracting metadata' },
  { id: 'ready', label: 'Ready', description: 'Preparing playback' },
] as const;

export function LoadingScreen({ progress }: LoadingScreenProps) {
  const { stage, current, total, message } = progress;
  const percentage = total > 0 ? Math.round((current / total) * 100) : 0;

  // Find current stage index
  const currentStageIndex = STAGES.findIndex(s => s.id === stage);

  return (
    <div className="fixed inset-0 z-50 bg-gray-950/95 backdrop-blur-sm flex items-center justify-center">
      <div className="max-w-md w-full mx-4">
        {/* Logo / Icon */}
        <div className="text-center mb-8">
          <div className="w-16 h-16 mx-auto rounded-2xl bg-red-600 flex items-center justify-center mb-4">
            <svg className="w-10 h-10 animate-pulse" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-white">Processing Videos</h2>
          <p className="text-gray-400 text-sm mt-1">
            {message || 'Please wait while we prepare your footage...'}
          </p>
        </div>

        {/* Progress Bar */}
        <div className="mb-6">
          <div className="flex justify-between text-sm mb-2">
            <span className="text-gray-400">
              {current} of {total} files
            </span>
            <span className="text-gray-300 font-medium">{percentage}%</span>
          </div>
          <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-red-600 to-red-500 rounded-full transition-all duration-300 ease-out"
              style={{ width: `${percentage}%` }}
            />
          </div>
        </div>

        {/* Stage Indicators */}
        <div className="flex items-center justify-between">
          {STAGES.map((s, index) => {
            const isCompleted = index < currentStageIndex;
            const isCurrent = index === currentStageIndex;
            const isPending = index > currentStageIndex;

            return (
              <div key={s.id} className="flex flex-col items-center flex-1">
                {/* Stage dot */}
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center mb-2 transition-all ${
                    isCompleted
                      ? 'bg-green-600 text-white'
                      : isCurrent
                      ? 'bg-red-600 text-white ring-4 ring-red-600/30'
                      : 'bg-gray-800 text-gray-500'
                  }`}
                >
                  {isCompleted ? (
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : isCurrent ? (
                    <div className="w-2 h-2 bg-white rounded-full animate-pulse" />
                  ) : (
                    <div className="w-2 h-2 bg-gray-600 rounded-full" />
                  )}
                </div>

                {/* Stage label */}
                <span
                  className={`text-xs font-medium ${
                    isCompleted || isCurrent ? 'text-gray-300' : 'text-gray-500'
                  }`}
                >
                  {s.label}
                </span>

                {/* Connector line (except for last stage) */}
                {index < STAGES.length - 1 && (
                  <div
                    className={`absolute h-0.5 top-4 -translate-y-1/2 ${
                      isCompleted ? 'bg-green-600' : 'bg-gray-800'
                    }`}
                    style={{
                      left: `${((index + 0.5) / STAGES.length) * 100}%`,
                      right: `${((STAGES.length - index - 1.5) / STAGES.length) * 100}%`,
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>

        {/* Connecting lines between stages */}
        <div className="relative h-0 -mt-[58px] mb-[58px]">
          <div className="absolute top-0 left-[16.67%] right-[16.67%] flex">
            {STAGES.slice(0, -1).map((_, index) => {
              const isCompleted = index < currentStageIndex;
              return (
                <div
                  key={index}
                  className={`flex-1 h-0.5 mx-4 ${
                    isCompleted ? 'bg-green-600' : 'bg-gray-800'
                  }`}
                />
              );
            })}
          </div>
        </div>

        {/* Tip */}
        <div className="mt-8 text-center">
          <p className="text-xs text-gray-500">
            Tip: For best results, drop entire folders from your Tesla USB drive
          </p>
        </div>
      </div>
    </div>
  );
}
