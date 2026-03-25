from __future__ import annotations

import random
from typing import Any

_US_FIRST_NAMES = [
    "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Elijah", "Sophia",
    "Lucas", "Mia", "Mason", "Amelia", "Ethan", "Harper", "Logan", "Evelyn",
]

_US_LAST_NAMES = [
    "Smith", "Johnson", "Brown", "Davis", "Wilson", "Moore", "Taylor", "Clark",
    "Hall", "Young", "Allen", "King", "Wright", "Scott", "Green", "Baker",
]

_UK_FIRST_NAMES = [
    "Oliver", "George", "Harry", "Noah", "Jack", "Isla", "Olivia", "Amelia",
    "Ava", "Lily", "Grace", "Freya", "Emily", "Arthur", "Leo", "Charlie",
]

_UK_LAST_NAMES = [
    "Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Davies", "Evans",
    "Thomas", "Roberts", "Johnson", "Walker", "Wright", "Hall", "Green", "Turner",
]

_US_STREETS = [
    "Main St", "Oak Ave", "Maple Dr", "Sunset Blvd", "Park Ave", "Washington St",
    "Lakeview Rd", "Hillcrest Dr", "Cedar Ln", "Broadway", "Riverside Dr", "Lincoln Ave",
]

_UK_STREETS = [
    "High Street", "Station Road", "Church Lane", "Victoria Road", "Park Road", "London Road",
    "Main Street", "Mill Lane", "The Crescent", "Queens Road", "Kingsway", "Green Lane",
]

_US_LOCATIONS = [
    {"state": "California", "city": "Los Angeles", "postal_prefix": "90"},
    {"state": "New York", "city": "Brooklyn", "postal_prefix": "11"},
    {"state": "Texas", "city": "Houston", "postal_prefix": "77"},
    {"state": "Florida", "city": "Miami", "postal_prefix": "33"},
    {"state": "Illinois", "city": "Chicago", "postal_prefix": "60"},
    {"state": "Washington", "city": "Seattle", "postal_prefix": "98"},
]

_UK_LOCATIONS = [
    {"state": "England", "city": "London", "postal_area": "SW"},
    {"state": "England", "city": "Manchester", "postal_area": "M"},
    {"state": "England", "city": "Birmingham", "postal_area": "B"},
    {"state": "Scotland", "city": "Glasgow", "postal_area": "G"},
    {"state": "England", "city": "Liverpool", "postal_area": "L"},
    {"state": "England", "city": "Leeds", "postal_area": "LS"},
]


def _choice(items: list[str]) -> str:
    return random.choice(items)


def _digits(length: int) -> str:
    return "".join(random.choice("0123456789") for _ in range(length))


def _letters(length: int) -> str:
    return "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(length))


def _person_name(country_code: str) -> str:
    code = str(country_code or "").upper()
    if code == "UK":
        return f"{_choice(_UK_FIRST_NAMES)} {_choice(_UK_LAST_NAMES)}"
    return f"{_choice(_US_FIRST_NAMES)} {_choice(_US_LAST_NAMES)}"


def _us_address() -> dict[str, Any]:
    location = random.choice(_US_LOCATIONS)
    building_number = str(random.randint(10, 9999))
    street_name = _choice(_US_STREETS)
    postal_code = f"{location['postal_prefix']}{_digits(3)}"
    return {
        "api_owner": "local-generator",
        "api_updates": "local-us",
        "building_number": building_number,
        "city": location["city"],
        "country": "United States",
        "country_code": "US",
        "country_flag": "🇺🇸",
        "currency": "USD",
        "gender": random.choice(["Female", "Male"]),
        "person_name": _person_name("US"),
        "phone_number": f"+1{_digits(10)}",
        "postal_code": postal_code,
        "state": location["state"],
        "street_address": f"{building_number} {street_name}",
        "street_name": street_name,
    }


def _uk_postcode(area: str) -> str:
    if len(area) == 1:
        district = f"{area}{random.randint(1, 9)}"
    else:
        district = f"{area}{random.randint(1, 20)}"
    inward = f"{random.randint(1, 9)}{_letters(2)}"
    return f"{district} {inward}"


def _uk_address() -> dict[str, Any]:
    location = random.choice(_UK_LOCATIONS)
    building_number = str(random.randint(1, 999))
    street_name = _choice(_UK_STREETS)
    postal_code = _uk_postcode(location["postal_area"])
    return {
        "api_owner": "local-generator",
        "api_updates": "local-uk",
        "building_number": building_number,
        "city": location["city"],
        "country": "United Kingdom",
        "country_code": "UK",
        "country_flag": "🇬🇧",
        "currency": "GBP",
        "gender": random.choice(["Female", "Male"]),
        "person_name": _person_name("UK"),
        "phone_number": f"+44 7{_digits(9)}",
        "postal_code": postal_code,
        "state": location["state"],
        "street_address": f"{building_number} {street_name}",
        "street_name": street_name,
    }


def generate_billing_address(country_code: str) -> dict[str, Any]:
    code = str(country_code or "US").strip().upper()
    if code == "UK":
        return _uk_address()
    return _us_address()
