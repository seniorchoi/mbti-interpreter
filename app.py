from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from openai import OpenAI
import os
import re
import uuid
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from flask_migrate import Migrate
from functools import wraps
import logging
from authlib.integrations.flask_client import OAuth
from urllib.parse import urlencode
import stripe
from datetime import datetime

load_dotenv()

app = Flask(__name__)


# Retrieve the DATABASE_URL environment variable
db_url = os.environ.get('DATABASE_URL')

# Replace 'postgres://' with 'postgresql://' if necessary
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


db = SQLAlchemy(app)
migrate = Migrate(app, db)


stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
app.secret_key = os.environ.get('SECRET_KEY')


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


from models import Visitor, UniqueVisitor, ClickCount, UserEmail, User

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
    return {
        'session': session,
        'current_year': datetime.utcnow().year,
    }

#@app.context_processor
#def inject_session():
#    return dict(session=session)

def email_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('email_provided'):
            return redirect(url_for('enter_email'))
        return f(*args, **kwargs)
    return decorated_function

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


#ROUTES

# Route to display the email form
@app.route('/enter_email', methods=['GET'])
def enter_email():
    return render_template('email_form.html')

# Route to handle form submission
@app.route('/submit_email', methods=['POST'])
def submit_email():
    email = request.form.get('email')
    if email:
        # Check if email already exists
        existing_email = UserEmail.query.filter_by(email=email).first()
        if not existing_email:
            new_email = UserEmail(email=email)
            db.session.add(new_email)
            db.session.commit()
        # Set a session variable to indicate email has been provided
        session['email_provided'] = True
        return redirect(url_for('index'))
    else:
        return redirect(url_for('index'))

@app.route('/', methods=['GET', 'POST'])
def index():
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
    
    if request.method == 'POST':
        # Increment interpreter click count
        interpreter_click.count += 1
        
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
            max_tokens=200,
            n=1,
            stop=None,
            temperature=0.7)

            interpretation = response.choices[0].message.content.strip()

            # Add logging here
            logging.info(f"Interpreter Input: MBTI Type: {mbti_type}, User Message: {user_message}")
            logging.info(f"Interpreter Output: {interpretation}")
        except Exception as e:
            interpretation = "Sorry, an error occurred while processing your request."
            logging.error(f"Error in Interpreter: {e}")

        db.session.commit()

        return render_template(
            'index.html',
            interpretation=interpretation,
            mbti_type=mbti_type,
            user_message=user_message,
            mbti_types=MBTI_TYPES,
            visitor_count=visitor.total_visitors,
            unique_visitor_count=unique_visitor.unique_visitors,
            interpreter_clicks=interpreter_click.count
        )
    else:
        # Default values for GET request
        return render_template(
            'index.html',
            mbti_type='INTJ',
            user_message='',
            interpretation=None,
            mbti_types=MBTI_TYPES,
            visitor_count=visitor.total_visitors,
            unique_visitor_count=unique_visitor.unique_visitors,
            interpreter_clicks=interpreter_click.count
        )

    return render_template('index.html')


@app.route('/translator', methods=['GET', 'POST'])
@requires_premium
def translator():
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
    
    if request.method == 'POST':
        # Increment translator click count
        translator_click.count += 1
        
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
                max_tokens=200,
                n=1,
                temperature=0.7,
            )

            # Assuming the AI provides the translation and interpretation separated by a delimiter
            output = response.choices[0].message.content.strip()
            # You may need to parse the output appropriately

            # Add logging here
            logging.info(f"Translator Input: From MBTI: {from_mbti}, To MBTI: {to_mbti}, Original Message: {original_message}")
            logging.info(f"Translator Output: {output}")
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
            logging.error(f"Error in Translator: {e}")

        db.session.commit()
        
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
            translator_clicks=translator_click.count
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
            visitor_count=visitor.total_visitors,
            unique_visitor_count=unique_visitor.unique_visitors,
            translator_clicks=translator_click.count
        )


@app.route('/guesser', methods=['GET', 'POST'])
def guesser():
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
    
    if request.method == 'POST':
        # Increment guesser click count
        guesser_click.count += 1
        
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
                max_tokens=300,
                n=1,
                temperature=0.7,
            )
            raw_output = response.choices[0].message.content

            # Add logging here
            logging.info(f"Guesser Input: Message: {message}")
            logging.info(f"Guesser Output: {raw_output}")

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
            logging.error(f"Error in Guesser: {e}")
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

        db.session.commit()

        return render_template(
            'guesser.html',
            message=message,
            output=parsed_output or raw_output,
            visitor_count=visitor.total_visitors,
            unique_visitor_count=unique_visitor.unique_visitors,
            guesser_clicks=guesser_click.count
        )
    else:
        return render_template(
            'guesser.html',
            message='',
            output='',
            visitor_count=visitor.total_visitors,
            unique_visitor_count=unique_visitor.unique_visitors,
            guesser_clicks=guesser_click.count
        )


#LOGIN
@app.route('/login')
def login():
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

    return redirect(url_for('index'))

@app.route('/logout')
def logout():
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
    return render_template('upgrade.html', stripe_publishable_key=os.environ.get('STRIPE_PUBLISHABLE_KEY'))

@app.route('/create-checkout-session', methods=['POST'])
@requires_auth
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': 'price_1Q2qrZKjJ23rv2vUc6hO0tpY',  # Replace with your price ID from Stripe
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('upgrade', _external=True),
            customer_email=session['profile']['email']
        )
        return jsonify({'sessionId': checkout_session.id})
    except Exception as e:
        return jsonify(error=str(e)), 403

@app.route('/payment-success')
@requires_auth
def payment_success():
    session_id = request.args.get('session_id')
    checkout_session = stripe.checkout.Session.retrieve(session_id)

    if checkout_session.payment_status == 'paid':
        user = User.query.filter_by(auth0_id=session['profile']['user_id']).first()
        if user:
            user.is_premium = True
            db.session.commit()
        return render_template('payment_success.html')
    else:
        return redirect(url_for('upgrade'))

@app.route('/payment-cancel')
@requires_auth
def payment_cancel():
    return render_template('payment_cancel.html')

@app.route('/profile')
@requires_auth
def profile():
    return render_template('profile.html', user=session['profile'])

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')


# Route for Terms of Service
@app.route('/terms-of-service')
def terms_of_service():
    return render_template('terms_of_service.html')








if __name__ == '__main__':
    app.run()
