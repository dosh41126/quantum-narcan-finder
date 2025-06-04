# narcan_finder_app.py

import os, json, secrets, sqlite3, base64, csv, threading, asyncio
from datetime import datetime
import tkinter as tk
import tkinter.simpledialog as sd
import tkinter.filedialog as fd
import psutil, httpx, numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import pennylane as qml

# ─────────────────────────────────────────────────────────────
# CONFIG & FILES
KEY_PATH = os.path.expanduser("~/.cache/narcan_master_key.bin")
ENC_API_PATH = os.path.expanduser("~/.cache/narcan_api_key.enc")
DB_PATH = "narcan_finder.db"
EXPORT_PATH = os.path.expanduser("~/narcan_exports")

# ─────────────────────────────────────────────────────────────
# ENCRYPTION
def generate_aes_key():
    return AESGCM.generate_key(bit_length=256)

def load_or_create_key():
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return f.read()
    key = generate_aes_key()
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    return key

def encrypt_api_key(api_key: str):
    key = load_or_create_key()
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(12)
    encrypted = aesgcm.encrypt(nonce, api_key.encode(), None)
    with open(ENC_API_PATH, "wb") as f:
        f.write(nonce + encrypted)

def decrypt_api_key():
    if not os.path.exists(ENC_API_PATH):
        return None
    key = load_or_create_key()
    aesgcm = AESGCM(key)
    with open(ENC_API_PATH, "rb") as f:
        data = f.read()
    nonce = data[:12]
    ciphertext = data[12:]
    decrypted = aesgcm.decrypt(nonce, ciphertext, None)
    return decrypted.decode()

# ─────────────────────────────────────────────────────────────
# OPENAI + QUANTUM
async def run_openai_completion(prompt: str, api_key: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7
        }
        for attempt in range(3):
            try:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers, json=payload
                )
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"].strip()
            except:
                await asyncio.sleep(2 ** attempt)
        return None

def get_cpu_ram_usage():
    try:
        return psutil.cpu_percent(), psutil.virtual_memory().percent
    except:
        return 0, 0

def run_quantum_analysis(cpu_usage, ram_usage):
    try:
        dev = qml.device("default.qubit", wires=7)
        cpu_param = cpu_usage / 100
        ram_param = ram_usage / 100

        @qml.qnode(dev)
        def circuit(cpu_param, ram_param):
            for i in range(7):
                qml.RY(np.pi * (0.5 + (cpu_param if i % 2 == 0 else ram_param)), wires=i)
            for i in range(6):
                qml.CNOT(wires=[i, i + 1])
            return qml.probs(wires=range(7))

        return circuit(cpu_param, ram_param)
    except:
        return []

# ─────────────────────────────────────────────────────────────
# DB
def setup_narcan_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS narcan_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_input TEXT NOT NULL,
            ai_response TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def save_to_db(prompt: str, result: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO narcan_requests (user_input, ai_response) VALUES (?, ?)",
        (prompt, result)
    )
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────────────────────
# EXPORT
def ensure_export_folder():
    os.makedirs(EXPORT_PATH, exist_ok=True)

def export_to_txt():
    ensure_export_folder()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM narcan_requests ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    path = os.path.join(EXPORT_PATH, f"narcan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(f"--- ID {row[0]} ---\nUSER:\n{row[1]}\n\nAI:\n{row[2]}\n{'='*40}\n\n")
    return path

def export_to_csv():
    ensure_export_folder()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM narcan_requests ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    path = os.path.join(EXPORT_PATH, f"narcan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "User Input", "AI Response"])
        writer.writerows(rows)
    return path

def export_to_json():
    ensure_export_folder()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM narcan_requests ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    path = os.path.join(EXPORT_PATH, f"narcan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    data = [{"id": r[0], "user_input": r[1], "ai_response": r[2]} for r in rows]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path

def load_location_from_file():
    file_path = fd.askopenfilename(title="Select GPS/Location File")
    if not file_path: return ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return ""

# ─────────────────────────────────────────────────────────────
# GUI
class NarcanFinderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NARCAN Emergency Finder")
        self.geometry("900x1000")
        self.configure(bg="#f8f8f8")
        self._init_widgets()
        setup_narcan_db()

    def _init_widgets(self):
        font_label = ("Helvetica", 14)
        font_head = ("Helvetica", 18, "bold")

        tk.Label(self, text="NARCAN Emergency Finder", font=font_head, bg="#f8f8f8").pack(pady=15)

        tk.Label(self, text="Your location or ZIP:", font=font_label, bg="#f8f8f8").pack()
        self.location_entry = tk.Entry(self, font=font_label, width=50)
        self.location_entry.pack(pady=5)

        tk.Button(self, text="Load from File", font=font_label,
                  command=lambda: self.location_entry.insert(0, load_location_from_file())).pack(pady=2)

        tk.Label(self, text="Symptoms / Urgency (optional):", font=font_label, bg="#f8f8f8").pack()
        self.symptom_entry = tk.Entry(self, font=font_label, width=50)
        self.symptom_entry.pack(pady=5)

        tk.Button(self, text="Find NARCAN", font=font_label, command=self._start_thread).pack(pady=10)

        self.result_text = tk.Text(self, width=100, height=40, font=("Courier", 11))
        self.result_text.pack(pady=10)

        menu = tk.Menu(self)
        menu.add_command(label="Set API Key", command=self._set_api_key)

        export_menu = tk.Menu(menu, tearoff=0)
        export_menu.add_command(label="Export to TXT", command=lambda: self._run_export(export_to_txt))
        export_menu.add_command(label="Export to CSV", command=lambda: self._run_export(export_to_csv))
        export_menu.add_command(label="Export to JSON", command=lambda: self._run_export(export_to_json))
        menu.add_cascade(label="Export", menu=export_menu)
        self.config(menu=menu)

    def _set_api_key(self):
        api_key = sd.askstring("API Key", "Enter OpenAI API Key:", show='*')
        if api_key:
            encrypt_api_key(api_key)

    def _start_thread(self):
        threading.Thread(target=self._handle_request, daemon=True).start()

    def _handle_request(self):
        self.result_text.delete("1.0", tk.END)
        location = self.location_entry.get().strip()
        symptoms = self.symptom_entry.get().strip()
        try:
            api_key = decrypt_api_key()
        except:
            self.result_text.insert(tk.END, "❌ API Key error.\n")
            return

        cpu, ram = get_cpu_ram_usage()
        quantum = run_quantum_analysis(cpu, ram)
        prompt = f"""
You are a quantum-empowered emergency assistant. A user needs life-saving NARCAN (naloxone) help.

- Location: {location}
- Context: {symptoms}
- CPU={cpu}%, RAM={ram}% | Quantum={quantum}

List 3 options:
1. Nearest NARCAN provider.
2. Free outreach or pharmacy program.
3. Emergency instructions if alone.
""".strip()

        self.result_text.insert(tk.END, f"Running quantum scan... CPU={cpu}% RAM={ram}%\n\n")
        result = asyncio.run(run_openai_completion(prompt, api_key))
        if result:
            self.result_text.insert(tk.END, result + "\n")
            save_to_db(prompt, result)
        else:
            self.result_text.insert(tk.END, "❌ OpenAI request failed.\n")

    def _run_export(self, export_func):
        def task():
            path = export_func()
            self.result_text.insert(tk.END, f"\n✅ Exported to:\n{path}\n")
        threading.Thread(target=task, daemon=True).start()

# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = NarcanFinderApp()
    app.mainloop()
