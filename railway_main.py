import os
import json
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests

app = Flask(__name__)

# Session storage for users
user_sessions = {}

# Load credentials from environment variable
def get_drive_service():
    service_account_json = os.getenv('SERVICE_ACCOUNT_JSON')
    if not service_account_json:
        raise ValueError("SERVICE_ACCOUNT_JSON not found in environment variables")

    credentials_dict = json.loads(service_account_json)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_dict,
        scopes=['https://www.googleapis.com/auth/drive.readonly']
    )
    return build('drive', 'v3', credentials=credentials)

# Google Drive folder ID to search
FOLDER_ID = "1uxLpoZ_oGYAymVBJzA60SH_ZVipFs3y5"
WAPI_TOKEN = os.getenv('WAPI_TOKEN')
WAPI_NUMBER = os.getenv('WAPI_NUMBER')
GROUP_ID = "120363039812918773@g.us"

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "message": "Bot is running"}), 200

@app.route('/test', methods=['GET'])
def test():
    return jsonify({"status": "ok", "message": "Test endpoint working"}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        print("[INFO] Webhook received")
        data = request.json
        print(f"[DEBUG] Data received: {data}")

        if not data or 'messages' not in data:
            print("[INFO] No messages in data")
            return jsonify({"status": "ok"}), 200

        for message in data.get('messages', []):
            from_number = message.get('from')
            text = message.get('body', '').strip()

            print(f"[INFO] Processing message from {from_number}: {text}")

            # Only process messages from the authorized group
            if not from_number == GROUP_ID:
                print(f"[INFO] Ignoring message from {from_number} (not in authorized group {GROUP_ID})")
                continue

            print(f"[INFO] Message is from authorized group, processing...")

            # Check if message is a command
            if text == '/buscardocs':
                print(f"[INFO] /buscardocs command received")
                user_sessions[from_number] = {'stage': 'cliente'}
                send_message(from_number, "🔍 Qual cliente procura?\n(nome, código ou CNPJ)")
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
                print(f"[INFO] User not in session, ignoring message")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[ERROR] Error in webhook: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

def send_message(to_number, message):
    """Send message via W-API"""
    try:
        # Use the correct W-API endpoint
        url = f"https://api.w-api.app/v1/sendMessage"
        headers = {
            "Authorization": f"Bearer {WAPI_TOKEN}",
            "Content-Type": "application/json"
        }
        # W-API expects a different payload structure
        payload = {
            "to": to_number,
            "body": message
        }
        print(f"[DEBUG] Sending message to {to_number}")
        print(f"[DEBUG] URL: {url}")
        print(f"[DEBUG] Token present: {bool(WAPI_TOKEN)}")

        response = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"[DEBUG] Response status: {response.status_code}")
        print(f"[DEBUG] Response body: {response.text}")

        return response.status_code in [200, 201]
    except Exception as e:
        print(f"Error sending message: {str(e)}")
        return False

def search_documents(folder_id, client_name, year, month, doc_type):
    """Recursive search in Google Drive folder"""
    try:
        service = get_drive_service()
        results = []

        def traverse_folder(folder_id, depth=0):
            if depth > 10:  # Prevent infinite recursion
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
                        results.append({
                            'id': file['id'],
                            'name': file.get('name'),
                            'link': f"https://drive.google.com/file/d/{file['id']}/view"
                        })

                    # If it's a folder, traverse it
                    if file.get('mimeType') == 'application/vnd.google-apps.folder':
                        traverse_folder(file['id'], depth + 1)

            except Exception as e:
                print(f"Error traversing folder: {str(e)}")

        traverse_folder(folder_id)
        return results

    except Exception as e:
        print(f"Error searching documents: {str(e)}")
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
    app.run(host='0.0.0.0', port=5000, debug=False)
