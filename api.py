# =============================================================================
# RecruitLab — API Backend (Hardened)
# OWASP best practices: rate limiting, input validation, secure key handling
# =============================================================================

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import sys, os, tempfile, shutil, re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src/recrutamento'))
from crew import Recrutamento, read_cvs

# =============================================================================
# CONFIGURAÇÃO — valores via variáveis de ambiente (nunca hardcoded)
# =============================================================================

# OWASP A02: Chaves nunca no código — sempre via env vars
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY não está definida nas variáveis de ambiente.")

# Código de acesso simples para bloquear uso público
# Define ACCESS_CODE nas env vars do Railway
ACCESS_CODE = os.environ.get("ACCESS_CODE", "")

# Origem permitida do frontend
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "https://frontend-phi-blue-88.vercel.app"
).split(",")

# =============================================================================
# LIMITES DE VALIDAÇÃO
# =============================================================================

MAX_JOB_DESCRIPTION_LEN = 8_000
MAX_EMPRESA_LEN          = 100
MAX_CV_SIZE_MB           = 5
MAX_CV_SIZE_BYTES        = MAX_CV_SIZE_MB * 1024 * 1024
MAX_CVS_PER_REQUEST      = 50
ALLOWED_EXTENSIONS       = {".pdf", ".txt"}

# =============================================================================
# RATE LIMITING
# =============================================================================

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="RecruitLab API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# =============================================================================
# CORS
# =============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "X-Access-Code"],
)

# =============================================================================
# FUNÇÕES DE VALIDAÇÃO
# =============================================================================

def sanitize_text(text: str, max_len: int, field_name: str) -> str:
    if not isinstance(text, str):
        raise HTTPException(status_code=422, detail=f"{field_name}: tipo inválido.")
    sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text).strip()
    if not sanitized:
        raise HTTPException(status_code=422, detail=f"{field_name}: não pode estar vazio.")
    if len(sanitized) > max_len:
        raise HTTPException(status_code=422, detail=f"{field_name}: excede o limite de {max_len} caracteres.")
    return sanitized


def validate_cv_file(cv: UploadFile) -> None:
    _, ext = os.path.splitext(cv.filename or "")
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Ficheiro '{cv.filename}': extensão não permitida. Usa PDF ou TXT.")
    cv.file.seek(0, 2)
    size = cv.file.tell()
    cv.file.seek(0)
    if size > MAX_CV_SIZE_BYTES:
        raise HTTPException(status_code=422, detail=f"Ficheiro '{cv.filename}': excede o limite de {MAX_CV_SIZE_MB}MB.")
    if size == 0:
        raise HTTPException(status_code=422, detail=f"Ficheiro '{cv.filename}': ficheiro vazio.")


def safe_filename(filename: str) -> str:
    base = os.path.basename(filename or "ficheiro")
    safe = re.sub(r'[^\w.\-]', '_', base)
    if safe.startswith('.'):
        safe = '_' + safe
    return safe[:100]

# =============================================================================
# PARSING
# =============================================================================

def parse_emails(raw: str) -> list:
    emails = []
    blocos = re.split(r'\n?CANDIDATO:', raw)
    for bloco in blocos:
        bloco = bloco.strip()
        if not bloco:
            continue
        try:
            candidato = re.search(r'^(.+?)[\n\r]', bloco)
            email     = re.search(r'EMAIL:\s*(.+)', bloco)
            assunto   = re.search(r'ASSUNTO:\s*(.+)', bloco)
            corpo     = re.search(r'CORPO:\s*([\s\S]+?)(?:---|$)', bloco)
            if candidato and assunto and corpo:
                email_val = email.group(1).strip() if email else ""
                if email_val.upper() in ["NAO ENCONTRADO", "NÃO ENCONTRADO", ""]:
                    email_val = ""
                if email_val and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email_val):
                    email_val = ""
                emails.append({
                    "candidato": candidato.group(1).strip()[:200],
                    "email":     email_val[:200],
                    "assunto":   assunto.group(1).strip()[:300],
                    "corpo":     corpo.group(1).strip()[:5000],
                })
        except Exception:
            continue
    return emails


def extract_sections(result_str: str) -> tuple:
    if "SECCAO 2 - EMAILS" in result_str:
        parts      = result_str.split("SECCAO 2 - EMAILS")
        relatorio  = parts[0].replace("SECCAO 1 - RELATORIO", "").strip()
        emails_raw = parts[1].strip()
    else:
        relatorio  = result_str
        emails_raw = ""
    return relatorio, emails_raw

# =============================================================================
# ENDPOINTS
# =============================================================================

@app.post("/analisar")
@limiter.limit("10/hour")
async def analisar(
    request: Request,
    job_description: str = Form(...),
    empresa: str = Form(default="a nossa empresa"),
    cvs: list[UploadFile] = File(...)
):
    # Verificação de código de acesso
    code = request.headers.get("X-Access-Code", "")
    if ACCESS_CODE and code != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Código de acesso inválido.")

    job_description = sanitize_text(job_description, MAX_JOB_DESCRIPTION_LEN, "Descrição da vaga")
    empresa         = sanitize_text(empresa, MAX_EMPRESA_LEN, "Nome da empresa")

    if len(cvs) == 0:
        raise HTTPException(status_code=422, detail="Adiciona pelo menos um CV.")
    if len(cvs) > MAX_CVS_PER_REQUEST:
        raise HTTPException(status_code=422, detail=f"Máximo de {MAX_CVS_PER_REQUEST} CVs por análise.")

    for cv in cvs:
        validate_cv_file(cv)

    tmp_dir = tempfile.mkdtemp()
    try:
        for cv in cvs:
            safe_name = safe_filename(cv.filename)
            path = os.path.join(tmp_dir, safe_name)
            with open(path, "wb") as f:
                shutil.copyfileobj(cv.file, f)

        cvs_content = read_cvs(tmp_dir)
        result = Recrutamento().crew().kickoff(inputs={
            "job_description": job_description,
            "cvs_content":     cvs_content,
            "empresa":         empresa,
        })

        result_str = str(result)
        relatorio, emails_raw = extract_sections(result_str)
        emails = parse_emails(emails_raw)

        return JSONResponse({"relatorio": relatorio, "emails": emails})

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Erro interno ao processar os CVs.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/")
@limiter.limit("30/minute")
async def root(request: Request):
    return {"status": "online", "servico": "RecruitLab"}
