from flask import Flask, render_template, request, redirect, url_for, session
from openai import OpenAI
import os
import re
import uuid

app = Flask(__name__)

client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

app.secret_key = '7WG20yg6YU/oZdObHCeDR4dq900fyuV9U7q2n6momCE='

COUNTER_FILE = 'counter.txt'
UNIQUE_COUNTER_FILE = 'unique_counter.txt'
INTERPRETER_CLICKS_FILE = 'interpreter_clicks.txt'
TRANSLATOR_CLICKS_FILE = 'translator_clicks.txt'
GUESSER_CLICKS_FILE = 'guesser_clicks.txt'


def read_count(file_name):
    if os.path.exists(file_name):
        with open(file_name, 'r') as f:
            count = f.read()
            if count:
                return int(count)
            else:
                return 0
    else:
        return 0

def write_count(file_name, count):
    with open(file_name, 'w') as f:
        f.write(str(count))


MBTI_TYPES = [
    "INTJ", "INTP", "ENTJ", "ENTP",
    "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ",
    "ISTP", "ISFP", "ESTP", "ESFP"
]


@app.route('/', methods=['GET', 'POST'])
def index():
    # Read and update the visitor count
    visitor_count = read_count(COUNTER_FILE)
    visitor_count += 1
    write_count(COUNTER_FILE, visitor_count)

    # Read the visitor count
    unique_visitor_count = read_count(UNIQUE_COUNTER_FILE)

    # Check if the user has been counted in this session
    if not session.get('interpreter_visited'):
        unique_visitor_count += 1
        write_count(UNIQUE_COUNTER_FILE, unique_visitor_count)
        session['interpreter_visited'] = True

    # Read the interpreter click count
    interpreter_clicks = read_count(INTERPRETER_CLICKS_FILE)

    if request.method == 'POST':
        # Increment the interpreter click count
        interpreter_clicks += 1
        write_count(INTERPRETER_CLICKS_FILE, interpreter_clicks)

        mbti_type = request.form['mbti_type']
        user_message = request.form['user_message']
        prompt = (
            f"Determine if the following message: \"{user_message}\" Reflects the traits of {mbti_type}. "
            f"If so, explain how this message reflects the traits of an {mbti_type}. "
            f"And then interpret the message, which part of the message reflects traits of {mbti_type} and why."
            f"If it does not reflect the traits of {mbti_type}, say which mbti traits the message reflects, and why. "
            f"Be confident and assertive in your tone."
        )
        messages=[
            {"role": "system", "content": "You are an mbti expert."},
            {
                "role": "user",
                "content": prompt
            }
        ]
        try:
            response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=150,
            n=1,
            stop=None,
            temperature=0.7)

            interpretation = response.choices[0].message.content.strip()
        except Exception as e:
            interpretation = "Sorry, an error occurred while processing your request."
        return render_template(
            'index.html',
            interpretation=interpretation,
            mbti_type=mbti_type,
            user_message=user_message,
            mbti_types=MBTI_TYPES,
            visitor_count=visitor_count,
            unique_visitor_count=unique_visitor_count,
            interpreter_clicks=interpreter_clicks
        )
    else:
        # Default values for GET request
        return render_template(
            'index.html',
            mbti_type='INTJ',
            user_message='',
            interpretation=None,
            mbti_types=MBTI_TYPES,
            unique_visitor_count=unique_visitor_count,
            visitor_count=visitor_count,
            interpreter_clicks=interpreter_clicks
        )

    return render_template('index.html')


