import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import sync_playwright

CURRENCY_SUFFIX = r"(?:EUR|PLN|CZK|zł|€)"
CURRENCY_PREFIX = r"(?:€|EUR)"
PRICE_NUM = r"[\d][\d.,]*"
PRICE_PATTERN = (
    rf"(?:{CURRENCY_PREFIX}\s*{PRICE_NUM}|"
    rf"{PRICE_NUM}\s*{CURRENCY_SUFFIX})"
)
ROUTE_PATTERN = r"\b([A-Z]{3})\s*[-–>]+\s*([A-Z]{3})\b"
ROUTE_PATTERN_LOOSE = r"\b([A-Z]{3})\s*(?:[-–>]|→|to)\s*([A-Z]{3})\b"
FLIGHT_NO_PATTERN = r"\b([A-Z]{1,3}\d{2,5})\b"
TIME_PATTERN = r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b"
BRACKET_IATA_PATTERN = r"\[([A-Z]{3})\]"
IATA_PATTERN = r"\b[A-Z]{3}\b"

NON_IATA_CODES = {"PLN", "EUR", "CZK", "THE", "THU", "FRI", "SAT", "SUN", "MON", "TUE", "WED"}
DISPLAY_DATE_PATTERN = (
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{2})/(\d{2})/(\d{2})"
)
AIRLINE_TRACKBOOK_PATTERN = r"trackBook\s*\(\s*'([A-Z0-9]{2,3})'"
CODE_SPAN_PATTERN = r'<span class="code">([A-Z]{3})'


@dataclass
class ScrapeResult:
    offers: list
    error: Optional[str] = None
    message: str = ""



def parse_date(s):
    # zmiana formatu daty
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    parts = s.split(".")
    if len(parts) != 3:
        return ""
    try:
        d = int(parts[0])
        m = int(parts[1])
        y = int(parts[2])
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return ""


def parse_display_date(text):
    match = re.search(DISPLAY_DATE_PATTERN, text)
    if not match:
        return ""
    day, month, year = match.groups()
    return f"20{year}-{month}-{day}"


def parse_display_dates(text):
    dates = []
    for day, month, year in re.findall(DISPLAY_DATE_PATTERN, text):
        dates.append(f"20{year}-{month}-{day}")
    return dates


def price_amount(price_text):
    cleaned = price_text.upper()
    for token in ("EUR", "PLN", "CZK", "€", "ZŁ", "ZL"):
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.strip().replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_price_text(price_text):
    """Zwraca (kwota: float, waluta: str). Waluta = 'UNKNOWN' jesli nie rozpoznano."""
    text = price_text.strip().replace("\xa0", " ").upper()

    currency = "UNKNOWN"
    if "PLN" in text or "ZŁ" in text or "ZL" in text:
        currency = "PLN"
    elif "EUR" in text or "€" in text:
        currency = "EUR"
    elif "CZK" in text:
        currency = "CZK"
    elif "GBP" in text or "£" in text:
        currency = "GBP"
    elif "USD" in text or "$" in text:
        currency = "USD"

    return price_amount(text), currency


def find_all_prices(text):
    return [m.group(0).strip() for m in re.finditer(PRICE_PATTERN, text, re.IGNORECASE)]


def pick_total_price(text):
    prices = find_all_prices(text)
    if not prices:
        return None
    return max(prices, key=price_amount)


def parse_flight_url(url):
    url_parsed = urllib.parse.urlparse(url.strip())
    qs = urllib.parse.parse_qs(url_parsed.query)

    # 
    origin = None
    if get_first(qs, "srcap0"): origin = get_first(qs, "srcap0")
    elif get_first(qs, "srcap1"): origin = get_first(qs, "srcap1")
    elif get_first(qs, "srcap"): origin = get_first(qs, "srcap")
    elif get_first(qs, "srcAirport"): origin = get_first(qs, "srcAirport")
    elif get_first(qs, "srcAirport0"): origin = get_first(qs, "srcAirport0")
    elif get_first(qs, "origin"): origin = get_first(qs, "origin")
    
    if origin:
        origin = origin.upper()
    if origin and len(origin) > 3:
        src_air_raw = get_first(qs, "srcAirport")
        if src_air_raw:
            bracket = re.search(BRACKET_IATA_PATTERN, src_air_raw.upper())
            if bracket:
                origin = bracket.group(1)

    dst_parts = []
    keys_to_check = ["dstap0", "dstap1", "dstap2", "dstap3"]
    for key in keys_to_check:
        v = get_first(qs, key)
        if v:
            dst_parts.append(v.upper())
            
    dst_mc = None
    if get_first(qs, "dstMC"): dst_mc = get_first(qs, "dstMC")
    elif get_first(qs, "dstmc"): dst_mc = get_first(qs, "dstmc")

    destination = ""
    if len(dst_parts) > 0:
        destination = "/".join(dst_parts)
        
    if destination == "" and dst_mc:
        dst_mc_clean = dst_mc.upper().replace("_ALL", "").strip("_")
        if dst_mc_clean:
            destination = dst_mc_clean
        else:
            destination = dst_mc.upper()

    if destination == "":
        dst_air_raw = get_first(qs, "dstAirport")
        if dst_air_raw:
            m = re.search(BRACKET_IATA_PATTERN, dst_air_raw.upper())
            if m:
                destination = m.group(1)

    dep_raw = None
    if get_first(qs, "depdate"): dep_raw = get_first(qs, "depdate")
    elif get_first(qs, "depDate"): dep_raw = get_first(qs, "depDate")
    
    arr_raw = None
    if get_first(qs, "arrdate"): arr_raw = get_first(qs, "arrdate")
    elif get_first(qs, "arrDate"): arr_raw = get_first(qs, "arrDate")

    departure_date = ""
    if dep_raw:
        departure_date = parse_date(dep_raw)
        
    return_date = None
    if arr_raw:
        return_date = parse_date(arr_raw)

    result = {
        "origin": origin or "",
        "destination": destination or "",
        "departure_date": departure_date,
        "return_date": return_date,
    }
    return result


