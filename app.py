    # app.py
from flask import Flask, render_template, request
from openai import OpenAI
import os


app = Flask(__name__)

client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))


COUNTER_FILE = 'counter.txt'


MBTI_TYPES = [
    "INTJ", "INTP", "ENTJ", "ENTP",
    "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ",
    "ISTP", "ISFP", "ESTP", "ESFP"
]


def read_visitor_count():
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, 'r') as f:
            count = f.read()
            if count:
                return int(count)
            else:
                return 0
    else:
        return 0

def write_visitor_count(count):
    with open(COUNTER_FILE, 'w') as f:
        f.write(str(count))


@app.route('/', methods=['GET', 'POST'])
def index():
    visitor_count = read_visitor_count()
    visitor_count += 1
    write_visitor_count(visitor_count)

    if request.method == 'POST':
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
            model="gpt-4",
            messages=messages,
            max_tokens=300,
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
            mbti_types=MBTI_TYPES
        )
    else:
        # Default values for GET request
        return render_template(
            'index.html',
            visitor_count=visitor_count,
            mbti_type='INTJ',
            user_message='',
            interpretation=None,
            mbti_types=MBTI_TYPES
        )

    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
