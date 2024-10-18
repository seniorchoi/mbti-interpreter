from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed, FileRequired
from flask_misaka import Misaka, markdown
from werkzeug.utils import secure_filename
import base64
from openai import OpenAI
import os
import re
import uuid
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from sqlalchemy import func
from dotenv import load_dotenv
from flask_migrate import Migrate
from functools import wraps
import logging
from authlib.integrations.flask_client import OAuth
from urllib.parse import urlencode, urlparse, urljoin
import stripe
from datetime import datetime
import json
import tiktoken
from difflib import SequenceMatcher
from mixpanel import Mixpanel


load_dotenv()

app = Flask(__name__)
Misaka(app)

# Retrieve the DATABASE_URL environment variable
db_url = os.environ.get('DATABASE_URL')

# Replace 'postgres://' with 'postgresql://' if necessary
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-default-secret-key')
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

db = SQLAlchemy(app)
migrate = Migrate(app, db)


stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
mp = Mixpanel("6fa5905b9195c203cb2dde06935627eb")

#app.secret_key = os.environ.get('SECRET_KEY')

app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # Max upload size: 5MB
app.config['UPLOAD_EXTENSIONS'] = ['.jpg', '.jpeg', '.png']
app.config['UPLOAD_PATH'] = 'uploads'  # Create this directory in your project


# Initialize OAuth
oauth = OAuth(app)

auth0 = oauth.register(
    'auth0',
    client_id=os.environ['AUTH0_CLIENT_ID'],
    client_secret=os.environ['AUTH0_CLIENT_SECRET'],
    api_base_url='https://' + os.environ['AUTH0_DOMAIN'],
    client_kwargs={
        'scope': 'openid profile email',
    },
    server_metadata_url='https://' + os.environ['AUTH0_DOMAIN'] + '/.well-known/openid-configuration',
)

logging.basicConfig(level=logging.INFO)


from models import Visitor, UniqueVisitor, ClickCount, UserEmail, User, UserConversation,UserMBTIAnalysis, UserTopic

@app.before_request
def initialize_counts():
    if not Visitor.query.first():
        visitor = Visitor(total_visitors=0)
        db.session.add(visitor)
    
    if not UniqueVisitor.query.first():
        unique_visitor = UniqueVisitor(unique_visitors=0)
        db.session.add(unique_visitor)
    
    for feature in ['interpreter', 'translator', 'guesser']:
        if not ClickCount.query.filter_by(feature=feature).first():
            click_count = ClickCount(feature=feature, count=0)
            db.session.add(click_count)
    
    db.session.commit()


MBTI_TYPES = [
    "INTJ", "INTP", "ENTJ", "ENTP",
    "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ",
    "ISTP", "ISFP", "ESTP", "ESFP"
]

@app.context_processor
def inject_globals():
    user = None
    if 'profile' in session:
        user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    return {
        'user' : user,
        'session': session,
        'current_year': datetime.utcnow().year,
    }


# Authentication required decorator
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'profile' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def requires_premium(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'profile' not in session:
            return redirect('/login')
        user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
        if not user or not user.is_premium:
            return redirect(url_for('upgrade'))
        return f(*args, **kwargs)
    return decorated

def estimate_tokens(text):
    encoding = tiktoken.encoding_for_model('gpt-4')
    tokens = encoding.encode(text)
    return len(tokens)

@app.template_filter('fromjson')
def fromjson(value):
    return json.loads(value)


#ROUTES

@app.route('/', methods=['GET', 'POST'])
def index():
    # Initialize variables
    mbti_type = 'INTJ'
    user_message = ''
    interpretation = None
    user = None  # Initialize user

    # Increment total visitors
    visitor = Visitor.query.first()
    visitor.total_visitors += 1

    # Check and increment unique visitors
    unique_visitor = UniqueVisitor.query.first()
    if not session.get('interpreter_visited'):
        unique_visitor.unique_visitors += 1
        session['interpreter_visited'] = True

    # Get interpreter click count
    interpreter_click = ClickCount.query.filter_by(feature='interpreter').first()

    # Flag to indicate whether to process the prompt
    process_prompt = False

    # Check if form data is present
    if request.method == 'POST':
        mbti_type = request.form.get('mbti_type', 'INTJ')
        user_message = request.form.get('user_message', '')
        # Save form data in session
        session['saved_prompt'] = {
            'mbti_type': mbti_type,
            'user_message': user_message
        }
        # Redirect to self to handle processing in GET
        return redirect(url_for('index'))
    else:
        # Check if there's saved form data after login
        saved_prompt = session.get('saved_prompt', None)
        if saved_prompt:
            mbti_type = saved_prompt.get('mbti_type', 'INTJ')
            user_message = saved_prompt.get('user_message', '')
            # Set flag to process the prompt
            process_prompt = True
            # Remove the saved prompt from the session
            session.pop('saved_prompt', None)

    if process_prompt and user_message:
        if 'profile' not in session:
            # User not authenticated, redirect to login
            session['next_url'] = request.url  # Save the current URL
            # Save the prompt again since we haven't processed it yet
            session['saved_prompt'] = {
                'mbti_type': mbti_type,
                'user_message': user_message
            }
            return redirect(url_for('login', next=request.path))
        else:
            # User is authenticated, retrieve user data
            user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()

            # Check if user has insights or is premium
            if not user.is_premium and user.insights <= 0:
                flash("You have run out of insights. Please purchase more or upgrade to premium.", "warning")
                return redirect(url_for('purchase_insights'))

            # Decrement insights if not premium
            if not user.is_premium:
                user.insights -= 1
                db.session.commit()


            # Increment interpreter click count
            interpreter_click.count += 1
            
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
                    max_tokens=300,
                    n=1,
                    temperature=0.7
                )

                interpretation = response.choices[0].message.content.strip()

                # Add logging here
                #logging.info(f"Interpreter Input: MBTI Type: {mbti_type}, User Message: {user_message}")
                #logging.info(f"Interpreter Output: {interpretation}")
            except Exception as e:
                interpretation = "Sorry, an error occurred while processing your request."
                logging.error(f"Error in Interpreter: {e}")

            db.session.commit()

    else:
        # No message to process
        if 'profile' in session:
            user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
                
    return render_template(
        'index.html',
        mbti_type=mbti_type,
        user_message=user_message,
        interpretation=interpretation,
        mbti_types=MBTI_TYPES,
        visitor_count=visitor.total_visitors,
        unique_visitor_count=unique_visitor.unique_visitors,
        interpreter_clicks=interpreter_click.count,
        user=user
    )


