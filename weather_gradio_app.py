import gradio as gr
import json
import asyncio
import websockets
import subprocess
import os
import time
import threading
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import uuid
import pandas as pd
import requests

API_KEY = ""

@dataclass
class MCPTool:
    """Represents an MCP tool"""
    name: str
    description: str
    parameters: Dict[str, Any]

class WeatherMCPClient:
    """Client to interact with Claude Desktop's weather MCP server"""
    
    def __init__(self):
        self.tools: List[MCPTool] = []
        self.connected = False
        self.session_id = None
        self.server_process = None
        self.websocket = None
        self.response_futures = {}
        self.claude_config_path = self.find_claude_config()
        
    def find_claude_config(self) -> Optional[str]:
        """Find Claude Desktop configuration file"""
        possible_paths = [
            os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json"),  # macOS
            os.path.expanduser("~/.config/claude/claude_desktop_config.json"),  # Linux
            os.path.expanduser("~/AppData/Roaming/Claude/claude_desktop_config.json"),  # Windows
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                print(f"📁 Found Claude config at: {path}")
                return path
        
        print("❌ Claude Desktop config not found")
        return None
    
    def get_weather_server_config(self) -> Optional[Dict[str, Any]]:
        """Extract weather server configuration from Claude Desktop config"""
        if not self.claude_config_path:
            return None
            
        try:
            with open(self.claude_config_path, 'r') as f:
                config = json.load(f)
            
            mcp_servers = config.get('mcpServers', {})
            weather_server = mcp_servers.get('weather')
            
            if weather_server:
                print(f"🌤️ Found weather server config: {weather_server}")
                return weather_server
            else:
                print("❌ No weather server found in Claude config")
                available_servers = list(mcp_servers.keys())
                print(f"📋 Available servers: {available_servers}")
                return None
                
        except Exception as e:
            print(f"❌ Error reading Claude config: {e}")
            return None
    
    async def connect_to_mcp_server(self) -> bool:
        """Connect to the weather MCP server"""
        weather_config = self.get_weather_server_config()
        if not weather_config:
            return False
        
        try:
            # Start MCP server based on config
            command = weather_config.get('command')
            args = weather_config.get('args', [])
            env = weather_config.get('env', {})
            
            if not command:
                print("❌ No command found in weather server config")
                return False
            
            print(f"🚀 Starting MCP server: {command} {' '.join(args)}")
            
            # Start the MCP server process
            full_command = [command] + args
            server_env = os.environ.copy()
            server_env.update(env)
            
            self.server_process = subprocess.Popen(
                full_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=server_env,
                text=True
            )
            
            # Give server time to start
            await asyncio.sleep(2)
            
            # Initialize MCP session via stdio
            await self.initialize_mcp_session()
            
            return True
            
        except Exception as e:
            print(f"❌ Error connecting to MCP server: {e}")
            return False
    
    async def initialize_mcp_session(self):
        """Initialize MCP session with stdio communication"""
        try:
            # Send initialize request
            init_request = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "clientInfo": {
                        "name": "weather-gradio-client",
                        "version": "1.0.0"
                    }
                }
            }
            
            print("📤 Sending initialize request...")
            await self.send_request(init_request)
            
            # Send initialized notification
            initialized_notification = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }
            
            await self.send_request(initialized_notification)
            
            # Discover tools
            await self.discover_tools()
            
            self.connected = True
            print("✅ MCP session initialized successfully")
            
        except Exception as e:
            print(f"❌ Error initializing MCP session: {e}")
            raise
    
    async def send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send request to MCP server via stdio"""
        if not self.server_process:
            raise Exception("MCP server not started")
        
        try:
            # Send request
            request_str = json.dumps(request) + '\n'
            self.server_process.stdin.write(request_str)
            self.server_process.stdin.flush()
            
            # Read response (if expecting one)
            if request.get("id"):
                response_line = self.server_process.stdout.readline()
                if response_line:
                    response = json.loads(response_line.strip())
                    print(f"📥 Received response: {response}")
                    return response
            
            return {}
            
        except Exception as e:
            print(f"❌ Error sending MCP request: {e}")
            raise
    
    async def discover_tools(self):
        """Discover available tools from the MCP server"""
        try:
            tools_request = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/list"
            }
            
            print("🔍 Discovering MCP tools...")
            response = await self.send_request(tools_request)
            
            if response and "result" in response:
                tools_data = response["result"].get("tools", [])
                self.tools = []
                
                for tool_data in tools_data:
                    tool = MCPTool(
                        name=tool_data["name"],
                        description=tool_data.get("description", ""),
                        parameters=tool_data.get("inputSchema", {})
                    )
                    self.tools.append(tool)
                    print(f"🔧 Found tool: {tool.name} - {tool.description}")
                    
                    # Print detailed parameter info
                    if "properties" in tool.parameters:
                        props = tool.parameters["properties"]
                        required = tool.parameters.get("required", [])
                        print(f"   Parameters:")
                        for prop_name, prop_info in props.items():
                            req_marker = " (required)" if prop_name in required else ""
                            prop_type = prop_info.get("type", "unknown")
                            prop_desc = prop_info.get("description", "")
                            print(f"     • {prop_name} ({prop_type}){req_marker}: {prop_desc}")
                
                print(f"✅ Discovered {len(self.tools)} tools")
                return True
            else:
                print("❌ No tools found in response")
                return False
                
        except Exception as e:
            print(f"❌ Error discovering tools: {e}")
            return False
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a specific MCP tool"""
        if not self.connected:
            return {"error": "Not connected to MCP server"}
        
        try:
            call_request = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            }
            
            print(f"🔧 Calling tool {tool_name} with args: {arguments}")
            response = await self.send_request(call_request)
            
            if response and "result" in response:
                return {
                    "success": True,
                    "data": response["result"]
                }
            elif response and "error" in response:
                return {
                    "success": False,
                    "error": response["error"]["message"]
                }
            else:
                return {
                    "success": False,
                    "error": "Unknown response format"
                }
                
        except Exception as e:
            print(f"❌ Error calling tool {tool_name}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_forecast(self, location: str, latitude: float, longitude: float) -> str:
        """Get weather forecast using MCP GetForecast tool"""
        try:
            # Run async call in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # First try with latitude/longitude (original approach)
            result = loop.run_until_complete(
                self.call_tool("GetForecast", {
                    "latitude": latitude,
                    "longitude": longitude
                })
            )
            
            # If that fails, try with just location string
            if not result.get("success") or result.get("data", {}).get("isError"):
                print(f"🔄 Retrying GetForecast with location string: {location}")
                result = loop.run_until_complete(
                    self.call_tool("GetForecast", {
                        "location": location
                    })
                )
                
                # If still failing, try other common parameter names
                if not result.get("success") or result.get("data", {}).get("isError"):
                    print(f"🔄 Retrying GetForecast with 'query' parameter")
                    result = loop.run_until_complete(
                        self.call_tool("GetForecast", {
                            "query": location
                        })
                    )
            
            loop.close()
            
            if not result.get("success"):
                return f"❌ Error: {result.get('error', 'Unknown error')}"
            
            # Parse the actual MCP response
            data = result["data"]
            
            # The exact format depends on your MCP server's response
            # This is a generic parser that should work with most forecast responses
            if "content" in data:
                # If the response is in content format
                content = data["content"]
                if isinstance(content, list) and len(content) > 0:
                    return content[0].get("text", str(data))
                else:
                    return str(content)
            else:
                # Direct data format
                return f"📊 **Weather Forecast**\n\n{json.dumps(data, indent=2)}"
            
        except Exception as e:
            return f"❌ Error calling GetForecast: {str(e)}"
    
    def get_alerts(self, state: str) -> str:
        """Get weather alerts using MCP GetAlerts tool"""
        try:
            # Run async call in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            result = loop.run_until_complete(
                self.call_tool("GetAlerts", {
                    "state": state
                })
            )
            
            loop.close()
            
            if not result.get("success"):
                return f"❌ Error: {result.get('error', 'Unknown error')}"
            
            # Parse the actual MCP response
            data = result["data"]
            
            # The exact format depends on your MCP server's response
            if "content" in data:
                # If the response is in content format
                content = data["content"]
                if isinstance(content, list) and len(content) > 0:
                    return content[0].get("text", str(data))
                else:
                    return str(content)
            else:
                # Direct data format
                return f"🚨 **Weather Alerts for {state}**\n\n{json.dumps(data, indent=2)}"
            
        except Exception as e:
            return f"❌ Error calling GetAlerts: {str(e)}"
    
    async def connect(self) -> bool:
        """Main connection method"""
        print("🔌 Connecting to Claude Desktop weather MCP server...")
        
        success = await self.connect_to_mcp_server()
        if success:
            print("✅ Successfully connected to MCP server")
            self.list_discovered_tools()
        else:
            print("❌ Failed to connect to MCP server")
        
        return success
    
    def list_discovered_tools(self):
        """List all discovered tools"""
        print(f"\n🔧 Discovered {len(self.tools)} MCP tools:")
        for tool in self.tools:
            print(f"  • {tool.name}: {tool.description}")
            if tool.parameters:
                params = tool.parameters.get("properties", {})
                param_names = list(params.keys())
                print(f"    Parameters: {param_names}")
        print()
    
    def disconnect(self):
        """Disconnect from MCP server"""
        if self.server_process:
            self.server_process.terminate()
            self.server_process = None
        self.connected = False
        print("🔌 Disconnected from MCP server")

