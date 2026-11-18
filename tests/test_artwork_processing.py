from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf
from PIL import Image

from artwork_processing import ArtworkProcessor, ProcessOptions, inspect_artwork


class ArtworkProcessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.pdf_path = self.root / "sample_artwork.pdf"
        document = pymupdf.open()
        page = document.new_page(width=200, height=300)
        page.draw_rect(
            pymupdf.Rect(25, 30, 175, 270),
            color=(1, 0, 0),
            width=0.5,
        )
        page.draw_rect(
            pymupdf.Rect(45, 50, 155, 250),
            color=(0.2, 0.2, 0.2),
            fill=(0.8, 0.8, 0.8),
        )
        document.save(self.pdf_path)
        document.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_inspection_finds_vector_candidates(self) -> None:
        inspection = inspect_artwork(self.pdf_path)

        self.assertGreater(inspection.detected_bounds.width, 0)
        self.assertGreater(inspection.detected_bounds.height, 0)
        self.assertTrue(inspection.options)

    def test_processing_exports_requested_images(self) -> None:
        output_dir = self.root / "output"
        result = ArtworkProcessor(
            ProcessOptions(dpi=72, output_format="png")
        ).process_file(self.pdf_path, output_dir)

        self.assertIsNotNone(result.diecut_path)
        self.assertTrue(result.diecut_path.is_file())
        self.assertIsNotNone(result.clean_path)
        self.assertTrue(result.clean_path.is_file())
        with Image.open(result.diecut_path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertGreater(image.width, 0)
            self.assertGreater(image.height, 0)


if __name__ == "__main__":
    unittest.main()
