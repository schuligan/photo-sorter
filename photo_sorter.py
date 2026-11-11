"""Photo Sorter: the first tool on the shared engine."""
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
    engine.serve(TOOL)
