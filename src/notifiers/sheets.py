import json
import gspread
from datetime import datetime, timezone
from oauth2client.service_account import ServiceAccountCredentials
from tenacity import retry, wait_exponential, stop_after_attempt
from src.core.config import logger, GOOGLE_CREDENTIALS, GOOGLE_SHEET_ID

def _get_sheets_client():
    if not GOOGLE_CREDENTIALS:
        return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        logger.error(f"Google Sheets yetkilendirme hatasi: {e}")
        return None

@retry(wait=wait_exponential(multiplier=2, min=4, max=10), stop=stop_after_attempt(3))
def _append_rows_with_retry(worksheet, rows):
    worksheet.append_rows(rows, value_input_option="RAW")

@retry(wait=wait_exponential(multiplier=2, min=4, max=10), stop=stop_after_attempt(3))
def _insert_rows_with_retry(worksheet, rows, row_idx):
    worksheet.insert_rows(rows, row=row_idx, value_input_option="RAW")

@retry(wait=wait_exponential(multiplier=2, min=4, max=10), stop=stop_after_attempt(3))
def _update_cells_with_retry(worksheet, cell_list):
    worksheet.update_cells(cell_list)

@retry(wait=wait_exponential(multiplier=2, min=4, max=10), stop=stop_after_attempt(3))
def _delete_rows_with_retry(worksheet, start_idx, end_idx):
    worksheet.delete_rows(start_idx, end_idx)

def batch_archive_to_sheet(items):
    client = _get_sheets_client()
    if not client or not GOOGLE_SHEET_ID:
        logger.warning("Google Sheets aktif degil (kimlik veya ID eksik).")
        return

    try:
        sheet = client.open_by_key(GOOGLE_SHEET_ID)
        try:
            worksheet = sheet.worksheet("Genel Bakış")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title="Genel Bakış", rows="1000", cols="20")
            worksheet.append_row(["Tarih", "Kaynak", "Kategori", "Başlık", "Özet", "Puan", "Link"])

        # Yeni isareti temizle
        all_values = worksheet.get_all_values()
        if len(all_values) > 1:
            cells_to_update = []
            for i, row in enumerate(all_values[1:], start=2):
                if len(row) > 1 and row[1].startswith("🆕 "):
                    clean_source = row[1].replace("🆕 ", "", 1)
                    cells_to_update.append(gspread.Cell(row=i, col=2, value=clean_source))
            if cells_to_update:
                _update_cells_with_retry(worksheet, cells_to_update)

        # Yeni satirlari ekle
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        rows_to_add = []
        for item in items:
            rows_to_add.append([
                now_str,
                f"🆕 {item.get('source', '')}",
                item.get("category", ""),
                item.get("title", ""),
                "", # Ozet sutunu bos birakildi
                item.get("score", ""),
                item.get("link", "")
            ])
            
        if rows_to_add:
            _insert_rows_with_retry(worksheet, rows_to_add, 2)
            logger.info(f"Google Sheets: {len(rows_to_add)} satir en uste eklendi.")

        # 30 Gunluk Otomatik Arsivleme
        all_values = worksheet.get_all_values()
        if len(all_values) > 1:
            now_dt = datetime.now(timezone.utc)
            rows_to_archive = []
            indices_to_delete = []
            
            for i, row in enumerate(all_values[1:], start=2):
                date_str = row[0]
                try:
                    row_dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    if (now_dt - row_dt).days >= 30:
                        rows_to_archive.append(row)
                        indices_to_delete.append(i)
                except ValueError:
                    continue
                    
            if rows_to_archive:
                try:
                    archive_ws = sheet.worksheet("Arşiv")
                except gspread.exceptions.WorksheetNotFound:
                    archive_ws = sheet.add_worksheet(title="Arşiv", rows="1000", cols="20")
                    archive_ws.append_row(["Tarih", "Kaynak", "Kategori", "Başlık", "Özet", "Puan", "Link"])
                
                _append_rows_with_retry(archive_ws, rows_to_archive)
                
                # Silerken asagidan yukari silmek lazim kaymayi onlemek icin
                for idx in sorted(indices_to_delete, reverse=True):
                    _delete_rows_with_retry(worksheet, idx, idx)
                    
                logger.info(f"{len(rows_to_archive)} eski satir Arsiv sayfasina tasindi.")

    except Exception as e:
        logger.error(f"Sheets toplu arsiv hatasi: {e}")
