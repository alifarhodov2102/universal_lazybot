import asyncio
from functools import partial
import pdfplumber
import pytesseract
from pdf2image import convert_from_path
import logging

# Loglarni sozlash
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PDFEngine")



def extract_text_sync(pdf_path: str) -> str:
    """
    PDF-dan matn ajratish. 
    (cid:X) xatoligi yoki bo'sh matn bo'lsa OCR ishlatadi.
    """
    text = ""
    
    # 1. Raqamli matnni ajratishga urinish
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # Layout=True jadval ko'rinishidagi matnlarni tartibli saqlaydi
                page_text = page.extract_text(layout=True)
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        logger.error(f"pdfplumber error: {e}")

    # 2. Xatolikni tekshirish (cid muammosi yoki juda qisqa matn)
    # Solvera kabi PDF-larda (cid:2) chiqsa, bu extraction muvaffaqiyatsizligini bildiradi
    is_broken = "(cid:" in text
    is_too_short = len(text.strip()) < 50

    if is_broken or is_too_short:
        logger.info(f"Digital extraction failed (Broken: {is_broken}, Short: {is_too_short}). Starting OCR...")
        try:
            # PDF-ni rasmlarga aylantirish
            # Poppler o'rnatilgan va PATH-da bo'lishi shart!
            images = convert_from_path(pdf_path, dpi=300)
            ocr_text = ""
            for img in images:
                # Tesseract orqali rasmdan matnni o'qish
                ocr_text += pytesseract.image_to_string(img) + "\n"
            text = ocr_text
            logger.info("OCR successfully extracted text.")
        except Exception as e:
            logger.error(f"OCR fatal error: {e}")
            # Agar OCR ham ishlamasa, hech bo'lmasa mavjud buzuq matnni qaytaramiz
            pass

    return text.strip()

async def extract_text_async(pdf_path: str) -> str:
    """
    Botning asosiy oqimi (loop) bloklanmasligi uchun
    extraction jarayonini alohida thread-da bajaramiz.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(extract_text_sync, pdf_path))