import os
import google.generativeai as genai
from typing import List, Dict
from run_cloud import run_terraform

# Configure the Gemini model client
genai.configure(api_key="AIzaSyDxq3oqxKYDMEoKOG77P7VU5JOEJOEG3VQ")

# Initialize Gemini pro model
model = genai.GenerativeModel("gemini-pro")

def get_required_information(resource_type: str) -> List[str]:
    """
    Get a list of required questions based on the resource type.
    Returns a list of questions to be asked in the frontend.
    """
    try:
        # Start a chat to get the required information
        chat = model.start_chat(history=[
            {
                "role": "user",
                "parts": ["You are an AI assistant that helps users create the most basic Terraform Infrastructure as Code scripts for cloud resource provisioning. Given a cloud resource type, return ONLY a list of questions (always ) that need to be answered to create the most basic version of the Terraform configuration, with no additional text."]
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
        questions.append("Do you have any additional specifications or requirements? (Type 'none' if none)")
        
        return questions
    except Exception as e:
        print(f"Error getting questions: {str(e)}")
        return []

def generate_terraform_script_from_answers(resource_type: str, answers: List[str], questions: List[str]) -> str:
    """
    Generate a Terraform script using the provided answers.
    """
    try:
        # Create a formatted string with questions and answers, excluding 'none' additional specifications
        qa_pairs = []
        for q, a in zip(questions[:-1], answers[:-1]):  # Exclude the last pair if it's 'none'
            qa_pairs.append(f"Q: {q}\nA: {a}")
            
        # Add additional specifications if provided
        if answers[-1].lower() != 'none':
            qa_pairs.append(f"Additional Specifications: {answers[-1]}")
            
        qa_text = "\n".join(qa_pairs)
        
        # Start new chat with explicit infrastructure context
        chat = model.start_chat(history=[
            {
                "role": "user",
                "parts": ["You are an AI assistant that helps users create the most basic Terraform Infrastructure as Code scripts for cloud resource provisioning. Given a cloud resource type, return ONLY a list of questions (always including access key, secret key, and region) that need to be answered to create the most basic version of the Terraform configuration, with no additional text."]
            },
            {
                "role": "model",
                "parts": ["I will provide only the necessary questions (including access key, secret key, and region) for the most basic Terraform cloud infrastructure configuration when given a resource type. Please provide the resource type."]
            }
        ])
        
        # Send the resource type and Q&A pairs with explicit infrastructure context
        prompt = f"""
                Please generate the most basic Terraform Infrastructure as Code script for the following cloud resource:

                Resource Type: {resource_type}

                Configuration details:
                {qa_text}

                Generate only the Terraform configuration code using HashiCorp's HCL syntax. Include provider configuration and resource blocks as needed.
                """
        
        try:
            response = chat.send_message(prompt)
            
            # Extract the Terraform script from the response
            script_text = response.text
            
            # Clean up the response to extract just the code
            if "```" in script_text:
                # Extract code between backticks if present
                script_text = script_text.split("```")[1]
                if script_text.startswith("terraform"):
                    script_text = script_text[9:]  # Remove "terraform" language identifier
            
            return script_text.strip()
            
        except Exception as e:
            return f"Error generating Terraform script: {str(e)}\n\nPlease try again with more specific configuration details."
            
    except Exception as e:
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