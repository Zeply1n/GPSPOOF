"""GPX parsing, geodesic interpolation and route playback helpers."""
from __future__ import annotations
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from models import Coordinate

EARTH_RADIUS_METERS = 6_371_008.8

def distance(a: Coordinate, b: Coordinate) -> float:
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dp, dl = p2-p1, math.radians(b.longitude-a.longitude)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(h))

def interpolate(a: Coordinate, b: Coordinate, fraction: float) -> Coordinate:
    fraction = min(1.0, max(0.0, fraction))
    return Coordinate(a.latitude + (b.latitude-a.latitude)*fraction,
                      a.longitude + (b.longitude-a.longitude)*fraction)

def nudge(origin: Coordinate, direction: str, meters: float) -> Coordinate:
    if meters <= 0 or meters > 10000: raise ValueError("step must be between 0 and 10000 meters")
    bearings = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}
    if direction.upper() not in bearings: raise ValueError("invalid direction")
    bearing, angular = math.radians(bearings[direction.upper()]), meters/EARTH_RADIUS_METERS
    lat1, lon1 = math.radians(origin.latitude), math.radians(origin.longitude)
    lat2 = math.asin(math.sin(lat1)*math.cos(angular)+math.cos(lat1)*math.sin(angular)*math.cos(bearing))
    lon2 = lon1 + math.atan2(math.sin(bearing)*math.sin(angular)*math.cos(lat1), math.cos(angular)-math.sin(lat1)*math.sin(lat2))
    return Coordinate(math.degrees(lat2), ((math.degrees(lon2)+540)%360)-180)

def parse_gpx(data: bytes) -> list[Coordinate]:
    if len(data) > 10_000_000: raise ValueError("GPX is too large")
    root = ET.fromstring(data)
    points = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] in {"trkpt", "rtept", "wpt"}:
            points.append(Coordinate(float(element.attrib["lat"]), float(element.attrib["lon"])))
    if len(points) < 2: raise ValueError("GPX needs at least two points")
    return points

@dataclass
class Route:
    points: list[Coordinate]
    progress: float = 0.0
    playing: bool = False
    paused: bool = False
    loop: bool = False
    speed_mps: float = 1.4

    def at(self, progress: float) -> Coordinate:
        scaled = min(1.0, max(0.0, progress))*(len(self.points)-1); i = min(int(scaled), len(self.points)-2)
        return interpolate(self.points[i], self.points[i+1], scaled-i)
