"""
Epic FHIR Patient Portal - FastAPI Backend
Connects to Epic Sandbox FHIR Server with PKCE OAuth2 for patient data retrieval
"""

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
import json
import os
import secrets
import hashlib
import base64
from urllib.parse import urlencode
from datetime import datetime
from typing import Optional

app = FastAPI(title="Epic FHIR Patient Portal")

# Load configuration from config.json
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r") as f:
        return json.load(f)

CONFIG = load_config()
EPIC_CONFIG = CONFIG["epic"]

# In-memory session storage (use Redis/DB in production)
sessions = {}

# Templates
templates = Jinja2Templates(directory="templates")


# PKCE Helper Functions
def generate_code_verifier() -> str:
    """Generate a cryptographically random code verifier for PKCE"""
    return secrets.token_urlsafe(64)[:128]


def generate_code_challenge(code_verifier: str) -> str:
    """Generate code challenge from code verifier using SHA256"""
    digest = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('utf-8')


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login")
async def login():
    """Initiate Epic OAuth2 login flow with PKCE"""
    state = secrets.token_urlsafe(32)
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)
    
    # Store PKCE verifier in session for later use
    sessions[state] = {
        "status": "pending",
        "code_verifier": code_verifier
    }
    
    params = {
        "response_type": "code",
        "client_id": EPIC_CONFIG["client_id"],
        "redirect_uri": EPIC_CONFIG["redirect_uri"],
        "scope": EPIC_CONFIG["scope"],
        "state": state,
        "aud": EPIC_CONFIG["fhir_base_url"],
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }
    
    auth_url = f"{EPIC_CONFIG['auth_url']}?{urlencode(params)}"
    return RedirectResponse(url=auth_url)


@app.get("/callback")
async def callback(code: str = None, state: str = None, error: str = None):
    """Handle OAuth2 callback from Epic with PKCE token exchange"""
    if error:
        return RedirectResponse(url=f"/?error={error}")
    
    if not code or not state:
        return RedirectResponse(url="/?error=missing_params")
    
    if state not in sessions:
        return RedirectResponse(url="/?error=invalid_state")
    
    # Get the code verifier from session
    code_verifier = sessions[state].get("code_verifier")
    if not code_verifier:
        return RedirectResponse(url="/?error=missing_verifier")
    
    # Exchange code for access token with PKCE
    async with httpx.AsyncClient() as client:
        try:
            token_data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": EPIC_CONFIG["redirect_uri"],
                "client_id": EPIC_CONFIG["client_id"],
                "code_verifier": code_verifier
            }
            
            token_response = await client.post(
                EPIC_CONFIG["token_url"],
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if token_response.status_code != 200:
                print(f"Token error: {token_response.status_code} - {token_response.text}")
                return RedirectResponse(url=f"/?error=token_error&details={token_response.status_code}")
            
            token_json = token_response.json()
            
            # Extract patient ID from token response
            patient_id = token_json.get("patient")
            access_token = token_json.get("access_token")
            
            if not patient_id or not access_token:
                print(f"Missing data in token response: {token_json}")
                return RedirectResponse(url="/?error=missing_token_data")
            
            # Update session with authentication data
            sessions[state] = {
                "status": "authenticated",
                "access_token": access_token,
                "patient_id": patient_id,
                "token_type": token_json.get("token_type", "Bearer"),
                "expires_in": token_json.get("expires_in"),
                "scope": token_json.get("scope")
            }
            
            print(f"Successfully authenticated. Patient ID: {patient_id}")
            return RedirectResponse(url=f"/dashboard?session={state}")
            
        except Exception as e:
            print(f"Exception during token exchange: {str(e)}")
            return RedirectResponse(url=f"/?error=exception&details={str(e)}")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, session: str = None):
    """Render the patient dashboard"""
    if not session or session not in sessions:
        return RedirectResponse(url="/?error=no_session")
    
    session_data = sessions.get(session)
    if session_data.get("status") != "authenticated":
        return RedirectResponse(url="/?error=not_authenticated")
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "session_id": session
    })


def get_auth_headers(session_data: dict) -> dict:
    """Get authorization headers for FHIR API calls"""
    token_type = session_data.get("token_type", "Bearer")
    access_token = session_data.get("access_token")
    return {
        "Authorization": f"{token_type} {access_token}",
        "Accept": "application/fhir+json"
    }


