# mpc-weather-server
Weather Server based on Model Context Protocol. The below process will create two tools:

* get-alerts
  Get weather alerts for a state
  From server: weather
* get-forecast
  Get wether forecast for a location
  From server: weather

Claude can use these tools provided by weather server using Model Context Protocol. 

# How to Run
dotnet run /path/to/weather

# To create a new server
## Create new project
dotnet new console

## Import dependencies
dotnet add package ModelContextProtocol --prerelease
dotnet add package Microsoft.Extensions.Hosting

## Add code using MPC SDK and Microsoft libraries
.\Program.cs and .\WeatherTools.cs

## Compile and run
dotnet run

## Test with Claude for Desktop
code $env:AppData\Claude\claude_desktop_config.json

use https://jsonformatter.org/json-pretty-print for below:

{
    "mcpServers": {
        "weather": {
            "command": "dotnet",
            "args": [
               "run",
                "--project",
                "C:\\ABSOLUTE\\PATH\\TO\\PROJECT",
                "--no-build"
            ]
        }
    }
}


