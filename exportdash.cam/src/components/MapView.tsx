'use client';

import { useEffect, useRef, useState } from 'react';
import { SeiData } from '@/lib/dashcam-mp4';

interface MapViewProps {
  seiData: SeiData | null;
  heading?: number;
}

// We need to dynamically import Leaflet to avoid SSR issues
export function MapView({ seiData, heading }: MapViewProps) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.Marker | null>(null);
  const [isMapReady, setIsMapReady] = useState(false);
  const [L, setL] = useState<typeof import('leaflet') | null>(null);

  const lat = seiData?.latitude_deg;
  const lng = seiData?.longitude_deg;
  const headingDeg = heading ?? seiData?.heading_deg ?? 0;

  const hasValidCoords = lat !== undefined && lng !== undefined &&
    lat !== 0 && lng !== 0 && !isNaN(lat) && !isNaN(lng);

  // Load Leaflet dynamically on client side
  useEffect(() => {
    import('leaflet').then((leaflet) => {
      setL(leaflet.default);
    });
  }, []);

  // Initialize map once Leaflet is loaded - only runs once
  useEffect(() => {
    if (!L || !mapContainerRef.current || mapRef.current) return;

    // Default center (San Francisco)
    const map = L.map(mapContainerRef.current, {
      center: [37.7749, -122.4194],
      zoom: 17,
      zoomControl: false,
      attributionControl: false,
    });

    // Add OpenStreetMap tiles
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OSM',
      maxZoom: 19,
    }).addTo(map);

    mapRef.current = map;

    // Force a resize after a short delay to ensure tiles load
    setTimeout(() => {
      map.invalidateSize();
      setIsMapReady(true);
    }, 100);

    return () => {
      map.remove();
      mapRef.current = null;
      markerRef.current = null;
    };
  }, [L]); // Only depend on L - initialize once

  // Store last heading to avoid unnecessary icon updates
  const lastHeadingRef = useRef<number>(0);

  // Update marker when coordinates change
  useEffect(() => {
    if (!L || !mapRef.current || !isMapReady) return;

    const map = mapRef.current;

    if (hasValidCoords && lat !== undefined && lng !== undefined) {
      // Only recreate icon if heading changed significantly (> 5 degrees)
      const headingChanged = Math.abs(headingDeg - lastHeadingRef.current) > 5;

      if (!markerRef.current) {
        // Create marker for the first time
        const carIcon = L.divIcon({
          className: 'car-marker-container',
          html: `
            <div class="car-icon-inner" style="transform: rotate(${headingDeg}deg);">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M12 2L7 12H17L12 2Z" fill="#3B82F6" stroke="#1E40AF" stroke-width="1.5"/>
                <circle cx="12" cy="15" r="5" fill="#3B82F6" stroke="#1E40AF" stroke-width="1.5"/>
                <circle cx="12" cy="15" r="2" fill="#1E40AF"/>
              </svg>
            </div>
          `,
          iconSize: [24, 24],
          iconAnchor: [12, 12],
        });
        markerRef.current = L.marker([lat, lng], { icon: carIcon }).addTo(map);
        lastHeadingRef.current = headingDeg;
      } else {
        // Update position (always)
        markerRef.current.setLatLng([lat, lng]);

        // Only update icon if heading changed significantly
        if (headingChanged) {
          const carIcon = L.divIcon({
            className: 'car-marker-container',
            html: `
              <div class="car-icon-inner" style="transform: rotate(${headingDeg}deg);">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                  <path d="M12 2L7 12H17L12 2Z" fill="#3B82F6" stroke="#1E40AF" stroke-width="1.5"/>
                  <circle cx="12" cy="15" r="5" fill="#3B82F6" stroke="#1E40AF" stroke-width="1.5"/>
                  <circle cx="12" cy="15" r="2" fill="#1E40AF"/>
                </svg>
              </div>
            `,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
          });
          markerRef.current.setIcon(carIcon);
          lastHeadingRef.current = headingDeg;
        }
      }

      // Pan to position without animation for smoother updates
      map.setView([lat, lng], map.getZoom(), { animate: false });
    } else if (markerRef.current) {
      // Remove marker if no valid coords
      markerRef.current.remove();
      markerRef.current = null;
    }
  }, [L, lat, lng, headingDeg, hasValidCoords, isMapReady]);

  return (
    <div className="relative rounded-lg overflow-hidden bg-gray-900 w-full h-full" style={{ minHeight: '150px' }}>
      <div
        ref={mapContainerRef}
        style={{ width: '100%', height: '100%' }}
      />

      {/* Loading state */}
      {!isMapReady && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-900">
          <div className="text-gray-500 text-sm">Loading map...</div>
        </div>
      )}

      {/* No GPS overlay */}
      {isMapReady && !hasValidCoords && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-900/80 backdrop-blur-sm">
          <div className="text-center text-gray-500 text-sm">
            <svg className="w-8 h-8 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            <p>No GPS data</p>
          </div>
        </div>
      )}

      {/* Coordinates overlay */}
      {isMapReady && hasValidCoords && (
        <div className="absolute bottom-1 left-1 bg-black/60 rounded px-1.5 py-0.5 text-[9px] font-mono text-gray-400 z-[1000]">
          {lat?.toFixed(5)}, {lng?.toFixed(5)} {headingDeg > 0 && <span className="text-gray-500">{Math.round(headingDeg)}°</span>}
        </div>
      )}
    </div>
  );
}
