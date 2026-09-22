import type { GeoPoint, GeoPolygon, Snapshot, Zone } from "./types";

export type Ring = [number, number][];

/** GeoJSON ring (lon, lat), closed. */
export function polygonRing(polygon: GeoPolygon): Ring {
  const ring: Ring = polygon.vertices.map((v) => [v.longitude, v.latitude]);
  const first = ring[0];
  if (first) ring.push([first[0], first[1]]);
  return ring;
}

export function zonesToFeatureCollection(zones: Zone[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: zones.map((z) => ({
      type: "Feature",
      id: z.zone_id,
      properties: {
        zone_id: z.zone_id,
        status: z.status,
        coverage: z.coverage,
        assigned_drone_id: z.assigned_drone_id,
      },
      geometry: { type: "Polygon", coordinates: [polygonRing(z.polygon)] },
    })),
  };
}

export function pointFeature(
  point: GeoPoint,
  properties: Record<string, unknown>,
): GeoJSON.Feature<GeoJSON.Point> {
  return {
    type: "Feature",
    properties,
    geometry: { type: "Point", coordinates: [point.longitude, point.latitude] },
  };
}

export function dronesToFeatureCollection(snapshot: Snapshot): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: snapshot.drones
      .filter((d) => d.state !== null)
      .map((d) =>
        pointFeature(d.state!.position, {
          drone_id: d.drone.drone_id,
          label: d.drone.drone_id.replace("drone-", "D"),
          status: d.state!.status,
          link_state: d.link_state,
          battery: d.state!.battery_percent,
          heading: d.state!.heading_deg,
        }),
      ),
  };
}

export function bounds(polygon: GeoPolygon): [[number, number], [number, number]] {
  const lons = polygon.vertices.map((v) => v.longitude);
  const lats = polygon.vertices.map((v) => v.latitude);
  return [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
}
