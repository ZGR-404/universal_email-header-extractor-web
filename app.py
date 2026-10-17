import csv
import email
import imaplib
import io
import os
import re
import threading
import time
import uuid
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.policy import default
from flask import Flask, flash, redirect, render_template, request, session, url_for, make_response

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

PROVIDERS = {
    "gmail": {"label": "Gmail", "host": "imap.gmail.com", "port": 993, "auth_note": "Use a Google App Password with 2-Step Verification enabled."},
    "yahoo": {"label": "Yahoo", "host": "imap.mail.yahoo.com", "port": 993, "auth_note": "Use a Yahoo App Password."},
    "icloud": {"label": "iCloud", "host": "imap.mail.me.com", "port": 993, "auth_note": "Use an Apple app-specific password."},
}
# Credentials and recent results stay in server memory only; they are not written to disk.
STORES = {}
STORE_LOCK = threading.Lock()
STORE_TTL = 60 * 60


def get_store():
    token = session.get("token")
    if not token:
        return None
    with STORE_LOCK:
        item = STORES.get(token)
        if item and time.time() - item["last_seen"] < STORE_TTL:
            item["last_seen"] = time.time()
            return item
        if item:
            STORES.pop(token, None)
    session.pop("token", None)
    return None


def put_store(item):
    token = session.get("token") or uuid.uuid4().hex
    session["token"] = token
    item["last_seen"] = time.time()
    with STORE_LOCK:
        STORES[token] = item


def disconnect_store():
    token = session.pop("token", None)
    if token:
        with STORE_LOCK:
            STORES.pop(token, None)


