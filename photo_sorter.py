"""Photo Sorter: the first tool on the shared engine.

    python3 photo_sorter.py                      # the whole-computer button reads your own folders
    python3 photo_sorter.py --home "<folder>"    # point it at a fake home folder for testing
"""
import argparse

import engine

TOOL = engine.Tool(
    title="Prateek's Photo Sorter",
    images=(".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".tiff", ".tif"),
    videos=(".mov", ".mp4", ".m4v"),
    categories=("Selfies and people", "Food", "Travel and places", "Nature",
                "Documents and screenshots", "Pets", "Other"),
    instructions="Sort these photos and video frames by their main subject.",
)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", help="a fake home folder for testing the whole-computer button")
    engine.serve(TOOL, home=ap.parse_args().home)
