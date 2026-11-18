from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import main
from new_ui_window import NewUIWindow


class MainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_opens_artwork_window(self) -> None:
        with (
            patch("main.QApplication") as application,
            patch("main.NewUIWindow") as window_class,
        ):
            application.return_value.exec.return_value = 0

            self.assertEqual(main.main(), 0)

            application.assert_called_once_with(sys.argv)
            window_class.assert_called_once_with()
            window_class.return_value.btn_artwork.click.assert_called_once_with()
            window_class.return_value.show.assert_called_once_with()
            application.return_value.exec.assert_called_once_with()

    def test_window_contains_only_home_and_artwork_pages(self) -> None:
        window = NewUIWindow()
        self.assertEqual(window.content_stack.count(), 2)
        self.assertEqual(window.btn_home.text(), "HOME")
        self.assertEqual(window.btn_artwork.text(), "ARTWORK")
        self.assertFalse(hasattr(window, "btn_master"))
        self.assertFalse(hasattr(window, "btn_project"))
        self.assertFalse(hasattr(window, "btn_search"))
        window.close()


if __name__ == "__main__":
    unittest.main()
