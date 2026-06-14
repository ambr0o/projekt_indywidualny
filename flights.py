"""Scraping and parsing of AZair flight search results.

Provides Playwright-based scraping of the AZair results page plus a set of pure
parser helpers (regular expressions) that turn raw offer text and HTML into
structured offer dictionaries.
"""

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

# Map of IATA code -> airline name (extend as needed)
IATA_TO_AIRLINE_NAME = {
    "FR": "Ryanair",
    "W6": "Wizz Air",
    "W9": "Wizz Air Malta",
    "LO": "LOT",
    "LH": "Lufthansa",
    "KL": "KLM",
    "AF": "Air France",
    "BA": "British Airways",
    "U2": "easyJet",
    "EW": "Eurowings",
    "VY": "Vueling",
    "AZ": "ITA Airways",
    "OS": "Austrian",
    "LX": "Swiss",
    "SK": "SAS",
    "TP": "TAP",
    "TK": "Turkish Airlines",
    "EI": "Aer Lingus",
    "FI": "Icelandair",
    "DY": "Norwegian",
    "BT": "airBaltic",
    "IB": "Iberia",
    "AY": "Finnair",
    "RO": "TAROM",
    "OK": "Czech Airlines",
}


AIRLINE_SPAN_PATTERN = (
    r'<span class="airline\s+iata([A-Z0-9]{2,3})"[^>]*>([^<]+)</span>'
)
FLIGHTRADAR_PATTERN = r'title="flightradar24"[^>]*>([A-Z][A-Z0-9]\d+)</a>'
FLIGHT_LINK_TEXT_PATTERN = r">\s*([A-Z]{2}\d{2,5})\s*<"
AIRLINE_TRACKBOOK2_PATTERN = r"trackBook2?\s*\(\s*'([A-Z0-9]{2,3})'"
TOTAL_PRICE_PATTERN = (
    r'<span class="totalPrice">.*?<span class="tp">([^<]+)</span>'
)
SUBPRICE_PATTERN = r'<span class="subPrice">([^<]+)</span>'


@dataclass
class ScrapeResult:
    """Outcome of scraping an AZair results page.

    Attributes:
        offers: List of parsed offer dictionaries.
        error: Short error code if scraping failed, otherwise None.
        message: Human-readable message describing the result or error.
    """
    offers: list
    error: Optional[str] = None
    message: str = ""



def parse_date(s):
    """Normalize a date string to ISO format ``YYYY-MM-DD``.

    Args:
        s: Date string, either already ISO (``YYYY-MM-DD``) or dotted
            (``DD.MM.YYYY``).

    Returns:
        The date as ``YYYY-MM-DD``, or an empty string if it cannot be parsed.
    """
    # change of date format
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
    """Extract the first display date from text and return it as ISO.

    Args:
        text: Text that may contain a display date like ``Mon 24/05/26``.

    Returns:
        The date as ``YYYY-MM-DD``, or an empty string if none is found.
    """
    match = re.search(DISPLAY_DATE_PATTERN, text)
    if not match:
        return ""
    day, month, year = match.groups()
    return f"20{year}-{month}-{day}"


def parse_display_dates(text):
    """Extract all display dates from text and return them as ISO strings.

    Args:
        text: Text that may contain one or more display dates.

    Returns:
        A list of dates as ``YYYY-MM-DD`` in order of appearance.
    """
    dates = []
    for day, month, year in re.findall(DISPLAY_DATE_PATTERN, text):
        dates.append(f"20{year}-{month}-{day}")
    return dates


def price_amount(price_text):
    """Parse a numeric amount from a price string, ignoring currency tokens.

    Args:
        price_text: Raw price text such as ``"123,45 EUR"`` or ``"1.234,56"``.

    Returns:
        The amount as a float, or ``0.0`` if it cannot be parsed.
    """
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
    """Return ``(amount: float, currency: str)`` from a price string.

    Args:
        price_text: Raw price text including a currency symbol or code.

    Returns:
        A tuple of the amount and the detected currency code. The currency is
        ``'UNKNOWN'`` when it cannot be recognized.
    """
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
    """Return all price-like substrings found in the given text."""
    return [m.group(0).strip() for m in re.finditer(PRICE_PATTERN, text, re.IGNORECASE)]