@app.route('/translator', methods=['GET', 'POST'])
def translator():
    # Read and update the visitor count
    visitor_count = read_count(COUNTER_FILE)
    visitor_count += 1
    write_count(COUNTER_FILE, visitor_count)

    # Read the visitor count
    unique_visitor_count = read_count(UNIQUE_COUNTER_FILE)

    # Check if the user has been counted in this session
    if not session.get('interpreter_visited'):
        unique_visitor_count += 1
        write_count(UNIQUE_COUNTER_FILE, unique_visitor_count)
        session['interpreter_visited'] = True

    # Read the translator click count
    translator_clicks = read_count(TRANSLATOR_CLICKS_FILE)

    if request.method == 'POST':
        # Increment the translator click count
        translator_clicks += 1
        write_count(TRANSLATOR_CLICKS_FILE, translator_clicks)

        from_mbti = request.form['from_mbti']
        to_mbti = request.form['to_mbti']
        original_message = request.form['original_message']

        # Create the prompt for the AI
        prompt = (
            f"Please translate the following message from an {from_mbti} perspective to one "
            f"that an {to_mbti} would easily understand.\n"
            f"Then, provide an interpretation of the original message.\n\n"
            f"Original Message: \"{original_message}\"\n\n"
            f"Response format:\n"
            f"Translated Message:\n[Translated message here]\n\n"
            f"Interpretation:\n[Interpretation here]\n"
        )

        messages=[
            {"role": "system", "content": "You are an mbti expert."},
            {
                "role": "user",
                "content": prompt
            }
        ]

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=150,
                n=1,
                temperature=0.7,
            )

            # Assuming the AI provides the translation and interpretation separated by a delimiter
            output = response.choices[0].message.content.strip()
            # You may need to parse the output appropriately
            # For simplicity, let's assume the AI returns:
            # "Translated Message: ... Interpretation: ..."
            if "Interpretation:" in output:
                translated_message, interpretation = output.split("Interpretation:")
                translated_message = translated_message.replace("Translated Message:", "").strip()
                interpretation = interpretation.strip()
            else:
                translated_message = output
                interpretation = ""
        except Exception as e:
            translated_message = ""
            interpretation = "Sorry, an error occurred while processing your request."

        return render_template(
            'translator.html',
            from_mbti=from_mbti,
            to_mbti=to_mbti,
            original_message=original_message,
            translated_message=translated_message,
            interpretation=interpretation,
            mbti_types=MBTI_TYPES,
            visitor_count=visitor_count,
            unique_visitor_count=unique_visitor_count,
            translator_clicks=translator_clicks
        )
    else:
        return render_template(
            'translator.html',
            from_mbti='INTJ',
            to_mbti='INFP',
            original_message='',
            translated_message='',
            interpretation='',
            mbti_types=MBTI_TYPES,
            visitor_count=visitor_count,
            unique_visitor_count=unique_visitor_count,
            translator_clicks=translator_clicks
        )


@app.route('/guesser', methods=['GET', 'POST'])
def guesser():
    # Read and update the visitor count
    visitor_count = read_count(COUNTER_FILE)
    visitor_count += 1
    write_count(COUNTER_FILE, visitor_count)

    # Read the visitor count
    unique_visitor_count = read_count(UNIQUE_COUNTER_FILE)

    # Check if the user has been counted in this session
    if not session.get('interpreter_visited'):
        unique_visitor_count += 1
        write_count(UNIQUE_COUNTER_FILE, unique_visitor_count)
        session['interpreter_visited'] = True

    # Read the guesser click count
    guesser_clicks = read_count(GUESSER_CLICKS_FILE)

    if request.method == 'POST':
        # Increment the guesser click count
        guesser_clicks += 1
        write_count(GUESSER_CLICKS_FILE, guesser_clicks)

        message = request.form['message']

        # Create the prompt for the AI
        prompt = (
            f"Analyze the following message and guess the most likely Myers-Briggs personality type(s) of the person who wrote it. "
            f"Provide the top three most likely MBTI types with their respective probabilities in percentages. "
            f"Ensure that all probability predictions are unique and between 85% and 97%. "
            f"Do not include any additional text or headings before the numbered list."
            f"Explain your reasoning for each type.\n\n"
            f"Message: \"{message}\"\n\n"
            f"Be very confident and assertive in your predictions."
            f"Format your response exactly as:\n"
            f"1. [MBTI Type] - [Probability]%\nReasoning: [Your reasoning here]\n"
            f"2. [MBTI Type] - [Probability]%\nReasoning: [Your reasoning here]\n"
            f"3. [MBTI Type] - [Probability]%\nReasoning: [Your reasoning here]"
        )

        messages=[
            {"role": "system", "content": "You are an mbti expert."},
            {
                "role": "user",
                "content": prompt
            }
        ]
        raw_output = None
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=200,
                n=1,
                temperature=0.7,
            )
            raw_output = response.choices[0].message.content
            # Parse the output
            pattern = r'(\d+)\.\s*(\w{4})\s*-\s*(\d{1,3})%\s*Reasoning:\s*(.*?)(?=\n\d+\.|$)'
            matches = re.findall(pattern, raw_output, re.DOTALL)
            parsed_output = []
            for match in matches:
                rank, mbti_type, probability, reasoning = match
                parsed_output.append({
                    'rank': int(rank),
                    'mbti_type': mbti_type,
                    'probability': int(probability),
                    'reasoning': reasoning.strip()
                })
            # Process the AI's output as needed
        except Exception as e:
            print(f"Error: {e}")
            parsed_output = None
            raw_output = "An error occurred while processing the AI response."
        
        if not parsed_output:
            # Fallback to raw_output or display an error message
            parsed_output = None
            if raw_output:
                # Optionally display the raw AI output
                pass
            else:
                raw_output = "Unable to parse the AI response."

        return render_template(
            'guesser.html',
            message=message,
            output=parsed_output or raw_output,
            visitor_count=visitor_count,
            unique_visitor_count=unique_visitor_count,
            guesser_clicks=guesser_clicks
        )
    else:
        return render_template(
            'guesser.html',
            message='',
            output='',
            visitor_count=visitor_count,
            unique_visitor_count=unique_visitor_count,
            guesser_clicks=guesser_clicks
        )


if __name__ == '__main__':
    app.run()
