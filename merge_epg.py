import gzip
import shutil
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SOURCES_FILE = Path("sources.txt")
OUTPUT_EPG = Path("epg_merged.xml.gz")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def load_sources(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Brak pliku: {path}")
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if clean and not clean.startswith("#") and clean not in urls:
            urls.append(clean)
    return urls


def is_gzip_file(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(2) == b"\x1f\x8b"


def download_file(url: str, dest: Path):
    if not url.startswith(("http://", "https://")):
        local_src = Path(url)
        if not local_src.exists():
            raise FileNotFoundError(f"Brak lokalnego pliku źródłowego: {local_src}")
        shutil.copyfile(local_src, dest)
        return

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def merge_source(
    source_path: Path,
    seen_channels: set[str],
    seen_programmes: set[tuple[str, str]],
    channels_tmp_file,
    programmes_tmp_file,
) -> tuple[int, int]:
    opener = gzip.open if is_gzip_file(source_path) else open
    added_ch = 0
    added_prog = 0

    with opener(source_path, "rb") as f:
        context = ET.iterparse(f, events=("start", "end"))
        try:
            _, root = next(context)
        except StopIteration:
            return 0, 0

        for event, elem in context:
            if event != "end":
                continue

            if elem.tag == "channel":
                cid = (elem.get("id") or "").strip()
                if cid and cid not in seen_channels:
                    seen_channels.add(cid)
                    channels_tmp_file.write(b"  ")
                    channels_tmp_file.write(ET.tostring(elem, encoding="utf-8"))
                    channels_tmp_file.write(b"\n")
                    added_ch += 1
                root.clear()

            elif elem.tag == "programme":
                cid = (elem.get("channel") or "").strip()
                start = (elem.get("start") or "").strip()
                prog_key = (cid, start)
                if cid and start and prog_key not in seen_programmes:
                    seen_programmes.add(prog_key)
                    programmes_tmp_file.write(b"  ")
                    programmes_tmp_file.write(ET.tostring(elem, encoding="utf-8"))
                    programmes_tmp_file.write(b"\n")
                    added_prog += 1
                root.clear()

    return added_ch, added_prog


def main():
    urls = load_sources(SOURCES_FILE)
    if not urls:
        raise ValueError(f"Brak adresów URL w pliku {SOURCES_FILE.name}")

    print(f"Wczytano {len(urls)} źródeł z {SOURCES_FILE.name}.")

    seen_channels: set[str] = set()
    seen_programmes: set[tuple[str, str]] = set()
    total_channels = 0
    total_programmes = 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        ch_tmp = tmp_path / "channels.tmp"
        prog_tmp = tmp_path / "programmes.tmp"

        with open(ch_tmp, "wb") as ch_f, open(prog_tmp, "wb") as prog_f:
            for idx, url in enumerate(urls, start=1):
                print(f"[{idx}/{len(urls)}] Pobieranie: {url}")
                dl_path = tmp_path / f"src_{idx}.bin"
                try:
                    download_file(url, dl_path)
                    ch_cnt, pr_cnt = merge_source(
                        dl_path, seen_channels, seen_programmes, ch_f, prog_f
                    )
                    total_channels += ch_cnt
                    total_programmes += pr_cnt
                    print(f"    Dodano: {ch_cnt} kanałów, {pr_cnt} audycji.")
                except Exception as e:
                    print(f"    BŁĄD: {e}")
                finally:
                    if dl_path.exists():
                        dl_path.unlink()

        with gzip.open(OUTPUT_EPG, "wb", compresslevel=6) as out_gz:
            out_gz.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            out_gz.write(b'<tv generator-info-name="Merged EPG">\n')

            with open(ch_tmp, "rb") as ch_in:
                shutil.copyfileobj(ch_in, out_gz)

            with open(prog_tmp, "rb") as prog_in:
                shutil.copyfileobj(prog_in, out_gz)

            out_gz.write(b"</tv>\n")

    size_mb = OUTPUT_EPG.stat().st_size / (1024 * 1024)
    print(
        f"GOTOWE: {OUTPUT_EPG} ({size_mb:.2f} MB) | "
        f"Kanały: {total_channels} | Audycje: {total_programmes}"
    )


if __name__ == "__main__":
    main()