def extract_offer(text, html=""):
    if text == "" or text == None:
        return None

    price_text = pick_total_price(text)
    if not price_text:
        return None

    route_match = re.search(ROUTE_PATTERN, text)
    if not route_match:
        route_match = re.search(ROUTE_PATTERN_LOOSE, text, re.IGNORECASE)
        
    flight_match = re.search(FLIGHT_NO_PATTERN, text, re.IGNORECASE)
    time_matches = re.findall(TIME_PATTERN, text)

    airport_codes = []
    if html:
        for code in re.findall(CODE_SPAN_PATTERN, html, re.IGNORECASE):
            airport_codes.append(code.upper())
    all_iata_matches = re.findall(IATA_PATTERN, text.upper())
    for c in all_iata_matches:
        if c not in NON_IATA_CODES and c not in airport_codes:
            airport_codes.append(c)

    origin = "UNK"
    if route_match:
        origin = route_match.group(1).upper()
        
    destination = "UNK"
    if route_match:
        destination = route_match.group(2).upper()
        
    if (origin == "UNK" or destination == "UNK") and len(airport_codes) >= 2:
        origin = airport_codes[0]
        destination = airport_codes[1]

    departure_time = ""
    if len(time_matches) >= 1:
        departure_time = time_matches[0]
        
    arrival_time = ""
    if len(time_matches) >= 2:
        arrival_time = time_matches[1]
        
    flight_number = "UNKNOWN"
    if flight_match:
        flight_number = flight_match.group(1).upper()

    airline = "UNKNOWN"
    airline_match = re.search(AIRLINE_TRACKBOOK_PATTERN, html or text)
    if airline_match:
        airline = airline_match.group(1).upper()

    leg_dates = parse_display_dates(text)
    departure_date = leg_dates[0] if len(leg_dates) >= 1 else ""
    return_date = leg_dates[1] if len(leg_dates) >= 2 else None

    offer_dict = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "return_date": return_date,
        "departure_time": departure_time,
        "arrival_time": arrival_time,
        "price_text": price_text,
        "airline": airline,
        "flight_number": flight_number,
    }
    return offer_dict


def merge_offer(offer, ctx):
    if offer["origin"] == "UNK" or offer["origin"] == "":
        if "origin" in ctx and ctx["origin"] != "":
            offer["origin"] = ctx["origin"]
            
    if offer["destination"] == "UNK" or offer["destination"] == "":
        if "destination" in ctx and ctx["destination"] != "":
            offer["destination"] = ctx["destination"]
            
    dep_date = ""
    if "departure_date" in ctx:
        dep_date = ctx["departure_date"]
        
    dep_time = offer["departure_time"]
    
    if offer["departure_date"] == "" and dep_date != "":
        if dep_time != "":
            offer["departure_date"] = dep_date + "T" + dep_time
        else:
            offer["departure_date"] = dep_date
    elif offer["departure_date"] != "" and dep_time != "":
        if "T" not in offer["departure_date"]:
            offer["departure_date"] = offer["departure_date"] + "T" + dep_time

    if offer["return_date"] is None and "return_date" in ctx:
        if ctx["return_date"] is not None:
            offer["return_date"] = ctx["return_date"]

    # Klasyczne usuwanie ze slownika z instrukcja del (jak w C++)
    if "departure_time" in offer:
        del offer["departure_time"]
    if "arrival_time" in offer:
        del offer["arrival_time"]
        
    return offer


def offer_dedup_key(offer):
    return (
        offer["origin"],
        offer["destination"],
        offer.get("departure_date", ""),
        offer.get("return_date"),
        offer["price_text"],
    )


def sort_offers_by_price(offers):
    return sorted(offers, key=lambda o: price_amount(o["price_text"]))


def scrape_flight_from_results_url_full(results_url, max_results=20):
    results_url = results_url.strip()
    url_ctx = parse_flight_url(results_url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(results_url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_selector("div.result", timeout=35000)
            except Exception:
                page.wait_for_timeout(4000)

            if page.locator("div.noResults").count() > 0:
                browser.close()
                return ScrapeResult(
                    [],
                    error="no_results",
                    message="AZair: brak wynikow dla podanych parametrow",
                )

            offers = []
            seen = set()
            elements = page.locator("div.result").all()[:500]

            for element in elements:
                text = element.inner_text().strip()
                html = element.inner_html()
                offer = extract_offer(text, html=html)

                if offer is None:
                    continue

                offer = merge_offer(offer, url_ctx)

                key = offer_dedup_key(offer)
                if key in seen:
                    continue

                seen.add(key)
                offers.append(offer)

                if len(offers) >= max_results:
                    break

            browser.close()
            offers = sort_offers_by_price(offers)

            if len(offers) == 0:
                return ScrapeResult(
                    [],
                    error="parse_failed",
                    message="Strona zaladowana, ale nie udalo sie odczytac ofert",
                )
            return ScrapeResult(offers)

        except Exception as exc:
            browser.close()
            return ScrapeResult(
                [],
                error="scrape_error",
                message=str(exc),
            )
    

def get_first(qs, key):
    if key in qs:
        vals = qs[key]
        if len(vals) > 0:
            return vals[0].strip()
    return None
