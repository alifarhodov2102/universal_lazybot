import asyncio
import logging
from typing import Tuple

import pdfplumber
import pytesseract
from pdf2image import convert_from_path, pdfinfo_from_path

from config import MAX_CONCURRENT_OCR


logger = logging.getLogger("LazyAlice.PDFEngine")


# OCR consumes much more CPU and RAM than normal PDF text extraction.
# Digital PDFs can still be processed simultaneously.
OCR_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_OCR)

MIN_DIGITAL_TEXT_LENGTH = 50
OCR_DPI = 250


def extract_digital_text_sync(pdf_path: str) -> str:
    """
    Extract embedded text from a digital PDF using pdfplumber.

    This method is relatively lightweight and may run concurrently
    for several PDFs.
    """
    extracted_pages = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                try:
                    page_text = page.extract_text(layout=True)

                    if page_text:
                        extracted_pages.append(page_text)

                except Exception as exc:
                    logger.warning(
                        "Could not extract digital text from page %s: %s",
                        page_number,
                        exc,
                    )

    except Exception as exc:
        logger.error(
            "Could not open PDF with pdfplumber: %s",
            exc,
        )

    return "\n".join(extracted_pages).strip()


def needs_ocr(text: str) -> Tuple[bool, str]:
    """
    Decide whether OCR should be used.

    Returns:
        tuple:
            - True when OCR is required
            - explanation for logs
    """
    clean_text = text.strip()

    if not clean_text:
        return True, "no digital text found"

    if "(cid:" in clean_text.lower():
        return True, "broken CID characters detected"

    if len(clean_text) < MIN_DIGITAL_TEXT_LENGTH:
        return True, (
            f"digital text is too short "
            f"({len(clean_text)} characters)"
        )

    return False, "digital extraction successful"


def get_pdf_page_count(pdf_path: str) -> int:
    """
    Read the PDF page count using Poppler.

    Processing OCR page by page prevents the entire PDF from being
    loaded into memory at once.
    """
    try:
        pdf_info = pdfinfo_from_path(pdf_path)
        return int(pdf_info.get("Pages", 0))

    except Exception as exc:
        logger.error(
            "Could not determine PDF page count: %s",
            exc,
        )
        return 0


def extract_ocr_text_sync(pdf_path: str) -> str:
    """
    Extract text from a scanned or broken PDF using OCR.

    Pages are converted and processed one at a time to reduce
    Railway memory usage.
    """
    page_count = get_pdf_page_count(pdf_path)

    if page_count < 1:
        logger.error(
            "OCR could not start because PDF page count is unavailable."
        )
        return ""

    extracted_pages = []

    logger.info(
        "Starting OCR for %s page(s).",
        page_count,
    )

    for page_number in range(1, page_count + 1):
        images = []

        try:
            images = convert_from_path(
                pdf_path,
                dpi=OCR_DPI,
                first_page=page_number,
                last_page=page_number,
                thread_count=1,
                grayscale=True,
            )

            if not images:
                logger.warning(
                    "No image was generated for PDF page %s.",
                    page_number,
                )
                continue

            page_text = pytesseract.image_to_string(
                images[0],
                config="--psm 6",
            )

            if page_text:
                extracted_pages.append(page_text)

            logger.info(
                "OCR completed for page %s/%s.",
                page_number,
                page_count,
            )

        except Exception as exc:
            logger.exception(
                "OCR failed for page %s/%s: %s",
                page_number,
                page_count,
                exc,
            )

        finally:
            # Explicitly release Pillow image memory.
            for image in images:
                try:
                    image.close()
                except Exception:
                    pass

    return "\n".join(extracted_pages).strip()


def extract_text_sync(pdf_path: str) -> str:
    """
    Synchronous compatibility function.

    The Telegram bot should normally use extract_text_async(),
    because the async version protects OCR with a semaphore.
    """
    digital_text = extract_digital_text_sync(pdf_path)

    should_use_ocr, reason = needs_ocr(digital_text)

    if not should_use_ocr:
        return digital_text

    logger.info(
        "Digital extraction was insufficient: %s. Starting OCR.",
        reason,
    )

    ocr_text = extract_ocr_text_sync(pdf_path)

    if ocr_text:
        return ocr_text

    logger.warning(
        "OCR returned no text. Returning available digital text."
    )

    return digital_text


async def extract_text_async(pdf_path: str) -> str:
    """
    Extract PDF text without blocking the main Telegram event loop.

    Digital extraction may run for several PDFs simultaneously.
    OCR is separately limited using MAX_CONCURRENT_OCR.
    """
    digital_text = await asyncio.to_thread(
        extract_digital_text_sync,
        pdf_path,
    )

    should_use_ocr, reason = needs_ocr(digital_text)

    if not should_use_ocr:
        logger.info(
            "Digital PDF extraction completed successfully."
        )
        return digital_text

    logger.info(
        "Digital extraction was insufficient: %s.",
        reason,
    )

    # Only scanned or broken PDFs wait for an OCR slot.
    async with OCR_SEMAPHORE:
        logger.info(
            "OCR slot acquired. Available OCR slots: %s",
            OCR_SEMAPHORE._value,
        )

        ocr_text = await asyncio.to_thread(
            extract_ocr_text_sync,
            pdf_path,
        )

    if ocr_text:
        logger.info(
            "OCR successfully extracted PDF text."
        )
        return ocr_text

    logger.warning(
        "OCR returned no text. Returning available digital text."
    )

    return digital_text