def pick_total_price(text):
    """Return the highest price found in the text, assumed to be the total.

    Args:
        text: Text that may contain one or more prices.

    Returns:
        The highest price string, or None if no price is found.
    """
    prices = find_all_prices(text)
    if not prices:
        return None
    return max(prices, key=price_amount)


def parse_flight_url(url):
    """Parse an AZair search URL into structured route context.

    Args:
        url: A full AZair search/results URL.

    Returns:
        A dict with keys ``origin``, ``destination``, ``departure_date``,
        ``return_date`` and ``is_oneway`` derived from the query string.
    """
    url_parsed = urllib.parse.urlparse(url.strip())
    qs = urllib.parse.parse_qs(url_parsed.query)

    is_oneway = (get_first(qs, "isOneway") or "").lower() == "oneway"

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

    # In one-way searches, `arrdate` is the search window boundary, NOT a return date.
    return_date = None
    if not is_oneway and arr_raw:
        return_date = parse_date(arr_raw)

    result = {
        "origin": origin or "",
        "destination": destination or "",
        "departure_date": departure_date,
        "return_date": return_date,
        "is_oneway": is_oneway,
    }
    return result


def extract_airlines(html):
    "Return a list of (iata_code, name) for each segment in order of appearance."
    if not html:
        return []
    pairs = []
    for code, name in re.findall(AIRLINE_SPAN_PATTERN, html):
        pairs.append((code.upper(), name.strip()))

    if not pairs:
        for code in re.findall(AIRLINE_TRACKBOOK2_PATTERN, html):
            code_u = code.upper()
            name = IATA_TO_AIRLINE_NAME.get(code_u, code_u)
            pairs.append((code_u, name))
    return pairs


def extract_flight_numbers(html):
    "Flight numbers in segment order (from flightradar24 links or linked text)."
    if not html:
        return []
    numbers = []
    for fr in re.findall(FLIGHTRADAR_PATTERN, html, re.IGNORECASE):
        numbers.append(fr.upper())
    if not numbers:
        for fl in re.findall(FLIGHT_LINK_TEXT_PATTERN, html):
            numbers.append(fl.upper())
    return numbers


def extract_total_price(html):
    """Return the total price from the offer HTML, or None if not present."""
    if not html:
        return None
    match = re.search(TOTAL_PRICE_PATTERN, html, re.DOTALL)
    if match:
        return match.group(1).strip().replace("&nbsp;", " ")
    return None


def extract_subprices(html):
    """Return the list of per-leg sub-prices (subPrice) in leg order.

    Round-trip: ``[outbound_price, return_price]``. One-way: ``[flight_price]``.
    For low-cost carriers the sum of subPrices equals the totalPrice, so each
    subPrice is the one-way price of that leg.

    Args:
        html: The offer HTML.

    Returns:
        A list of price strings in leg order (may be empty).
    """
    if not html:
        return []
    return [m.strip().replace("&nbsp;", " ") for m in re.findall(SUBPRICE_PATTERN, html)]


