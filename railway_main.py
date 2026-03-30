import os
import json
import sys
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests

app = Flask(__name__)

# Session storage for users
user_sessions = {}

# Lazy load credentials - only when actually needed
_drive_service = None

def get_drive_service():
    """Lazy load Google Drive service only when needed"""
    global _drive_service

    if _drive_service is not None:
        return _drive_service

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
        _drive_service = build('drive', 'v3', credentials=credentials)
        print("[INFO] Google Drive service initialized successfully")
        return _drive_service
    except Exception as e:
        print(f"[ERROR] Failed to initialize Google Drive service: {str(e)}")
        return None

# Google Drive folder ID to search
FOLDER_ID = "1uxLpoZ_oGYAymVBJzA60SH_ZVipFs3y5"
WAPI_TOKEN = os.getenv('WAPI_TOKEN', '')
WAPI_NUMBER = os.getenv('WAPI_NUMBER', '')
GROUP_ID = "120363039812918773@g.us"

print(f"[STARTUP] WAPI_TOKEN present: {bool(WAPI_TOKEN)}")
print(f"[STARTUP] WAPI_NUMBER present: {bool(WAPI_NUMBER)}")
print(f"[STARTUP] FOLDER_ID: {FOLDER_ID}")
print(f"[STARTUP] GROUP_ID: {GROUP_ID}")

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "message": "Bot is running"}), 200

@app.route('/test', methods=['GET'])
def test():
    return jsonify({"status": "ok", "message": "Test endpoint working"}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    print("[INFO] ===== Webhook received =====")
    try:
        data = request.json
        print(f"[DEBUG] Request data keys: {list(data.keys()) if data else 'None'}")

        if not data or 'messages' not in data:
            print("[INFO] No messages in webhook data - returning ok")
            return jsonify({"status": "ok"}), 200

        messages = data.get('messages', [])
        print(f"[INFO] Processing {len(messages)} message(s)")

        for idx, message in enumerate(messages):
            print(f"\n[DEBUG] Message #{idx + 1}")
            from_number = message.get('from')
            text = message.get('body', '').strip()

            print(f"[INFO] From: {from_number}")
            print(f"[INFO] Text: {text}")

            # Only process messages from the authorized group
            if from_number != GROUP_ID:
                print(f"[INFO] Ignoring (not from authorized group {GROUP_ID})")
                continue

            print(f"[INFO] Message from authorized group - processing")

            # Check if message is a command
            if text == '/buscardocs':
                print(f"[INFO] Command /buscardocs received - starting session")
                user_sessions[from_number] = {'stage': 'cliente'}
                success = send_message(from_number, "🔍 Qual cliente procura?\n(nome, código ou CNPJ)")
                print(f"[INFO] Initial message sent: {success}")

            elif from_number in user_sessions:
                session = user_sessions[from_number]
                stage = session.get('stage')
                print(f"[INFO] User in session, stage: {stage}")

                if stage == 'cliente':
                    session['cliente'] = text
                    session['stage'] = 'ano'
                    send_message(from_number, f"✅ Cliente: {text}\n\nQual ano? (ex: 2024)")

                elif stage == 'ano':
                    session['ano'] = text
                    session['stage'] = 'mes'
                    send_message(from_number, f"✅ Ano: {text}\n\nQual mês? (ex: 03 ou 3)")

                elif stage == 'mes':
                    session['mes'] = text.zfill(2)
                    session['stage'] = 'tipo'
                    send_message(from_number, f"✅ Mês: {session['mes']}\n\nQual tipo de documento?\n• NF\n• NF-e\n• Recibo\n• Relatório\n• Outro")

                elif stage == 'tipo':
                    session['tipo'] = text
                    session['stage'] = 'buscar_docs'
                    send_message(from_number, "🔎 Buscando documentos...\n\nPor favor, aguarde...")
                    search_and_send_documents(from_number, session)
                    del user_sessions[from_number]
            else:
                print(f"[INFO] User not in session - ignoring message")

        print(f"[INFO] Webhook processing completed")
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[ERROR] Exception in webhook: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

def send_message(to_number, message):
    """Send message via W-API"""
    try:
        if not WAPI_TOKEN:
            print(f"[ERROR] WAPI_TOKEN not configured - cannot send message to {to_number}")
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

        print(f"[INFO] Sending message to {to_number}")
        print(f"[DEBUG] Message length: {len(message)}")

        response = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"[INFO] W-API Response status: {response.status_code}")

        if response.status_code not in [200, 201]:
            print(f"[ERROR] W-API Error: {response.text}")

        return response.status_code in [200, 201]

    except requests.exceptions.Timeout:
        print(f"[ERROR] Timeout sending message to {to_number}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Request error: {str(e)}")
        return False
    except Exception as e:
        print(f"[ERROR] Unexpected error sending message: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def search_documents(folder_id, client_name, year, month, doc_type):
    """Recursive search in Google Drive folder"""
    try:
        service = get_drive_service()

        if not service:
            print(f"[ERROR] Google Drive service not available")
            return []

        results = []
        print(f"[INFO] Starting document search in folder {folder_id}")

        def traverse_folder(folder_id, depth=0):
            if depth > 10:  # Prevent infinite recursion
                print(f"[WARN] Max recursion depth reached")
                return

            query = f"'{folder_id}' in parents and trashed=false"
            try:
                files = service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name, mimeType)',
                    pageSize=100
                ).execute()

                for file in files.get('files', []):
                    file_name = file.get('name', '').lower()

                    # Check if file matches criteria
                    if (client_name.lower() in file_name and
                        year in file_name and
                        month in file_name and
                        doc_type.lower() in file_name):
                        print(f"[INFO] Document found: {file.get('name')}")
                        results.append({
                            'id': file['id'],
                            'name': file.get('name'),
                            'link': f"https://drive.google.com/file/d/{file['id']}/view"
                        })

                    # If it's a folder, traverse it
                    if file.get('mimeType') == 'application/vnd.google-apps.folder':
                        traverse_folder(file['id'], depth + 1)

            except Exception as e:
                print(f"[ERROR] Error traversing folder: {str(e)}")

        traverse_folder(folder_id)
        print(f"[INFO] Search completed - found {len(results)} documents")
        return results

    except Exception as e:
        print(f"[ERROR] Error in search_documents: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

def search_and_send_documents(to_number, session):
    """Search documents and send results"""
    try:
        cliente = session.get('cliente')
        ano = session.get('ano')
        mes = session.get('mes')
        tipo = session.get('tipo')

        print(f"[INFO] Searching for: cliente={cliente}, ano={ano}, mes={mes}, tipo={tipo}")

        documents = search_documents(FOLDER_ID, cliente, ano, mes, tipo)
        print(f"[INFO] Found {len(documents)} documents")

        if documents:
            message = f"📄 Documentos encontrados para {cliente} ({mes}/{ano}):\n\n"
            for doc in documents[:5]:  # Limit to 5 documents
                message += f"• {doc['name']}\n{doc['link']}\n\n"
            send_message(to_number, message)
        else:
            send_message(to_number, f"❌ Nenhum documento encontrado para:\nCliente: {cliente}\nAno: {ano}\nMês: {mes}\nTipo: {tipo}")

    except Exception as e:
        print(f"[ERROR] Error in search_and_send_documents: {str(e)}")
        import traceback
        traceback.print_exc()
        send_message(to_number, f"❌ Erro ao buscar documentos: {str(e)}")

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"[STARTUP] Starting Flask app on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