def clean_header(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def decode_mailbox(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", "replace")
    raw = raw.strip()
    # IMAP LIST response: flags, delimiter, mailbox name.
    match = re.search(r'\((.*?)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(.+)$', raw)
    name = match.group(2).strip() if match else raw.split()[-1]
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    try:
        decoder = getattr(imaplib, "DecodeUTF7", None)
        if decoder:
            name = decoder(name)
        else:
            # imaplib's private decoder is present in common CPython versions.
            decoder = getattr(imaplib, "_decode_utf7", None)
            if decoder:
                name = decoder(name)
    except Exception:
        pass
    return name, raw


def list_mailboxes(client, provider):
    status, data = client.list()
    if status != "OK":
        raise RuntimeError("The server could not return the mailbox list.")
    folders = []
    for line in data or []:
        if not line:
            continue
        display, raw = decode_mailbox(line)
        if not display:
            continue
        folders.append({"name": display, "raw": raw, "select_name": display})
    # Yahoo can expose its Spam mailbox as Bulk. Show the familiar label but select the real mailbox.
    if provider == "yahoo":
        lower_names = [f["name"].casefold() for f in folders]
        if "spam" not in lower_names:
            for folder in folders:
                if folder["name"].casefold() == "bulk":
                    folder["display_name"] = "Spam"
                    break
    for folder in folders:
        folder.setdefault("display_name", folder["name"])
    folders.sort(key=lambda f: (0 if f["name"].casefold() == "inbox" else 1, f["display_name"].casefold()))
    return folders


def connect_imap(item):
    client = imaplib.IMAP4_SSL(item["host"], item["port"], timeout=20)
    try:
        client.login(item["email"], item["password"])
        return client
    except Exception:
        try:
            client.logout()
        except Exception:
            pass
        raise


def header_value(message, name):
    return clean_header(message.get(name, ""))


def get_ip(message):
    received = message.get_all("Received", []) or []
    text = "\n".join(str(x) for x in received)
    ips = re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", text)
    # Prefer public-looking first occurrence; this is header-derived, not a guaranteed sender IP.
    for ip in ips:
        octets = [int(x) for x in ip.split(".")]
        if all(0 <= x <= 255 for x in octets) and not (octets[0] == 10 or octets[0] == 127 or (octets[0] == 192 and octets[1] == 168) or (octets[0] == 172 and 16 <= octets[1] <= 31)):
            return ip
    return ips[0] if ips else ""


def auth_result(message, key):
    values = message.get_all("Authentication-Results", []) or []
    text = " ".join(str(v) for v in values)
    patterns = {
        "SPF": r"\bspf\s*[:=]\s*(pass|fail|softfail|neutral|none|temperror|permerror)",
        "DKIM": r"\bdkim\s*[:=]\s*(pass|fail|policy|neutral|none|temperror|permerror)",
        "DMARC": r"\bdmarc\s*[:=]\s*(pass|fail|bestguesspass|none|temperror|permerror)",
    }
    match = re.search(patterns[key], text, re.I)
    return match.group(1).upper() if match else "N/A"


def fetch_messages(item, folder, limit):
    client = connect_imap(item)
    rows = []
    try:
        # Select exact mailbox name; quote names with spaces/special characters.
        select_name = folder
        status, _ = client.select('"' + select_name.replace('\\', '\\\\').replace('"', '\\"') + '"', readonly=True)
        if status != "OK":
            status, _ = client.select(select_name, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Could not open mailbox: {folder}")
        status, data = client.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return rows
        ids = data[0].split()[-limit:]
        ids.reverse()
        for msg_id in ids:
            status, parts = client.uid("FETCH", msg_id, "(BODY.PEEK[HEADER])")
            if status != "OK":
                continue
            raw = next((part[1] for part in parts if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
            if not raw:
                continue
            msg = BytesParser(policy=default).parsebytes(raw)
            rows.append({
                "number": msg_id.decode("ascii", "ignore"),
                "uid": msg_id.decode("ascii", "ignore"),
                "ip": get_ip(msg),
                "from": header_value(msg, "From"),
                "subject": header_value(msg, "Subject"),
                "message_id": header_value(msg, "Message-ID"),
                "spf": auth_result(msg, "SPF"),
                "dkim": auth_result(msg, "DKIM"),
                "dmarc": auth_result(msg, "DMARC"),
            })
        return rows
    finally:
        try:
            client.logout()
        except Exception:
            pass


@app.context_processor
def inject_globals():
    store = get_store()
    return {"providers": PROVIDERS, "connected": bool(store), "account_email": store.get("email", "") if store else ""}


@app.get("/")
def index():
    store = get_store()
    folders = store.get("folders", []) if store else []
    rows = store.get("rows", []) if store else []
    return render_template("index.html", folders=folders, rows=rows, selected_folder=store.get("selected_folder", "INBOX") if store else "INBOX", provider=store.get("provider", "gmail") if store else "gmail", count=store.get("count", 50) if store else 50)


@app.post("/connect")
def connect():
    provider = request.form.get("provider", "gmail")
    address = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    if provider not in PROVIDERS or not address or not password:
        flash("دخل مزود البريد، الإيميل، وApp Password.", "error")
        return redirect(url_for("index"))
    config = PROVIDERS[provider]
    item = {"provider": provider, "email": address, "password": password, "host": config["host"], "port": config["port"], "rows": [], "selected_folder": "INBOX", "count": 50}
    try:
        client = connect_imap(item)
        try:
            folders = list_mailboxes(client, provider)
        finally:
            try:
                client.logout()
            except Exception:
                pass
        item["folders"] = folders
        put_store(item)
        flash(f"Connected successfully: {address}", "success")
    except Exception as exc:
        disconnect_store()
        msg = str(exc)
        if provider == "outlook":
            msg += " — Microsoft may reject IMAP App Password authentication; this is an account/provider limitation, not necessarily a wrong password."
        flash("Connection failed: " + msg, "error")
    return redirect(url_for("index"))


@app.post("/extract")
def extract():
    store = get_store()
    if not store:
        flash("Connect your email account first.", "error")
        return redirect(url_for("index"))
    folder = request.form.get("folder", "INBOX")
    try:
        count = max(1, min(int(request.form.get("count", "50")), 300))
    except ValueError:
        count = 50
    allowed = {f["name"] for f in store.get("folders", [])}
    if folder not in allowed:
        flash("Mailbox not found in the current account.", "error")
        return redirect(url_for("index"))
    try:
        rows = fetch_messages(store, folder, count)
        store["rows"] = rows
        store["selected_folder"] = folder
        store["count"] = count
        flash(f"Extraction completed: {len(rows)} message headers.", "success")
    except Exception as exc:
        flash("Extraction failed: " + str(exc), "error")
    return redirect(url_for("index"))


def select_folder(client, folder):
    status, _ = client.select('"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"', readonly=True)
    if status != "OK":
        status, _ = client.select(folder, readonly=True)
    if status != "OK":
        raise RuntimeError(f"Could not open mailbox: {folder}")


@app.get("/download/<uid>")
def download_source(uid):
    store = get_store()
    if not store:
        flash("Connect your email account first.", "error")
        return redirect(url_for("index"))
    row = next((r for r in store.get("rows", []) if r.get("uid") == uid), None)
    if not row:
        flash("Message not found in the current results.", "error")
        return redirect(url_for("index"))
    try:
        client = connect_imap(store)
        try:
            select_folder(client, store.get("selected_folder", "INBOX"))
            status, parts = client.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = None
            if status == "OK":
                raw = next((p[1] for p in parts if isinstance(p, tuple) and isinstance(p[1], bytes)), None)
        finally:
            try:
                client.logout()
            except Exception:
                pass
        if not raw:
            raise RuntimeError("The server returned no message source.")
    except Exception as exc:
        flash("Download failed: " + str(exc), "error")
        return redirect(url_for("index"))
    name = re.sub(r'[^A-Za-z0-9._-]+', "_", row.get("subject") or "email").strip("_")[:60] or "email"
    response = make_response(raw)
    response.headers["Content-Type"] = "message/rfc822"
    response.headers["Content-Disposition"] = f'attachment; filename="{name}_{uid}.eml"'
    return response


@app.get("/export.csv")
def export_csv():
    store = get_store()
    if not store or not store.get("rows"):
        flash("No results to export yet.", "error")
        return redirect(url_for("index"))
    output = io.StringIO(newline="")
    fields = ["number", "ip", "from", "subject", "message_id", "spf", "dkim", "dmarc"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(store["rows"])
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=email_headers.csv"
    return response


@app.post("/disconnect")
def disconnect():
    disconnect_store()
    flash("Disconnected. Credentials cleared from this app's memory.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # Local development only. For internet deployment, use a production WSGI server and HTTPS.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
