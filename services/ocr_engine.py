"""OCR Engine abstraction layer for scanned PDF processing.

This module provides a unified interface for OCR engines, allowing different
backends to be swapped without changing the document extraction pipeline.

Supported engines:
- MarkerOCREngine: Primary engine using Marker/Surya pipeline (recommended)
- DoclingOCREngine: Fallback engine using Docling/Tesseract (CPU-friendly)
- LegacyOCREngine: Basic OCR using pytesseract/easyocr (existing implementation)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Any
import logging
import os
import shutil

logger = logging.getLogger(__name__)


def _find_poppler_path() -> Optional[str]:
    """Find poppler binaries for pdf2image, checking bundled/portable locations first."""
    candidates = []

    # Common Windows install locations (helps when PATH changes after app start)
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            programs_dir = Path(local_appdata) / "Programs"
            if programs_dir.exists():
                # Example: %LOCALAPPDATA%\Programs\poppler-25.12.0\Library\bin
                for poppler_dir in programs_dir.glob("poppler*"):
                    candidates.append(str(poppler_dir / "Library" / "bin"))

        candidates.extend([
            r"C:\Program Files\poppler\Library\bin",
            r"C:\Program Files (x86)\poppler\Library\bin",
        ])

    for path in candidates:
        if os.path.isdir(path) and any(
            f.startswith('pdftoppm') or f.startswith('pdfinfo')
            for f in os.listdir(path)
        ):
            logger.debug(f"Found poppler at: {path}")
            return path

    return None  # Let pdf2image search PATH


def _has_poppler_binaries() -> bool:
    """Check if poppler binaries required by pdf2image are available."""
    poppler_path = _find_poppler_path()
    if poppler_path:
        return True

    # Fallback: rely on PATH
    return bool(shutil.which("pdfinfo") and shutil.which("pdftoppm"))


def _find_tesseract_cmd() -> Optional[str]:
    """Find a usable tesseract executable path."""
    candidates = []

    # Explicit override
    env_cmd = os.environ.get("TESSERACT_CMD")
    if env_cmd:
        candidates.append(env_cmd)

    # Common Windows install locations (helps when PATH changes after app start)
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            candidates.append(os.path.join(local_appdata, "Programs", "Tesseract-OCR", "tesseract.exe"))
        candidates.append(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        candidates.append(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    # 4. System PATH
    return shutil.which("tesseract")


def _configure_pytesseract_binary() -> bool:
    """Configure pytesseract to use a real tesseract binary and validate it."""
    try:
        import pytesseract
    except ImportError:
        return False

    tesseract_cmd = _find_tesseract_cmd()
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception as e:
        logger.debug(f"Tesseract binary not available: {e}")
        return False


@dataclass
class OCRPage:
    """Result of OCR for a single page."""
    page_number: int
    text: str
    confidence: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OCRResult:
    """Complete OCR result for a document."""
    pages: List[OCRPage]
    engine: str
    full_text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Compute full text from pages if not provided."""
        if not self.full_text and self.pages:
            self.full_text = "\n\n".join(p.text for p in self.pages if p.text)

    def to_page_dict(self) -> Dict[int, str]:
        """Convert to page number -> text dictionary for compatibility."""
        return {p.page_number: p.text for p in self.pages}


class OCREngine(ABC):
    """Abstract base class for OCR engines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the engine name for logging and metadata."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the engine's dependencies are installed and working."""
        pass

    @abstractmethod
    def extract_text(self, pdf_path: str) -> OCRResult:
        """
        Extract text from a PDF using OCR.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            OCRResult with extracted text and metadata
        """
        pass

    def extract_text_by_page(self, pdf_path: str) -> Dict[int, str]:
        """
        Extract text from a PDF, returning a dict mapping page numbers to text.

        This is a convenience method that wraps extract_text().

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Dictionary mapping page numbers (1-indexed) to extracted text
        """
        result = self.extract_text(pdf_path)
        return result.to_page_dict()


