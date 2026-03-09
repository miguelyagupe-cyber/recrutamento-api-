from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import sys, os, tempfile, shutil, re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src/recrutamento'))
from crew import Recrutamento, read_cvs

app = FastAPI(title="Agente de Triagem de CVs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def parse_emails(raw):
    emails = []
    blocos = re.split(r'\nCANDIDATO:', raw)
    for bloco in blocos:
        bloco = bloco.strip()
        if not bloco:
            continue
        try:
            candidato = re.search(r'^(.+?)[\n\r]', bloco)
            email = re.search(r'EMAIL:\s*(.+)', bloco)
            assunto = re.search(r'ASSUNTO:\s*(.+)', bloco)
            corpo = re.search(r'CORPO:\s*([\s\S]+?)(?:---|$)', bloco)
            if candidato and assunto and corpo:
                email_val = email.group(1).strip() if email else ""
                # Limpa valores inválidos
                if email_val.upper() in ["NAO ENCONTRADO", "NÃO ENCONTRADO", ""]:
                    email_val = ""
                emails.append({
                    "candidato": candidato.group(1).strip(),
                    "email": email_val,
                    "assunto": assunto.group(1).strip(),
                    "corpo": corpo.group(1).strip()
                })
        except:
            continue
    return emails

def extract_sections(result_str):
    """Separa relatório de emails no output do agente"""
    if "SECCAO 2 - EMAILS" in result_str:
        parts = result_str.split("SECCAO 2 - EMAILS")
        relatorio = parts[0].replace("SECCAO 1 - RELATORIO", "").strip()
        emails_raw = parts[1].strip()
    else:
        relatorio = result_str
        emails_raw = ""
    return relatorio, emails_raw

@app.post("/analisar")
async def analisar(
    job_description: str = Form(...),
    empresa: str = Form(default="a nossa empresa"),
    cvs: list[UploadFile] = File(...)
):
    tmp_dir = tempfile.mkdtemp()
    try:
        for cv in cvs:
            path = os.path.join(tmp_dir, cv.filename)
            with open(path, "wb") as f:
                shutil.copyfileobj(cv.file, f)

        cvs_content = read_cvs(tmp_dir)

        result = Recrutamento().crew().kickoff(inputs={
            "job_description": job_description,
            "cvs_content": cvs_content,
            "empresa": empresa
        })

        result_str = str(result)
        relatorio, emails_raw = extract_sections(result_str)
        emails = parse_emails(emails_raw)

        return JSONResponse({"relatorio": relatorio, "emails": emails})
    finally:
        shutil.rmtree(tmp_dir)

@app.get("/")
def root():
    return {"status": "online", "agente": "triagem-cvs", "modelo": "claude-sonnet-4-5"}
