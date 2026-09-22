import { describe, expect, it } from "vitest";
import { bounds, polygonRing, zonesToFeatureCollection } from "@/lib/geo";
import type { Zone } from "@/lib/types";

const zone: Zone = {
  zone_id: "A1",
  polygon: {
    vertices: [
      { latitude: 0, longitude: 0, altitude_m: 0 },
      { latitude: 0, longitude: 1, altitude_m: 0 },
      { latitude: 1, longitude: 1, altitude_m: 0 },
    ],
  },
  priority: 0.5,
  coverage: 0.25,
  assigned_drone_id: "drone-01",
  status: "SEARCHING",
};

describe("geo helpers", () => {
  it("closes the ring in lon/lat order", () => {
    const ring = polygonRing(zone.polygon);
    expect(ring).toHaveLength(4);
    expect(ring[0]).toEqual([0, 0]);
    expect(ring[1]).toEqual([1, 0]);
    expect(ring[3]).toEqual(ring[0]);
  });

  it("builds a feature collection with zone properties", () => {
    const fc = zonesToFeatureCollection([zone]);
    expect(fc.features).toHaveLength(1);
    const f = fc.features[0]!;
    expect(f.id).toBe("A1");
    expect(f.properties).toMatchObject({ status: "SEARCHING", coverage: 0.25 });
    expect(f.geometry.type).toBe("Polygon");
  });

  it("computes bounds as sw/ne", () => {
    expect(bounds(zone.polygon)).toEqual([
      [0, 0],
      [1, 1],
    ]);
  });
});
