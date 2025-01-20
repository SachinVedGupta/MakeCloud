from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import google.generativeai as genai
from typing import List, Dict
import logging
from run_cloud import run_terraform

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configure Gemini
genai.configure(api_key="ADD IT")
model = genai.GenerativeModel("gemini-pro")

@app.route('/get_info', methods=['GET'])
def get_info():
    """Endpoint to get questions for a resource type"""
    try:
        resource_type = request.args.get('resource_type')
        if not resource_type:
            return jsonify({'error': 'Resource type is required'}), 400

        questions = get_required_information(resource_type)
        return jsonify(questions)

    except Exception as e:
        logger.error(f"Error in get_info: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/generate_script', methods=['POST'])
def generate_script():
    """Endpoint to generate Terraform script"""
    try:
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400

        data = request.get_json()
        logger.debug(f"Received data: {data}")

        # Validate required fields
        required_fields = ['resource_type', 'questions', 'answers']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        resource_type = data['resource_type']
        questions = data['questions']
        answers = data['answers']

        # Validate data types
        if not all(isinstance(x, str) for x in [resource_type] + answers):
            return jsonify({'error': 'All answers must be strings'}), 400

        # Generate the Terraform script
        script = generate_terraform_script_from_answers(resource_type, answers, questions)
        
        # Clean up the script if it contains HCL marker
        if script and "hcl" in script.splitlines()[0]:
            script = "\n".join(script.splitlines()[1:])

        # Run Terraform (optional - comment out if not needed)
        try:
            terraform_output = run_terraform(script)
            return jsonify({
                'script': script,
                'terraform_output': terraform_output
            })
        except Exception as terraform_error:
            logger.error(f"Terraform execution error: {terraform_error}")
            return jsonify({
                'script': script,
                'terraform_error': str(terraform_error)
            })

    except Exception as e:
        logger.error(f"Error in generate_script: {str(e)}")
        return jsonify({'error': str(e)}), 500

def get_required_information(resource_type: str) -> List[str]:
    """Get required questions for a resource type"""
    try:
        chat = model.start_chat(history=[
            {
                "role": "user",
                "parts": ["You are an AI assistant that helps users create the most basic Terraform Infrastructure as Code scripts for cloud resource provisioning. Given a cloud resource type, return ONLY a list of questions (always including the access key, specific key, and region associated to the user's account) that need to be answered to create the most basic version of the Terraform configuration, with no additional text."]
            },
            {
                "role": "model",
                "parts": ["I will provide only the necessary questions (always including the access key, specific key, and region associated to the user's account) for the most basic Terraform cloud infrastructure configuration when given a resource type. Please provide the resource type."]
            }
        ])
        
        response = chat.send_message(f"What information is needed to create the most basic Terraform Infrastructure as Code configuration for {resource_type}? Return ONLY the questions (always including access key, secret key, and region), one per line, with no additional text.")
        
        seen = set()
        questions = []
        for q in [q.strip() for q in response.text.split('\n') if q.strip()]:
            if q not in seen:
                seen.add(q)
                questions.append(q)
        
        questions.append("Do you have any additional specifications or requirements? (Type 'none' if none)")
        return questions

    except Exception as e:
        logger.error(f"Error getting questions: {str(e)}")
        return []

def generate_terraform_script_from_answers(resource_type: str, answers: List[str], questions: List[str]) -> str:
    """Generate Terraform script from answers"""
    try:
        qa_pairs = []
        for q, a in zip(questions[:-1], answers[:-1]):
            qa_pairs.append(f"Q: {q}\nA: {a}")
        
        if answers[-1].lower() != 'none':
            qa_pairs.append(f"Additional Specifications: {answers[-1]}")
        
        qa_text = "\n".join(qa_pairs)
        
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
        
        prompt = f"""
                Please generate the most basic Terraform Infrastructure as Code script for the following cloud resource:

                Resource Type: {resource_type}

                Configuration details:
                {qa_text}

                Generate only the Terraform configuration code using HashiCorp's HCL syntax. Include provider configuration and resource blocks as needed.
                """
        
        response = chat.send_message(prompt)
        script_text = response.text
        
        if "```" in script_text:
            script_text = script_text.split("```")[1]
            if script_text.startswith("terraform"):
                script_text = script_text[9:]
        
        return script_text.strip()

    except Exception as e:
        logger.error(f"Error generating script: {str(e)}")
        return f"Error generating Terraform script: {str(e)}"

if __name__ == '__main__':
    app.run(debug=True, port=5000)