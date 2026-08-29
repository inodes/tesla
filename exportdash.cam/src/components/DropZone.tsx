'use client';

import { useCallback, useState } from 'react';

interface DropZoneProps {
  onFilesAdded: (files: File[]) => void;
  hasVideos: boolean;
}

export function DropZone({ onFilesAdded, hasVideos }: DropZoneProps) {
  const [isDragging, setIsDragging] = useState(false);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);

      const items = e.dataTransfer.items;
      const files: File[] = [];

      // Handle directory drops
      const processEntry = async (entry: FileSystemEntry): Promise<void> => {
        if (entry.isFile) {
          const fileEntry = entry as FileSystemFileEntry;
          const file = await new Promise<File>((resolve, reject) => {
            fileEntry.file(resolve, reject);
          });
          const name = file.name.toLowerCase();
          if (name.endsWith('.mp4') || name === 'event.json') {
            files.push(file);
          }
        } else if (entry.isDirectory) {
          const dirEntry = entry as FileSystemDirectoryEntry;
          const reader = dirEntry.createReader();
          const entries = await new Promise<FileSystemEntry[]>((resolve, reject) => {
            reader.readEntries(resolve, reject);
          });
          await Promise.all(entries.map(processEntry));
        }
      };

      // Process all dropped items
      const entries: FileSystemEntry[] = [];
      for (let i = 0; i < items.length; i++) {
        const entry = items[i].webkitGetAsEntry?.();
        if (entry) {
          entries.push(entry);
        }
      }

      if (entries.length > 0) {
        await Promise.all(entries.map(processEntry));
      } else {
        // Fallback for browsers without webkitGetAsEntry
        const droppedFiles = Array.from(e.dataTransfer.files).filter((f) => {
          const name = f.name.toLowerCase();
          return name.endsWith('.mp4') || name === 'event.json';
        });
        files.push(...droppedFiles);
      }

      if (files.length > 0) {
        onFilesAdded(files);
      }
    },
    [onFilesAdded]
  );

  const handleFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []).filter((f) => {
        const name = f.name.toLowerCase();
        return name.endsWith('.mp4') || name === 'event.json';
      });
      if (files.length > 0) {
        onFilesAdded(files);
      }
    },
    [onFilesAdded]
  );

  if (hasVideos) {
    return (
      <div
        className={`border-2 border-dashed rounded-lg p-4 text-center transition-colors ${
          isDragging ? 'border-blue-500 bg-blue-500/10' : 'border-gray-600 hover:border-gray-500'
        }`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <label className="cursor-pointer text-gray-400 hover:text-gray-300">
          <span className="text-sm">Drop more videos or click to add</span>
          <input
            type="file"
            accept="video/mp4,application/json"
            multiple
            onChange={handleFileInput}
            className="hidden"
          />
        </label>
      </div>
    );
  }

  // Sample Tesla file names for visual hint
  const sampleFiles = [
    '2026-01-24_18-40-57-front.mp4',
    '2026-01-24_18-41-57-front.mp4',
    '2026-01-24_18-40-57-back.mp4',
    '2026-01-24_18-40-57-left_repeater.mp4',
    '2026-01-24_18-40-57-right_repeater.mp4',
    '2026-01-24_18-40-57-left_pillar.mp4',
    '2026-01-24_18-40-57-right_pillar.mp4',
  ];

  return (
    <div
      className={`border-2 border-dashed rounded-xl p-12 text-center transition-all relative overflow-hidden ${
        isDragging
          ? 'border-blue-500 bg-blue-500/10 scale-[1.02]'
          : 'border-gray-600 hover:border-gray-500'
      }`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Decorative file list preview */}
      <div className="absolute -right-4 top-1/2 -translate-y-1/2 rotate-3 opacity-40 pointer-events-none select-none">
        <div className="bg-gray-800 rounded-lg p-3 shadow-2xl border border-gray-700 text-left w-72">
          <div className="flex items-center gap-2 mb-2 pb-2 border-b border-gray-700">
            <span className="text-[10px] text-gray-400 font-medium w-full">Name</span>
            <span className="text-[10px] text-gray-400 font-medium w-16 text-right">Size</span>
          </div>
          {sampleFiles.map((file, i) => (
            <div key={i} className="flex items-center gap-2 py-0.5">
              <svg className="w-3 h-3 text-gray-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" />
              </svg>
              <span className="text-[9px] text-gray-300 font-mono truncate">{file}</span>
              <span className="text-[9px] text-gray-500 w-12 text-right flex-shrink-0">80 MB</span>
            </div>
          ))}
          <div className="text-[9px] text-gray-600 mt-1">...</div>
        </div>
      </div>

      <div className="flex flex-col items-center gap-4 relative z-10">
        <div className="w-16 h-16 rounded-full bg-gray-800 flex items-center justify-center">
          <svg
            className="w-8 h-8 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
            />
          </svg>
        </div>
        <div className="text-center">
          <p className="text-xl font-medium text-gray-200">Drop your TeslaCam clips here</p>
          <p className="text-sm text-gray-500 mt-2 max-w-md">
            From your Tesla USB drive, navigate to{' '}
            <span className="text-gray-400 font-mono text-xs">TeslaCam</span> →{' '}
            <span className="text-gray-400 font-mono text-xs">SavedClips</span>,{' '}
            <span className="text-gray-400 font-mono text-xs">SentryClips</span>, or{' '}
            <span className="text-gray-400 font-mono text-xs">RecentClips</span>
            {' '}→ select a dated folder and drop all clips
          </p>
        </div>
        <label className="mt-4 px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg cursor-pointer transition-colors">
          <span>Browse Files</span>
          <input
            type="file"
            accept="video/mp4,application/json"
            multiple
            onChange={handleFileInput}
            className="hidden"
          />
        </label>
      </div>
    </div>
  );
}