@app.get("/api/patient/{session_id}")
async def get_patient(session_id: str):
    """Get patient demographics"""
    session = sessions.get(session_id)
    if not session or session.get("status") != "authenticated":
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    patient_id = session.get("patient_id")
    headers = get_auth_headers(session)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            url = f"{EPIC_CONFIG['fhir_base_url']}/Patient/{patient_id}"
            print(f"Fetching patient: {url}")
            
            response = await client.get(url, headers=headers)
            
            print(f"Patient response status: {response.status_code}")
            
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="Access token expired or invalid")
            
            if response.status_code != 200:
                print(f"Patient fetch error: {response.text}")
                raise HTTPException(status_code=response.status_code, detail=f"Failed to fetch patient data: {response.text}")
            
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
                "identifier": get_identifier(patient_data),
                "address": format_address(patient_data.get("address", [])),
                "phone": format_telecom(patient_data.get("telecom", []), "phone"),
                "email": format_telecom(patient_data.get("telecom", []), "email"),
                "maritalStatus": patient_data.get("maritalStatus", {}).get("text", "Unknown"),
                "language": get_preferred_language(patient_data)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"Exception fetching patient: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error fetching patient: {str(e)}")


@app.get("/api/medications/{session_id}")
async def get_medications(
    session_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50)
):
    """Get patient medications with pagination"""
    session = sessions.get(session_id)
    if not session or session.get("status") != "authenticated":
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    patient_id = session.get("patient_id")
    headers = get_auth_headers(session)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Calculate offset for pagination
            offset = (page - 1) * page_size
            
            url = f"{EPIC_CONFIG['fhir_base_url']}/MedicationRequest"
            params = {
                "patient": patient_id,
                "_count": page_size,
                "_offset": offset,
                "_sort": "-authoredon"
            }
            
            print(f"Fetching medications: {url} with params {params}")
            response = await client.get(url, params=params, headers=headers)
            
            print(f"Medications response status: {response.status_code}")
            
            if response.status_code == 401:
                return {"medications": [], "total": 0, "page": page, "page_size": page_size, "error": "Session expired"}
            
            if response.status_code != 200:
                print(f"Medications fetch error: {response.text}")
                return {"medications": [], "total": 0, "page": page, "page_size": page_size, "error": f"Failed to fetch: {response.status_code}"}
            
            bundle = response.json()
            total = bundle.get("total", 0)
            medications = []
            
            for entry in bundle.get("entry", []):
                resource = entry.get("resource", {})
                
                # Get medication name
                med_name = "Unknown Medication"
                if resource.get("medicationCodeableConcept"):
                    med_name = resource["medicationCodeableConcept"].get("text", med_name)
                    if med_name == "Unknown Medication":
                        codings = resource["medicationCodeableConcept"].get("coding", [])
                        if codings:
                            med_name = codings[0].get("display", med_name)
                elif resource.get("medicationReference"):
                    med_name = resource["medicationReference"].get("display", med_name)
                
                # Get dosage instructions
                dosage_text = "No dosage information"
                dosage_details = []
                if resource.get("dosageInstruction"):
                    for dosage in resource["dosageInstruction"]:
                        dosage_text = dosage.get("text", dosage_text)
                        
                        # Extract detailed dosage info
                        dose_detail = {
                            "text": dosage.get("text", ""),
                            "timing": "",
                            "route": "",
                            "method": "",
                            "doseQuantity": ""
                        }
                        
                        if dosage.get("timing"):
                            timing = dosage["timing"]
                            if timing.get("code"):
                                dose_detail["timing"] = timing["code"].get("text", "")
                            elif timing.get("repeat"):
                                repeat = timing["repeat"]
                                freq = repeat.get("frequency", "")
                                period = repeat.get("period", "")
                                period_unit = repeat.get("periodUnit", "")
                                dose_detail["timing"] = f"{freq}x per {period} {period_unit}"
                        
                        if dosage.get("route"):
                            dose_detail["route"] = dosage["route"].get("text", "")
                        
                        if dosage.get("doseAndRate"):
                            for dar in dosage["doseAndRate"]:
                                if dar.get("doseQuantity"):
                                    dq = dar["doseQuantity"]
                                    dose_detail["doseQuantity"] = f"{dq.get('value', '')} {dq.get('unit', '')}"
                        
                        dosage_details.append(dose_detail)
                
                # Get status
                status = resource.get("status", "unknown").title()
                
                # Get authored date
                authored = resource.get("authoredOn", "Unknown date")
                
                # Get prescriber
                prescriber = "Unknown"
                if resource.get("requester"):
                    prescriber = resource["requester"].get("display", "Unknown")
                
                # Get reason/indication
                reason = ""
                if resource.get("reasonCode"):
                    reasons = [r.get("text", "") for r in resource["reasonCode"] if r.get("text")]
                    reason = ", ".join(reasons)
                
                # Get dispense request details
                dispense_info = {}
                if resource.get("dispenseRequest"):
                    dr = resource["dispenseRequest"]
                    if dr.get("numberOfRepeatsAllowed"):
                        dispense_info["refills"] = dr["numberOfRepeatsAllowed"]
                    if dr.get("quantity"):
                        dispense_info["quantity"] = f"{dr['quantity'].get('value', '')} {dr['quantity'].get('unit', '')}"
                    if dr.get("expectedSupplyDuration"):
                        esd = dr["expectedSupplyDuration"]
                        dispense_info["supplyDuration"] = f"{esd.get('value', '')} {esd.get('unit', '')}"
                
                medications.append({
                    "id": resource.get("id", ""),
                    "name": med_name,
                    "dosage": dosage_text,
                    "dosageDetails": dosage_details,
                    "status": status,
                    "authoredOn": authored,
                    "prescriber": prescriber,
                    "reason": reason,
                    "dispenseInfo": dispense_info,
                    "intent": resource.get("intent", "").title(),
                    "category": get_category_text(resource.get("category", []))
                })
            
            return {
                "medications": medications,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total else 1
            }
            
        except Exception as e:
            print(f"Exception fetching medications: {str(e)}")
            return {"medications": [], "total": 0, "page": page, "page_size": page_size, "error": str(e)}


