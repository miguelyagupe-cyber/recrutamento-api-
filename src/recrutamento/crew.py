import pdfplumber
import anthropic
import base64
import os
from pdf2image import convert_from_path
from io import BytesIO
from dotenv import load_dotenv
from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task

load_dotenv()

llm = LLM(model="claude-sonnet-4-5")

client = anthropic.Anthropic()

def extract_text_with_vision(filepath):
    """Usa Claude Vision para extrair texto de PDFs com imagens"""
    images = convert_from_path(filepath, dpi=200)
    content = []
    for img in images:
        buffer = BytesIO()
        img.save(buffer, format="JPEG")
        img_base64 = base64.standard_b64encode(buffer.getvalue()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": img_base64}
        })
    content.append({
        "type": "text",
        "text": "Extrai todo o texto deste CV exactamente como aparece, incluindo nome, email, telefone, experiência, formação e skills. Não interpretes, apenas transcreve o texto visível."
    })
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": content}]
    )
    return response.content[0].text

def process_single_cv(args):
    filename, filepath = args
    if filename.endswith(".pdf"):
        text = ""
        try:
            with pdfplumber.open(filepath) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
        except:
            pass
        if len(text) < 100:
            try:
                text = extract_text_with_vision(filepath)
            except Exception as e:
                text = f"[Erro ao processar: {e}]"
        return f"\n\n--- CV: {filename} ---\n{text}"
    elif filename.endswith(".txt"):
        with open(filepath) as f:
            return f"\n\n--- CV: {filename} ---\n{f.read()}"
    return ""

def read_cvs(folder="cvs"):
    from concurrent.futures import ThreadPoolExecutor
    files = [(f, os.path.join(folder, f)) for f in os.listdir(folder)
             if f.endswith(".pdf") or f.endswith(".txt")]
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(process_single_cv, files))
    return "".join(results)

@CrewBase
class Recrutamento():
    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    @agent
    def cv_analyzer(self) -> Agent:
        return Agent(config=self.agents_config['cv_analyzer'], llm=llm, verbose=True)

    @agent
    def email_writer(self) -> Agent:
        return Agent(config=self.agents_config['email_writer'], llm=llm, verbose=True)

    @task
    def analyze_task(self) -> Task:
        return Task(config=self.tasks_config['analyze_task'])

    @task
    def email_task(self) -> Task:
        return Task(config=self.tasks_config['email_task'])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
