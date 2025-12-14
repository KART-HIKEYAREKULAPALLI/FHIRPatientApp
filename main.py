"""
Epic FHIR Patient Portal - FastAPI Backend
Connects to Epic Sandbox FHIR Server for patient data retrieval
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
import os
import secrets
from urllib.parse import urlencode
from datetime import datetime

app = FastAPI(title="Epic FHIR Patient Portal")

# Epic Sandbox Configuration
EPIC_CONFIG = {
    "auth_url": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize",
    "token_url": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token",
    "fhir_base_url": "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4",
    # Epic Sandbox public client ID for patient standalone launch
    "client_id": "990e5d51-e8c1-4d70-8033-45fcbeeeaa40",
    "redirect_uri": "http://localhost:8000/callback",
    "scope": "openid fhirUser patient/*.read launch/patient"
}

# In-memory session storage (use Redis/DB in production)
sessions = {}

# Templates
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login")
async def login():
    """Initiate Epic OAuth2 login flow"""
    state = secrets.token_urlsafe(32)
    sessions[state] = {"status": "pending"}
    
    params = {
        "response_type": "code",
        "client_id": EPIC_CONFIG["client_id"],
        "redirect_uri": EPIC_CONFIG["redirect_uri"],
        "scope": EPIC_CONFIG["scope"],
        "state": state,
        "aud": EPIC_CONFIG["fhir_base_url"]
    }
    
    auth_url = f"{EPIC_CONFIG['auth_url']}?{urlencode(params)}"
    return RedirectResponse(url=auth_url)


@app.get("/callback")
async def callback(code: str = None, state: str = None, error: str = None):
    """Handle OAuth2 callback from Epic"""
    if error:
        return RedirectResponse(url=f"/?error={error}")
    
    if not code or not state:
        return RedirectResponse(url="/?error=missing_params")
    
    if state not in sessions:
        return RedirectResponse(url="/?error=invalid_state")
    
    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        try:
            token_response = await client.post(
                EPIC_CONFIG["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": EPIC_CONFIG["redirect_uri"],
                    "client_id": EPIC_CONFIG["client_id"]
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if token_response.status_code != 200:
                return RedirectResponse(url=f"/?error=token_error&details={token_response.text}")
            
            token_data = token_response.json()
            
            # Store session data
            sessions[state] = {
                "status": "authenticated",
                "access_token": token_data.get("access_token"),
                "patient_id": token_data.get("patient"),
                "expires_in": token_data.get("expires_in")
            }
            
            return RedirectResponse(url=f"/dashboard?session={state}")
            
        except Exception as e:
            return RedirectResponse(url=f"/?error=exception&details={str(e)}")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, session: str = None):
    """Render the patient dashboard"""
    if not session or session not in sessions:
        return RedirectResponse(url="/?error=no_session")
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "session_id": session
    })


@app.get("/api/patient/{session_id}")
async def get_patient(session_id: str):
    """Get patient demographics"""
    session = sessions.get(session_id)
    if not session or session.get("status") != "authenticated":
        raise HTTPException(status_code=401, detail="Invalid session")
    
    patient_id = session.get("patient_id")
    access_token = session.get("access_token")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{EPIC_CONFIG['fhir_base_url']}/Patient/{patient_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch patient data")
        
        patient_data = response.json()
        
        # Parse patient data
        name = "Unknown"
        if patient_data.get("name"):
            name_obj = patient_data["name"][0]
            given = " ".join(name_obj.get("given", []))
            family = name_obj.get("family", "")
            name = f"{given} {family}".strip()
        
        return {
            "id": patient_data.get("id"),
            "name": name,
            "gender": patient_data.get("gender", "Unknown").title(),
            "birthDate": patient_data.get("birthDate", "Unknown"),
            "identifier": patient_data.get("identifier", [{}])[0].get("value", "N/A"),
            "address": format_address(patient_data.get("address", [])),
            "phone": format_telecom(patient_data.get("telecom", []), "phone"),
            "email": format_telecom(patient_data.get("telecom", []), "email"),
            "maritalStatus": patient_data.get("maritalStatus", {}).get("text", "Unknown"),
            "language": get_preferred_language(patient_data)
        }


@app.get("/api/medications/{session_id}")
async def get_medications(session_id: str):
    """Get patient medications"""
    session = sessions.get(session_id)
    if not session or session.get("status") != "authenticated":
        raise HTTPException(status_code=401, detail="Invalid session")
    
    patient_id = session.get("patient_id")
    access_token = session.get("access_token")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{EPIC_CONFIG['fhir_base_url']}/MedicationRequest",
            params={"patient": patient_id, "_count": 100},
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if response.status_code != 200:
            return {"medications": [], "error": "Failed to fetch medications"}
        
        bundle = response.json()
        medications = []
        
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            
            # Get medication name
            med_name = "Unknown Medication"
            if resource.get("medicationCodeableConcept"):
                med_name = resource["medicationCodeableConcept"].get("text", med_name)
            elif resource.get("medicationReference"):
                med_name = resource["medicationReference"].get("display", med_name)
            
            # Get dosage instructions
            dosage = "No dosage information"
            if resource.get("dosageInstruction"):
                dosage = resource["dosageInstruction"][0].get("text", dosage)
            
            # Get status
            status = resource.get("status", "unknown").title()
            
            # Get authored date
            authored = resource.get("authoredOn", "Unknown date")
            
            medications.append({
                "name": med_name,
                "dosage": dosage,
                "status": status,
                "authoredOn": authored
            })
        
        return {"medications": medications}


@app.get("/api/labs/{session_id}")
async def get_labs(session_id: str):
    """Get patient lab results"""
    session = sessions.get(session_id)
    if not session or session.get("status") != "authenticated":
        raise HTTPException(status_code=401, detail="Invalid session")
    
    patient_id = session.get("patient_id")
    access_token = session.get("access_token")
    
    async with httpx.AsyncClient() as client:
        # Query for laboratory observations
        response = await client.get(
            f"{EPIC_CONFIG['fhir_base_url']}/Observation",
            params={
                "patient": patient_id,
                "category": "laboratory",
                "_count": 100,
                "_sort": "-date"
            },
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if response.status_code != 200:
            return {"labs": [], "error": "Failed to fetch lab results"}
        
        bundle = response.json()
        labs = []
        
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            
            # Get test name
            test_name = resource.get("code", {}).get("text", "Unknown Test")
            
            # Get value
            value = "N/A"
            unit = ""
            if resource.get("valueQuantity"):
                value = resource["valueQuantity"].get("value", "N/A")
                unit = resource["valueQuantity"].get("unit", "")
            elif resource.get("valueString"):
                value = resource["valueString"]
            elif resource.get("valueCodeableConcept"):
                value = resource["valueCodeableConcept"].get("text", "N/A")
            
            # Get reference range
            ref_range = "N/A"
            if resource.get("referenceRange"):
                range_obj = resource["referenceRange"][0]
                low = range_obj.get("low", {}).get("value", "")
                high = range_obj.get("high", {}).get("value", "")
                if low and high:
                    ref_range = f"{low} - {high}"
            
            # Get status and date
            status = resource.get("status", "unknown").title()
            effective_date = resource.get("effectiveDateTime", "Unknown date")
            
            # Get interpretation
            interpretation = "Normal"
            if resource.get("interpretation"):
                interpretation = resource["interpretation"][0].get("text", 
                    resource["interpretation"][0].get("coding", [{}])[0].get("display", "Normal"))
            
            labs.append({
                "name": test_name,
                "value": f"{value} {unit}".strip(),
                "referenceRange": ref_range,
                "status": status,
                "date": effective_date,
                "interpretation": interpretation
            })
        
        return {"labs": labs}


@app.get("/api/vitals/{session_id}")
async def get_vitals(session_id: str):
    """Get patient vital signs"""
    session = sessions.get(session_id)
    if not session or session.get("status") != "authenticated":
        raise HTTPException(status_code=401, detail="Invalid session")
    
    patient_id = session.get("patient_id")
    access_token = session.get("access_token")
    
    async with httpx.AsyncClient() as client:
        # Query for vital signs observations
        response = await client.get(
            f"{EPIC_CONFIG['fhir_base_url']}/Observation",
            params={
                "patient": patient_id,
                "category": "vital-signs",
                "_count": 100,
                "_sort": "-date"
            },
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if response.status_code != 200:
            return {"vitals": [], "error": "Failed to fetch vital signs"}
        
        bundle = response.json()
        vitals = []
        
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            
            # Get vital sign name
            vital_name = resource.get("code", {}).get("text", "Unknown Vital")
            
            # Get value - handle different types
            value = "N/A"
            unit = ""
            
            if resource.get("valueQuantity"):
                value = resource["valueQuantity"].get("value", "N/A")
                unit = resource["valueQuantity"].get("unit", "")
            elif resource.get("component"):
                # Handle multi-component vitals like blood pressure
                components = []
                for comp in resource["component"]:
                    comp_name = comp.get("code", {}).get("text", "")
                    comp_value = comp.get("valueQuantity", {}).get("value", "")
                    comp_unit = comp.get("valueQuantity", {}).get("unit", "")
                    if comp_value:
                        components.append(f"{comp_value}")
                value = "/".join(components) if components else "N/A"
                unit = resource["component"][0].get("valueQuantity", {}).get("unit", "") if resource["component"] else ""
            
            # Get date and status
            effective_date = resource.get("effectiveDateTime", "Unknown date")
            status = resource.get("status", "unknown").title()
            
            vitals.append({
                "name": vital_name,
                "value": f"{value} {unit}".strip(),
                "date": effective_date,
                "status": status
            })
        
        return {"vitals": vitals}


@app.get("/api/logout/{session_id}")
async def logout(session_id: str):
    """Logout and clear session"""
    if session_id in sessions:
        del sessions[session_id]
    return {"message": "Logged out successfully"}


# Helper functions
def format_address(addresses):
    """Format address from FHIR Address array"""
    if not addresses:
        return "No address on file"
    
    addr = addresses[0]
    lines = addr.get("line", [])
    city = addr.get("city", "")
    state = addr.get("state", "")
    postal = addr.get("postalCode", "")
    
    address_parts = lines + [f"{city}, {state} {postal}".strip()]
    return ", ".join([p for p in address_parts if p])


def format_telecom(telecoms, system):
    """Extract telecom value by system type"""
    for telecom in telecoms:
        if telecom.get("system") == system:
            return telecom.get("value", "N/A")
    return "N/A"


def get_preferred_language(patient_data):
    """Get preferred language from patient data"""
    communication = patient_data.get("communication", [])
    for comm in communication:
        if comm.get("preferred"):
            return comm.get("language", {}).get("text", "English")
    return "English"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