@app.get("/api/labs/{session_id}")
async def get_labs(
    session_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50)
):
    """Get patient lab results with pagination"""
    session = sessions.get(session_id)
    if not session or session.get("status") != "authenticated":
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    patient_id = session.get("patient_id")
    headers = get_auth_headers(session)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            offset = (page - 1) * page_size
            
            url = f"{EPIC_CONFIG['fhir_base_url']}/Observation"
            params = {
                "patient": patient_id,
                "category": "laboratory",
                "_count": page_size,
                "_offset": offset,
                "_sort": "-date"
            }
            
            print(f"Fetching labs: {url} with params {params}")
            response = await client.get(url, params=params, headers=headers)
            
            print(f"Labs response status: {response.status_code}")
            
            if response.status_code == 401:
                return {"labs": [], "total": 0, "page": page, "page_size": page_size, "error": "Session expired"}
            
            if response.status_code != 200:
                print(f"Labs fetch error: {response.text}")
                return {"labs": [], "total": 0, "page": page, "page_size": page_size, "error": f"Failed to fetch: {response.status_code}"}
            
            bundle = response.json()
            total = bundle.get("total", 0)
            labs = []
            
            for entry in bundle.get("entry", []):
                resource = entry.get("resource", {})
                
                # Get test name and code
                test_name = resource.get("code", {}).get("text", "Unknown Test")
                test_code = ""
                if resource.get("code", {}).get("coding"):
                    coding = resource["code"]["coding"][0]
                    test_code = coding.get("code", "")
                    if not test_name or test_name == "Unknown Test":
                        test_name = coding.get("display", test_name)
                
                # Get value - handle different types
                value = "N/A"
                unit = ""
                value_type = ""
                
                if resource.get("valueQuantity"):
                    value = resource["valueQuantity"].get("value", "N/A")
                    unit = resource["valueQuantity"].get("unit", "")
                    value_type = "quantity"
                elif resource.get("valueString"):
                    value = resource["valueString"]
                    value_type = "string"
                elif resource.get("valueCodeableConcept"):
                    value = resource["valueCodeableConcept"].get("text", "N/A")
                    value_type = "coded"
                elif resource.get("valueBoolean") is not None:
                    value = "Yes" if resource["valueBoolean"] else "No"
                    value_type = "boolean"
                
                # Get reference range
                ref_range = "N/A"
                ref_low = None
                ref_high = None
                if resource.get("referenceRange"):
                    range_obj = resource["referenceRange"][0]
                    low = range_obj.get("low", {}).get("value", "")
                    high = range_obj.get("high", {}).get("value", "")
                    ref_low = low
                    ref_high = high
                    if low and high:
                        ref_range = f"{low} - {high}"
                    elif low:
                        ref_range = f">= {low}"
                    elif high:
                        ref_range = f"<= {high}"
                    if range_obj.get("text"):
                        ref_range = range_obj["text"]
                
                # Get status and date
                status = resource.get("status", "unknown").title()
                effective_date = resource.get("effectiveDateTime", "Unknown date")
                
                # Get interpretation
                interpretation = "Normal"
                interpretation_code = ""
                if resource.get("interpretation"):
                    interp = resource["interpretation"][0]
                    interpretation = interp.get("text", "")
                    if not interpretation and interp.get("coding"):
                        interpretation = interp["coding"][0].get("display", "Normal")
                        interpretation_code = interp["coding"][0].get("code", "")
                
                # Get performer
                performer = ""
                if resource.get("performer"):
                    performer = resource["performer"][0].get("display", "")
                
                # Get specimen info
                specimen = ""
                if resource.get("specimen"):
                    specimen = resource["specimen"].get("display", "")
                
                # Get notes
                notes = []
                if resource.get("note"):
                    notes = [n.get("text", "") for n in resource["note"] if n.get("text")]
                
                labs.append({
                    "id": resource.get("id", ""),
                    "name": test_name,
                    "code": test_code,
                    "value": f"{value} {unit}".strip() if unit else str(value),
                    "rawValue": value,
                    "unit": unit,
                    "valueType": value_type,
                    "referenceRange": ref_range,
                    "refLow": ref_low,
                    "refHigh": ref_high,
                    "status": status,
                    "date": effective_date,
                    "interpretation": interpretation,
                    "interpretationCode": interpretation_code,
                    "performer": performer,
                    "specimen": specimen,
                    "notes": notes,
                    "category": get_category_text(resource.get("category", []))
                })
            
            return {
                "labs": labs,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total else 1
            }
            
        except Exception as e:
            print(f"Exception fetching labs: {str(e)}")
            return {"labs": [], "total": 0, "page": page, "page_size": page_size, "error": str(e)}


