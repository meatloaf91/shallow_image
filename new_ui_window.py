from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QMovie, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from artwork_crop_dialogs import ArtworkManualCutDialog
from artwork_processing import ArtworkProcessor, ProcessOptions, inspect_artwork


_BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


class NewUIWindow(QMainWindow):
    """Small shell for the HOME and Artwork pages."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HAT Dashboard")
        self.resize(1047, 792)
        self.setMinimumSize(1047, 792)
        self._artwork_paths: list[Path] = []

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer_layout = QVBoxLayout(root)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("shell")
        outer_layout.addWidget(shell)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(12, 12, 12, 12)
        shell_layout.setSpacing(14)

        main_row = QHBoxLayout()
        main_row.setSpacing(14)
        shell_layout.addLayout(main_row, 1)

        icon_rail = QWidget()
        icon_rail.setFixedWidth(38)
        main_row.addWidget(icon_rail)

        content = QFrame()
        content.setObjectName("contentCard")
        main_row.addWidget(content, 1)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 18, 20, 18)
        content_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("headerBand")
        header.setFixedHeight(110)
        content_layout.addWidget(header)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(0)

        brand = QLabel("HAT")
        brand.setObjectName("hatBadge")
        brand.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        brand.setMinimumWidth(231)
        logo_pixmap = QPixmap(str(_BASE_DIR / "logo" / "hat_logo.png"))
        if not logo_pixmap.isNull():
            brand.setText("")
            brand.setPixmap(
                logo_pixmap.scaled(
                    231,
                    84,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        header_layout.addWidget(brand)
        header_layout.addStretch(1)

        self.btn_home = self._make_page_button("HOME")
        self.btn_artwork = self._make_page_button("ARTWORK")
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(8)
        buttons_layout.addWidget(self.btn_home)
        buttons_layout.addWidget(self.btn_artwork)
        header_layout.addLayout(buttons_layout)

        self.content_stack = QStackedWidget()
        self.home_page = self._build_home_page()
        self.artwork_page = self._build_artwork_page()
        self.content_stack.addWidget(self.home_page)
        self.content_stack.addWidget(self.artwork_page)
        content_layout.addWidget(self.content_stack, 1)

        self.section_title = QLabel("HOME")
        self.section_title.setObjectName("sectionTitle")
        content_layout.addWidget(
            self.section_title,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        )

        self.top_group = QButtonGroup(self)
        self.top_group.setExclusive(True)
        self.top_group.addButton(self.btn_home)
        self.top_group.addButton(self.btn_artwork)
        self.btn_home.clicked.connect(self._show_home)
        self.btn_artwork.clicked.connect(self._show_artwork)
        self.setStyleSheet(self._stylesheet())
        self.btn_home.setChecked(True)
        self._show_home()
        QTimer.singleShot(0, self._update_home_image)

    @staticmethod
    def _make_page_button(title: str) -> QPushButton:
        button = QPushButton(title)
        button.setObjectName("topHeaderBtn")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumSize(120 if title == "ARTWORK" else 90, 50)
        return button

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(0)
        card = QFrame()
        card.setObjectName("homeCard")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(40, 0, 40, 0)
        card_layout.setSpacing(24)
        card_layout.addStretch(1)

        text_image = QLabel()
        text_image.setObjectName("homeArtworkImage")
        text_image.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight
        )
        self._home_pixmap = QPixmap(
            str(_BASE_DIR / "logo" / "home_page_text.png")
        )
        if not self._home_pixmap.isNull():
            text_image.setPixmap(self._home_pixmap)
        else:
            text_image.setText("HAT\nARTWORK")
        self._home_img_label = text_image

        text_wrapper = QWidget()
        text_wrapper.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        text_wrapper_layout = QVBoxLayout(text_wrapper)
        text_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        text_wrapper_layout.setSpacing(0)
        text_wrapper_layout.addWidget(
            text_image,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        text_wrapper_layout.addStretch(1)
        card_layout.addWidget(text_wrapper, 0, Qt.AlignmentFlag.AlignRight)

        gif_label = QLabel()
        gif_label.setObjectName("homeArtworkAnimation")
        gif_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        gif_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        gif_movie = QMovie(str(_BASE_DIR / "logo" / "360_Bottle 1.gif"))
        if gif_movie.isValid():
            gif_label.setMovie(gif_movie)
            gif_movie.start()
        else:
            gif_label.setText("Prepare and export packshot artwork\nusing the Artwork page.")
            gif_label.setWordWrap(True)
        gif_wrapper = QWidget()
        gif_wrapper.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        gif_wrapper_layout = QVBoxLayout(gif_wrapper)
        gif_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        gif_wrapper_layout.setSpacing(0)
        gif_wrapper_layout.addWidget(
            gif_label,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        gif_wrapper_layout.addStretch(1)
        card_layout.addWidget(gif_wrapper, 0, Qt.AlignmentFlag.AlignLeft)
        card_layout.addStretch(1)
        self._home_img_label = text_image
        self._text_wrapper_layout = text_wrapper_layout
        self._gif_movie = gif_movie
        self._gif_label = gif_label
        self._gif_wrapper_layout = gif_wrapper_layout
        self._home_card = card
        layout.addWidget(card, 1)
        return page

    def _build_artwork_page(self) -> QWidget:
        page = QWidget()
        outer_layout = QHBoxLayout(page)
        outer_layout.setContentsMargins(0, 16, 0, 0)
        outer_layout.setSpacing(18)

        left_spacer = QWidget()
        left_spacer.setMaximumWidth(178)
        outer_layout.addWidget(left_spacer)

        panel = QFrame()
        panel.setObjectName("artworkPanel")
        panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(24, 24, 24, 24)
        panel_layout.setSpacing(12)

        title = QLabel("Artwork Processing")
        title.setObjectName("artworkTitle")
        panel_layout.addWidget(title)
        description = QLabel(
            "Set global crop actions, fine-tune local crops, and export TIFF artwork."
        )
        description.setObjectName("artworkDescription")
        description.setWordWrap(True)
        panel_layout.addWidget(description)

        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        source_label = QLabel("PDF artwork")
        source_label.setObjectName("artworkFieldLabel")
        self.artwork_input = QLineEdit()
        self.artwork_input.setObjectName("artworkInput")
        self.artwork_input.setPlaceholderText("Select one or more PDF files")
        source_button = QPushButton("Browse")
        source_button.setObjectName("artworkBrowseButton")
        source_button.clicked.connect(self._choose_artwork_input)
        source_row.addWidget(source_label)
        source_row.addWidget(self.artwork_input, 1)
        source_row.addWidget(source_button)
        panel_layout.addLayout(source_row)

        settings_row = QHBoxLayout()
        settings_row.addStretch(1)
        self.artwork_dpi = QLineEdit("300")
        self.artwork_dpi.setObjectName("artworkSmallInput")
        self.artwork_dpi.setFixedWidth(80)
        settings_row.addWidget(self.artwork_dpi)
        dpi_unit = QLabel("dpi")
        dpi_unit.setObjectName("artworkMutedLabel")
        settings_row.addWidget(dpi_unit)
        settings_row.addSpacing(16)
        self.artwork_include_diecut = QCheckBox("include w/ diecut version")
        self.artwork_include_diecut.setObjectName("artworkCheckBox")
        self.artwork_include_diecut.setChecked(True)
        settings_row.addWidget(self.artwork_include_diecut)
        settings_row.addStretch(1)
        panel_layout.addLayout(settings_row)

        manual_cut_row = QHBoxLayout()
        manual_cut_row.addStretch(1)
        self.artwork_manual_cut_button = QPushButton("Manual Cut")
        self.artwork_manual_cut_button.setObjectName("collectorRunBtn")
        self.artwork_manual_cut_button.setFixedHeight(50)
        self.artwork_manual_cut_button.setMinimumWidth(180)
        self.artwork_manual_cut_button.clicked.connect(self._open_manual_cut)
        manual_cut_row.addWidget(self.artwork_manual_cut_button)
        manual_cut_row.addStretch(1)
        panel_layout.addLayout(manual_cut_row)

        self.artwork_log = QTextEdit()
        self.artwork_log.setObjectName("artworkLog")
        self.artwork_log.setReadOnly(True)
        self.artwork_log.setPlaceholderText(
            "Detection details and output paths will appear here."
        )
        panel_layout.addWidget(self.artwork_log, 1)
        outer_layout.addWidget(panel, 1)
        return page

    def _show_home(self) -> None:
        self.content_stack.setCurrentWidget(self.home_page)
        self.section_title.setText("HOME")

    def _show_artwork(self) -> None:
        self.content_stack.setCurrentWidget(self.artwork_page)
        self.section_title.setText("ARTWORK")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_home_image()

    def _update_home_image(self) -> None:
        card_height = self._home_card.height()
        if card_height <= 0:
            return

        text_height = max(20, int(card_height * 0.1997))
        text_top_margin = max(0, (card_height - text_height) // 2 - 20)
        self._text_wrapper_layout.setContentsMargins(0, text_top_margin, 0, 0)
        if not self._home_pixmap.isNull():
            self._home_img_label.setPixmap(
                self._home_pixmap.scaledToHeight(
                    text_height, Qt.TransformationMode.SmoothTransformation
                )
            )

        gif_height = max(60, int(card_height * 0.4659))
        if self._gif_movie.isValid():
            current_size = self._gif_movie.currentImage().size()
            if current_size.height() > 0:
                gif_width = int(current_size.width() * gif_height / current_size.height())
                self._gif_movie.setScaledSize(QSize(gif_width, gif_height))
        gif_top_margin = max(0, (card_height - gif_height) // 2 - 67)
        self._gif_wrapper_layout.setContentsMargins(0, gif_top_margin, 0, 0)

    def _choose_artwork_input(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose artwork PDFs", "", "PDF files (*.pdf)"
        )
        if paths:
            self._artwork_paths = [Path(path) for path in paths]
            self.artwork_input.setText(" | ".join(paths))

    def _selected_artwork_paths(self) -> list[Path]:
        paths = [path for path in self._artwork_paths if path.is_file()]
        if paths:
            return paths
        entered_path = Path(self.artwork_input.text().strip())
        if entered_path.is_file() and entered_path.suffix.lower() == ".pdf":
            return [entered_path]
        return []

    def _open_manual_cut(self) -> None:
        input_paths = self._selected_artwork_paths()
        if not input_paths:
            QMessageBox.warning(
                self,
                "Missing artwork PDF",
                "Choose one or more PDF files before opening Manual Cut.",
            )
            return
        try:
            inspections = [inspect_artwork(path, dpi=72) for path in input_paths]
        except (FileNotFoundError, RuntimeError, ValueError, OSError) as error:
            QMessageBox.critical(self, "Artwork preview failed", str(error))
            return

        dialog = ArtworkManualCutDialog(inspections, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        output_dir = dialog.output_folder
        if output_dir is None:
            return

        try:
            dpi = int(self.artwork_dpi.text().strip())
            if dpi < 72:
                raise ValueError
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid resolution",
                "Resolution must be a whole number of at least 72 DPI.",
            )
            return

        options = ProcessOptions(
            dpi=dpi,
            output_format="tiff",
            include_diecut=self.artwork_include_diecut.isChecked(),
        )
        lines: list[str] = []
        failures: list[str] = []
        for input_path in input_paths:
            try:
                result = ArtworkProcessor(options).process_file(
                    input_path,
                    output_dir,
                    dialog.selections().get(input_path),
                )
            except (FileNotFoundError, RuntimeError, ValueError, OSError) as error:
                failures.append(f"{input_path.name}: {error}")
                continue
            lines.append(
                f"{input_path.name} | {result.detection.source} | "
                f"confidence {result.detection.confidence:.2f}"
            )
            if result.diecut_path is not None:
                lines.append(f"  Die-cut TIFF: {result.diecut_path}")
            if result.clean_path is not None:
                lines.append(f"  Clean TIFF: {result.clean_path}")
            lines.extend(f"  Warning: {warning}" for warning in result.warnings)
        lines.extend(f"FAILED: {failure}" for failure in failures)
        self.artwork_log.setPlainText("\n".join(lines))
        if failures:
            QMessageBox.warning(
                self, "Some artwork could not be processed", "\n".join(failures)
            )

    @staticmethod
    def _stylesheet() -> str:
        return """
            #root, #shell {
                background-color: #E5E5E5;
            }
            #contentCard {
                background-color: #FFFFFF;
                border: 1px solid #C0C0C0;
                border-radius: 18px;
            }
            #headerBand {
                background-color: #111F35;
                border-radius: 10px;
            }
            #artworkPanel, #homeCard {
                background-color: #FFFFFF;
                border: 1px solid #C0C0C0;
                border-radius: 14px;
            }
            #hatBadge {
                color: #FFFFFF;
                font-family: "Segoe UI";
                font-size: 34px;
                font-weight: 900;
                letter-spacing: 3px;
            }
            #sectionTitle {
                color: #111F35;
                font-family: "Segoe UI";
                font-size: 13px;
                font-weight: 800;
                padding-top: 8px;
            }
            #homeTitle, #artworkTitle {
                color: #111F35;
                font-family: "Segoe UI";
                font-size: 24px;
                font-weight: 800;
            }
            #homeHero {
                color: #8A244B;
                background-color: #F4F4F4;
                border: 1px solid #E1E1E1;
                border-radius: 18px;
                font-family: "Segoe UI";
                font-size: 32px;
                font-weight: 900;
                letter-spacing: 2px;
            }
            #homeArtworkImage {
                color: #8A244B;
                font-family: "Segoe UI";
                font-size: 32px;
                font-weight: 900;
            }
            #homeArtworkAnimation {
                color: #5D6875;
                font-family: "Segoe UI";
                font-size: 14px;
            }
            #homeDescription, #artworkDescription, #artworkMutedLabel {
                color: #5D6875;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            #artworkFieldLabel {
                color: #111F35;
                font-family: "Segoe UI";
                font-size: 13px;
                font-weight: 700;
            }
            #topHeaderBtn {
                background-color: transparent;
                color: #FFFFFF;
                border: 1px solid transparent;
                border-radius: 8px;
                min-width: 90px;
                min-height: 50px;
                padding: 0 16px;
                font-family: "Segoe UI";
                font-size: 14px;
                font-weight: 700;
            }
            #topHeaderBtn:checked, #topHeaderBtn:hover {
                background-color: #8A244B;
                border-color: #8A244B;
            }
            #artworkInput, #artworkSmallInput {
                background-color: #F2F2F2;
                color: #111111;
                border: 1px solid #C8C8C8;
                border-radius: 7px;
                min-height: 34px;
                padding: 0 10px;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            #artworkBrowseButton, #collectorRunBtn {
                background-color: #8A244B;
                color: #FFFFFF;
                border: 1px solid #8A244B;
                border-radius: 8px;
                min-height: 40px;
                padding: 0 16px;
                font-family: "Segoe UI";
                font-weight: 700;
            }
            #artworkCheckBox {
                color: #111F35;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            #artworkLog {
                background-color: #F7F7F7;
                color: #222222;
                border: 1px solid #D0D0D0;
                border-radius: 8px;
                padding: 8px;
                font-family: "Segoe UI";
                font-size: 12px;
            }
        """
