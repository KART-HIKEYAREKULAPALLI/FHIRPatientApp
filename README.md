# Epic FHIR Patient Portal

A FastAPI application that connects to the **Epic Sandbox FHIR Server** using **PKCE OAuth2** authentication to display patient health information including demographics, medications, lab results, and vital signs.

## Features

✅ **PKCE OAuth2 Authentication** - Secure sign-in with Epic accounts using code challenge  
✅ **Patient Demographics** - View name, gender, DOB, contact info  
✅ **Medications List** - All prescriptions with dosage, prescriber, and dispense info  
✅ **Lab Results** - Laboratory observations with reference ranges and interpretations  
✅ **Vital Signs** - Blood pressure, heart rate, temperature with components  
✅ **Pagination** - Navigate through large datasets efficiently  
✅ **Expandable Details** - Click any item to see full details  
✅ **Clean, Modern UI** - Responsive design with tabbed navigation  

## Project Structure

```
epic-fhir-app/
├── main.py              # FastAPI backend with FHIR API calls
├── config.json          # Configuration (URLs, client ID, etc.)
├── requirements.txt     # Python dependencies
├── templates/
│   ├── index.html       # Sign-in page
│   └── dashboard.html   # Patient dashboard with tabs
└── README.md
```

## Configuration

All Epic FHIR configuration is stored in `config.json`:

```json
{
    "epic": {
        "auth_url": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize",
        "token_url": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token",
        "fhir_base_url": "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4",
        "client_id": "YOUR_CLIENT_ID",
        "redirect_uri": "http://localhost:8000/callback",
        "scope": "openid fhirUser patient/*.read launch/patient"
    },
    "app": {
        "host": "0.0.0.0",
        "port": 8000,
        "debug": true
    }
}
```

### Important Notes:

- **client_id**: Your registered Epic application client ID
- **fhir_base_url**: Must be the FHIR R4 endpoint (not STU3)
- **scope**: Defines what resources your app can access
- **redirect_uri**: Must match exactly what's registered with Epic

## Prerequisites

- Python 3.9+
- Epic Sandbox Account (free at [fhir.epic.com](https://fhir.epic.com))
- Registered application with Epic (to get client_id)

## Quick Start

### 1. Clone & Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure

Edit `config.json` with your Epic client credentials:

```json
{
    "epic": {
        "client_id": "your-client-id-here"
    }
}
```

### 3. Run the Application

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Or using Python directly:

```bash
python main.py
```

### 4. Access the Portal

Open your browser to: **http://localhost:8000**

## Using Epic Sandbox

### Test Patient Credentials

When you click "Sign in with Epic", you'll be redirected to Epic's login page. Use these **sandbox test credentials**:

| Patient | Username | Password | FHIR ID |
|---------|----------|----------|---------|
| Camila Lopez | `fhircamila` | `epicepic1` | `erXuFYUfucBZaryVksYEcMg3` |
| Jason Argonaut | `fhirjason` | `epicepic1` | Check Epic docs |
| Derrick Lin | `fhirderrick` | `epicepic1` | `eq081-VQEgP8drUUqCWzHfw3` |

> These are pre-configured test patients in Epic's sandbox environment with various data.

### First-Time Login Flow

1. Click **"Sign in with Epic"** on the home page
2. You'll be redirected to Epic's authorization page
3. Enter test credentials (e.g., `fhircamila` / `epicepic1`)
4. Authorize the application to access your data
5. You'll be redirected back to the dashboard with your patient data

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Sign-in page |
| `/login` | GET | Initiates Epic OAuth2 + PKCE flow |
| `/callback` | GET | OAuth2 callback handler |
| `/dashboard` | GET | Patient dashboard |
| `/api/patient/{session_id}` | GET | Patient demographics |
| `/api/medications/{session_id}` | GET | Patient medications (paginated) |
| `/api/labs/{session_id}` | GET | Lab results (paginated) |
| `/api/vitals/{session_id}` | GET | Vital signs (paginated) |
| `/api/logout/{session_id}` | GET | Logout and clear session |

### Pagination Parameters

All data endpoints support pagination:

- `page` (default: 1) - Page number
- `page_size` (default: 10, max: 50) - Items per page

Example: `/api/medications/abc123?page=2&page_size=20`

## PKCE Authentication Flow

This application uses **PKCE (Proof Key for Code Exchange)** for secure OAuth2:

1. **Generate Verifier**: Random 128-character string (`code_verifier`)
2. **Create Challenge**: SHA256 hash of verifier, base64url encoded (`code_challenge`)
3. **Authorize**: Send challenge with authorization request
4. **Token Exchange**: Send original verifier with token request
5. **Verification**: Epic verifies `SHA256(code_verifier) == code_challenge`

This prevents authorization code interception attacks.

## FHIR Resources Used

- **Patient** - Demographics, identifiers, contact info
- **MedicationRequest** - Prescription medications with dosage
- **Observation (category: laboratory)** - Lab test results
- **Observation (category: vital-signs)** - Vital sign measurements

## Security Notes

⚠️ **Development Only** - This example uses in-memory session storage. For production:

- Use a proper session store (Redis, database)
- Implement token refresh before expiry
- Add HTTPS (required by Epic for production)
- Store `config.json` secrets securely (environment variables)
- Add CSRF protection
- Implement rate limiting

## Troubleshooting

### "Access Denied" Error
- Make sure you're using the correct test patient credentials
- Verify your client_id is correct in config.json
- Check that redirect_uri matches your Epic app registration

### "JSONDecodeError" or Empty Responses
- The access token may have expired (default: 1 hour)
- Verify the FHIR base URL is correct (R4, not STU3)
- Check console logs for response status codes

### Empty Data in Dashboard
- Not all test patients have medications/labs/vitals
- Try a different test patient (fhircamila has varied data)
- Check the "total" count in API responses

### OAuth Redirect Issues
- Ensure you're running on `localhost:8000`
- Check that redirect_uri in config.json matches exactly
- Clear browser cookies and try again

## License

MIT License - Feel free to use and modify for your projects.

## Resources

- [Epic FHIR Documentation](https://fhir.epic.com/Documentation)
- [SMART on FHIR](https://docs.smarthealthit.org/)
- [HL7 FHIR R4 Specification](https://hl7.org/fhir/R4/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [PKCE RFC 7636](https://tools.ietf.org/html/rfc7636)
