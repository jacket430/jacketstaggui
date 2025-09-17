import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from PySide6.QtCore import QThread, Signal

from models.image_list_model import Image
from utils.xmp_sidecar_generator import XmpSidecarGenerator


class SidecarGenerationThread(QThread):
    progress_updated = Signal(int, str)
    log_updated = Signal(str)
    generation_finished = Signal(int, int, bool)  # processed_count, error_count, cancelled
    sidecars_generated = Signal(list)

    def __init__(self, files: List[Image], format_type: str, overwrite: bool,
                 blacklist_file: Optional[str], custom_blacklist_tags: Optional[List[str]] = None,
                 parent=None):
        super().__init__(parent)
        self.files = files
        self.format_type = format_type
        self.overwrite = overwrite
        self.blacklist_file = blacklist_file
        self.custom_blacklist_tags = custom_blacklist_tags
        self.cancelled = False

    def run(self):
        processed_count = 0
        error_count = 0
        successful_images = []
        log_batch = []
        BATCH_SIZE = 50  # Log updates every 50 files

        image_generator = XmpSidecarGenerator(
            blacklist_file=self.blacklist_file,
            custom_blacklist_tags=self.custom_blacklist_tags,
        )

        def process_single_image(image):
            """Worker function to process a single image."""
            try:
                success = image_generator.generate_sidecar(
                    image.path, image.tags, self.format_type, self.overwrite)
                return image, success, None
            except Exception as e:
                return image, False, str(e)

        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as executor:
            future_to_image = {executor.submit(process_single_image, image): image for image in self.files}

            completed = 0
            for future in as_completed(future_to_image):
                if self.cancelled:
                    for f in future_to_image:
                        f.cancel()
                    break

                image, success, error = future.result()
                completed += 1
                
                filename = image.path.name

                if success:
                    processed_count += 1
                    log_batch.append(f"✓ Created {self.format_type} sidecar for {filename}")
                    successful_images.append(image)
                else:
                    error_count += 1
                    if error:
                        log_batch.append(f"✗ Error processing {filename}: {error}")
                    else:
                        log_batch.append(f"✗ Failed to create {self.format_type} sidecar for {filename}")

                # Update progress and logs in batches
                if completed % BATCH_SIZE == 0 or completed == len(self.files):
                    self.log_updated.emit('\n'.join(log_batch))
                    log_batch = []
                    self.progress_updated.emit(completed, f"Processing: {filename} ({completed}/{len(self.files)})")

        if successful_images:
            self.sidecars_generated.emit(successful_images)
            
        # Emit any remaining logs
        if log_batch:
            self.log_updated.emit('\n'.join(log_batch))

        self.generation_finished.emit(processed_count, error_count, self.cancelled)

    def stop(self):
        self.cancelled = True
