"""
XMP Sidecar Generator for TagGUI

This module provides XMP sidecar generation functionality integrated from sidecarcrasher.
It processes images with tags and creates XMP sidecar files that can be read by 
photo management software like digiKam, Lightroom, and Darktable.
"""

from pathlib import Path
from typing import List, Dict, Tuple, Optional
import subprocess
import re
import sys
import tempfile
import uuid
import shutil
import time


class XmpSidecarGenerator:
    def __init__(self, blacklist_file: Optional[str] = None, custom_blacklist_tags: Optional[List[str]] = None):
        self.blacklist = self._load_blacklist(blacklist_file, custom_blacklist_tags)
    
    def _load_blacklist(self, blacklist_file: Optional[str] = None, custom_blacklist_tags: Optional[List[str]] = None) -> set:
        """Load blacklisted tags from file or use default blacklist."""
        if blacklist_file == "DISABLED":
            return set()
            
        default_blacklist = {
            "blurry", "low quality", "bad quality", "worst quality", "jpeg artifacts",
            "watermark", "text", "signature", "username", "logo",
            "image", "picture", "photo", "art", "artwork", "drawing", "painting",
            "digital art", "digital painting", "illustration", "sketch",
            "ai generated", "artificial intelligence", "machine learning",
            "deep learning", "neural network", "gan", "stable diffusion",
            "midjourney", "dalle", "openai", "automatic1111"
        }
        
        # Start with default blacklist
        final_blacklist = default_blacklist.copy()
        
        # Add custom blacklist tags if provided
        if custom_blacklist_tags:
            custom_tags = {tag.strip().lower() for tag in custom_blacklist_tags if tag.strip()}
            final_blacklist = final_blacklist.union(custom_tags)
        
        # Add file-based blacklist if provided
        if blacklist_file:
            try:
                blacklist_path = Path(blacklist_file)
                if blacklist_path.exists():
                    with open(blacklist_path, 'r', encoding='utf-8') as f:
                        file_blacklist = {line.strip().lower() for line in f if line.strip() and not line.startswith('#')}
                    final_blacklist = final_blacklist.union(file_blacklist)
                else:
                    print(f"Blacklist file not found: {blacklist_file}")
                    print("Using default blacklist only.")
            except Exception as e:
                print(f"Error loading blacklist file: {e}")
                print("Using default blacklist only.")
        
        return final_blacklist
    
    def _has_unicode_surrogates(self, filename: str) -> bool:
        """Check if filename contains Unicode surrogate characters (emojis, etc.)."""
        try:
            # Check for actual surrogate characters (0xD800-0xDFFF)
            for char in filename:
                if 0xD800 <= ord(char) <= 0xDFFF:
                    return True
            
            # Check for emoji and other high Unicode characters that might cause issues
            for char in filename:
                # Emojis are typically in these ranges
                if (0x1F600 <= ord(char) <= 0x1F64F or  # Emoticons
                    0x1F300 <= ord(char) <= 0x1F5FF or  # Misc Symbols and Pictographs
                    0x1F680 <= ord(char) <= 0x1F6FF or  # Transport and Map
                    0x1F1E0 <= ord(char) <= 0x1F1FF or  # Regional indicators
                    0x2600 <= ord(char) <= 0x26FF or    # Misc symbols
                    0x2700 <= ord(char) <= 0x27BF or    # Dingbats
                    0xFE00 <= ord(char) <= 0xFE0F or    # Variation selectors
                    0x1F900 <= ord(char) <= 0x1F9FF):   # Supplemental Symbols
                    return True
            
            return False
        except (UnicodeError, ValueError):
            return True
    
    def _create_temp_copy_for_exiftool(self, file_path: Path) -> Tuple[Path, bool]:
        """Create a temporary copy of the file with ASCII-only name for exiftool processing."""
        filename = file_path.name
        
        if not self._has_unicode_surrogates(filename):
            return file_path, False
        
        # Create a temporary file with ASCII-only name
        temp_dir = tempfile.mkdtemp()
        temp_filename = f"temp_exif_{uuid.uuid4().hex}{file_path.suffix}"
        temp_path = Path(temp_dir) / temp_filename
        
        try:
            shutil.copy2(file_path, temp_path)
            print(self._safe_console_text(f"Created temporary copy for exiftool: {temp_path.name}"))
            return temp_path, True
        except Exception as e:
            print(self._safe_console_text(f"Warning: Could not create temporary copy of {filename}: {e}"))
            return file_path, False
    
    def _cleanup_temp_file(self, temp_path: Path):
        """Clean up temporary file and directory."""
        try:
            if temp_path.exists():
                temp_path.unlink()
            temp_dir = temp_path.parent
            if temp_dir.exists() and temp_dir.name.startswith('tmp'):
                temp_dir.rmdir()
        except Exception as e:
            print(self._safe_console_text(f"Warning: Could not clean up temporary file {temp_path}: {e}"))
    
    def _safe_filename_for_subprocess(self, file_path: Path) -> str:
        """Convert Path to string with proper Unicode handling for subprocess calls."""
        try:
            # Try to encode as UTF-8 to check for issues
            str_path = str(file_path)
            str_path.encode('utf-8')
            return str_path
        except UnicodeEncodeError:
            # If encoding fails, try to use the filesystem encoding
            try:
                return str(file_path).encode(sys.getfilesystemencoding(), errors='replace').decode(sys.getfilesystemencoding())
            except:
                # Last resort: use repr to get a safe representation
                return repr(str(file_path))
    
    def _safe_console_text(self, text: str) -> str:
        """Return text safe to print in current console by replacing un-encodable chars."""
        try:
            encoding = sys.stdout.encoding or 'utf-8'
            text.encode(encoding)
            return text
        except Exception:
            try:
                return text.encode('ascii', 'backslashreplace').decode('ascii')
            except Exception:
                return repr(text)
    
    def filter_tags(self, tags: List[str]) -> List[str]:
        """Filter out blacklisted tags from the tag list."""
        filtered_tags = []
        for tag in tags:
            tag_lower = tag.lower().strip()
            if tag_lower and tag_lower not in self.blacklist:
                filtered_tags.append(tag)
            else:
                print(f"Filtered out blacklisted tag: '{tag}'")
        
        return filtered_tags
    
    def read_existing_metadata(self, image_file: Path) -> Dict[str, any]:
        """Read existing metadata from image file using exiftool and extract XMP data."""
        metadata = {
            'existing_xmp': None,
            'existing_tags': [],
            'hierarchical_subjects': [],
            'faces': [],
            'face_regions': [],
            'gps_location': None,
            'all_metadata': {}
        }
        
        try:
            # Create temporary copy if filename has Unicode surrogates
            temp_file, was_copied = self._create_temp_copy_for_exiftool(image_file)
            
            try:
                # Use exiftool to read all metadata from the image file directly
                safe_filename = self._safe_filename_for_subprocess(temp_file)
                result = subprocess.run([
                    'exiftool', '-a', '-G1', '-s', safe_filename
                ], capture_output=True, text=True, check=True, encoding='utf-8', errors='replace')
            finally:
                # Clean up temporary file if we created one
                if was_copied:
                    self._cleanup_temp_file(temp_file)
            
            lines = result.stdout.split('\n')
            face_regions_by_name = {}  # Dictionary to collect face regions by name
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Store all metadata for reference
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()
                    metadata['all_metadata'][key] = value
                    
                    # Also store without group prefix for easier lookup
                    if ']' in key:
                        simple_key = key.split(']', 1)[1].strip()
                        metadata['all_metadata'][simple_key] = value
                
                # Extract GPS location
                if 'GPSPosition' in line:
                    gps_match = re.search(r'GPSPosition\s*:\s*(.+)', line)
                    if gps_match:
                        metadata['gps_location'] = gps_match.group(1).strip()
                
                # Extract existing tags from specific sources only
                if any(tag_field in line for tag_field in ['Keywords', 'Subject', 'TagsList', 'HierarchicalSubject']):
                    tag_match = re.search(r':\s*(.+)', line)
                    if tag_match:
                        tag_content = tag_match.group(1).strip()
                        if tag_content and tag_content != '(none)':
                            # Skip non-tag data like coordinates
                            if re.match(r'^\d+\s+\d+\s+\d+\s+\d+$', tag_content):
                                continue
                            
                            # Handle hierarchical subjects (People|Name format)
                            if '|' in tag_content:
                                if tag_content not in metadata['hierarchical_subjects']:
                                    metadata['hierarchical_subjects'].append(tag_content)
                            else:
                                # Convert People/Name format to People|Name for consistency
                                if '/' in tag_content and not tag_content.startswith('http'):
                                    tag_content = tag_content.replace('/', '|')
                                    if tag_content not in metadata['hierarchical_subjects']:
                                        metadata['hierarchical_subjects'].append(tag_content)
                                elif tag_content not in metadata['existing_tags']:
                                    metadata['existing_tags'].append(tag_content)
                
                # Extract face region data - collect all fields by name
                if 'RegionName' in line or 'RegionPersonDisplayName' in line:
                    face_match = re.search(r'(?:RegionName|RegionPersonDisplayName)\s*:\s*(.+)', line)
                    if face_match:
                        face_name = face_match.group(1).strip()
                        if face_name:
                            if face_name not in face_regions_by_name:
                                face_regions_by_name[face_name] = {'name': face_name}
                                if face_name not in metadata['faces']:
                                    metadata['faces'].append(face_name)
                
                elif 'RegionRectangle' in line:
                    rect_match = re.search(r'RegionRectangle\s*:\s*(.+)', line)
                    if rect_match:
                        # Find the most recent face name to associate this rectangle with
                        for face_name in face_regions_by_name:
                            face_regions_by_name[face_name]['rectangle'] = rect_match.group(1).strip()
                            break
                
                elif 'RegionAreaX' in line:
                    area_match = re.search(r'RegionAreaX\s*:\s*(.+)', line)
                    if area_match:
                        for face_name in face_regions_by_name:
                            face_regions_by_name[face_name]['area_x'] = area_match.group(1).strip()
                            break
                
                elif 'RegionAreaY' in line:
                    area_match = re.search(r'RegionAreaY\s*:\s*(.+)', line)
                    if area_match:
                        for face_name in face_regions_by_name:
                            face_regions_by_name[face_name]['area_y'] = area_match.group(1).strip()
                            break
                
                elif 'RegionAreaW' in line:
                    area_match = re.search(r'RegionAreaW\s*:\s*(.+)', line)
                    if area_match:
                        for face_name in face_regions_by_name:
                            face_regions_by_name[face_name]['area_w'] = area_match.group(1).strip()
                            break
                
                elif 'RegionAreaH' in line:
                    area_match = re.search(r'RegionAreaH\s*:\s*(.+)', line)
                    if area_match:
                        for face_name in face_regions_by_name:
                            face_regions_by_name[face_name]['area_h'] = area_match.group(1).strip()
                            break
                
                elif 'RegionAppliedToDimensionsW' in line:
                    dim_match = re.search(r'RegionAppliedToDimensionsW\s*:\s*(.+)', line)
                    if dim_match:
                        for face_name in face_regions_by_name:
                            face_regions_by_name[face_name]['applied_w'] = dim_match.group(1).strip()
                            break
                
                elif 'RegionAppliedToDimensionsH' in line:
                    dim_match = re.search(r'RegionAppliedToDimensionsH\s*:\s*(.+)', line)
                    if dim_match:
                        for face_name in face_regions_by_name:
                            face_regions_by_name[face_name]['applied_h'] = dim_match.group(1).strip()
                            break
                
                elif 'RegionAppliedToDimensionsUnit' in line:
                    unit_match = re.search(r'RegionAppliedToDimensionsUnit\s*:\s*(.+)', line)
                    if unit_match:
                        for face_name in face_regions_by_name:
                            face_regions_by_name[face_name]['applied_unit'] = unit_match.group(1).strip()
                            break
            
            # Convert face regions to list
            metadata['face_regions'] = list(face_regions_by_name.values())
        
        except subprocess.CalledProcessError as e:
            print(self._safe_console_text(f"Warning: Could not read metadata from {image_file.name}: {e}"))
            if e.stderr:
                print(self._safe_console_text(f"exiftool stderr: {e.stderr}"))
        except FileNotFoundError:
            print(self._safe_console_text("Warning: exiftool not found. Install exiftool to read existing metadata."))
        except UnicodeDecodeError as e:
            print(self._safe_console_text(f"Warning: Unicode encoding error reading metadata from {image_file.name}: {e}"))
            print("This may be due to emoji or special characters in the filename.")
        except Exception as e:
            print(self._safe_console_text(f"Warning: Error reading metadata from {image_file.name}: {e}"))
        
        return metadata
    
    def generate_sidecar(self, image_file: Path, tags: List[str], format_type: str = "xmp", 
                        overwrite: bool = True) -> bool:
        """Generate sidecar file for an image with tags."""
        if not tags:
            print(self._safe_console_text(f"No tags provided for {image_file}"))
            return False
        
        original_count = len(tags)
        tags = self.filter_tags(tags)
        filtered_count = len(tags)
        
        if not tags:
            print(self._safe_console_text(f"All tags were filtered out from {image_file.name}"))
            return False
        
        if original_count != filtered_count:
            print(self._safe_console_text(f"Filtered {original_count - filtered_count} blacklisted tags from {image_file.name}"))
        
        output_dir = image_file.parent
        
        # Delete existing XMP file to prevent conflicts if overwrite is enabled
        if format_type.lower() == "xmp" and overwrite:
            xmp_file = output_dir / f"{image_file.stem}.xmp"
            if xmp_file.exists():
                deleted = False
                for i in range(3):  # Retry up to 3 times
                    try:
                        xmp_file.unlink()
                        deleted = True
                        print(self._safe_console_text(
                            f"Deleted existing XMP file: {xmp_file.name}"))
                        break
                    except Exception as e:
                        print(self._safe_console_text(
                            f"Warning: Could not delete existing XMP file "
                            f"(attempt {i+1}/3): {e}"))
                        time.sleep(0.1)
                
                if not deleted:
                    print(self._safe_console_text(
                        f"Error: Failed to delete existing XMP file "
                        f"{xmp_file.name} after multiple attempts."))
                    return False
        
        # Read existing metadata from image
        print(self._safe_console_text(f"Reading existing metadata from {image_file.name}..."))
        existing_metadata = self.read_existing_metadata(image_file)
        
        if existing_metadata.get('existing_tags'):
            print(self._safe_console_text(f"Found {len(existing_metadata['existing_tags'])} existing tags"))
        if existing_metadata.get('faces'):
            print(self._safe_console_text(f"Found {len(existing_metadata['faces'])} face(s): {', '.join(existing_metadata['faces'])}"))
        if existing_metadata.get('face_regions'):
            print(self._safe_console_text(f"Found {len(existing_metadata['face_regions'])} face region(s) with coordinates"))
        if existing_metadata.get('gps_location'):
            print(self._safe_console_text(f"Found GPS location: {existing_metadata['gps_location']}"))
        
        # Generate sidecar file
        if format_type.lower() == "xmp":
            sidecar_file = output_dir / f"{image_file.stem}.xmp"
            try:
                # Prepare input and output paths for exiftool with Unicode-safe handling
                temp_image_file, was_copied = self._create_temp_copy_for_exiftool(image_file)
                unicode_in_output = self._has_unicode_surrogates(sidecar_file.name)

                temp_sidecar_dir = None
                sidecar_output_path = sidecar_file
                if unicode_in_output or was_copied:
                    temp_sidecar_dir = Path(tempfile.mkdtemp())
                    sidecar_output_path = temp_sidecar_dir / f"temp_{uuid.uuid4().hex}.xmp"

                safe_image_filename = self._safe_filename_for_subprocess(temp_image_file)
                safe_sidecar_filename = self._safe_filename_for_subprocess(sidecar_output_path)

                # Build exiftool command: clone metadata and append tags
                exiftool_cmd = [
                    'exiftool',
                    '-tagsFromFile', safe_image_filename,
                    '-all:all',
                    # TIFF to XMP-tiff
                    '-XMP-tiff:Make<EXIF:Make',
                    '-XMP-tiff:Model<EXIF:Model',
                    '-XMP-tiff:Orientation<EXIF:Orientation',
                    '-XMP-tiff:XResolution<EXIF:XResolution',
                    '-XMP-tiff:YResolution<EXIF:YResolution',
                    '-XMP-tiff:ResolutionUnit<EXIF:ResolutionUnit',
                    # EXIF to XMP-exif
                    '-XMP-exif:ExposureTime<EXIF:ExposureTime',
                    '-XMP-exif:FNumber<EXIF:FNumber',
                    '-XMP-exif:ISOSpeedRatings<EXIF:ISO',
                    '-XMP-exif:FocalLength<EXIF:FocalLength',
                    '-XMP-exif:DateTimeOriginal<EXIF:DateTimeOriginal',
                    '-XMP-exif:LensModel<EXIF:LensModel',
                    '-XMP-exif:LensMake<EXIF:LensMake',
                    '-XMP-exif:WhiteBalance<EXIF:WhiteBalance',
                    '-XMP-exif:MeteringMode<EXIF:MeteringMode',
                    '-XMP-exif:ExposureProgram<EXIF:ExposureProgram',
                    # Lens info in auxiliary namespace for broader compatibility
                    '-XMP-aux:Lens<EXIF:LensModel',
                    '-XMP-aux:LensID<Composite:LensID',
                    # Additional commonly useful EXIF → XMP-exif fields
                    '-XMP-exif:ExposureBiasValue<EXIF:ExposureBiasValue',
                    '-XMP-exif:ShutterSpeedValue<EXIF:ShutterSpeedValue',
                    '-XMP-exif:ApertureValue<EXIF:ApertureValue',
                    '-XMP-exif:BrightnessValue<EXIF:BrightnessValue',
                    '-XMP-exif:Flash<EXIF:Flash',
                    '-XMP-exif:FocalLengthIn35mmFilm<EXIF:FocalLengthIn35mmFilm',
                    '-XMP-exif:ColorSpace<EXIF:ColorSpace',
                    '-XMP-exif:ExifVersion<EXIF:ExifVersion',
                    '-XMP-exif:SceneCaptureType<EXIF:SceneCaptureType',
                    '-XMP-exif:SensingMethod<EXIF:SensingMethod',
                    '-XMP-exif:SubjectArea<EXIF:SubjectArea',
                    '-XMP-exif:PixelXDimension<EXIF:ExifImageWidth',
                    '-XMP-exif:PixelYDimension<EXIF:ExifImageHeight',
                    # ISO synonyms
                    '-XMP-exif:PhotographicSensitivity<EXIF:ISO',
                    # Timezone and subsecond precision
                    '-XMP-exif:OffsetTime<EXIF:OffsetTime',
                    '-XMP-exif:OffsetTimeOriginal<EXIF:OffsetTimeOriginal',
                    '-XMP-exif:OffsetTimeDigitized<EXIF:OffsetTimeDigitized',
                    '-XMP-exif:SubSecTimeOriginal<EXIF:SubSecTimeOriginal',
                    '-XMP-exif:SubSecTimeDigitized<EXIF:SubSecTimeDigitized',
                    # Host computer
                    '-XMP-exif:HostComputer<IFD0:HostComputer',
                    # GPS into XMP-exif (use Composite for Lat/Long to preserve E/W/N/S)
                    '-XMP-exif:GPSLatitude<Composite:GPSLatitude',
                    '-XMP-exif:GPSLongitude<Composite:GPSLongitude',
                    '-XMP-exif:GPSAltitude<GPS:GPSAltitude',
                    '-XMP-exif:GPSDateStamp<GPS:GPSDateStamp',
                    '-XMP-exif:GPSTimeStamp<GPS:GPSTimeStamp',
                    '-XMP-exif:GPSSpeed<GPS:GPSSpeed',
                    '-XMP-exif:GPSSpeedRef<GPS:GPSSpeedRef',
                    '-XMP-exif:GPSImgDirection<GPS:GPSImgDirection',
                    '-XMP-exif:GPSImgDirectionRef<GPS:GPSImgDirectionRef',
                    '-XMP-exif:GPSDestBearing<GPS:GPSDestBearing',
                    '-XMP-exif:GPSDestBearingRef<GPS:GPSDestBearingRef',
                    # XMP core from EXIF/ITPC
                    '-XMP-xmp:CreateDate<EXIF:CreateDate',
                    '-XMP-xmp:ModifyDate<EXIF:ModifyDate',
                    '-XMP-xmp:CreatorTool<IFD0:Software',
                    '-XMP-dc:Title<IPTC:ObjectName',
                    '-XMP-dc:Description<IPTC:Caption-Abstract',
                    '-XMP-dc:Creator<IPTC:By-line',
                    '-XMP-dc:Rights<IPTC:CopyrightNotice'
                ]

                # Broad group-to-group copies: EXIF → XMP-exif, IFD0 → XMP-tiff, GPS → XMP-exif
                exiftool_cmd.extend([
                    '-XMP-exif:all<EXIF:all',
                    '-XMP-exif:all<ExifIFD:all',
                    '-XMP-tiff:all<IFD0:all',
                    '-XMP-exif:all<GPS:all'
                ])

                # Final catch-all to push everything exiftool can map into XMP where possible
                exiftool_cmd.extend([
                    '-XMP:all<EXIF:all',
                    '-XMP:all<ExifIFD:all',
                    '-XMP:all<IFD0:all',
                    '-XMP:all<GPS:all',
                    '-XMP:all<XMP:all',
                    '-XMP:all<Composite:all'
                ])

                if tags:
                    for tag in tags:
                        exiftool_cmd.append(f"-XMP-lr:HierarchicalSubject+=Auto Tags|{tag}")
                    for tag in tags:
                        exiftool_cmd.append(f"-XMP-digiKam:TagsList+=Auto Tags/{tag}")
                    for tag in tags:
                        exiftool_cmd.append(f"-XMP:Subject+={tag}")
                    for tag in tags:
                        exiftool_cmd.append(f"-IPTC:Keywords+={tag}")

                try:
                    exiftool_cmd.extend(['-o', safe_sidecar_filename, safe_image_filename])
                    subprocess.run(exiftool_cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace')
                finally:
                    # Move the temp sidecar to the final emoji path if needed
                    try:
                        if sidecar_output_path != sidecar_file and sidecar_output_path.exists():
                            if sidecar_file.exists():
                                sidecar_file.unlink()
                            shutil.move(str(sidecar_output_path), str(sidecar_file))
                    except Exception as move_err:
                        print(self._safe_console_text(f"Warning: Could not move temporary sidecar to final path: {move_err}"))
                        # Fall through; we still clean up and report success/failure below
                    # Clean up temporary image copy and temp sidecar directory
                    if was_copied:
                        self._cleanup_temp_file(temp_image_file)
                    if temp_sidecar_dir and temp_sidecar_dir.exists():
                        try:
                            temp_sidecar_dir.rmdir()
                        except Exception:
                            pass

                print(self._safe_console_text(f"Created {sidecar_file} with {len(tags)} AI tags under 'Auto Tags' hierarchy"))
                return True
            except FileNotFoundError:
                print(self._safe_console_text("Error: exiftool not found. Install exiftool and ensure it's in PATH."))
                return False
            except subprocess.CalledProcessError as e:
                stdout = e.stdout.strip() if e.stdout else ''
                stderr = e.stderr.strip() if e.stderr else ''
                print(self._safe_console_text(f"Error creating sidecar via exiftool for {image_file.name}:"))
                if stdout:
                    print(self._safe_console_text(f"stdout: {stdout}"))
                if stderr:
                    print(self._safe_console_text(f"stderr: {stderr}"))
                return False
            except UnicodeDecodeError as e:
                print(self._safe_console_text(f"Unicode encoding error creating sidecar for {image_file.name}: {e}"))
                return False
        else:
            print(self._safe_console_text(f"Unsupported format: {format_type}"))
            return False
