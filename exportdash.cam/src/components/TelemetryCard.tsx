'use client';

import { useMemo } from 'react';
import { SeiData } from '@/lib/dashcam-mp4';
import Image from 'next/image';

interface TelemetryCardProps {
  seiData: SeiData | null;
  isLoading: boolean;
  error: string | null;
  speedUnit: 'mph' | 'kmh';
  onSpeedUnitToggle: () => void;
}

const AUTOPILOT_LABELS: Record<number, string> = {
  0: 'OFF',
  1: 'Self Driving',
  2: 'Autosteer',
  3: 'TACC',
};

export function TelemetryCard({
  seiData,
  isLoading,
  error,
  speedUnit,
  onSpeedUnitToggle,
}: TelemetryCardProps) {
  const displaySpeed = useMemo(() => {
    if (!seiData?.vehicle_speed_mps) return 0;
    return speedUnit === 'mph'
      ? Math.round(seiData.vehicle_speed_mps * 2.23694)
      : Math.round(seiData.vehicle_speed_mps * 3.6);
  }, [seiData, speedUnit]);

  const gearLetter = useMemo(() => {
    if (seiData?.gear_state === undefined) return 'P';
    return ['P', 'D', 'R', 'N'][seiData.gear_state] || 'P';
  }, [seiData]);

  const steeringAngle = seiData?.steering_wheel_angle || 0;
  // Clamp accelerator to 0-100% (value might already be 0-100 or 0-1)
  const rawAccel = seiData?.accelerator_pedal_position || 0;
  const acceleratorPosition = Math.min(100, rawAccel > 1 ? rawAccel : rawAccel * 100);
  const autopilotLabel = AUTOPILOT_LABELS[seiData?.autopilot_state ?? 0] || 'OFF';
  const isAutopilotActive = (seiData?.autopilot_state ?? 0) > 0;

  if (isLoading) {
    return (
      <div className="telemetry-card">
        <div className="flex items-center justify-center py-4 text-gray-500">
          <div className="w-5 h-5 border-2 border-gray-400 border-t-white rounded-full animate-spin mr-2" />
          Loading telemetry...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="telemetry-card">
        <div className="text-center py-3 text-gray-500 text-sm">{error}</div>
      </div>
    );
  }

  if (!seiData) {
    return (
      <div className="telemetry-card">
        <div className="text-center py-3 text-gray-500 text-sm">No telemetry data</div>
      </div>
    );
  }

  return (
    <div className="telemetry-wrapper">
      <div className="telemetry-card">
        {/* Column 1: Gear + Brake */}
        <div className="telemetry-column">
          <div className="telemetry-circle telemetry-gear">{gearLetter}</div>
          <div className={`telemetry-circle telemetry-brake ${seiData.brake_applied ? 'active' : ''}`}>
            <Image src="/left-pedal.png" alt="Brake" width={16} height={16} className="pedal-icon" />
          </div>
        </div>

        {/* Left Blinker */}
        <div className={`telemetry-blinker left ${seiData.blinker_on_left ? 'active' : ''}`}>
          <Image src="/blinker.svg" alt="Left" width={20} height={20} />
        </div>

        {/* Speed Display */}
        <div className="telemetry-speed" onClick={onSpeedUnitToggle}>
          <div className="speed-value">{displaySpeed}</div>
          <div className="speed-unit">{speedUnit}</div>
        </div>

        {/* Right Blinker */}
        <div className={`telemetry-blinker right ${seiData.blinker_on_right ? 'active' : ''}`}>
          <Image src="/blinker.svg" alt="Right" width={20} height={20} className="rotate-180" />
        </div>

        {/* Column 2: Steering + Accelerator */}
        <div className="telemetry-column">
          <div className={`telemetry-circle telemetry-steering ${isAutopilotActive ? 'autopilot' : ''}`}>
            <Image
              src="/wheel.svg"
              alt="Steering"
              width={16}
              height={16}
              className="wheel-icon"
              style={{ transform: `rotate(${steeringAngle}deg)` }}
            />
          </div>
          <div className="telemetry-circle telemetry-accelerator">
            <div className="accelerator-fill" style={{ height: `${acceleratorPosition}%` }} />
            <Image src="/right-pedal.png" alt="Accelerator" width={16} height={16} className="pedal-icon overlay" />
          </div>
        </div>
      </div>

      {/* Autopilot Label */}
      {isAutopilotActive && <div className="telemetry-autopilot">{autopilotLabel}</div>}

      <style jsx>{`
        .telemetry-wrapper {
          display: flex;
          flex-direction: column;
          align-items: center;
        }

        .telemetry-card {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 8px 12px;
          background: rgba(225, 225, 225, 0.85);
          border-radius: 12px;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }

        .telemetry-column {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }

        .telemetry-circle {
          width: 28px;
          height: 28px;
          border-radius: 50%;
          background: #a4a4a4;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .telemetry-gear {
          font-size: 16px;
          font-weight: 700;
          color: #006deb;
        }

        .telemetry-brake.active {
          background: #ff4444;
        }

        .telemetry-steering.autopilot {
          background: #006deb;
        }

        .telemetry-steering :global(.wheel-icon) {
          filter: brightness(0.5);
          transition: transform 0.1s ease-out;
        }

        .telemetry-steering.autopilot :global(.wheel-icon) {
          filter: brightness(0) invert(1);
        }

        .telemetry-accelerator {
          position: relative;
          overflow: hidden;
        }

        .accelerator-fill {
          position: absolute;
          bottom: 0;
          left: 0;
          right: 0;
          background: linear-gradient(to top, #4caf50, #8bc34a);
          transition: height 0.1s ease-out;
          border-radius: 0 0 50% 50%;
        }

        :global(.pedal-icon) {
          filter: brightness(0.6);
        }

        :global(.pedal-icon.overlay) {
          position: relative;
          z-index: 1;
        }

        .telemetry-blinker {
          opacity: 0.3;
          transition: opacity 0.2s;
        }

        .telemetry-blinker.active {
          opacity: 1;
          animation: blink 1s steps(1) infinite;
        }

        .telemetry-speed {
          display: flex;
          flex-direction: column;
          align-items: center;
          min-width: 50px;
          cursor: pointer;
          user-select: none;
        }

        .speed-value {
          font-size: 32px;
          font-weight: 600;
          line-height: 1;
          color: #333;
        }

        .speed-unit {
          font-size: 12px;
          font-weight: 600;
          color: #666;
          text-transform: uppercase;
        }

        .telemetry-autopilot {
          margin-top: -1px;
          padding: 2px 12px;
          background: rgba(255, 255, 255, 0.7);
          border-radius: 0 0 8px 8px;
          font-size: 11px;
          font-weight: 600;
          color: #006deb;
        }

        @keyframes blink {
          0% { opacity: 1; }
          50% { opacity: 0.3; }
        }

        @media (max-width: 640px) {
          .telemetry-card {
            gap: 10px;
            padding: 10px 15px;
          }

          .telemetry-circle {
            width: 32px;
            height: 32px;
          }

          .telemetry-gear {
            font-size: 18px;
          }

          .speed-value {
            font-size: 40px;
          }

          .speed-unit {
            font-size: 14px;
          }

          .telemetry-blinker :global(img) {
            width: 22px;
            height: 22px;
          }
        }
      `}</style>
    </div>
  );
}
