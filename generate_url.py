"""Builder for AZair flight search URLs.

Turns route parameters (airport labels, IATA codes, dates, options) into the
query string AZair expects. Also usable as a CLI via ``main``.
"""

import argparse
import urllib.parse


BASE_URL = "https://www.azair.eu/azfin.php"

# AZair slot indexes (e.g. WMI=0, KTW=6, RZE=7, KRK=8 for Warsaw)
WARSAW_SRC_SLOTS = [0, 6, 7, 8]


def month_from_date(date_str):
    """2026-05-24 -> 202605; 16.4.2026 -> 202604."""
    date_str = date_str.strip()
    if "-" in date_str:
        parts = date_str.split("-")
        if len(parts) >= 2:
            return parts[0] + parts[1]
    if "." in date_str:
        parts = date_str.split(".")
        if len(parts) == 3:
            return f"{parts[2]}{int(parts[1]):02d}"
    return ""


def build_url_params(
    src_airport_label,
    dst_airport_label,
    dep_date,
    arr_date,
    src_typed_text="",
    dst_typed_text="",
    src_codes=None,
    dst_codes=None,
    src_slots=None,
    dst_slots=None,
    src_mc="",
    dst_mc="",
    currency="EUR",
    is_oneway="return",
    min_days_stay="2",
    max_days_stay="8",
    adults="1",
    max_chng="1",
):
    """Build the AZair query parameter dict for a search.

    Args:
        src_airport_label: Display label of the origin airport.
        dst_airport_label: Display label of the destination airport.
        dep_date: Departure date (window start), ISO or dotted format.
        arr_date: Return date / window end, ISO or dotted format.
        src_typed_text: Text typed in the origin field.
        dst_typed_text: Text typed in the destination field.
        src_codes: IATA codes for origin slots.
        dst_codes: IATA codes for destination slots.
        src_slots: Explicit origin slot indexes (defaults to 0,1,2,...).
        dst_slots: Explicit destination slot indexes (defaults to 0,1,2,...).
        src_mc: AZair origin metropolitan code (e.g. "WAR_ALL").
        dst_mc: AZair destination metropolitan code (e.g. "MIL_ALL").
        currency: Currency code.
        is_oneway: "return" or "oneway".
        min_days_stay: Minimum days of stay (round-trip).
        max_days_stay: Maximum days of stay (round-trip).
        adults: Number of adult passengers.
        max_chng: Maximum number of changes (connections).

    Returns:
        A dict of AZair query parameters.
    """
    # AZair only accepts the DD.M.YYYY format in depdate/arrdate fields (not ISO).
    dep_date = _to_azair_date(dep_date)
    arr_date = _to_azair_date(arr_date)

    params = {
        "tp": "0",
        "searchtype": "flexi",
        "srcAirport": src_airport_label,
        "srcTypedText": src_typed_text,
        "srcFreeTypedText": "",
        "srcMC": src_mc,
        "srcFreeAirport": "",
        "dstAirport": dst_airport_label,
        "dstTypedText": dst_typed_text,
        "dstFreeTypedText": "",
        "dstMC": dst_mc,
        "dstFreeAirport": "",
        "adults": adults,
        "children": "0",
        "infants": "0",
        "minHourStay": "0:45",
        "maxHourStay": "23:20",
        "minHourOutbound": "0:00",
        "maxHourOutbound": "24:00",
        "minHourInbound": "0:00",
        "maxHourInbound": "24:00",
        "depdate": dep_date,
        "arrdate": arr_date,
        "minDaysStay": min_days_stay,
        "maxDaysStay": max_days_stay,
        "nextday": "0",
        "autoprice": "true",
        "currency": currency,
        "wizzxclub": "false",
        "flyoneclub": "false",
        "blueairbenefits": "false",
        "megavolotea": "false",
        "schengen": "false",
        "transfer": "false",
        "samedep": "true",
        "samearr": "true",
        "dep0": "true",
        "dep1": "true",
        "dep2": "true",
        "dep3": "true",
        "dep4": "true",
        "dep5": "true",
        "dep6": "true",
        "arr0": "true",
        "arr1": "true",
        "arr2": "true",
        "arr3": "true",
        "arr4": "true",
        "arr5": "true",
        "arr6": "true",
        "maxChng": max_chng,
        "isOneway": is_oneway,
        "resultSubmit": "Search",
    }

    add_airport_slots(params, "src", src_codes, src_slots)
    add_airport_slots(params, "dst", dst_codes, dst_slots)

    return params


def _to_azair_date(s):
    """Convert '2026-07-01' or '1.7.2026' to AZair format '1.7.2026' (D.M.YYYY).

    AZair rejects the ISO format (YYYY-MM-DD) on the depdate/arrdate fields.
    """
    s = s.strip()
    if "-" in s:
        parts = s.split("-")
        if len(parts) == 3:
            y, m, d = parts
            return f"{int(d)}.{int(m)}.{int(y)}"
    if "." in s:
        parts = s.split(".")
        if len(parts) == 3:
            d, m, y = parts
            return f"{int(d)}.{int(m)}.{int(y)}"
    return s


def add_airport_slots(params, prefix, codes, slots=None):
    """Add ``{prefix}apN`` slot params for each airport code in place."""
    if not codes:
        return
    if slots is None:
        for i, code in enumerate(codes):
            params[f"{prefix}ap{i}"] = code.strip().upper()
        return
    for slot, code in zip(slots, codes):
        params[f"{prefix}ap{slot}"] = code.strip().upper()


