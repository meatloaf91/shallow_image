from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from artwork_processing import (
    ArtworkInspection,
    Bounds,
    CutOption,
    CutSelection,
    render_artwork_preview,
)

def _artwork_preview_qimage(preview: Any) -> QImage:
    if hasattr(preview, "samples"):
        return QImage(
            preview.samples,
            preview.width,
            preview.height,
            preview.stride,
            QImage.Format.Format_RGB888,
        ).copy()
    image = preview.convert("RGB")
    return QImage(
        image.tobytes(),
        image.width,
        image.height,
        image.width * 3,
        QImage.Format.Format_RGB888,
    ).copy()

class ArtworkCropCanvas(QLabel):
    """Preview canvas with a draggable crop rectangle in PDF-point space."""

    cropChanged = Signal(object)

    def __init__(self, image: QImage, page_bounds: object, initial_bounds: object, initial_path: tuple = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = QPixmap.fromImage(image)
        self._page_bounds = page_bounds
        self._crop = initial_bounds
        self._drag_start: tuple[float, float] | None = None
        self._drag_current: tuple[float, float] | None = None
        self._interaction: str | None = None
        self._interaction_start: tuple[float, float] | None = None
        self._interaction_bounds: object | None = None
        self._manual_drawn = False
        self._zoom = 1.0
        self._show_crop_border = True
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._pan_start: tuple[float, float] | None = None
        self._hover_bounds = None
        self._stroke_shape = "rectangle"
        self._corners = "sharp"
        self._corner_amount = 0.0
        self._stroke_weight = 0.5
        self._stroke_color = "#B8F35A"
        self._custom_path = list(initial_path)
        self._custom_segment: dict[str, object] | None = None
        self._custom_out_handle: tuple[float, float] | None = None
        self._path_history: list[tuple[tuple, tuple[float, float] | None]] = []
        self._path_history_index = 0
        self._active_path_before: tuple[tuple, tuple[float, float] | None] | None = None
        self._selected_anchor_index: int | None = None
        self._selected_anchor_indices: set[int] = set()
        self._selected_handle: tuple[str, int, int] | tuple[str] | None = None
        self._edit_tool = "pen"
        self._selection_start: tuple[float, float] | None = None
        self._selection_current: tuple[float, float] | None = None
        self._selection_base: set[int] = set()
        self._base_canvas_width = 700
        self._base_canvas_height = 460
        self._pen_cursor = self._create_pen_cursor()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(self._base_canvas_width, self._base_canvas_height)
        self.resize(self._base_canvas_width, self._base_canvas_height)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._path_history = [self._path_snapshot()]
        self._refresh()

    def _image_rect(self):
        target = self.size()
        return self._image.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def set_artwork_image(self, image: QImage) -> None:
        self._image = QPixmap.fromImage(image)
        self._refresh()

    def set_crop_border_visible(self, visible: bool) -> None:
        self._show_crop_border = visible
        self._refresh()

    def set_zoom(self, value: int) -> None:
        self._zoom = min(3.0, max(0.25, value / 100.0))
        content_w = max(self._base_canvas_width, round(self._base_canvas_width * self._zoom))
        content_h = max(self._base_canvas_height, round(self._base_canvas_height * self._zoom))
        self.setMinimumSize(content_w, content_h)
        self.resize(content_w, content_h)
        self._refresh()

    def wheelEvent(self, event) -> None:
        current = round(self._zoom * 100)
        step = 10 if event.angleDelta().y() > 0 else -10
        self.set_zoom(current + step)
        event.accept()

    def set_crop_style(self, color: str) -> None:
        self._crop_color = color
        self._refresh()

    def set_stroke_style(self, weight: float, color: str) -> None:
        self._stroke_weight = max(0.1, min(1.0, weight))
        self._stroke_color = color
        self._crop_color = color
        self._refresh()

    def set_hover_bounds(self, bounds: object | None) -> None:
        self._hover_bounds = bounds
        self._refresh()

    def set_shape_settings(self, stroke_shape: str, corners: str, corner_amount: float) -> None:
        self._stroke_shape = stroke_shape
        self._corners = corners
        self._corner_amount = max(0.0, min(1.0, corner_amount))
        if stroke_shape != "custom":
            self.unsetCursor()
        else:
            self._update_custom_cursor()
        self._refresh()

    def set_edit_tool(self, tool: str) -> None:
        self._edit_tool = tool
        if self._stroke_shape == "custom":
            self._update_custom_cursor()
        self._refresh()

    def _update_custom_cursor(self) -> None:
        self.setCursor(
            self._pen_cursor if self._edit_tool == "pen" else Qt.CursorShape.ArrowCursor
        )

    @staticmethod
    def _create_pen_cursor() -> QCursor:
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPainterPath()
        outline.moveTo(2, 22)
        outline.lineTo(6, 20)
        outline.lineTo(20, 6)
        outline.lineTo(17, 3)
        outline.lineTo(3, 17)
        outline.closeSubpath()
        painter.setPen(QPen(
            QColor("#1F2937"), 1.5, Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
        ))
        painter.setBrush(QColor("#F8FAFC"))
        painter.drawPath(outline)
        painter.setPen(QPen(QColor("#1F2937"), 1))
        painter.drawLine(5, 19, 7, 21)
        painter.end()
        return QCursor(pixmap, 2, 22)

    def set_custom_path(self, commands: tuple = ()) -> None:
        self._custom_path = list(commands)
        self._path_history = [self._path_snapshot()]
        self._path_history_index = 0
        self._active_path_before = None
        self._refresh()

    def _path_snapshot(self) -> tuple[tuple, tuple[float, float] | None]:
        return (tuple(self._custom_path), self._custom_out_handle)

    def _commit_path_change(self, before: tuple[tuple, tuple[float, float] | None] | None = None) -> None:
        previous = before if before is not None else self._path_snapshot()
        current = self._path_snapshot()
        if current == previous:
            return
        self._path_history = self._path_history[: self._path_history_index + 1]
        self._path_history.append(current)
        self._path_history_index = len(self._path_history) - 1

    def _undo_path_change(self) -> None:
        if self._path_history_index <= 0:
            return
        self._path_history_index -= 1
        path, outgoing_handle = self._path_history[self._path_history_index]
        self._custom_path = list(path)
        self._custom_out_handle = outgoing_handle
        self._custom_segment = None
        self._interaction = None
        self._selected_anchor_indices.clear()
        self._selected_anchor_index = None
        self._selected_handle = None
        self._refresh()
        self.cropChanged.emit(self._custom_path_bounds())
        self._update_custom_cursor()

    def _custom_path_bounds(self) -> object | None:
        points = [point for _, values in self._custom_path for point in zip(values[::2], values[1::2])]
        if not points:
            return None
        xs, ys = zip(*points)
        return Bounds(min(xs), min(ys), max(xs), max(ys))

    def _custom_anchor_points(self) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for command, values in self._custom_path:
            if command in {"M", "L"}:
                points.append((values[0], values[1]))
            elif command == "Q":
                points.append((values[2], values[3]))
            elif command == "C":
                points.append((values[4], values[5]))
        if self._custom_segment is not None and not self._custom_segment["first"]:
            point = self._custom_segment["end"]
            if not points or points[-1] != point:
                points.append(point)
        return points

    def _first_anchor_hit(self, x: float, y: float, tolerance: float = 12.0) -> bool:
        if not self._custom_path or self._custom_path[0][0] != "M":
            return False
        first_anchor = self._custom_path[0][1]
        display_x, display_y, _, _ = self._to_display_rect(
            Bounds(first_anchor[0], first_anchor[1], first_anchor[0], first_anchor[1])
        )
        return (x - display_x) ** 2 + (y - display_y) ** 2 <= tolerance ** 2

    def _page_to_display(self, point: tuple[float, float]) -> tuple[float, float]:
        left, top, width, height = self._display_rect()
        return (
            left + (point[0] - self._page_bounds.left) / max(self._page_bounds.width, 1) * width,
            top + (point[1] - self._page_bounds.top) / max(self._page_bounds.height, 1) * height,
        )

    def _custom_anchor_hit(self, x: float, y: float, tolerance: float = 10.0) -> int | None:
        nearest: tuple[float, int] | None = None
        for index, point in enumerate(self._custom_anchor_points()):
            display_x, display_y = self._page_to_display(point)
            distance = (x - display_x) ** 2 + (y - display_y) ** 2
            if distance <= tolerance ** 2 and (nearest is None or distance < nearest[0]):
                nearest = (distance, index)
        return nearest[1] if nearest is not None else None

    @staticmethod
    def _lerp_point(
        first: tuple[float, float], second: tuple[float, float], amount: float
    ) -> tuple[float, float]:
        return (
            first[0] + (second[0] - first[0]) * amount,
            first[1] + (second[1] - first[1]) * amount,
        )

    def _segment_point(self, segment_index: int, amount: float) -> tuple[float, float]:
        command, values = self._custom_path[segment_index]
        anchors = self._custom_anchor_points()
        start = anchors[segment_index - 1]
        if command == "Z":
            end = anchors[0]
            return self._lerp_point(start, end, amount)
        if command == "L":
            end = (values[0], values[1])
            return self._lerp_point(start, end, amount)
        if command == "Q":
            control = (values[0], values[1])
            end = (values[2], values[3])
            first_leg = self._lerp_point(start, control, amount)
            second_leg = self._lerp_point(control, end, amount)
            return self._lerp_point(first_leg, second_leg, amount)
        if command == "C":
            control1 = (values[0], values[1])
            control2 = (values[2], values[3])
            end = (values[4], values[5])
            first = self._lerp_point(start, control1, amount)
            second = self._lerp_point(control1, control2, amount)
            third = self._lerp_point(control2, end, amount)
            first_mid = self._lerp_point(first, second, amount)
            second_mid = self._lerp_point(second, third, amount)
            return self._lerp_point(first_mid, second_mid, amount)
        return start

    def _custom_segment_hit(self, x: float, y: float, tolerance: float = 9.0):
        if len(self._custom_path) < 2:
            return None
        nearest: tuple[float, int, float] | None = None
        for segment_index, (command, _) in enumerate(self._custom_path):
            if segment_index == 0 or command not in {"L", "Q", "C", "Z"}:
                continue
            previous = self._custom_path[segment_index - 1][0]
            if previous == "Z":
                continue
            sample_count = 48 if command in {"Q", "C"} else 1
            sample_t = 0.0
            sample_x, sample_y = self._page_to_display(self._segment_point(segment_index, 0.0))
            for sample_index in range(1, sample_count + 1):
                current_t = sample_index / sample_count
                current_x, current_y = self._page_to_display(
                    self._segment_point(segment_index, current_t)
                )
                dx, dy = current_x - sample_x, current_y - sample_y
                length_squared = dx * dx + dy * dy
                along = 0.0 if length_squared == 0 else max(
                    0.0, min(1.0, ((x - sample_x) * dx + (y - sample_y) * dy) / length_squared)
                )
                hit_x, hit_y = sample_x + along * dx, sample_y + along * dy
                distance_squared = (x - hit_x) ** 2 + (y - hit_y) ** 2
                if nearest is None or distance_squared < nearest[0]:
                    segment_t = sample_t + (current_t - sample_t) * along
                    nearest = (distance_squared, segment_index, segment_t)
                sample_t, sample_x, sample_y = current_t, current_x, current_y
        if nearest is None or nearest[0] > tolerance * tolerance:
            return None
        _, segment_index, amount = nearest
        command = self._custom_path[segment_index][0]
        if command in {"Q", "C"}:
            step = 1.0 / 48
            low = max(0.0, amount - step)
            high = min(1.0, amount + step)

            def distance_squared(parameter: float) -> float:
                display_x, display_y = self._page_to_display(
                    self._segment_point(segment_index, parameter)
                )
                return (x - display_x) ** 2 + (y - display_y) ** 2

            for _ in range(48):
                first = low + (high - low) / 3
                second = high - (high - low) / 3
                if distance_squared(first) <= distance_squared(second):
                    high = second
                else:
                    low = first
            amount = (low + high) / 2
        return segment_index, amount

    def _insert_custom_anchor(self, segment_index: int, amount: float) -> int:
        before = self._path_snapshot()
        command, values = self._custom_path[segment_index]
        anchors = self._custom_anchor_points()
        start = anchors[segment_index - 1]
        new_anchor_index = segment_index
        if command == "Z":
            point = self._segment_point(segment_index, amount)
            self._custom_path[segment_index] = ("L", point)
            self._custom_path.append(("Z", ()))
        elif command == "L":
            end = (values[0], values[1])
            point = self._lerp_point(start, end, amount)
            self._custom_path[segment_index] = ("L", point)
            self._custom_path.insert(segment_index + 1, ("L", end))
        elif command == "Q":
            control = (values[0], values[1])
            end = (values[2], values[3])
            first_control = self._lerp_point(start, control, amount)
            second_control = self._lerp_point(control, end, amount)
            point = self._lerp_point(first_control, second_control, amount)
            self._custom_path[segment_index] = ("Q", (*first_control, *point))
            self._custom_path.insert(segment_index + 1, ("Q", (*second_control, *end)))
        elif command == "C":
            control1 = (values[0], values[1])
            control2 = (values[2], values[3])
            end = (values[4], values[5])
            first = self._lerp_point(start, control1, amount)
            second = self._lerp_point(control1, control2, amount)
            third = self._lerp_point(control2, end, amount)
            first_mid = self._lerp_point(first, second, amount)
            second_mid = self._lerp_point(second, third, amount)
            point = self._lerp_point(first_mid, second_mid, amount)
            self._custom_path[segment_index] = (
                "C", (*first, *first_mid, *point)
            )
            self._custom_path.insert(
                segment_index + 1, ("C", (*second_mid, *third, *end))
            )
        else:
            return -1
        self._selected_anchor_index = new_anchor_index
        self._selected_anchor_indices = {new_anchor_index}
        self._selected_handle = None
        self._manual_drawn = True
        self._commit_path_change(before)
        self._refresh()
        self.cropChanged.emit(self._custom_path_bounds())
        return new_anchor_index

    def _anchors_in_display_rect(self, rect: QRect) -> set[int]:
        normalized = rect.normalized()
        return {
            index
            for index, point in enumerate(self._custom_anchor_points())
            if normalized.contains(round(self._page_to_display(point)[0]), round(self._page_to_display(point)[1]))
        }

    def _custom_handle_points(self, anchor_index: int) -> list[tuple[tuple[str, int, int] | tuple[str], tuple[float, float]]]:
        handles: list[tuple[tuple[str, int, int] | tuple[str], tuple[float, float]]] = []
        if anchor_index > 0 and anchor_index < len(self._custom_path):
            command, values = self._custom_path[anchor_index]
            if command == "C":
                handles.append((("path", anchor_index, 1), (values[2], values[3])))
            elif command == "Q":
                handles.append((("path", anchor_index, 0), (values[0], values[1])))
        outgoing_index = anchor_index + 1
        if outgoing_index < len(self._custom_path):
            command, values = self._custom_path[outgoing_index]
            if command == "C":
                handles.append((("path", outgoing_index, 0), (values[0], values[1])))
            elif command == "Q":
                handles.append((("path", outgoing_index, 0), (values[0], values[1])))
        if (
            anchor_index == len(self._custom_anchor_points()) - 1
            and self._custom_out_handle is not None
            and not (self._custom_path and self._custom_path[-1][0] == "Z")
        ):
            handles.append((("pending",), self._custom_out_handle))
        return handles

    def _custom_handle_hit(self, x: float, y: float, anchor_index: int, tolerance: float = 9.0):
        nearest = None
        for descriptor, point in self._custom_handle_points(anchor_index):
            display_x, display_y = self._page_to_display(point)
            distance = (x - display_x) ** 2 + (y - display_y) ** 2
            if distance <= tolerance ** 2 and (nearest is None or distance < nearest[0]):
                nearest = (distance, descriptor)
        return nearest[1] if nearest is not None else None

    def _move_custom_anchors(self, anchor_indices: set[int], dx: float, dy: float) -> None:
        for command_index, (command, values) in enumerate(self._custom_path):
            updated = list(values)
            if command_index in anchor_indices:
                if command == "M" or command == "L":
                    updated[0] += dx
                    updated[1] += dy
                elif command == "Q":
                    updated[2] += dx
                    updated[3] += dy
                elif command == "C":
                    updated[2] += dx
                    updated[3] += dy
                    updated[4] += dx
                    updated[5] += dy
            if command == "Q" and (command_index in anchor_indices or command_index - 1 in anchor_indices):
                updated[0] += dx
                updated[1] += dy
            elif command == "C" and command_index - 1 in anchor_indices:
                updated[0] += dx
                updated[1] += dy
            if updated != list(values):
                self._custom_path[command_index] = (command, tuple(updated))
        if anchor_indices and max(anchor_indices) == len(self._custom_anchor_points()) - 1 and self._custom_out_handle is not None:
            self._custom_out_handle = (self._custom_out_handle[0] + dx, self._custom_out_handle[1] + dy)

    def _move_custom_anchor(self, anchor_index: int, dx: float, dy: float) -> None:
        self._move_custom_anchors({anchor_index}, dx, dy)

    def _move_custom_handle(self, descriptor: tuple, point: tuple[float, float]) -> None:
        if descriptor[0] == "pending":
            self._custom_out_handle = point
            return
        _, command_index, control_index = descriptor
        command, values = self._custom_path[command_index]
        updated = list(values)
        point_offset = control_index * 2
        updated[point_offset] = point[0]
        updated[point_offset + 1] = point[1]
        self._custom_path[command_index] = (command, tuple(updated))

    def _begin_custom_anchor_edit(self, anchor_index: int, x: float, y: float) -> None:
        self._selected_anchor_index = anchor_index
        self._selected_handle = self._custom_handle_hit(x, y, anchor_index)
        self._interaction_start = self._to_page_point(x, y)
        self._manual_drawn = False
        if self._selected_handle is None:
            self._interaction = "move_custom_anchor"
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self._interaction = "move_custom_handle"
            self.setCursor(Qt.CursorShape.CrossCursor)
        self._refresh()

    def _anchor_segment_indices(self, anchor_index: int) -> tuple[int | None, int | None]:
        incoming = anchor_index if anchor_index > 0 and anchor_index < len(self._custom_path) else None
        outgoing_index = anchor_index + 1
        outgoing = (
            outgoing_index
            if outgoing_index < len(self._custom_path) and self._custom_path[outgoing_index][0] != "Z"
            else None
        )
        return incoming, outgoing

    def _segment_controls(self, segment_index: int) -> tuple[tuple[float, float], tuple[float, float]]:
        command, values = self._custom_path[segment_index]
        anchors = self._custom_anchor_points()
        start, end = anchors[segment_index - 1], anchors[segment_index]
        if command == "C":
            return (values[0], values[1]), (values[2], values[3])
        if command == "Q":
            control = (values[0], values[1])
            return (
                (start[0] + 2 * (control[0] - start[0]) / 3, start[1] + 2 * (control[1] - start[1]) / 3),
                (end[0] + 2 * (control[0] - end[0]) / 3, end[1] + 2 * (control[1] - end[1]) / 3),
            )
        return (
            (start[0] + (end[0] - start[0]) / 3, start[1] + (end[1] - start[1]) / 3),
            (start[0] + 2 * (end[0] - start[0]) / 3, start[1] + 2 * (end[1] - start[1]) / 3),
        )

    def _set_segment_controls(
        self,
        segment_index: int,
        control1: tuple[float, float],
        control2: tuple[float, float],
    ) -> None:
        _, values = self._custom_path[segment_index]
        endpoint = (values[4], values[5]) if len(values) >= 6 else self._custom_anchor_points()[segment_index]
        self._custom_path[segment_index] = (
            "C",
            (control1[0], control1[1], control2[0], control2[1], endpoint[0], endpoint[1]),
        )

    def _set_anchor_curve_mode(self, anchor_index: int, mode: str) -> None:
        before = self._path_snapshot()
        anchors = self._custom_anchor_points()
        incoming_index, outgoing_index = self._anchor_segment_indices(anchor_index)
        anchor = anchors[anchor_index]
        incoming = None
        outgoing = None
        if incoming_index is not None:
            incoming = list(self._segment_controls(incoming_index))
        if outgoing_index is not None:
            outgoing = list(self._segment_controls(outgoing_index))

        incoming_command = self._custom_path[incoming_index][0] if incoming_index is not None else ""
        outgoing_command = self._custom_path[outgoing_index][0] if outgoing_index is not None else ""
        incoming_active = incoming is not None and incoming_command in {"C", "Q"} and incoming[1] != anchor
        outgoing_active = outgoing is not None and outgoing_command in {"C", "Q"} and outgoing[0] != anchor

        if mode == "corner":
            if incoming_index is not None:
                values = self._custom_path[incoming_index][1]
                endpoint = (values[-2], values[-1])
                self._custom_path[incoming_index] = ("L", endpoint)
            if outgoing_index is not None:
                values = self._custom_path[outgoing_index][1]
                endpoint = (values[-2], values[-1])
                self._custom_path[outgoing_index] = ("L", endpoint)
            self._selected_anchor_index = anchor_index
            self._selected_anchor_indices = {anchor_index}
            self._selected_handle = None
            self._manual_drawn = True
            self._commit_path_change(before)
            self._refresh()
            self.cropChanged.emit(self._custom_path_bounds())
            return
        elif mode == "curve":
            if incoming is None and outgoing is None:
                return
            use_incoming = incoming_active or (not outgoing_active and outgoing is None)
            if use_incoming:
                incoming[1] = incoming[1] if incoming_active else self._segment_controls(incoming_index)[1]
                if outgoing is not None:
                    outgoing[0] = anchor
            else:
                outgoing[0] = outgoing[0] if outgoing_active else self._segment_controls(outgoing_index)[0]
                if incoming is not None:
                    incoming[1] = anchor
        elif mode == "curve_mirror":
            if incoming is None and outgoing is None:
                return
            if not incoming_active and not outgoing_active:
                if incoming is not None and outgoing is not None:
                    source = self._segment_controls(outgoing_index)[0]
                    reflected = (2 * anchor[0] - source[0], 2 * anchor[1] - source[1])
                    outgoing[0] = anchor
                    incoming[1] = reflected
                elif outgoing is not None:
                    outgoing[0] = self._segment_controls(outgoing_index)[0]
                else:
                    incoming[1] = self._segment_controls(incoming_index)[1]
            elif outgoing_active:
                if incoming is not None:
                    source = outgoing[0]
                    incoming[1] = (2 * anchor[0] - source[0], 2 * anchor[1] - source[1])
                    outgoing[0] = anchor
            elif incoming_active and outgoing is not None:
                source = incoming[1]
                outgoing[0] = (2 * anchor[0] - source[0], 2 * anchor[1] - source[1])
                incoming[1] = anchor
        elif mode == "curve_two_handles":
            if incoming is None and outgoing is None:
                return
            if incoming is not None and not incoming_active:
                incoming[1] = self._segment_controls(incoming_index)[1]
            if outgoing is not None and not outgoing_active:
                outgoing[0] = self._segment_controls(outgoing_index)[0]

        if incoming_index is not None:
            self._set_segment_controls(incoming_index, incoming[0], incoming[1])
        if outgoing_index is not None:
            self._set_segment_controls(outgoing_index, outgoing[0], outgoing[1])
        self._selected_anchor_index = anchor_index
        self._selected_anchor_indices = {anchor_index}
        self._selected_handle = None
        self._manual_drawn = True
        self._commit_path_change(before)
        self._refresh()
        self.cropChanged.emit(self._custom_path_bounds())

    def _set_anchor_corner_curve(self, anchor_index: int, anchor_type: str) -> None:
        self._set_anchor_curve_mode(anchor_index, "corner" if anchor_type == "corner" else "curve")

    def _anchor_local_direction(self, anchor_index: int) -> tuple[float, float] | None:
        anchors = self._custom_anchor_points()
        if anchor_index < 0 or anchor_index >= len(anchors) or len(anchors) < 2:
            return None
        if anchor_index == 0:
            start, end = anchors[0], anchors[1]
        elif anchor_index == len(anchors) - 1:
            start, end = anchors[-2], anchors[-1]
        else:
            start, end = anchors[anchor_index - 1], anchors[anchor_index + 1]
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-9:
            return None
        return dx / length, dy / length

    def _can_straighten_selected_anchors(self, parallel_tolerance: float = 0.05) -> bool:
        if len(self._selected_anchor_indices) != 2:
            return False
        first, second = sorted(self._selected_anchor_indices)
        first_direction = self._anchor_local_direction(first)
        second_direction = self._anchor_local_direction(second)
        if first_direction is None or second_direction is None:
            return False
        dot = first_direction[0] * second_direction[0] + first_direction[1] * second_direction[1]
        return dot < 0.5 or abs(dot) >= 1.0 - parallel_tolerance

    def _straighten_selected_anchors(self) -> None:
        if not self._can_straighten_selected_anchors():
            return
        before = self._path_snapshot()
        first, last = sorted(self._selected_anchor_indices)
        anchors = self._custom_anchor_points()
        selected_points = [anchors[first], anchors[last]]
        center = (
            sum(point[0] for point in selected_points) / 2,
            sum(point[1] for point in selected_points) / 2,
        )
        align_horizontally = abs(selected_points[1][0] - selected_points[0][0]) >= abs(
            selected_points[1][1] - selected_points[0][1]
        )
        for index in (first, last):
            point = anchors[index]
            target = (point[0], center[1]) if align_horizontally else (center[0], point[1])
            self._move_custom_anchors({index}, target[0] - point[0], target[1] - point[1])
        self._commit_path_change(before)
        self._manual_drawn = True
        self._refresh()
        self.cropChanged.emit(self._custom_path_bounds())

    def _build_anchor_menu(self, anchor_index: int) -> QMenu:
        menu = QMenu(self)
        curve_action = menu.addAction("Curve")
        mirror_action = menu.addAction("Curve (mirror handle)")
        two_handles_action = menu.addAction("Curve (2 handles)")
        menu.addSeparator()
        corner_action = menu.addAction("Corner")
        curve_action.triggered.connect(
            lambda checked=False: self._set_anchor_curve_mode(anchor_index, "curve")
        )
        mirror_action.triggered.connect(
            lambda checked=False: self._set_anchor_curve_mode(anchor_index, "curve_mirror")
        )
        two_handles_action.triggered.connect(
            lambda checked=False: self._set_anchor_curve_mode(anchor_index, "curve_two_handles")
        )
        corner_action.triggered.connect(
            lambda checked=False: self._set_anchor_curve_mode(anchor_index, "corner")
        )
        if self._can_straighten_selected_anchors():
            menu.addSeparator()
            straighten_action = menu.addAction("Straighten line")
            straighten_action.triggered.connect(
                lambda checked=False: self._straighten_selected_anchors()
            )
        return menu

    def _show_anchor_menu(self, anchor_index: int, global_position) -> None:
        self._build_anchor_menu(anchor_index).exec(global_position)

    def undo_custom_anchor(self) -> None:
        if self._stroke_shape != "custom":
            return
        if self._custom_segment is not None:
            if self._active_path_before is not None:
                path, outgoing_handle = self._active_path_before
                self._custom_path = list(path)
                self._custom_out_handle = outgoing_handle
            self._custom_segment = None
            self._interaction = None
            self._active_path_before = None
            self._refresh()
            self.cropChanged.emit(self._custom_path_bounds())
            self._update_custom_cursor()
            return
        if self._active_path_before is not None:
            path, outgoing_handle = self._active_path_before
            self._custom_path = list(path)
            self._custom_out_handle = outgoing_handle
            self._active_path_before = None
            self._interaction = None
            self._interaction_start = None
            self._refresh()
            self.cropChanged.emit(self._custom_path_bounds())
            self._update_custom_cursor()
            return
        self._undo_path_change()

    def keyPressEvent(self, event) -> None:
        undo_shortcut = (
            event.key() == Qt.Key.Key_Z
            and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        )
        if self._stroke_shape == "custom" and (
            event.key() in {Qt.Key.Key_Backspace, Qt.Key.Key_Delete} or undo_shortcut
        ):
            self.undo_custom_anchor()
            event.accept()
            return
        super().keyPressEvent(event)

    def _custom_qpath(self, preview: dict | None = None) -> QPainterPath:
        path = QPainterPath()
        def display_point(point: tuple[float, float]) -> tuple[float, float]:
            left, top, width, height = self._display_rect()
            return (
                left + (point[0] - self._page_bounds.left) / max(self._page_bounds.width, 1) * width,
                top + (point[1] - self._page_bounds.top) / max(self._page_bounds.height, 1) * height,
            )
        for command, values in self._custom_path:
            if command == "M":
                path.moveTo(*display_point((values[0], values[1])))
            elif command == "L":
                path.lineTo(*display_point((values[0], values[1])))
            elif command == "Q":
                control = display_point((values[0], values[1]))
                end = display_point((values[2], values[3]))
                path.quadTo(control[0], control[1], end[0], end[1])
            elif command == "C":
                control1 = display_point((values[0], values[1]))
                control2 = display_point((values[2], values[3]))
                end = display_point((values[4], values[5]))
                path.cubicTo(
                    control1[0], control1[1], control2[0], control2[1], end[0], end[1]
                )
            elif command == "Z":
                path.closeSubpath()
        if preview is not None and not preview["first"]:
            control1 = display_point(preview["control1"])
            control2 = display_point(preview["control2"])
            end = display_point(preview["end"])
            path.cubicTo(
                control1[0], control1[1], control2[0], control2[1], end[0], end[1]
            )
        return path

    def _custom_point(self, point: tuple[float, float], modifiers: Qt.KeyboardModifier) -> tuple[float, float]:
        if not self._custom_path or not modifiers & Qt.KeyboardModifier.ShiftModifier:
            return point
        last_values = self._custom_path[-1][1]
        last = (last_values[-2], last_values[-1])
        dx, dy = point[0] - last[0], point[1] - last[1]
        return (last[0] + dx, last[1]) if abs(dx) >= abs(dy) else (last[0], last[1] + dy)

    def _display_rect(self):
        scaled = self._image_rect()
        left = (self.width() - scaled.width()) / 2 + self._pan_x
        top = (self.height() - scaled.height()) / 2 + self._pan_y
        return left, top, scaled.width(), scaled.height()

    def _rect_hit_test(self, x: float, y: float) -> str | None:
        if self._crop is None:
            return None
        rect_x, rect_y, rect_w, rect_h = self._to_display_rect(self._crop)
        pad = 10
        if rect_w <= 0 or rect_h <= 0:
            if rect_x <= x <= rect_x + rect_w and rect_y <= y <= rect_y + rect_h:
                return "move"
            return None
        if x <= rect_x + pad and y <= rect_y + pad:
            return "resize_tl"
        if x >= rect_x + rect_w - pad and y <= rect_y + pad:
            return "resize_tr"
        if x <= rect_x + pad and y >= rect_y + rect_h - pad:
            return "resize_bl"
        if x >= rect_x + rect_w - pad and y >= rect_y + rect_h - pad:
            return "resize_br"
        if x <= rect_x + pad:
            return "resize_left"
        if x >= rect_x + rect_w - pad:
            return "resize_right"
        if y <= rect_y + pad:
            return "resize_top"
        if y >= rect_y + rect_h - pad:
            return "resize_bottom"
        if rect_x <= x <= rect_x + rect_w and rect_y <= y <= rect_y + rect_h:
            return "move"
        return None

    def _clamp_page_point(self, x: float, y: float) -> tuple[float, float]:
        return (
            max(self._page_bounds.left, min(self._page_bounds.right, x)),
            max(self._page_bounds.top, min(self._page_bounds.bottom, y)),
        )

    def _apply_interaction(self, end_point: tuple[float, float]) -> None:
        if self._interaction is None or self._interaction_start is None or self._interaction_bounds is None:
            return
        start_x, start_y = self._interaction_start
        end_x, end_y = end_point
        base = self._interaction_bounds
        dx = end_x - start_x
        dy = end_y - start_y
        if self._interaction == "move":
            left = base.left + dx
            top = base.top + dy
            right = base.right + dx
            bottom = base.bottom + dy
            width = base.width
            height = base.height
            if left < self._page_bounds.left:
                delta = self._page_bounds.left - left
                left += delta
                right += delta
            if top < self._page_bounds.top:
                delta = self._page_bounds.top - top
                top += delta
                bottom += delta
            if right > self._page_bounds.right:
                delta = right - self._page_bounds.right
                left -= delta
                right -= delta
            if bottom > self._page_bounds.bottom:
                delta = bottom - self._page_bounds.bottom
                top -= delta
                bottom -= delta
            self._crop = Bounds(left, top, right, bottom)
            return

        if self._interaction in {"resize_left", "resize_tl", "resize_bl"}:
            new_left = min(base.right - 1.0, max(self._page_bounds.left, base.left + dx))
            base = Bounds(new_left, base.top, base.right, base.bottom)
        if self._interaction in {"resize_right", "resize_tr", "resize_br"}:
            new_right = max(base.left + 1.0, min(self._page_bounds.right, base.right + dx))
            base = Bounds(base.left, base.top, new_right, base.bottom)
        if self._interaction in {"resize_top", "resize_tl", "resize_tr"}:
            new_top = min(base.bottom - 1.0, max(self._page_bounds.top, base.top + dy))
            base = Bounds(base.left, new_top, base.right, base.bottom)
        if self._interaction in {"resize_bottom", "resize_bl", "resize_br"}:
            new_bottom = max(base.top + 1.0, min(self._page_bounds.bottom, base.bottom + dy))
            base = Bounds(base.left, base.top, base.right, new_bottom)
        self._crop = base

    def _cursor_for_interaction(self, interaction: str | None) -> Qt.CursorShape:
        mapping = {
            "move": Qt.CursorShape.SizeAllCursor,
            "resize_left": Qt.CursorShape.SizeHorCursor,
            "resize_right": Qt.CursorShape.SizeHorCursor,
            "resize_top": Qt.CursorShape.SizeVerCursor,
            "resize_bottom": Qt.CursorShape.SizeVerCursor,
            "resize_tl": Qt.CursorShape.SizeFDiagCursor,
            "resize_tr": Qt.CursorShape.SizeBDiagCursor,
            "resize_bl": Qt.CursorShape.SizeBDiagCursor,
            "resize_br": Qt.CursorShape.SizeFDiagCursor,
        }
        return mapping.get(interaction, Qt.CursorShape.CrossCursor)

    def _to_page_point(self, x: float, y: float) -> tuple[float, float]:
        left, top, width, height = self._display_rect()
        px = self._page_bounds.left + ((x - left) / max(width, 1)) * self._page_bounds.width
        py = self._page_bounds.top + ((y - top) / max(height, 1)) * self._page_bounds.height
        return (
            max(self._page_bounds.left, min(self._page_bounds.right, px)),
            max(self._page_bounds.top, min(self._page_bounds.bottom, py)),
        )

    def _to_display_rect(self, bounds: object) -> tuple[float, float, float, float]:
        left, top, width, height = self._display_rect()
        x = left + (bounds.left - self._page_bounds.left) / max(self._page_bounds.width, 1) * width
        y = top + (bounds.top - self._page_bounds.top) / max(self._page_bounds.height, 1) * height
        w = bounds.width / max(self._page_bounds.width, 1) * width
        h = bounds.height / max(self._page_bounds.height, 1) * height
        return x, y, w, h

    def _refresh(self) -> None:
        pixmap = self._image_rect()
        canvas = QPixmap(self.size())
        canvas.fill(QColor("#F2F2F2"))
        painter = QPainter(canvas)
        painter.drawPixmap((self.width() - pixmap.width()) // 2, (self.height() - pixmap.height()) // 2, pixmap)
        painter.setOpacity(1.0 if self._show_crop_border else 0.0)
        x, y, width, height = self._to_display_rect(self._crop)
        stroke_color = getattr(self, "_crop_color", self._stroke_color)
        stroke_pen = QPen(QColor(stroke_color))
        stroke_pen.setWidthF(4.0 * self._stroke_weight)
        painter.setPen(stroke_pen)
        rect = QRect(round(x), round(y), max(1, round(width)), max(1, round(height)))
        if self._stroke_shape == "custom" and self._custom_path:
            painter.drawPath(
                self._custom_qpath(self._custom_segment)
                if self._custom_segment else self._custom_qpath()
            )
            anchors = self._custom_anchor_points()
            if len(self._selected_anchor_indices) == 1 and self._selected_anchor_index is not None and self._selected_anchor_index < len(anchors):
                selected_anchor = anchors[self._selected_anchor_index]
                selected_x, selected_y = self._page_to_display(selected_anchor)
                painter.setPen(QPen(QColor("#697586"), 1, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for descriptor, handle in self._custom_handle_points(self._selected_anchor_index):
                    handle_x, handle_y = self._page_to_display(handle)
                    painter.drawLine(round(selected_x), round(selected_y), round(handle_x), round(handle_y))
                    painter.setPen(QPen(QColor("#1F2937"), 1))
                    painter.setBrush(QColor("#FFFFFF"))
                    painter.drawEllipse(round(handle_x - 4), round(handle_y - 4), 8, 8)
                    painter.setPen(QPen(QColor("#697586"), 1, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
            for index, anchor in enumerate(anchors):
                anchor_x, anchor_y = self._page_to_display(anchor)
                painter.setPen(QPen(QColor("#1F2937"), 1))
                painter.setBrush(QColor("#7CD5C7") if index in self._selected_anchor_indices else QColor("#FFFFFF"))
                painter.drawRect(round(anchor_x - 3), round(anchor_y - 3), 7, 7)
            if self._selection_start is not None and self._selection_current is not None:
                selection_rect = QRect(
                    round(self._selection_start[0]), round(self._selection_start[1]),
                    round(self._selection_current[0] - self._selection_start[0]),
                    round(self._selection_current[1] - self._selection_start[1]),
                ).normalized()
                painter.setPen(QPen(QColor("#D02752"), 1, Qt.PenStyle.DashLine))
                painter.setBrush(QColor(208, 39, 82, 35))
                painter.drawRect(selection_rect)
        elif self._stroke_shape == "ellipse":
            painter.drawEllipse(rect)
        elif self._corners == "rounded":
            radius = min(width, height) * (0.5 * self._corner_amount)
            painter.drawRoundedRect(rect, radius, radius)
        elif self._corners == "beveled" or self._stroke_shape == "custom":
            path = QPainterPath()
            bevel = min(width, height) * (0.25 * self._corner_amount)
            path.moveTo(x + bevel, y)
            path.lineTo(x + width - bevel, y)
            path.lineTo(x + width, y + bevel)
            path.lineTo(x + width, y + height - bevel)
            path.lineTo(x + width - bevel, y + height)
            path.lineTo(x + bevel, y + height)
            path.lineTo(x, y + height - bevel)
            path.lineTo(x, y + bevel)
            path.closeSubpath()
            painter.drawPath(path)
        else:
            painter.drawRect(rect)
        if self._hover_bounds is not None:
            hover_x, hover_y, hover_width, hover_height = self._to_display_rect(self._hover_bounds)
            painter.setPen(QPen(QColor("#FFFF00"), 3, Qt.PenStyle.DashLine))
            painter.drawRect(
                round(hover_x), round(hover_y),
                max(1, round(hover_width)), max(1, round(hover_height)),
            )
        painter.setOpacity(1.0)
        painter.end()
        self.setPixmap(canvas)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = (event.position().x(), event.position().y())
            return
        point = self._to_page_point(event.position().x(), event.position().y())
        if self._stroke_shape == "custom":
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            if event.button() == Qt.MouseButton.RightButton:
                anchor_index = self._custom_anchor_hit(event.position().x(), event.position().y())
                if anchor_index is not None:
                    if anchor_index not in self._selected_anchor_indices:
                        self._selected_anchor_indices = {anchor_index}
                    self._selected_anchor_index = anchor_index
                    self._refresh()
                    self._show_anchor_menu(anchor_index, event.globalPosition().toPoint())
                event.accept()
                return
            if event.button() != Qt.MouseButton.LeftButton:
                return
            if self._edit_tool == "select":
                if len(self._selected_anchor_indices) == 1 and self._selected_anchor_index is not None:
                    selected_handle = self._custom_handle_hit(
                        event.position().x(), event.position().y(), self._selected_anchor_index
                    )
                    if selected_handle is not None:
                        self._selected_handle = selected_handle
                        self._interaction_start = point
                        self._active_path_before = self._path_snapshot()
                        self._interaction = "move_custom_handle"
                        self._manual_drawn = False
                        self.setCursor(Qt.CursorShape.CrossCursor)
                        self._refresh()
                        event.accept()
                        return
                anchor_index = self._custom_anchor_hit(event.position().x(), event.position().y())
                if anchor_index is not None:
                    if anchor_index not in self._selected_anchor_indices:
                        self._selected_anchor_indices = {anchor_index}
                    self._selected_anchor_index = anchor_index
                    handle = (
                        self._custom_handle_hit(event.position().x(), event.position().y(), anchor_index)
                        if len(self._selected_anchor_indices) == 1 else None
                    )
                    self._selected_handle = handle
                    self._interaction_start = point
                    self._active_path_before = self._path_snapshot()
                    self._interaction = "move_custom_handle" if handle is not None else "move_custom_anchors"
                    self._manual_drawn = False
                    self.setCursor(Qt.CursorShape.CrossCursor if handle is not None else Qt.CursorShape.SizeAllCursor)
                else:
                    segment_hit = self._custom_segment_hit(
                        event.position().x(), event.position().y()
                    )
                    if segment_hit is not None:
                        self._insert_custom_anchor(*segment_hit)
                    else:
                        self._selection_start = (event.position().x(), event.position().y())
                        self._selection_current = self._selection_start
                        self._selection_base = (
                            set(self._selected_anchor_indices)
                            if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
                            else set()
                        )
                        self._interaction = "select_anchors"
                        self.setCursor(Qt.CursorShape.CrossCursor)
                self._refresh()
                event.accept()
                return
            if self._selected_anchor_index is not None:
                handle = self._custom_handle_hit(
                    event.position().x(), event.position().y(), self._selected_anchor_index
                )
                if handle is not None:
                    self._selected_handle = handle
                    self._interaction_start = point
                    self._active_path_before = self._path_snapshot()
                    self._interaction = "move_custom_handle"
                    self._manual_drawn = False
                    self.setCursor(Qt.CursorShape.CrossCursor)
                    self._refresh()
                    event.accept()
                    return
            if (
                len(self._custom_anchor_points()) >= 3
                and self._first_anchor_hit(event.position().x(), event.position().y())
            ):
                before = self._path_snapshot()
                self._custom_path.append(("Z", ()))
                self._custom_out_handle = None
                self._custom_segment = None
                self._interaction = None
                self._commit_path_change(before)
                self._refresh()
                self.cropChanged.emit(self._custom_path_bounds())
                self._update_custom_cursor()
                event.accept()
                return
            anchor_index = self._custom_anchor_hit(event.position().x(), event.position().y())
            if anchor_index is not None:
                self._selected_anchor_indices = {anchor_index}
                self._selected_anchor_index = anchor_index
                self._selected_handle = None
                self._active_path_before = self._path_snapshot()
                self._interaction_start = point
                self._interaction = "move_custom_anchors"
                self._manual_drawn = False
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self._refresh()
                event.accept()
                return
            segment_hit = self._custom_segment_hit(
                event.position().x(), event.position().y()
            )
            if segment_hit is not None:
                self._insert_custom_anchor(*segment_hit)
                event.accept()
                return
            if self._custom_path and self._custom_path[-1][0] == "Z":
                event.accept()
                return
            point = self._custom_point(point, event.modifiers())
            new_anchor_index = len(self._custom_anchor_points()) if self._custom_path else 0
            self._active_path_before = self._path_snapshot()
            if not self._custom_path:
                self._custom_path.append(("M", point))
                self._custom_segment = {
                    "start": point,
                    "end": point,
                    "handle": point,
                    "first": True,
                }
            else:
                last_values = self._custom_path[-1][1]
                start = (last_values[-2], last_values[-1])
                self._custom_segment = {
                    "start": start,
                    "end": point,
                    "handle": point,
                    "control1": self._custom_out_handle or start,
                    "control2": point,
                    "first": False,
                    "force_line": bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier),
                }
            self._selected_anchor_index = new_anchor_index
            self._selected_anchor_indices = {new_anchor_index}
            self._selected_handle = None
            self._manual_drawn = True
            self._interaction = "custom"
            self._update_custom_cursor()
            self._refresh()
            return
        hit = self._rect_hit_test(event.position().x(), event.position().y()) if self._crop is not None else None
        if hit is not None:
            self._interaction = hit
            self._interaction_start = point
            self._interaction_bounds = self._crop
            self._manual_drawn = True
            self.setCursor(self._cursor_for_interaction(hit))
            return
        self._drag_start = point
        self._drag_current = point
        self._interaction = "draw"
        self._manual_drawn = True
        self._interaction_start = point
        self._interaction_bounds = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_start is not None:
            self._pan_x += event.position().x() - self._pan_start[0]
            self._pan_y += event.position().y() - self._pan_start[1]
            self._pan_start = (event.position().x(), event.position().y())
            self._refresh()
            return
        if self._interaction == "select_anchors" and self._selection_start is not None:
            self._selection_current = (event.position().x(), event.position().y())
            selection_rect = QRect(
                round(self._selection_start[0]), round(self._selection_start[1]),
                round(self._selection_current[0] - self._selection_start[0]),
                round(self._selection_current[1] - self._selection_start[1]),
            )
            self._selected_anchor_indices = self._selection_base | self._anchors_in_display_rect(selection_rect)
            self._selected_anchor_index = next(iter(self._selected_anchor_indices), None)
            self._refresh()
            return
        if self._interaction in {"move_custom_anchor", "move_custom_anchors"} and self._interaction_start is not None:
            point = self._clamp_page_point(*self._to_page_point(event.position().x(), event.position().y()))
            dx = point[0] - self._interaction_start[0]
            dy = point[1] - self._interaction_start[1]
            selected = self._selected_anchor_indices or {self._selected_anchor_index}
            self._move_custom_anchors(selected, dx, dy)
            self._interaction_start = point
            self._manual_drawn = True
            self._refresh()
            self.cropChanged.emit(self._custom_path_bounds())
            return
        if self._interaction == "move_custom_handle" and self._selected_handle is not None:
            point = self._clamp_page_point(*self._to_page_point(event.position().x(), event.position().y()))
            self._move_custom_handle(self._selected_handle, point)
            self._manual_drawn = True
            self._refresh()
            self.cropChanged.emit(self._custom_path_bounds())
            return
        if self._interaction == "custom" and self._custom_segment is not None:
            point = self._to_page_point(event.position().x(), event.position().y())
            anchor = self._custom_segment["end"]
            if self._custom_segment["first"]:
                self._custom_segment["handle"] = point
            else:
                dx = point[0] - anchor[0]
                dy = point[1] - anchor[1]
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    if abs(dx) >= abs(dy):
                        dy = 0.0
                    else:
                        dx = 0.0
                handle = (anchor[0] + dx, anchor[1] + dy)
                self._custom_segment["handle"] = handle
                self._custom_segment["control2"] = (anchor[0] - dx, anchor[1] - dy)
            self._refresh()
            self.cropChanged.emit(self._custom_path_bounds())
            return
        if self._interaction == "draw":
            if self._drag_start is None:
                return
            self._drag_current = self._to_page_point(event.position().x(), event.position().y())
            self._set_drag_crop()
            return
        if self._interaction is not None and self._interaction_start is not None:
            point = self._clamp_page_point(*self._to_page_point(event.position().x(), event.position().y()))
            self._apply_interaction(point)
            self._refresh()
            self.cropChanged.emit(self._crop)
            return
        hit = self._rect_hit_test(event.position().x(), event.position().y()) if self._crop is not None else None
        if self._stroke_shape == "custom":
            self._update_custom_cursor()
        else:
            self.setCursor(self._cursor_for_interaction(hit))

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_start is not None:
            self._pan_start = None
            return
        if self._interaction == "select_anchors":
            if self._selection_start is not None and self._selection_current is not None:
                rect = QRect(
                    round(self._selection_start[0]), round(self._selection_start[1]),
                    round(self._selection_current[0] - self._selection_start[0]),
                    round(self._selection_current[1] - self._selection_start[1]),
                )
                self._selected_anchor_indices = self._selection_base | self._anchors_in_display_rect(rect)
                self._selected_anchor_index = next(iter(self._selected_anchor_indices), None)
            self._selection_start = None
            self._selection_current = None
            self._interaction = None
            self._update_custom_cursor()
            self._refresh()
            return
        if self._interaction in {"move_custom_anchor", "move_custom_anchors", "move_custom_handle"}:
            self._interaction = None
            self._interaction_start = None
            self._commit_path_change(self._active_path_before)
            self._active_path_before = None
            self._update_custom_cursor()
            self._refresh()
            return
        if self._interaction == "custom" and self._custom_segment is not None:
            segment = self._custom_segment
            if segment["first"]:
                anchor = segment["end"]
                handle = segment["handle"]
                self._custom_out_handle = (
                    handle
                    if abs(handle[0] - anchor[0]) + abs(handle[1] - anchor[1]) >= 2
                    else None
                )
            else:
                end = segment["end"]
                handle = segment["handle"]
                dragged = abs(handle[0] - end[0]) + abs(handle[1] - end[1]) >= 2
                if not segment["force_line"] and (self._custom_out_handle is not None or dragged):
                    control1 = segment["control1"]
                    control2 = segment["control2"] if dragged else end
                    self._custom_path.append(("C", (
                        control1[0], control1[1], control2[0], control2[1], end[0], end[1]
                    )))
                else:
                    self._custom_path.append(("L", end))
                self._custom_out_handle = handle if dragged and not segment["force_line"] else None
            self._custom_segment = None
            self._interaction = None
            self._commit_path_change(self._active_path_before)
            self._active_path_before = None
            self._update_custom_cursor()
            self._refresh()
            self.cropChanged.emit(self._custom_path_bounds())
            return
        if self._interaction == "custom":
            self._interaction = None
            self._commit_path_change(self._active_path_before)
            self._active_path_before = None
            self._update_custom_cursor()
            return
        if self._interaction == "draw":
            if self._drag_start is not None:
                self._drag_current = self._to_page_point(event.position().x(), event.position().y())
                self._set_drag_crop()
            self._drag_start = None
            self._drag_current = None
            self._interaction = None
            self._interaction_start = None
            self._interaction_bounds = None
            self.unsetCursor()
            return
        if self._interaction is not None:
            self._interaction = None
            self._interaction_start = None
            self._interaction_bounds = None
            self._refresh()
            self.cropChanged.emit(self._crop)
            self.unsetCursor()

    def _set_drag_crop(self) -> None:
        if self._drag_start is None or self._drag_current is None:
            return
        x1, y1 = self._drag_start
        x2, y2 = self._drag_current
        self._crop = Bounds(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self._refresh()
        self.cropChanged.emit(self._crop)

    def crop(self) -> object:
        return self._custom_path_bounds() if self._stroke_shape == "custom" and self._custom_path else self._crop

    def custom_path(self) -> tuple:
        return tuple(self._custom_path)

class ArtworkCropDialog(QDialog):
    """Large single-artwork crop editor."""

    def __init__(self, inspection: ArtworkInspection, parent: QWidget | None = None, initial_bounds: object | None = None, saved_selection: CutSelection | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowTitle(f"Manual Cut - {inspection.input_path.name}")
        self.resize(980, 700)
        self.setMinimumSize(780, 560)
        self._inspection = inspection
        self._mode = "manual"
        self._selected_source: CutSelection | None = None
        self._manual_hide_selection: CutSelection | None = None
        self._selection_actions: dict[tuple[str, str], str] = {}
        self._action_combos: dict[tuple[str, str], QComboBox] = {}
        self._action_options: dict[tuple[str, str], CutOption] = {}
        self._initial_bounds = initial_bounds
        self._saved_selection = saved_selection
        self._hover_option: CutOption | None = None
        self._manual_crop_active = False
        self._stroke_shape = "rectangle"
        self._corners = "sharp"
        self._corner_amount = 0.0
        self._stroke_weight = 0.5
        self._stroke_color = "#B8F35A"
        self._preserved_hide_items: tuple[tuple[str, str, tuple[int, int, int] | None, int | None], ...] = ()
        self._build_ui()

    def _add_action_row(self, layout: QVBoxLayout, option: CutOption, mode: str) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(option.label)
        label.setObjectName("cropOptionCheck")
        label.installEventFilter(self)
        label.setProperty("crop_option", option)
        row.addWidget(label)
        row.addStretch(1)
        combo = QComboBox()
        combo.setObjectName("cropOptionCheck")
        combo.addItems(["show", "hide", "trim", "trim & hide"])
        combo.setCurrentText("show")
        combo.setFixedWidth(115)
        self._set_action_combo_color(combo, "show")
        row.addWidget(combo)
        key = (mode, option.label)
        self._selection_actions[key] = "show"
        self._action_combos[key] = combo
        self._action_options[key] = option

        def handle_change(action: str) -> None:
            self._selection_actions[key] = action
            self._set_action_combo_color(combo, action)
            if action == "show":
                self.canvas.set_crop_style("#B8F35A")
                self._selected_source = None
                self._manual_hide_selection = None
                self._refresh_artwork_preview()
                return
            if action in {"trim", "trim & hide"}:
                self._manual_crop_active = False
                self.canvas.set_crop_style("#00FF66")
                self.canvas._crop = option.bounds
                self._selected_source = CutSelection(option.bounds, mode, option.label, option.color_rgb, option.xref)
                self._manual_hide_selection = None if action == "trim" else CutSelection(option.bounds, mode, option.label, option.color_rgb, option.xref)
                for other_key, other_action in list(self._selection_actions.items()):
                    if other_key != key and other_action in {"trim", "trim & hide"}:
                        self._selection_actions[other_key] = "show"
                        other_combo = self._action_combos.get(other_key)
                        if other_combo is not None:
                            other_combo.blockSignals(True)
                            other_combo.setCurrentText("show")
                            other_combo.blockSignals(False)
                            self._set_action_combo_color(other_combo, "show")
            elif action == "hide":
                self.canvas.set_crop_style("#00FF66")
                self._selected_source = None
                self._manual_hide_selection = CutSelection(self.canvas.crop(), mode, option.label, option.color_rgb, option.xref)
            self._refresh_artwork_preview()

        combo.currentTextChanged.connect(handle_change)
        layout.addLayout(row)

    @staticmethod
    def _set_action_combo_color(combo: QComboBox, action: str) -> None:
        colors = {
            "show": "#7CD5C7",
            "hide": "#D3D3D3",
            "trim": "#BD5579",
            "trim & hide": "#BD5579",
        }
        combo.setStyleSheet(
            f"QComboBox {{ background-color: {colors.get(action, '#FFFFFF')}; padding: 3px 8px; }}"
        )

    @staticmethod
    def _edit_tool_icon(tool: str, color: str) -> QIcon:
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if tool == "pen":
            path = QPainterPath()
            path.moveTo(4, 19)
            path.lineTo(6, 14)
            path.lineTo(16, 4)
            path.lineTo(20, 8)
            path.lineTo(10, 18)
            path.closeSubpath()
            painter.setBrush(QColor(color))
            painter.drawPath(path)
            painter.drawLine(6, 14, 10, 18)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(color), 1.8, Qt.PenStyle.DashLine))
            painter.drawRect(4, 4, 16, 16)
            painter.setPen(QPen(QColor(color), 2))
            painter.setBrush(QColor(color))
            for x, y in ((4, 4), (20, 4), (4, 20), (20, 20)):
                painter.drawRect(x - 1, y - 1, 3, 3)
        painter.end()
        return QIcon(pixmap)

    def _refresh_edit_tool_buttons(self) -> None:
        for tool, button in self._edit_tool_buttons.items():
            active = tool == self._active_edit_tool
            color = "#D02752" if active else "#111F35"
            button.setIcon(self._edit_tool_icon(tool, color))
            button.setStyleSheet(
                "QToolButton { background: #F5F7FA; border: 1px solid #C8CDD6; border-radius: 5px; }"
                f"QToolButton:hover {{ background: #E9EDF2; border: 1px solid {color}; }}"
                f"QToolButton:checked {{ background: #FCECEF; border: 2px solid #D02752; }}"
            )

    def _set_edit_tool(self, tool: str) -> None:
        self._active_edit_tool = tool
        if hasattr(self, "canvas"):
            self.canvas.set_edit_tool(tool)
        self._refresh_edit_tool_buttons()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        title = QLabel(self._inspection.input_path.name)
        title.setObjectName("manualCutTitle")
        layout.addWidget(title)
        hint = QLabel("Select a crop method on the left. Use the mouse wheel to zoom and middle-drag to pan.")
        hint.setObjectName("manualCutDescription")
        layout.addWidget(hint)

        initial = self._initial_bounds if self._initial_bounds is not None else self._inspection.detected_bounds
        body = QHBoxLayout()
        crop_panel = QFrame()
        crop_panel.setObjectName("cropControlPanel")
        crop_panel.setFixedWidth(350)
        crop_layout = QVBoxLayout(crop_panel)
        crop_title = QLabel("Local Crop Setup")
        crop_title.setObjectName("manualCutSectionLabel")
        crop_layout.addWidget(crop_title)
        self._hover_checkbox = QCheckBox("show crop border")
        self._hover_checkbox.setObjectName("cropOptionCheck")
        self._hover_checkbox.setStyleSheet(
            "QCheckBox#cropOptionCheck::indicator:checked "
            "{ background-color: #D02752; border: 1px solid #D02752; }"
        )
        self._hover_checkbox.setChecked(True)
        crop_layout.addWidget(self._hover_checkbox)
        tabs = QTabWidget()
        tabs.setObjectName("cropTabs")
        layers_tab = QWidget(); layers_layout = QVBoxLayout(layers_tab)
        layer_options = [option for option in self._inspection.options if option.kind == "layer"]
        if not layer_options: layers_layout.addWidget(QLabel("No PDF layers found."))
        for option in layer_options: self._add_action_row(layers_layout, option, "layer")
        layers_layout.addStretch(1)
        layers_scroll = QScrollArea()
        layers_scroll.setWidgetResizable(True)
        layers_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layers_scroll.setWidget(layers_tab)
        separations_tab = QWidget(); separations_layout = QVBoxLayout(separations_tab)
        separation_options = [option for option in self._inspection.options if option.kind == "spot_color"]
        if not separation_options: separations_layout.addWidget(QLabel("No vector separations found."))
        for option in separation_options: self._add_action_row(separations_layout, option, "spot_color")
        separations_layout.addStretch(1)
        separations_scroll = QScrollArea()
        separations_scroll.setWidgetResizable(True)
        separations_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        separations_scroll.setWidget(separations_tab)
        tabs.addTab(layers_scroll, "By Layer")
        tabs.addTab(separations_scroll, "By Spot Color")
        shape_tab = QWidget()
        shape_layout = QVBoxLayout(shape_tab)
        shape_layout.setSpacing(8)
        stroke_shape_row = QHBoxLayout()
        stroke_shape_row.setSpacing(8)
        stroke_shape_label = QLabel("stroke shape")
        stroke_shape_label.setObjectName("cropOptionCheck")
        stroke_shape_row.addWidget(stroke_shape_label)
        stroke_shape_row.addStretch(1)
        self._stroke_shape_combo = QComboBox()
        self._stroke_shape_combo.addItems(["rectangle", "ellipse", "custom"])
        self._stroke_shape_combo.setCurrentText("rectangle")
        self._stroke_shape_combo.setObjectName("cropOptionCheck")
        self._stroke_shape_combo.setFixedWidth(115)
        self._set_action_combo_color(self._stroke_shape_combo, "hide")
        self._stroke_shape_combo.currentTextChanged.connect(self._shape_settings_changed)
        stroke_shape_row.addWidget(self._stroke_shape_combo)
        shape_layout.addLayout(stroke_shape_row)
        stroke_weight_row = QHBoxLayout()
        stroke_weight_row.setSpacing(8)
        stroke_weight_label = QLabel("stroke weight")
        stroke_weight_label.setObjectName("cropOptionCheck")
        stroke_weight_row.addWidget(stroke_weight_label)
        stroke_weight_row.addStretch(1)
        self._stroke_weight_value = QLabel("0.50")
        self._stroke_weight_value.setObjectName("cropOptionCheck")
        stroke_weight_row.addWidget(self._stroke_weight_value)
        shape_layout.addLayout(stroke_weight_row)
        self._stroke_weight_slider = QSlider(Qt.Orientation.Horizontal)
        self._stroke_weight_slider.setRange(1, 10)
        self._stroke_weight_slider.setValue(5)
        self._stroke_weight_slider.valueChanged.connect(self._shape_settings_changed)
        shape_layout.addWidget(self._stroke_weight_slider)
        stroke_color_row = QHBoxLayout()
        stroke_color_row.setSpacing(8)
        stroke_color_label = QLabel("stroke color")
        stroke_color_label.setObjectName("cropOptionCheck")
        stroke_color_row.addWidget(stroke_color_label)
        stroke_color_row.addStretch(1)
        self._stroke_color_button = QPushButton()
        self._stroke_color_button.setFixedSize(115, 28)
        self._stroke_color_button.setToolTip("Choose stroke color")
        self._stroke_color_button.clicked.connect(self._choose_stroke_color)
        stroke_color_row.addWidget(self._stroke_color_button)
        shape_layout.addLayout(stroke_color_row)
        self._set_stroke_color_button()
        corners_row = QHBoxLayout()
        corners_row.setSpacing(8)
        self._corners_label = QLabel("corners")
        self._corners_label.setObjectName("cropOptionCheck")
        corners_row.addWidget(self._corners_label)
        corners_row.addStretch(1)
        self._corners_combo = QComboBox()
        self._corners_combo.addItems(["sharp", "rounded", "beveled"])
        self._corners_combo.setCurrentText("sharp")
        self._corners_combo.setObjectName("cropOptionCheck")
        self._corners_combo.setFixedWidth(115)
        self._set_action_combo_color(self._corners_combo, "hide")
        self._corners_combo.currentTextChanged.connect(self._shape_settings_changed)
        corners_row.addWidget(self._corners_combo)
        shape_layout.addLayout(corners_row)
        self._corner_slider_label = QLabel("0.00")
        shape_layout.addWidget(self._corner_slider_label)
        self._corner_slider = QSlider(Qt.Orientation.Horizontal)
        self._corner_slider.setRange(0, 100)
        self._corner_slider.setValue(0)
        self._corner_slider.valueChanged.connect(self._shape_settings_changed)
        shape_layout.addWidget(self._corner_slider)
        shape_layout.addStretch(1)
        tabs.addTab(shape_tab, "Shape Settings")
        self._update_corner_controls()
        crop_layout.addWidget(tabs, 1)
        body.addWidget(crop_panel)
        self._active_edit_tool = "pen"
        self._edit_tool_buttons: dict[str, QToolButton] = {}
        tool_panel = QFrame()
        tool_panel.setObjectName("cropToolPanel")
        tool_panel.setFixedWidth(46)
        tool_layout = QVBoxLayout(tool_panel)
        tool_layout.setContentsMargins(4, 8, 4, 4)
        tool_layout.setSpacing(7)
        tool_group = QButtonGroup(self)
        tool_group.setExclusive(True)
        for tool, tooltip in (("pen", "Pen tool: draw crop path"), ("select", "Anchor select: drag to select and move anchors")):
            button = QToolButton()
            button.setCheckable(True)
            button.setFixedSize(36, 36)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(24, 24))
            button.setToolTip(tooltip)
            tool_group.addButton(button)
            self._edit_tool_buttons[tool] = button
            button.clicked.connect(lambda checked=False, name=tool: self._set_edit_tool(name))
            tool_layout.addWidget(button)
        tool_layout.addStretch(1)
        self._edit_tool_buttons["pen"].setChecked(True)
        self._refresh_edit_tool_buttons()
        tool_panel.setVisible(self._stroke_shape == "custom")
        body.addWidget(tool_panel, 0, Qt.AlignmentFlag.AlignTop)
        self.canvas = ArtworkCropCanvas(self._inspection_image(), self._inspection.page_bounds, initial)
        self._hover_checkbox.toggled.connect(self.canvas.set_crop_border_visible)
        self.canvas.set_edit_tool(self._active_edit_tool)
        self.canvas.cropChanged.connect(self._on_canvas_crop_changed)
        self._undo_custom_path_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self._undo_custom_path_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._undo_custom_path_shortcut.activated.connect(self.canvas.undo_custom_anchor)
        canvas_scroll = QScrollArea()
        canvas_scroll.setWidgetResizable(True)
        canvas_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        canvas_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        canvas_scroll.setWidget(self.canvas)
        body.addWidget(canvas_scroll, 1)
        layout.addLayout(body, 1)
        self._restore_selection_state()

        zoom_row = QHBoxLayout(); zoom_label = QLabel("Zoom"); zoom_label.setObjectName("manualCutDescription"); zoom_row.addWidget(zoom_label)
        zoom_slider = QSlider(Qt.Orientation.Horizontal); zoom_slider.setObjectName("manualCutZoomSlider"); zoom_slider.setRange(25, 300); zoom_slider.setValue(100); zoom_slider.setTickInterval(25); zoom_slider.valueChanged.connect(self.canvas.set_zoom); zoom_row.addWidget(zoom_slider, 1)
        self.zoom_value = QLabel("100%"); self.zoom_value.setObjectName("manualCutDescription"); zoom_slider.valueChanged.connect(lambda value: self.zoom_value.setText(f"{value}%")); zoom_row.addWidget(self.zoom_value)
        layout.addLayout(zoom_row)

        footer = QHBoxLayout(); footer.addStretch(1)
        accept = QPushButton("Use this crop"); accept.setObjectName("collectorRunBtn"); accept.setFixedHeight(50); accept.setMinimumWidth(180); accept.clicked.connect(self.accept); footer.addWidget(accept); layout.addLayout(footer)

    def _on_canvas_crop_changed(self, bounds: object) -> None:
        if getattr(self.canvas, "_manual_drawn", False):
            preserved = list(getattr(self._saved_selection, "hide_items", ()) or ())
            for key, option in self._action_options.items():
                action = self._selection_actions.get(key)
                if action in {"hide", "trim & hide"}:
                    item = (option.kind, option.label, option.color_rgb, option.xref)
                    if item not in preserved:
                        preserved.append(item)
                if action in {"trim", "trim & hide"}:
                    self._selection_actions[key] = "show"
                    combo = self._action_combos.get(key)
                    if combo is not None:
                        combo.blockSignals(True)
                        combo.setCurrentText("show")
                        combo.blockSignals(False)
                        self._set_action_combo_color(combo, "show")
            self._preserved_hide_items = tuple(preserved)
            self._manual_crop_active = True
            self._selected_source = None
            self._manual_hide_selection = None
            self.canvas.set_crop_style("#00FF66")
        self.canvas._manual_drawn = False

    def _restore_selection_state(self) -> None:
        if self._saved_selection is None:
            return
        selection = self._saved_selection
        self._stroke_shape = selection.stroke_shape
        self._corners = selection.corners
        self._corner_amount = selection.corner_amount
        self._stroke_weight = selection.stroke_weight
        self._stroke_color = selection.stroke_color
        self._stroke_shape_combo.setCurrentText(self._stroke_shape)
        self._corners_combo.setCurrentText(self._corners)
        self._corner_slider.setValue(round(self._corner_amount * 100))
        self._stroke_weight_slider.setValue(round(self._stroke_weight * 10))
        self._set_stroke_color_button()
        self._shape_settings_changed()
        self.canvas.set_custom_path(selection.custom_path)
        if selection.manual_crop:
            self._manual_crop_active = True
            self.canvas._crop = selection.bounds
            self._refresh_artwork_preview()
            self.canvas._refresh()
            return
        source_key = None
        if selection.kind in {"layer", "spot_color"} and selection.name:
            source_key = (selection.kind, selection.name)
            action = "trim & hide" if any(item[:2] == source_key for item in selection.hide_items) else "trim"
            self._selection_actions[source_key] = action
            combo = self._action_combos.get(source_key)
            if combo is not None:
                combo.blockSignals(True)
                combo.setCurrentText(action)
                combo.blockSignals(False)
                self._set_action_combo_color(combo, action)
        for kind, name, _, _ in selection.hide_items:
            key = (kind, name)
            if key == source_key:
                continue
            self._selection_actions[key] = "hide"
            combo = self._action_combos.get(key)
            if combo is not None:
                combo.blockSignals(True)
                combo.setCurrentText("hide")
                combo.blockSignals(False)
                self._set_action_combo_color(combo, "hide")
        self.canvas._crop = selection.bounds
        self._refresh_artwork_preview()
        self.canvas._refresh()

    def _update_corner_controls(self) -> None:
        visible = self._stroke_shape_combo.currentText() == "rectangle" and self._corners_combo.currentText() in {"rounded", "beveled"}
        self._corners_label.setVisible(self._stroke_shape_combo.currentText() == "rectangle")
        self._corners_combo.setVisible(self._stroke_shape_combo.currentText() == "rectangle")
        self._corner_slider_label.setVisible(visible)
        self._corner_slider.setVisible(visible)

    def _shape_settings_changed(self, _value: object = None) -> None:
        self._stroke_shape = self._stroke_shape_combo.currentText()
        self._corners = self._corners_combo.currentText()
        self._corner_amount = self._corner_slider.value() / 100.0
        self._stroke_weight = self._stroke_weight_slider.value() / 10.0
        self._corner_slider_label.setText(f"{self._corner_amount:.2f}")
        self._stroke_weight_value.setText(f"{self._stroke_weight:.2f}")
        self._update_corner_controls()
        self._edit_tool_buttons["pen"].parentWidget().setVisible(self._stroke_shape == "custom")
        self.canvas.set_shape_settings(self._stroke_shape, self._corners, self._corner_amount)
        self.canvas.set_stroke_style(self._stroke_weight, self._stroke_color)

    def _set_stroke_color_button(self) -> None:
        self._stroke_color_button.setStyleSheet(
            f"QPushButton {{ background-color: {self._stroke_color}; "
            "border: 1px solid #A8A8A8; border-radius: 4px; }}"
            "QPushButton:hover { border: 2px solid #555555; }"
        )

    def _choose_stroke_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._stroke_color), self, "Choose stroke color")
        if not color.isValid():
            return
        self._stroke_color = color.name().upper()
        self._set_stroke_color_button()
        self.canvas.set_stroke_style(self._stroke_weight, self._stroke_color)

    def _set_source_crop(self, option: object, checked: bool, mode: str) -> None:
        if not checked:
            return
        self._mode = mode
        self.canvas._crop = option.bounds
        self._selected_source = CutSelection(
            option.bounds,
            "layer" if mode == "layer" else "spot_color",
            option.label,
            option.color_rgb,
            option.xref,
        )
        self.canvas.set_crop_style("#00FF66")
        self.canvas._refresh()

    def _set_manual_hide(self, option: object, checked: bool, mode: str) -> None:
        if not checked:
            return
        self._manual_hide_selection = CutSelection(
            self.canvas.crop(), mode, option.label, option.color_rgb, option.xref,
        )
        self.canvas.set_crop_style("#00FF66")
        self.canvas._refresh()

    def _tab_changed(self, index: int) -> None:
        self._mode = "manual" if index == 2 else "source"
        if index == 2:
            self.canvas.set_crop_style("#00FF66")
            self._selected_source = None
        else:
            self.canvas.set_crop_style("#00FF66")

    def eventFilter(self, obj, event) -> bool:
        if isinstance(obj, QLabel) and obj.property("crop_option") is not None:
            option = obj.property("crop_option")
            if event.type() == QEvent.Type.Enter:
                self._hover_option = option
                self.canvas.set_hover_bounds(option.bounds)
            elif event.type() == QEvent.Type.Leave:
                self._hover_option = None
                self.canvas.set_hover_bounds(None)
        return super().eventFilter(obj, event)

    def _inspection_image(self) -> QImage:
        raw = render_artwork_preview(self._inspection.input_path, dpi=300)
        return _artwork_preview_qimage(raw)

    def _refresh_artwork_preview(self) -> None:
        hide_items = list(self._preserved_hide_items)
        hide_items.extend(
            (option.kind, option.label, option.color_rgb, option.xref)
            for key, option in self._action_options.items()
            if self._selection_actions.get(key) in {"hide", "trim & hide"}
        )
        hide_items = tuple(dict.fromkeys(hide_items))
        try:
            raw = render_artwork_preview(
                self._inspection.input_path,
                dpi=300,
                hide_items=hide_items,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Preview update failed", str(exc))
            return
        self.canvas.set_artwork_image(_artwork_preview_qimage(raw))

    def _set_bounds(self, bounds: object) -> None:
        self.canvas._crop = bounds
        self.canvas._refresh()

    def _spin_changed(self) -> None:
        return

    def crop_bounds(self) -> object:
        return self.canvas.crop()

    def crop_selection(self) -> CutSelection:
        shape_settings = {
            "stroke_shape": self._stroke_shape,
            "corners": self._corners,
            "corner_amount": self._corner_amount,
            "custom_path": self.canvas.custom_path(),
            "stroke_weight": self._stroke_weight,
            "stroke_color": self._stroke_color,
        }
        hide_items = tuple(
            (option.kind, option.label, option.color_rgb, option.xref)
            for key, option in self._action_options.items()
            if self._selection_actions.get(key) in {"hide", "trim & hide"}
        )
        hide_items = tuple(dict.fromkeys(hide_items + self._preserved_hide_items))
        if self._manual_crop_active:
            return CutSelection(
                self.canvas.crop(),
                "manual",
                hide_items=hide_items,
                manual_crop=True,
                **shape_settings,
            )
        trim_option = next(
            (self._action_options[key] for key, action in self._selection_actions.items()
             if action in {"trim", "trim & hide"}),
            None,
        )
        if trim_option is not None:
            return CutSelection(
                trim_option.bounds,
                trim_option.kind,
                trim_option.label,
                trim_option.color_rgb,
                trim_option.xref,
                hide_items=hide_items,
                **shape_settings,
            )
        if hide_items:
            return CutSelection(self.canvas.crop(), "manual", hide_items=hide_items, **shape_settings)
        if self._manual_hide_selection is not None:
            hide = self._manual_hide_selection
            return CutSelection(
                self.canvas.crop(), "manual", "", None, None,
                hide.kind, hide.name, hide.color_rgb, hide.xref,
                hide_items=((hide.kind, hide.name, hide.color_rgb, hide.xref),),
                **shape_settings,
            )
        if self._selected_source is not None:
            return CutSelection(
                self.canvas.crop(),
                self._selected_source.kind,
                self._selected_source.name,
                self._selected_source.color_rgb,
                self._selected_source.xref,
                hide_items=(),
                **shape_settings,
            )
        return CutSelection(self._inspection.page_bounds, "manual", **shape_settings)

class ArtworkManualCutDialog(QDialog):
    """Manual cut dialog with individual and bulk review modes."""

    def __init__(self, inspections: list[ArtworkInspection], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowTitle("Manual Cut - Artwork Preview")
        self.resize(1120, 760)
        self.setMinimumSize(860, 560)
        self._inspections = inspections
        self._selections: dict[Path, object | None] = {}
        self._preview_labels: dict[Path, QLabel] = {}
        self._copied_crop: CutSelection | None = None
        self._selected_paths: set[Path] = set()
        self._selection_anchor: Path | None = None
        self._bulk_mode = True
        self._bulk_actions: dict[tuple[str, str], str] = {}
        self._bulk_combos: dict[tuple[str, str], QComboBox] = {}
        self._bulk_rows: dict[tuple[str, str], dict[str, object]] = {}
        self._card_widgets: dict[Path, QFrame] = {}
        self._show_crop_border = True
        self._global_hover_option: tuple[str, str] | None = None
        self.output_folder: Path | None = None
        self._build_ui()

    @staticmethod
    def _set_action_combo_color(combo: QComboBox, action: str) -> None:
        colors = {
            "show": "#7CD5C7",
            "hide": "#D3D3D3",
            "trim": "#BD5579",
            "trim & hide": "#BD5579",
        }
        combo.setStyleSheet(
            f"QComboBox {{ background-color: {colors.get(action, '#FFFFFF')}; }}"
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        heading = QLabel("Manual Cut")
        heading.setObjectName("manualCutTitle")
        layout.addWidget(heading)

        self._bulk_panel = QWidget()
        self._bulk_panel.setVisible(True)
        self._bulk_panel.setFixedWidth(350)
        self._bulk_panel.setObjectName("cropControlPanel")
        self._bulk_layout = QVBoxLayout(self._bulk_panel)
        self._bulk_layout.setContentsMargins(12, 12, 12, 12)
        crop_title = QLabel("Global Crop Setup")
        crop_title.setObjectName("manualCutSectionLabel")
        self._bulk_layout.addWidget(crop_title)
        self._global_hover_checkbox = QCheckBox("show crop border")
        self._global_hover_checkbox.setObjectName("cropOptionCheck")
        self._global_hover_checkbox.setStyleSheet(
            "QCheckBox#cropOptionCheck::indicator:checked "
            "{ background-color: #D02752; border: 1px solid #D02752; }"
        )
        self._global_hover_checkbox.setChecked(True)
        self._global_hover_checkbox.toggled.connect(self._set_crop_border_visible)
        self._bulk_layout.addWidget(self._global_hover_checkbox)
        self._bulk_tabs = QTabWidget()
        self._bulk_tabs.setObjectName("cropTabs")
        self._bulk_tabs.addTab(self._bulk_layer_tab(), "By Layer")
        self._bulk_tabs.addTab(self._bulk_spot_tab(), "By Spot Color")
        self._bulk_layout.addWidget(self._bulk_tabs, 1)

        intro = QLabel("Click an artwork thumbnail to open its manual crop editor.")
        intro.setObjectName("manualCutDescription")
        layout.addWidget(intro)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setObjectName("manualCutScroll")
        self._scroll.viewport().installEventFilter(self)
        self._cards = QWidget()
        self._cards_layout = QGridLayout(self._cards)
        self._cards_layout.setContentsMargins(2, 2, 2, 2)
        self._cards_layout.setSpacing(14)
        for index, inspection in enumerate(self._inspections):
            self._cards_layout.addWidget(self._build_card(inspection), index // 3, index % 3)
        for column in range(3):
            self._cards_layout.setColumnStretch(column, 1)
        self._scroll.setWidget(self._cards)
        content_row = QHBoxLayout()
        content_row.setSpacing(12)
        content_row.addWidget(self._bulk_panel, 0, Qt.AlignmentFlag.AlignTop)
        content_row.addWidget(self._scroll, 1)
        layout.addLayout(content_row, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        apply_button = QPushButton("Use selected cuts")
        apply_button.setObjectName("collectorRunBtn")
        apply_button.setFixedHeight(50)
        apply_button.setMinimumWidth(180)
        apply_button.clicked.connect(self._choose_output_folder_and_accept)
        footer.addWidget(apply_button)
        layout.addLayout(footer)

    def _choose_output_folder_and_accept(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose folder to save artwork files")
        if not folder:
            return
        self.output_folder = Path(folder)
        self.accept()

    def _build_bulk_catalog(self, kind: str) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for inspection in self._inspections:
            for option in inspection.options:
                if option.kind != kind:
                    continue
                label = option.label.strip()
                if label and label not in seen:
                    seen.add(label)
                    names.append(label)
        return names

    def _bulk_layer_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        names = self._build_bulk_catalog("layer")
        if not names:
            layout.addWidget(QLabel("No PDF layers found."))
            return tab
        for name in names:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(name)
            label.setObjectName("cropOptionCheck")
            label.installEventFilter(self)
            label.setProperty("global_crop_option", ("layer", name))
            row.addWidget(label)
            row.addStretch(1)
            combo = QComboBox()
            combo.setObjectName("cropOptionCheck")
            combo.addItems(["show", "hide", "trim", "trim & hide"])
            combo.setCurrentText("show")
            combo.setFixedWidth(115)
            self._set_action_combo_color(combo, "show")
            row.addWidget(combo)
            key = ("layer", name)
            self._bulk_actions[key] = "show"
            self._bulk_combos[key] = combo
            combo.currentTextChanged.connect(
                lambda action, target=name: self._on_bulk_combo_change("layer", target, action)
            )
            layout.addLayout(row)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(tab)
        return scroll

    def _bulk_spot_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        names = self._build_bulk_catalog("spot_color")
        if not names:
            layout.addWidget(QLabel("No vector separations found."))
            return tab
        for name in names:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(name)
            label.setObjectName("cropOptionCheck")
            label.installEventFilter(self)
            label.setProperty("global_crop_option", ("spot_color", name))
            row.addWidget(label)
            row.addStretch(1)
            combo = QComboBox()
            combo.setObjectName("cropOptionCheck")
            combo.addItems(["show", "hide", "trim", "trim & hide"])
            combo.setCurrentText("show")
            combo.setFixedWidth(115)
            self._set_action_combo_color(combo, "show")
            row.addWidget(combo)
            key = ("spot_color", name)
            self._bulk_actions[key] = "show"
            self._bulk_combos[key] = combo
            combo.currentTextChanged.connect(
                lambda action, target=name: self._on_bulk_combo_change("spot_color", target, action)
            )
            layout.addLayout(row)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(tab)
        return scroll

    def _on_bulk_combo_change(self, kind: str, name: str, action: str) -> None:
        key = (kind, name)
        self._bulk_actions[key] = action
        combo = self._bulk_combos.get(key)
        if combo is not None:
            self._set_action_combo_color(combo, action)
        if action in {"trim", "trim & hide"}:
            for other_key, other_action in list(self._bulk_actions.items()):
                if other_key != key and other_action in {"trim", "trim & hide"}:
                    self._bulk_actions[other_key] = "show"
                    other_combo = self._bulk_combos.get(other_key)
                    if other_combo is not None:
                        other_combo.blockSignals(True)
                        other_combo.setCurrentText("show")
                        other_combo.blockSignals(False)
                        self._set_action_combo_color(other_combo, "show")
        self._refresh_bulk_previews()

    def _set_crop_border_visible(self, visible: bool) -> None:
        self._show_crop_border = visible
        self._refresh_bulk_previews()

    def _set_global_hover_option(self, option: tuple[str, str] | None) -> None:
        self._global_hover_option = option
        self._refresh_bulk_previews()

    def _global_hide_items_for(
        self,
        inspection: ArtworkInspection,
    ) -> tuple[tuple[str, str, tuple[int, int, int] | None, int | None], ...]:
        return tuple(
            (option.kind, option.label, option.color_rgb, option.xref)
            for option in inspection.options
            if self._bulk_actions.get((option.kind, option.label)) in {"hide", "trim & hide"}
        )

    def _refresh_bulk_previews(self) -> None:
        trim_choice = next(
            ((kind, name) for (kind, name), action in self._bulk_actions.items()
             if action in {"trim", "trim & hide"}),
            None,
        )
        for inspection in self._inspections:
            local_selection = self._selections.get(inspection.input_path)
            if local_selection is not None:
                local_bounds = local_selection.bounds
                hide_items = tuple(dict.fromkeys(
                    (*local_selection.hide_items, *self._global_hide_items_for(inspection))
                ))
                self._update_preview(
                    inspection,
                    local_bounds,
                    selection=local_selection,
                    hide_items=hide_items,
                )
                continue
            bounds = None
            hover_bounds = None
            hide_items = self._global_hide_items_for(inspection)
            if trim_choice is not None:
                trim_kind, trim_name = trim_choice
                option = next(
                    (candidate for candidate in inspection.options
                     if candidate.kind == trim_kind and candidate.label == trim_name),
                    None,
                )
                if option is not None:
                    bounds = option.bounds
            if self._global_hover_option is not None:
                hover_kind, hover_name = self._global_hover_option
                hover_option = next(
                    (candidate for candidate in inspection.options
                     if candidate.kind == hover_kind and candidate.label == hover_name),
                    None,
                )
                if hover_option is not None:
                    hover_bounds = hover_option.bounds
            self._update_preview(inspection, bounds, hover_bounds, hide_items=hide_items)

    def _build_card(self, inspection: ArtworkInspection) -> QFrame:
        card = QFrame()
        card.setObjectName("manualCutCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)

        preview = QLabel()
        preview.setObjectName("manualCutPreview")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setFixedSize(300, 210)
        preview.setCursor(Qt.CursorShape.PointingHandCursor)
        preview.installEventFilter(self)
        self._preview_labels[inspection.input_path] = preview
        self._card_widgets[inspection.input_path] = card
        reset_btn = QPushButton()
        reset_btn.setObjectName("manualCutResetBtn")
        reset_btn.setToolTip("Reset crop")
        reset_btn.setFixedSize(34, 30)
        reset_btn.setStyleSheet(
            "QPushButton#manualCutResetBtn { background: #F2F4F8; color: #444; border: 1px solid #C8CDD6; border-radius: 8px; font-size: 18px; font-weight: 700; }"
            "QPushButton#manualCutResetBtn:hover { background: #E9EDF2; }"
        )
        reset_btn.setText("↺")
        reset_btn.clicked.connect(lambda checked=False, path=inspection.input_path: self._clear_saved_selection(path))

        menu_btn = QToolButton()
        menu_btn.setObjectName("manualCutMenuBtn")
        menu_btn.setToolTip("Crop actions")
        menu_btn.setFixedSize(34, 30)
        menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_btn.setArrowType(Qt.ArrowType.NoArrow)
        menu_btn.setStyleSheet(
            "QToolButton#manualCutMenuBtn { background: #F2F4F8; color: #2D2D2D; border: 1px solid #C8CDD6; border-radius: 9px; font-size: 20px; font-weight: 800; padding: 0; }"
            "QToolButton#manualCutMenuBtn:hover { background: #E9EDF2; border-color: #B9C1CC; }"
            "QToolButton#manualCutMenuBtn:pressed { background: #E2E8F0; }"
            "QToolButton#manualCutMenuBtn::menu-indicator { image: none; }"
        )
        menu_btn.setText("⋯")
        menu = QMenu(menu_btn)
        menu.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet(
            "QMenu { background: rgba(255,255,255,0.97); border: 1px solid #D9DEE5; border-radius: 10px; padding: 6px; }"
            "QMenu::item { padding: 8px 18px; color: #1F2937; font-size: 12px; border-radius: 6px; }"
            "QMenu::item:selected { background: #EEF5FF; color: #111827; }"
        )
        copy_action = menu.addAction("Copy")
        paste_action = menu.addAction("Paste")
        menu_btn.setMenu(menu)
        menu.triggered.connect(lambda action, path=inspection.input_path: self._handle_card_menu_action(path, action, copy_action, paste_action))

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addStretch(1)
        top_row.addWidget(reset_btn)
        top_row.addWidget(menu_btn)
        card_layout.addLayout(top_row)
        card_layout.addWidget(preview, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self._selections[inspection.input_path] = None
        self._update_preview(inspection, None)
        self._refresh_saved_tag(inspection.input_path)
        return card

    def _handle_card_menu_action(self, path: Path, action, copy_action, paste_action) -> None:
        if action == copy_action:
            self._copied_crop = self._selections.get(path)
            return
        if action == paste_action:
            source = self._copied_crop
            if source is None:
                return
            for inspection in self._inspections:
                if inspection.input_path == path:
                    self._selections[path] = source
                    self._refresh_saved_tag(path)
                    preview_bounds = source.bounds if source.kind in {"layer", "spot_color"} or source.manual_crop else None
                    self._update_preview(
                        inspection,
                        preview_bounds,
                        selection=source,
                        hide_items=source.hide_items,
                    )
                    break

    def _clear_saved_selection(self, path: Path) -> None:
        self._selections[path] = None
        self._refresh_saved_tag(path)
        self._refresh_bulk_previews()

    def _refresh_saved_tag(self, path: Path) -> None:
        selection = self._selections.get(path)
        preview = self._preview_labels.get(path)
        has_saved = selection is not None
        if preview is not None:
            if path in self._selected_paths:
                preview.setStyleSheet(
                    "QLabel#manualCutPreview { background-color: rgba(124, 213, 199, 0.2); border: 2px solid #7CD5C7; border-radius: 8px; }"
                )
            else:
                preview.setStyleSheet(
                    "QLabel#manualCutPreview { border: 2px solid #D02752; border-radius: 8px; }"
                    if has_saved else ""
                )

    def _apply_selection(self, selected: set[Path]) -> None:
        self._selected_paths = selected
        self._selection_anchor = next(iter(selected), None) if selected else None
        for path, preview in self._preview_labels.items():
            if path in selected:
                preview.setStyleSheet("QLabel#manualCutPreview { background-color: rgba(124, 213, 199, 0.2); border: 2px solid #7CD5C7; border-radius: 8px; }")
            elif self._selections.get(path) is not None:
                preview.setStyleSheet("QLabel#manualCutPreview { border: 2px solid #D02752; border-radius: 8px; }")
            else:
                preview.setStyleSheet("")

    def eventFilter(self, obj, event) -> bool:
        if isinstance(obj, QLabel) and obj.property("global_crop_option") is not None:
            option = obj.property("global_crop_option")
            if event.type() == QEvent.Type.Enter:
                self._global_hover_option = option
                self._refresh_bulk_previews()
            elif event.type() == QEvent.Type.Leave:
                self._global_hover_option = None
                self._refresh_bulk_previews()
        if event.type() == QEvent.Type.MouseButtonDblClick:
            if obj in self._preview_labels.values():
                path = next(path for path, label in self._preview_labels.items() if label is obj)
                inspection = next(item for item in self._inspections if item.input_path == path)
                local_selection = self._selections.get(path)
                initial_bounds = local_selection.bounds if local_selection is not None else None
                if self._bulk_mode and initial_bounds is None:
                    trim_choice = next(
                        ((kind, name) for (kind, name), action in self._bulk_actions.items()
                         if action in {"trim", "trim & hide"}),
                        None,
                    )
                    if trim_choice is not None:
                        trim_kind, trim_name = trim_choice
                        trim_option = next(
                            (option for option in inspection.options
                             if option.kind == trim_kind and option.label == trim_name),
                            None,
                        )
                        if trim_option is not None:
                            initial_bounds = trim_option.bounds
                editor = ArtworkCropDialog(inspection, self, initial_bounds, local_selection)
                if editor.exec() == QDialog.DialogCode.Accepted:
                    selection = editor.crop_selection()
                    self._selections[path] = selection
                    self._selected_paths = {path}
                    self._selection_anchor = path
                    self._apply_selection({path})
                    trim_bounds = (
                        selection.bounds
                        if selection.kind in {"layer", "spot_color"} or selection.manual_crop
                        else None
                    )
                    self._update_preview(
                        inspection,
                        trim_bounds,
                        selection=selection,
                        hide_items=selection.hide_items,
                    )
                return False

        if event.type() == QEvent.Type.MouseButtonPress:
            if obj in self._preview_labels.values():
                path = next(path for path, label in self._preview_labels.items() if label is obj)
                mod = QApplication.keyboardModifiers()
                ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
                shift = bool(mod & Qt.KeyboardModifier.ShiftModifier)
                if ctrl:
                    next_selection = set(self._selected_paths)
                    if path in next_selection:
                        next_selection.remove(path)
                    else:
                        next_selection.add(path)
                    self._selection_anchor = path
                    self._apply_selection(next_selection)
                    return False
                if shift:
                    if self._selection_anchor is None:
                        self._selection_anchor = path
                    anchor_index = next((idx for idx, inspection in enumerate(self._inspections) if inspection.input_path == self._selection_anchor), None)
                    current_index = next((idx for idx, inspection in enumerate(self._inspections) if inspection.input_path == path), None)
                    if anchor_index is not None and current_index is not None:
                        start, end = sorted((anchor_index, current_index))
                        next_selection = {self._inspections[idx].input_path for idx in range(start, end + 1)}
                        self._apply_selection(next_selection)
                    return False
                self._selected_paths = {path}
                self._selection_anchor = path
                self._apply_selection({path})
                return False

            if self._selected_paths and obj not in self._preview_labels.values():
                self._selected_paths.clear()
                self._selection_anchor = None
                self._apply_selection(set())
        return super().eventFilter(obj, event)

    def _update_preview(
        self,
        inspection: ArtworkInspection,
        bounds: object,
        hover_bounds: object = None,
        selection: CutSelection | None = None,
        hide_items: tuple[tuple[str, str, tuple[int, int, int] | None, int | None], ...] = (),
    ) -> None:
        if not self._show_crop_border:
            bounds = None
            hover_bounds = None
        raw = inspection.preview
        if hide_items:
            try:
                raw = render_artwork_preview(
                    inspection.input_path,
                    dpi=72,
                    hide_items=hide_items,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "Preview update failed", str(exc))
                return
        image = _artwork_preview_qimage(raw)
        pixmap = QPixmap.fromImage(image)
        pixmap = pixmap.scaled(286, 196, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter = None
        if bounds is not None or hover_bounds is not None:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            scale_x = pixmap.width() / max(inspection.page_bounds.width, 1)
            scale_y = pixmap.height() / max(inspection.page_bounds.height, 1)
        if bounds is not None:
            stroke_color = selection.stroke_color if selection is not None else "#00FF66"
            stroke_weight = selection.stroke_weight if selection is not None else 1.0
            pen = QPen(QColor(stroke_color))
            pen.setWidthF(4.0 * stroke_weight)
            painter.setPen(pen)
            x = bounds.left * scale_x
            y = bounds.top * scale_y
            width = max(1, bounds.width * scale_x)
            height = max(1, bounds.height * scale_y)
            rect = QRect(round(x), round(y), round(width), round(height))
            stroke_shape = selection.stroke_shape if selection is not None else "rectangle"
            corners = selection.corners if selection is not None else "sharp"
            corner_amount = selection.corner_amount if selection is not None else 0.0
            if stroke_shape == "custom" and selection is not None and selection.custom_path:
                custom_path = QPainterPath()
                for command, values in selection.custom_path:
                    if command == "M":
                        custom_path.moveTo(values[0] * scale_x, values[1] * scale_y)
                    elif command == "L":
                        custom_path.lineTo(values[0] * scale_x, values[1] * scale_y)
                    elif command == "Q":
                        custom_path.quadTo(
                            values[0] * scale_x, values[1] * scale_y,
                            values[2] * scale_x, values[3] * scale_y,
                        )
                    elif command == "C":
                        custom_path.cubicTo(
                            values[0] * scale_x, values[1] * scale_y,
                            values[2] * scale_x, values[3] * scale_y,
                            values[4] * scale_x, values[5] * scale_y,
                        )
                    elif command == "Z":
                        custom_path.closeSubpath()
                painter.drawPath(custom_path)
            elif stroke_shape == "ellipse":
                painter.drawEllipse(rect)
            elif corners == "rounded":
                radius = min(width, height) * (0.5 * corner_amount)
                painter.drawRoundedRect(rect, radius, radius)
            elif corners == "beveled" or stroke_shape == "custom":
                path = QPainterPath()
                bevel = min(width, height) * (0.25 * corner_amount)
                path.moveTo(x + bevel, y)
                path.lineTo(x + width - bevel, y)
                path.lineTo(x + width, y + bevel)
                path.lineTo(x + width, y + height - bevel)
                path.lineTo(x + width - bevel, y + height)
                path.lineTo(x + bevel, y + height)
                path.lineTo(x, y + height - bevel)
                path.lineTo(x, y + bevel)
                path.closeSubpath()
                painter.drawPath(path)
            else:
                painter.drawRect(rect)
        if hover_bounds is not None:
            painter.setPen(QPen(QColor("#FFFF00"), 3, Qt.PenStyle.DashLine))
            painter.drawRect(
                round(hover_bounds.left * scale_x), round(hover_bounds.top * scale_y),
                max(1, round(hover_bounds.width * scale_x)),
                max(1, round(hover_bounds.height * scale_y)),
            )
        if painter is not None:
            painter.end()
        self._preview_labels[inspection.input_path].setPixmap(pixmap)

    def selections(self) -> dict[Path, object | None]:
        if self._bulk_mode:
            selections: dict[Path, object | None] = {}
            for inspection in self._inspections:
                local_selection = self._selections.get(inspection.input_path)
                if local_selection is not None:
                    selections[inspection.input_path] = local_selection
                    continue
                trim_option: CutOption | None = None
                hide_items: list[tuple[str, str, tuple[int, int, int] | None, int | None]] = []
                for option in inspection.options:
                    action = self._bulk_actions.get((option.kind, option.label), "")
                    if action in {"trim", "trim & hide"}:
                        trim_option = option
                    if action in {"hide", "trim & hide"}:
                        hide_items.append((option.kind, option.label, option.color_rgb, option.xref))
                if trim_option is not None:
                    selection = CutSelection(
                        bounds=trim_option.bounds,
                        kind=trim_option.kind,
                        name=trim_option.label,
                        color_rgb=trim_option.color_rgb,
                        xref=trim_option.xref,
                        hide_items=tuple(hide_items),
                    )
                elif hide_items:
                    selection = CutSelection(
                        bounds=inspection.page_bounds,
                        kind="manual",
                        hide_items=tuple(hide_items),
                    )
                else:
                    selection = CutSelection(
                        bounds=inspection.page_bounds,
                        kind="manual",
                    )
                selections[inspection.input_path] = selection
            return selections
        return dict(self._selections)
