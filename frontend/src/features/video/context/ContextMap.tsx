/**
 * OpenStreetMap map (Leaflet, BSD-2), loaded lazily as its own chunk. It is only mounted when
 * online services are enabled: offline, no tile request ever leaves the browser.
 * The browser's default Referer must be kept, as the OSM tile policy requires.
 */
import "leaflet/dist/leaflet.css";

import L from "leaflet";
import { useEffect, useRef } from "react";

export const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export default function ContextMap({
  latitude,
  longitude,
  track,
  label,
}: {
  latitude: number;
  longitude: number;
  track: readonly (readonly [number, number])[];
  label: string;
}) {
  const container = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = container.current;
    if (!node) return undefined;
    const map = L.map(node, { attributionControl: true, scrollWheelZoom: false }).setView(
      [latitude, longitude],
      15,
    );
    L.tileLayer(TILE_URL, { maxZoom: 19, attribution: ATTRIBUTION }).addTo(map);
    const accent = getComputedStyle(node).getPropertyValue("--primary").trim() || "#ff3366";
    if (track.length > 1) {
      const line = L.polyline(
        track.map(([lat, lon]) => [lat, lon] as [number, number]),
        { color: accent, weight: 3, opacity: 0.9 },
      ).addTo(map);
      map.fitBounds(line.getBounds(), { padding: [24, 24], maxZoom: 16 });
    }
    // Place names come from OpenStreetMap/GeoNames: text only (Leaflet renders strings as HTML).
    const tooltip = document.createElement("span");
    tooltip.textContent = label;
    L.circleMarker([latitude, longitude], {
      radius: 8,
      weight: 2,
      color: "#ffffff",
      fillColor: accent,
      fillOpacity: 1,
    })
      .bindTooltip(tooltip)
      .addTo(map);
    return () => {
      map.remove();
    };
  }, [latitude, longitude, track, label]);

  return (
    <div
      ref={container}
      role="region"
      aria-label={label}
      className="h-72 w-full overflow-hidden rounded-lg border"
    />
  );
}
