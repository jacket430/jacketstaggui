import os
from pathlib import Path
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QDockWidget,
                               QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QProgressBar, QScrollArea, QTextEdit,
                               QVBoxLayout, QWidget)

from dialogs.generate_sidecars_dialog import GenerateSidecarsDialog
from models.image_list_model import ImageListModel
from utils.big_widgets import TallPushButton
from utils.image import Image
from utils.settings import get_settings
from utils.settings_widgets import SettingsBigCheckBox, SettingsPlainTextEdit
from utils.sidecar_generation_thread import SidecarGenerationThread
from utils.utils import set_text_edit_height

GENERATE_BUTTON_TEXT = 'Generate XMP Sidecars'
CANCEL_BUTTON_TEXT = 'Cancel'
DEFAULT_BLACKLIST_TAGS = [
    'blurry', 'low quality', 'bad quality', 'worst quality', 'jpeg artifacts',
    'watermark', 'text', 'signature', 'username', 'logo',
    'image', 'picture', 'photo', 'art', 'artwork', 'drawing', 'painting',
    'digital art', 'digital painting', 'illustration', 'sketch',
    'ai generated', 'artificial intelligence', 'machine learning',
    'deep learning', 'neural network', 'gan', 'stable diffusion',
    'midjourney', 'dalle', 'openai', 'automatic1111'
]


