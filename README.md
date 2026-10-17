# Universal Email Header Extractor — Web Edition

A Python + Flask web version of the desktop email-header extractor. It opens in a browser and supports Gmail, Hotmail/Outlook, Yahoo, and iCloud IMAP endpoints, mailbox listing, header extraction, search/filtering, and CSV export.

## Requirements
- Python 3.10 or newer
- A mailbox that permits IMAP access
- An app password where supported by the email provider

## Run on Windows
1. Extract the ZIP into a folder, for example `C:\Users\pc\Desktop\UniversalEmailWeb`.
2. Open that folder in File Explorer.
3. Click the address bar, type `cmd`, and press Enter.
4. Create and activate a virtual environment:

```bat
py -m venv .venv
.venv\Scripts\activate
```

5. Install dependencies:

```bat
python -m pip install -r requirements.txt
```

6. Start the web app:

```bat
python app.py
```

7. Open `http://127.0.0.1:5000` in Chrome or Edge.

## Provider notes
- Gmail: use a Google App Password and enable IMAP access if required.
- Yahoo: use a Yahoo App Password. Yahoo may expose Spam as `Bulk`; this app displays that mailbox as Spam when no separate Spam mailbox is returned.
- iCloud: use an Apple app-specific password.
- Hotmail/Outlook: the app attempts IMAP with the supplied password, but Microsoft may reject password-based IMAP and require OAuth2/Modern Authentication. The application cannot bypass a provider's authentication policy.

## Security
- Credentials are kept only in server memory and are not written to a database or file. They are cleared when you disconnect or restart the app, and stale sessions expire after one hour.
- This build binds to `127.0.0.1` by default, so it is local to your computer.
- Do not expose this development server directly to the public internet. A public deployment needs HTTPS, a production WSGI server, secure secret management, CSRF protection, rate limiting, and a reviewed authentication design.
- Header-derived IPs may be absent or may refer to intermediate mail servers; they are not guaranteed to identify the original sender.
