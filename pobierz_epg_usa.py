import gzip
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# --- KONFIGURACJA ---
DAYS = 7
OUT_XML = "epg_usa_7dni.xml"
OUT_GZ = "epg_usa_7dni.xml.gz"

STATIONS = [
    {
        "id": "WBZ-DT.us_locals1",
        "name": "USA CBS Boston WBZ",
        "url": "https://www.tvpassport.com/tv-listings/stations/cbs-wbz-boston-ma/134",
    },
    {
        "id": "KCAL-DT.us_locals1",
        "name": "USA CBS Los Angeles KCAL",
        "url": "https://www.tvpassport.com/tv-listings/stations/kcal-los-angeles/1826",
    },
    {
        "id": "KCBS-DT.us_locals1",
        "name": "USA CBS Los Angeles KCBS",
        "url": "https://www.tvpassport.com/tv-listings/stations/cbs-kcbs-los-angeles-ca/2172",
    },
    {
        "id": "WCBS-DT.us_locals1",
        "name": "USA CBS New York WCBS",
        "url": "https://www.tvpassport.com/tv-listings/stations/cbs-wcbs-new-york-ny/1766",
    },
    {
        "id": "WFLD-DT.us_locals1",
        "name": "USA FOX Chicago WFLD",
        "url": "https://www.tvpassport.com/tv-listings/stations/fox-wfld-chicago-il/1606",
    },
    {
        "id": "KTTV-DT.us_locals1",
        "name": "USA FOX Los Angeles KTTV",
        "url": "https://www.tvpassport.com/tv-listings/stations/fox-kttv-los-angeles-ca/1949",
    },
    {
        "id": "WNYW-DT.us_locals1",
        "name": "USA FOX New York WNYW",
        "url": "https://www.tvpassport.com/tv-listings/stations/fox-wnyw-new-york-ny/1638",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

TZ_NY = ZoneInfo("America/New_York")
TZ_PL = ZoneInfo("Europe/Warsaw")


def format_xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S %z")


def fetch_station_day(session: requests.Session, station_url: str, date_str: str):
    if not hasattr(session, "_ny_offset_hours"):
        anchors = {
            "CBS News New York 11pm": 23,
            "CBS News New York 6pm": 18,
            "CBS News New York 5pm": 17,
            "CBS News New York 12pm": 12,
        }
        offset_h = 0
        try:
            cal_url = f"https://www.tvpassport.com/tv-listings/stations/cbs-wcbs-new-york-ny/1766/{date_str}"
            cal_resp = session.get(cal_url, headers=HEADERS, timeout=20)
            cal_resp.raise_for_status()
            cal_soup = BeautifulSoup(cal_resp.text, "html.parser")
            for item in cal_soup.select("div.list-group-item[data-st]"):
                t = item.get("data-showname", "").strip()
                st = item.get("data-st", "").strip()
                if t in anchors and st:
                    actual_h = datetime.strptime(st, "%Y-%m-%d %H:%M:%S").hour
                    expected_h = anchors[t]
                    offset_h = (expected_h - actual_h + 12) % 24 - 12
                    print(f"Skalibrowano TV Passport -> NY ({t}): {offset_h:+d}h")
                    break
        except Exception as exc:
            print(f"[UWAGA] Błąd kalibracji strefy: {exc}")
        session._ny_offset_hours = offset_h

    url = f"{station_url}/{date_str}"
    resp = session.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select("div.list-group-item[data-st]")
    programs = []

    for item in items:
        st_raw = item.get("data-st", "").strip()
        dur_raw = item.get("data-duration", "").strip()
        title = item.get("data-showname", "").strip()

        if not st_raw or not title:
            continue

        try:
            start_naive = datetime.strptime(st_raw, "%Y-%m-%d %H:%M:%S")
            start_ny = (start_naive + timedelta(hours=session._ny_offset_hours)).replace(tzinfo=TZ_NY)
            duration_min = int(dur_raw) if dur_raw.isdigit() else 30
            stop_ny = start_ny + timedelta(minutes=duration_min)
        except ValueError:
            continue

        start_pl = start_ny.astimezone(TZ_PL)
        stop_pl = stop_ny.astimezone(TZ_PL)

        programs.append({
            "start": start_pl,
            "stop": stop_pl,
            "title": title,
            "sub_title": item.get("data-episodetitle", "").strip(),
            "desc": item.get("data-description", "").strip(),
            "category": item.get("data-showtype", "").strip(),
            "icon": item.get("data-showpicture", "").strip(),
            "season": item.get("data-season", "").strip(),
            "episode": item.get("data-episode", "").strip(),
        })

    return programs


def build_epg():
    tv = ET.Element("tv", {"generator-info-name": "USA 7-Day EPG (TVPassport)"})

    for st in STATIONS:
        ch_el = ET.SubElement(tv, "channel", {"id": st["id"]})
        dn = ET.SubElement(ch_el, "display-name", {"lang": "en"})
        dn.text = st["name"]

    session = requests.Session()
    today_ny = datetime.now(TZ_NY).date()
    total_count = 0

    for st in STATIONS:
        print(f"Pobieram: {st['name']} ({st['id']})...")
        seen_keys = set()
        st_count = 0

        for offset in range(DAYS):
            day_str = (today_ny + timedelta(days=offset)).strftime("%Y-%m-%d")
            try:
                progs = fetch_station_day(session, st["url"], day_str)
            except Exception as exc:
                print(f"  [BŁĄD] {day_str}: {exc}")
                continue

            for p in progs:
                key = (p["start"], p["title"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                prog_el = ET.SubElement(tv, "programme", {
                    "start": format_xmltv_time(p["start"]),
                    "stop": format_xmltv_time(p["stop"]),
                    "channel": st["id"],
                })

                t_el = ET.SubElement(prog_el, "title", {"lang": "en"})
                t_el.text = p["title"]

                if p["sub_title"]:
                    st_el = ET.SubElement(prog_el, "sub-title", {"lang": "en"})
                    st_el.text = p["sub_title"]

                if p["desc"]:
                    d_el = ET.SubElement(prog_el, "desc", {"lang": "en"})
                    d_el.text = p["desc"]

                if p["category"]:
                    c_el = ET.SubElement(prog_el, "category", {"lang": "en"})
                    c_el.text = p["category"]

                if p["season"].isdigit() and p["episode"].isdigit():
                    ep_el = ET.SubElement(prog_el, "episode-num", {"system": "onscreen"})
                    ep_el.text = f"S{int(p['season']):02d}E{int(p['episode']):02d}"

                if p["icon"]:
                    icon_url = p["icon"]
                    if not icon_url.startswith("http"):
                        icon_url = f"https://cdn.tvpassport.com/image/show/960x540/{icon_url}"
                    ET.SubElement(prog_el, "icon", {"src": icon_url})

                st_count += 1
                total_count += 1

            time.sleep(0.4)  # delikatny odstęp między zapytaniami

        print(f"  -> Zapisano {st_count} audycji.")

    ET.indent(tv, space="  ")
    xml_bytes = ET.tostring(tv, encoding="utf-8", xml_declaration=True)

    with open(OUT_XML, "wb") as f:
        f.write(xml_bytes)

    with gzip.open(OUT_GZ, "wb") as f:
        f.write(xml_bytes)

    print(f"\nGotowe! Łącznie {total_count} audycji -> {OUT_XML} oraz {OUT_GZ}")


if __name__ == "__main__":
    build_epg()