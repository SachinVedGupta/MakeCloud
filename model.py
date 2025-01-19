import os
import google.generativeai as genai

# Configure the Gemini model client
genai.configure(api_key="gemini api key")

# Initialize Gemini pro model
model = genai.GenerativeModel("gemini-pro")

# User input function to be used for recurrent prompts
def get_user_input(prompt):
    return input(prompt)

# Continuosly ask the user for necessary information needed to create a valid Terraform script
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

    user_input = initial_prompt
    terraform_script = ""

    # Continues to ask user for information until full script is generated
    while True:
        # Ping user input to the model
        response = chat.send_message(user_input)

        # Check if the message contains the Terraform script
        if "Here's the complete Terraform script:" in response.text:
            terraform_script = response.text.split("Here's the complete Terraform script:")
            [1].strip()
            break

        print(response.text)
        user_input = get_user_input("> ")

    return terraform_script

def main():
    initial_prompt = get_user_input("What cloud resource would you like to provision? ")
    terraform_script = generate_terraform_script(initial_prompt)
    print("\nGenerated Terraform Script:")
    print(terraform_script)

if __name__ == "__main__":
    main()

# Example usage:
print("Welcome to the Terraform Script Generator!")
main()
