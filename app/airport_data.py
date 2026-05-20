from __future__ import annotations

AIRPORTS: dict[str, dict] = {
    "ATL": {"lat": 33.6407, "lon": -84.4277, "city": "Atlanta", "name": "Hartsfield-Jackson Atlanta International"},
    "DEN": {"lat": 39.8561, "lon": -104.6737, "city": "Denver", "name": "Denver International"},
    "LAS": {"lat": 36.0840, "lon": -115.1537, "city": "Las Vegas", "name": "Harry Reid International"},
    "MCO": {"lat": 28.4312, "lon": -81.3081, "city": "Orlando", "name": "Orlando International"},
    "PHL": {"lat": 39.8744, "lon": -75.2424, "city": "Philadelphia", "name": "Philadelphia International"},
    "DFW": {"lat": 32.8998, "lon": -97.0403, "city": "Dallas", "name": "Dallas/Fort Worth International"},
    "MIA": {"lat": 25.7959, "lon": -80.2870, "city": "Miami", "name": "Miami International"},
    "ORD": {"lat": 41.9742, "lon": -87.9073, "city": "Chicago", "name": "O'Hare International"},
    "LAX": {"lat": 33.9425, "lon": -118.4081, "city": "Los Angeles", "name": "Los Angeles International"},
    "TPA": {"lat": 27.9755, "lon": -82.5332, "city": "Tampa", "name": "Tampa International"},
    "FLL": {"lat": 26.0726, "lon": -80.1527, "city": "Fort Lauderdale", "name": "Fort Lauderdale-Hollywood International"},
    "PHX": {"lat": 33.4373, "lon": -112.0078, "city": "Phoenix", "name": "Phoenix Sky Harbor International"},
    "SFO": {"lat": 37.6213, "lon": -122.3790, "city": "San Francisco", "name": "San Francisco International"},
    "SAN": {"lat": 32.7338, "lon": -117.1933, "city": "San Diego", "name": "San Diego International"},
    "CLE": {"lat": 41.4117, "lon": -81.8498, "city": "Cleveland", "name": "Cleveland Hopkins International"},
    "CVG": {"lat": 39.0488, "lon": -84.6678, "city": "Cincinnati", "name": "Cincinnati/Northern Kentucky International"},
    "RDU": {"lat": 35.8801, "lon": -78.7880, "city": "Raleigh", "name": "Raleigh-Durham International"},
    "CLT": {"lat": 35.2140, "lon": -80.9431, "city": "Charlotte", "name": "Charlotte Douglas International"},
    "BWI": {"lat": 39.1754, "lon": -76.6683, "city": "Baltimore", "name": "Baltimore/Washington International"},
    "MDW": {"lat": 41.7868, "lon": -87.7522, "city": "Chicago", "name": "Chicago Midway International"},
    "SJU": {"lat": 18.4394, "lon": -66.0018, "city": "San Juan", "name": "Luis Muñoz Marín International"},
    "CUN": {"lat": 21.0365, "lon": -86.8771, "city": "Cancún", "name": "Cancún International"},
    # Additional Frontier hubs
    "BOI": {"lat": 43.5644, "lon": -116.2228, "city": "Boise", "name": "Boise Airport"},
    "ABQ": {"lat": 35.0402, "lon": -106.6090, "city": "Albuquerque", "name": "Albuquerque International Sunport"},
    "OKC": {"lat": 35.3931, "lon": -97.6007, "city": "Oklahoma City", "name": "Will Rogers World Airport"},
    "MSY": {"lat": 29.9934, "lon": -90.2580, "city": "New Orleans", "name": "Louis Armstrong New Orleans International"},
    "SAT": {"lat": 29.5337, "lon": -98.4698, "city": "San Antonio", "name": "San Antonio International"},
    "AUS": {"lat": 30.1975, "lon": -97.6664, "city": "Austin", "name": "Austin-Bergstrom International"},
    "MSP": {"lat": 44.8848, "lon": -93.2223, "city": "Minneapolis", "name": "Minneapolis-Saint Paul International"},
    "DTW": {"lat": 42.2162, "lon": -83.3554, "city": "Detroit", "name": "Detroit Metropolitan Wayne County Airport"},
    "PIT": {"lat": 40.4915, "lon": -80.2329, "city": "Pittsburgh", "name": "Pittsburgh International"},
    "BNA": {"lat": 36.1245, "lon": -86.6782, "city": "Nashville", "name": "Nashville International"},
    "JAX": {"lat": 30.4941, "lon": -81.6879, "city": "Jacksonville", "name": "Jacksonville International"},
    "MCI": {"lat": 39.2976, "lon": -94.7139, "city": "Kansas City", "name": "Kansas City International"},
    "STL": {"lat": 38.7487, "lon": -90.3700, "city": "St. Louis", "name": "St. Louis Lambert International"},
    "PDX": {"lat": 45.5898, "lon": -122.5951, "city": "Portland", "name": "Portland International"},
    "SEA": {"lat": 47.4502, "lon": -122.3088, "city": "Seattle", "name": "Seattle-Tacoma International"},
    "SLC": {"lat": 40.7899, "lon": -111.9791, "city": "Salt Lake City", "name": "Salt Lake City International"},
    "BUF": {"lat": 42.9405, "lon": -78.7322, "city": "Buffalo", "name": "Buffalo Niagara International"},
    "PUJ": {"lat": 18.5674, "lon": -68.3634, "city": "Punta Cana", "name": "Punta Cana International"},
    "GDL": {"lat": 20.5218, "lon": -103.3106, "city": "Guadalajara", "name": "Don Miguel Hidalgo y Costilla International"},
    "MZT": {"lat": 23.1614, "lon": -106.2659, "city": "Mazatlán", "name": "General Rafael Buelna International"},
    "PVR": {"lat": 20.6801, "lon": -105.2544, "city": "Puerto Vallarta", "name": "Licenciado Gustavo Díaz Ordaz International"},
    "SJD": {"lat": 23.1518, "lon": -109.7210, "city": "Los Cabos", "name": "Los Cabos International"},
}

AIRPORT_CODES: set[str] = set(AIRPORTS.keys())


def get_coords(iata: str) -> tuple[float, float] | None:
    info = AIRPORTS.get(iata.upper())
    if info:
        return info["lat"], info["lon"]
    return None


def get_airport_info(iata: str) -> dict | None:
    return AIRPORTS.get(iata.upper())
