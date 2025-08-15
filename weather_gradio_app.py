import gradio as gr
import json
import asyncio
import subprocess
import os
import uuid
import requests
import base64
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

# ------------------------
# Config
# ------------------------
API_KEY = os.environ.get("GOOGLE_GEOCODE_API_KEY", "<redacted>")
REQUIRED_ROLE = os.environ.get("REQUIRED_ROLE", "alerts:read")

# ------------------------
# MCP Client
# ------------------------
@dataclass
class MCPTool:
    name: str
    description: str
    parameters: Dict[str, Any]

class WeatherMCPClient:
    def __init__(self):
        self.tools: List[MCPTool] = []
        self.connected = False
        self.server_process = None
        self.claude_config_path = self.find_claude_config()

    def find_claude_config(self) -> Optional[str]:
        paths = [
            os.path.expanduser("~/mcp-weather-server/claude_desktop_config.json"),
            os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json"),
            os.path.expanduser("~/.config/claude/claude_desktop_config.json"),
            os.path.expanduser("~/AppData/Roaming/Claude/claude_desktop_config.json"),
        ]
        for path in paths:
            if os.path.exists(path):
                return path
        return None

    async def connect_to_mcp_server(self) -> bool:
        config = self.get_weather_server_config()
        if not config or "command" not in config:
            return False
        try:
            full_cmd = [config["command"]] + config.get("args", [])
            env = os.environ.copy()
            env.update(config.get("env", {}))
            self.server_process = subprocess.Popen(
                full_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
            )
            await asyncio.sleep(2)
            await self.initialize_mcp_session()
            return True
        except Exception as e:
            print(f"Error connecting to MCP server: {e}")
            return False

    async def initialize_mcp_session(self):
        if not self.server_process:
            raise Exception("MCP server not started")
        init_req = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "weather-gradio-client", "version": "1.0.0"}
            }
        }
        await self.send_request(init_req)
        await self.send_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        await self.discover_tools()
        self.connected = True

    async def send_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        req_str = json.dumps(req) + "\n"
        self.server_process.stdin.write(req_str)
        self.server_process.stdin.flush()
        if "id" in req:
            line = self.server_process.stdout.readline()
            if line:
                return json.loads(line.strip())
        return {}

    async def discover_tools(self):
        req = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/list"}
        resp = await self.send_request(req)
        self.tools = []
        if resp and "result" in resp:
            for t in resp["result"].get("tools", []):
                self.tools.append(MCPTool(name=t["name"], description=t.get("description", ""), parameters=t.get("inputSchema", {})))
        return bool(self.tools)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self.connected:
            return {"error": "Not connected to MCP server"}
        try:
            req = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}}
            resp = await self.send_request(req)
            if resp.get("result"):
                return {"success": True, "data": resp["result"]}
            elif resp.get("error"):
                return {"success": False, "error": resp["error"]["message"]}
            else:
                return {"success": False, "error": "Unknown response format"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_weather_server_config(self) -> Optional[Dict[str, Any]]:
        if not self.claude_config_path:
            return None
        try:
            with open(self.claude_config_path, 'r') as f:
                cfg = json.load(f)
            return cfg.get("mcpServers", {}).get("weather")
        except Exception as e:
            print(f"Error reading Claude config: {e}")
            return None

    def get_forecast(self, location: str, latitude: float, longitude: float) -> str:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            res = loop.run_until_complete(self.call_tool("get_forecast", {"latitude": latitude, "longitude": longitude}))
            loop.close()
            if not res.get("success"):
                return f"Error: {res.get('error','Unknown error')}"
            data = res["data"]
            if "content" in data and isinstance(data["content"], list) and len(data["content"]) > 0:
                return data["content"][0].get("text", str(data))
            return f"**Weather Forecast**\n\n{json.dumps(data, indent=2)}"
        except Exception as e:
            return f"Error calling get_forecast: {e}"

    def get_alerts(self, state: str, id_token: Optional[str]) -> str:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            args = {"state": state}
            if id_token:
                args["authToken"] = id_token
            res = loop.run_until_complete(self.call_tool("get_alerts", args))
            loop.close()
            if not res.get("success"):
                return f"Error: {res.get('error','Unknown error')}"
            data = res["data"]
            if "content" in data and isinstance(data["content"], list) and len(data["content"]) > 0:
                return data["content"][0].get("text", str(data))
            return f"🚨 **Weather Alerts for {state}**\n\n{json.dumps(data, indent=2)}"
        except Exception as e:
            return f"Error calling get_alerts: {e}"

    async def connect(self) -> bool:
        return await self.connect_to_mcp_server()

    def disconnect(self):
        if self.server_process:
            self.server_process.terminate()
            self.server_process = None
        self.connected = False

# ------------------------
# JWT Helpers
# ------------------------
def _b64url_decode(s: str) -> bytes:
    rem = len(s) % 4
    if rem > 0:
        s += "=" * (4 - rem)
    return base64.urlsafe_b64decode(s.encode())

def parse_jwt_no_verify(token: str) -> Optional[Dict[str, Any]]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        return json.loads(_b64url_decode(parts[1]))
    except Exception as e:
        print(f"Failed to parse token: {e}")
        return None

# ------------------------
# Geocoding
# ------------------------
def get_coordinates_from_location(location: str):
    if not API_KEY:
        return None
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={location}&key={API_KEY}"
    resp = requests.get(url).json()
    if resp.get("status") == "OK":
        loc = resp["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    return None

# ------------------------
# MCP Client instance
# ------------------------
mcp_client = WeatherMCPClient()

# ------------------------
# Gradio login/query functions
# ------------------------
def accept_id_token(pasted_token: str):
    if not pasted_token.strip():
        return "No token provided", None, None
    payload = parse_jwt_no_verify(pasted_token.strip())
    if not payload:
        return "Failed to parse token", None, None

    roles = []
    if isinstance(payload.get("roles"), list):
        roles = payload["roles"]
    elif "role" in payload:
        roles = [payload["role"]]
    elif isinstance(payload.get("groups"), list):
        roles = payload["groups"]

    # Service accounts automatically get alerts access
    if payload.get("sub", "").endswith("gserviceaccount.com"):
        if REQUIRED_ROLE not in roles:
            roles.append(REQUIRED_ROLE)

    user_ctx = {"sub": payload.get("sub"), "roles": roles, "claims": payload}
    display = f"Token accepted. Subject: {payload.get('sub')}\nClaims: {', '.join(roles) or '(none)'}"
    return display, user_ctx, pasted_token.strip()

def clear_token():
    return "Logged out.", None, None

def connect_to_mcp() -> str:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(mcp_client.connect())
        loop.close()
        if success:
            tools_list = "\n".join([f"• **{t.name}**: {t.description}" for t in mcp_client.tools])
            return f"**Connected to MCP Server!**\n\n**Discovered Tools:**\n{tools_list}"
        return "**Failed to connect to MCP server**"
    except Exception as e:
        return f"**Connection Error**: {e}"

def query_weather_forecast(location: str) -> str:
    if not location.strip():
        return "Please enter a location"
    if not mcp_client.connected:
        return "Not connected to MCP server. Please connect first."
    coords = get_coordinates_from_location(location)
    if not coords:
        return "Failed to geocode location"
    return mcp_client.get_forecast(location, coords[0], coords[1])

def query_weather_alerts(state: str, user_ctx, id_token) -> str:
    if not user_ctx or REQUIRED_ROLE not in (user_ctx.get("roles") or []):
        return "Access denied: you don't have permission to view weather alerts."
    if not state.strip():
        return "Please enter a US state"
    if not mcp_client.connected:
        return "Not connected to MCP server. Please connect first."
    return mcp_client.get_alerts(state, id_token)

# ------------------------
# Build Gradio UI
# ------------------------
with gr.Blocks(title="Weather MCP Interface (GCP OIDC)", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# Weather Information via MCP (GCP ID Token based RBAC)")

    # Connection tab
    with gr.Tab("Connection"):
        gr.Markdown("### 🔌 MCP Server Connection")
        connect_btn = gr.Button("Connect to MCP Server", variant="primary")
        connection_status = gr.Markdown("Click 'Connect' to establish connection with your MCP server.")
        connect_btn.click(fn=connect_to_mcp, outputs=[connection_status])

    # Forecast tab
    with gr.Tab("Weather Forecast"):
        gr.Markdown("### Get Weather Forecast")
        location_input = gr.Textbox(label="Location", placeholder="Enter city name (e.g., 'London')")
        forecast_btn = gr.Button("Get Forecast", variant="primary")
        forecast_output = gr.Markdown("Connect to MCP server first, then query forecasts.")
        forecast_btn.click(fn=query_weather_forecast, inputs=[location_input], outputs=[forecast_output])

    # Login tab
    with gr.Tab("Login"):
        gr.Markdown("### Sign in with your Google ID token")
        gr.Markdown("Authenticate separately, then paste your `id_token` (JWT) here.")
        idtoken_input = gr.Textbox(label="Paste Google ID Token (id_token)", lines=3)
        accept_btn = gr.Button("Accept ID Token", variant="primary")
        logout_btn = gr.Button("Logout")
        login_status = gr.Markdown("Not signed in.")
        current_user_state = gr.State(value=None)
        auth_token_state = gr.State(value=None)

        accept_btn.click(fn=accept_id_token, inputs=[idtoken_input], outputs=[login_status, current_user_state, auth_token_state])
        logout_btn.click(fn=clear_token, inputs=[], outputs=[login_status, current_user_state, auth_token_state])

    # Alerts tab (hidden by default)
    with gr.Tab("Weather Alerts", visible=False) as alerts_tab:
        gr.Markdown("### Get Weather Alerts (RBAC protected)")
        state_input = gr.Textbox(label="US State", placeholder="Enter US state name (e.g., 'California')")
        alerts_btn = gr.Button("Get Alerts", variant="secondary")
        alerts_output = gr.Markdown("Login first, then query alerts.")
        alerts_btn.click(fn=query_weather_alerts, inputs=[state_input, current_user_state, auth_token_state], outputs=[alerts_output])

    # Toggle alerts tab visibility based on login
    def _alerts_tab_vis(user_ctx):
        return gr.update(visible=bool(user_ctx and REQUIRED_ROLE in (user_ctx.get("roles") or [])))

    current_user_state.change(fn=_alerts_tab_vis, inputs=[current_user_state], outputs=[alerts_tab])

# Cleanup
def cleanup():
    mcp_client.disconnect()

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860, share=False, show_api=False)
    cleanup()
