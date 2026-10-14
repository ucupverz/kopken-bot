"""
Generator static map pakai OpenStreetMap.
Tidak butuh API key. Free.
"""

from io import BytesIO
from staticmap import StaticMap, CircleMarker, Line


# Zoom level
DEFAULT_ZOOM = 15
IMAGE_WIDTH = 600
IMAGE_HEIGHT = 400


def generate_map_with_markers(points, zoom=DEFAULT_ZOOM, draw_line=False):
    """
    Generate map image dari OpenStreetMap dengan multiple markers.
    
    Args:
        points: list of (lat, lng, color, label) — min 1 titik
        zoom: zoom level (default 15)
        draw_line: kalau True, gambar garis antar markers
    
    Return: PIL Image atau None
    """
    if not points:
        return None

    # Filter titik valid
    valid_points = []
    for p in points:
        lat, lng, color = p[0], p[1], p[2] if len(p) > 2 else 'red'
        try:
            lat_f = float(lat)
            lng_f = float(lng)
            if lat_f == 0 and lng_f == 0:
                continue
            valid_points.append((lat_f, lng_f, color))
        except Exception:
            continue

    if not valid_points:
        return None

    # Setup map
    m = StaticMap(IMAGE_WIDTH, IMAGE_HEIGHT,
                  url_template='http://a.tile.osm.org/{z}/{x}/{y}.png')

    # Tambah markers
    coords_for_line = []
    for lat, lng, color in valid_points:
        marker = CircleMarker((lng, lat), color, 12)
        m.add_marker(marker)
        coords_for_line.append((lng, lat))

    # Draw line antar marker (kalau ada > 1 titik)
    if draw_line and len(coords_for_line) >= 2:
        line = Line(coords_for_line, 'blue', 3)
        m.add_line(line)

    try:
        image = m.render(zoom=zoom)
        return image
    except Exception as e:
        print(f"[MAP] ❌ Render error: {e}")
        return None


def generate_map_driver_resto(driver_lat, driver_lng, store_lat, store_lng):
    """
    Generate map dengan 2 pin:
    - Driver (merah)
    - Restoran (biru)
    
    Return: BytesIO atau None
    """
    points = []

    # Driver marker (merah)
    if driver_lat and driver_lng:
        points.append((driver_lat, driver_lng, 'red'))

    # Restoran marker (biru)
    if store_lat and store_lng:
        points.append((store_lat, store_lng, 'blue'))

    if not points:
        return None

    # Kalau cuma 1 pin, zoom in lebih dekat
    zoom = 15 if len(points) >= 2 else 16

    image = generate_map_with_markers(points, zoom=zoom, draw_line=False)
    if not image:
        return None

    # Convert ke BytesIO
    bio = BytesIO()
    image.save(bio, 'PNG')
    bio.seek(0)
    return bio


def generate_map_single(lat, lng, zoom=16):
    """Generate map dengan 1 pin. Return BytesIO atau None."""
    points = [(lat, lng, 'red')]
    image = generate_map_with_markers(points, zoom=zoom)
    if not image:
        return None
    bio = BytesIO()
    image.save(bio, 'PNG')
    bio.seek(0)
    return bio