@app.route('/translator', methods=['GET', 'POST'])
def translator():
    # Initialize variables
    from_mbti = 'INTJ'
    to_mbti = 'INFP'
    original_message = ''
    translated_message = ''
    interpretation = ''
    user = None  # Initialize user

    # Increment total visitors
    visitor = Visitor.query.first()
    visitor.total_visitors += 1
    
    # Check and increment unique visitors
    unique_visitor = UniqueVisitor.query.first()
    if not session.get('translator_visited'):
        unique_visitor.unique_visitors += 1
        session['translator_visited'] = True
    
    # Get translator click count
    translator_click = ClickCount.query.filter_by(feature='translator').first()

    # Flag to indicate whether to process the message
    process_message = False

    # Check if form data is present
    if request.method == 'POST':
        from_mbti = request.form.get('from_mbti', 'INTJ')
        to_mbti = request.form.get('to_mbti', 'INFP')
        original_message = request.form.get('original_message', '')
        # Save form data in session
        session['saved_translator'] = {
            'from_mbti': from_mbti,
            'to_mbti': to_mbti,
            'original_message': original_message
        }
        # Redirect to self to handle processing in GET
        return redirect(url_for('translator'))
    else:
        # Check if there's saved form data after login
        saved_translator = session.get('saved_translator', None)
        if saved_translator:
            from_mbti = saved_translator.get('from_mbti', 'INTJ')
            to_mbti = saved_translator.get('to_mbti', 'INFP')
            original_message = saved_translator.get('original_message', '')
            # Set flag to process the message
            process_message = True
            # Remove the saved data from the session
            session.pop('saved_translator', None)

    if process_message and original_message:
        if 'profile' not in session:
            # User not authenticated, redirect to login
            session['next_url'] = request.url  # Save the current URL
            # Save the data again since we haven't processed it yet
            session['saved_translator'] = {
                'from_mbti': from_mbti,
                'to_mbti': to_mbti,
                'original_message': original_message
            }
            return redirect(url_for('login', next=request.path))
        else:
            # User is authenticated, retrieve user data
            user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()

            # Check if user has insights or is premium
            if not user.is_premium and user.insights <= 0:
                flash("You have run out of insights. Please purchase more or upgrade to premium.", "warning")
                return redirect(url_for('purchase_insights'))

            # Decrement insights if not premium
            if not user.is_premium:
                user.insights -= 1
                db.session.commit()

            # Increment translator click count
            translator_click.count += 1
            

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
                    max_tokens=300,
                    n=1,
                    temperature=0.7,
                )

                # Assuming the AI provides the translation and interpretation separated by a delimiter
                output = response.choices[0].message.content.strip()
                # You may need to parse the output appropriately

                # Add logging here
                #logging.info(f"Translator Input: From MBTI: {from_mbti}, To MBTI: {to_mbti}, Original Message: {original_message}")
                #logging.info(f"Translator Output: {output}")
                
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
                logging.error(f"Error in Translator: {e}")

            db.session.commit()
    else:
        # No message to process
        if 'profile' in session:
            user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()

    return render_template(
        'translator.html',
        from_mbti=from_mbti,
        to_mbti=to_mbti,
        original_message=original_message,
        translated_message=translated_message,
        interpretation=interpretation,
        mbti_types=MBTI_TYPES,
        visitor_count=visitor.total_visitors,
        unique_visitor_count=unique_visitor.unique_visitors,
        translator_clicks=translator_click.count,
        user=user
    )