class MarkerOCREngine(OCREngine):
    """
    OCR engine using Marker/Surya pipeline.

    Marker is a full end-to-end pipeline that converts PDFs to Markdown.
    It handles layout detection, table recognition, and reading order automatically.

    Requirements:
        pip install marker-pdf

    Hardware:
        - GPU recommended but not required
        - ~2-4GB VRAM usage
    """

    def __init__(self, output_format: str = "markdown"):
        """
        Initialize the Marker OCR engine.

        Args:
            output_format: Output format - "markdown", "json", or "html"
        """
        self.output_format = output_format
        self._converter = None

    @property
    def name(self) -> str:
        return "marker"

    def is_available(self) -> bool:
        """Check if Marker is installed and working."""
        try:
            from marker.converters.pdf import PdfConverter
            return True
        except ImportError:
            return False

    def _get_converter(self):
        """Lazily initialize the Marker converter."""
        if self._converter is None:
            try:
                from marker.converters.pdf import PdfConverter
                from marker.config.parser import ConfigParser

                config_parser = ConfigParser({"output_format": self.output_format})
                self._converter = PdfConverter(config=config_parser.generate_config_dict())
                logger.info("Marker OCR engine initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Marker: {e}")
                raise
        return self._converter

    def extract_text(self, pdf_path: str) -> OCRResult:
        """Extract text from PDF using Marker."""
        logger.info(f"Running Marker OCR on {pdf_path}")

        converter = self._get_converter()
        rendered = converter(pdf_path)

        # Marker returns a rendered document object
        # The markdown attribute contains the full text
        full_text = rendered.markdown if hasattr(rendered, 'markdown') else str(rendered)

        # Marker doesn't provide per-page results in the simple API,
        # so we return the full document as a single page
        # For more granular results, we could use the lower-level API
        pages = [OCRPage(
            page_number=1,
            text=full_text,
            metadata={"format": self.output_format}
        )]

        return OCRResult(
            pages=pages,
            engine=self.name,
            full_text=full_text,
            metadata={
                "output_format": self.output_format,
                "source_file": pdf_path
            }
        )


class DoclingOCREngine(OCREngine):
    """
    OCR engine using IBM's Docling toolkit.

    Docling provides document conversion with layout analysis and
    falls back to Tesseract/EasyOCR for scanned pages. It's MIT licensed
    and runs well on CPU.

    Requirements:
        pip install docling

    Hardware:
        - CPU-friendly, no GPU required
        - Good for air-gapped/local execution
    """

    def __init__(self):
        """Initialize the Docling OCR engine."""
        self._converter = None

    @property
    def name(self) -> str:
        return "docling"

    def is_available(self) -> bool:
        """Check if Docling is installed."""
        try:
            from docling.document_converter import DocumentConverter
            return True
        except ImportError:
            return False

    def _get_converter(self):
        """Lazily initialize the Docling converter."""
        if self._converter is None:
            try:
                from docling.document_converter import DocumentConverter
                self._converter = DocumentConverter()
                logger.info("Docling OCR engine initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Docling: {e}")
                raise
        return self._converter

    def extract_text(self, pdf_path: str) -> OCRResult:
        """Extract text from PDF using Docling."""
        logger.info(f"Running Docling OCR on {pdf_path}")

        converter = self._get_converter()
        result = converter.convert(pdf_path)

        # Export to markdown
        markdown = result.document.export_to_markdown()

        pages = [OCRPage(
            page_number=1,
            text=markdown,
            metadata={"format": "markdown"}
        )]

        return OCRResult(
            pages=pages,
            engine=self.name,
            full_text=markdown,
            metadata={
                "source_file": pdf_path
            }
        )


