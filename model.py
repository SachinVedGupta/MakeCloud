import os
import google.generativeai as genai

# Configure the Gemini model client
genai.configure(api_key=os.environ.get("Gemini api key here"))

# Initialize Gemini pro model
model = genai.GenerativeModel("gemini-pro")

# User input function to be used for recurrent prompts
def get_user_input(prompt):
    return input(prompt)

# Tell Gemini to ask the user for necessary information needed to create a valid Terraform script
def generate_terraform_script(initial_prompt):
    # Start new chat
    chat = model.start_chat(history=[
        {
            "role": "user",
            "parts": ["You are an AI assistant that helps users create Terraform scripts for cloud resource provisioning. Ask for necessary details and generate a complete, valid Terraform script based on user input."]
        },
        {
            "role": "model",
            "parts": ["Understood. I'm here to help users create Terraform scripts for cloud resource provisioning. I'll ask for necessary details and generate a complete, valid Terraform script based on the information provided. How can I assist you today?"]
        }
    ])

