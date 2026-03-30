import os
import json
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests

app = Flask(__name__)
user_sessions = {}
_drive_service = None

FOLDER_ID = os.getenv('FOLDER_ID', '1uxLpoZ_oGYAymVBJzA60SH_ZVipFs3y5')
WAPI_TOKEN = os.getenv('WAPI_TOKEN', '')
WAPI_NUMBER = os.getenv('WAPI_NUMBER', '')
GROUP_ID = os.getenv('GROUP_ID', '120363039812918773@g.us')

print(f"[STARTUP] WAPI_TOKEN present: {bool(WAPI_TOKEN)}")
print(f"[STARTUP] WAPI_NUMBER present: {bool(WAPI_NUMBER)}")
print(f"[STARTUP] FOLDER_ID: {FOLDER_ID}")
print(f"[STARTUP] GROUP_ID: {GROUP_ID}")


def get_drive_service():
    global _drive_service
    if _drive_service is not None:
        return _drive_service
    try:
        svc_json = os.getenv('SERVICE_ACCOUNT_JSON')
        if not svc_json:
            print("[WARN] SERVICE_ACCOUNT_JSON not set")
            return None
        creds = service_account.Credentials.from_service_account_info(
            json.loads(svc_json),
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        _drive_service = build('drive', 'v3', credentials=creds)
        print("[INFO] Google Drive service initialized")
        return _drive_service
    except Exception as e:
        print(f"[ERROR] Drive service failed: {e}")
        return None


@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "message": "Bot is running"}), 200


@app.route('/webhook', methods=['POST'])
def webhook():
    print("[INFO] ===== Webhook received =====")
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            print("[WARN] No JSON data received")
            return jsonify({"status": "ok"}), 200

        print(f"[DEBUG] Keys: {list(data.keys())}")

        messages = data.get('messages', [])
        if not messages:
            print("[INFO] No messages in payload")
            return jsonify({"status": "ok"}), 200

        for msg in messages:
            from_number = msg.get('from', '')
            text = msg.get('body', '').strip()

            print(f"[INFO] From: {from_number} | Text: {text}")

            if from_number != GROUP_ID:
                print(f"[INFO] Ignored (not from group)")
                continue

            if text == '/buscardocs':
                user_sessions[from_number] = {'stage': 'cliente'}
                send_message(from_number, "Qual cliente procura? (nome, codigo ou CNPJ)")

            elif from_number in user_sessions:
                session = user_sessions[from_number]
                stage = session.get('stage')

                if stage == 'cliente':
                    session['cliente'] = text
                    session['stage'] = 'ano'
                    send_message(from_number, f"Cliente: {text}\n\nQual ano? (ex: 2024)")

                elif stage == 'ano':
                    session['ano'] = text
                    session['stage'] = 'mes'
                    send_message(from_number, f"Ano: {text}\n\nQual mes? (ex: 03)")

                elif stage == 'mes':
                    session['mes'] = text.zfill(2)
                    session['stage'] = 'tipo'
                    send_message(from_number, f"Mes: {session['mes']}\n\nQual tipo?\nNF, NF-e, Recibo, Relatorio ou Outro")

                elif stage == 'tipo':
                    session['tipo'] = text
                    send_message(from_number, "Buscando documentos, aguarde...")
                    search_and_send_documents(from_number, session)
                    del user_sessions[from_number]

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[ERROR] Webhook exception: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error"}), 500


def send_message(to, body):
    try:
        if not WAPI_TOKEN:
            print("[ERROR] WAPI_TOKEN not set")
            return False
        url = "https://api.w-api.app/v1/sendMessage"
        headers = {"Authorization": f"Bearer {WAPI_TOKEN}", "Content-Type": "application/json"}
        payload = {"to": to, "body": body}
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        print(f"[INFO] W-API status: {resp.status_code}")
        if resp.status_code not in [200, 201]:
            print(f"[ERROR] W-API body: {resp.text}")
        return resp.status_code in [200, 201]
    except Exception as e:
        print(f"[ERROR] send_message: {e}")
        return False


def search_and_send_documents(to, session):
    try:
        cliente = session.get('cliente', '')
        ano = session.get('ano', '')
        mes = session.get('mes', '')
        tipo = session.get('tipo', '')
        print(f"[INFO] Searching: {cliente} / {ano} / {mes} / {tipo}")

        svc = get_drive_service()
        if not svc:
            send_message(to, "Google Drive nao configurado.")
            return

        results = []

        def traverse(folder_id, depth=0):
            if depth > 8:
                return
            try:
                resp = svc.files().list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    fields='files(id, name, mimeType)',
                    pageSize=100
                ).execute()
                for f in resp.get('files', []):
                    name = f.get('name', '').lower()
                    if (cliente.lower() in name and ano in name and
                            mes in name and tipo.lower() in name):
                        results.append({'name': f['name'], 'id': f['id']})
                    if f.get('mimeType') == 'application/vnd.google-apps.folder':
                        traverse(f['id'], depth + 1)
            except Exception as e:
                print(f"[ERROR] traverse: {e}")

        traverse(FOLDER_ID)
        print(f"[INFO] Found {len(results)} docs")

        if results:
            msg = f"{len(results)} documento(s) para {cliente} ({mes}/{ano}):\n\n"
            for d in results[:5]:
                msg += f"- {d['name']}\nhttps://drive.google.com/file/d/{d['id']}/view\n\n"
            send_message(to, msg)
        else:
            send_message(to, f"Nenhum documento encontrado para:\nCliente: {cliente}\nAno: {ano} / Mes: {mes}\nTipo: {tipo}")

    except Exception as e:
        print(f"[ERROR] search_and_send: {e}")
        send_message(to, f"Erro na busca: {e}")


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"[STARTUP] Flask starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