class LegacyOCREngine(OCREngine):
    """
    Legacy OCR engine using pytesseract or easyocr.

    This wraps the existing OCR implementation in the document_extractor module
    for backward compatibility. It's simpler than the VLM-based engines but
    less accurate on complex layouts.

    Requirements:
        pip install pytesseract pillow pdf2image
        # OR
        pip install easyocr pdf2image

    Also requires Tesseract to be installed on the system for pytesseract.
    """

    def __init__(self, engine: str = "pytesseract", language: str = "eng"):
        """
        Initialize the legacy OCR engine.

        Args:
            engine: OCR backend - "pytesseract" or "easyocr"
            language: Tesseract language code (e.g., "eng", "eng+fra")
        """
        self._engine = engine
        self._language = language
        self._easyocr_reader = None

    @property
    def name(self) -> str:
        return f"legacy_{self._engine}"

    def is_available(self) -> bool:
        """Check if the selected engine is available."""
        try:
            from pdf2image import convert_from_path
            if not _has_poppler_binaries():
                return False

            if self._engine == "pytesseract":
                return _configure_pytesseract_binary()
            elif self._engine == "easyocr":
                import easyocr
                return True
            return False
        except ImportError:
            return False

    def _get_easyocr_reader(self):
        """Lazily initialize EasyOCR reader."""
        if self._easyocr_reader is None:
            import easyocr

            # Map Tesseract language codes to EasyOCR codes
            lang_map = {
                "eng": "en", "fra": "fr", "deu": "de", "spa": "es",
                "ita": "it", "por": "pt", "nld": "nl", "pol": "pl",
                "rus": "ru", "jpn": "ja", "kor": "ko", "chi_sim": "ch_sim"
            }
            langs = []
            for lang in self._language.split("+"):
                langs.append(lang_map.get(lang, lang))

            self._easyocr_reader = easyocr.Reader(langs, gpu=False)
        return self._easyocr_reader

    def extract_text(self, pdf_path: str) -> OCRResult:
        """Extract text from PDF using pytesseract or easyocr."""
        logger.info(f"Running legacy OCR ({self._engine}) on {pdf_path}")

        from pdf2image import convert_from_path
        import numpy as np

        if not _has_poppler_binaries():
            raise RuntimeError(
                "Poppler binaries not found. Install Poppler and add it to PATH, "
                "or place poppler/bin next to Clio.exe."
            )
        if self._engine == "pytesseract" and not _configure_pytesseract_binary():
            raise RuntimeError(
                "Tesseract binary not found. Install Tesseract OCR and add it to PATH, "
                "or set TESSERACT_CMD."
            )

        # Convert PDF pages to images
        poppler_path = _find_poppler_path()
        images = convert_from_path(str(pdf_path), dpi=300, poppler_path=poppler_path)

        pages = []
        for page_num, image in enumerate(images, start=1):
            text = ""

            if self._engine == "pytesseract":
                import pytesseract
                text = pytesseract.image_to_string(image, lang=self._language)
            elif self._engine == "easyocr":
                reader = self._get_easyocr_reader()
                img_array = np.array(image)
                results = reader.readtext(img_array)
                text = " ".join([r[1] for r in results])

            pages.append(OCRPage(
                page_number=page_num,
                text=text.strip(),
                metadata={"engine": self._engine}
            ))
            logger.debug(f"OCR extracted {len(text)} chars from page {page_num}")

        return OCRResult(
            pages=pages,
            engine=self.name,
            metadata={
                "language": self._language,
                "source_file": pdf_path
            }
        )