@app.route('/guesser', methods=['GET', 'POST'])
def guesser():
    # Initialize variables
    message = ''
    output = ''
    user = None  # Initialize user

    # Increment total visitors
    visitor = Visitor.query.first()
    visitor.total_visitors += 1
    
    # Check and increment unique visitors
    unique_visitor = UniqueVisitor.query.first()
    if not session.get('guesser_visited'):
        unique_visitor.unique_visitors += 1
        session['guesser_visited'] = True
    
    # Get guesser click count
    guesser_click = ClickCount.query.filter_by(feature='guesser').first()

    # Flag to indicate whether to process the message
    process_message = False

    # Check if form data is present
    if request.method == 'POST':
        message = request.form.get('message', '')
        # Save form data in session
        session['saved_guesser'] = {
            'message': message
        }
        # Redirect to self to handle processing in GET
        return redirect(url_for('guesser'))
    else:
        # Check if there's saved form data after login
        saved_guesser = session.get('saved_guesser', None)
        if saved_guesser:
            message = saved_guesser.get('message', '')
            # Set flag to process the message
            process_message = True
            # Remove the saved data from the session
            session.pop('saved_guesser', None)

    if process_message and message:
        if 'profile' not in session:
            # User not authenticated, redirect to login
            session['next_url'] = request.url  # Save the current URL
            # Save the data again since we haven't processed it yet
            session['saved_guesser'] = {
                'message': message
            }
            return redirect(url_for('login', next=request.path))
        else:
            # User is authenticated, retrieve user data
            user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()

            # Check if user has insights or is premium
            if not user.is_premium and user.insights <= 0:
                flash("You have run out of insights. Please purchase more or upgrade to premium.", "warning")
                return redirect(url_for('purchase_insights'))

            # Decrement insights if not premium
            if not user.is_premium:
                user.insights -= 1
                db.session.commit()

            # Increment guesser click count
            guesser_click.count += 1

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
                    max_tokens=400,
                    n=1,
                    temperature=0.7,
                )
                raw_output = response.choices[0].message.content

                # Add logging here
                #logging.info(f"Guesser Input: Message: {message}")
                #logging.info(f"Guesser Output: {raw_output}")

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
                # Prepare the output to send to the template
                if parsed_output:
                    output = parsed_output  # List of dictionaries
                else:
                    output = raw_output  # String message

                
                # Add logging here
                logging.info(f"Guesser Input: Message: {message}")
                logging.info(f"Guesser Output: {output}")

            except Exception as e:
                logging.error(f"Error in Guesser: {e}")
                parsed_output = None

            db.session.commit()
    else:
        # No message to process
        if 'profile' in session:
            user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()

    return render_template(
        'guesser.html',
        message=message,
        output=output,
        visitor_count=visitor.total_visitors,
        unique_visitor_count=unique_visitor.unique_visitors,
        guesser_clicks=guesser_click.count,
        user=user
    )


@app.route('/vision', methods=['GET', 'POST'])
@requires_auth
def vision():
    from forms import ImageUploadForm
    form = ImageUploadForm()
    interpretation = None
    user = None
    filename = None
    encoded_image = None  # For base64 embedding
    file_ext = None  # Initialize file extension

    # Check if the user is authenticated
    if 'profile' in session:
        user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()

    if request.method == 'POST':
        if not user:
            return jsonify({'error': 'Please log in first.'}), 401

        # Check insights
        if not user.is_premium and user.insights <= 0:
            flash("You have run out of insights. Please purchase more insights or upgrade to premium.", "warning")
            return jsonify({'error': 'You have run out of insights. Please purchase more or upgrade to premium.'}), 403

        # Decrement insights if not premium
        if not user.is_premium:
            user.insights -= 2
            db.session.commit()

        # Save the uploaded image
        uploaded_file = request.files.get('image')
        if not uploaded_file:
            return jsonify({'error': 'No image uploaded'}), 400

        filename = secure_filename(uploaded_file.filename)
        if filename != '':
            file_ext = os.path.splitext(filename)[1]
            if file_ext.lower() not in app.config['UPLOAD_EXTENSIONS']:
                return jsonify({'error': 'Invalid image format!'}), 400

            image_path = os.path.join(app.config['UPLOAD_PATH'], filename)
            uploaded_file.save(image_path)

            # Read and encode the image in base64
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')


            # Prepare the message content with the image
            message_content = [
                {"type": "text", "text": "Analyze the following image and describe which 3 MBTI type you can most closely associate it to, and put a percentage above 83 percent for each. Explain your reasoning. Do not say 'I can't analyze the image directly.'" },
                {"type": "image_url", "image_url": {"url": f"data:image/{file_ext.lower().strip('.')};base64,{base64_image}"}}
            ]

            # Call the OpenAI API
            try:
                response = client.chat.completions.create(
                    model="gpt-4o",  # Use the appropriate model name
                    messages=[
                        {"role": "system", "content": "You are an MBTI expert."},
                        {"role": "user", "content": message_content}
                    ],
                    max_tokens=400,
                    n=1,
                    temperature=0.9
                )
                interpretation = response.choices[0].message.content.strip()

                html_interpretation = markdown(interpretation)


                # Add logging here
                logging.info(f"Image interpretation: {html_interpretation}")

                # Optionally, delete the uploaded image after processing
                os.remove(image_path)

                return jsonify({
                    'interpretation': html_interpretation,
                    'encoded_image': f"data:image/{file_ext.lower().strip('.')};base64,{base64_image}",
                    'file_ext': file_ext.lower().strip('.')
                })

            except Exception as e:
                logging.error(f"Error in image analysis: {e}")
                return jsonify({'error': 'An error occurred while analyzing the image.'}), 500

    return render_template(
        'vision.html',
        form=form,
        user=user,
        interpretation=None,
        #filename=filename,
        encoded_image=None,
        #file_ext=file_ext
    )

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    filename = secure_filename(filename)
    return send_from_directory(app.config['UPLOAD_PATH'], filename)

###Dynamic Test###

