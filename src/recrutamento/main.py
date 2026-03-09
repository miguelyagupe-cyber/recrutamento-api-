import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from crew import Recrutamento, read_cvs

def run():
    cvs_content = read_cvs("cvs")
    num_cvs = cvs_content.count("--- CV:")
    print(f"A iniciar análise de candidatos...")
    print(f"CVs encontrados: {num_cvs}")
    print("-" * 50)

    with open("job_description.txt", "r") as f:
        job_description = f.read()

    result = Recrutamento().crew().kickoff(inputs={
        "job_description": job_description,
        "cvs_content": cvs_content,
        "empresa": "Empresa"
    })

    print("\n" + "="*50)
    print("RESULTADO FINAL")
    print("="*50)
    print(result)

if __name__ == "__main__":
    run()
