from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import List
import google.generativeai as genai
from fastapi.middleware.cors import CORSMiddleware
from run_cloud import run_terraform

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
            {"role": "user", "parts": ["You are an AI assistant that helps users create the most basic Terraform Infrastructure as Code scripts for cloud resource provisioning. Given a cloud resource type, return ONLY a list of questions (always include the necessary credentials associated with the user's account) that need to be answered to create the most basic version of the Terraform configuration, with no additional text."]},
            {"role": "model", "parts": ["I will provide only the necessary questions (always including the necessary crendentials associated with the user's account) for the most basic Terraform cloud infrastructure configuration when given a resource type. Please provide the resource type."]}
        ])

        # Get the questions
        response = chat.send_message(f"What information is needed to create the most basic Terraform Infrastructure as Code configuration for {resource_type}? Return ONLY the questions (always including the necessary crendentials associated with the user's account), one per line, with no additional text.")

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
            {"role": "user", "parts": ["You are an AI assistant that helps users create the most basic Terraform Infrastructure as Code scripts for cloud resource provisioning. Given a cloud resource type, return ONLY a list of questions (always include the necessary credentials associated with the user's account) that need to be answered to create the most basic version of the Terraform configuration, with no additional text."]},
            {"role": "model", "parts": ["I will provide only the necessary questions (always including the necessary crendentials associated with the user's account) for the most basic Terraform cloud infrastructure configuration when given a resource type. Please provide the resource type."]}
        ])

        response = chat.send_message(prompt)
        script_text = response.text.split("```")[1].strip() if "```" in response.text else response.text.strip()

        print(script_text)
        script = script_text

        print("\n\n\n\n\nDoing terraform config")

        # Check if the first line contains "hdl"
        if "hcl" in script.splitlines()[0]:
            # Remove the first line if "hdl" is found
            new_script = "\n".join(script.splitlines()[1:])
        else:
            # Otherwise, keep the script unchanged
            new_script = script

        run_terraform(new_script)
        return new_script
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating Terraform script: {e}")