@app.route('/adaptive_test', methods=['GET', 'POST'])
@requires_premium
def adaptive_test():
    user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    if not user:
        flash("User not found.", "warning")
        return redirect(url_for('index'))
    """
    # Retrieve past conversations
    past_conversations = UserConversation.query.filter_by(user_id=user.id).all()

    # Extract previously asked questions
    past_questions = []
    for conv in past_conversations:
        conversation = json.loads(conv.conversation)
        questions = [msg['content'] for msg in conversation if msg['role'] == 'assistant']
        past_questions.extend(questions)

    # Limit to the last 5 questions to avoid exceeding token limits
    recent_past_questions = past_questions[-10:]
    """
    # Initialize question number when starting the test
    if 'question_number' not in session:
        session['question_number'] = 1

    if 'exchange_count' not in session:
        session['exchange_count'] = 0
    
    if 'name' not in session:
        # Prompt the user for their name
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            if name:
                session['name'] = name
                session['session_id'] = str(uuid.uuid4())
                session['conversation'] = []
                session['question_number'] = 1
                session['exchange_count'] = 0  # Initialize exchange counter
                session.modified = True
                return redirect(url_for('adaptive_test'))
            else:
                flash("Please enter your name to continue.", "warning")
        return render_template('ask_name.html', user=user)
    else:
        if 'system_prompt' not in session:
            # Fetch topics already explored by the user
            explored_topics = [ut.topic for ut in user.user_topics]
            past_questions_text = '; '.join(session.get('past_questions', []))
            # Instruct the AI to select a new topic
            system_prompt = {
                'role': 'system',
                'content': (
                    f'You are an experienced and empathetic psychologist conducting an in-depth personality assessment with a client named {session["name"]}. '
                    f'Your goal is to understand {session["name"]}\'s cognitive processes by encouraging them to share stories, experiences, thoughts, and feelings. '
                    f'Please follow these instructions carefully:\n'
                    f'1. Select a single topic relevant to MBTI personality assessment that has not been previously discussed with {session["name"]}. '
                    f'2. Do not choose any of the following topics: {", ".join(explored_topics)}.\n'
                    f'3. Introduce the topic by stating exactly: "The topic I would like to explore with you today is: [Topic]".\n'
                    f'4. Ask only **one** open-ended, thought-provoking question related to this topic.\n'
                    f'5. Do **not** ask multiple questions at once.\n'
                    f'6. Wait for {session["name"]}\'s response before proceeding.\n'
                    f'7. After each of {session["name"]}\'s responses, provide a brief acknowledgment (one sentence) and then ask the next open-ended question related to the topic.\n'
                    f'8. Ensure that each question is unique and explores new angles or subtopics not previously covered.\n'
                    f'9. Avoid repeating or rephrasing questions you have already asked.\n'
                    f'10. Do not ask direct questions about preferences or personality traits.\n'
                    f'11. Ensure the questions are indirect and do not hint at specific personality traits.\n'
                    f'12. Make the conversation feel natural and comfortable.\n'
                    f'13. When you feel you have gathered enough information to understand {session["name"]}\'s cognitive processes, conclude the assessment by stating exactly: "Thank you for sharing, {session["name"]}. Our session is now complete." Only say this closing statement at the very end of the conversation.\n'
                    f'14. Do not include any content outside of these instructions.\n'
                    f'15. Under no circumstances should you include multiple questions or the closing statement in a single response before the end of the session.'
                )
            }
            session['system_prompt'] = system_prompt
            session['conversation'] = []
            session.modified = True
            
        if request.method == 'POST':
            user_input = request.form.get('user_input', '').strip()

            if not session['conversation']:
                # Initial interaction, generate AI's first message
                messages = [session['system_prompt']]
            else:
                # Append user's response
                if user_input:
                    session['conversation'].append({'role': 'user', 'content': user_input})
                    session.modified = True
                else:
                    flash("Please enter your response.", "warning")
                    return redirect(url_for('adaptive_test'))
                """
                if user_input:
                    if not user_input and not session['conversation']:
                        messages = [session['system_prompt']]
                    else:
                        # Append user's response
                        session['conversation'].append({'role': 'user', 'content': user_input})
                """
                messages = [session['system_prompt']] + session['conversation']     
                # Append user's response to the conversation
                #session['conversation'].append({'role': 'user', 'content': user_input})
                #session.modified = True
                #messages = [session['system_prompt']] + session['conversation'] + [{'role': 'user', 'content': user_input}]

            # **Increment exchange count before generating the AI's next question**
            session['exchange_count'] += 1
            session.modified = True

            # Generate AI's next response
            #ai_response = generate_next_question(session['conversation'], session['name'], recent_past_questions)
            try:
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=messages,
                    max_tokens=200,
                    temperature=0.8,
                )
                ai_response = response.choices[0].message.content.strip()
                logging.info(f"AI Assistant's response: {ai_response}")

                #session['conversation'].append({'role': 'user', 'content': user_input})
                #session['conversation'].append({'role': 'assistant', 'content': ai_response})
                # Append AI's response to the conversation with question number
                session['conversation'].append({
                    'role': 'assistant',
                    'content': ai_response,
                    'question_number': session['question_number']
                })
                if 'past_questions' not in session:
                    session['past_questions'] = []
                session['past_questions'].append(ai_response)
                
                session.modified = True

                # Increment the question number
                session['question_number'] += 1
                # Extract the topic from the AI's message if not already set
                if 'topic' not in session:
                    session['topic'] = extract_topic_from_ai_response(ai_response)
                    #logging.info(f"session topic: {session['topic']}")
                    session.modified = True

                MAX_EXCHANGES = 8

                if 'session is now complete' in ai_response.lower() or session['exchange_count'] >= MAX_EXCHANGES:
                    # Analyze responses and provide the result
                    mbti_result = analyze_responses(session['conversation'], session['name'])

                    # Generate a unique session ID if not already in session
                    if 'session_id' not in session:
                        session['session_id'] = str(uuid.uuid4())

                    # Save the conversation to the database
                    conversation_record = UserConversation(
                        user_id=user.id,
                        session_id=session['session_id'],
                        conversation=json.dumps(session['conversation']),
                    )
                    db.session.add(conversation_record)

                    # Save the explored topic
                    if 'topic' in session:
                        user_topic = UserTopic(
                            user_id=user.id,
                            topic=session['topic'],
                            timestamp=datetime.utcnow()
                        )
                        db.session.add(user_topic)
                    
                    analysis_record = UserMBTIAnalysis(
                        user_id=user.id,
                        session_id=session['session_id'],
                        mbti_type=mbti_result.get('type', 'Unknown'),
                        confidence=mbti_result.get('confidence', 0.0),
                        explanation=mbti_result.get('explanation', '')
                    )
                    db.session.add(analysis_record)
                    
                    user.mbti_type = mbti_result.get('type', 'Unknown')
                    db.session.commit()

                    session['mbti_result'] = mbti_result
                    #logging.info(f"MBTI Result: {mbti_result}")
                    # Clear session data related to the test
                    session.pop('question_number', None)
                    session.pop('conversation', None)
                    session.pop('exchange_count', None)
                    session.pop('name', None)
                    session.pop('session_id', None)
                    session.pop('topic', None)
                    session.pop('system_prompt', None)
                    # **Return JSON indicating test completion**
                    return jsonify({'test_complete': True})
                else:
                    return jsonify({'conversation': session['conversation']})
            except Exception as e:
                logging.error(f"Error generating AI response: {e}")
                return jsonify({'error': 'An error occurred while generating the AI response.'}), 500            

        return render_template('adaptive_test.html', user=user)

