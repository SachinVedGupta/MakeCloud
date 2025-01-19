import os
import google.generativeai as genai

genai.configure(api_key=os.environ.get("Gemini api key here")) #Configure the Gemini model client

model = genai.GenerativeModel("gemini-pro") #Initialize model as gemini pro

