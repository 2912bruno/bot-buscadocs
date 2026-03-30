import os
import json
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests

app = Flask(__name__)
user_sessions = {}

def get_drive_service():
    try:
        service_account_json = os.getenv('SERVICE_ACCOUNT_JSON')
        if not service_account_json:
            print("[WARN] SERVICE_ACCOUNT_JSON not set - Google Drive search will not work")
            return None
        credentials_dict = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        print(f"[ERROR] Failed to create Drive service: {str(e)}")
        return None

FOLDER_ID = "1uxLpoZ_oGYAymVBJzA60SH_ZVipFs3y5"
WAPI_TOKEN = os.getenv('WAPI_TOKEN', '')
GROUP_ID = "120363039812918773@g.us"

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "bot": "BuscaDocs", "version": "1.0"}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if not data or 'messages' not in data:
            return jsonify({"status": "ok"}), 200

        for message in data.get('messages', []):
            from_number = message.get('from')
            text = message.get('body', '').strip()

            if from_number != GROUP_ID:
                continue

            if text == '/buscardocs':
                user_sessions[from_number] = {'stage': 'cliente'}
                send_message(from_number, "Qual cliente procura?")
            elif from_number in user_sessions:
                session = user_sessions[from_number]
                stage = session.get('stage')

                if stage == 'cliente':
                    session['cliente'] = text
                    session['stage'] = 'ano'
                    send_message(from_number, f"Cliente: {text}. Qual ano?")
                elif stage == 'ano':
                    session['ano'] = text
                    session['stage'] = 'mes'
                    send_message(from_number, f"Ano: {text}. Qual mes?")
                elif stage == 'mes':
                    session['mes'] = text.zfill(2)
                    session['stage'] = 'tipo'
                    send_message(from_number, f"Mes: {session['mes']}. Qual tipo?")
                elif stage == 'tipo':
                    session['tipo'] = text
                    search_and_send_documents(from_number, session)
                    if from_number in user_sessions:
                        del user_sessions[from_number]

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"[ERROR] Webhook error: {str(e)}")
        return jsonify({"status": "error"}), 500

def send_message(to_number, message):
    try:
        if not WAPI_TOKEN:
            print("[WARN] WAPI_TOKEN not set - cannot send message")
            return False

        url = "https://api.w-api.app/v1/sendMessage"
        headers = {
            "Authorization": f"Bearer {WAPI_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "to": to_number,
            "body": message
        }
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        return response.status_code in [200, 201]
    except Exception as e:
        print(f"[ERROR] Send message error: {str(e)}")
        return False

def search_documents(folder_id, client_name, year, month, doc_type):
    try:
        service = get_drive_service()
        if not service:
            return []

        results = []

        def traverse_folder(fid, depth=0):
            if depth > 10:
                return
            query = f"'{fid}' in parents and trashed=false"
            try:
                files = service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name, mimeType)',
                    pageSize=100
                ).execute()

                for file in files.get('files', []):
                    fname = file.get('name', '').lower()
                    if (client_name.lower() in fname and
                        year in fname and
                        month in fname and
                        doc_type.lower() in fname):
                        results.append({
                            'name': file.get('name'),
                            'link': f"https://drive.google.com/file/d/{file['id']}/view"
                        })
                    if file.get('mimeType') == 'application/vnd.google-apps.folder':
                        traverse_folder(file['id'], depth + 1)
            except Exception as e:
                print(f"[ERROR] Traverse folder error: {str(e)}")

        traverse_folder(folder_id)
        return results
    except Exception as e:
        print(f"[ERROR] Search documents error: {str(e)}")
        return []

def search_and_send_documents(to_number, session):
    try:
        cliente = session.get('cliente')
        ano = session.get('ano')
        mes = session.get('mes')
        tipo = session.get('tipo')

        documents = search_documents(FOLDER_ID, cliente, ano, mes, tipo)

        if documents:
            message = f"Encontrados {len(documents)} documentos:\n"
            for doc in documents[:5]:
                message += f"{doc['name']}\n{doc['link']}\n"
            send_message(to_number, message)
        else:
            send_message(to_number, f"Nenhum documento encontrado para {cliente}")
    except Exception as e:
        print(f"[ERROR] Search and send error: {str(e)}")
        send_message(to_number, f"Erro ao buscar: {str(e)}")

if __name__ == '__main__':
    print("[INFO] Starting BuscaDocs bot...")
    print(f"[INFO] WAPI Token configured: {bool(WAPI_TOKEN)}")
    print(f"[INFO] Google Drive Folder ID: {FOLDER_ID}")
    app.run(host='0.0.0.0', port=5000, debug=False)