@app.route('/test_result')
@requires_auth
def test_result():
    mbti_result = session.get('mbti_result')
    if not mbti_result:
        flash("No test result found.", "warning")
        return redirect(url_for('adaptive_test'))
    response = render_template('test_result.html', mbti_result=mbti_result)
    # Clear the result from the session
    session.pop('mbti_result', None)
    return response


def generate_next_question(conversation, name, past_questions):
    past_questions_text = '; '.join(past_questions)

    # Include the user's name in the system prompt
    system_prompt = {
        'role': 'system',
        'content': (
            f'You are an experienced and empathetic psychologist conducting an in-depth personality assessment with a client named {name}. '
            f'Your goal is to understand {name}\'s cognitive processes by encouraging them to share stories, experiences, thoughts, and feelings. '
            f'Ask open-ended, thought-provoking questions that explore different aspects of {name}\'s life, perspectives, motivations, and behaviors. '
            f'Ensure that each question is unique and delves into new topics or angles not previously covered in the conversation. '
            f'Avoid repeating or rephrasing questions you have already asked. '
            f'Do not list or mention any past questions. '
            f'Here are some questions you have already asked: {past_questions_text}. '
            f'Avoid direct questions about preferences or personality traits. '
            f'Ensure the question is indirect and does not hint at specific personality traits. '
            f'Make the conversation feel natural and comfortable, and encourage {name} to reflect deeply. '
            f'When you feel you have gathered enough information to understand {name}\'s cognitive processes, conclude the assessment by saying exactly: '
            f'"Thank you for sharing, {name}. Our session is now complete."'
        )
    }

    # Build the messages list
    messages = [system_prompt] + conversation

    # Extract the user's last message
    #user_last_message = conversation[-1]['content']

    # Call the OpenAI API
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=200,
            temperature=0.9,  # Increased for creativity
        )
        ai_response = response.choices[0].message.content.strip()

        # Check if the question is similar to past questions
        if is_similar_to_past_questions(ai_response, past_questions):
            # If similar, prompt the AI to generate a new question
            for _ in range(3):  # Retry up to 3 times
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages + [{'role': 'assistant', 'content': ai_response}],
                    max_tokens=200,
                    temperature=0.9,
                )
                ai_response = response.choices[0].message.content.strip()
                if not is_similar_to_past_questions(ai_response, past_questions):
                    break
            else:
                # If still similar after retries, proceed with the last response
                pass

        return ai_response
    except Exception as e:
        logging.error(f"Error generating AI response: {e}")
        return "I'm sorry, but I'm having trouble responding right now. Please try again later."


def extract_topic_from_ai_response(ai_response):
    match = re.search(
        r'The topic I would like to explore with you today is:\s*(.*?)(?:\.|\")',
        ai_response, re.IGNORECASE
    )
    if match:
        topic = match.group(1).strip()
        return topic
    else:
        return 'Unknown Topic'

def is_similar_to_past_questions(new_question, past_questions, threshold=0.7):
    for past_question in past_questions:
        similarity = SequenceMatcher(None, new_question.lower(), past_question.lower()).ratio()
        if similarity > threshold:
            return True
    return False

def analyze_responses(conversation, name):
    # Initialize mbti_result with default values
    mbti_result = {
        'type': 'Unknown',
        'confidence': 0.0,
        'explanation': 'An error occurred during analysis.'
    }
    # Convert the conversation into a transcript format
    transcript = ''
    for msg in conversation:
        role = 'Psychologist' if msg['role'] == 'assistant' else name
        transcript += f"{role}: {msg['content']}\n"

    # Use the AI to analyze the conversation and determine MBTI type
    analysis_prompt = {
        'role': 'system',
        'content': (
            f'You are an expert psychologist specializing in MBTI and cognitive functions. '
            f'Analyze the following transcript of a conversation with a client named {name}. '
            f'Based on their stories and experiences, infer {name}\'s dominant cognitive functions (e.g., Te, Ti, Se, Si, Fe, Fi, Ne, Ni). '
            f'Determine {name}\'s MBTI personality type, including their dominant, auxiliary, tertiary, and inferior cognitive functions. '
            f'Determine {name}\'s MBTI personality type and your confidence level in percentage.'
            f'Provide a comprehensive analysis, referencing specific parts of the conversation that support your conclusions. '
            f'Present your findings in the following format exactly:\n\n'
            f'MBTI Type: [4-letter MBTI Type]\n'
            f'Confidence: [Your confidence level as a percentage]\n'
            f'Explanation:\n[Your detailed explanation here]\n\n'
            f'Make sure to start with "MBTI Type:" and include all sections as specified.'
        )
    }

    #conversation_messages = [msg for msg in conversation if msg['role'] in ['user', 'assistant']]
    messages = [
        analysis_prompt,
        {'role': 'user', 'content': transcript}
    ]
    # Call the OpenAI API
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            max_tokens=1500,
            temperature=0.7,
        )
        analysis = response.choices[0].message.content.strip()

        # Add logging to see the AI's response
        #logging.info(f"AI Analysis Response:\n{analysis}")

        # Parse the analysis to extract MBTI type and explanation
        mbti_result = parse_detailed_analysis(analysis)

        # Add logging to see the parsed result
        logging.info(f"Parsed MBTI Result: {mbti_result}")

        return mbti_result
    except Exception as e:
        logging.error(f"Error analyzing responses: {e}")
        return {
            'type': 'Unknown',
            'confidence': 0.0,
            'explanation': 'An error occurred during analysis.'}