@app.get("/api/vitals/{session_id}")
async def get_vitals(
    session_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50)
):
    """Get patient vital signs with pagination"""
    session = sessions.get(session_id)
    if not session or session.get("status") != "authenticated":
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    patient_id = session.get("patient_id")
    headers = get_auth_headers(session)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            offset = (page - 1) * page_size
            
            url = f"{EPIC_CONFIG['fhir_base_url']}/Observation"
            params = {
                "patient": patient_id,
                "category": "vital-signs",
                "_count": page_size,
                "_offset": offset,
                "_sort": "-date"
            }
            
            print(f"Fetching vitals: {url} with params {params}")
            response = await client.get(url, params=params, headers=headers)
            
            print(f"Vitals response status: {response.status_code}")
            
            if response.status_code == 401:
                return {"vitals": [], "total": 0, "page": page, "page_size": page_size, "error": "Session expired"}
            
            if response.status_code != 200:
                print(f"Vitals fetch error: {response.text}")
                return {"vitals": [], "total": 0, "page": page, "page_size": page_size, "error": f"Failed to fetch: {response.status_code}"}
            
            bundle = response.json()
            total = bundle.get("total", 0)
            vitals = []
            
            for entry in bundle.get("entry", []):
                resource = entry.get("resource", {})
                
                # Get vital sign name and code
                vital_name = resource.get("code", {}).get("text", "Unknown Vital")
                vital_code = ""
                loinc_code = ""
                if resource.get("code", {}).get("coding"):
                    for coding in resource["code"]["coding"]:
                        if coding.get("system") == "http://loinc.org":
                            loinc_code = coding.get("code", "")
                            if not vital_name or vital_name == "Unknown Vital":
                                vital_name = coding.get("display", vital_name)
                        vital_code = coding.get("code", vital_code)
                
                # Get value - handle different types including components
                value = "N/A"
                unit = ""
                components = []
                
                if resource.get("valueQuantity"):
                    value = resource["valueQuantity"].get("value", "N/A")
                    unit = resource["valueQuantity"].get("unit", "")
                elif resource.get("component"):
                    # Handle multi-component vitals like blood pressure
                    comp_values = []
                    for comp in resource["component"]:
                        comp_name = comp.get("code", {}).get("text", "")
                        if not comp_name and comp.get("code", {}).get("coding"):
                            comp_name = comp["code"]["coding"][0].get("display", "")
                        comp_value = comp.get("valueQuantity", {}).get("value", "")
                        comp_unit = comp.get("valueQuantity", {}).get("unit", "")
                        
                        components.append({
                            "name": comp_name,
                            "value": comp_value,
                            "unit": comp_unit
                        })
                        
                        if comp_value:
                            comp_values.append(str(comp_value))
                    
                    value = "/".join(comp_values) if comp_values else "N/A"
                    if components:
                        unit = components[0].get("unit", "")
                
                # Get date and status
                effective_date = resource.get("effectiveDateTime", "Unknown date")
                status = resource.get("status", "unknown").title()
                
                # Get interpretation
                interpretation = ""
                if resource.get("interpretation"):
                    interp = resource["interpretation"][0]
                    interpretation = interp.get("text", "")
                    if not interpretation and interp.get("coding"):
                        interpretation = interp["coding"][0].get("display", "")
                
                # Get performer
                performer = ""
                if resource.get("performer"):
                    performer = resource["performer"][0].get("display", "")
                
                # Get body site
                body_site = ""
                if resource.get("bodySite"):
                    body_site = resource["bodySite"].get("text", "")
                
                # Get method
                method = ""
                if resource.get("method"):
                    method = resource["method"].get("text", "")
                
                vitals.append({
                    "id": resource.get("id", ""),
                    "name": vital_name,
                    "code": vital_code,
                    "loincCode": loinc_code,
                    "value": f"{value} {unit}".strip() if unit else str(value),
                    "rawValue": value,
                    "unit": unit,
                    "components": components,
                    "date": effective_date,
                    "status": status,
                    "interpretation": interpretation,
                    "performer": performer,
                    "bodySite": body_site,
                    "method": method,
                    "category": get_category_text(resource.get("category", []))
                })
            
            return {
                "vitals": vitals,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total else 1
            }
            
        except Exception as e:
            print(f"Exception fetching vitals: {str(e)}")
            return {"vitals": [], "total": 0, "page": page, "page_size": page_size, "error": str(e)}


