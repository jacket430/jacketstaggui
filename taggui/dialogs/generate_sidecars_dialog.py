from utils.settings_widgets import SettingsBigCheckBox
from utils.utils import ConfirmationDialog


class GenerateSidecarsDialog(ConfirmationDialog):
    def __init__(self, images_to_process_count: int, sidecars_to_generate_count: int):
        title = 'Generate XMP Sidecars'
        question = (f'Generate XMP sidecar files for {images_to_process_count} '
                    f'images?\n({sidecars_to_generate_count} sidecars will be '
                    f'generated)')
        super().__init__(title=title, question=question)
        self.show_alert_check_box = SettingsBigCheckBox(
            key='show_alert_when_sidecar_generation_finished', default=True,
            text='Show alert when finished')
        self.setCheckBox(self.show_alert_check_box)
        layout = self.layout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)
