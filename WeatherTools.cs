using Microsoft.IdentityModel.Tokens;
using ModelContextProtocol.Server;
using System;
using System.ComponentModel;
using System.IdentityModel.Tokens.Jwt;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text.Json;

namespace QuickstartWeatherServer.Tools;

[McpServerToolType]
public static class WeatherTools
{
    // ------------------------
    // Self-signed JWT validator
    // ------------------------
    public static class GoogleJwtValidator
    {
        private static readonly string Issuer = "service-account@vertexai-250626.iam.gserviceaccount.com";

        // Validate self-signed JWT with provided public key and audience
        public static ClaimsPrincipal ValidateJwt(string idToken, string publicKeyPath, string expectedAudience)
        {
            if (string.IsNullOrWhiteSpace(idToken))
                throw new ArgumentException("ID token must not be null or empty.", nameof(idToken));

            var rsa = RSA.Create();
            rsa.ImportFromPem(System.IO.File.ReadAllText(publicKeyPath));

            var key = new RsaSecurityKey(rsa); // no KeyId needed

            var validationParameters = new TokenValidationParameters
            {
                ValidateIssuer = true,
                ValidIssuer = Issuer,
                ValidateAudience = true,
                ValidAudience = expectedAudience,
                ValidateLifetime = true,
                RequireSignedTokens = true,
                ValidateIssuerSigningKey = true,
                IssuerSigningKey = key
            };

            var handler = new JwtSecurityTokenHandler();
            return handler.ValidateToken(idToken, validationParameters, out _);
        }
    }

    // ------------------------
    // Helper to convert state names
    // ------------------------
    public static string GetStateByName(string name)
    {
        switch (name.ToUpper())
        {
            case "ALABAMA": return "AL";
            case "ALASKA": return "AK";
            case "ARIZONA": return "AZ";
            case "ARKANSAS": return "AR";
            case "CALIFORNIA": return "CA";
            case "COLORADO": return "CO";
            case "CONNECTICUT": return "CT";
            case "DELAWARE": return "DE";
            case "FLORIDA": return "FL";
            case "GEORGIA": return "GA";
            case "HAWAII": return "HI";
            case "IDAHO": return "ID";
            case "ILLINOIS": return "IL";
            case "INDIANA": return "IN";
            case "IOWA": return "IA";
            case "KANSAS": return "KS";
            case "KENTUCKY": return "KY";
            case "LOUISIANA": return "LA";
            case "MAINE": return "ME";
            case "MARYLAND": return "MD";
            case "MASSACHUSETTS": return "MA";
            case "MICHIGAN": return "MI";
            case "MINNESOTA": return "MN";
            case "MISSISSIPPI": return "MS";
            case "MISSOURI": return "MO";
            case "MONTANA": return "MT";
            case "NEBRASKA": return "NE";
            case "NEVADA": return "NV";
            case "NEW HAMPSHIRE": return "NH";
            case "NEW JERSEY": return "NJ";
            case "NEW MEXICO": return "NM";
            case "NEW YORK": return "NY";
            case "NORTH CAROLINA": return "NC";
            case "NORTH DAKOTA": return "ND";
            case "OHIO": return "OH";
            case "OKLAHOMA": return "OK";
            case "OREGON": return "OR";
            case "PENNSYLVANIA": return "PA";
            case "RHODE ISLAND": return "RI";
            case "SOUTH CAROLINA": return "SC";
            case "SOUTH DAKOTA": return "SD";
            case "TENNESSEE": return "TN";
            case "TEXAS": return "TX";
            case "UTAH": return "UT";
            case "VERMONT": return "VT";
            case "VIRGINIA": return "VA";
            case "WASHINGTON": return "WA";
            case "WEST VIRGINIA": return "WV";
            case "WISCONSIN": return "WI";
            case "WYOMING": return "WY";
            default: throw new Exception("Not Available");
        }
    }

    // ------------------------
    // Get alerts for a state
    // ------------------------
    [McpServerTool, Description("Get weather alerts for a US state.")]
    public static async Task<string> GetAlerts(
        HttpClient client,
        [Description("The US state to get alerts for.")] string state,
        [Description("Self-signed JWT obtained from Python client.")] string authToken
    )
    {
        const string publicKeyPath = "vertexai-250626-public.pem";
        const string expectedAudience = "https://alerts-api.example.com";

        try
        {
            var principal = GoogleJwtValidator.ValidateJwt(authToken, publicKeyPath, expectedAudience);

            var roleClaims = principal.Claims
                .Where(c => c.Type == "roles" || c.Type == ClaimTypes.Role)
                .Select(c => c.Value);

            if (!roleClaims.Contains("alerts:read"))
            {
                return "Access denied: missing required role.";
            }
        }
        catch (Exception ex)
        {
            return $"Invalid token: {ex.Message}";
        }

        string stateAbbreviation = GetStateByName(state);
        var jsonElement = await client.GetFromJsonAsync<JsonElement>($"/alerts/active/area/{stateAbbreviation}");
        var alerts = jsonElement.GetProperty("features").EnumerateArray();

        if (!alerts.Any())
            return "No active alerts for this state.";

        return string.Join("\n--\n", alerts.Select(alert =>
        {
            var properties = alert.GetProperty("properties");
            return $"""
                Event: {properties.GetProperty("event").GetString()}
                Area: {properties.GetProperty("areaDesc").GetString()}
                Severity: {properties.GetProperty("severity").GetString()}
                """;
        }));
    }

    // ------------------------
    // Get forecast for a location
    // ------------------------
    [McpServerTool, Description("Get weather forecast for a location.")]
    public static async Task<string> GetForecast(
        HttpClient client,
        [Description("Latitude of the location.")] double latitude,
        [Description("Longitude of the location.")] double longitude)
    {
        var jsonElement = await client.GetFromJsonAsync<JsonElement>($"/points/{latitude},{longitude}");
        var forecast = jsonElement.GetProperty("properties").GetProperty("forecast").GetString();
        jsonElement = await client.GetFromJsonAsync<JsonElement>(forecast);
        var periods = jsonElement.GetProperty("properties").GetProperty("periods").EnumerateArray();

        return string.Join("\n---\n", periods.Select(period => $"""
            {period.GetProperty("name").GetString()}
            Temperature: {period.GetProperty("temperature").GetInt32()}°F
            Wind: {period.GetProperty("windSpeed").GetString()} {period.GetProperty("windDirection").GetString()}
            Forecast: {period.GetProperty("detailedForecast").GetString()}
            """));
    }
}