# Initialize MCP client
mcp_client = WeatherMCPClient()

def get_coordinates_from_location(location: str):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={location}&key={API_KEY}"
    resp = requests.get(url).json()
    if resp['status'] == 'OK':
        coords = resp['results'][0]['geometry']['location']
        return coords['lat'], coords['lng']
    return None


def connect_to_mcp() -> str:
    """Connect to the MCP server"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(mcp_client.connect())
        loop.close()
        
        if success:
            tools_list = "\n".join([f"• **{tool.name}**: {tool.description}" for tool in mcp_client.tools])
            return f"✅ **Connected to MCP Server!**\n\n**Discovered Tools:**\n{tools_list}"
        else:
            return "❌ **Failed to connect to MCP server**\n\nMake sure:\n1. Claude Desktop is installed\n2. Weather MCP server is configured\n3. Claude Desktop config file exists"
    except Exception as e:
        return f"❌ **Connection Error**: {str(e)}"

def query_weather_forecast(location: str) -> str:
    """Query weather forecast via MCP"""
    if not location.strip():
        return "❌ Please enter a location"
    
    if not mcp_client.connected:
        return "❌ Not connected to MCP server. Please connect first."
    
    coords = get_coordinates_from_location(location)
    latitude, longitude = coords
    
    return mcp_client.get_forecast(location, latitude, longitude)

def query_weather_alerts(state: str) -> str:
    """Query weather alerts via MCP"""
    if not state.strip():
        return "❌ Please enter a US state"
    
    if not mcp_client.connected:
        return "❌ Not connected to MCP server. Please connect first."
    
    return mcp_client.get_alerts(state)

# Create Gradio interface
with gr.Blocks(title="Weather MCP Interface", theme=gr.themes.Soft()) as demo:
    gr.Markdown("""
    # 🌤️ Weather Information via MCP
    
    Connect to your Claude Desktop weather MCP server and use the available tools.
    """)
    
    with gr.Tab("Connection"):
        gr.Markdown("### 🔌 MCP Server Connection")
        gr.Markdown("Connect to your Claude Desktop weather MCP server to discover and use available tools.")
        
        connect_btn = gr.Button("Connect to MCP Server", variant="primary")
        connection_status = gr.Markdown("Click 'Connect' to establish connection with your MCP server.")
        
        connect_btn.click(
            fn=connect_to_mcp,
            outputs=[connection_status]
        )
    
    with gr.Tab("Weather Forecast"):
        gr.Markdown("### 📊 Get Weather Forecast")
        gr.Markdown("Uses the **GetForecast** MCP tool.")
        
        location_input = gr.Textbox(
            label="Location", 
            placeholder="Enter city name (e.g., 'London', 'New York', 'Tokyo')"
        )
        
        forecast_btn = gr.Button("Get Forecast", variant="primary")
        forecast_output = gr.Markdown("Connect to MCP server first, then query forecasts.")
        
        forecast_btn.click(
            fn=query_weather_forecast,
            inputs=[location_input],
            outputs=[forecast_output]
        )
    
    with gr.Tab("Weather Alerts"):
        gr.Markdown("### 🚨 Get Weather Alerts")
        gr.Markdown("Uses the **GetAlerts** MCP tool.")
        
        state_input = gr.Textbox(
            label="US State", 
            placeholder="Enter US state name (e.g., 'California', 'Texas')"
        )
        
        alerts_btn = gr.Button("Get Alerts", variant="secondary")
        alerts_output = gr.Markdown("Connect to MCP server first, then query alerts.")
        
        alerts_btn.click(
            fn=query_weather_alerts,
            inputs=[state_input],
            outputs=[alerts_output]
        )
    
    with gr.Tab("Debug Info"):
        gr.Markdown("""
        ## 🐛 Debug Information
        
        ### Claude Desktop Config Locations:
        - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
        - **Linux**: `~/.config/claude/claude_desktop_config.json`  
        - **Windows**: `~/AppData/Roaming/Claude/claude_desktop_config.json`
        
        ### Expected Config Format:
        ```json
        {
          "mcpServers": {
            "weather": {
              "command": "path/to/weather-server",
              "args": ["--port", "3000"],
              "env": {
                "API_KEY": "your-key"
              }
            }
          }
        }
        ```
        
        ### Connection Process:
        1. **Find Config**: Locate Claude Desktop configuration file
        2. **Extract Server**: Get weather server configuration  
        3. **Start Process**: Launch MCP server with specified command
        4. **Initialize**: Send MCP initialize request
        5. **Discover Tools**: List available tools (GetForecast, GetAlerts)
        6. **Ready**: Server ready for tool calls
        
        ### Troubleshooting:
        - Ensure Claude Desktop is installed and configured
        - Check that weather MCP server is defined in config
        - Verify server command/path is correct
        - Check server logs for startup errors
        """)

# Cleanup function
def cleanup():
    """Cleanup function to disconnect MCP client"""
    mcp_client.disconnect()

if __name__ == "__main__":
    print("🌤️ Starting Weather MCP Interface...")
    print("📡 Server will be available at: http://127.0.0.1:7860")
    
    # Check for Claude config on startup
    config_path = mcp_client.find_claude_config()
    if config_path:
        print(f"📁 Found Claude Desktop config: {config_path}")
        weather_config = mcp_client.get_weather_server_config()
        if weather_config:
            print("🌤️ Weather MCP server configuration found!")
        else:
            print("❌ No weather server in Claude config")
    else:
        print("❌ Claude Desktop config not found")
    
    print("\n🚀 Launching interface...")
    
    try:
        demo.launch(
            server_name="127.0.0.1",
            server_port=7860,
            share=False,
            show_api=False
        )
    finally:
        cleanup()
