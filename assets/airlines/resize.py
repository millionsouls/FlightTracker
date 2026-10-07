from pathlib import Path
from PIL import Image

# Input and output directories
input_dir = Path("./new")
output_dir = Path("./airline_logos_16")

# Target size
width = 20
height = 20

# Create output directory if it doesn't exist
output_dir.mkdir(parents=True, exist_ok=True)

# Process all PNG files
for image_path in input_dir.glob("*.png"):
    with Image.open(image_path) as img:
        resized = img.resize((width, height), Image.Resampling.LANCZOS)

        output_path = output_dir / image_path.name
        resized.save(output_path)

        print(f"Resized: {image_path.name}")

print("Done!")