def parse_slots(value):
    """Parse a comma-separated slot index string into a list of ints, or None."""
    if not value:
        return None
    return [int(s.strip()) for s in value.split(",") if s.strip() != ""]


def build_search_url(
    src_airport_label,
    dst_airport_label,
    dep_date,
    arr_date,
    **kwargs,
):
    """Build the full AZair search URL from route parameters."""
    params = build_url_params(
        src_airport_label,
        dst_airport_label,
        dep_date,
        arr_date,
        **kwargs,
    )
    return BASE_URL + "?" + urllib.parse.urlencode(params)


def parse_codes(value):
    """Parse a comma-separated IATA code string into a list, or None if empty."""
    if not value:
        return None
    return [c.strip() for c in value.split(",") if c.strip()]


def parse_args():
    """Parse command-line arguments for the URL generator CLI."""
    parser = argparse.ArgumentParser(
        description="Generuj link wyszukiwania AZair z podanych parametrów.",
    )
    parser.add_argument(
        "--src-label",
        required=True,
        help='Etykieta lotniska wylotu, np. "Warsaw (Modlin) [WMI]"',
    )
    parser.add_argument(
        "--dst-label",
        default="",
        help='Etykieta lotniska przylotu, np. "Milan [MXP] (+LIN,BGY)". Niepotrzebne gdy --anywhere.',
    )
    parser.add_argument(
        "--dep",
        required=True,
        help="Data wylotu (zakres od), np. 2026-05-24 lub 16.4.2026",
    )
    parser.add_argument(
        "--arr",
        required=True,
        help="Data powrotu (zakres do), np. 2027-01-31",
    )
    parser.add_argument("--src-text", default="", help="Tekst wpisany przy wylocie")
    parser.add_argument("--dst-text", default="", help="Tekst wpisany przy przylocie")
    parser.add_argument(
        "--src-codes",
        default="",
        help="Kody IATA wylotu po przecinku, np. WMI,KTW,RZE,KRK",
    )
    parser.add_argument(
        "--src-slots",
        default="",
        help="Indeksy srcap po przecinku (jak w AZair), np. 0,6,7,8; puste = 0,1,2,...",
    )
    parser.add_argument(
        "--warsaw-src",
        action="store_true",
        help=f"Uzyj slotow Warszawy {WARSAW_SRC_SLOTS} dla --src-codes",
    )
    parser.add_argument(
        "--dst-codes",
        default="",
        help="Kody IATA przylotu po przecinku, np. LIN,BGY",
    )
    parser.add_argument(
        "--dst-slots",
        default="",
        help="Indeksy dstap po przecinku; puste = 0,1,2,...",
    )
    parser.add_argument(
        "--dst-mc",
        default="",
        help="Miasto docelowe AZair, np. MIL_ALL",
    )
    parser.add_argument(
        "--src-mc",
        default="",
        help="Miasto wylotowe AZair, np. WAR_ALL",
    )
    parser.add_argument(
        "--anywhere",
        action="store_true",
        help="Tryb Anywhere: dowolne lotnisko docelowe (ignoruje --dst-* gdy podane)",
    )
    parser.add_argument(
        "--currency",
        default="EUR",
        choices=["EUR", "PLN", "CZK", "GBP", "USD"],
    )
    parser.add_argument(
        "--oneway",
        default="return",
        choices=["return", "oneway"],
        help="return = tam i z powrotem, oneway = w jedną stronę",
    )
    parser.add_argument("--min-days", default="5", help="minDaysStay")
    parser.add_argument("--max-days", default="8", help="maxDaysStay")
    parser.add_argument("--adults", default="1")
    parser.add_argument("--max-chng", default="1", help="maxChng (przesiadki)")
    return parser.parse_args()


def main():
    """Parse CLI arguments, build the search URL and print it."""
    args = parse_args()
    if not args.anywhere and not args.dst_label:
        raise SystemExit("Bez --anywhere musisz podac --dst-label.")

    src_slots = parse_slots(args.src_slots)
    if args.warsaw_src:
        src_slots = WARSAW_SRC_SLOTS

    if args.anywhere:
        dst_label = "Anywhere [XXX]"
        dst_typed_text = "any"
        dst_codes = None
        dst_slots = None
        dst_mc = ""
    else:
        dst_label = args.dst_label
        dst_typed_text = args.dst_text
        dst_codes = parse_codes(args.dst_codes)
        dst_slots = parse_slots(args.dst_slots)
        dst_mc = args.dst_mc

    url = build_search_url(
        args.src_label,
        dst_label,
        args.dep,
        args.arr,
        src_typed_text=args.src_text,
        dst_typed_text=dst_typed_text,
        src_codes=parse_codes(args.src_codes),
        dst_codes=dst_codes,
        src_slots=src_slots,
        dst_slots=dst_slots,
        src_mc=args.src_mc,
        dst_mc=dst_mc,
        currency=args.currency,
        is_oneway=args.oneway,
        min_days_stay=args.min_days,
        max_days_stay=args.max_days,
        adults=args.adults,
        max_chng=args.max_chng,
    )
    print("Wygenerowany link")
    print(url)


if __name__ == "__main__":
    main()
