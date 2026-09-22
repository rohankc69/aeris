"use client";

import { GeoJSONSource, Map as MapLibreMap, type MapLayerMouseEvent, type StyleSpecification } from "maplibre-gl";
import { useEffect, useRef } from "react";
import { bounds, dronesToFeatureCollection, pointFeature, polygonRing, zonesToFeatureCollection } from "@/lib/geo";
import type { Snapshot } from "@/lib/types";

const ZONE_COLORS: Record<string, string> = {
  UNSEARCHED: "#3a4652",
  ASSIGNED: "#7b6a2a",
  SEARCHING: "#2d6a8f",
  PARTIAL: "#8a5a1e",
  COMPLETE: "#2f7d4a",
  REQUIRES_RECHECK: "#8f3d3d",
};

// OpenStreetMap raster tiles: fine for development. Use a proper tile provider in production.
const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

interface Props {
  snapshot: Snapshot | null;
  selectedDrone: string | null;
  onSelectDrone: (droneId: string) => void;
}

export function MissionMap({ snapshot, selectedDrone, onSelectDrone }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const fittedMission = useRef<string | null>(null);

  const recenter = () => {
    const m = map.current;
    if (m && snapshot) m.fitBounds(bounds(snapshot.mission.search_area.polygon), { padding: 60, duration: 300 });
  };

  useEffect(() => {
    if (!container.current || map.current) return;
    const m = new MapLibreMap({
      container: container.current,
      style: STYLE,
      center: [8.55, 47.38],
      zoom: 13,
      attributionControl: { compact: true },
    });
    m.on("load", () => {
      m.addSource("area", { type: "geojson", data: emptyCollection() });
      m.addSource("zones", { type: "geojson", data: emptyCollection() });
      m.addSource("drones", { type: "geojson", data: emptyCollection() });
      m.addSource("base", { type: "geojson", data: emptyCollection() });
      m.addSource("detections", { type: "geojson", data: emptyCollection() });

      m.addLayer({
        id: "zones-fill",
        type: "fill",
        source: "zones",
        paint: {
          "fill-color": ["get", "color"],
          "fill-opacity": ["interpolate", ["linear"], ["get", "coverage"], 0, 0.25, 1, 0.55],
        },
      });
      m.addLayer({
        id: "zones-line",
        type: "line",
        source: "zones",
        paint: { "line-color": "#9fb3c8", "line-width": 1 },
      });
      m.addLayer({
        id: "zones-label",
        type: "symbol",
        source: "zones",
        layout: { "text-field": ["get", "label"], "text-size": 11 },
        paint: { "text-color": "#e6edf3", "text-halo-color": "#0f1419", "text-halo-width": 1 },
      });
      m.addLayer({
        id: "area-line",
        type: "line",
        source: "area",
        paint: { "line-color": "#4cc2ff", "line-width": 2, "line-dasharray": [2, 2] },
      });
      m.addLayer({
        id: "base",
        type: "circle",
        source: "base",
        paint: { "circle-radius": 7, "circle-color": "#e6edf3", "circle-stroke-color": "#0f1419", "circle-stroke-width": 2 },
      });
      m.addLayer({
        id: "detections",
        type: "circle",
        source: "detections",
        paint: {
          "circle-radius": ["match", ["get", "kind"], "confirmed", 11, "candidate", 9, 6],
          "circle-color": ["match", ["get", "kind"], "confirmed", "#3fb950", "candidate", "#f85149", "#d29922"],
          "circle-opacity": 0.85,
          "circle-stroke-color": "#0f1419",
          "circle-stroke-width": 2,
        },
      });
      m.addLayer({
        id: "drones",
        type: "circle",
        source: "drones",
        paint: {
          "circle-radius": ["case", ["get", "selected"], 10, 7],
          "circle-color": [
            "match",
            ["get", "link_state"],
            "CONNECTED", "#4cc2ff",
            "DEGRADED", "#d29922",
            "#f85149",
          ],
          "circle-stroke-color": "#0f1419",
          "circle-stroke-width": 2,
        },
      });
      m.addLayer({
        id: "drones-label",
        type: "symbol",
        source: "drones",
        layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.4] },
        paint: { "text-color": "#e6edf3", "text-halo-color": "#0f1419", "text-halo-width": 1 },
      });
      m.on("click", "drones", (e: MapLayerMouseEvent) => {
        const id = e.features?.[0]?.properties?.drone_id as string | undefined;
        if (id) onSelectDrone(id);
      });
      m.on("mouseenter", "drones", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "drones", () => (m.getCanvas().style.cursor = ""));
    });
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
    // onSelectDrone is stable enough for the map's lifetime; re-creating the map is worse.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !snapshot || !m.isStyleLoaded()) return;
    const setData = (id: string, data: GeoJSON.FeatureCollection) =>
      m.getSource<GeoJSONSource>(id)?.setData(data);

    const zones = zonesToFeatureCollection(snapshot.zones);
    for (const f of zones.features) {
      const status = f.properties?.status as string;
      f.properties = {
        ...f.properties,
        color: ZONE_COLORS[status] ?? "#3a4652",
        label: `${f.properties?.zone_id} ${Math.round((f.properties?.coverage as number) * 100)}%`,
      };
    }
    setData("zones", zones);

    const drones = dronesToFeatureCollection(snapshot);
    for (const f of drones.features) {
      f.properties = { ...f.properties, selected: f.properties?.drone_id === selectedDrone };
    }
    setData("drones", drones);

    setData("area", {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {},
          geometry: { type: "Polygon", coordinates: [polygonRing(snapshot.mission.search_area.polygon)] },
        },
      ],
    });
    setData("base", { type: "FeatureCollection", features: [pointFeature(snapshot.mission.base_position, { label: "BASE" })] });
    const openCandidates = new Set(snapshot.candidates.filter((c) => c.confirmed === null).map((c) => c.detection_id));
    const confirmed = new Set(snapshot.candidates.filter((c) => c.confirmed === true).map((c) => c.detection_id));
    setData("detections", {
      type: "FeatureCollection",
      features: snapshot.detections.map((d) =>
        pointFeature(d.position, {
          id: d.detection_id,
          kind: confirmed.has(d.detection_id) ? "confirmed" : openCandidates.has(d.detection_id) ? "candidate" : "detection",
        }),
      ),
    });

    // Zoom to the search area whenever a different mission is shown, not just the first one.
    if (fittedMission.current !== snapshot.mission.mission_id) {
      m.fitBounds(bounds(snapshot.mission.search_area.polygon), { padding: 60, duration: 0 });
      fittedMission.current = snapshot.mission.mission_id;
    }
  }, [snapshot, selectedDrone]);

  return (
    <div className="map">
      <div ref={container} className="map-canvas" />
      {snapshot && (
        <button className="map-recenter" onClick={recenter} title="Zoom to the search area">
          Recenter
        </button>
      )}
    </div>
  );
}

function emptyCollection(): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}
