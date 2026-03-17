from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from PySide6 import QtCore, QtGui, QtMultimedia, QtMultimediaWidgets, QtWidgets

from .config import save_settings
from .models import AppSettings, JobOptions, JobRecord
from .pipeline import list_image_files, probe_video
from .profiles import IMAGE_PROFILES, PROFILES
from .tools import refresh_tool_paths
from .worker import JobWorker, ToolInstallWorker


class ImageCompareViewer(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.before_pixmap: QtGui.QPixmap | None = None
        self.after_pixmap: QtGui.QPixmap | None = None
        self.before_label = "Before"
        self.after_label = "After"
        self.split_ratio = 0.5
        self._image_rect = QtCore.QRect()
        self.setMinimumHeight(280)
        self.setMouseTracking(True)

    def set_images(self, before_path: Path | None, after_path: Path | None) -> None:
        self.before_pixmap = QtGui.QPixmap(str(before_path)) if before_path else None
        self.after_pixmap = QtGui.QPixmap(str(after_path)) if after_path else None
        self.update()

    def clear(self) -> None:
        self.before_pixmap = None
        self.after_pixmap = None
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#fbf8f3"))

        if not self.before_pixmap or not self.after_pixmap:
            painter.setPen(QtGui.QColor("#6b7280"))
            painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "No before/after pair loaded yet.")
            return

        content_rect = self.rect().adjusted(12, 12, -12, -12)
        aspect = self.after_pixmap.width() / max(1, self.after_pixmap.height())
        target_width = content_rect.width()
        target_height = int(target_width / max(aspect, 0.01))
        if target_height > content_rect.height():
            target_height = content_rect.height()
            target_width = int(target_height * aspect)

        self._image_rect = QtCore.QRect(0, 0, target_width, target_height)
        self._image_rect.moveCenter(content_rect.center())

        before = self.before_pixmap.scaled(
            self._image_rect.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        after = self.after_pixmap.scaled(
            self._image_rect.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        draw_rect = QtCore.QRect(0, 0, after.width(), after.height())
        draw_rect.moveCenter(self._image_rect.center())

        painter.drawPixmap(draw_rect, after)

        split_x = draw_rect.left() + int(draw_rect.width() * self.split_ratio)
        clip_rect = QtCore.QRect(draw_rect.left(), draw_rect.top(), max(1, split_x - draw_rect.left()), draw_rect.height())
        painter.save()
        painter.setClipRect(clip_rect)
        painter.drawPixmap(draw_rect, before)
        painter.restore()

        painter.setPen(QtGui.QPen(QtGui.QColor("#bc5b3c"), 2))
        painter.drawLine(split_x, draw_rect.top(), split_x, draw_rect.bottom())

        label_padding = 8
        before_box = QtCore.QRect(draw_rect.left() + 10, draw_rect.top() + 10, 90, 28)
        after_box = QtCore.QRect(draw_rect.right() - 100, draw_rect.top() + 10, 90, 28)
        painter.fillRect(before_box, QtGui.QColor(31, 41, 51, 180))
        painter.fillRect(after_box, QtGui.QColor(188, 91, 60, 210))
        painter.setPen(QtGui.QColor("#ffffff"))
        painter.drawText(before_box.adjusted(label_padding, 0, -label_padding, 0), QtCore.Qt.AlignmentFlag.AlignVCenter, self.before_label)
        painter.drawText(after_box.adjusted(label_padding, 0, -label_padding, 0), QtCore.Qt.AlignmentFlag.AlignVCenter, self.after_label)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        self._update_split(event.position().x())

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            self._update_split(event.position().x())

    def _update_split(self, x: float) -> None:
        if not self._image_rect.width():
            return
        ratio = (x - self._image_rect.left()) / max(1, self._image_rect.width())
        self.split_ratio = max(0.0, min(1.0, ratio))
        self.update()


class ImageComparisonDialog(QtWidgets.QDialog):
    def __init__(self, pairs: list[tuple[Path, Path]], start_index: int = 0, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.pairs = pairs
        self.current_index = max(0, min(start_index, len(pairs) - 1)) if pairs else 0

        self.setWindowTitle("Image Before / After")
        self.resize(1280, 820)

        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        self.prev_button = QtWidgets.QPushButton("Prev")
        self.prev_button.clicked.connect(self.show_previous)
        self.next_button = QtWidgets.QPushButton("Next")
        self.next_button.clicked.connect(self.show_next)
        self.info_label = QtWidgets.QLabel("")
        self.info_label.setWordWrap(True)
        controls.addWidget(self.prev_button)
        controls.addWidget(self.next_button)
        controls.addWidget(self.info_label, stretch=1)
        layout.addLayout(controls)

        self.viewer = ImageCompareViewer()
        self.viewer.setMinimumSize(960, 640)
        layout.addWidget(self.viewer, stretch=1)

        self._show_pair()

    def _show_pair(self) -> None:
        if not self.pairs:
            self.viewer.clear()
            self.info_label.setText("No image pair available.")
            self.prev_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        before_path, after_path = self.pairs[self.current_index]
        self.viewer.set_images(before_path, after_path)
        self.info_label.setText(f"{self.current_index + 1}/{len(self.pairs)}  {before_path.name} -> {after_path.name}")
        self.prev_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(self.current_index < len(self.pairs) - 1)

    def show_previous(self) -> None:
        if self.current_index > 0:
            self.current_index -= 1
            self._show_pair()

    def show_next(self) -> None:
        if self.current_index < len(self.pairs) - 1:
            self.current_index += 1
            self._show_pair()


class VideoComparisonDialog(QtWidgets.QDialog):
    def __init__(self, before_path: Path, after_path: Path, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.before_path = before_path
        self.after_path = after_path
        self.is_scrubbing = False

        self.setWindowTitle("Video Before / After")
        self.resize(1440, 860)

        layout = QtWidgets.QVBoxLayout(self)
        labels = QtWidgets.QHBoxLayout()
        before_label = QtWidgets.QLabel(f"Original: {before_path.name}")
        after_label = QtWidgets.QLabel(f"Upscaled: {after_path.name}")
        labels.addWidget(before_label, stretch=1)
        labels.addWidget(after_label, stretch=1)
        layout.addLayout(labels)

        viewers = QtWidgets.QHBoxLayout()
        self.before_video = QtMultimediaWidgets.QVideoWidget()
        self.after_video = QtMultimediaWidgets.QVideoWidget()
        viewers.addWidget(self.before_video, stretch=1)
        viewers.addWidget(self.after_video, stretch=1)
        layout.addLayout(viewers, stretch=1)

        controls = QtWidgets.QHBoxLayout()
        self.play_button = QtWidgets.QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)
        self.restart_button = QtWidgets.QPushButton("Restart")
        self.restart_button.clicked.connect(self.restart_playback)
        self.position_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(self._begin_scrub)
        self.position_slider.sliderReleased.connect(self._end_scrub)
        self.position_slider.sliderMoved.connect(self._set_position)
        self.time_label = QtWidgets.QLabel("00:00 / 00:00")
        controls.addWidget(self.play_button)
        controls.addWidget(self.restart_button)
        controls.addWidget(self.position_slider, stretch=1)
        controls.addWidget(self.time_label)
        layout.addLayout(controls)

        self.before_player = QtMultimedia.QMediaPlayer(self)
        self.after_player = QtMultimedia.QMediaPlayer(self)
        self.before_audio = QtMultimedia.QAudioOutput(self)
        self.after_audio = QtMultimedia.QAudioOutput(self)
        self.before_audio.setVolume(0.0)
        self.after_audio.setVolume(0.7)
        self.before_player.setAudioOutput(self.before_audio)
        self.after_player.setAudioOutput(self.after_audio)
        self.before_player.setVideoOutput(self.before_video)
        self.after_player.setVideoOutput(self.after_video)
        self.before_player.setSource(QtCore.QUrl.fromLocalFile(str(before_path.resolve())))
        self.after_player.setSource(QtCore.QUrl.fromLocalFile(str(after_path.resolve())))

        self.after_player.durationChanged.connect(self._update_duration)
        self.after_player.positionChanged.connect(self._update_position)
        self.after_player.playbackStateChanged.connect(self._sync_play_button)

    def _format_ms(self, milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _update_duration(self, duration: int) -> None:
        self.position_slider.setRange(0, max(0, duration))
        self.time_label.setText(f"{self._format_ms(self.after_player.position())} / {self._format_ms(duration)}")

    def _update_position(self, position: int) -> None:
        if not self.is_scrubbing:
            self.position_slider.setValue(position)
        self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(self.after_player.duration())}")

    def _sync_play_button(self, state: QtMultimedia.QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText("Pause" if state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState else "Play")

    def _begin_scrub(self) -> None:
        self.is_scrubbing = True

    def _end_scrub(self) -> None:
        self.is_scrubbing = False
        self._set_position(self.position_slider.value())

    def _set_position(self, position: int) -> None:
        self.before_player.setPosition(position)
        self.after_player.setPosition(position)

    def toggle_playback(self) -> None:
        if self.after_player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
            self.before_player.pause()
            self.after_player.pause()
        else:
            current = self.after_player.position()
            self.before_player.setPosition(current)
            self.after_player.setPosition(current)
            self.before_player.play()
            self.after_player.play()

    def restart_playback(self) -> None:
        self._set_position(0)
        self.before_player.play()
        self.after_player.play()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.before_player.stop()
        self.after_player.stop()
        super().closeEvent(event)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = refresh_tool_paths(settings)
        self.jobs: list[JobRecord] = []
        self.active_job: JobRecord | None = None
        self.active_worker: JobWorker | None = None
        self.install_workers: list[ToolInstallWorker] = []
        self.preview_dialogs: list[QtWidgets.QDialog] = []
        self.compare_pairs: list[tuple[Path, Path]] = []
        self.current_compare_index = 0
        self.active_job_started_at: float | None = None
        self.progress_timer = QtCore.QTimer(self)
        self.progress_timer.setInterval(1000)
        self.progress_timer.timeout.connect(self.refresh_active_job_eta)

        self.setWindowTitle("Upscaler")
        self.resize(1680, 1100)
        self.setMinimumSize(1440, 920)
        self._build_ui()
        self._apply_style()
        self._populate_profiles()
        self._load_settings_into_form()
        self.refresh_tool_status()

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)

        main_layout = QtWidgets.QHBoxLayout(root)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(18)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(14)
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(14)
        main_layout.addLayout(left, stretch=3)
        main_layout.addLayout(right, stretch=2)

        self.form_group = QtWidgets.QGroupBox("Render Setup")
        form_layout = QtWidgets.QGridLayout(self.form_group)
        form_layout.setVerticalSpacing(10)
        form_layout.setHorizontalSpacing(10)
        self.form_layout = form_layout
        form_layout.setColumnStretch(0, 0)
        form_layout.setColumnStretch(1, 1)
        form_layout.setColumnStretch(2, 0)
        form_layout.setColumnStretch(3, 1)

        row = 0
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.currentTextChanged.connect(self.apply_profile)
        form_layout.addWidget(QtWidgets.QLabel("Profile"), row, 0)
        form_layout.addWidget(self.profile_combo, row, 1, 1, 3)
        row += 1

        self.profile_label = QtWidgets.QLabel("")
        self.profile_label.setWordWrap(True)
        form_layout.addWidget(self.profile_label, row, 0, 1, 4)
        row += 1

        self.input_edit = QtWidgets.QLineEdit()
        browse_input = QtWidgets.QPushButton("Browse")
        browse_input.clicked.connect(self.pick_input)
        analyze_btn = QtWidgets.QPushButton("Analyze")
        analyze_btn.clicked.connect(self.analyze_input)
        form_layout.addWidget(QtWidgets.QLabel("Input video"), row, 0)
        form_layout.addWidget(self.input_edit, row, 1)
        form_layout.addWidget(browse_input, row, 2)
        form_layout.addWidget(analyze_btn, row, 3)
        row += 1

        self.output_edit = QtWidgets.QLineEdit()
        browse_output = QtWidgets.QPushButton("Save As")
        browse_output.clicked.connect(self.pick_output)
        form_layout.addWidget(QtWidgets.QLabel("Output file"), row, 0)
        form_layout.addWidget(self.output_edit, row, 1, 1, 2)
        form_layout.addWidget(browse_output, row, 3)
        row += 1

        self.metadata_box = QtWidgets.QPlainTextEdit()
        self.metadata_box.setReadOnly(True)
        self.metadata_box.setFixedHeight(100)
        form_layout.addWidget(QtWidgets.QLabel("Input analysis"), row, 0)
        form_layout.addWidget(self.metadata_box, row, 1, 1, 3)
        row += 1

        self.target_height_combo = QtWidgets.QComboBox()
        self.target_height_combo.addItem("Keep source", 0)
        self.target_height_combo.addItem("720p / HD", 720)
        self.target_height_combo.addItem("1080p", 1080)
        self.target_height_combo.addItem("1440p", 1440)
        self.target_height_combo.addItem("2160p / 4K", 2160)
        form_layout.addWidget(QtWidgets.QLabel("Target height"), row, 0)
        form_layout.addWidget(self.target_height_combo, row, 1)

        self.target_fps_combo = QtWidgets.QComboBox()
        self.target_fps_combo.addItem("Keep source", 0.0)
        self.target_fps_combo.addItem("24 fps", 24.0)
        self.target_fps_combo.addItem("30 fps", 30.0)
        self.target_fps_combo.addItem("48 fps", 48.0)
        self.target_fps_combo.addItem("60 fps", 60.0)
        form_layout.addWidget(QtWidgets.QLabel("Target fps"), row, 2)
        form_layout.addWidget(self.target_fps_combo, row, 3)
        row += 1

        self.upscale_backend_combo = QtWidgets.QComboBox()
        self.upscale_backend_combo.addItem("FFmpeg scale", "ffmpeg")
        self.upscale_backend_combo.addItem("Local AI VSR (Real-ESRGAN)", "realesrgan")
        form_layout.addWidget(QtWidgets.QLabel("Upscale backend"), row, 0)
        form_layout.addWidget(self.upscale_backend_combo, row, 1)

        self.interp_backend_combo = QtWidgets.QComboBox()
        self.interp_backend_combo.addItem("None", "none")
        self.interp_backend_combo.addItem("FFmpeg minterpolate", "minterpolate")
        self.interp_backend_combo.addItem("RIFE", "rife")
        form_layout.addWidget(QtWidgets.QLabel("Interpolation"), row, 2)
        form_layout.addWidget(self.interp_backend_combo, row, 3)
        row += 1

        self.upscale_model_combo = QtWidgets.QComboBox()
        for model in ["realesrgan-x4plus", "realesr-animevideov3", "realesrgan-x4plus-anime", "realesrnet-x4plus"]:
            self.upscale_model_combo.addItem(model)
        form_layout.addWidget(QtWidgets.QLabel("Upscale model"), row, 0)
        form_layout.addWidget(self.upscale_model_combo, row, 1)

        self.interp_model_combo = QtWidgets.QComboBox()
        for model in ["rife-v4.6", "rife-v4", "rife-anime"]:
            self.interp_model_combo.addItem(model)
        form_layout.addWidget(QtWidgets.QLabel("RIFE model"), row, 2)
        form_layout.addWidget(self.interp_model_combo, row, 3)
        row += 1

        self.denoise_spin = QtWidgets.QSpinBox()
        self.denoise_spin.setRange(0, 8)
        form_layout.addWidget(QtWidgets.QLabel("Denoise"), row, 0)
        form_layout.addWidget(self.denoise_spin, row, 1)

        self.sharpen_spin = QtWidgets.QDoubleSpinBox()
        self.sharpen_spin.setRange(0.0, 1.5)
        self.sharpen_spin.setSingleStep(0.05)
        form_layout.addWidget(QtWidgets.QLabel("Sharpen"), row, 2)
        form_layout.addWidget(self.sharpen_spin, row, 3)
        row += 1

        self.grain_spin = QtWidgets.QDoubleSpinBox()
        self.grain_spin.setRange(0.0, 1.0)
        self.grain_spin.setSingleStep(0.05)
        form_layout.addWidget(QtWidgets.QLabel("Grain"), row, 0)
        form_layout.addWidget(self.grain_spin, row, 1)

        self.deband_check = QtWidgets.QCheckBox("Deband")
        form_layout.addWidget(self.deband_check, row, 2, 1, 2)
        row += 1

        self.temporal_spin = QtWidgets.QDoubleSpinBox()
        self.temporal_spin.setRange(0.0, 1.0)
        self.temporal_spin.setSingleStep(0.05)
        self.temporal_spin.setValue(0.0)
        form_layout.addWidget(QtWidgets.QLabel("Temporal VSR"), row, 0)
        form_layout.addWidget(self.temporal_spin, row, 1)
        row += 1

        self.video_brightness_spin = QtWidgets.QDoubleSpinBox()
        self.video_brightness_spin.setRange(0.5, 1.5)
        self.video_brightness_spin.setSingleStep(0.02)
        self.video_brightness_spin.setValue(1.0)
        form_layout.addWidget(QtWidgets.QLabel("Brightness"), row, 0)
        form_layout.addWidget(self.video_brightness_spin, row, 1)

        self.video_contrast_spin = QtWidgets.QDoubleSpinBox()
        self.video_contrast_spin.setRange(0.5, 1.8)
        self.video_contrast_spin.setSingleStep(0.02)
        self.video_contrast_spin.setValue(1.0)
        form_layout.addWidget(QtWidgets.QLabel("Contrast"), row, 2)
        form_layout.addWidget(self.video_contrast_spin, row, 3)
        row += 1

        self.video_saturation_spin = QtWidgets.QDoubleSpinBox()
        self.video_saturation_spin.setRange(0.0, 2.0)
        self.video_saturation_spin.setSingleStep(0.02)
        self.video_saturation_spin.setValue(1.0)
        form_layout.addWidget(QtWidgets.QLabel("Saturation"), row, 0)
        form_layout.addWidget(self.video_saturation_spin, row, 1)

        self.video_gamma_spin = QtWidgets.QDoubleSpinBox()
        self.video_gamma_spin.setRange(0.5, 1.8)
        self.video_gamma_spin.setSingleStep(0.02)
        self.video_gamma_spin.setValue(1.0)
        form_layout.addWidget(QtWidgets.QLabel("Gamma"), row, 2)
        form_layout.addWidget(self.video_gamma_spin, row, 3)
        row += 1

        self.encoder_combo = QtWidgets.QComboBox()
        self.encoder_combo.addItem("HEVC NVENC", "hevc_nvenc")
        self.encoder_combo.addItem("H.264 NVENC", "h264_nvenc")
        self.encoder_combo.addItem("x264 CPU", "libx264")
        form_layout.addWidget(QtWidgets.QLabel("Encoder"), row, 0)
        form_layout.addWidget(self.encoder_combo, row, 1)

        self.cq_spin = QtWidgets.QSpinBox()
        self.cq_spin.setRange(10, 28)
        form_layout.addWidget(QtWidgets.QLabel("CQ / CRF"), row, 2)
        form_layout.addWidget(self.cq_spin, row, 3)
        row += 1

        self.keep_temp_check = QtWidgets.QCheckBox("Keep temp frames")
        form_layout.addWidget(self.keep_temp_check, row, 0, 1, 2)
        row += 1

        buttons = QtWidgets.QHBoxLayout()
        self.queue_button = QtWidgets.QPushButton("Add To Queue")
        self.queue_button.setObjectName("primary")
        self.queue_button.clicked.connect(self.enqueue_job)
        self.start_button = QtWidgets.QPushButton("Start Queue")
        self.start_button.clicked.connect(self.start_next_job)
        self.cancel_button = QtWidgets.QPushButton("Cancel Active")
        self.cancel_button.clicked.connect(self.cancel_active_job)
        buttons.addWidget(self.queue_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        form_layout.addLayout(buttons, row, 0, 1, 4)
        row += 1

        self.video_advanced_button = QtWidgets.QToolButton()
        self.video_advanced_button.setText("Show advanced video controls")
        self.video_advanced_button.setCheckable(True)
        self.video_advanced_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.video_advanced_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.video_advanced_button.toggled.connect(self.toggle_video_advanced)
        form_layout.addWidget(self.video_advanced_button, row, 0, 1, 4)

        left.addWidget(self.form_group)

        self.image_group = QtWidgets.QGroupBox("Image Upscale / Batch")
        image_layout = QtWidgets.QGridLayout(self.image_group)
        image_layout.setHorizontalSpacing(10)
        image_layout.setVerticalSpacing(10)
        self.image_layout = image_layout
        image_layout.setColumnStretch(0, 0)
        image_layout.setColumnStretch(1, 1)
        image_layout.setColumnStretch(2, 0)
        image_layout.setColumnStretch(3, 1)

        image_row = 0
        self.image_profile_combo = QtWidgets.QComboBox()
        self.image_profile_combo.currentTextChanged.connect(self.apply_image_profile)
        image_layout.addWidget(QtWidgets.QLabel("Profile"), image_row, 0)
        image_layout.addWidget(self.image_profile_combo, image_row, 1, 1, 3)
        image_row += 1

        self.image_profile_label = QtWidgets.QLabel("")
        self.image_profile_label.setWordWrap(True)
        image_layout.addWidget(self.image_profile_label, image_row, 0, 1, 4)
        image_row += 1

        self.image_input_edit = QtWidgets.QLineEdit()
        image_file_btn = QtWidgets.QPushButton("File")
        image_file_btn.clicked.connect(self.pick_image_file)
        image_dir_btn = QtWidgets.QPushButton("Folder")
        image_dir_btn.clicked.connect(self.pick_image_folder)
        image_layout.addWidget(QtWidgets.QLabel("Input image(s)"), image_row, 0)
        image_layout.addWidget(self.image_input_edit, image_row, 1)
        image_layout.addWidget(image_file_btn, image_row, 2)
        image_layout.addWidget(image_dir_btn, image_row, 3)
        image_row += 1

        self.image_output_edit = QtWidgets.QLineEdit()
        image_output_btn = QtWidgets.QPushButton("Choose Output")
        image_output_btn.clicked.connect(self.pick_image_output_target)
        image_layout.addWidget(QtWidgets.QLabel("Output target"), image_row, 0)
        image_layout.addWidget(self.image_output_edit, image_row, 1, 1, 2)
        image_layout.addWidget(image_output_btn, image_row, 3)
        image_row += 1

        self.image_meta_box = QtWidgets.QPlainTextEdit()
        self.image_meta_box.setReadOnly(True)
        self.image_meta_box.setFixedHeight(90)
        analyze_images_btn = QtWidgets.QPushButton("Analyze")
        analyze_images_btn.clicked.connect(self.analyze_image_input)
        image_layout.addWidget(QtWidgets.QLabel("Batch analysis"), image_row, 0)
        image_layout.addWidget(self.image_meta_box, image_row, 1, 1, 2)
        image_layout.addWidget(analyze_images_btn, image_row, 3)
        image_row += 1

        self.image_backend_combo = QtWidgets.QComboBox()
        self.image_backend_combo.addItem("HAT (Recommended)", "hat")
        self.image_backend_combo.addItem("Pillow Lanczos", "ffmpeg")
        self.image_backend_combo.addItem("Local AI (Real-ESRGAN)", "realesrgan")
        image_layout.addWidget(QtWidgets.QLabel("Upscale backend"), image_row, 0)
        image_layout.addWidget(self.image_backend_combo, image_row, 1)

        self.image_model_combo = QtWidgets.QComboBox()
        for model in ["real_hat_gan", "real_hat_gan_sharper", "realesrgan-x4plus", "realesrgan-x4plus-anime", "realesrnet-x4plus", "realesr-animevideov3"]:
            self.image_model_combo.addItem(model)
        image_layout.addWidget(QtWidgets.QLabel("Model"), image_row, 2)
        image_layout.addWidget(self.image_model_combo, image_row, 3)
        image_row += 1

        self.image_scale_combo = QtWidgets.QComboBox()
        self.image_scale_combo.addItem("2x", 2)
        self.image_scale_combo.addItem("4x", 4)
        image_layout.addWidget(QtWidgets.QLabel("Upscale factor"), image_row, 0)
        image_layout.addWidget(self.image_scale_combo, image_row, 1)

        self.image_format_combo = QtWidgets.QComboBox()
        self.image_format_combo.addItem("PNG", "png")
        self.image_format_combo.addItem("JPG", "jpg")
        self.image_format_combo.addItem("WEBP", "webp")
        self.image_format_combo.currentIndexChanged.connect(self.sync_single_image_output_suffix)
        image_layout.addWidget(QtWidgets.QLabel("Output format"), image_row, 2)
        image_layout.addWidget(self.image_format_combo, image_row, 3)
        image_row += 1

        self.image_denoise_spin = QtWidgets.QSpinBox()
        self.image_denoise_spin.setRange(0, 8)
        image_layout.addWidget(QtWidgets.QLabel("Denoise"), image_row, 0)
        image_layout.addWidget(self.image_denoise_spin, image_row, 1)

        self.image_sharpen_spin = QtWidgets.QDoubleSpinBox()
        self.image_sharpen_spin.setRange(0.0, 1.5)
        self.image_sharpen_spin.setSingleStep(0.05)
        image_layout.addWidget(QtWidgets.QLabel("Sharpen"), image_row, 2)
        image_layout.addWidget(self.image_sharpen_spin, image_row, 3)
        image_row += 1

        self.image_brightness_spin = QtWidgets.QDoubleSpinBox()
        self.image_brightness_spin.setRange(0.5, 1.5)
        self.image_brightness_spin.setSingleStep(0.02)
        self.image_brightness_spin.setValue(1.0)
        image_layout.addWidget(QtWidgets.QLabel("Brightness"), image_row, 0)
        image_layout.addWidget(self.image_brightness_spin, image_row, 1)

        self.image_contrast_spin = QtWidgets.QDoubleSpinBox()
        self.image_contrast_spin.setRange(0.5, 1.8)
        self.image_contrast_spin.setSingleStep(0.02)
        self.image_contrast_spin.setValue(1.0)
        image_layout.addWidget(QtWidgets.QLabel("Contrast"), image_row, 2)
        image_layout.addWidget(self.image_contrast_spin, image_row, 3)
        image_row += 1

        self.image_saturation_spin = QtWidgets.QDoubleSpinBox()
        self.image_saturation_spin.setRange(0.0, 2.0)
        self.image_saturation_spin.setSingleStep(0.02)
        self.image_saturation_spin.setValue(1.0)
        image_layout.addWidget(QtWidgets.QLabel("Saturation"), image_row, 0)
        image_layout.addWidget(self.image_saturation_spin, image_row, 1)

        self.image_gamma_spin = QtWidgets.QDoubleSpinBox()
        self.image_gamma_spin.setRange(0.5, 1.8)
        self.image_gamma_spin.setSingleStep(0.02)
        self.image_gamma_spin.setValue(1.0)
        image_layout.addWidget(QtWidgets.QLabel("Gamma"), image_row, 2)
        image_layout.addWidget(self.image_gamma_spin, image_row, 3)
        image_row += 1

        self.image_autocontrast_check = QtWidgets.QCheckBox("Autocontrast")
        image_layout.addWidget(self.image_autocontrast_check, image_row, 0, 1, 2)

        self.image_overwrite_check = QtWidgets.QCheckBox("Overwrite existing")
        image_layout.addWidget(self.image_overwrite_check, image_row, 2, 1, 2)
        image_row += 1

        self.image_queue_button = QtWidgets.QPushButton("Add Image Job")
        self.image_queue_button.setObjectName("primary")
        self.image_queue_button.clicked.connect(self.enqueue_image_job)
        image_layout.addWidget(self.image_queue_button, image_row, 0, 1, 4)
        image_row += 1

        self.image_advanced_button = QtWidgets.QToolButton()
        self.image_advanced_button.setText("Show advanced image controls")
        self.image_advanced_button.setCheckable(True)
        self.image_advanced_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.image_advanced_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.image_advanced_button.toggled.connect(self.toggle_image_advanced)
        image_layout.addWidget(self.image_advanced_button, image_row, 0, 1, 4)

        left.addWidget(self.image_group)

        self.tools_toggle_button = QtWidgets.QToolButton()
        self.tools_toggle_button.setText("Show tools and paths")
        self.tools_toggle_button.setCheckable(True)
        self.tools_toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tools_toggle_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.tools_toggle_button.toggled.connect(self.toggle_tools)
        left.addWidget(self.tools_toggle_button)

        self.tools_group = QtWidgets.QGroupBox("Tools")
        tools_layout = QtWidgets.QGridLayout(self.tools_group)
        tools_layout.setHorizontalSpacing(10)
        tools_layout.setVerticalSpacing(10)
        self.tools_layout = tools_layout
        tools_layout.setColumnStretch(0, 0)
        tools_layout.setColumnStretch(1, 1)
        tools_layout.setColumnStretch(2, 0)

        self.ffmpeg_edit = QtWidgets.QLineEdit()
        self.ffprobe_edit = QtWidgets.QLineEdit()
        self.realesrgan_edit = QtWidgets.QLineEdit()
        self.rife_edit = QtWidgets.QLineEdit()
        self.hat_edit = QtWidgets.QLineEdit()

        tools_layout.addWidget(QtWidgets.QLabel("ffmpeg"), 0, 0)
        tools_layout.addWidget(self.ffmpeg_edit, 0, 1, 1, 2)
        tools_layout.addWidget(QtWidgets.QLabel("ffprobe"), 1, 0)
        tools_layout.addWidget(self.ffprobe_edit, 1, 1, 1, 2)

        tools_layout.addWidget(QtWidgets.QLabel("Real-ESRGAN"), 2, 0)
        tools_layout.addWidget(self.realesrgan_edit, 2, 1)
        realesrgan_btn = QtWidgets.QPushButton("Download")
        realesrgan_btn.clicked.connect(lambda: self.install_tool("realesrgan"))
        tools_layout.addWidget(realesrgan_btn, 2, 2)

        tools_layout.addWidget(QtWidgets.QLabel("RIFE"), 3, 0)
        tools_layout.addWidget(self.rife_edit, 3, 1)
        rife_btn = QtWidgets.QPushButton("Download")
        rife_btn.clicked.connect(lambda: self.install_tool("rife"))
        tools_layout.addWidget(rife_btn, 3, 2)

        tools_layout.addWidget(QtWidgets.QLabel("HAT"), 4, 0)
        tools_layout.addWidget(self.hat_edit, 4, 1)
        hat_btn = QtWidgets.QPushButton("Download")
        hat_btn.clicked.connect(lambda: self.install_tool("hat"))
        tools_layout.addWidget(hat_btn, 4, 2)

        refresh_btn = QtWidgets.QPushButton("Refresh Detection")
        refresh_btn.clicked.connect(self.refresh_tool_status)
        tools_layout.addWidget(refresh_btn, 5, 0, 1, 3)
        left.addWidget(self.tools_group)
        self.tools_group.hide()
        left.addStretch(1)

        active_group = QtWidgets.QGroupBox("Active Job")
        active_layout = QtWidgets.QVBoxLayout(active_group)
        self.active_job_title = QtWidgets.QLabel("No active job.")
        self.active_job_title.setWordWrap(True)
        self.active_progress_bar = QtWidgets.QProgressBar()
        self.active_progress_bar.setRange(0, 100)
        self.active_progress_bar.setValue(0)
        self.active_progress_bar.setFormat("%p%")
        active_meta_layout = QtWidgets.QHBoxLayout()
        self.active_stage_label = QtWidgets.QLabel("Stage: waiting")
        self.active_elapsed_label = QtWidgets.QLabel("Elapsed: 00:00")
        self.active_eta_label = QtWidgets.QLabel("ETA: --:--")
        active_meta_layout.addWidget(self.active_stage_label, stretch=2)
        active_meta_layout.addWidget(self.active_elapsed_label, stretch=1)
        active_meta_layout.addWidget(self.active_eta_label, stretch=1)
        active_layout.addWidget(self.active_job_title)
        active_layout.addWidget(self.active_progress_bar)
        active_layout.addLayout(active_meta_layout)
        right.addWidget(active_group, stretch=0)

        compare_group = QtWidgets.QGroupBox("Before / After Preview")
        compare_layout = QtWidgets.QVBoxLayout(compare_group)
        compare_controls = QtWidgets.QHBoxLayout()
        self.compare_prev_button = QtWidgets.QPushButton("Prev")
        self.compare_prev_button.clicked.connect(self.show_previous_compare_pair)
        self.compare_next_button = QtWidgets.QPushButton("Next")
        self.compare_next_button.clicked.connect(self.show_next_compare_pair)
        self.compare_refresh_button = QtWidgets.QPushButton("Refresh Pairs")
        self.compare_refresh_button.clicked.connect(self.refresh_compare_pairs)
        self.compare_pair_label = QtWidgets.QLabel("No image pair loaded.")
        self.compare_pair_label.setWordWrap(True)
        compare_controls.addWidget(self.compare_prev_button)
        compare_controls.addWidget(self.compare_next_button)
        compare_controls.addWidget(self.compare_refresh_button)
        compare_controls.addWidget(self.compare_pair_label, stretch=1)
        compare_layout.addLayout(compare_controls)
        self.compare_viewer = ImageCompareViewer()
        self.compare_viewer.setObjectName("compareViewer")
        compare_layout.addWidget(self.compare_viewer)
        right.addWidget(compare_group, stretch=3)

        queue_group = QtWidgets.QGroupBox("Queue")
        queue_layout = QtWidgets.QVBoxLayout(queue_group)
        self.queue_table = QtWidgets.QTableWidget(0, 7)
        self.queue_table.setHorizontalHeaderLabels(["ID", "Type", "Status", "Stage", "Progress", "Input", "Output"])
        self.queue_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.queue_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.queue_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        queue_layout.addWidget(self.queue_table)
        right.addWidget(queue_group, stretch=2)

        logs_group = QtWidgets.QGroupBox("Logs")
        logs_layout = QtWidgets.QVBoxLayout(logs_group)
        self.log_box = QtWidgets.QPlainTextEdit()
        self.log_box.setReadOnly(True)
        logs_layout.addWidget(self.log_box)
        right.addWidget(logs_group, stretch=2)

        self.toggle_video_advanced(False)
        self.toggle_image_advanced(False)
        self.toggle_tools(False)
        self._update_compare_buttons()
        self._set_active_job_panel("No active job.", 0, "waiting", None, None)
        self.statusBar().showMessage("Ready")

    def _apply_style(self) -> None:
        palette = self.palette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#f4efe7"))
        palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#1f2933"))
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor("#fbf8f3"))
        palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#efe6da"))
        palette.setColor(QtGui.QPalette.Button, QtGui.QColor("#eadfce"))
        palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#1f2933"))
        palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#bc5b3c"))
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
        self.setPalette(palette)
        self.setStyleSheet(
            """
            QWidget { font-family: Segoe UI; font-size: 10pt; }
            QGroupBox {
                border: 1px solid #d6cab9;
                border-radius: 12px;
                margin-top: 12px;
                padding: 14px 12px 12px 12px;
                font-weight: 600;
                background: #fffaf4;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QLabel#muted {
                color: #5e6b73;
            }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget {
                border: 1px solid #d8cab8;
                border-radius: 8px;
                padding: 4px 8px;
                background: #fffdf9;
                min-height: 22px;
            }
            QPushButton {
                border-radius: 8px;
                padding: 8px 12px;
                background: #eadfce;
                border: 1px solid #d5c1ab;
                min-height: 24px;
            }
            QPushButton#primary {
                background: #bc5b3c;
                color: white;
                border: 1px solid #a14b30;
                font-weight: 700;
            }
            QToolButton {
                border: none;
                color: #6e5139;
                font-weight: 600;
                padding: 4px 0;
                text-align: left;
            }
            QToolButton:hover {
                color: #bc5b3c;
            }
            QWidget#compareViewer {
                border: 1px solid #d8cab8;
                border-radius: 12px;
                background: #fffdf9;
            }
            QProgressBar {
                border: 1px solid #d8cab8;
                border-radius: 8px;
                background: #fffdf9;
                min-height: 22px;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 7px;
                background: #bc5b3c;
            }
            QPushButton:hover { background: #e0d0bc; }
            QPushButton#primary:hover { background: #a85135; }
            """
        )

    def _populate_profiles(self) -> None:
        for name in PROFILES:
            self.profile_combo.addItem(name)
        self.profile_combo.setCurrentText("Fast FFmpeg 1080p60")
        self.apply_profile("Fast FFmpeg 1080p60")
        for name in IMAGE_PROFILES:
            self.image_profile_combo.addItem(name)
        self.image_profile_combo.setCurrentText("Photo Restore HAT 4x")
        self.apply_image_profile("Photo Restore HAT 4x")

    def _load_settings_into_form(self) -> None:
        self.ffmpeg_edit.setText(self.settings.tool_paths.ffmpeg)
        self.ffprobe_edit.setText(self.settings.tool_paths.ffprobe)
        self.realesrgan_edit.setText(self.settings.tool_paths.realesrgan)
        self.rife_edit.setText(self.settings.tool_paths.rife)
        self.hat_edit.setText(self.settings.tool_paths.hat)

    def refresh_tool_status(self) -> None:
        self.settings.tool_paths.ffmpeg = self.ffmpeg_edit.text().strip() or self.settings.tool_paths.ffmpeg
        self.settings.tool_paths.ffprobe = self.ffprobe_edit.text().strip() or self.settings.tool_paths.ffprobe
        self.settings.tool_paths.realesrgan = self.realesrgan_edit.text().strip()
        self.settings.tool_paths.rife = self.rife_edit.text().strip()
        self.settings.tool_paths.hat = self.hat_edit.text().strip()
        refresh_tool_paths(self.settings)
        self._load_settings_into_form()
        save_settings(self.settings)
        self.log(f"Tools refreshed. FFmpeg: {self.settings.tool_paths.ffmpeg}")

    def apply_profile(self, profile_name: str) -> None:
        profile = PROFILES[profile_name]
        self.profile_label.setText(profile.description)
        self._set_combo_by_data(self.target_height_combo, profile.target_height)
        self._set_combo_by_data(self.target_fps_combo, profile.target_fps)
        self._set_combo_by_data(self.upscale_backend_combo, profile.upscale_backend)
        self._set_combo_by_data(self.interp_backend_combo, profile.interpolation_backend)
        self._set_combo_by_text(self.upscale_model_combo, profile.upscale_model)
        self._set_combo_by_text(self.interp_model_combo, profile.interpolation_model)
        self._set_combo_by_data(self.encoder_combo, profile.encoder)
        self.denoise_spin.setValue(profile.denoise_strength)
        self.sharpen_spin.setValue(profile.sharpen_strength)
        self.grain_spin.setValue(profile.grain_strength)
        self.deband_check.setChecked(profile.use_deband)
        self.temporal_spin.setValue(profile.temporal_strength)
        self.cq_spin.setValue(profile.cq_value)
        self.video_brightness_spin.setValue(1.0)
        self.video_contrast_spin.setValue(1.0)
        self.video_saturation_spin.setValue(1.0)
        self.video_gamma_spin.setValue(1.0)

    def apply_image_profile(self, profile_name: str) -> None:
        profile = IMAGE_PROFILES[profile_name]
        self.image_profile_label.setText(profile["description"])
        self._set_combo_by_data(self.image_scale_combo, profile["image_scale"])
        self._set_combo_by_data(self.image_backend_combo, profile["upscale_backend"])
        self._set_combo_by_text(self.image_model_combo, profile["upscale_model"])
        self._set_combo_by_data(self.image_format_combo, profile["image_output_format"])
        self.image_denoise_spin.setValue(profile["denoise_strength"])
        self.image_sharpen_spin.setValue(profile["sharpen_strength"])
        self.image_brightness_spin.setValue(profile["brightness"])
        self.image_contrast_spin.setValue(profile["contrast"])
        self.image_saturation_spin.setValue(profile["saturation"])
        self.image_gamma_spin.setValue(profile["gamma"])
        self.image_autocontrast_check.setChecked(profile["auto_contrast"])

    def _set_combo_by_data(self, combo: QtWidgets.QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_combo_by_text(self, combo: QtWidgets.QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_layout_row_visible(self, layout: QtWidgets.QGridLayout, row: int, visible: bool) -> None:
        for column in range(layout.columnCount()):
            item = layout.itemAtPosition(row, column)
            if item and item.widget():
                item.widget().setVisible(visible)
            elif item and item.layout():
                for index in range(item.layout().count()):
                    child = item.layout().itemAt(index)
                    if child and child.widget():
                        child.widget().setVisible(visible)

    def toggle_video_advanced(self, checked: bool) -> None:
        for row in range(5, 15):
            self._set_layout_row_visible(self.form_layout, row, checked)
        self.video_advanced_button.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
        )
        self.video_advanced_button.setText("Hide advanced video controls" if checked else "Show advanced video controls")

    def toggle_image_advanced(self, checked: bool) -> None:
        for row in range(5, 11):
            self._set_layout_row_visible(self.image_layout, row, checked)
        self.image_advanced_button.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
        )
        self.image_advanced_button.setText("Hide advanced image controls" if checked else "Show advanced image controls")

    def toggle_tools(self, checked: bool) -> None:
        self.tools_group.setVisible(checked)
        self.tools_toggle_button.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
        )
        self.tools_toggle_button.setText("Hide tools and paths" if checked else "Show tools and paths")

    def _format_seconds(self, seconds: float | None) -> str:
        if seconds is None or seconds < 0:
            return "--:--"
        total = int(round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _set_active_job_panel(self, title: str, progress: int, stage: str, elapsed: float | None, eta: float | None) -> None:
        self.active_job_title.setText(title)
        self.active_progress_bar.setValue(max(0, min(progress, 100)))
        self.active_stage_label.setText(f"Stage: {stage}")
        self.active_elapsed_label.setText(f"Elapsed: {self._format_seconds(elapsed)}")
        self.active_eta_label.setText(f"ETA: {self._format_seconds(eta)}")

    def refresh_active_job_eta(self) -> None:
        if not self.active_job or self.active_job_started_at is None:
            return
        elapsed = max(0.0, time.monotonic() - self.active_job_started_at)
        progress = max(0, min(self.active_job.progress, 100))
        eta: float | None = None
        if progress > 0 and progress < 100:
            eta = elapsed * (100 - progress) / progress
        self._set_active_job_panel(
            f"{self.active_job.id} • {self.active_job.options.input_path.name}",
            progress,
            self.active_job.stage,
            elapsed,
            eta,
        )

    def sync_single_image_output_suffix(self) -> None:
        input_text = self.image_input_edit.text().strip()
        output_text = self.image_output_edit.text().strip()
        if not input_text or not output_text:
            return
        input_path = Path(input_text)
        output_path = Path(output_text)
        if not input_path.exists() or not input_path.is_file():
            return
        suffix = f".{self.image_format_combo.currentData()}"
        if output_path.suffix:
            self.image_output_edit.setText(str(output_path.with_suffix(suffix)))

    def refresh_compare_pairs(self) -> None:
        input_text = self.image_input_edit.text().strip()
        output_text = self.image_output_edit.text().strip()
        if not input_text or not output_text:
            self.compare_pairs = []
            self.current_compare_index = 0
            self._show_compare_pair()
            return

        input_path = Path(input_text)
        output_path = Path(output_text)
        if not input_path.exists() or not output_path.exists():
            self.compare_pairs = []
            self.current_compare_index = 0
            self._show_compare_pair()
            return

        self.compare_pairs = self._build_compare_pairs(input_path, output_path)
        if self.current_compare_index >= len(self.compare_pairs):
            self.current_compare_index = max(0, len(self.compare_pairs) - 1)
        self._show_compare_pair()

    def _build_compare_pairs(self, input_path: Path, output_path: Path) -> list[tuple[Path, Path]]:
        if input_path.is_file():
            if output_path.is_file():
                return [(input_path, output_path)] if output_path.exists() else []
            if output_path.is_dir():
                output_files = list_image_files(output_path)
                candidate = next((path for path in output_files if path.stem.startswith(f"{input_path.stem}_lifted")), None)
                return [(input_path, candidate)] if candidate else []
            return []

        if not output_path.is_dir():
            return []

        source_files = list_image_files(input_path)
        output_files = list_image_files(output_path)
        output_by_stem = {path.stem: path for path in output_files}
        pairs: list[tuple[Path, Path]] = []

        for source in source_files:
            candidate = output_by_stem.get(f"{source.stem}_lifted")
            if not candidate:
                candidate = next((path for path in output_files if path.stem.startswith(f"{source.stem}_lifted")), None)
            if candidate:
                pairs.append((source, candidate))
        return pairs

    def _show_compare_pair(self) -> None:
        if not self.compare_pairs:
            self.compare_viewer.clear()
            self.compare_pair_label.setText("No image pair loaded.")
            self._update_compare_buttons()
            return

        before_path, after_path = self.compare_pairs[self.current_compare_index]
        self.compare_viewer.set_images(before_path, after_path)
        self.compare_pair_label.setText(
            f"{self.current_compare_index + 1}/{len(self.compare_pairs)}  {before_path.name} -> {after_path.name}"
        )
        self._update_compare_buttons()

    def _update_compare_buttons(self) -> None:
        has_pairs = bool(self.compare_pairs)
        self.compare_prev_button.setEnabled(has_pairs and self.current_compare_index > 0)
        self.compare_next_button.setEnabled(has_pairs and self.current_compare_index < len(self.compare_pairs) - 1)

    def show_previous_compare_pair(self) -> None:
        if not self.compare_pairs:
            return
        self.current_compare_index = max(0, self.current_compare_index - 1)
        self._show_compare_pair()

    def show_next_compare_pair(self) -> None:
        if not self.compare_pairs:
            return
        self.current_compare_index = min(len(self.compare_pairs) - 1, self.current_compare_index + 1)
        self._show_compare_pair()

    def open_result_viewer(self, job: JobRecord) -> None:
        if job.options.job_kind == "image":
            pairs = self._build_compare_pairs(job.options.input_path, job.options.output_path)
            if not pairs:
                return
            dialog = ImageComparisonDialog(pairs, parent=self)
        else:
            if not job.options.input_path.exists() or not job.options.output_path.exists():
                return
            dialog = VideoComparisonDialog(job.options.input_path, job.options.output_path, parent=self)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.finished.connect(lambda _result, d=dialog: self.preview_dialogs.remove(d) if d in self.preview_dialogs else None)
        self.preview_dialogs.append(dialog)
        dialog.show()

    def pick_input(self) -> None:
        start_dir = self.settings.last_input_dir or str(Path.home())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select input video",
            start_dir,
            "Video Files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v);;All Files (*.*)",
        )
        if not file_path:
            return
        self.input_edit.setText(file_path)
        self.settings.last_input_dir = str(Path(file_path).parent)
        if not self.output_edit.text().strip():
            self.output_edit.setText(str(Path(file_path).with_name(Path(file_path).stem + "_upscaled.mp4")))
        save_settings(self.settings)
        self.analyze_input()

    def pick_output(self) -> None:
        start_dir = self.settings.last_output_dir or str(Path.home())
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select output file",
            start_dir,
            "MP4 Video (*.mp4);;MKV Video (*.mkv);;All Files (*.*)",
        )
        if not file_path:
            return
        self.output_edit.setText(file_path)
        self.settings.last_output_dir = str(Path(file_path).parent)
        save_settings(self.settings)

    def analyze_input(self) -> None:
        input_path = Path(self.input_edit.text().strip())
        if not input_path.exists():
            self.metadata_box.setPlainText("Select a valid input file first.")
            return
        try:
            meta = probe_video(self.ffprobe_edit.text().strip() or "ffprobe", input_path)
        except Exception as exc:
            self.metadata_box.setPlainText(f"Analysis failed: {exc}")
            return
        message = (
            f"Resolution: {meta.width}x{meta.height}\n"
            f"FPS: {meta.fps:.3f}\n"
            f"Duration: {meta.duration:.2f}s\n"
            f"Frames: ~{meta.frame_count}\n"
            f"Video codec: {meta.video_codec or 'unknown'}\n"
            f"Audio: {'yes' if meta.has_audio else 'no'} ({meta.audio_codec or 'n/a'})"
        )
        self.metadata_box.setPlainText(message)

    def pick_image_file(self) -> None:
        start_dir = self.settings.last_input_dir or str(Path.home())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select image",
            start_dir,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;All Files (*.*)",
        )
        if not file_path:
            return
        self.image_input_edit.setText(file_path)
        self.settings.last_input_dir = str(Path(file_path).parent)
        save_settings(self.settings)
        self.analyze_image_input()
        self.refresh_compare_pairs()

    def pick_image_folder(self) -> None:
        start_dir = self.settings.last_input_dir or str(Path.home())
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select image folder", start_dir)
        if not folder:
            return
        self.image_input_edit.setText(folder)
        self.settings.last_input_dir = folder
        save_settings(self.settings)
        self.analyze_image_input()
        self.refresh_compare_pairs()

    def pick_image_output_target(self) -> None:
        start_dir = self.settings.last_output_dir or str(Path.home())
        input_path = Path(self.image_input_edit.text().strip())
        if input_path.exists() and input_path.is_file():
            default_target = self.image_output_edit.text().strip() or str(
                input_path.with_name(f"{input_path.stem}_lifted.{self.image_format_combo.currentData()}")
            )
            file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Select output image",
                default_target,
                "Images (*.png *.jpg *.jpeg *.webp);;All Files (*.*)",
            )
            if not file_path:
                return
            self.image_output_edit.setText(file_path)
            self.settings.last_output_dir = str(Path(file_path).parent)
        else:
            folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder", start_dir)
            if not folder:
                return
            self.image_output_edit.setText(folder)
            self.settings.last_output_dir = folder
        save_settings(self.settings)
        self.refresh_compare_pairs()

    def analyze_image_input(self) -> None:
        input_path = Path(self.image_input_edit.text().strip())
        if not input_path.exists():
            self.image_meta_box.setPlainText("Select an image file or folder first.")
            return
        files = list_image_files(input_path)
        if not files:
            self.image_meta_box.setPlainText("No supported images found.")
            return
        probe = QtGui.QImage(str(files[0]))
        message = (
            f"Images found: {len(files)}\n"
            f"Sample: {files[0].name}\n"
            f"Sample resolution: {probe.width()}x{probe.height()}\n"
            f"Mode: {'batch folder' if input_path.is_dir() else 'single image'}"
        )
        self.image_meta_box.setPlainText(message)
        if not self.image_output_edit.text().strip():
            if input_path.is_file():
                self.image_output_edit.setText(
                    str(files[0].with_name(f"{files[0].stem}_lifted.{self.image_format_combo.currentData()}"))
                )
            else:
                self.image_output_edit.setText(str(files[0].parent / "upscaled_images"))
        self.refresh_compare_pairs()

    def collect_job_options(self) -> JobOptions:
        input_path = Path(self.input_edit.text().strip())
        output_path = Path(self.output_edit.text().strip())
        if not input_path.exists():
            raise ValueError("Input video does not exist.")
        if not output_path.name:
            raise ValueError("Output file is required.")
        return JobOptions(
            input_path=input_path,
            output_path=output_path,
            profile_name=self.profile_combo.currentText(),
            target_height=int(self.target_height_combo.currentData()),
            target_fps=float(self.target_fps_combo.currentData()),
            denoise_strength=self.denoise_spin.value(),
            sharpen_strength=self.sharpen_spin.value(),
            grain_strength=self.grain_spin.value(),
            use_deband=self.deband_check.isChecked(),
            upscale_backend=str(self.upscale_backend_combo.currentData()),
            interpolation_backend=str(self.interp_backend_combo.currentData()),
            upscale_model=self.upscale_model_combo.currentText(),
            interpolation_model=self.interp_model_combo.currentText(),
            encoder=str(self.encoder_combo.currentData()),
            cq_value=self.cq_spin.value(),
            keep_temp=self.keep_temp_check.isChecked(),
            temporal_strength=self.temporal_spin.value(),
            brightness=self.video_brightness_spin.value(),
            contrast=self.video_contrast_spin.value(),
            saturation=self.video_saturation_spin.value(),
            gamma=self.video_gamma_spin.value(),
        )

    def collect_image_job_options(self) -> JobOptions:
        input_path = Path(self.image_input_edit.text().strip())
        output_path = Path(self.image_output_edit.text().strip())
        if not input_path.exists():
            raise ValueError("Input image or folder does not exist.")
        if not output_path.name:
            raise ValueError("Output target is required.")

        image_output_format = str(self.image_format_combo.currentData())
        if input_path.is_file():
            if output_path.suffix:
                image_output_format = output_path.suffix.lstrip(".").lower()
            else:
                output_path = output_path / f"{input_path.stem}_lifted.{image_output_format}"
        elif output_path.suffix:
            raise ValueError("For batch folders, output must be a folder.")

        return JobOptions(
            input_path=input_path,
            output_path=output_path,
            profile_name=self.image_profile_combo.currentText(),
            target_height=0,
            target_fps=0.0,
            denoise_strength=self.image_denoise_spin.value(),
            sharpen_strength=self.image_sharpen_spin.value(),
            grain_strength=0.0,
            use_deband=False,
            upscale_backend=str(self.image_backend_combo.currentData()),
            interpolation_backend="none",
            upscale_model=self.image_model_combo.currentText(),
            interpolation_model="",
            encoder="libx264",
            cq_value=18,
            keep_temp=False,
            job_kind="image",
            image_scale=int(self.image_scale_combo.currentData()),
            image_output_format=image_output_format,
            brightness=self.image_brightness_spin.value(),
            contrast=self.image_contrast_spin.value(),
            saturation=self.image_saturation_spin.value(),
            gamma=self.image_gamma_spin.value(),
            auto_contrast=self.image_autocontrast_check.isChecked(),
            overwrite=self.image_overwrite_check.isChecked(),
        )

    def enqueue_job(self) -> None:
        try:
            options = self.collect_job_options()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid job", str(exc))
            return
        record = JobRecord(options=options)
        self.jobs.append(record)
        self._append_job_row(record)
        self.log(f"Queued video job {record.id}: {options.input_path.name} -> {options.output_path.name}")

    def enqueue_image_job(self) -> None:
        try:
            options = self.collect_image_job_options()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid job", str(exc))
            return
        record = JobRecord(options=options)
        self.jobs.append(record)
        self._append_job_row(record)
        self.log(f"Queued image job {record.id}: {options.input_path.name} -> {options.output_path}")
        self.refresh_compare_pairs()

    def _append_job_row(self, record: JobRecord) -> None:
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)
        self.queue_table.setItem(row, 0, QtWidgets.QTableWidgetItem(record.id))
        self.queue_table.setItem(row, 1, QtWidgets.QTableWidgetItem(record.options.job_kind))
        self.queue_table.setItem(row, 2, QtWidgets.QTableWidgetItem(record.status))
        self.queue_table.setItem(row, 3, QtWidgets.QTableWidgetItem(record.stage))
        self.queue_table.setItem(row, 4, QtWidgets.QTableWidgetItem(f"{record.progress}%"))
        self.queue_table.setItem(row, 5, QtWidgets.QTableWidgetItem(record.options.input_path.name))
        self.queue_table.setItem(row, 6, QtWidgets.QTableWidgetItem(str(record.options.output_path)))

    def _update_job_row(self, record: JobRecord) -> None:
        for row in range(self.queue_table.rowCount()):
            cell = self.queue_table.item(row, 0)
            if cell and cell.text() == record.id:
                self.queue_table.item(row, 2).setText(record.status)
                self.queue_table.item(row, 3).setText(record.stage)
                self.queue_table.item(row, 4).setText(f"{record.progress}%")
                return

    def start_next_job(self) -> None:
        if self.active_worker:
            return
        pending = next((job for job in self.jobs if job.status == "Queued"), None)
        if not pending:
            self.statusBar().showMessage("No queued jobs.")
            return
        self.active_job = pending
        pending.status = "Running"
        pending.stage = "Preparing"
        self._update_job_row(pending)

        self.settings.tool_paths.ffmpeg = self.ffmpeg_edit.text().strip() or self.settings.tool_paths.ffmpeg
        self.settings.tool_paths.ffprobe = self.ffprobe_edit.text().strip() or self.settings.tool_paths.ffprobe
        self.settings.tool_paths.realesrgan = self.realesrgan_edit.text().strip()
        self.settings.tool_paths.rife = self.rife_edit.text().strip()
        self.settings.tool_paths.hat = self.hat_edit.text().strip()
        save_settings(self.settings)

        worker = JobWorker(replace(self.settings), pending.options)
        worker.progress_changed.connect(self.on_job_progress)
        worker.log_line.connect(self.log)
        worker.completed.connect(self.on_job_completed)
        worker.failed.connect(self.on_job_failed)
        self.active_worker = worker
        self.active_job_started_at = time.monotonic()
        self.progress_timer.start()
        self._set_active_job_panel(
            f"{pending.id} • {pending.options.input_path.name}",
            pending.progress,
            pending.stage,
            0.0,
            None,
        )
        worker.start()
        self.statusBar().showMessage(f"Running job {pending.id}")

    def on_job_progress(self, value: int, stage: str) -> None:
        if not self.active_job:
            return
        self.active_job.progress = value
        self.active_job.stage = stage
        self._update_job_row(self.active_job)
        self.refresh_active_job_eta()
        self.statusBar().showMessage(f"{self.active_job.id}: {stage} ({value}%)")

    def on_job_completed(self) -> None:
        if not self.active_job:
            return
        completed_job = self.active_job
        self.active_job.progress = 100
        self.active_job.status = "Done"
        self.active_job.stage = "Completed"
        self._update_job_row(self.active_job)
        self.log(f"Job {self.active_job.id} completed.")
        self.active_worker = None
        self.active_job = None
        self.progress_timer.stop()
        self.active_job_started_at = None
        self._set_active_job_panel(f"Last job completed: {completed_job.id}", 100, "completed", None, None)
        if completed_job.options.job_kind == "image":
            self.refresh_compare_pairs()
        self.open_result_viewer(completed_job)
        self.statusBar().showMessage("Job completed")
        self.start_next_job()

    def on_job_failed(self, message: str) -> None:
        if not self.active_job:
            return
        status = "Cancelled" if "cancel" in message.lower() else "Failed"
        self.active_job.status = status
        self.active_job.stage = message
        self.active_job.error = message
        self._update_job_row(self.active_job)
        self.log(f"Job {self.active_job.id} failed: {message}")
        self.active_worker = None
        self.active_job = None
        self.progress_timer.stop()
        self.active_job_started_at = None
        self._set_active_job_panel("Last job failed.", 0, message, None, None)
        self.statusBar().showMessage(message)
        self.start_next_job()

    def cancel_active_job(self) -> None:
        if self.active_worker:
            self.active_worker.cancel()
            self.log("Cancellation requested.")

    def install_tool(self, tool_key: str) -> None:
        worker = ToolInstallWorker(tool_key)
        worker.progress_changed.connect(lambda value, stage: self.statusBar().showMessage(f"{stage} ({value}%)"))
        worker.completed.connect(lambda path, key=tool_key: self.on_tool_installed(key, path))
        worker.failed.connect(lambda message, key=tool_key: self.on_tool_failed(key, message))
        worker.finished.connect(lambda w=worker: self.install_workers.remove(w) if w in self.install_workers else None)
        self.install_workers.append(worker)
        worker.start()
        self.log(f"Downloading {tool_key} from GitHub releases...")

    def on_tool_installed(self, tool_key: str, exe_path: str) -> None:
        if tool_key == "realesrgan":
            self.realesrgan_edit.setText(exe_path)
            self.settings.tool_paths.realesrgan = exe_path
        elif tool_key == "rife":
            self.rife_edit.setText(exe_path)
            self.settings.tool_paths.rife = exe_path
        elif tool_key == "hat":
            self.hat_edit.setText(exe_path)
            self.settings.tool_paths.hat = exe_path
        save_settings(self.settings)
        self.statusBar().showMessage(f"{tool_key} ready")
        self.log(f"{tool_key} installed: {exe_path}")
        if tool_key == "realesrgan":
            self.log("Real-ESRGAN portable package installed with executable and model files.")
        if tool_key == "hat":
            self.log("HAT backend installed with official repository and pretrained image models.")

    def on_tool_failed(self, tool_key: str, message: str) -> None:
        self.statusBar().showMessage(f"{tool_key} install failed")
        self.log(f"{tool_key} install failed: {message}")
        QtWidgets.QMessageBox.warning(self, "Tool install failed", f"{tool_key}: {message}")

    def log(self, message: str) -> None:
        self.log_box.appendPlainText(message)
        scroll = self.log_box.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.settings.tool_paths.ffmpeg = self.ffmpeg_edit.text().strip() or self.settings.tool_paths.ffmpeg
        self.settings.tool_paths.ffprobe = self.ffprobe_edit.text().strip() or self.settings.tool_paths.ffprobe
        self.settings.tool_paths.realesrgan = self.realesrgan_edit.text().strip()
        self.settings.tool_paths.rife = self.rife_edit.text().strip()
        self.settings.tool_paths.hat = self.hat_edit.text().strip()
        save_settings(self.settings)
        super().closeEvent(event)