class XmpSidecarGeneratorWidget(QDockWidget):
    """A widget for generating XMP sidecar files for images."""

    def __init__(self, parent=None, image_list_model: ImageListModel = None):
        super().__init__(parent)
        self.image_list_model = image_list_model
        self.settings = get_settings()
        self.generation_thread = None
        self.is_generating = False
        self.selected_image_indices = []

        # Each `QDockWidget` needs a unique object name for saving its state.
        self.setObjectName('xmp_sidecar_generator')
        self.setWindowTitle('XMP Sidecar Generator')
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea
                             | Qt.DockWidgetArea.RightDockWidgetArea)

        self.create_widgets()
        self.create_layout()
        self.connect_signals()

        self.update_file_count()
        self.update_blacklist_text()

    def create_widgets(self):
        """Create all widgets for the dock."""
        self.generate_cancel_button = TallPushButton(GENERATE_BUTTON_TEXT)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat('%v / %m images processed (%p%)')
        self.progress_bar.hide()

        self.status_label = QLabel('Ready to generate XMP sidecars')
        self.status_label.hide()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.hide()

        self.settings_form_layout = self._create_settings_form()

        # Initially disable generate button until we have files to process.
        try:
            can_process = bool(self._get_files_to_process())
            self.generate_cancel_button.setEnabled(can_process)
        except Exception:
            # If there's any error accessing the model, disable the button.
            self.generate_cancel_button.setEnabled(False)

    def _create_settings_form(self) -> QFormLayout:
        """Create the form layout for sidecar generation settings."""
        form_layout = QFormLayout()
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # File selection options
        self.only_no_sidecars_checkbox = SettingsBigCheckBox(
            key='xmp_only_no_sidecars', default=False)
        form_layout.addRow('Only process files without sidecars',
                                   self.only_no_sidecars_checkbox)

        # Overwrite option
        self.overwrite_checkbox = SettingsBigCheckBox(
            key='xmp_overwrite', default=False)
        form_layout.addRow('Overwrite existing XMP files',
                                   self.overwrite_checkbox)

        # Use blacklist option
        self.use_blacklist_checkbox = SettingsBigCheckBox(
            key='xmp_use_default_blacklist', default=True)
        form_layout.addRow('Use blacklist', self.use_blacklist_checkbox)

        # Custom blacklist tags
        self.blacklist_form_container = QWidget()
        blacklist_form = QFormLayout(self.blacklist_form_container)
        blacklist_form.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapAllRows)
        blacklist_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.custom_blacklist_text = SettingsPlainTextEdit(
            key='xmp_blacklist_text')
        self.custom_blacklist_text.setPlaceholderText(
            'Enter custom blacklisted tags...')
        blacklist_form.addRow('Blacklist tags (one per line):',
                                self.custom_blacklist_text)
        set_text_edit_height(self.custom_blacklist_text, 4)

        self.reset_blacklist_button = TallPushButton('Reset to Default')
        blacklist_form.addRow(self.reset_blacklist_button)

        form_layout.addRow(self.blacklist_form_container)
        return form_layout

    def create_layout(self):
        """Create the main layout for the dock."""
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(self.generate_cancel_button)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addWidget(self.log_text)
        layout.addLayout(self.settings_form_layout)
        layout.addStretch()

        # A scroll area is used to ensure all settings are visible,
        # even on smaller screens.
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(container)
        self.setWidget(scroll_area)

    def connect_signals(self):
        """Connect widget signals to appropriate slots."""
        self.only_no_sidecars_checkbox.toggled.connect(self.update_file_count)
        self.use_blacklist_checkbox.toggled.connect(
            self.update_blacklist_text)
        self.reset_blacklist_button.clicked.connect(
            self.reset_blacklist_to_default)
        self.generate_cancel_button.clicked.connect(
            self.generate_or_cancel_sidecars)

    def _get_files_to_process(self) -> List[Image]:
        """Get the list of images to process based on current settings."""
        if not self.image_list_model or not hasattr(
                self.image_list_model, 'images') or not self.image_list_model.images:
            return []

        if self.selected_image_indices:
            files = [self.image_list_model.images[i]
                     for i in self.selected_image_indices
                     if i < len(self.image_list_model.images)]
        else:
            files = []

        if self.only_no_sidecars_checkbox.isChecked():
            files = [img for img in files if not img.has_xmp]

        return files

    def set_selected_image_indices(self, selected_indices: List):
        """Set the selected image indices from the main window's image list."""
        if selected_indices:
            self.selected_image_indices = [
                idx.row() if hasattr(idx, 'row') else idx
                for idx in selected_indices
            ]
        else:
            self.selected_image_indices = []
        self.update_file_count()

    @Slot()
    def update_file_count(self):
        """Update the generate button state based on available files."""
        try:
            if not self.image_list_model:
                self.generate_cancel_button.setEnabled(False)
                return

            files_to_process = self._get_files_to_process()
            files_with_tags = [img for img in files_to_process if img.tags]

            self.generate_cancel_button.setEnabled(bool(files_with_tags))
        except Exception as e:
            # If there's any error, disable the button.
            self.generate_cancel_button.setEnabled(False)

    @Slot()
    def update_blacklist_text(self):
        """Update the blacklist text box based on the checkbox state."""
        use_blacklist = self.use_blacklist_checkbox.isChecked()
        self.blacklist_form_container.setVisible(use_blacklist)

        if use_blacklist and not self.custom_blacklist_text.toPlainText():
            self.custom_blacklist_text.setPlainText(
                '\n'.join(DEFAULT_BLACKLIST_TAGS))

    @Slot()
    def reset_blacklist_to_default(self):
        """Reset the blacklist tags to the default list."""
        self.custom_blacklist_text.setPlainText(
            '\n'.join(DEFAULT_BLACKLIST_TAGS))

    @Slot()
    def generate_or_cancel_sidecars(self):
        """Handle both generate and cancel operations based on current state."""
        if self.is_generating:
            if self.generation_thread:
                self.generation_thread.stop()
            self.status_label.setText('Cancelling operation...')
            self.generate_cancel_button.setText('Cancelling...')
            self.generate_cancel_button.setEnabled(False)
        else:
            self.generate_sidecars()

    @Slot()
    def generate_sidecars(self):
        """Generate XMP sidecar files for the selected images."""
        files_to_process = self._get_files_to_process()
        files_with_tags = [img for img in files_to_process if img.tags]

        if not files_with_tags:
            QMessageBox.warning(self, 'No Files to Process',
                                'No files with tags were found to process.')
            return

        overwrite = self.overwrite_checkbox.isChecked()
        use_blacklist = self.use_blacklist_checkbox.isChecked()
        blacklist_file = None if use_blacklist else 'DISABLED'
        format_type = 'xmp'

        custom_blacklist_tags = None
        if use_blacklist:
            custom_blacklist_text = self.custom_blacklist_text.toPlainText().strip()
            if custom_blacklist_text:
                custom_blacklist_tags = [
                    line.strip() for line in custom_blacklist_text.split('\n')
                    if line.strip()]

        files_without_sidecars = [
            img for img in files_with_tags if not img.has_xmp]
        sidecars_to_generate = (len(files_with_tags) if overwrite
                                else len(files_without_sidecars))

        confirmation_dialog = GenerateSidecarsDialog(
            images_to_process_count=len(files_with_tags),
            sidecars_to_generate_count=sidecars_to_generate)
        if confirmation_dialog.exec() != QMessageBox.StandardButton.Yes:
            return

        show_alert_when_finished = (
            confirmation_dialog.show_alert_check_box.isChecked())

        self.is_generating = True
        self.generate_cancel_button.setText(CANCEL_BUTTON_TEXT)
        self.progress_bar.show()
        self.status_label.show()
        self.log_text.show()

        self.start_generation(files_with_tags, format_type, overwrite,
                              blacklist_file, custom_blacklist_tags,
                              show_alert_when_finished)

    def start_generation(self, files: List[Image], format_type: str,
                         overwrite: bool, blacklist_file: Optional[str],
                         custom_blacklist_tags: Optional[List[str]] = None,
                         show_alert_when_finished: bool = False):
        """Start the XMP sidecar generation process."""
        self.generate_cancel_button.setEnabled(True)
        self.progress_bar.setMaximum(len(files))
        self.progress_bar.setValue(0)
        self.status_label.setText('Starting generation...')
        self.log_text.clear()

        self.generation_thread = SidecarGenerationThread(
            files=files,
            format_type=format_type,
            overwrite=overwrite,
            blacklist_file=blacklist_file,
            custom_blacklist_tags=custom_blacklist_tags
        )

        self.generation_thread.progress_updated.connect(self.update_progress)
        self.generation_thread.log_updated.connect(self.update_log)
        self.generation_thread.sidecars_generated.connect(self.image_list_model.update_sidecar_statuses)
        self.generation_thread.generation_finished.connect(
            lambda processed, errors, cancelled: self.on_generation_finished(
                processed, errors, cancelled, show_alert_when_finished, format_type
            )
        )

        self.generation_thread.start()

    @Slot(int, str)
    def update_progress(self, value: int, text: str):
        """Update the progress bar and status label."""
        self.progress_bar.setValue(value)
        font_metrics = self.status_label.fontMetrics()
        elided_text = font_metrics.elidedText(
            text, Qt.TextElideMode.ElideRight, self.status_label.width())
        self.status_label.setText(elided_text)

    @Slot(str)
    def update_log(self, text: str):
        """Append a message to the log text edit."""
        self.log_text.append(text)

    @Slot(int, int, bool, bool, str)
    def on_generation_finished(self, processed_count: int, error_count: int,
                               cancelled: bool,
                               show_alert_when_finished: bool, format_type: str):
        """Handle the completion of the generation process."""
        self.progress_bar.hide()
        self.status_label.hide()

        if show_alert_when_finished:
            if cancelled:
                title = 'Generation Cancelled'
                summary = (f'XMP Sidecar Generation Cancelled\n\n'
                           f'Successfully processed: {processed_count} files\n'
                           f'Errors: {error_count} files\n'
                           f'Remaining files were not processed.')
            else:
                title = 'Generation Complete'
                summary = (f'XMP Sidecar Generation Complete\n\n'
                           f'Successfully processed: {processed_count} files\n'
                           f'Errors: {error_count} files\n')
            QMessageBox.information(self, title, summary)

        self.is_generating = False
        self.generation_thread = None
        self.generate_cancel_button.setText(GENERATE_BUTTON_TEXT)
        self.generate_cancel_button.setEnabled(True)
        self.update_file_count()
