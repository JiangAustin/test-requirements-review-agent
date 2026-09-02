from __future__ import annotations

from pathlib import Path

import fitz


def build_text_table_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    # add some text
    page.insert_textbox(
        fitz.Rect(50, 50, 400, 200), "Wi-Fi must be available. Timeout: 30 s", fontsize=12
    )
    # draw simple table: two rows two cols
    # cell positions
    x0, y0, w, h = 50, 150, 300, 80
    colw = w / 2
    rowh = h / 2
    # draw grid
    for r in range(3):
        p1 = (x0, y0 + r * rowh)
        p2 = (x0 + w, y0 + r * rowh)
        page.draw_line(p1, p2)
    for c in range(3):
        p1 = (x0 + c * colw, y0)
        p2 = (x0 + c * colw, y0 + h)
        page.draw_line(p1, p2)
    # insert cell text
    page.insert_text((x0 + 10, y0 + 10), "Param")
    page.insert_text((x0 + colw + 10, y0 + 10), "Value")
    page.insert_text((x0 + 10, y0 + rowh + 10), "Timeout")
    page.insert_text((x0 + colw + 10, y0 + rowh + 10), "30 s")
    doc.save(path)
    doc.close()
    return path


def build_image_only_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    # draw a rectangle background
    page.draw_rect(fitz.Rect(50, 50, 400, 700), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    # insert a tiny 1x1 PNG image (base64) to ensure page.get_images() reports an image
    # create an empty pixmap and insert it as an image
    irect = fitz.IRect(0, 0, 100, 100)
    pix = fitz.Pixmap(fitz.csRGB, irect)
    img_rect = fitz.Rect(60, 60, 160, 160)
    page.insert_image(img_rect, pixmap=pix)
    pix = None
    doc.save(path)
    doc.close()
    return path


def build_encrypted_pdf(path: Path, user_pw: str = "user", owner_pw: str = "owner") -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Encrypted doc")
    # save encrypted
    try:
        # modern PyMuPDF uses owner_pw and user_pw
        doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=owner_pw, user_pw=user_pw)
    except TypeError:
        # fallback: omit user_pw
        doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=owner_pw)
    doc.close()
    return path


def build_damaged_pdf(path: Path) -> Path:
    path.write_bytes(b"%%PDF-1.4\n%\x00\x00\x00\ntruncated")
    return path