class VisionOCREngine(OCREngine):
    """
    OCR engine that uses vision-capable AI models (Anthropic, OpenAI, or Ollama).

    Flow:
      1. Render each PDF page to a PNG image
      2. Send each image to the vision model with an OCR extraction prompt
      3. Optionally run a lightweight cleanup LLM pass over the raw output

    Requirements:
        pip install pdf2image pillow
        Plus Poppler binaries for pdf2image.
    """

    OCR_PROMPT = (
        "You are an expert OCR system processing a scanned document page. "
        "Extract ALL text exactly as it appears, preserving:\n"
        "- Original line breaks and paragraph spacing\n"
        "- Tables and columnar layouts (use spacing or | separators)\n"
        "- Headers, footers, and page numbers\n"
        "- Reference codes, initials, and identifiers (e.g. 'WPW:FJB:ls', 'RO-2024-001')\n"
        "- Dates, signatures, and stamps even if partially legible\n\n"
        "If a region is too degraded to read, write [illegible] rather than guessing random characters. "
        "Output ONLY the extracted text with no commentary."
    )

    FORM_OCR_PROMPT = (
        "You are an expert OCR system processing a scanned government or regulatory form. "
        "Your goal is to extract the SEMANTIC CONTENT — field labels and their filled-in values — "
        "not the visual structure of the form.\n\n"
        "RULES:\n"
        "- Individual character entry boxes (like [ P ][ A ][ P ][ S ]) read as a single "
        "continuous string: 'PAPS'. Collapse the spaces between boxes.\n"
        "- Empty boxes, blank underlines '______', and blank table cells contain no data — skip them.\n"
        "- Form decorations (box borders, ruling lines, grid characters like |, _, -, [, ]) "
        "are NOT content. Ignore them entirely.\n"
        "- Checkbox rows: if a box is marked (X, ✓, filled), note it as 'checked'; "
        "empty checkboxes are noise.\n"
        "- Field labels are the printed text (NAME, LICENSE NUMBER, DATE, CATEGORY). "
        "Output them as-is.\n"
        "- For code fields (numeric or alphanumeric boxes), read the characters left-to-right "
        "as one token.\n\n"
        "OUTPUT FORMAT:\n"
        "- For labeled form fields: 'FIELD LABEL: value'\n"
        "- For narrative/descriptive text blocks: plain paragraphs, fully preserved.\n"
        "- For tables with real data: preserve columns with spacing.\n\n"
        "If a region is too degraded, write [illegible]. "
        "Output ONLY the extracted content with no commentary."
    )

    IMAGE_PROMPT = (
        "You are indexing an image for a document search system. Produce text that "
        "makes this image findable by search.\n\n"
        "1. Describe what the image shows: subject, setting, people/objects, actions, "
        "and the kind of image (photo, diagram, chart, screenshot, whiteboard, scan...).\n"
        "2. Transcribe ALL legible text exactly as written — labels, signs, captions, "
        "code, table contents, axis labels and values.\n"
        "3. For charts and diagrams: state what is being shown and summarize the key "
        "data, trends, or relationships.\n\n"
        "Be thorough but factual — do not speculate beyond what is visible. "
        "Output only the description and transcription, no commentary."
    )

    CLEANUP_PROMPT_TEMPLATE = (
        "You are an expert OCR post-processor. The text below was extracted by a vision model from a "
        "scanned document page and contains OCR noise mixed with real content.\n\n"
        "REMOVE or FIX:\n"
        "- Garbled character sequences that form no recognizable word or code "
        "(e.g. 'I tr 1J}· v .' or 'F/03t/ofJ' — random mixes of letters, digits, symbols)\n"
        "- Isolated stray characters or punctuation that are clearly scan artifacts "
        "(lone '}', '·', stray periods not ending sentences)\n"
        "- Repeated separator junk lines (e.g. '- - - - -', '_ _ _ _', '| | | |')\n"
        "- Form box artifacts: spaced-out single characters that belong together "
        "(e.g. 'P  A  P  S' should become 'PAPS' when they are clearly a code in individual boxes)\n"
        "- Empty bracket pairs '[ ]', '[ ]', sequences of pipes '| | |' with no content\n"
        "- Common character substitutions: fix 'rn'→'m', '0'→'O' in words, "
        "'l'→'1' in numbers where context makes it unambiguous\n\n"
        "ALWAYS PRESERVE:\n"
        "- Every real word, sentence, and paragraph — do NOT summarize or paraphrase\n"
        "- Reference codes, initials, and routing marks like 'WPW:FJB:ls' — "
        "these are intentional document metadata\n"
        "- Dates, case numbers, docket IDs, report numbers even if they look unusual\n"
        "- '[illegible]' markers left by the vision model\n"
        "- All paragraph breaks, list structure, section headers\n"
        "- Narrative text blocks exactly as written (event descriptions, cause descriptions, etc.)\n\n"
        "Return ONLY the cleaned text. No explanations, no commentary.\n\n"
        "Text:\n{text}"
    )

    def __init__(
        self,
        provider,                       # AIProvider for vision extraction
        model: str,
        cleanup_pass: bool = True,
        cleanup_provider=None,          # AIProvider for cleanup (defaults to same as provider)
        cleanup_model: Optional[str] = None,
        dpi: int = 200,
        max_pages: int = 0,
        enhance_image: bool = True,     # Boost contrast + sharpness (helps old/degraded scans)
        form_mode: bool = False,        # Use form-aware prompt; remove ruled lines from images
    ):
        self._provider = provider
        self._model = model
        self._cleanup_pass = cleanup_pass
        self._cleanup_provider = cleanup_provider or provider
        self._cleanup_model = cleanup_model or model
        self._dpi = dpi
        self._max_pages = max_pages
        self._enhance_image = enhance_image
        self._form_mode = form_mode

    @property
    def name(self) -> str:
        return f"vision_ai"

    def is_available(self) -> bool:
        """Available as long as pdf2image + poppler are present."""
        try:
            from pdf2image import convert_from_path
            return _has_poppler_binaries()
        except ImportError:
            return False

    @staticmethod
    def _remove_ruled_lines(image) -> "Image":
        """Remove horizontal and vertical ruling lines from a form image using numpy.

        Detects rows/columns where dark pixels span most of the image width/height —
        the signature of a printed form rule — and paints them white before the image
        is sent to the vision model.  Text strokes are short relative to the page
        dimension, so they survive the filter.
        """
        try:
            import numpy as np
            from PIL import Image as PILImage

            gray = image.convert("L")
            arr = np.array(gray, dtype=np.uint8)

            # Binarise: 1 = dark pixel (ink / line), 0 = light (background)
            threshold = 180
            binary = (arr < threshold).astype(np.float32)

            h, w = binary.shape

            # A ruled line spans > 60 % of the page in one dimension
            # but only a few pixels in the other.
            h_span = 0.60  # fraction of width a horizontal line must cover
            v_span = 0.60  # fraction of height a vertical line must cover

            row_fill = binary.mean(axis=1)   # fraction of dark pixels per row
            col_fill = binary.mean(axis=0)   # fraction of dark pixels per column

            line_rows = row_fill > h_span
            line_cols = col_fill > v_span

            if not (line_rows.any() or line_cols.any()):
                return image  # nothing to remove

            result = arr.copy()
            result[line_rows, :] = 255  # paint ruled rows white
            result[:, line_cols] = 255  # paint ruled columns white

            n_rows = int(line_rows.sum())
            n_cols = int(line_cols.sum())
            if n_rows or n_cols:
                logger.debug(f"Form line removal: erased {n_rows} row(s), {n_cols} col(s)")

            return PILImage.fromarray(result).convert("RGB")

        except Exception as e:
            logger.warning(f"Ruled-line removal failed, using original image: {e}")
            return image

    def describe_image(self, image_path: str) -> str:
        """Describe a standalone image and transcribe its text for indexing.

        Unlike the scanned-page path, the image is sent as-is (no contrast/
        sharpness boost — that helps degraded scans but distorts photos),
        downscaled only to keep the payload reasonable.
        """
        import base64
        import io
        from PIL import Image

        img = Image.open(image_path)
        img = img.convert("RGB")  # flattens alpha, takes frame 1 of animations
        max_dim = 2000
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        logger.info(
            f"Vision image description: {image_path} "
            f"({len(image_b64) // 1024}KB, model={self._model})"
        )
        result = self._provider.complete_with_image(
            prompt=self.IMAGE_PROMPT,
            image_base64=image_b64,
            media_type="image/png",
            max_tokens=2048,
            model=self._model,
        )
        return (result.get("text") or "").strip()

    def extract_text(self, pdf_path: str, pages: Optional[List[int]] = None) -> OCRResult:
        """Extract text by sending page images to a vision model.

        Args:
            pages: Optional 1-based page numbers to OCR. When given, only those
                pages are rendered and sent — the selective path for documents
                where native extraction covered everything except a few scanned
                or figure-only pages. When None, every page is processed.
        """
        import base64
        import io
        from pdf2image import convert_from_path

        logger.info(f"Running Vision AI OCR on {pdf_path} with model={self._model}")

        if not _has_poppler_binaries():
            raise RuntimeError(
                "Poppler binaries not found. Install Poppler and add it to PATH, "
                "or place poppler/bin next to Clio.exe."
            )

        poppler_path = _find_poppler_path()
        if pages:
            page_images = []
            for p in sorted(set(pages)):
                rendered = convert_from_path(
                    str(pdf_path), dpi=self._dpi, poppler_path=poppler_path,
                    first_page=p, last_page=p,
                )
                if rendered:
                    page_images.append((p, rendered[0]))
        else:
            rendered = convert_from_path(str(pdf_path), dpi=self._dpi, poppler_path=poppler_path)
            page_images = list(enumerate(rendered, start=1))

        if self._max_pages and len(page_images) > self._max_pages:
            logger.warning(f"Vision OCR: truncating {len(page_images)} pages to {self._max_pages}")
            page_images = page_images[:self._max_pages]

        ocr_prompt = self.FORM_OCR_PROMPT if self._form_mode else self.OCR_PROMPT
        if self._form_mode:
            logger.info("Vision OCR: using form-aware prompt with ruled-line removal")

        pages_out = []
        for page_num, image in page_images:
            # Preprocess image for better OCR on degraded/old scans
            if self._enhance_image:
                from PIL import ImageEnhance
                image = ImageEnhance.Contrast(image).enhance(2.0)
                image = ImageEnhance.Sharpness(image).enhance(2.0)

            # Form mode: remove horizontal/vertical ruling lines before sending to model.
            # This prevents the model from reading box borders as characters.
            if self._form_mode:
                image = self._remove_ruled_lines(image)

            # Encode image as PNG base64
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            logger.info(f"Vision OCR: page {page_num} ({len(page_images)} page(s) queued, {len(image_b64) // 1024}KB image)")

            try:
                result = self._provider.complete_with_image(
                    prompt=ocr_prompt,
                    image_base64=image_b64,
                    media_type="image/png",
                    max_tokens=4096,
                    model=self._model,
                )
                raw_text = result["text"]
                logger.info(f"Vision OCR: page {page_num} extracted {len(raw_text)} chars")
                if not raw_text.strip():
                    logger.warning(f"Vision OCR: page {page_num} returned empty — model could not read the image")
            except ValueError as e:
                # Model not found or configuration error — abort the whole run
                raise
            except Exception as e:
                logger.error(f"Vision OCR failed on page {page_num}: {e}")
                raw_text = ""

            # Optional cleanup pass — uses a separate provider/model if configured
            final_text = raw_text
            if self._cleanup_pass and raw_text.strip():
                try:
                    cleanup_result = self._cleanup_provider.complete(
                        prompt=self.CLEANUP_PROMPT_TEMPLATE.replace("{text}", raw_text),
                        max_tokens=4096,
                        model=self._cleanup_model,
                    )
                    cleaned = cleanup_result["text"]
                    # Safety: if cleanup produced empty output, keep the raw OCR text
                    final_text = cleaned if cleaned.strip() else raw_text
                    if not cleaned.strip():
                        logger.warning(f"Vision OCR cleanup returned empty on page {page_num}, using raw text")
                    else:
                        logger.info(f"Vision OCR: page {page_num} cleanup → {len(cleaned)} chars")
                except Exception as e:
                    logger.warning(f"Vision OCR cleanup pass failed on page {page_num}: {e}")
                    final_text = raw_text  # Fall back to raw

            pages_out.append(OCRPage(
                page_number=page_num,
                text=final_text.strip(),
                metadata={
                    "model": self._model,
                    "cleanup_applied": self._cleanup_pass and bool(raw_text.strip()),
                },
            ))
            logger.debug(f"Vision OCR page {page_num}: {len(final_text)} chars")

        cleanup_pages = [p.page_number for p in pages_out if p.metadata.get("cleanup_applied")]

        return OCRResult(
            pages=pages_out,
            engine=self.name,
            metadata={
                "model": self._model,
                "cleanup_pass": self._cleanup_pass,
                "cleanup_pages": cleanup_pages,
                "dpi": self._dpi,
                "source_file": pdf_path,
            }
        )


