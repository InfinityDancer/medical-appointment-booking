import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def fetch_sheet(state: dict, docId: str):
    print(state)
    creds = Credentials.from_service_account_file(
        "../../medisync-bot.json", scopes=SCOPES
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(docId).sheet1
    rows = sheet.get_all_values()

    state["sheet_data"] = rows
    return state
