import argparse
import urllib.parse


BASE_URL = "https://www.azair.eu/azfin.php"

# Indeksy slotow AZair (np. WMI=0, KTW=6, RZE=7, KRK=8 dla Warszawy)
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
    dst_mc="",
    currency="EUR",
    is_oneway="return",
    min_days_stay="5",
    max_days_stay="8",
    adults="1",
    max_chng="1",
):
    params = {
        "searchtype": "flexi",
        "tp": "0",
        "isOneway": is_oneway,
        "srcAirport": src_airport_label,
        "srcTypedText": src_typed_text,
        "srcFreeTypedText": "",
        "srcMC": "",
        "srcFreeAirport": "",
        "dstAirport": dst_airport_label,
        "dstTypedText": dst_typed_text,
        "dstFreeTypedText": "",
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
        "indexSubmit": "Search",
    }

    dep_month = month_from_date(dep_date)
    if dep_month:
        params["depmonth"] = dep_month
    arr_month = month_from_date(arr_date)
    if arr_month:
        params["arrmonth"] = arr_month

    if dst_mc:
        params["dstMC"] = dst_mc

    add_airport_slots(params, "src", src_codes, src_slots)
    add_airport_slots(params, "dst", dst_codes, dst_slots)

    return params


def add_airport_slots(params, prefix, codes, slots=None):
    if not codes:
        return
    if slots is None:
        for i, code in enumerate(codes):
            params[f"{prefix}ap{i}"] = code.strip().upper()
        return
    for slot, code in zip(slots, codes):
        params[f"{prefix}ap{slot}"] = code.strip().upper()


def parse_slots(value):
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
    params = build_url_params(
        src_airport_label,
        dst_airport_label,
        dep_date,
        arr_date,
        **kwargs,
    )
    return BASE_URL + "?" + urllib.parse.urlencode(params)


def parse_codes(value):
    if not value:
        return None
    return [c.strip() for c in value.split(",") if c.strip()]


def parse_args():
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
        required=True,
        help='Etykieta lotniska przylotu, np. "Milan [MXP] (+LIN,BGY)"',
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
    args = parse_args()
    src_slots = parse_slots(args.src_slots)
    if args.warsaw_src:
        src_slots = WARSAW_SRC_SLOTS
    url = build_search_url(
        args.src_label,
        args.dst_label,
        args.dep,
        args.arr,
        src_typed_text=args.src_text,
        dst_typed_text=args.dst_text,
        src_codes=parse_codes(args.src_codes),
        dst_codes=parse_codes(args.dst_codes),
        src_slots=src_slots,
        dst_slots=parse_slots(args.dst_slots),
        dst_mc=args.dst_mc,
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