@app.get("/api/logout/{session_id}")
async def logout(session_id: str):
    """Logout and clear session"""
    if session_id in sessions:
        del sessions[session_id]
    return {"message": "Logged out successfully"}


# Helper functions
def get_identifier(patient_data):
    """Extract a meaningful identifier from patient data"""
    identifiers = patient_data.get("identifier", [])
    for ident in identifiers:
        # Prefer MRN or specific identifier types
        if ident.get("type", {}).get("coding"):
            for coding in ident["type"]["coding"]:
                if coding.get("code") in ["MR", "MRN"]:
                    return ident.get("value", "N/A")
        # Check for Epic FHIR ID
        if "epic" in ident.get("system", "").lower():
            return ident.get("value", "N/A")
    
    # Fallback to first identifier or patient ID
    if identifiers:
        return identifiers[0].get("value", patient_data.get("id", "N/A"))
    return patient_data.get("id", "N/A")


def format_address(addresses):
    """Format address from FHIR Address array"""
    if not addresses:
        return "No address on file"
    
    addr = addresses[0]
    lines = addr.get("line", [])
    city = addr.get("city", "")
    state = addr.get("state", "")
    postal = addr.get("postalCode", "")
    country = addr.get("country", "")
    
    address_parts = []
    if lines:
        address_parts.extend(lines)
    
    city_state_zip = []
    if city:
        city_state_zip.append(city)
    if state:
        city_state_zip.append(state)
    if postal:
        city_state_zip.append(postal)
    
    if city_state_zip:
        address_parts.append(", ".join(city_state_zip))
    
    if country:
        address_parts.append(country)
    
    return ", ".join([p for p in address_parts if p]) or "No address on file"


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
            lang = comm.get("language", {})
            return lang.get("text", lang.get("coding", [{}])[0].get("display", "English"))
    return "English"


def get_category_text(categories):
    """Extract category text from FHIR category array"""
    texts = []
    for cat in categories:
        if cat.get("text"):
            texts.append(cat["text"])
        elif cat.get("coding"):
            for coding in cat["coding"]:
                if coding.get("display"):
                    texts.append(coding["display"])
                    break
    return ", ".join(texts) if texts else ""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=CONFIG["app"]["host"],
        port=CONFIG["app"]["port"]
    )
