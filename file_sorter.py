"""File Sorter: every file type, sorted on this Mac by type. Claude is never called.

    python3 file_sorter.py                      # the whole-computer button reads your own folders
    python3 file_sorter.py --home "<folder>"    # point it at a fake home folder for testing
"""
import argparse
import os

import engine

TYPES = {
    "Documents": "pdf doc docx pages txt rtf rtfd md odt tex epub",
    "Spreadsheets": "xls xlsx xlsm csv tsv numbers ods",
    "Slides": "ppt pptx key odp",
    "Images": "jpg jpeg png heic heif gif webp tif tiff bmp svg raw cr2 cr3 nef arw dng psd ai",
    "Videos": "mov mp4 m4v avi mkv webm wmv mpg mpeg 3gp",
    "Audio": "mp3 m4a wav aiff aif flac aac ogg opus caf",
    "Zip files": "zip rar 7z tar gz tgz bz2 xz",
    "Installers": "dmg pkg mpkg iso",
    "Code": "py js ts jsx tsx html htm css json yaml yml xml sh zsh swift java c h cpp go rs rb php sql ipynb",
}
BY_EXT = {f".{ext}": cat for cat, exts in TYPES.items() for ext in exts.split()}
CATEGORIES = (*TYPES, "Screenshots", "Other")


def classify(name):
    stem, ext = os.path.splitext(name)
    if ext.lower() in (".png", ".jpg", ".jpeg", ".heic") and stem.startswith(("Screenshot", "Screen Shot", "CleanShot")):
        return "Screenshots"
    return BY_EXT.get(ext.lower(), "Other")


TOOL = engine.Tool(
    title="Prateek's File Sorter",
    images=(), videos=(),
    categories=CATEGORIES,
    instructions="",
    classify=classify,
    page="file_page.html",
)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", help="a fake home folder for testing the whole-computer button")
    ap.add_argument("--film", action="store_true", help="recording extras: full screen and countdown on F")
    engine.serve(TOOL, home=ap.parse_args().home)