def extract_offer(text, html=""):
    """Parse a single offer from its text and HTML into a dictionary.

    Args:
        text: The inner text of the offer element.
        html: The inner HTML of the offer element (optional but recommended,
            since airline, flight number and sub-prices come from the markup).

    Returns:
        A dict describing the offer, or None when no valid price is found.
    """
    if text == "" or text == None:
        return None

    price_text = extract_total_price(html) or pick_total_price(text)
    if not price_text:
        return None

    route_match = re.search(ROUTE_PATTERN, text)
    if not route_match:
        route_match = re.search(ROUTE_PATTERN_LOOSE, text, re.IGNORECASE)

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

    # Airline and flight number - separately for outbound and return
    airlines = extract_airlines(html or "")
    flight_numbers = extract_flight_numbers(html or "")

    if not airlines:
        # Final fallback: regex on the text itself
        flight_match = re.search(FLIGHT_NO_PATTERN, text, re.IGNORECASE)
        if flight_match:
            flight_numbers = [flight_match.group(1).upper()]

    outbound_airline_code = airlines[0][0] if len(airlines) >= 1 else "UNKNOWN"
    outbound_airline_name = airlines[0][1] if len(airlines) >= 1 else "UNKNOWN"
    return_airline_code = airlines[1][0] if len(airlines) >= 2 else outbound_airline_code
    return_airline_name = airlines[1][1] if len(airlines) >= 2 else outbound_airline_name

    outbound_flight_no = flight_numbers[0] if len(flight_numbers) >= 1 else "UNKNOWN"
    return_flight_no = flight_numbers[1] if len(flight_numbers) >= 2 else "UNKNOWN"

    # Per-leg sub-prices (subPrice). For low-cost carriers subPrice = one-way price of the leg.
    subprices = extract_subprices(html or "")
    outbound_price_text = subprices[0] if len(subprices) >= 1 else None
    return_price_text = subprices[1] if len(subprices) >= 2 else None

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
        # Backward-compat: the first airline/number go into "airline"/"flight_number"
        "airline": outbound_airline_name,
        "airline_code": outbound_airline_code,
        "flight_number": outbound_flight_no,
        # New fields: return leg
        "return_airline": return_airline_name,
        "return_airline_code": return_airline_code,
        "return_flight_number": return_flight_no,
        # Per-leg sub-prices
        "outbound_price_text": outbound_price_text,
        "return_price_text": return_price_text,
    }
    return offer_dict


def merge_offer(offer, ctx):
    """Fill missing offer fields from the URL-derived route context.

    Args:
        offer: The offer dict produced by ``extract_offer``.
        ctx: Route context produced by ``parse_flight_url``.

    Returns:
        The same offer dict, with origin/destination/dates completed and the
        helper time fields removed.
    """
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

    # One-way: no return date, even if ctx has one (then it is the search window boundary).
    is_oneway = ctx.get("is_oneway", False)
    if is_oneway:
        offer["return_date"] = None
        offer["return_price_text"] = None
    else:
        if offer["return_date"] is None and "return_date" in ctx:
            if ctx["return_date"] is not None:
                offer["return_date"] = ctx["return_date"]

    # Remove helper fields (times) - they are not stored in the database.
    if "departure_time" in offer:
        del offer["departure_time"]
    if "arrival_time" in offer:
        del offer["arrival_time"]
        
    return offer


def offer_dedup_key(offer):
    """Return a tuple used to deduplicate offers (route, dates and price)."""
    return (
        offer["origin"],
        offer["destination"],
        offer.get("departure_date", ""),
        offer.get("return_date"),
        offer["price_text"],
    )


def sort_offers_by_price(offers):
    """Return the offers sorted by ascending price."""
    return sorted(offers, key=lambda o: price_amount(o["price_text"]))


def scrape_flight_from_results_url_full(results_url, max_results=20):
    """Scrape offers from an AZair results URL using a headless browser.

    Loads the page, waits for the AJAX-loaded results to stabilize, then parses
    each result element into an offer, deduplicating and sorting by price.

    Args:
        results_url: A full AZair results URL.
        max_results: Maximum number of offers to return (cheapest first).

    Returns:
        A ScrapeResult containing the offers, or an error code and message when
        the page has no results or scraping fails.
    """
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

            # AZair loads offers via AJAX after the first one appears.
            # Wait until the div.result count stops growing (stabilizes),
            # otherwise we catch only part of the offers (often the pricier /
            # slower-loading ones).
            prev_count = -1
            stable_reads = 0
            for _ in range(20):  # max ~20s
                count = page.locator("div.result").count()
                if count == prev_count and count > 0:
                    stable_reads += 1
                    if stable_reads >= 3:  # 3 reads without change = loaded
                        break
                else:
                    stable_reads = 0
                prev_count = count
                page.wait_for_timeout(1000)

            if page.locator("div.noResults").count() > 0:
                browser.close()
                return ScrapeResult(
                    [],
                    error="no_results",
                    message="brak wynikow dla podanych parametrow",
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
    """Return the first stripped value for a query-string key, or None."""
    if key in qs:
        vals = qs[key]
        if len(vals) > 0:
            return vals[0].strip()
    return None
