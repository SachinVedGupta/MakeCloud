import os
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import List
import google.generativeai as genai
<<<<<<< HEAD
from typing import List, Dict
from run_cloud import run_terraform

# Configure the Gemini model client
genai.configure(api_key="AIzaSyDxq3oqxKYDMEoKOG77P7VU5JOEJOEG3VQ")
=======
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
>>>>>>> b5852e9c14e2e5391fb63394dadb82f0f4789554

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
<<<<<<< HEAD
            {
                "role": "user",
                "parts": ["You are an AI assistant that helps users create the most basic Terraform Infrastructure as Code scripts for cloud resource provisioning. Given a cloud resource type, return ONLY a list of questions (always including necessary credentials associated to the user's account) that need to be answered to create the most basic version of the Terraform configuration, with no additional text."]
            },
            {
                "role": "model",
                "parts": ["I will provide only the necessary questions (always including necessary credentials associated to the user's account) for the most basic Terraform cloud infrastructure configuration when given a resource type. Please provide the resource type."]
            }
        ])
        
        # Ask for the questions
        response = chat.send_message(f"What information is needed to create the most basic Terraform Infrastructure as Code configuration for {resource_type}? Return ONLY the questions (always including access key, secret key, and region), one per line, with no additional text.")
        
        # Split the response into individual questions and remove duplicates while maintaining order
        seen = set()
        questions = []
        for q in [q.strip() for q in response.text.split('\n') if q.strip()]:
            if q not in seen:
                seen.add(q)
                questions.append(q)
        
        # Add the additional specifications question
=======
            {"role": "user", "parts": ["You are an AI assistant that helps users create Terraform scripts. Ask the User for neccesary credentials associated with their account."]},
            {"role": "model", "parts": ["Provide questions to configure a basic Terraform resource. Ask the User for neccesary credentials associated with their account."]}
        ])

        # Get the questions
        response = chat.send_message(
            f"What information is needed to create a Terraform configuration for {resource_type}? "
            "Return ONLY the questions, one per line, no additional text. Ask the User for neccesary credentials associated with their account."
        )

        questions = list(dict.fromkeys(q.strip() for q in response.text.split("\n") if q.strip()))  # Remove duplicates
>>>>>>> b5852e9c14e2e5391fb63394dadb82f0f4789554
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
<<<<<<< HEAD
        return f"Error in script generation: {str(e)}"

def get_manual_answers(questions: List[str]) -> List[str]:
    """
    Manually collect answers for testing purposes.
    """
    print("\nPlease provide answers to the following questions:")
    answers = []
    for i, question in enumerate(questions, 1):
        while True:
            try:
                answer = input(f"{i}. {question}\nAnswer: ").strip()
                if answer:  # Ensure answer is not empty
                    answers.append(answer)
                    break
                print("Please provide a non-empty answer.")
            except KeyboardInterrupt:
                print("\nInput cancelled. Exiting...")
                return []
    return answers

def main(resource_type: str = None, answers: List[str] = None, test_mode: bool = False):
    """
    Main function that can be called from the frontend or used for testing.
    """
    try:
        if not resource_type:
            raise ValueError("Resource type must be provided")
        
        # Get the questions
        questions = get_required_information(resource_type)
        if not questions:
            raise ValueError("Failed to generate questions for the resource type")
        
        # Print questions with proper numbering
        print("\nQuestions to ask:")
        for i, q in enumerate(questions, 1):
            print(f"{i}. {q}")
        
        if test_mode:
            # Get manual answers for testing
            answers = get_manual_answers(questions)
            if not answers:
                return "Script generation cancelled."
        
        if answers:
            if len(questions) != len(answers):
                raise ValueError(f"Number of answers ({len(answers)}) doesn't match number of questions ({len(questions)})")
            
            return generate_terraform_script_from_answers(resource_type, answers, questions)
        
        return questions
        
    except Exception as e:
        return f"Error in main function: {str(e)}"

# Example usage for testing
if __name__ == "__main__":
    try:
        test_resource = input("What cloud resource would you like to provision? ").strip()
        if not test_resource:
            print("Resource type cannot be empty. Exiting...")
            exit(1)
            
        # Run in test mode to manually input answers
        script = main(resource_type=test_resource, test_mode=True)
        
        print("\nGenerated Terraform Script:")
        print(script)

        print("\n\n\n\n\nDoing terraform config")

        # Check if the first line contains "hdl"
        if "hcl" in script.splitlines()[0]:
            # Remove the first line if "hdl" is found
            new_script = "\n".join(script.splitlines()[1:])
        else:
            # Otherwise, keep the script unchanged
            new_script = script

        # print(new_script)
        run_terraform(new_script)
        
    except KeyboardInterrupt:
        print("\nProgram cancelled by user. Exiting...")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
=======
        raise HTTPException(status_code=500, detail=f"Error generating Terraform script: {e}")
>>>>>>> b5852e9c14e2e5391fb63394dadb82f0f4789554
