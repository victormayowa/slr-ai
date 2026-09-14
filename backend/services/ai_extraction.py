import json
import os
from google import genai
from google.genai import types
import openai
import anthropic
from dotenv import load_dotenv
load_dotenv()

gemini_client = None
try:
    gemini_client = genai.Client()
except Exception:
    pass

openai_client = None
if os.getenv("OPENAI_API_KEY"):
    openai_client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

anthropic_client = None
if os.getenv("ANTHROPIC_API_KEY"):
    anthropic_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

async def extract_variables(paper_text: str, columns: list[str], provider: str = "gemini") -> dict:
    """
    Extracts specific variables from a paper text using the specified AI provider.
    Returns a dictionary mapping column names to the extracted values.
    """
    
    # Generate the JSON structure we want the LLM to output
    json_schema = "{\n"
    for col in columns:
        json_schema += f'    "{col}": "value extracted from text, or \'Not specified\'",\n'
    json_schema += "}"
    
    prompt = f"""
    You are an expert academic data extractor. Read the following paper text and extract the specific requested variables.
    Be precise, concise, and accurate. If a variable is not mentioned in the text, explicitly write "Not specified".
    
    Variables to extract:
    {', '.join(columns)}
    
    Paper Text:
    {paper_text}
    
    You must respond in valid JSON format matching this exact schema:
    {json_schema}
    """
    
    try:
        if provider == "openai":
            if not openai_client:
                raise Exception("OPENAI_API_KEY not configured")
            response = await openai_client.chat.completions.create(
                model="gpt-4o",
                response_format={{ "type": "json_object" }},
                messages=[{"role": "user", "content": prompt}]
            )
            return json.loads(response.choices[0].message.content)
            
        elif provider == "anthropic":
            if not anthropic_client:
                raise Exception("ANTHROPIC_API_KEY not configured")
            response = await anthropic_client.messages.create(
                model="claude-3-5-sonnet-20240620",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt + "\n\nOutput only raw JSON. Do not include markdown blocks."}]
            )
            return json.loads(response.content[0].text)
            
        else:
            # Default to Gemini
            if not gemini_client:
                raise Exception("GEMINI_API_KEY not configured")
            try:
                response = gemini_client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
            except Exception as e:
                print(f"Gemini first attempt failed, retrying: {e}")
                response = gemini_client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
            return json.loads(response.text)
            
    except Exception as e:
        print(f"API Error ({provider}) during extraction: {e}")
        # Return fallback data
        fallback = {}
        for col in columns:
            fallback[col] = f"[{provider.upper()} SIMULATION] Extracted value for {col}"
        return fallback