def parse_detailed_analysis(analysis_text):
    mbti_type = 'Unknown'
    explanation = ''
    confidence = 0.0

    # Use regex to extract information
    mbti_type_match = re.search(r'MBTI Type:\s*([A-Z]{4})', analysis_text)
    if mbti_type_match:
        mbti_type = mbti_type_match.group(1)


    explanation_match = re.search(r'Explanation:\s*(.+)', analysis_text, re.DOTALL)
    if explanation_match:
        explanation = explanation_match.group(1).strip()

    # Extract confidence if provided
    confidence_match = re.search(r'Confidence:\s*(\d+)%', analysis_text)
    if confidence_match:
        confidence = float(confidence_match.group(1)) if confidence_match else 0.0


    return {
        'type': mbti_type,
        'explanation': explanation,
        'confidence': confidence
    }


@app.route('/get_conversation')
def get_conversation():
    conversation = session.get('conversation', [])
    return jsonify({'conversation': conversation})


def is_safe_url(target):
    """Ensure the target URL is safe for redirection."""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (
        test_url.scheme in ('http', 'https') and
        ref_url.netloc == test_url.netloc
    )


#COMBINED MBTI###

def aggregate_mbti_analyses(user_id):
    analyses = UserMBTIAnalysis.query.filter_by(user_id=user_id).all()
    if not analyses:
        return None

    # Count occurrences of each MBTI type
    type_counts = {}
    for analysis in analyses:
        mbti_type = analysis.mbti_type
        if mbti_type in type_counts:
            type_counts[mbti_type] += 1
        else:
            type_counts[mbti_type] = 1

    # Determine the most frequent MBTI type
    most_common_type = max(type_counts, key=type_counts.get)
    total_analyses = len(analyses)
    confidence = (type_counts[most_common_type] / total_analyses) * 100

    # Compile explanations and functions
    explanations = [analysis.explanation for analysis in analyses if analysis.mbti_type == most_common_type]
    combined_explanation = '\n\n'.join(explanations)

    # For simplicity, take the functions from the most recent analysis of the most common type
    recent_analysis = UserMBTIAnalysis.query.filter_by(user_id=user_id, mbti_type=most_common_type).order_by(UserMBTIAnalysis.timestamp.desc()).first()

    aggregated_result = {
        'type': most_common_type,
        'confidence': round(confidence, 2),
        'explanation': combined_explanation,
    }

    return aggregated_result

def get_user_past_conversations(user_id):
    # Fetch all past conversations for the user
    past_conversations = UserConversation.query.filter_by(user_id=user_id).order_by(UserConversation.timestamp.desc()).limit(10).all()
    return past_conversations
"""
def compile_past_conversations(user, past_conversations):
    transcript = ''
    for conv in past_conversations:
        # Load the conversation from JSON
        conversation = json.loads(conv.conversation)
        
        # Build the conversation transcript
        transcript += f"Session on {conv.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
        for msg in conversation:
            role = 'Psychologist' if msg['role'] == 'assistant' else user.name
            transcript += f"{role}: {msg['content']}\n"
        transcript += '\n'
    return transcript
"""

def summarize_conversation(conversation_text):
    summary_prompt = [
        {'role': 'system', 'content': 'You are a helpful assistant that summarizes conversations succinctly.'},
        {'role': 'user', 'content': f'Please provide a concise summary of the following conversation:\n\n{conversation_text}'}
    ]
    try:
        response = client.chat.completions.create(
            model='gpt-4o',
            messages=summary_prompt,
            max_tokens=500,
            temperature=0.5,
        )
        summary = response.choices[0].message.content.strip()
        return summary
    except Exception as e:
        logging.error(f"Error summarizing conversation: {e}")
        return None

def compile_past_conversations(user, past_conversations):
    transcript = ''
    total_tokens = 0
    max_tokens = 4000  # Adjust as needed, keeping in mind the model's limit and desired response length

    for conv in past_conversations:
        conversation_text = f"Session on {conv.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
        conversation = json.loads(conv.conversation)
        for msg in conversation:
            role = 'Psychologist' if msg['role'] == 'assistant' else 'You'
            conversation_text += f"{role}: {msg['content']}\n"
        conversation_text += '\n'

        tokens = estimate_tokens(conversation_text)
        if total_tokens + tokens > max_tokens:
            # Need to summarize
            summary = summarize_conversation(conversation_text)
            if summary:
                summary_text = f"Summary of session on {conv.timestamp.strftime('%Y-%m-%d %H:%M:%S')}:\n{summary}\n\n"
                transcript += summary_text
                total_tokens += estimate_tokens(summary_text)
            else:
                # If summarization fails, skip this conversation
                continue
        else:
            transcript += conversation_text
            total_tokens += tokens

    return transcript