def get_available_engines() -> List[str]:
    """Get list of available OCR engines."""
    engines = []

    # Check Marker
    try:
        from marker.converters.pdf import PdfConverter
        engines.append("marker")
    except ImportError:
        pass

    # Check Docling
    try:
        from docling.document_converter import DocumentConverter
        engines.append("docling")
    except ImportError:
        pass

    # Check legacy engines
    try:
        from pdf2image import convert_from_path
        if _has_poppler_binaries():
            try:
                import pytesseract
                if _configure_pytesseract_binary():
                    engines.append("pytesseract")
            except ImportError:
                pass
            try:
                import easyocr
                engines.append("easyocr")
            except ImportError:
                pass
        else:
            logger.debug("Poppler not found; legacy OCR engines unavailable")
    except ImportError:
        pass

    return engines


def _get_preferred_engine(available: List[str]) -> Optional[str]:
    """Choose the preferred OCR engine for this application."""
    for preferred in ["docling", "marker", "pytesseract", "easyocr"]:
        if preferred in available:
            return preferred
    return available[0] if available else None


def create_ocr_engine(
    engine_name: str = "auto",
    fallback: bool = True,
    **kwargs
) -> Optional[OCREngine]:
    """
    Create an OCR engine instance.

    Args:
        engine_name: Engine to use - "marker", "docling", "pytesseract",
                     "easyocr", or "auto" (selects best available)
        fallback: If True and the requested engine isn't available,
                  try to use a fallback engine
        **kwargs: Additional arguments passed to the engine constructor

    Returns:
        OCREngine instance, or None if no engine is available
    """
    available = get_available_engines()

    if not available:
        logger.warning("No OCR engines available. Install marker-pdf, docling, or pytesseract.")
        return None

    # Auto-select best available engine
    if engine_name == "auto":
        engine_name = _get_preferred_engine(available)

    # Create the requested engine
    if engine_name == "marker" and "marker" in available:
        return MarkerOCREngine(**kwargs)
    elif engine_name == "docling" and "docling" in available:
        return DoclingOCREngine()
    elif engine_name == "pytesseract" and "pytesseract" in available:
        return LegacyOCREngine(engine="pytesseract", **kwargs)
    elif engine_name == "easyocr" and "easyocr" in available:
        return LegacyOCREngine(engine="easyocr", **kwargs)

    # Fallback if requested engine not available
    if fallback and available:
        fallback_engine = _get_preferred_engine(available)
        logger.warning(f"Requested OCR engine '{engine_name}' not available, falling back to '{fallback_engine}'")
        return create_ocr_engine(fallback_engine, fallback=False, **kwargs)

    logger.error(f"OCR engine '{engine_name}' not available")
    return None


def is_scanned_pdf(pdf_path: str, threshold: int = 100) -> bool:
    """
    Check if a PDF is likely scanned (image-only) by attempting text extraction.

    Args:
        pdf_path: Path to the PDF file
        threshold: Minimum average characters per page to consider as text-based

    Returns:
        True if the PDF appears to be scanned/image-only
    """
    import pdfplumber

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return False

            total_chars = 0
            for page in pdf.pages:
                text = page.extract_text() or ""
                total_chars += len(text.strip())

            avg_chars_per_page = total_chars / len(pdf.pages)
            is_scanned = avg_chars_per_page < threshold

            if is_scanned:
                logger.info(
                    f"PDF '{pdf_path}' detected as scanned "
                    f"({avg_chars_per_page:.0f} avg chars/page < {threshold} threshold)"
                )

            return is_scanned

    except Exception as e:
        logger.warning(f"Error checking if PDF is scanned: {e}")
        return False
