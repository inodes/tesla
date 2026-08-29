'use client';

import { SeiData } from '@/lib/dashcam-mp4';

interface MetadataOverlayProps {
  seiData: SeiData | null;
  isLoading: boolean;
  error: string | null;
  speedUnit: 'mph' | 'kmh';
}

const GEAR_NAMES: Record<number, string> = {
  0: 'P',
  1: 'D',
  2: 'R',
  3: 'N',
};

const AUTOPILOT_NAMES: Record<number, string> = {
  0: 'Off',
  1: 'FSD',
  2: 'Autosteer',
  3: 'TACC',
};

export function MetadataOverlay({ seiData, isLoading, error, speedUnit }: MetadataOverlayProps) {
  if (isLoading) {
    return (
      <div className="absolute top-4 left-4 bg-black/70 backdrop-blur-sm rounded-lg px-4 py-2 text-white">
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          <span className="text-sm">Extracting metadata...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="absolute top-4 left-4 bg-red-900/70 backdrop-blur-sm rounded-lg px-4 py-2 text-white">
        <span className="text-sm">{error}</span>
      </div>
    );
  }

  if (!seiData) {
    return null;
  }

  // Convert speed (using snake_case field names from Tesla's protobuf)
  const speedMps = seiData.vehicle_speed_mps || 0;
  const speed = speedUnit === 'mph' ? speedMps * 2.23694 : speedMps * 3.6;
  const speedLabel = speedUnit === 'mph' ? 'mph' : 'km/h';

  // Convert G-forces
  const gX = (seiData.linear_acceleration_mps2_x || 0) / 9.81;
  const gY = (seiData.linear_acceleration_mps2_y || 0) / 9.81;

  const gear = GEAR_NAMES[seiData.gear_state ?? 0] || '?';
  const autopilot = AUTOPILOT_NAMES[seiData.autopilot_state ?? 0] || 'Off';

  return (
    <div className="absolute inset-x-0 bottom-0 p-4">
      <div className="bg-black/70 backdrop-blur-sm rounded-xl p-4 text-white">
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
          {/* Speed */}
          <div className="text-center">
            <div className="text-3xl font-bold tabular-nums">{Math.round(speed)}</div>
            <div className="text-xs text-gray-400 uppercase">{speedLabel}</div>
          </div>

          {/* Gear */}
          <div className="text-center">
            <div
              className={`text-3xl font-bold ${
                gear === 'D' ? 'text-green-400' : gear === 'R' ? 'text-red-400' : 'text-gray-300'
              }`}
            >
              {gear}
            </div>
            <div className="text-xs text-gray-400 uppercase">Gear</div>
          </div>

          {/* Steering */}
          <div className="text-center">
            <div className="text-2xl font-bold tabular-nums">
              {(seiData.steering_wheel_angle || 0).toFixed(1)}°
            </div>
            <div className="text-xs text-gray-400 uppercase">Steering</div>
          </div>

          {/* G-Force */}
          <div className="text-center">
            <div className="text-lg font-bold tabular-nums">
              <span className={gY > 0.1 ? 'text-green-400' : gY < -0.1 ? 'text-red-400' : ''}>
                {gY >= 0 ? '+' : ''}
                {gY.toFixed(2)}
              </span>
              <span className="text-gray-500 mx-1">/</span>
              <span className={gX > 0.1 ? 'text-yellow-400' : gX < -0.1 ? 'text-yellow-400' : ''}>
                {gX >= 0 ? '+' : ''}
                {gX.toFixed(2)}
              </span>
            </div>
            <div className="text-xs text-gray-400 uppercase">G (Lat/Long)</div>
          </div>

          {/* Autopilot */}
          <div className="text-center">
            <div
              className={`text-xl font-bold ${
                seiData.autopilot_state ? 'text-blue-400' : 'text-gray-500'
              }`}
            >
              {autopilot}
            </div>
            <div className="text-xs text-gray-400 uppercase">Autopilot</div>
          </div>

          {/* Indicators */}
          <div className="text-center flex items-center justify-center gap-3">
            {/* Brake */}
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center ${
                seiData.brake_applied ? 'bg-red-500' : 'bg-gray-700'
              }`}
              title="Brake"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path d="M10 18a8 8 0 100-16 8 8 0 000 16zM8 7a1 1 0 00-1 1v4a1 1 0 002 0V8a1 1 0 00-1-1zm4 0a1 1 0 00-1 1v4a1 1 0 002 0V8a1 1 0 00-1-1z" />
              </svg>
            </div>

            {/* Left Blinker */}
            <div
              className={`w-8 h-8 rounded flex items-center justify-center transition-colors ${
                seiData.blinker_on_left ? 'bg-green-500 animate-pulse' : 'bg-gray-700'
              }`}
              title="Left Blinker"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path
                  fillRule="evenodd"
                  d="M12.707 5.293a1 1 0 010 1.414L9.414 10l3.293 3.293a1 1 0 01-1.414 1.414l-4-4a1 1 0 010-1.414l4-4a1 1 0 011.414 0z"
                  clipRule="evenodd"
                />
              </svg>
            </div>

            {/* Right Blinker */}
            <div
              className={`w-8 h-8 rounded flex items-center justify-center transition-colors ${
                seiData.blinker_on_right ? 'bg-green-500 animate-pulse' : 'bg-gray-700'
              }`}
              title="Right Blinker"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path
                  fillRule="evenodd"
                  d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z"
                  clipRule="evenodd"
                />
              </svg>
            </div>
          </div>
        </div>

        {/* GPS Coordinates (if available) */}
        {seiData.latitude_deg !== undefined && seiData.longitude_deg !== undefined && (
          <div className="mt-3 pt-3 border-t border-gray-700 flex items-center justify-between text-sm">
            <div className="text-gray-400">
              <span className="font-mono">
                {seiData.latitude_deg.toFixed(6)}, {seiData.longitude_deg.toFixed(6)}
              </span>
            </div>
            {seiData.heading_deg !== undefined && (
              <div className="text-gray-400">
                Heading: <span className="font-mono">{seiData.heading_deg.toFixed(1)}°</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