def generate_combined_analysis(user, transcript):
    analysis_prompt = [
        {
            'role': 'system',
            'content': (
                f'You are an expert psychologist specializing in MBTI personality assessments and cognitive functions. '
                f'Provide a detailed analysis using cognitive functions, referencing specific aspects and timestamps from the following transcript with {user} to support your conclusions. '
                f'Make sure your analysis is structured, clear, and uses headings as indicated. '
                f'Based on the following transcript of multiple sessions with {user}, please provide a comprehensive MBTI analysis '
                f'Provide your analysis in the following format:\n\n'
                f'1. MBTI Type:\n'
                f'Clearly state the MBTI type you believe {user} to be.\n\n'
                f'2. Confidence Level:\n'
                f'Include your level of confidence as a percentage.\n\n'
                f'3. Cognitive Function Analysis:\n'
            )
        },
        {
            'role': 'user',
            'content': transcript
        }
    ]
    try:
        response = client.chat.completions.create(
            model='gpt-4',
            messages=analysis_prompt,
            max_tokens=4000,  # Adjust as needed
            temperature=0.7,
        )
        analysis = response.choices[0].message.content.strip()
        return analysis
    except Exception as e:
        logging.error(f"Error generating combined analysis: {e}")
        return None

@app.route('/get_combined_report_data')
@requires_premium
def get_combined_report_data():
    user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    past_conversations = get_user_past_conversations(user.id)

    if not past_conversations:
        return jsonify({'error': 'No past conversations found. Please take the adaptive test first.'}), 400

    # Compile the transcript
    transcript = compile_past_conversations(user, past_conversations)

    # Generate the combined analysis
    combined_analysis = generate_combined_analysis(user, transcript)

    if not combined_analysis:
        return jsonify({'error': 'An error occurred while generating the combined analysis.'}), 500

    # Prepare the data to send back
    data = {
        'combined_analysis': combined_analysis,
        'past_conversations': [
            {
                'timestamp': conv.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'conversation': json.loads(conv.conversation)
            }
            for conv in past_conversations
        ]
    }

    return jsonify(data)

@app.route('/combined_report')
@requires_premium
def combined_report():
    user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    return render_template('combined_report.html', user=user)


#LOGIN
@app.route('/login')
def login():
    next_url = request.args.get('next')
    if next_url and is_safe_url(next_url):
        session['next_url'] = next_url
    else:
        session['next_url'] = url_for('index')
    return auth0.authorize_redirect(redirect_uri=os.environ['AUTH0_CALLBACK_URL'])

@app.route('/callback')
def callback_handling():
    auth0.authorize_access_token()
    resp = auth0.get('userinfo')
    userinfo = resp.json()

    # Store user information in session
    session['profile'] = {
        'user_id': userinfo['sub'],
        'name': userinfo['name'],
        'email': userinfo['email'],
        'picture': userinfo['picture']
    }

    # Check if user exists in the database
    user = User.query.filter_by(auth0_id=userinfo['sub']).first()
    if not user:
        user = User(
            auth0_id=userinfo['sub'],
            email=userinfo['email'],
            is_premium=False  # Default to free user
        )
        db.session.add(user)
        db.session.commit()
        # Track sign-up event in Mixpanel
        mp.track(user.id, 'User Sign Up', {
        'Email': user.email
        })
        
    else:
        # Existing user, check if insights is None
        if user.insights is None:
            user.insights = 50  # Assign default insights value
            db.session.commit()
    
        # Track login event in Mixpanel
        mp.track(user.id, 'User Log In', {
        'Email': user.email,
        'Premium': user.is_premium
        })


    # Redirect back to the original page
    next_url = session.pop('next_url', None)
    if next_url and is_safe_url(next_url):
        return redirect(next_url)
    else:
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    mp.track(user.id, 'User Log Out', {
        'Email': user.email
    })
    # Clear session data
    session.clear()

    # Construct the logout URL
    params = {
        'returnTo': url_for('index', _external=True),
        'client_id': os.environ['AUTH0_CLIENT_ID']
    }
    logout_url = 'https://{}/v2/logout?{}'.format(
        os.environ['AUTH0_DOMAIN'],
        urlencode(params)
    )
    return redirect(logout_url)


#STRIPE
@app.route('/upgrade')
@requires_auth
def upgrade():
    user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    return render_template(
        'upgrade.html',
        user=user,
        stripe_publishable_key=os.environ.get('STRIPE_PUBLISHABLE_KEY'))

@app.route('/create-checkout-session', methods=['POST'])
@requires_auth
def create_checkout_session():
    try:
        user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
        if not user:
            logging.error("User not found in database.")
            return jsonify(error="User not found."), 400

        customer_email = user.email

        # Use existing Stripe Customer ID if available
        if user.stripe_customer_id:
            customer_id = user.stripe_customer_id
        else:
            # Create a new Stripe customer
            customer = stripe.Customer.create(email=customer_email)
            customer_id = customer.id
            user.stripe_customer_id = customer_id
            db.session.commit()

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': 'price_1QAmJBKjJ23rv2vUdLNtT9kq',  # Replace with your actual Price ID
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('payment_cancel', _external=True),
            customer=customer_id
        )
        return jsonify({'sessionId': checkout_session.id})
    except Exception as e:
        logging.error(f"Error in create_checkout_session: {e}")
        return jsonify(error=str(e)), 403

