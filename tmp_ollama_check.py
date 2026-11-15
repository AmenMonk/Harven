import os, json, shutil, subprocess

out = {}
home = os.path.expanduser("~")
p = os.path.join(home, ".ollama", "models")
out["models_dir"] = p
out["models_dir_exists"] = os.path.exists(p)
out["items"] = os.listdir(p) if os.path.exists(p) else []

sz = 0
cnt = 0
for root, dirs, files in os.walk(p):
    for f in files:
        try:
            sz += os.path.getsize(os.path.join(root, f))
            cnt += 1
        except Exception:
            pass
out["file_count"] = cnt
out["size_gb"] = round(sz / 1024 ** 3, 2)

out["H_OllamaModels_exists"] = os.path.exists(r"H:\OllamaModels")
if out["H_OllamaModels_exists"]:
    try:
        out["H_OllamaModels_items"] = os.listdir(r"H:\OllamaModels")
    except Exception as e:
        out["H_list_err"] = str(e)
try:
    out["H_free_gb"] = round(shutil.disk_usage("H:\\").free / 1024 ** 3, 1)
    out["C_free_gb"] = round(shutil.disk_usage("C:\\").free / 1024 ** 3, 1)
except Exception as e:
    out["disk_err"] = str(e)

out["env_user"] = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "[Environment]::GetEnvironmentVariable('OLLAMA_MODELS','User')"],
    capture_output=True, text=True).stdout.strip()
out["env_machine"] = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "[Environment]::GetEnvironmentVariable('OLLAMA_MODELS','Machine')"],
    capture_output=True, text=True).stdout.strip()

tl = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ollama.exe"],
                    capture_output=True, text=True).stdout
out["ollama_running"] = "ollama.exe" in tl

which = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(Get-Command ollama -ErrorAction SilentlyContinue).Source"],
                       capture_output=True, text=True).stdout.strip()
out["ollama_exe"] = which

print(json.dumps(out, ensure_ascii=False, indent=1))
