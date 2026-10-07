import http.server
import socketserver
import json
import os
import sys
import urllib.request
import urllib.error

# Ensure local directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mcp_server import OneCMetadataMCPServer

PORT = int(os.environ.get("PORT", 8000))

def load_secrets():
    secrets = {}
    secrets_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.env")
    if os.path.exists(secrets_file):
        with open(secrets_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    secrets[k.strip()] = v.strip()
    return secrets

def load_system_prompt():
    prompt_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt")
    if os.path.exists(prompt_file):
        with open(prompt_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return """Ты — ведущий эксперт-разработчик 1С:Предприятие 8.3 с глубокими знаниями языка запросов и Схемы Компоновки Данных (СКД)."""

class BoardRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        metadata_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "metadata.json")
        if not os.path.exists(metadata_path):
            metadata_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metadata.json")
        self.mcp_server = OneCMetadataMCPServer(metadata_path)
        self.system_prompt = load_system_prompt()
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            if os.path.exists(index_path):
                with open(index_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.wfile.write(b"<h1>1C Query AI Board</h1><p>index.html not found</p>")
        elif self.path == "/status":
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            secrets = load_secrets()
            api_key_set = bool(secrets.get("OPENAI_API_KEY"))
            res = {
                "status": "online",
                "api_key_configured": api_key_set,
                "model": secrets.get("OPENAI_MODEL", "gpt-4o"),
                "metadata_loaded": bool(self.mcp_server.metadata),
                "system_prompt_loaded": bool(self.system_prompt)
            }
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/generate":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            prompt = data.get("prompt", "")

            # 1. Ищем объекты в 1С через MCP-сервер
            search_res = self.mcp_server.search_metadata(prompt)
            
            secrets = load_secrets()
            api_key = secrets.get("OPENAI_API_KEY", "")
            model = secrets.get("OPENAI_MODEL", "gpt-4o")
            base_url = secrets.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

            # Базовый ответ (на случай ошибок или отсутствия ключа)
            response_data = {
                "status": "error",
                "prompt": prompt,
                "mcp_results": search_res,
                "system_prompt_used": self.system_prompt,
                "api_key_present": bool(api_key),
                "bsl_code": "// Ошибка: API ключ не настроен в secrets.env.",
                "parameters": [],
                "architecture_comment": "Невозможно сгенерировать код без подключения к LLM."
            }

            # 2. Если есть ключ, отправляем запрос к ИИ (OpenAI API)
            if api_key:
                system_content = (
                    f"{self.system_prompt}\n\n"
                    f"ДОСТУПНЫЕ МЕТАДАННЫЕ ИЗ 1С (Используй строго эти имена):\n"
                    f"{json.dumps(search_res, ensure_ascii=False)}\n\n"
                    f"ВАЖНО: Верни ответ СТРОГО в формате JSON с ключами:\n"
                    f"- bsl_code (строка с готовым кодом 1С)\n"
                    f"- parameters (массив строк, например ['&Период', '&Склад'])\n"
                    f"- architecture_comment (краткое текстовое описание архитектуры пакета)"
                )
                
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"}
                }
                
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }
                
                req = urllib.request.Request(
                    f"{base_url.rstrip('/')}/chat/completions",
                    data=json.dumps(payload).encode('utf-8'),
                    headers=headers,
                    method="POST"
                )
                
                try:
                    with urllib.request.urlopen(req) as response:
                        res_body = response.read()
                        llm_response = json.loads(res_body.decode('utf-8'))
                        llm_content = llm_response['choices'][0]['message']['content']
                        parsed_content = json.loads(llm_content)
                        
                        response_data["status"] = "success"
                        response_data["bsl_code"] = parsed_content.get("bsl_code", "// Ошибка: ИИ не вернул код")
                        response_data["parameters"] = parsed_content.get("parameters", [])
                        response_data["architecture_comment"] = parsed_content.get("architecture_comment", "")
                except Exception as e:
                    response_data["status"] = "error"
                    response_data["bsl_code"] = f"// Ошибка при обращении к API ИИ:\n// {str(e)}"
                    response_data["architecture_comment"] = "Сбой интеграции с языковой моделью."

            # Возврат итогового JSON на фронтенд
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode('utf-8'))

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"[INFO] Starting 1C AI Query Board Server on http://localhost:{PORT}", file=sys.stderr)
    with socketserver.TCPServer(("", PORT), BoardRequestHandler) as httpd:
        httpd.serve_forever()