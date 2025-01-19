import os
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import List
import google.generativeai as genai
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure the Gemini model client using an environment variable
genai.configure(api_key="FILLER_KEY")

class TerraformModel(BaseModel):
    resource_type: str
    answers: List[str]
    questions: List[str]

@app.get("/get_info")
async def get_required_information(resource_type: str = Query(..., description="The type of cloud resource")) -> List[str]:
    """
    Get a list of required questions based on the resource type.
    """
    # Initialize Gemini pro model
    try:
        model = genai.GenerativeModel("gemini-pro")
    except Exception as e:
        raise RuntimeError(f"Error initializing Gemini model: {e}")

    try:
        # Start a chat session
        chat = model.start_chat(history=[
            {"role": "user", "parts": ["You are an AI assistant that helps users create Terraform scripts. Ask the User for neccesary credentials associated with their account."]},
            {"role": "model", "parts": ["Provide questions to configure a basic Terraform resource. Ask the User for neccesary credentials associated with their account."]}
        ])

        # Get the questions
        response = chat.send_message(
            f"What information is needed to create a Terraform configuration for {resource_type}? "
            "Return ONLY the questions, one per line, no additional text. Ask the User for neccesary credentials associated with their account."
        )

        questions = list(dict.fromkeys(q.strip() for q in response.text.split("\n") if q.strip()))  # Remove duplicates
        questions.append("Do you have any additional specifications or requirements? (Type 'none' if none)")

        return questions
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching questions: {e}")

@app.post("/generate_script")
async def generate_terraform_script(terraformModel: TerraformModel) -> str:
    """
    Generate a Terraform script using the provided answers.
    """
    try:
        model = genai.GenerativeModel("gemini-pro")
    except Exception as e:
        raise RuntimeError(f"Error initializing Gemini model: {e}")

    try:
        qa_pairs = [f"Q: {q}\nA: {a}" for q, a in zip(terraformModel.questions, terraformModel.answers) if a.lower() != 'none']
        prompt = f"""
            Generate a Terraform script for:
            Resource Type: {terraformModel.resource_type}

            Configuration:
            {' '.join(qa_pairs)}

            Provide the code in HCL syntax with provider and resource blocks only.
        """

        chat = model.start_chat(history=[
            {"role": "user", "parts": ["Generate Terraform scripts based on user input."]},
            {"role": "model", "parts": ["I will generate Terraform scripts in HCL syntax."]}
        ])

        response = chat.send_message(prompt)
        script_text = response.text.split("```")[1].strip() if "```" in response.text else response.text.strip()
        return script_text
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating Terraform script: {e}")
