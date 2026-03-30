import os
import json
import threading
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
user_sessions = {}

WAPI_TOKEN = os.getenv('WAPI_TOKEN', '')
WAPI_NUMBER = os.getenv('WAPI_NUMBER', '')
GROUP_ID = os.getenv('GROUP_ID', '120363039812918773@g.us')
FOLDER_ID = os.getenv('FOLDER_ID', '1uxLpoZ_oGYAymVBJzA60SH_ZVipFs3y5')

print("[STARTUP] WAPI_TOKEN: " + ("OK" if WAPI_TOKEN else "FALTANDO"))
print("[STARTUP] WAPI_NUMBER: " + ("OK" if WAPI_NUMBER else "FALTANDO"))


@app.route('/', methods=['GET'])
def health():
    return "OK", 200


@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


@app.route('/webhook', methods=['GET', 'POST', 'OPTIONS'])
def webhook():
    if request.method in ['GET', 'OPTIONS']:
        return jsonify({"status": "ok"}), 200
    try:
        data = request.get_json(force=True, silent=True) or {}
        print("[WEBHOOK] payload keys: " + str(list(data.keys())))
        messages = data.get('messages', [])
        if not messages:
            # Try alternative payload structures from W-API
            msg = data.get('message', {})
            if msg:
                messages = [msg]
        for msg in messages:
            from_id = msg.get('from', '') or msg.get('chatId', '') or msg.get('groupId', '')
            text = (msg.get('body', '') or msg.get('text', '') or msg.get('content', '') or '').strip()
            print("[MSG] from=" + str(from_id) + " text=" + str(text[:80]))
            if GROUP_ID not in from_id and from_id != GROUP_ID:
                print("[SKIP] nao e o grupo alvo: " + str(from_id))
                continue
            if text.lower() in ['/buscardocs', 'buscardocs']:
                user_sessions[from_id] = {'stage': 'cliente'}
                send_msg(from_id, "Qual cliente voce procura? (nome ou CNPJ)")
            elif from_id in user_sessions:
                s = user_sessions[from_id]
                stage = s.get('stage')
                if stage == 'cliente':
                    s['cliente'] = text
                    s['stage'] = 'ano'
                    send_msg(from_id, "Cliente: " + text + "\nQual ano? (ex: 2024)")
                elif stage == 'ano':
                    s['ano'] = text
                    s['stage'] = 'mes'
                    send_msg(from_id, "Ano: " + text + "\nQual mes? (ex: 03)")
                elif stage == 'mes':
                    s['mes'] = text.zfill(2)
                    s['stage'] = 'tipo'
                    send_msg(from_id, "Mes: " + s['mes'] + "\nQual tipo?\nNF / NF-e / Recibo / Relatorio / Outro")
                elif stage == 'tipo':
                    s['tipo'] = text
                    send_msg(from_id, "Buscando documentos, aguarde...")
                    t = threading.Thread(target=buscar_e_enviar, args=(from_id, dict(s)))
                    t.daemon = True
                    t.start()
                    del user_sessions[from_id]
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print("[ERROR] webhook: " + str(e))
        return jsonify({"status": "ok"}), 200


def send_msg(to, body):
    try:
        if not WAPI_TOKEN:
            print("[ERROR] WAPI_TOKEN nao configurado")
            return False
        resp = requests.post(
            "https://api.w-api.app/v1/message/send-text?instanceId=" + WAPI_NUMBER,
            json={"phone": to, "message": body},
            headers={"Authorization": "Bearer " + WAPI_TOKEN, "Content-Type": "application/json"},
            timeout=15
        )
        print("[WAPI] status=" + str(resp.status_code) + " body=" + resp.text[:100])
        return resp.status_code in [200, 201]
    except Exception as e:
        print("[ERROR] send_msg: " + str(e))
        return False


def buscar_e_enviar(to, session):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        cliente = session.get('cliente', '')
        ano = session.get('ano', '')
        mes = session.get('mes', '')
        tipo = session.get('tipo', '')
        print("[BUSCA] " + cliente + " / " + ano + " / " + mes + " / " + tipo)

        svc_json = os.getenv('SERVICE_ACCOUNT_JSON')
        if not svc_json:
            send_msg(to, "Erro: SERVICE_ACCOUNT_JSON nao configurado.")
            return

        creds = service_account.Credentials.from_service_account_info(
            json.loads(svc_json),
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        svc = build('drive', 'v3', credentials=creds)
        results = []

        def traverse(folder_id, depth=0):
            if depth > 8 or len(results) >= 10:
                return
            try:
                resp = svc.files().list(
                    q="'" + folder_id + "' in parents and trashed=false",
                    fields='files(id, name, mimeType)',
                    pageSize=100
                ).execute()
                for f in resp.get('files', []):
                    name = f.get('name', '').lower()
                    terms = [k.lower() for k in [cliente, ano, mes, tipo] if k]
                    if all(k in name for k in terms):
                        results.append(f)
                    if f.get('mimeType') == 'application/vnd.google-apps.folder':
                        traverse(f['id'], depth + 1)
            except Exception as e:
                print("[ERROR] traverse: " + str(e))

        traverse(FOLDER_ID)
        print("[BUSCA] " + str(len(results)) + " encontrados")

        if results:
            msg = str(len(results)) + " documento(s) encontrado(s):\n\n"
            for d in results[:5]:
                msg += "- " + d['name'] + "\nhttps://drive.google.com/file/d/" + d['id'] + "/view\n\n"
            send_msg(to, msg)
        else:
            send_msg(to, "Nenhum documento encontrado.\nCliente: " + cliente + " / Ano: " + ano + " / Mes: " + mes + " / Tipo: " + tipo)

    except Exception as e:
        print("[ERROR] buscar_e_enviar: " + str(e))
        send_msg(to, "Erro ao buscar: " + str(e)[:100])


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print("[STARTUP] Rodando na porta " + str(port))
    app.run(host='0.0.0.0', port=port, debug=False)