@app.route('/payment-success')
@requires_auth
def payment_success():
    try:
        session_id = request.args.get('session_id')
        if not session_id:
            logging.error("Session ID missing in payment_success")
            return "Session ID is missing.", 400

        # Retrieve the checkout session from Stripe
        checkout_session = stripe.checkout.Session.retrieve(session_id)

        if checkout_session.payment_status == 'paid':
            user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
            if user:
                user.is_premium = True
                # Store Stripe Customer ID and Subscription ID
                user.stripe_customer_id = checkout_session.customer
                user.stripe_subscription_id = checkout_session.subscription
                db.session.commit()
                session['profile']['is_premium'] = True
            else:
                logging.error("User not found in database.")
                return "User not found.", 404
            return render_template('payment_success.html')
        else:
            logging.warning("Payment not completed.")
            return redirect(url_for('upgrade'))
    except Exception as e:
        logging.error(f"Error in payment_success: {e}")
        return "An error occurred during payment processing.", 500

@app.route('/payment-cancel')
@requires_auth
def payment_cancel():
    return render_template('payment_cancel.html')

@app.route('/cancel-subscription', methods=['POST'])
@requires_auth
def cancel_subscription():
    try:
        user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
        if not user:
            logging.error("User not found in database.")
            return "User not found.", 404

        # Retrieve the Stripe Customer ID and Subscription ID
        stripe_customer_id = user.stripe_customer_id
        stripe_subscription_id = user.stripe_subscription_id

        if not stripe_subscription_id:
            logging.error("No subscription found for user.")
            return "No active subscription to cancel.", 400

        # Cancel the subscription in Stripe
        stripe.Subscription.delete(stripe_subscription_id)

        # Update user's status in the database
        user.is_premium = False
        user.stripe_subscription_id = None
        db.session.commit()

        # Update session data
        session['profile']['is_premium'] = False

        flash("Your subscription has been cancelled.", "success")
        return redirect(url_for('profile'))
    except Exception as e:
        logging.error(f"Error in cancel_subscription: {e}")
        flash("An error occurred while cancelling your subscription.", "danger")
        return redirect(url_for('profile'))

#ONE TIME PURCHASE
@app.route('/purchase-insights', methods=['GET'])
def purchase_insights():
    user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    if not user:
        logging.error("User not found in database.")
        return jsonify(error="User not found."), 400
    return render_template(
        'purchase_insights.html',
        user=user,        
        stripe_publishable_key=os.environ.get('STRIPE_PUBLISHABLE_KEY'),
        basic_price=2,
        standard_price=6,
        premium_price=8,
        basic_insights=50,
        standard_insights=120,
        premium_insights=200,
    )


@app.route('/create-one-time-session', methods=['POST'])
def create_one_time_session():
    try:
        data = request.get_json()
        package_type = data.get('package_type')

        if 'profile' not in session:
            # User not authenticated
            return jsonify(error="You need to log in to make a purchase."), 401

        user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
        if not user:
            logging.error("User not found in database.")
            return jsonify(error="User not found."), 400

        customer_email = user.email

        # Use existing Stripe Customer ID if available
        if user.stripe_customer_id:
            customer_id = user.stripe_customer_id
        else:
            # Create a new Stripe customer
            customer = stripe.Customer.create(email=customer_email)
            customer_id = customer.id
            user.stripe_customer_id = customer_id
            db.session.commit()

        # Define your price IDs and insights mapping
        package_details = {
            'basic': {
                'price_id': 'price_1Q99LeKjJ23rv2vUREoligTP',  # Replace with your actual Price ID
                'insights': 50
            },
            'standard': {
                'price_id': 'price_1Q99ZSKjJ23rv2vUmajrrkeK',  # Replace with your actual Price ID
                'insights': 120
            },
            'premium': {
                'price_id': 'price_1Q99MHKjJ23rv2vU52gnja0g',  # Replace with your actual Price ID
                'insights': 200
            }
        }

        if package_type not in package_details:
            return jsonify(error="Invalid package type."), 400

        package = package_details[package_type]

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': package['price_id'],
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('one_time_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}&package_type=' + package_type,
            cancel_url=url_for('purchase_insights', _external=True),
            customer=customer_id
        )
        return jsonify({'sessionId': checkout_session.id})
    except Exception as e:
        logging.error(f"Error in create_one_time_session: {e}")
        return jsonify(error=str(e)), 403

@app.route('/one-time-success')
def one_time_success():
    try:
        session_id = request.args.get('session_id')
        package_type = request.args.get('package_type')

        if not session_id or not package_type:
            logging.error("Session ID or package type missing in one_time_success")
            return "Session ID or package type is missing.", 400

        # Retrieve the checkout session from Stripe
        checkout_session = stripe.checkout.Session.retrieve(session_id)

        if checkout_session.payment_status == 'paid':
            if 'profile' not in session:
                # User not authenticated
                return redirect(url_for('login', next=request.url))

            user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
            if user:
                # Define insights mapping
                insights_mapping = {
                    'basic': 20,
                    'standard': 120,
                    'premium': 200
                }
                insights_to_add = insights_mapping.get(package_type, 0)
                # Add insights to the user's account
                user.insights += insights_to_add
                db.session.commit()
                flash(f"Your purchase was successful! {insights_to_add} insights have been added to your account.", "success")
            else:
                logging.error("User not found in database.")
                return "User not found.", 404
            return redirect(url_for('index'))
        else:
            logging.warning("Payment not completed.")
            return redirect(url_for('purchase_insights'))
    except Exception as e:
        logging.error(f"Error in one_time_success: {e}")
        return "An error occurred during payment processing.", 500


@app.route('/profile')
@requires_auth
def profile():
    user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    return render_template('profile.html', user=user)

@app.route('/privacy-policy')
def privacy_policy():
    user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    return render_template('privacy_policy.html', user=user)


# Route for Terms of Service
@app.route('/terms-of-service')
def terms_of_service():
    user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
    return render_template('terms_of_service.html', user=user)








if __name__ == '__main__':
    app.run()
